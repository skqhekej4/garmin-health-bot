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
import time
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


# ── 월별 훈련 목표 (시트 '목표' 탭에 사용자/연월별 저장) ──
GOAL_OPTIONS = {
    "속도향상": "달리기 속도(스피드) 향상 — 인터벌·템포 중심으로 페이스 끌어올리기",
    "지구력": "지구력·장거리 — 롱런으로 거리/시간 빌드업",
    "대회대비": "다가오는 대회 대비 — 강도 유지하며 볼륨 조절(샤프닝/테이퍼)",
    "언덕트레일": "언덕·트레일 특화 — 오르막/하강·트레일 주행 강화",
    "건강유지": "부상 없이 가볍게 컨디션 유지",
    "체중감량": "체중 감량 — 유산소 볼륨 중심",
}


def _goal_ws():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
    ss = gspread.authorize(creds).open(SHEET_NAME)
    try:
        return ss.worksheet("목표")
    except Exception:
        ws = ss.add_worksheet("목표", rows=200, cols=4)
        ws.update(values=[["사용자", "연월", "목표키", "목표설명"]], range_name="A1")
        return ws


def get_goal(name):
    """이번 달 선택된 목표 설명 반환 (없으면 None)"""
    ym = date.today().strftime("%Y-%m")
    try:
        for row in _goal_ws().get_all_values()[1:]:
            if len(row) >= 4 and row[0] == name and row[1] == ym:
                return row[3]
    except Exception as e:
        print(f"  목표 읽기 실패({name}): {e}")
    return None


def set_goal(name, key):
    """이번 달 목표 저장(키→설명). 같은 달이면 갱신."""
    ym = date.today().strftime("%Y-%m")
    desc = GOAL_OPTIONS.get(key, key)
    try:
        ws = _goal_ws()
        rows = ws.get_all_values()
        for i, row in enumerate(rows[1:], start=2):
            if len(row) >= 2 and row[0] == name and row[1] == ym:
                ws.update(values=[[name, ym, key, desc]], range_name=f"A{i}")
                print(f"  [{name}] 이번달 목표 갱신: {key}")
                return
        ws.update(values=[[name, ym, key, desc]], range_name=f"A{len(rows) + 1}")
        print(f"  [{name}] 이번달 목표 저장: {key}")
    except Exception as e:
        print(f"  목표 저장 실패({name}): {e}")


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


def today_garmin_present(tab):
    """오늘 행에 가민 핵심 지표가 들어와 있나 (동기화 됐나)"""
    today = date.today().isoformat()
    try:
        rows = s5.get_sheet_data(tab, days=2)
        t = next((r for r in rows if r.get("날짜") == today), {})
        return bool(t.get("훈련준비도") or t.get("수면점수") or t.get("HRV"))
    except Exception:
        return False


