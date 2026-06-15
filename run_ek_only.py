"""은경 1명만: 문진 → 시트 저장 → 리포트 발송 (오늘 보충용)"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from dotenv import load_dotenv
load_dotenv()
import step4_telegram_bot as s4
import step5_analyze_report as s5

chat_id = s4.CHAT_ID_EK
info = {"name": "은경", "tab": "은경", "gender": "female"}

print("은경 문진 전송 → 답변 대기")
answers = s4.run_questionnaire(chat_id, info)
if answers:
    s4.save_answers_to_sheet(info, answers)
print("리포트 생성/발송")
s5.report_user("은경", s5.USERS["은경"], send=True)
print("✅ 완료 — 은경 문진+리포트")
