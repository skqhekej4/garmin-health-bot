"""
3단계: 가민 데이터 수집 → 구글 시트 자동 저장
==============================================
매일 실행하여 어제/오늘 건강 데이터를 수집하고
구글 시트 '가민건강코치'에 저장합니다.

수집 항목:
- 수면시간, 수면점수
- HRV (수면 중 측정값)
- 안정심박 (수면 데이터 기준)
- 스트레스 (일일 평균)
- 운동종류, 운동시간, 거리, 페이스
- 걸음수, 칼로리
- 체중, 체지방률, 근육량 (체중계 연동 시)

실행:
python step3_collect_and_save.py
"""

import os
import json
import importlib
from pathlib import Path
from datetime import date, datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

import garth
from garth.exc import GarthHTTPError
import gspread
from google.oauth2.service_account import Credentials

# ─── 설정 ───
BASE_DIR = Path(__file__).parent.resolve()
SERVICE_ACCOUNT_FILE = str(BASE_DIR / os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"))
SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "가민건강코치")

# 시트 헤더 (step3/4/5 공통) — v2: 기존 24개 컬럼은 그대로 두고 뒤에만 추가(과거 데이터 보존)
HEADERS = [
    "날짜",
    "수면시간", "수면점수", "HRV", "안정심박", "스트레스",
    "운동종류", "운동시간(분)", "거리(km)", "페이스", "걸음수", "칼로리",
    "체중", "체지방률", "근육량",
    # ── (구) 문진 컬럼 — v2부터 미사용, 과거 데이터 보존용으로 유지 ──
    "컨디션", "수면체감", "음주", "식사", "수분", "카페인", "통증부위", "운동계획", "생리주기",
    # ── v2 추가: 가민 종합지표 (자동수집) ──
    "HRV상태", "바디배터리", "훈련준비도", "훈련상태", "급성부하", "부하비율", "VO2max",
    # ── v2 추가: Hooper 기반 문진 ──
    "피로도", "근육통", "심리스트레스", "수면만족", "특이사항",
]


# ─── 가민 로그인 ───
def garmin_login(email, password, user_name):
    """저장된 토큰으로 로그인 (실패 시 비밀번호 재시도)"""
    token_dir = str(BASE_DIR / f".garth_tokens_{user_name}")

    # 1) 저장된 토큰 우선 시도
    if os.path.exists(token_dir):
        try:
            garth.configure(domain="garmin.com")
            garth.resume(token_dir)
            # 토큰 유효성 확인
            garth.connectapi(
                "/activitylist-service/activities/search/activities",
                params={"start": 0, "limit": 1}
            )
            garth.save(token_dir)
            print(f"  [로그인] {user_name} 토큰 로그인 성공")
            return True
        except Exception as e:
            print(f"  [로그인] 토큰 실패: {e}")

    # 2) 비밀번호 로그인
    try:
        garth.configure(domain="garmin.com")
        garth.login(email, password)
        garth.save(token_dir)
        print(f"  [로그인] {user_name} 비밀번호 로그인 성공 (토큰 저장)")
        return True
    except Exception as e:
        print(f"  [로그인] 실패: {e}")
        return False


# ─── 데이터 수집 함수들 ───
def get_sleep_data(target_date):
    """수면시간(시간), 수면점수, 안정심박 반환"""
    result = {"수면시간": "", "수면점수": "", "안정심박": ""}
    try:
        data = garth.connectapi(
            f"/wellness-service/wellness/dailySleepData/{garth.client.username}",
            params={"date": target_date, "nonSleepBufferMinutes": 60}
        )
        if data:
            dto = data.get("dailySleepDTO", {})
            # 수면시간 (초 → 시간)
            sleep_sec = dto.get("sleepTimeSeconds", 0)
            if sleep_sec:
                result["수면시간"] = str(round(sleep_sec / 3600, 1))

            # 수면점수
            score = dto.get("sleepScores", {}).get("overall", {}).get("value")
            if score is not None:
                result["수면점수"] = str(score)

            # 안정심박 (수면 중 평균심박 — avgHeartRate 또는 averageSpO2HRSleep)
            rhr = dto.get("avgHeartRate") or dto.get("averageSpO2HRSleep")
            if rhr is not None:
                result["안정심박"] = str(int(rhr))
    except Exception as e:
        print(f"    수면 데이터 실패: {e}")

    return result


