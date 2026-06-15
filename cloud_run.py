"""
클라우드 아침/점심 브리핑 흐름 (GitHub Actions, 공개 저장소=무제한 실행시간)
================================================================
- morning : 09시경 시작, ~10시까지 동시 대화형 문진. 답하면 브리핑.
            10시까지 무응답이면 점심 재시도 안내.
- lunch   : 11:30 시작, ~13시까지 재시도. 답하면 브리핑.
            아침에 이미 받은 사람은 건너뜀. 끝까지 무응답이면 '종료' 멘트.

상태는 시트 '브리핑상태' 컬럼('', 발송, 종료)에 기록 → 중복 방지.
대기시간(분)은 Q_BUDGET_MIN 환경변수로 조절.
사용: python cloud_run.py morning|lunch
"""
import os
import sys
import subprocess
from pathlib import Path
from datetime import date, datetime
from dotenv import load_dotenv

load_dotenv()

import gspread
from google.oauth2.service_account import Credentials

import step4_telegram_bot as s4
import step5_analyze_report as s5

BASE_DIR = Path(__file__).parent.resolve()
SERVICE_ACCOUNT_FILE = str(BASE_DIR / os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"))
SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "가민건강코치")
HEADERS = s4.HEADERS
CORE = ["피로도", "근육통", "심리스트레스", "수면만족"]
USERS = s5.USERS  # {이름: {chat_id, tab, gender, trend_key, profile}}


def _ws(tab):
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
    return gspread.authorize(creds).open(SHEET_NAME).worksheet(tab)


def get_state(tab):
    try:
        ws = _ws(tab)
        today = date.today().isoformat()
        col = ws.col_values(1)
        for i, d in enumerate(col):
            if d == today:
                row = ws.row_values(i + 1)
                idx = HEADERS.index("브리핑상태")
                return row[idx] if idx < len(row) else ""
    except Exception as e:
        print(f"  상태읽기 실패({tab}): {e}")
    return ""


def set_state(tab, value):
    try:
        ws = _ws(tab)
        today = date.today().isoformat()
        col = ws.col_values(1)
        idx = HEADERS.index("브리핑상태")
        for i, d in enumerate(col):
            if d == today:
                ws.update_cell(i + 1, idx + 1, value)
                return
        row = [""] * len(HEADERS)
        row[0] = today
        row[idx] = value
        ws.update(values=[row], range_name=f"A{len(col) + 1}")
    except Exception as e:
        print(f"  상태쓰기 실패({tab}): {e}")


def run_step3():
    subprocess.run([sys.executable, str(BASE_DIR / "step3_collect_and_save.py")], cwd=str(BASE_DIR))


def send_status_to_ryu():
    """류우에게 오늘 두 사람의 문진/브리핑 현황 요약 발송 (은경 진행 모니터링용)"""
    today = date.today().isoformat()
    lines = [f"📋 오늘({date.today().strftime('%m/%d')}) 문진 현황"]
    for name, info in USERS.items():
        try:
            rows = s5.get_sheet_data(info["tab"], days=2)
            trow = next((r for r in rows if r.get("날짜") == today), {})
            answered = all(trow.get(k) for k in CORE)
        except Exception:
            answered = False
        sent = get_state(info["tab"]) == "발송"
        lines.append(f"• {name}: 문진 {'✅' if answered else '❌'} / 브리핑 {'✅' if sent else '❌'}")
    ryu = USERS.get("류우")
    if ryu:
        s4.send_text(ryu["chat_id"], "\n".join(lines))
        print("  → 류우에게 현황 요약 발송")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "morning"
    budget = int(os.getenv("Q_BUDGET_MIN", "60"))
    print(f"{'=' * 50}\n  cloud_run: {mode}  ({date.today()}, 대기 {budget}분)\n{'=' * 50}")

    # ⏰ 시간대 가드: GitHub cron이 지연/글리치로 엉뚱한 시각에 떠도(예: 새벽)
    #    그 시간대가 아니면 아무 동작 없이 종료 → 사용자 안 깨움. (수동실행은 면제)
    if os.getenv("SCHEDULED") == "true":
        hour = datetime.now().hour  # 워크플로우 TZ=Asia/Seoul → KST 기준
        windows = {"morning": range(7, 11), "lunch": range(11, 15)}
        if hour not in windows.get(mode, range(0, 24)):
            print(f"⏰ 현재 {hour}시(KST) — '{mode}' 시간대 아님(스케줄 지연 추정). 동작 없이 종료.")
            return

    # 아직 발송/종료 안 된 사용자만 대상
    pending = [(n, i) for n, i in USERS.items() if get_state(i["tab"]) not in ("발송", "종료")]
    if pending:
        s4.flush_updates()
        users = [{"chat_id": i["chat_id"], "name": n, "tab": i["tab"], "gender": i["gender"]} for n, i in pending]

        garmin = {"done": False}

        def brief(name, info, answers):
            """한 사람 브리핑 발송 (가민 수집은 첫 발송 때 1회)"""
            if not garmin["done"]:
                print("  가민 수집(첫 완료자)")
                run_step3()
                garmin["done"] = True
            s4.save_answers_to_sheet({"name": name, "tab": info["tab"], "gender": info["gender"]}, answers)
            s4.send_text(info["chat_id"], "✅ *문진이 완료되었습니다!*\n브리핑을 준비할게요, 잠시만요 💪")
            if s5.report_user(name, info, send=True):
                set_state(info["tab"], "발송")
                print(f"  [{name}] 브리핑 발송")

        def on_user_done(u, answers):
            """문진 완료 즉시 호출 — 다른 사람 기다리지 않고 바로 브리핑"""
            if all(answers.get(k) for k in CORE):
                brief(u["name"], USERS[u["name"]], answers)

        # 동시 대화형 문진 (각자 끝나는 즉시 on_user_done → 브리핑)
        results = s4.run_session_multi(users, budget_minutes=budget, on_done=on_user_done)

        # 세션 종료 후: 핵심은 답했는데 아직 발송 안 된 사람 + 미응답 처리
        for n, i in pending:
            if get_state(i["tab"]) == "발송":
                continue
            ans = results.get(n, {})
            if all(ans.get(k) for k in CORE):
                brief(n, i, ans)
            elif mode == "lunch":
                s4.send_text(i["chat_id"], "📭 오늘은 문진 답변이 없어서 브리핑을 보내지 않았어요.\n내일 아침에 다시 만나요! 🌙")
                set_state(i["tab"], "종료")
                print(f"  [{n}] 종료")
            else:
                s4.send_text(i["chat_id"], "⏰ 오전 문진을 못 받았어요. 점심때(11:30~) 다시 보낼게요!")
                print(f"  [{n}] 오전 미응답 — 점심 재시도 예정")
    else:
        print("대상 없음(이미 발송/종료)")

    # 점심(하루 마지막 실행)엔 류우에게 두 사람 현황 요약 발송 (모니터링용)
    if mode == "lunch":
        send_status_to_ryu()

    print("\n완료")


if __name__ == "__main__":
    main()
