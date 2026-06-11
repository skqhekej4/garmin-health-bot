"""
통합 실행 스크립트 - Windows 작업 스케줄러용
=============================================
매일 아침 자동 실행:
  1) 가민 데이터 수집 → 시트 저장 (step3)
  2) 문진 전송 + 답변 수집 대기 (step4)
  3) Claude 분석 + 리포트 전송 (step5)

실행:
    python run_daily.py              (전체 자동)
    python run_daily.py --collect    (1: 데이터 수집만)
    python run_daily.py --question   (2: 문진만)
    python run_daily.py --report     (3: 분석+리포트만)
"""

import sys
import os
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
PYTHON = sys.executable


def run_script(script_name, args=None):
    """파이썬 스크립트 실행"""
    cmd = [PYTHON, str(BASE_DIR / script_name)]
    if args:
        cmd.extend(args)

    print(f"\n{'='*55}")
    print(f"  실행: {script_name} {' '.join(args or [])}")
    print(f"{'='*55}\n")

    result = subprocess.run(cmd, cwd=str(BASE_DIR))
    return result.returncode == 0


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--all"

    print("=" * 55)
    print("  🏃 가민 건강 코치 - 일일 자동 실행")
    print("=" * 55)

    if mode in ("--all", "--collect"):
        # 1단계: 가민 데이터 수집 + 시트 저장
        run_script("step3_collect_and_save.py")

    if mode in ("--all", "--question"):
        # 2단계: 문진 전송 + 답변 수집 (30분 대기)
        run_script("step4_telegram_bot.py", ["--send-and-listen"])

    if mode in ("--all", "--report"):
        # 3단계: Claude 분석 + 리포트 전송
        run_script("step5_analyze_report.py")

    print(f"\n{'='*55}")
    print(f"  일일 실행 완료!")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