def get_hrv_data(target_date):
    """HRV 수치 + HRV상태(균형/불균형/낮음) 반환 (dict)"""
    result = {"HRV": "", "HRV상태": ""}
    try:
        data = garth.connectapi(f"/hrv-service/hrv/{target_date}")
        if data:
            # 방법 1: hrvSummary (실제 Epix Pro / FR265S 응답 구조)
            summary = data.get("hrvSummary", {}) or {}
            if summary:
                val = summary.get("lastNightAvg") or summary.get("weeklyAvg")
                if val:
                    result["HRV"] = str(val)
                # HRV 상태: BALANCED / UNBALANCED / LOW / POOR
                status = summary.get("status")
                if status:
                    result["HRV상태"] = str(status)

            # 방법 2: 최상위 레벨 fallback
            if not result["HRV"]:
                val = data.get("lastNightAvg")
                if val:
                    result["HRV"] = str(val)

            # 방법 3: hrvSummaries 배열 fallback
            if not result["HRV"]:
                summaries = data.get("hrvSummaries", []) or []
                if summaries:
                    s = summaries[0]
                    val = s.get("lastNightAvg") or s.get("weeklyAvg")
                    if val:
                        result["HRV"] = str(val)
                    if not result["HRV상태"] and s.get("status"):
                        result["HRV상태"] = str(s.get("status"))
    except Exception as e:
        print(f"    HRV 데이터 실패: {e}")

    return result


def get_training_readiness(target_date):
    """가민 훈련 준비도(0~100) 반환 — 6개 요소 종합 점수"""
    result = {"훈련준비도": ""}
    try:
        data = garth.connectapi(f"/metrics-service/metrics/trainingreadiness/{target_date}")
        if data and isinstance(data, list) and len(data) > 0:
            latest = data[-1]  # 당일 가장 최근 스냅샷
            score = latest.get("score")
            if score is not None:
                result["훈련준비도"] = str(score)
    except Exception as e:
        print(f"    훈련준비도 실패: {e}")
    return result


def get_training_status(target_date):
    """훈련상태(생산적/유지 등), 급성부하, 급성:만성 부하비율, VO2max 반환"""
    result = {"훈련상태": "", "급성부하": "", "부하비율": "", "VO2max": ""}
    try:
        data = garth.connectapi(f"/metrics-service/metrics/trainingstatus/aggregated/{target_date}")
        if not data:
            return result

        # VO2max
        try:
            vo2 = data.get("mostRecentVO2Max", {}).get("generic", {}).get("vo2MaxValue")
            if vo2:
                result["VO2max"] = str(vo2)
        except Exception:
            pass

        # 훈련상태 + 부하 (기기별 dict이므로 첫 유효 기기 사용)
        mrts = data.get("mostRecentTrainingStatus", {}) or {}
        latest_map = mrts.get("latestTrainingStatusData", {}) or {}
        device_data = None
        for _, v in latest_map.items():
            if v:
                device_data = v
                break
        if device_data:
            phrase = device_data.get("trainingStatusFeedbackPhrase") or device_data.get("trainingStatus")
            if phrase:
                result["훈련상태"] = str(phrase)
            atl = device_data.get("acuteTrainingLoadDTO", {}) or {}
            acute = atl.get("dailyTrainingLoadAcute") or atl.get("acuteTrainingLoad")
            if acute is not None:
                result["급성부하"] = str(round(acute))
            ratio = atl.get("dailyAcuteChronicWorkloadRatio")
            if ratio is not None:
                result["부하비율"] = str(round(ratio, 2))
    except Exception as e:
        print(f"    훈련상태 실패: {e}")
    return result


