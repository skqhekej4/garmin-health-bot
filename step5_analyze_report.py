"""
5단계: Claude API 분석 + 텔레그램 리포트 전송
==============================================
- 구글 시트에서 7일간 데이터 + 오늘 문진 읽기
- Claude API(Sonnet)로 건강 코칭 분석
- 개인별 텔레그램 리포트 전송

실행:
    python step5_analyze_report.py
    python step5_analyze_report.py --test   (분석만, 전송 안 함)
"""

import os
import sys
import json
import asyncio
from pathlib import Path
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.resolve()

# ─── 환경변수 ───
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID_RYU = os.getenv("TELEGRAM_CHAT_ID_RYU")
CHAT_ID_EK = os.getenv("TELEGRAM_CHAT_ID_EK")
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
SERVICE_ACCOUNT_FILE = str(BASE_DIR / os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"))
SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "가민건강코치")

# 시트 헤더 (step3/4/5 공통)
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

# 사용자 정보
USERS = {}
if CHAT_ID_RYU:
    USERS["류우"] = {
        "chat_id": CHAT_ID_RYU,
        "tab": "류우",
        "gender": "male",
        "trend_key": "ryu",
        "profile": "30대 남성(1992년생, 만 34세), 풀마라톤 러너(대구마라톤 완주), 트레일러닝(제주 종주 60km 완주). 단순 건강유지가 아니라 트레일 대회를 목표로 적극 '훈련'하는 러너 — 훈련 자극이 필요함. Garmin Epix Pro Gen 2 착용"
    }
if CHAT_ID_EK and CHAT_ID_EK != "eunkyeong_chat_id":
    USERS["은경"] = {
        "chat_id": CHAT_ID_EK,
        "tab": "은경",
        "gender": "female",
        "trend_key": "eunkyeong",
        "profile": "30대 여성(1992년생, 만 34세), 풀마라톤 완주 경험. 단순 건강유지가 아니라 트레일 대회를 목표로 적극 '훈련'하는 러너 — 훈련 자극이 필요함. Garmin Forerunner 265S 착용"
    }


# 목표 대회 (트레일) — 시기별 훈련 추천의 기준
RACES = [
    {"date": "2026-06-27", "name": "15km 트레일 대회", "confirmed": True},
    {"date": "2026-10", "name": "제주 UTMB 20K (트레일)", "confirmed": False},
    {"date": "2026-10", "name": "무주 GWTS 20K (트레일)", "confirmed": False},
]


def race_context():
    """다가오는 대회 D-day 텍스트 + 가장 가까운 확정 대회까지 일수"""
    today = date.today()
    lines, nearest = [], None
    for r in RACES:
        if r["confirmed"]:
            try:
                days = (date.fromisoformat(r["date"]) - today).days
                if days >= 0:
                    lines.append(f"• {r['name']}: {r['date']} (D-{days})")
                    if nearest is None or days < nearest:
                        nearest = days
            except Exception:
                pass
        else:
            lines.append(f"• {r['name']}: {r['date']} 예정")
    return ("\n".join(lines) if lines else "예정 대회 없음"), nearest


# ═══════════════════════════════════════════════
# 구글 시트에서 데이터 읽기
# ═══════════════════════════════════════════════
def get_sheet_data(tab_name, days=7):
    """구글 시트에서 최근 N일간 데이터 읽기"""
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    spreadsheet = gc.open(SHEET_NAME)
    ws = spreadsheet.worksheet(tab_name)

    all_rows = ws.get_all_values()
    if len(all_rows) <= 1:
        return []

    headers = all_rows[0]
    data_rows = all_rows[1:]

    result = []
    for row in data_rows:
        row_dict = {}
        for i, h in enumerate(headers):
            row_dict[h] = row[i] if i < len(row) else ""
        result.append(row_dict)

    # 날짜순 정렬 (오래된 순 → 최신)
    result.sort(key=lambda x: x.get("날짜", ""))

    # 최근 N일간 데이터만 추출
    recent = result[-days:] if len(result) > days else result

    return recent