def today_answers(tab):
    """오늘 행에 저장된 문진 답변 dict (없으면 빈 dict)"""
    today = date.today().isoformat()
    keys = ["피로도", "근육통", "심리스트레스", "수면만족", "음주", "식사", "운동계획", "특이사항", "체중", "생리주기"]
    try:
        rows = s5.get_sheet_data(tab, days=2)
        t = next((r for r in rows if r.get("날짜") == today), {})
        return {k: t.get(k, "") for k in keys}
    except Exception:
        return {}


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

    # 이번 달 선택된 훈련 목표를 메모리에 반영 (선택한 게 있으면 기본값 대신 사용)
    for n, i in USERS.items():
        g = get_goal(n)
        if g:
            i["goal"] = g

    # 아직 발송/종료 안 된 사용자만 대상
    pending = [(n, i) for n, i in USERS.items() if get_state(i["tab"]) not in ("발송", "종료")]

    garmin = {"done": False}
    handled = set()

    def brief(name, info, answers):
        """브리핑 발송. 단 오늘 가민 미동기화면 어제 걸 안 주고 '경고'만 하고 보류."""
        if name in handled:
            return
        handled.add(name)
        if answers.get("월목표"):  # 이번달 목표 선택했으면 저장 + 즉시 반영
            set_goal(name, answers["월목표"])
            info["goal"] = GOAL_OPTIONS.get(answers["월목표"], answers["월목표"])
        if not garmin["done"]:
            print("  가민 수집")
            run_step3()
            garmin["done"] = True
        s4.save_answers_to_sheet({"name": name, "tab": info["tab"], "gender": info["gender"]}, answers)
        if not today_garmin_present(info["tab"]):
            s4.send_text(info["chat_id"],
                         "✅ 문진은 완료됐어요! 다만 ⚠️ *오늘 가민 데이터가 아직 안 올라왔어요.*\n"
                         "• 아직 동기화 안 했으면 → *가민 커넥트* 앱을 한 번 열어주세요\n"
                         "• 이미 했으면 → 가민이 아침 데이터(수면·준비도) 계산 중이라 그래요\n"
                         "데이터가 준비되는 즉시 — *보통 오전 10시~10시 반쯤* — 브리핑이 자동으로 와요 🙆 (따로 안 하셔도 됩니다)")
            print(f"  [{name}] 가민 미동기화 — 경고, 발송 보류")
            return
        s4.send_text(info["chat_id"], "✅ *문진이 완료되었습니다!*\n브리핑을 준비할게요, 잠시만요 💪")
        if s5.report_user(name, info, send=True):
            set_state(info["tab"], "발송")
            print(f"  [{name}] 브리핑 발송")

    def on_user_done(u, answers):
        if all(answers.get(k) for k in CORE):
            brief(u["name"], USERS[u["name"]], answers)

    def try_deliver_deferred():
        """문진은 답했지만 가민 미동기화로 보류된 사람 → 이제 동기화됐으면 브리핑 발송. 남은 보류 수 반환."""
        deferred = [(n, i) for n, i in pending
                    if get_state(i["tab"]) != "발송" and all(today_answers(i["tab"]).get(k) for k in CORE)]
        if not deferred:
            return 0
        run_step3()  # 가민 재수집
        remaining = 0
        for n, i in deferred:
            if today_garmin_present(i["tab"]):
                s4.send_text(i["chat_id"], "✅ *가민 동기화 확인!* 브리핑 보내드려요 💪")
                if s5.report_user(n, i, send=True):
                    set_state(i["tab"], "발송")
                    print(f"  [{n}] 동기화 후 브리핑 발송")
            else:
                remaining += 1
        return remaining

    if pending:
        # (A) 이미 오늘 문진 답한 사람 → 문진 다시 안 묻고 가민 재수집 후 브리핑 재시도(동기화 대기였던 경우)
        for n, i in pending:
            if all(today_answers(i["tab"]).get(k) for k in CORE):
                print(f"  [{n}] 이미 문진 답함 → 브리핑 재시도")
                brief(n, i, today_answers(i["tab"]))

        # (B) 아직 문진 안 한 사람만 대화형 문진 (check 모드는 문진 안 묻고 배송만)
        need_q = [] if mode == "check" else [
            (n, i) for n, i in pending
            if get_state(i["tab"]) != "발송" and not all(today_answers(i["tab"]).get(k) for k in CORE)]
        if need_q:
            s4.flush_updates()
            users = [{"chat_id": i["chat_id"], "name": n, "tab": i["tab"], "gender": i["gender"],
                      "ask_goal": get_goal(n) is None} for n, i in need_q]
            results = s4.run_session_multi(users, budget_minutes=budget, on_done=on_user_done)
            for n, i in need_q:
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

    # ※ 실행 내 반복 재시도(재로그인)는 제거함 — 가민 429(Too Many Requests) 유발.
    #   각 실행은 가민 로그인 1회만. 늦게 올라오는 데이터는 '다음 예약 실행'(10:30/11:30/12:00)이 받는다.

    # 점심(하루 마지막 실행)엔 류우에게 두 사람 현황 요약 발송 (모니터링용)
    if mode == "lunch":
        send_status_to_ryu()

    print("\n완료")


if __name__ == "__main__":
    main()