def get_body_battery(target_date):
    """바디배터리 당일 최고치(아침 회복 수준) 반환"""
    result = {"바디배터리": ""}
    try:
        data = garth.connectapi(
            "/wellness-service/wellness/bodyBattery/reports/daily",
            params={"startDate": target_date, "endDate": target_date},
        )
        if data and isinstance(data, list) and len(data) > 0:
            arr = data[0].get("bodyBatteryValuesArray", []) or []
            levels = []
            for entry in arr:
                # 형태가 [ts, level] 또는 [ts, status, level] 둘 다 처리
                if isinstance(entry, list) and len(entry) >= 2:
                    candidate = entry[-1]
                    if isinstance(candidate, (int, float)) and 0 <= candidate <= 100:
                        levels.append(candidate)
            if levels:
                result["바디배터리"] = str(int(max(levels)))
    except Exception as e:
        print(f"    바디배터리 실패: {e}")
    return result


# 운동 종류 한글 매핑 (공용)
TYPE_MAP = {
    "running": "러닝", "trail_running": "트레일러닝", "treadmill_running": "트레드밀",
    "walking": "걷기", "hiking": "하이킹", "cycling": "사이클", "indoor_cycling": "실내사이클",
    "swimming": "수영", "pool_swimming": "수영", "open_water_swimming": "수영",
    "strength_training": "근력운동", "yoga": "요가", "elliptical": "일립티컬", "golf": "골프",
}
RUN_TYPES = ("running", "trail_running", "treadmill_running")


def get_activity_trend(days=28):
    """최근 N일 운동 이력을 분석해 '꾸준함/추세' 요약 반환 (dict).
    하루 1개만 보는 시트와 달리, 가민에서 활동 전체를 끌어와 주별 빈도/거리/공백을 계산."""
    out = {
        "분석기간": f"최근 {days}일", "총운동수": 0, "주당평균": 0.0, "총거리km": 0.0,
        "마지막운동후일수": None, "최장공백일": 0, "종목분포": {}, "주별": [], "텍스트": "",
    }
    try:
        acts = garth.connectapi(
            "/activitylist-service/activities/search/activities",
            params={"start": 0, "limit": 60},
        ) or []

        today = date.today()
        cutoff = today - timedelta(days=days - 1)

        rows = []  # (날짜, 종목, 거리km, 시간분, 러닝여부)
        for a in acts:
            ds = (a.get("startTimeLocal") or "")[:10]
            if not ds:
                continue
            try:
                d = datetime.strptime(ds, "%Y-%m-%d").date()
            except Exception:
                continue
            if d < cutoff or d > today:
                continue
            tkey = a.get("activityType", {}).get("typeKey", "")
            dist = round((a.get("distance") or 0) / 1000.0, 1)
            dur = round((a.get("duration") or 0) / 60.0)
            rows.append((d, TYPE_MAP.get(tkey, tkey or "기타"), dist, dur, tkey in RUN_TYPES))

        rows.sort(key=lambda r: r[0])

        out["총운동수"] = len(rows)
        out["총거리km"] = round(sum(r[2] for r in rows if r[4]), 1)  # 러닝 계열 거리 합
        out["주당평균"] = round(len(rows) / (days / 7.0), 1)

        for _, name, _, _, _ in rows:
            out["종목분포"][name] = out["종목분포"].get(name, 0) + 1

        # 주별(최근 7일=0주차) 횟수/거리/시간
        weekly = {}
        for d, _, dist, dur, is_run in rows:
            wk = (today - d).days // 7
            w = weekly.setdefault(wk, {"횟수": 0, "거리": 0.0, "시간": 0})
            w["횟수"] += 1
            w["거리"] += dist if is_run else 0
            w["시간"] += dur
        out["주별"] = [{"주차": k, **v} for k, v in sorted(weekly.items())]

        if rows:
            out["마지막운동후일수"] = (today - rows[-1][0]).days

        # 윈도우 내 최장 연속 휴식일
        wd = set(r[0] for r in rows)
        longest = cur = 0
        for i in range(days):
            dd = cutoff + timedelta(days=i)
            if dd in wd:
                cur = 0
            else:
                cur += 1
                longest = max(longest, cur)
        out["최장공백일"] = longest

        # 사람이 읽는 요약 텍스트
        wk_label = {0: "이번주", 1: "1주전", 2: "2주전", 3: "3주전"}
        wk_lines = []
        for w in out["주별"]:
            lbl = wk_label.get(w["주차"], f"{w['주차']}주전")
            seg = f"{lbl} {w['횟수']}회"
            if w["거리"]:
                seg += f"/{round(w['거리'], 1)}km"
            wk_lines.append(seg)
        types = ", ".join(f"{k} {v}" for k, v in sorted(out["종목분포"].items(), key=lambda x: -x[1]))
        out["텍스트"] = (
            f"{out['분석기간']}: 총 {out['총운동수']}회 (주당 {out['주당평균']}회), 러닝거리 {out['총거리km']}km\n"
            f"주별: {' · '.join(wk_lines) if wk_lines else '기록 없음'}\n"
            f"마지막 운동: {out['마지막운동후일수']}일 전 · 최장 휴식: {out['최장공백일']}일\n"
            f"종목 분포: {types if types else '없음'}"
        )
    except Exception as e:
        print(f"    운동추세 실패: {e}")
    return out


