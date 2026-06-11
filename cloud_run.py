"""
클라우드 오케스트레이터 (GitHub Actions 전용)
=============================================
- ask        : 아침에 문진 질문만 발송 (이전 백로그 비우고)
- check      : 답 회수 → 충분하면 가민수집+리포트 발송(하루 1회), 아니면 대기
- finalcheck : check와 같되, 끝까지 무응답이면 '오늘 브리핑 종료' 멘트

사용: python cloud_run.py ask|check|finalcheck

스케줄(한국시간): 08:30 ask / 10:00·11:00·12:00 check / 13:00 finalcheck
상태는 시트의 '브리핑상태' 컬럼에 기록(''/발송/종료) → 중복 발송 방지.
"""
import os
import sys
import subprocess
from pathlib import Path
from datetime import date
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
CORE = ["피로도", "근육통", "심리스트레스", "수면만족"]  # 이 4개 다 답하면 '충분'
USERS = s5.USERS  # {이름: {chat_id, tab, gender, trend_key, profile}}


def _ws(tab):
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
    return gspread.authorize(creds).open(SHEET_NAME).worksheet(tab)


def get_state(tab):
    """오늘 행의 브리핑상태 반환 (''=없음 / 발송 / 종료)"""
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


def sufficient(ans):
    """핵심 문진 4개를 다 답했으면 브리핑 발송 가능"""
    return all(ans.get(k) for k in CORE)


def run_step3():
    """가민 수집 새로고침 (양쪽 사용자, 시트 갱신). 문진/상태는 보존됨."""
    subprocess.run([sys.executable, str(BASE_DIR / "step3_collect_and_save.py")], cwd=str(BASE_DIR))


def do_ask():
    print("문진 발송 모드")
    s4.flush_updates()  # 어제 백로그 비우기
    for name, info in USERS.items():
        s4.send_all_questions(info["chat_id"], {"name": name, "gender": info["gender"]})
        print(f"  [{name}] 문진 발송 완료")


def do_check(final=False):
    print(f"체크 모드 (final={final})")
    run_step3()  # 가민 데이터 새로고침 (데이터 완전성 + 최신 리포트)

    collected = s4.collect_answers()  # {chat_id: {key: val}}
    print(f"  회수된 답변: {{ {', '.join(f'{k}:{len(v)}개' for k, v in collected.items())} }}")

    # 1) 회수한 답을 각 사용자 시트에 저장 (누적)
    for name, info in USERS.items():
        ans = collected.get(str(info["chat_id"]), {})
        if ans:
            s4.save_answers_to_sheet({"name": name, "tab": info["tab"]}, ans)

    # 2) 사용자별로 발송 판단
    for name, info in USERS.items():
        state = get_state(info["tab"])
        if state == "발송":
            print(f"  [{name}] 이미 발송됨 — 건너뜀")
            continue
        ans = collected.get(str(info["chat_id"]), {})
        if sufficient(ans):
            print(f"  [{name}] 문진 충분 → 리포트 발송")
            if s5.report_user(name, info, send=True):
                set_state(info["tab"], "발송")
        elif final and state != "종료":
            print(f"  [{name}] 최종 무응답 → 종료 멘트")
            s4.send_text(info["chat_id"],
                         "📭 오늘은 문진 답변이 없어서 브리핑을 보내지 않았어요.\n내일 아침에 다시 만나요! 🌙")
            set_state(info["tab"], "종료")
        else:
            print(f"  [{name}] 아직 미응답 — 다음 체크에서 다시 확인")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    print(f"{'=' * 50}\n  cloud_run: {mode}  ({date.today()})\n{'=' * 50}")
    if mode == "ask":
        do_ask()
    elif mode == "finalcheck":
        do_check(final=True)
    else:
        do_check(final=False)
    print("완료")


if __name__ == "__main__":
    main()
