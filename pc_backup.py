# -*- coding: utf-8 -*-
"""PC backup for the '관망(observe)' period.

Garmin blocks GitHub's datacenter IPs (429), so cloud step3 cannot log in.
This runs on the user's PC (residential IP -> no 429) and does the FULL pipeline:
  1) collect Garmin -> Google Sheet + fresh trend JSON
  2) generate the COMPLETE report (workout analysis / race prediction need the JSON)
  3) deliver, state-guarded (skip if cloud already sent -> no double send)

Scheduled at 10:25 / 11:25 / 12:25 (5 min before each cloud check) so the PC
delivers the complete report first and the cloud just skips. Cloud keeps doing
the interactive questionnaire and we watch whether its Garmin login recovers.
"""
import sys, io, subprocess
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
from dotenv import load_dotenv
load_dotenv()

BASE = Path(__file__).parent.resolve()

# 1) collect Garmin -> sheet + trend JSON (residential IP, no 429)
print("[PC backup] step3 collect ...")
subprocess.run([sys.executable, str(BASE / "step3_collect_and_save.py")], cwd=str(BASE))

# 2) + 3) generate complete report and deliver (state-guarded)
import cloud_run as cr
import step5_analyze_report as s5

for name, info in cr.USERS.items():
    g = cr.get_goal(name)
    if g:
        info["goal"] = g

for name, info in cr.USERS.items():
    ans = cr.today_answers(info["tab"])
    answered = all(ans.get(k) for k in cr.CORE)
    synced = cr.today_garmin_present(info["tab"])
    state = cr.get_state(info["tab"])
    if answered and synced and state != "발송":
        if s5.report_user(name, info, send=True):
            cr.set_state(info["tab"], "발송")
            print(f"[{name}] PC 백업 발송 완료")
    else:
        print(f"[{name}] skip (문진={answered} 동기화={synced} 상태={state!r})")

print("[PC backup] done")
