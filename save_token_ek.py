"""
은경씨 가민 토큰 최초 저장 스크립트 (429 재시도 포함)
"""

import os
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import garth

BASE_DIR = Path(__file__).parent.resolve()

email = os.getenv("GARMIN_EMAIL_EK")
password = os.getenv("GARMIN_PASSWORD_EK")
token_dir = str(BASE_DIR / ".garth_tokens_eunkyeong")

if not email or not password or email == "eunkyeong_garmin_email@example.com":
    print("[!] .env에 은경씨 가민 계정 정보를 먼저 입력하세요!")
    exit()

print(f"은경씨 가민 로그인 시도: {email}")
print("(MFA 코드가 필요하면 이메일 확인 후 입력하세요)\n")

max_retries = 4
delays = [0, 60, 120, 180]  # 0초, 1분, 2분, 3분 대기

for attempt in range(max_retries):
    if delays[attempt] > 0:
        print(f"  {delays[attempt]}초 대기 후 재시도... ({attempt+1}/{max_retries})")
        time.sleep(delays[attempt])

    try:
        garth.configure(domain="garmin.com")
        garth.login(email, password)
        garth.save(token_dir)
        print(f"\n✅ 토큰 저장 완료: {token_dir}")
        print("이후 자동 로그인됩니다!")
        exit()
    except Exception as e:
        err = str(e)
        if "429" in err:
            print(f"  429 에러 (너무 많은 요청) - 가민 서버가 잠시 차단 중")
        else:
            print(f"  로그인 실패: {e}")
            break

print(f"\n❌ 모든 시도 실패")
print("5~10분 후 다시 실행해보세요.")