def get_lifestyle_trend(tab, days=28):
    """음주/과식 추이 집계 — 최근 N일 문진에서 빈도 계산 (수면·회복 영향 추적)"""
    try:
        rows = get_sheet_data(tab, days=days)
    except Exception:
        return "음주/식사 기록 읽기 실패"

    total = drink_days = drink_heavy = overeat_days = 0
    recent_marks = []  # 최근 날짜별 음주 표시 (시각적 패턴용)
    for r in rows:
        al = (r.get("음주") or "").strip()
        meal = (r.get("식사") or "").strip()
        if al or meal:
            total += 1
        drank = al and al not in ("안마심",)
        if drank:
            drink_days += 1
            if al in ("5잔이상", "과음"):
                drink_heavy += 1
        if meal in ("과식", "야식", "늦은시간"):
            overeat_days += 1
        if al:
            recent_marks.append("🍺" if drank else "·")

    if total == 0:
        return "아직 음주/식사 기록이 거의 없음 (문진 데이터가 쌓이면 추이 분석 시작)"

    weeks = days / 7.0
    pattern = "".join(recent_marks[-14:]) if recent_marks else ""
    return (
        f"최근 {days}일 중 기록 {total}일 → 음주 {drink_days}일(주 {round(drink_days/weeks, 1)}회, "
        f"그중 많이 마신 날 {drink_heavy}일) · 과식/야식 {overeat_days}일"
        + (f"\n최근 음주 패턴(오래된→최근): {pattern}" if pattern else "")
    )


def get_weight_trend(tab, days=60):
    """체중 변화 추이 — 시트 체중 컬럼에서 기록된 값들로 최근값+변화 계산.
    기록 없으면 빈 문자열(리포트에서 체중 줄 생략)."""
    try:
        rows = get_sheet_data(tab, days=days)
    except Exception:
        return ""
    weights = []  # (날짜, kg)
    for r in rows:
        w = (r.get("체중") or "").strip()
        if w:
            try:
                weights.append((r.get("날짜", ""), float(w)))
            except ValueError:
                pass
    if not weights:
        return ""
    latest_d, latest = weights[-1]
    if len(weights) >= 2:
        first_d, first = weights[0]
        delta = round(latest - first, 1)
        sign = "+" if delta > 0 else ""
        arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
        return (f"최근 체중 {latest}kg, {first_d} 대비 {sign}{delta}kg {arrow} "
                f"(기록 {len(weights)}회, 최근 {days}일)")
    return f"최근 체중 {latest}kg (기록 1회 — 추이는 더 쌓이면 보여줄게요)"


