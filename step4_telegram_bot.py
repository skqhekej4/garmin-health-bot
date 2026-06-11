"""
4단계: 텔레그램 봇 - 버튼식 문진 + 답변 수집 + 시트 저장
=========================================================
질문이 하나씩 버튼과 함께 오고, 버튼만 누르면 됩니다.
모든 답변 완료 후 자동으로 시트 저장 + 리포트 생성.

실행:
    python step4_telegram_bot.py --send-and-listen   (문진+수집+리포트)
    python step4_telegram_bot.py --test              (연결 테스트)
"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path
from datetime import date, timedelta
from dotenv import load_dotenv
import requests

load_dotenv()

BASE_DIR = Path(__file__).parent.resolve()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID_RYU = os.getenv("TELEGRAM_CHAT_ID_RYU")
CHAT_ID_EK = os.getenv("TELEGRAM_CHAT_ID_EK")
SERVICE_ACCOUNT_FILE = str(BASE_DIR / os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"))
SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "가민건강코치")

HEADERS = [
    "날짜",
    "수면시간", "수면점수", "HRV", "안정심박", "스트레스",
    "운동종류", "운동시간(분)", "거리(km)", "페이스", "걸음수", "칼로리",
    "체중", "체지방률", "근육량",
    "컨디션", "수면체감", "음주", "식사", "수분", "카페인", "통증부위", "운동계획", "생리주기",
    "HRV상태", "바디배터리", "훈련준비도", "훈련상태", "급성부하", "부하비율", "VO2max",
    "피로도", "근육통", "심리스트레스", "수면만족", "특이사항",
    "브리핑상태",
]

QUESTIONNAIRE_KEYS = ["피로도", "근육통", "심리스트레스", "수면만족", "음주", "식사", "운동계획", "특이사항", "체중", "생리주기"]

USERS = {}
if CHAT_ID_RYU:
    USERS[CHAT_ID_RYU] = {"name": "류우", "tab": "류우", "gender": "male"}
if CHAT_ID_EK and CHAT_ID_EK != "eunkyeong_chat_id":
    USERS[CHAT_ID_EK] = {"name": "은경", "tab": "은경", "gender": "female"}


def get_questions(gender="male"):
    """v2: Hooper 웰니스 지수(피로/근육통/스트레스/수면) 기반 + 운동의도 + 특이사항.
    가민이 객관적으로 못 보는 '주관 신호'만 묻는다. 매일 6~7문항."""
    questions = [
        {"key": "피로도", "text": "오늘 몸의 전반적 피로도는? (무거움/지침 정도)",
         "buttons": ["1(거뜬)", "2(가벼움)", "3(보통)", "4(피곤)", "5(탈진)"]},
        {"key": "근육통", "text": "근육통/뻐근함 정도는?",
         "buttons": ["1(없음)", "2(약간)", "3(보통)", "4(쑤심)", "5(심함)"]},
        {"key": "심리스트레스", "text": "정신적 스트레스/긴장 정도는?",
         "buttons": ["1(편안)", "2(약간)", "3(보통)", "4(높음)", "5(매우높음)"]},
        {"key": "수면만족", "text": "어젯밤 잠, 주관적으로 어땠나요?",
         "buttons": ["1(최악)", "2(부족)", "3(보통)", "4(좋음)", "5(완벽)"]},
        {"key": "음주", "text": "어제 음주는? (수면·회복에 영향)",
         "buttons": ["안마심", "1~2잔", "3~4잔", "5잔이상", "과음"]},
        {"key": "식사", "text": "어제 저녁 식사는? (과식·야식은 수면 방해)",
         "buttons": ["적당히", "과식", "야식", "늦은시간", "거름"]},
        {"key": "운동계획", "text": "오늘 운동 의도/계획은?",
         "buttons": ["쉬는날", "가볍게", "보통", "강하게", "대회/고강도"]},
        {"key": "특이사항", "text": "오늘 통증·몸살 등 특이사항? (여러개 선택 후 '완료')",
         "buttons": ["없음", "무릎", "발목", "허리", "어깨/목", "종아리/허벅지",
                     "두통", "감기기운", "기타", "완료"],
         "multi": True},
        {"key": "체중", "text": "오늘 체중 (선택) — 숫자로 입력하거나(예: 72.5) '건너뛰기'",
         "buttons": ["건너뛰기"]},
    ]
    if gender == "female":
        questions.append({"key": "생리주기", "text": "생리 관련 상태는?",
                          "buttons": ["해당없음", "생리중", "생리전(PMS)", "생리직후", "배란기쯤"]})
    return questions


# ─── 텔레그램 API ───
def tg_post(method, data):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        r = requests.post(url, json=data, timeout=10)
        return r.json()
    except Exception as e:
        print(f"    TG 에러: {e}")
        return {}


def send_text(chat_id, text):
    return tg_post("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})


def send_question_buttons(chat_id, question_text, buttons, q_index, total):
    """인라인 키보드 버튼으로 질문 전송"""
    # 버튼을 2열로 배치
    keyboard = []
    row = []
    for btn in buttons:
        row.append({"text": btn, "callback_data": btn})
        if len(row) >= 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    text = f"📋 *질문 {q_index}/{total}*\n\n{question_text}"
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": {"inline_keyboard": keyboard}
    }
    return tg_post("sendMessage", data)


def answer_callback(callback_query_id):
    tg_post("answerCallbackQuery", {"callback_query_id": callback_query_id})


def remove_keyboard(chat_id, message_id, original_text):
    """답변 완료 후 인라인 키보드 제거 (이전 버튼 잘못 누르기 방지)"""
    tg_post("editMessageReplyMarkup", {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": {"inline_keyboard": []}
    })


def get_updates(offset=0, timeout=30):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    try:
        r = requests.get(url, params={"offset": offset, "timeout": timeout}, timeout=timeout + 5)
        result = r.json()
        if result.get("ok"):
            return result.get("result", [])
    except Exception:
        pass
    return []


# ════════════════════════════════════════════════════════════
#  비동기 문진 (클라우드용) — 아침에 보내놓고, 나중 체크에서 답을 회수
# ════════════════════════════════════════════════════════════
def flush_updates():
    """기존(어제) 백로그를 확정 처리해 비움 → 이후 탭만 오늘 답으로 회수됨"""
    ups = get_updates(offset=0, timeout=0)
    if ups:
        get_updates(offset=ups[-1]["update_id"] + 1, timeout=0)  # 확정(삭제)


def send_all_questions(chat_id, user_info):
    """오늘 문진 질문을 모두 한 번에 전송(각 질문=버튼 메시지). 대기 안 함.
    callback_data를 'key|버튼' 으로 인코딩 → 나중에 어느 질문 답인지 구분."""
    name = user_info["name"]
    questions = get_questions(user_info.get("gender", "male"))
    today_str = date.today().strftime("%m/%d")
    send_text(chat_id, f"🌅 *{name}님, 좋은 아침이에요!*\n\n{today_str} 건강 문진이에요.\n출근길에 편하게 눌러주세요 (아무 때나, 오후 1시까지 답하면 브리핑이 와요)")
    time.sleep(0.5)
    for i, q in enumerate(questions):
        keyboard, row = [], []
        for btn in q["buttons"]:
            row.append({"text": btn, "callback_data": f"{q['key']}|{btn}"})
            if len(row) >= 3:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        text = f"📋 *{i + 1}/{len(questions)}* {q['text']}"
        tg_post("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "Markdown",
                                "reply_markup": {"inline_keyboard": keyboard}})
        time.sleep(0.2)


def collect_answers():
    """그동안 눌린 버튼 답을 getUpdates로 회수. {chat_id: {key: value}} 반환.
    offset 미확정(읽기만) → 여러 체크가 같은 답을 반복 회수해도 됨(텔레그램 24h 보존)."""
    updates = get_updates(offset=0, timeout=0)
    answers = {}          # chat_id -> {key: value}
    multi = {}            # (chat_id, key) -> set  (특이사항 멀티선택 누적)
    for u in updates:
        cb = u.get("callback_query")
        if cb:
            chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
            data = cb.get("data", "") or ""
            try:
                answer_callback(cb["id"])  # 버튼 로딩 스피너 해제(반복 호출 무해)
            except Exception:
                pass
            if "|" not in data:
                continue
            key, val = data.split("|", 1)
            a = answers.setdefault(chat_id, {})
            if key == "특이사항":
                s = multi.setdefault((chat_id, key), set())
                if val == "없음":
                    a[key] = "없음"
                elif val == "완료":
                    pass
                else:
                    s.add(val)
                if s:
                    a[key] = ", ".join(sorted(s))
            else:
                a[key] = val  # 마지막 탭이 최종(다시 눌러 수정 가능)
        else:
            # 텍스트 답(체중) 처리
            msg = u.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            txt = (msg.get("text") or "").strip().replace("kg", "").replace("키로", "")
            if chat_id and txt and not txt.startswith("/"):
                try:
                    float(txt)
                    answers.setdefault(chat_id, {})["체중"] = txt
                except ValueError:
                    pass
    return answers


# ════════════════════════════════════════════════════════════
#  동시 대화형 문진 (여러 사용자) — 단일 getUpdates 루프로 각자 진행
# ════════════════════════════════════════════════════════════
def _send_current_q(cid, st):
    """현재 질문을 버튼과 함께 전송하고 message_id 기록"""
    q = st["questions"][st["idx"]]
    keyboard, row = [], []
    for b in q["buttons"]:
        row.append({"text": b, "callback_data": b})
        if len(row) >= 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    text = f"📋 *{st['idx'] + 1}/{len(st['questions'])}* {q['text']}"
    r = tg_post("sendMessage", {"chat_id": cid, "text": text, "parse_mode": "Markdown",
                                "reply_markup": {"inline_keyboard": keyboard}})
    st["sent_msg_id"] = r.get("result", {}).get("message_id")


def _advance(cid, st):
    """이전 질문 키보드 제거 후 다음 질문(또는 완료)"""
    if st["sent_msg_id"]:
        remove_keyboard(cid, st["sent_msg_id"], "")
    st["idx"] += 1
    if st["idx"] >= len(st["questions"]):
        st["done"] = True
    else:
        _send_current_q(cid, st)


def run_session_multi(users, budget_minutes=60):
    """여러 사용자 동시 대화형 문진. users:[{chat_id,name,tab,gender}] → {name: answers}"""
    state = {}
    today_str = date.today().strftime("%m/%d")
    for u in users:
        cid = str(u["chat_id"])
        state[cid] = {"u": u, "questions": get_questions(u["gender"]), "idx": 0,
                      "answers": {}, "multi": [], "done": False, "sent_msg_id": None}
        send_text(cid, f"🌅 *{u['name']}님, 좋은 아침이에요!*\n\n{today_str} 건강 문진이에요. 버튼만 눌러주세요!")
        _send_current_q(cid, state[cid])

    # 기존 업데이트 무시
    ups = get_updates(timeout=0)
    offset = ups[-1]["update_id"] + 1 if ups else 0

    start = time.time()
    while not all(s["done"] for s in state.values()) and (time.time() - start) < budget_minutes * 60:
        for upd in get_updates(offset=offset, timeout=10):
            offset = upd["update_id"] + 1
            cb = upd.get("callback_query")
            if not cb:
                # 텍스트(체중) — 현재 질문이 체중일 때만
                msg = upd.get("message", {})
                cid = str(msg.get("chat", {}).get("id", ""))
                st = state.get(cid)
                if st and not st["done"] and st["questions"][st["idx"]]["key"] == "체중":
                    txt = (msg.get("text") or "").strip()
                    if txt and not txt.startswith("/"):
                        st["answers"]["체중"] = txt
                        _advance(cid, st)
                continue
            cid = str(cb.get("message", {}).get("chat", {}).get("id", ""))
            try:
                answer_callback(cb["id"])
            except Exception:
                pass
            st = state.get(cid)
            if not st or st["done"]:
                continue
            q = st["questions"][st["idx"]]
            sel = cb.get("data", "")
            if sel not in set(q["buttons"]):
                continue  # 이전 질문 버튼 무시
            if q.get("multi"):
                if sel == "완료":
                    st["answers"][q["key"]] = ", ".join(st["multi"]) if st["multi"] else "없음"
                    st["multi"] = []
                    _advance(cid, st)
                elif sel == "없음":
                    st["answers"][q["key"]] = "없음"
                    st["multi"] = []
                    _advance(cid, st)
                elif sel not in st["multi"]:
                    st["multi"].append(sel)
                    send_text(cid, f"✓ {sel} (현재: {', '.join(st['multi'])})\n더 있으면 선택, 끝이면 '완료'")
            else:
                st["answers"][q["key"]] = sel
                _advance(cid, st)

    return {s["u"]["name"]: s["answers"] for s in state.values()}


# ─── 버튼식 문진 진행 ───
def run_questionnaire(chat_id, user_info, timeout_minutes=25, budget_minutes=None, auto_confirm=True):
    """한 사용자에 대해 버튼식 문진 진행 (동기, 하나씩 → 누르면 다음).
    budget_minutes: 사용자당 전체 대기 예산(분). 초과 시 남은 질문은 빈칸 처리.
    auto_confirm: True면 끝에 '문진 완료' 요약 메시지 자동 발송."""
    name = user_info["name"]
    gender = user_info["gender"]
    questions = get_questions(gender)
    answers = {}

    today_str = date.today().strftime("%m/%d")
    send_text(chat_id, f"🌅 *{name}님, 좋은 아침이에요!*\n\n{today_str} 건강 문진을 시작합니다.\n버튼만 눌러주세요!")

    time.sleep(1)

    # offset 초기화 (기존 업데이트 무시)
    updates = get_updates(timeout=0)
    offset = 0
    if updates:
        offset = updates[-1]["update_id"] + 1

    user_start = time.time()
    for i, q in enumerate(questions):
        # 전체 시간 예산 초과 시 남은 질문은 건너뜀 (미응답 사용자가 작업 오래 붙잡는 것 방지)
        if budget_minutes and (time.time() - user_start) > budget_minutes * 60:
            answers[q["key"]] = ""
            print(f"    {q['key']}: (예산 초과 건너뜀)")
            continue
        is_multi = q.get("multi", False)
        valid_buttons = set(q["buttons"])  # 현재 질문의 유효 버튼들
        sent = send_question_buttons(chat_id, q["text"], q["buttons"], i + 1, len(questions))
        sent_msg_id = sent.get("result", {}).get("message_id")

        start = time.time()
        answered = False
        multi_selections = []

        while not answered and (time.time() - start) < timeout_minutes * 60:
            updates = get_updates(offset=offset, timeout=10)
            for u in updates:
                offset = u["update_id"] + 1

                cb = u.get("callback_query")
                if cb:
                    cb_chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
                    if cb_chat_id == chat_id:
                        answer_callback(cb["id"])
                        selection = cb["data"]

                        # ★ 현재 질문 버튼이 아니면 무시 (이전 질문 버튼 잘못 누르기 방지)
                        if selection not in valid_buttons:
                            print(f"    (무시: '{selection}'은 현재 질문 버튼 아님)")
                            continue

                        if is_multi:
                            if selection == "완료":
                                result = ", ".join(multi_selections) if multi_selections else "없음"
                                answers[q["key"]] = result
                                print(f"    {q['key']}: {result}")
                                answered = True
                            elif selection == "없음":
                                answers[q["key"]] = "없음"
                                print(f"    {q['key']}: 없음")
                                answered = True
                            else:
                                if selection not in multi_selections:
                                    multi_selections.append(selection)
                                    send_text(chat_id, f"✓ {selection} 선택됨 (현재: {', '.join(multi_selections)})\n더 있으면 선택, 끝이면 '완료'")
                        else:
                            answers[q["key"]] = selection
                            print(f"    {q['key']}: {selection}")
                            answered = True

                        # ★ 답변 완료 시 키보드 제거 (다시 누르기 방지)
                        if answered and sent_msg_id:
                            remove_keyboard(chat_id, sent_msg_id, "")
                        if answered:
                            break

                msg = u.get("message", {})
                msg_chat_id = str(msg.get("chat", {}).get("id", ""))
                msg_text = msg.get("text", "").strip()
                if msg_chat_id == chat_id and msg_text and not msg_text.startswith("/"):
                    answers[q["key"]] = msg_text
                    print(f"    {q['key']}: {msg_text} (텍스트)")
                    answered = True
                    if sent_msg_id:
                        remove_keyboard(chat_id, sent_msg_id, "")
                    break

        if not answered:
            answers[q["key"]] = ""
            print(f"    {q['key']}: (타임아웃)")

        time.sleep(0.3)

    # 완료 메시지 (auto_confirm일 때만)
    if auto_confirm:
        confirm = f"✅ *{name}님 문진 완료!*\n\n"
        for k, v in answers.items():
            confirm += f"• {k}: {v}\n"
        confirm += f"\n분석 리포트를 준비 중이에요... 잠시만요! 💪"
        send_text(chat_id, confirm)

    return answers


# ─── 시트 저장 ───
def save_answers_to_sheet(user_info, answers):
    import gspread
    from google.oauth2.service_account import Credentials

    # 체중: '건너뛰기'거나 숫자가 아니면 저장 안 함
    w = (answers.get("체중") or "").strip().replace("kg", "").replace("키로", "")
    try:
        answers["체중"] = str(float(w)) if w else ""
    except ValueError:
        answers["체중"] = ""

    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    spreadsheet = gc.open(SHEET_NAME)

    today_str = date.today().isoformat()
    tab_name = user_info["tab"]

    try:
        ws = spreadsheet.worksheet(tab_name)
        all_dates = ws.col_values(1)

        # 오늘 행 찾기 (문진은 오늘 아침에 하므로 오늘 행에 저장)
        row_num = None
        for i, d in enumerate(all_dates):
            if d == today_str:
                row_num = i + 1
                break

        if row_num:
            for qk in QUESTIONNAIRE_KEYS:
                if qk in answers and answers[qk] and qk in HEADERS:
                    col_idx = HEADERS.index(qk) + 1
                    ws.update_cell(row_num, col_idx, answers[qk])
            print(f"  [{user_info['name']}] 문진 시트 저장 완료 ({today_str} 행)")
        else:
            # 오늘 행이 없으면 (step3가 아직 안 돌았을 경우) 새 행 생성
            print(f"  [{user_info['name']}] {today_str} 행 없음 - 새 행 생성")
            row_data = [""] * len(HEADERS)
            row_data[0] = today_str  # 날짜
            for qk in QUESTIONNAIRE_KEYS:
                if qk in answers and answers[qk] and qk in HEADERS:
                    idx = HEADERS.index(qk)
                    row_data[idx] = answers[qk]
            next_row = len(all_dates) + 1
            ws.update(values=[row_data], range_name=f'A{next_row}')
            print(f"  [{user_info['name']}] 문진 데이터 새 행 추가 (행 {next_row})")
    except Exception as e:
        print(f"  [{user_info['name']}] 시트 저장 실패: {e}")


# ─── 메인 ───
def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--test"

    print("=" * 55)
    print("  가민 건강 코치 - 텔레그램 문진 (버튼식)")
    print("=" * 55)

    if mode == "--test":
        for chat_id, info in USERS.items():
            result = send_text(chat_id, f"🤖 *가민 건강 코치 봇 테스트*\n\n{info['name']}님, 봇 정상 작동! ✅")
            ok = result.get("ok", False)
            print(f"  [{info['name']}] 테스트: {'성공' if ok else '실패'}")

    elif mode in ("--send-and-listen", "--send"):
        budget_env = os.getenv("Q_BUDGET_MIN")
        budget = int(budget_env) if budget_env else None
        for chat_id, info in USERS.items():
            print(f"\n  ▶ {info['name']} 문진 시작 (대기예산: {budget or '무제한'}분)")
            answers = run_questionnaire(chat_id, info, budget_minutes=budget)

            if answers:
                print(f"  ▶ {info['name']} 시트 저장")
                save_answers_to_sheet(info, answers)

        # 문진 완료 후 자동으로 리포트 생성
        if mode == "--send-and-listen":
            print(f"\n{'─'*55}")
            print(f"  ▶ 리포트 자동 생성")
            print(f"{'─'*55}")
            subprocess.run([sys.executable, str(BASE_DIR / "step5_analyze_report.py")], cwd=str(BASE_DIR))

    else:
        print("  사용법:")
        print("    python step4_telegram_bot.py --test")
        print("    python step4_telegram_bot.py --send-and-listen")


if __name__ == "__main__":
    main()
