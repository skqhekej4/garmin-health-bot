# -*- coding: utf-8 -*-
"""Cloud auth experiment: does python-garminconnect 0.3.6 (+ curl_cffi Chrome-TLS
impersonation) beat the 429 that garth gets from a GitHub Actions datacenter IP?

Run in GitHub Actions (datacenter IP). Prints clear SUCCESS/FAIL + which endpoint
failed, so we can decide cloud-only vs residential collection.
"""
import os, datetime, traceback
from dotenv import load_dotenv
load_dotenv()

today = datetime.date.today().isoformat()
email = os.getenv("GARMIN_EMAIL_RYU")
pw = os.getenv("GARMIN_PASSWORD_RYU")

print("=== garminconnect 0.3.6 + curl_cffi cloud auth test ===")
try:
    import garminconnect
    print("garminconnect:", getattr(garminconnect, "__version__", "?"))
except Exception as e:
    print("garminconnect import FAIL:", e)
try:
    import curl_cffi
    print("curl_cffi:", getattr(curl_cffi, "__version__", "?"))
except Exception as e:
    print("curl_cffi import FAIL:", e)

from garminconnect import Garmin

ok = False
# try a couple of constructor signatures across versions
for desc, factory in [
    ("Garmin(email, password)", lambda: Garmin(email, pw)),
    ("Garmin(email=, password=)", lambda: Garmin(email=email, password=pw)),
]:
    try:
        print(f"\n--- attempt: {desc} ---")
        g = factory()
        g.login()
        print("LOGIN OK (fresh login from datacenter IP succeeded!)")
        try:
            tr = g.get_training_readiness(today)
            print("READINESS OK:", str(tr)[:200])
        except Exception as e:
            print("readiness fetch err:", type(e).__name__, str(e)[:200])
        ok = True
        break
    except TypeError as e:
        print("signature mismatch:", str(e)[:120])
        continue
    except Exception as e:
        print("LOGIN FAIL:", type(e).__name__, str(e)[:300])
        if "429" in str(e):
            print(">>> STILL 429 from datacenter IP — curl_cffi did NOT beat the block")
        traceback.print_exc()
        break

print("\nRESULT:", "CLOUD-ONLY FIX WORKS" if ok else "cloud login still blocked")