def _fmt_pace(sec_per_km):
    """초/km → 'M:SS' 페이스 문자열"""
    m = int(sec_per_km // 60)
    s = int(round(sec_per_km % 60))
    if s == 60:
        m += 1
        s = 0
    return f"{m}:{s:02d}"


def _fmt_time(sec):
    """초 → 'H:MM:SS' 또는 'MM:SS'"""
    sec = int(sec)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build_pace_zones(race_pred):
    """레이스 예측에서 (예상기록 텍스트, 훈련 페이스존 텍스트) 계산"""
    if not race_pred:
        return "", ""
    t5, t10 = race_pred.get("5K"), race_pred.get("10K")
    th, tm = race_pred.get("하프"), race_pred.get("풀")

    preds = []
    if t5:
        preds.append(f"5K {_fmt_time(t5)}({_fmt_pace(t5/5)}/km)")
    if t10:
        preds.append(f"10K {_fmt_time(t10)}({_fmt_pace(t10/10)}/km)")
    if th:
        preds.append(f"하프 {_fmt_time(th)}({_fmt_pace(th/21.0975)}/km)")
    if tm:
        preds.append(f"풀 {_fmt_time(tm)}({_fmt_pace(tm/42.195)}/km)")
    pred_text = " · ".join(preds)

    zones = []
    if tm:
        mp = tm / 42.195
        zones.append(f"회복조깅: {_fmt_pace(mp+60)}~{_fmt_pace(mp+75)}/km")
        zones.append(f"이지(편하게): {_fmt_pace(mp+35)}~{_fmt_pace(mp+55)}/km")
        zones.append(f"마라톤페이스: {_fmt_pace(mp)}/km")
    if t10:
        zones.append(f"템포(역치): {_fmt_pace(t10/10)}~{_fmt_pace(t10/10+10)}/km")
    if t5:
        zones.append(f"인터벌(빠르게): {_fmt_pace(t5/5-5)}~{_fmt_pace(t5/5)}/km")
    zone_text = "\n".join(f"  - {z}" for z in zones)
    return pred_text, zone_text


# ═══════════════════════════════════════════════
# Claude API 분석
# ═══════════════════════════════════════════════
def build_prompt(user_name, user_info, data_rows):
    """Claude에게 보낼 분석 프롬프트 생성"""
    gender = user_info["gender"]
    profile = user_info["profile"]

    today_str = date.today().isoformat()

    # 운동 추세 + 레이스 예측 로드 (step3가 저장한 파일)
    trend_text = "운동 추세 데이터 없음"
    race_pred = {}
    try:
        tk = user_info.get("trend_key")
        if tk:
            tp = BASE_DIR / f"training_trend_{tk}.json"
            if tp.exists():
                td = json.loads(tp.read_text(encoding="utf-8"))
                if td.get("텍스트"):
                    trend_text = td["텍스트"]
                race_pred = td.get("레이스예측") or {}
    except Exception:
        pass

    # 레이스 예측 → 훈련 페이스존 (오늘 페이스 추천 + 월요일 수준요약 기준)
    pred_text, zone_text = build_pace_zones(race_pred)
    is_monday = date.today().weekday() == 0  # 0=월요일

    # 월요일에만 '내 수준' 블록 추가 (수준은 천천히 변해서 매일은 노이즈)
    if is_monday and pred_text:
        level_block = """

(5) *📊 이번주 내 수준*  ← 오늘이 월요일이라 추가. [내 러닝 수준] 데이터로 4~5줄:
- 예상 기록(5K/10K/하프/풀)을 보기 좋게 정리
- 심폐체력 점수와 **본인 나이대(프로필의 나이 참고) 기준** 위치(상위권/보통 등) 한 줄, 쉬운 말로
- 최근 4주 운동량·빈도 요약, 좋아졌으면 격려
동기부여 되게, 쉬운 말로."""
    else:
        level_block = ""

    race_text, days_to_race = race_context()

    # 음주/과식 추이 (수면·회복 영향 추적)
    lifestyle_text = "음주/식사 데이터 없음"
    try:
        lifestyle_text = get_lifestyle_trend(user_info["tab"], days=28)
    except Exception:
        pass

    # 체중 변화 추이 (문진으로 입력된 체중)
    weight_text = ""
    try:
        weight_text = get_weight_trend(user_info["tab"], days=60)
    except Exception:
        pass

    # ── 오늘 행 찾기 (날짜가 정확히 오늘인 행) ──
    today_row = None
    history = []
    for row in data_rows:
        if row.get("날짜") == today_str:
            today_row = row
        else:
            history.append(row)

    # 오늘 행이 없으면 마지막 행을 사용 (fallback)
    if today_row is None:
        today_row = data_rows[-1] if data_rows else {}
        history = data_rows[:-1] if len(data_rows) > 1 else []

    # 성별 특화 지시
    gender_instruction = ""
    if gender == "female":
        cycle = today_row.get("생리주기", "")
        if cycle and cycle != "해당없음":
            gender_instruction = f"""
[여성 건강 특화 분석]
현재 생리주기 상태: {cycle}
- 생리주기에 따른 운동 강도, 영양, 컨디션 변화를 반영하여 코칭해주세요.
- 생리중이면 철분 섭취와 저강도 운동 권장
- 생리전(PMS)이면 피로/부종/감정 변화 고려
- 배란기면 운동 퍼포먼스가 좋은 시기임을 반영
"""

    # ── 오늘 데이터 텍스트 ──
    today_detail = ""
    for k, v in today_row.items():
        if v:
            today_detail += f"  {k}: {v}\n"

    # ── 과거 데이터 텍스트 (추세 분석용, 날짜 명시) ──
    history_text = ""
    for row in history:
        row_date = row.get('날짜', '?')
        # 어제/그저께 등 라벨 추가
        try:
            from datetime import datetime
            rd = datetime.strptime(row_date, "%Y-%m-%d").date()
            days_ago = (date.today() - rd).days
            if days_ago == 1:
                label = f"{row_date} (어제)"
            elif days_ago == 2:
                label = f"{row_date} (그저께)"
            else:
                label = f"{row_date} ({days_ago}일 전)"
        except Exception:
            label = row_date

        history_text += f"\n[{label}] "
        vals = [f"{k}:{v}" for k, v in row.items() if k != "날짜" and v]
        history_text += ", ".join(vals)

    # ── 최근 운동 기록 별도 정리 (날짜 오인 방지) ──
    recent_exercise = ""
    for row in reversed(history):
        ex_type = row.get("운동종류", "")
        if ex_type:
            ex_date = row.get("날짜", "?")
            ex_dur = row.get("운동시간(분)", "")
            ex_dist = row.get("거리(km)", "")
            ex_pace = row.get("페이스", "")
            recent_exercise += f"  - {ex_date}: {ex_type}"
            if ex_dur:
                recent_exercise += f" {ex_dur}분"
            if ex_dist:
                recent_exercise += f" {ex_dist}km"
            if ex_pace:
                recent_exercise += f" (페이스 {ex_pace})"
            recent_exercise += "\n"
            if len(recent_exercise.split("\n")) > 4:
                break

    # 오늘 운동도 있으면 추가
    today_ex = today_row.get("운동종류", "")
    if today_ex:
        recent_exercise = f"  - {today_str} (오늘): {today_ex}" + \
            (f" {today_row.get('운동시간(분)', '')}분" if today_row.get('운동시간(분)') else "") + \
            (f" {today_row.get('거리(km)', '')}km" if today_row.get('거리(km)') else "") + \
            "\n" + recent_exercise

    yesterday_str = (date.today() - timedelta(days=1)).isoformat()

    prompt = f"""당신은 {user_name}님 전담 **스포츠 주치의 + 퍼포먼스 코치**입니다. 가민 워치의 객관 지표와 본인의 주관 문진을 종합해, "오늘 어떻게 움직여야 하는가"를 결정해주는 아침 브리핑을 작성합니다. 막연한 위로가 아니라 **데이터 근거에 기반한 실행 판단**을 줍니다.

[사용자 프로필]
{profile}

[★ 오늘({today_str}) 데이터 — 판단의 핵심 기준]
{today_detail}

[과거 데이터 — 추세/기준선 비교용]
{history_text}

[최근 운동 기록 — 날짜를 반드시 확인할 것]
{recent_exercise if recent_exercise else "  최근 운동 기록 없음"}

[★ 운동 추세 — 최근 28일 꾸준함 점검]
{trend_text}

[★ 음주·과식 추이 — 최근 28일 (수면/회복에 직접 영향)]
{lifestyle_text}

[체중 추이]
{weight_text if weight_text else "체중 입력 없음 (리포트에서 체중 줄 생략)"}

[★ 내 러닝 수준 & 훈련 페이스존 — 오늘 페이스 추천의 '기준']
예상 레이스 기록: {pred_text if pred_text else "데이터 없음"}
훈련 페이스존(가민 예측 기반):
{zone_text if zone_text else "  (레이스 예측 없음 — 최근 실제 페이스로 추천할 것)"}
오늘 심폐체력 점수: {today_row.get("VO2max") or "기록없음"}

[★ 다가오는 목표 대회 — 시기에 맞는 '훈련'을 추천할 것]
{race_text}
※ 이 사람은 단순 건강유지가 아니라 트레일 대회를 준비하는 러너. 컨디션 괜찮은 날은 '훈련 자극'(언덕·템포·롱런 등)을 적극 추천할 것.
{gender_instruction}
[지표 해석 기준 — 이 기준으로 객관 판단할 것]
• 훈련준비도(0~100): 가민이 수면·HRV·회복시간·부하·스트레스를 종합한 점수.
  - 1~24 매우낮음(휴식/회복) / 25~49 낮음(가볍게) / 50~74 보통(평소훈련 OK) / 75~100 높음(고강도 가능)
• HRV상태: BALANCED=자율신경 정상회복 / UNBALANCED·LOW·POOR=회복 저하, 강도 낮출 신호
• 부하비율(급성:만성 ACWR): 0.8~1.3 최적 / <0.8 디트레이닝(체력저하) / 1.3~1.5 주의 / >1.5 부상위험 구간(강도↑ 금지)
• 훈련상태: PRODUCTIVE=체력 향상중(좋음) / MAINTAINING=유지 / RECOVERY=회복기 / UNPRODUCTIVE·OVERREACHING=과부하·역효과(휴식 필요) / DETRAINING=훈련부족
• 바디배터리(0~100, 아침 최고치): 높을수록 밤사이 회복 양호. 50 미만이면 회복 부족
• Hooper 주관문진(피로도·근육통·심리스트레스 각 1~5는 높을수록 나쁨 / 수면만족 1~5는 높을수록 좋음)
  - 피로+근육통이 4~5로 높으면 가민이 좋게 나와도 과훈련/부상 위험을 우선시할 것
• 음주·과식: 어제 음주(특히 3~4잔 이상)나 과식/야식은 그날 밤 수면질과 심장 회복력을 직접 떨어뜨림.
  - 오늘 회복 지표(수면점수·심장회복력·몸배터리)가 나쁜데 어제 음주/과식이 있었으면 그게 원인일 가능성이 큼 → 반드시 인과로 연결해 설명
  - 최근 28일 음주가 잦으면(주 3회 이상) 회복이 만성적으로 눌릴 수 있음 → 추이로 부드럽게 짚어줄 것 (잔소리 아닌, 사실+영향)

[⚠️ 경고 신호 자동 감지 — 해당되면 리포트 최상단 '주의 신호'에 명시]
1. 안정심박이 최근 평균보다 +5bpm 이상 높음 + (HRV상태 LOW/UNBALANCED 또는 HRV 수치 급락) → 감염/과훈련 초기 신호 가능 → 고강도 금지, 컨디션 관찰
2. 부하비율 >1.5 → 부상 위험. 이번주 강도/거리 늘리지 말 것
3. 훈련상태가 OVERREACHING/UNPRODUCTIVE → 회복 우선
4. 주관(피로·근육통 4~5)과 객관(가민 양호)이 충돌 → 본인 몸 신호를 우선, 그 불일치를 짚어줄 것

[작성 원칙]
1. 반드시 오늘({today_str}) 기준. 과거는 추세 비교에만 사용
2. 데이터에 없는 항목은 언급/추측 금지 (예: [체중 추이]가 비어있으면 체중 줄 생략)
3. 객관(가민) vs 주관(문진)을 **교차 검증**하는 게 핵심. 둘이 맞으면 확신, 어긋나면 그 이유를 추론
4. ★★ 쉬운 말만 쓸 것 (가장 중요) ★★
   - 🚫 절대 쓰면 안 되는 단어(이 단어가 리포트에 한 번이라도 나오면 실패): "훈련준비도", "부하비율", "급성부하", "만성부하", "HRV", "VO2max", "자율신경", "디트레이닝", "ACWR", "BALANCED", "UNBALANCED", "PRODUCTIVE", "MAINTAINING". → 반드시 아래 쉬운 말로 바꿔 쓸 것.
   - 리포트(출력)에는 전문용어/영어약자를 절대 그대로 쓰지 말 것. 중학생도 바로 이해할 일상 한국어로.
   - 반드시 아래처럼 바꿔 쓰기:
     · HRV / HRV상태 → "심장 회복력" (BALANCED="잘 회복됨", UNBALANCED/LOW="회복이 덜 됨")
     · 안정심박 → "쉴 때 심장 박는 횟수"
     · 자율신경 → 쓰지 말 것. 그냥 "몸의 회복 상태"
     · 훈련준비도 → "오늘 운동해도 되는 정도(점수)"
     · 부하비율/ACWR → "그동안보다 무리한 정도"
     · 급성부하/만성부하 → "최근 운동량"
     · 훈련상태(PRODUCTIVE 등) → "체력이 좋아지는 중 / 유지 중 / 무리해서 떨어지는 중 / 쉬는 중"
     · 바디배터리 → "몸의 배터리(기운)"
     · VO2max → "심폐 체력 점수"
     · 디트레이닝 → "운동이 부족해 체력이 떨어지는 것"
   - 숫자를 말할 땐 그 숫자가 좋은지 나쁜지 한마디로 풀어줄 것 (예: "심장 회복력 34로 평소보다 낮아요")
5. ⚠️ 운동 날짜 정확히. "어제"는 실제 어제({yesterday_str}) 데이터일 때만. 아니면 "X월 X일"
6. 칭찬/위로보다 **판단과 근거**. 단, 위협적이지 않게 친한 코치가 말하듯 편하게

[리포트 형식 — 정확히 이 구조. 폰으로 읽으니 짧고 스캔되게. 섹션은 딱 이 4개(결론+3블록)만. *별표*는 굵게용. 각 줄은 짧게]

📊 *{user_name}님 아침 브리핑* ({today_str})

(1) 맨 위 — 오늘의 결론
{{신호등}} *오늘: <세게 운동 OK / 평소대로 / 살살 가볍게 / 푹 쉬기> 중 택1*
바로 아랫줄에 이유 한 줄(쉬운 말). 경고신호(위 1~4) 있으면 그 아래 "⚠️ ..." 한 줄 더(없으면 생략).
   - 신호등 색 규칙 엄수: 🟢=세게/평소대로, 🟡=살살 가볍게, 🔴=푹 쉬기. 색과 문구 반드시 일치.

(2) *🩺 오늘 몸 상태*  ← 각 항목 한 줄씩. 핵심 숫자 + 좋음/보통/낮음 한마디
• 수면: 시간·점수 한 줄 평가 (어제 술/과식 있었으면 여기서 "→ 그 영향" 원인 연결)
• 회복: 몸배터리·심장회복력·오늘 운동돼도 되는 점수를 묶어 한 줄 (좋음/보통/낮음 + 핵심 수치)
• 무리 정도: 위험하면 강조, 괜찮으면 "괜찮아요" 한마디로 짧게
• 내 느낌: 피로·근육통·스트레스 주관을 한 줄. 객관과 다르면 그 차이만 짚기

(3) *🏃 오늘 추천*  ← 실행 2개(최대 3개), 구체적으로. 위 결론과 일관되게
1. 운동: ★ **트레일 대회를 준비하는 러너 기준으로 '훈련'을 처방**(단순 유지·살살만 금지). [훈련 페이스존]에서 골라 구체적 페이스(범위)+시간/거리. 회복상태에 맞춰:
   - 🟢: **적극 훈련** — 템포/인터벌/언덕반복/롱런(거리·시간 점증) 중 택. 트레일 대비 **언덕·계단·트레일 주행** 적극 권장
   - 🟡: 이지~모더릿 30~50분 또는 가벼운 질주(스트라이드) 약간 섞기
   - 🔴: 회복 (대회까지 부상 금물). 단 "오늘 푹 쉬어 내일 강하게" 식 훈련 맥락으로
   [다가오는 대회] D-day가 가까우면(2~3주) 강도 유지+볼륨 줄이는 샤프닝, 멀면 베이스·지구력·언덕 빌딩. 통증 있으면 대체운동. (예: "언덕 반복 6회, 오르막 전력/내려와 회복")
2. 생활: 시간·양 포함한 처방 (예: "오후 2시 후 커피 금지", "물 2L+11시 취침")

(4) *📈 최근 추세*  ← 각 한 줄. 데이터 없는 줄은 통째로 생략
🏃 운동: 주당 빈도 + 이번주 증감/긴 공백 (주3회 이상이면 양호)
🍷 술·야식: 빈도 + 회복 영향. 잦으면(주3회+) 부드럽게 경고. 데이터 거의 없으면 이 줄 생략
⚖️ 체중: 최근 체중 + 변화 방향. [체중 추이] 데이터 없으면 이 줄 생략
{level_block}
"""
    return prompt


def analyze_with_claude(prompt):
    """Claude API로 분석"""
    import anthropic

    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )

    return message.content[0].text


