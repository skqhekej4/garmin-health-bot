"""
가민 토큰 저장 스크립트 (최초 1회만 실행)
==========================================
MFA 코드를 입력하여 로그인한 뒤, 토큰을 저장합니다.
이후 자동화 스크립트에서는 이 토큰으로 로그인하므로 MFA 불필요.

토큰 만료 시 이 스크립트를 다시 실행하면 됩니다.

실행: python save_token.py
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import garth

# 토큰 저장 경로 (프로젝트 폴더 기준 절대경로)
BASE_DIR = Path(__file__).parent.resolve()
TOKEN_DIR_RYU = BASE_DIR / ".garth_tokens_ryu"
TOKEN_DIR_EK = BASE_DIR / ".garth_tokens_eunkyeong"


def save_garmin_token(email, password, token_dir, display_name):
    print(f"\n{'='*50}")
    print(f"  {display_name} 가민 토큰 저장")
    print(f"{'='*50}")

    garth.configure(domain="garmin.com")

    print(f"\n로그인: {email}")
    print("MFA 코드가 요청되면 가민 앱/이메일에서 확인 후 입력하세요.\n")

    try:
        garth.login(email, password)
        garth.save(str(token_dir))
        print(f"\n토큰 저장 완료! -> {token_dir}")
        print("이제 자동화 스크립트에서 MFA 없이 접속 가능합니다.")
        return True
    except Exception as e:
        print(f"\n로그인 실패: {e}")
        return False


def main():
    print("=" * 50)
    print("  가민 토큰 저장 (최초 1회)")
    print("=" * 50)

    # 류우
    ryu_email = os.getenv("GARMIN_EMAIL_RYU")
    ryu_password = os.getenv("GARMIN_PASSWORD_RYU")

    if ryu_email and ryu_password:
        save_garmin_token(ryu_email, ryu_password, TOKEN_DIR_RYU, "류우")
    else:
        print("\n[!] .env에 GARMIN_EMAIL_RYU 정보 없음")

    # 은경씨 (설정된 경우만)
    ek_email = os.getenv("GARMIN_EMAIL_EK")
    ek_password = os.getenv("GARMIN_PASSWORD_EK")

    if ek_email and ek_password and ek_email != "eunkyeong_garmin_email@example.com":
        import importlib
        importlib.reload(garth)
        save_garmin_token(ek_email, ek_password, TOKEN_DIR_EK, "은경")
    else:
        print("\n[참고] 은경씨 계정은 7단계에서 추가")

    print("\n\n완료! 이제 step1_garth_test.py 를 다시 실행해보세요.")
    print("MFA 없이 바로 데이터가 나오면 성공입니다.")


if __name__ == "__main__":
    main()