def get_race_predictions():
    """가민 예상 레이스 기록(5K/10K/하프/풀, 초 단위) 반환 — 페이스존 추천 기준"""
    out = {}
    try:
        dn = garth.client.username
        rp = garth.connectapi(f"/metrics-service/metrics/racepredictions/latest/{dn}")
        if rp:
            for api_key, label in [("time5K", "5K"), ("time10K", "10K"),
                                   ("timeHalfMarathon", "하프"), ("timeMarathon", "풀")]:
                v = rp.get(api_key)
                if v:
                    out[label] = int(v)
    except Exception as e:
        print(f"    레이스예측 실패: {e}")
    return out


def save_activity_trend(user_key):
    """운동 추세 + 레이스 예측을 파일로 저장 (step5가 읽어 리포트에 사용)"""
    trend = get_activity_trend(28)
    trend["레이스예측"] = get_race_predictions()
    try:
        path = BASE_DIR / f"training_trend_{user_key}.json"
        path.write_text(json.dumps(trend, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [{user_key}] 운동추세 저장: 총 {trend.get('총운동수')}회 / 주당 {trend.get('주당평균')}회")
    except Exception as e:
        print(f"  [{user_key}] 운동추세 저장 실패: {e}")


def get_stress_data(target_date):
    """일일 평균 스트레스 반환"""
    try:
        data = garth.connectapi(
            f"/wellness-service/wellness/dailyStress/{target_date}"
        )
        if data:
            # 방법 1: overallStressLevel
            val = data.get("overallStressLevel")
            if val is not None and val > 0:
                return str(val)

            # 방법 2: averageStressLevel
            val = data.get("averageStressLevel")
            if val is not None and val > 0:
                return str(val)

            # 방법 3: bodyBatteryStatList 에서 계산
            stress_vals = data.get("stressValuesArray", [])
            if stress_vals:
                # [timestamp, stress_level] 형태, -1/-2는 무효값
                valid = [s[1] for s in stress_vals if len(s) > 1 and s[1] > 0]
                if valid:
                    return str(round(sum(valid) / len(valid)))

    except Exception as e:
        print(f"    스트레스 데이터 실패: {e}")

    return ""


def get_activity_data(target_date):
    """당일 운동 데이터 반환 (가장 최근 활동 1개)"""
    result = {"운동종류": "", "운동시간(분)": "", "거리(km)": "", "페이스": ""}
    try:
        activities = garth.connectapi(
            "/activitylist-service/activities/search/activities",
            params={"start": 0, "limit": 10}
        )
        if activities:
            for act in activities:
                # 활동 날짜 확인
                act_date = act.get("startTimeLocal", "")[:10]
                if act_date == target_date:
                    # 운동 종류
                    act_type = act.get("activityType", {}).get("typeKey", "")
                    act_name = act.get("activityName", act_type)

                    # 한글 변환 (주요 운동)
                    type_map = {
                        "running": "러닝",
                        "trail_running": "트레일러닝",
                        "treadmill_running": "트레드밀",
                        "walking": "걷기",
                        "hiking": "하이킹",
                        "cycling": "사이클",
                        "swimming": "수영",
                        "pool_swimming": "수영",
                        "strength_training": "근력운동",
                        "yoga": "요가",
                        "elliptical": "일립티컬",
                        "golf": "골프",
                    }
                    result["운동종류"] = type_map.get(act_type, act_name)

                    # 운동시간 (초 → 분)
                    duration = act.get("duration", 0)
                    if duration:
                        result["운동시간(분)"] = str(round(duration / 60))

                    # 거리 (m → km)
                    distance = act.get("distance", 0)
                    if distance:
                        result["거리(km)"] = str(round(distance / 1000, 2))

                    # 페이스 계산 (러닝 계열만)
                    if act_type in ("running", "trail_running", "treadmill_running") and distance and duration:
                        pace_sec_per_km = duration / (distance / 1000)
                        pace_min = int(pace_sec_per_km // 60)
                        pace_sec = int(pace_sec_per_km % 60)
                        result["페이스"] = f"{pace_min}'{pace_sec:02d}\""

                    break  # 첫 번째 매칭 활동만

    except Exception as e:
        print(f"    활동 데이터 실패: {e}")

    return result


def get_daily_summary(target_date):
    """걸음수, 칼로리 반환"""
    result = {"걸음수": "", "칼로리": ""}
    try:
        # 방법 1: usersummary (사용자명 없이)
        data = garth.connectapi(
            "/usersummary-service/usersummary/daily",
            params={"calendarDate": target_date}
        )
        if data:
            steps = data.get("totalSteps")
            if steps is not None:
                result["걸음수"] = str(steps)
            cal = data.get("totalKilocalories")
            if cal is not None:
                result["칼로리"] = str(cal)
            return result
    except Exception:
        pass

    # 방법 2: wellness daily chart
    try:
        data = garth.connectapi(
            f"/wellness-service/wellness/dailySummaryChart/{target_date}"
        )
        if data and isinstance(data, list) and len(data) > 0:
            day = data[0]
            steps = day.get("totalSteps")
            if steps is not None:
                result["걸음수"] = str(steps)
            cal = day.get("totalKilocalories")
            if cal is not None:
                result["칼로리"] = str(cal)
    except Exception as e:
        print(f"    일일 요약 실패: {e}")

    return result


def get_weight_data(target_date):
    """체중, 체지방률, 근육량 반환 (체중계 연동 시)"""
    result = {"체중": "", "체지방률": "", "근육량": ""}
    try:
        today = date.today().isoformat()
        data = garth.connectapi(
            "/weight-service/weight/dateRange",
            params={"startDate": target_date, "endDate": today}
        )
        if data and data.get("dailyWeightSummaries"):
            latest = data["dailyWeightSummaries"][-1]
            summaries = latest.get("summaryValues", [])
            for sv in summaries:
                # weight in grams
                if sv.get("qualifierKey") == "WEIGHT":
                    result["체중"] = str(round(sv.get("value", 0) / 1000, 1))
                elif sv.get("qualifierKey") == "BODY_FAT":
                    result["체지방률"] = str(round(sv.get("value", 0), 1))
                elif sv.get("qualifierKey") == "MUSCLE_MASS":
                    result["근육량"] = str(round(sv.get("value", 0) / 1000, 1))

            # 대안: 첫 번째 값이 체중 (구조가 단순할 때)
            if not result["체중"] and summaries:
                val = summaries[0].get("value", 0)
                if val > 10000:  # grams
                    result["체중"] = str(round(val / 1000, 1))
    except Exception as e:
        # 체중계 미연동 시 정상적으로 실패 → 무시
        pass

    return result


# ─── 전체 데이터 수집 ───
def collect_garmin_data(target_date):
    """하루치 가민 데이터를 모두 수집하여 딕셔너리로 반환"""
    print(f"\n  [{target_date}] 데이터 수집 시작...")

    data = {"날짜": target_date}

    # 수면 (수면시간, 수면점수, 안정심박)
    print(f"    수면 데이터...")
    sleep = get_sleep_data(target_date)
    data.update(sleep)

    # HRV (수치 + 상태)
    print(f"    HRV...")
    data.update(get_hrv_data(target_date))

    # 스트레스
    print(f"    스트레스...")
    data["스트레스"] = get_stress_data(target_date)

    # 가민 종합지표 (v2 추가)
    print(f"    훈련준비도...")
    data.update(get_training_readiness(target_date))
    print(f"    훈련상태/부하/VO2max...")
    data.update(get_training_status(target_date))
    print(f"    바디배터리...")
    data.update(get_body_battery(target_date))

    # 운동/활동
    print(f"    운동 활동...")
    activity = get_activity_data(target_date)
    data.update(activity)

    # 걸음수/칼로리
    print(f"    걸음수/칼로리...")
    summary = get_daily_summary(target_date)
    data.update(summary)

    # 체중
    print(f"    체중...")
    weight = get_weight_data(target_date)
    data.update(weight)

    # 문진 항목은 빈칸 (4단계 텔레그램 봇에서 채움) — v2 Hooper 문진
    for qk in ["피로도", "근육통", "심리스트레스", "수면만족", "운동계획", "특이사항", "생리주기"]:
        data[qk] = ""

    # 수집 결과 출력
    print(f"\n    ── 수집 결과 ──")
    for k, v in data.items():
        if v:
            print(f"    {k}: {v}")
        else:
            print(f"    {k}: (없음)")

    return data


# ─── 구글 시트 저장 ───
def get_google_sheet():
    """구글 시트 연결"""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open(SHEET_NAME)


def save_to_sheet(spreadsheet, tab_name, data):
    """수집 데이터를 구글 시트의 해당 탭에 저장"""
    ws = spreadsheet.worksheet(tab_name)

    # 헤더 확인/갱신
    first_row = ws.row_values(1)
    if not first_row or first_row != HEADERS:
        ws.update(values=[HEADERS], range_name='A1')
        print(f"    -> '{tab_name}' 헤더 갱신 완료")

    # 기존 데이터에서 같은 날짜 행 찾기
    all_dates = ws.col_values(1)  # A열 = 날짜
    target_date = data["날짜"]
    row_num = None

    for i, d in enumerate(all_dates):
        if d == target_date:
            row_num = i + 1  # 1-based index
            break

    # 데이터를 헤더 순서에 맞게 배열
    row_data = [data.get(h, "") for h in HEADERS]

    if row_num:
        # 기존 행 업데이트 (문진 데이터가 있으면 유지)
        existing = ws.row_values(row_num)
        # 기존 문진 항목이 있으면 덮어쓰지 않기 (v2 Hooper 문진 + 구 컬럼 모두 보존)
        # 체중도 보존: 가민 체중계 미연동이라 문진으로 입력한 값을 빈값이 덮어쓰지 않도록
        questionnaire_cols = ["피로도", "근육통", "심리스트레스", "수면만족", "음주", "식사",
                              "운동계획", "특이사항", "생리주기", "체중",
                              "컨디션", "수면체감", "수분", "카페인", "통증부위"]
        for h in questionnaire_cols:
            idx = HEADERS.index(h)
            if idx < len(existing) and existing[idx]:
                row_data[idx] = existing[idx]

        ws.update(values=[row_data], range_name=f'A{row_num}')
        print(f"    -> '{tab_name}' {target_date} 데이터 업데이트 (행 {row_num})")
    else:
        # 새 행 추가 (마지막 데이터 행 다음)
        next_row = len(all_dates) + 1
        ws.update(values=[row_data], range_name=f'A{next_row}')
        print(f"    -> '{tab_name}' {target_date} 데이터 추가 (행 {next_row})")


# ─── 메인 실행 ───
def main():
    import sys
    import time

    print("=" * 55)
    print("  가민 건강 코치 - 3단계: 데이터 수집 + 시트 저장")
    print("=" * 55)

    # 수집 대상 날짜 결정
    # --days N : 과거 N일간 백필 (예: --days 30)
    # 기본값: 어제 + 오늘 (오늘 수면 데이터 포함)
    days = 1
    for i, arg in enumerate(sys.argv):
        if arg == "--days" and i + 1 < len(sys.argv):
            days = int(sys.argv[i + 1])

    target_dates = []
    for d in range(days, 0, -1):
        target_dates.append((date.today() - timedelta(days=d)).isoformat())
    # 오늘 날짜도 추가 (오늘 수면 데이터 수집)
    today_str = date.today().isoformat()
    if today_str not in target_dates:
        target_dates.append(today_str)

    print(f"\n  수집 대상: {len(target_dates)}일")
    print(f"  기간: {target_dates[0]} ~ {target_dates[-1]}")

    # ─── 류우 데이터 수집 ───
    print(f"\n{'─'*55}")
    print(f"  ▶ 류우 데이터 수집")
    print(f"{'─'*55}")

    ryu_email = os.getenv("GARMIN_EMAIL_RYU")
    ryu_password = os.getenv("GARMIN_PASSWORD_RYU")

    if not ryu_email or not ryu_password:
        print("  [!] .env에 GARMIN_EMAIL_RYU 정보 없음")
        return

    if not garmin_login(ryu_email, ryu_password, "ryu"):
        print("  [!] 류우 가민 로그인 실패. 토큰 갱신이 필요할 수 있습니다.")
        print("      → python save_token.py 실행 후 다시 시도하세요.")
        return

    ryu_data_list = []
    for td in target_dates:
        data = collect_garmin_data(td)
        ryu_data_list.append(data)
        if days > 1:
            time.sleep(1)  # API 부하 방지

    # 운동 추세 분석 저장 (로그인 상태일 때)
    print(f"\n    운동 추세 분석(최근 28일)...")
    save_activity_trend("ryu")

    # ─── 은경 데이터 수집 (설정된 경우만) ───
    ek_data_list = []
    ek_email = os.getenv("GARMIN_EMAIL_EK")
    ek_password = os.getenv("GARMIN_PASSWORD_EK")

    if ek_email and ek_password and ek_email != "eunkyeong_garmin_email@example.com":
        print(f"\n{'─'*55}")
        print(f"  ▶ 은경 데이터 수집")
        print(f"{'─'*55}")

        # garth 세션 초기화
        importlib.reload(garth)

        if garmin_login(ek_email, ek_password, "eunkyeong"):
            for td in target_dates:
                data = collect_garmin_data(td)
                ek_data_list.append(data)
                if days > 1:
                    time.sleep(1)
            print(f"\n    운동 추세 분석(최근 28일)...")
            save_activity_trend("eunkyeong")
        else:
            print("  [!] 은경 가민 로그인 실패")

    # ─── 구글 시트 저장 ───
    print(f"\n{'─'*55}")
    print(f"  ▶ 구글 시트 저장")
    print(f"{'─'*55}")

    try:
        spreadsheet = get_google_sheet()
        print(f"  시트 연결 성공: {spreadsheet.url}")

        # 류우 저장
        print(f"\n  [류우 시트 저장]")
        for data in ryu_data_list:
            save_to_sheet(spreadsheet, "류우", data)

        # 은경 저장
        if ek_data_list:
            print(f"\n  [은경 시트 저장]")
            for data in ek_data_list:
                save_to_sheet(spreadsheet, "은경", data)

    except Exception as e:
        print(f"  [!] 시트 저장 실패: {e}")
        print(f"      구글 시트가 서비스 계정에 공유되어 있는지 확인하세요.")
        return

    # ─── 완료 ───
    print(f"\n{'='*55}")
    print(f"  3단계 완료! ({len(target_dates)}일 수집)")
    print(f"  시트: {spreadsheet.url}")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