# ═══════════════════════════════════════════════
# 텔레그램 전송
# ═══════════════════════════════════════════════
async def send_telegram(chat_id, text):
    """텔레그램으로 리포트 전송 (긴 메시지 분할)"""
    import requests

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    # 텔레그램 메시지 최대 4096자 → 분할
    max_len = 4000
    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        # 줄바꿈 기준으로 분할
        split_pos = text.rfind("\n", 0, max_len)
        if split_pos == -1:
            split_pos = max_len
        parts.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")

    for part in parts:
        payload = {
            "chat_id": chat_id,
            "text": part,
            "parse_mode": "Markdown",
        }
        try:
            resp = requests.post(url, data=payload, timeout=10)
            result = resp.json()
            if not result.get("ok"):
                # Markdown 파싱 실패 시 plain text로 재시도
                payload["parse_mode"] = ""
                requests.post(url, data=payload, timeout=10)
        except Exception as e:
            print(f"    전송 실패: {e}")


def report_user(user_name, info, send=True):
    """단일 사용자 리포트 생성 + (옵션) 텔레그램 발송. 성공 시 True."""
    try:
        data_rows = get_sheet_data(info["tab"], days=7)
    except Exception as e:
        print(f"  [{user_name}] 시트 읽기 실패: {e}")
        return False
    if not data_rows:
        print(f"  [{user_name}] 데이터 없음")
        return False
    report = analyze_with_claude(build_prompt(user_name, info, data_rows))
    print(f"\n----- {user_name} -----\n{report}\n----------")
    if send:
        asyncio.run(send_telegram(info["chat_id"], report))
        print(f"  [{user_name}] 리포트 전송 완료!")
    return True


