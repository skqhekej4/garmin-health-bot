"""
클라우드 아침 브리핑 흐름 (GitHub Actions 전용) — 대화형
=====================================================
1) 각 사용자에게 문진을 '하나씩' 진행 (누르면 다음 질문). 최대 Q_BUDGET_MIN분 대기.
2) 핵심 4개(피로도·근육통·심리스트레스·수면만족)를 답했으면 → 완료 멘트
   → (답한 사람 있으면) 가민 데이터 수집 → 그 사람 브리핑 발송.
3) 다 못 받았으면 → '오늘 브리핑 건너뜀' 멘트, 발송 안 함.

문진을 먼저 받고 '그 뒤에' 가민을 수집하므로(보통 9시 가까움) 수면 데이터 동기화에 유리.
사용: python cloud_run.py
"""
import os
import sys
import subprocess
from pathlib import Path
from datetime import date
from dotenv import load_dotenv

load_dotenv()

import step4_telegram_bot as s4
import step5_analyze_report as s5

BASE_DIR = Path(__file__).parent.resolve()
CORE = ["피로도", "근육통", "심리스트레스", "수면만족"]   # 이 4개 다 답하면 브리핑 발송
USERS = s5.USERS  # {이름: {chat_id, tab, gender, trend_key, profile}}


def run_step3():
    """가민 수집 새로고침 (양쪽 사용자, 시트 갱신). 문진/상태는 보존됨."""
    subprocess.run([sys.executable, str(BASE_DIR / "step3_collect_and_save.py")], cwd=str(BASE_DIR))


def main():
    print(f"{'=' * 50}\n  아침 브리핑 흐름  ({date.today()})\n{'=' * 50}")
    budget = int(os.getenv("Q_BUDGET_MIN", "25"))
    s4.flush_updates()  # 이전 백로그 비우기

    # 1) 사용자별 대화형 문진 (하나씩)
    results = {}
    for name, info in USERS.items():
        u = {"name": name, "tab": info["tab"], "gender": info["gender"]}
        print(f"\n▶ {name} 문진 시작 (최대 {budget}분 대기)")
        answers = s4.run_questionnaire(info["chat_id"], u, budget_minutes=budget, auto_confirm=False)
        ok = all(answers.get(k) for k in CORE)
        # 즉시 피드백
        if ok:
            s4.send_text(info["chat_id"], "✅ *문진이 완료되었습니다!*\n브리핑을 준비할게요, 잠시만요 💪")
        else:
            s4.send_text(info["chat_id"], "📭 문진을 다 받지 못해 오늘 브리핑은 건너뛸게요.\n내일 아침에 다시 만나요! 🌙")
        results[name] = (info, u, answers, ok)

    # 2) 답한 사람이 있으면 가민 수집 (이 시점이면 9시 가까워 동기화 양호)
    if any(ok for _, _, _, ok in results.values()):
        print("\n▶ 가민 데이터 수집")
        run_step3()

    # 3) 답한 사람에게 브리핑 발송
    for name, (info, u, answers, ok) in results.items():
        if not ok:
            print(f"  [{name}] 미응답 — 브리핑 생략")
            continue
        s4.save_answers_to_sheet(u, answers)
        s5.report_user(name, info, send=True)

    print("\n완료")


if __name__ == "__main__":
    main()