# ═══════════════════════════════════════════════
# 메인 실행
# ═══════════════════════════════════════════════
async def main():
    test_mode = "--test" in sys.argv

    print("=" * 55)
    print("  가민 건강 코치 - 5단계: Claude 분석 + 리포트")
    print("=" * 55)

    if not CLAUDE_API_KEY or CLAUDE_API_KEY == "your_claude_api_key":
        print("  [!] CLAUDE_API_KEY가 .env에 없습니다!")
        return

    for user_name, info in USERS.items():
        print(f"\n{'─'*55}")
        print(f"  ▶ {user_name} 리포트 생성")
        print(f"{'─'*55}")

        # 1) 시트에서 데이터 읽기
        print(f"  구글 시트에서 데이터 읽는 중...")
        try:
            data_rows = get_sheet_data(info["tab"], days=7)
        except Exception as e:
            print(f"  [!] 시트 읽기 실패: {e}")
            continue

        if not data_rows:
            print(f"  [!] {user_name} 데이터가 없습니다. step3를 먼저 실행하세요.")
            continue

        print(f"  {len(data_rows)}일간 데이터 로드 완료")

        # 2) Claude 분석
        print(f"  Claude API 분석 중...")
        prompt = build_prompt(user_name, info, data_rows)

        try:
            report = analyze_with_claude(prompt)
            print(f"  분석 완료! (길이: {len(report)}자)")
        except Exception as e:
            print(f"  [!] Claude API 실패: {e}")
            continue

        # 3) 출력 / 전송
        print(f"\n{'─'*40}")
        print(report)
        print(f"{'─'*40}")

        if not test_mode:
            print(f"\n  텔레그램 전송 중...")
            await send_telegram(info["chat_id"], report)
            print(f"  [{user_name}] 리포트 전송 완료!")
        else:
            print(f"  (테스트 모드 - 전송 생략)")

    print(f"\n{'='*55}")
    print(f"  5단계 완료!")
    print(f"{'='*55}")


if __name__ == "__main__":
    asyncio.run(main())
