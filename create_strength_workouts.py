# -*- coding: utf-8 -*-
"""
가민 커넥트 근력 워크아웃 자동 생성 도구 (비공식 workout-service API).

단계(phase):
  mine   : "참고용" 근력 워크아웃 스키마 채굴 + 운동명(exercise) 후보 검색 → 로그 출력
  dryrun : 페이로드 생성 + 사람이 읽을 수 있는 요약 출력 (API 호출 없음, 승인용)
  upload : 중복 검사 → POST 생성 → GET 재조회 검증 → workoutId 보고

인증은 저장된 garth 토큰(.garth_tokens_ryu / .garth_tokens_eunkyeong)만 사용.
토큰이 없거나 만료면 즉시 실패한다 — 비밀번호 로그인은 절대 시도하지 않는다.
"""

import argparse
import importlib
import json
import pathlib
import sys

BASE_DIR = pathlib.Path(__file__).resolve().parent

ACCOUNTS = {
    "ryu": ".garth_tokens_ryu",
    "ek": ".garth_tokens_eunkyeong",
}

REFERENCE_WORKOUT_NAME = "참고용"

# 운동명 매핑용 검색 키워드 (한글명 → 후보 검색어들)
EXERCISE_KEYWORDS = {
    "플랭크": ["PLANK"],
    "데드버그": ["DEAD_BUG", "DEADBUG"],
    "버드독": ["BIRD_DOG", "BIRDDOG", "BIRD"],
    "팔로프 프레스": ["PALLOF"],
    "스플릿 스쿼트": ["SPLIT_SQUAT"],
    "힙쓰러스트(글루트 브리지)": ["HIP_THRUST", "THRUST", "GLUTE", "BRIDGE", "HIP_RAISE"],
    "클램쉘": ["CLAM"],
    "몬스터워크(밴드 사이드 워크)": ["MONSTER", "LATERAL_WALK", "BAND_WALK", "SIDE_WALK", "LATERAL_BAND"],
    "푸시업": ["PUSH_UP", "PUSHUP"],
    "파이크 푸시업": ["PIKE"],
    "밴드 로우": ["BAND_ROW", "ROW"],
}


def resume_only_login(account):
    """저장된 토큰으로만 로그인. 실패 시 예외 — 비밀번호 로그인 금지."""
    import garth
    importlib.reload(garth)  # 계정 전환 시 세션 초기화 (step3 패턴)
    token_dir = str(BASE_DIR / ACCOUNTS[account])
    if not pathlib.Path(token_dir).exists():
        raise RuntimeError(f"토큰 디렉토리 없음: {token_dir}")
    garth.configure(domain="garmin.com")
    garth.resume(token_dir)
    # 유효성 확인 (읽기 전용 호출)
    garth.connectapi(
        "/activitylist-service/activities/search/activities",
        params={"start": 0, "limit": 1},
    )
    print(f"[로그인] {account}: 토큰 로그인 성공")
    return garth


def list_workouts(garth, limit=200):
    data = garth.connectapi(
        "/workout-service/workouts",
        params={"start": 0, "limit": limit, "myWorkoutsOnly": True},
    )
    if data is None:
        raise RuntimeError("GET /workout-service/workouts 응답이 None")
    return data


def section(title):
    print(f"\n===== {title} =====")


# ────────────────────────── phase: mine ──────────────────────────

def phase_mine(accounts):
    """참고용 워크아웃 스키마 채굴 + exercise 후보 검색."""
    ref_found = False
    for account in accounts:
        try:
            garth = resume_only_login(account)
        except Exception as e:
            print(f"[오류] {account} 토큰 로그인 실패: {e}")
            print("비밀번호 로그인은 시도하지 않습니다. 중단.")
            sys.exit(1)

        workouts = list_workouts(garth)
        section(f"WORKOUT_LIST ({account}) — 총 {len(workouts)}개")
        for w in workouts:
            print(f"  - id={w.get('workoutId')} name={w.get('workoutName')!r} "
                  f"sport={((w.get('sportType') or {}).get('sportTypeKey'))}")

        ref = next((w for w in workouts
                    if (w.get("workoutName") or "").strip() == REFERENCE_WORKOUT_NAME), None)
        if ref and not ref_found:
            ref_found = True
            wid = ref["workoutId"]
            full = garth.connectapi(f"/workout-service/workout/{wid}")
            out = BASE_DIR / "schema_ref.json"
            out.write_text(json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8")
            section(f"SCHEMA_REF_JSON (account={account}, workoutId={wid})")
            print(json.dumps(full, ensure_ascii=False, indent=2))
            section("SCHEMA_REF_JSON_END")

        # exercise 목록/검색 후보 엔드포인트 시도 (추측 enum 생성 방지 — 실데이터만)
        if not getattr(phase_mine, "_exercises_done", False):
            phase_mine._exercises_done = True
            try_exercise_sources(garth)

    if not ref_found:
        section("REFERENCE_NOT_FOUND")
        print(f"이름이 {REFERENCE_WORKOUT_NAME!r}인 워크아웃을 찾지 못함. 위 목록 확인 요망.")


def try_exercise_sources(garth):
    """가민 커넥트가 쓰는 exercise 정의 소스들을 시도해 실제 enum 후보를 수집."""
    import requests

    sources = [
        ("web-data Exercises.json",
         "https://connect.garmin.com/web-data/exercises/Exercises.json"),
        ("web-translations exercise_types (en)",
         "https://connect.garmin.com/web-translations/exercise_types/exercise_types.properties"),
    ]
    exercises_flat = []  # (category, name)

    for label, url in sources:
        section(f"EXERCISE_SOURCE: {label}")
        try:
            r = requests.get(url, timeout=30)
            print(f"GET {url} -> {r.status_code}, {len(r.content)} bytes")
            if r.status_code != 200:
                continue
            if url.endswith(".json"):
                data = r.json()
                # 구조 파악용 최상위 키 출력
                if isinstance(data, dict):
                    print(f"최상위 키: {list(data.keys())[:30]}")
                    exercises_flat.extend(flatten_exercises(data))
                    print(f"평탄화된 (category, name) 수: {len(exercises_flat)}")
            else:
                # properties 형식: KEY=Display Name
                lines = r.text.splitlines()
                print(f"라인 수: {len(lines)}, 앞 5줄: {lines[:5]}")
                for ln in lines:
                    if "=" in ln:
                        k = ln.split("=", 1)[0].strip()
                        exercises_flat.append(("(properties)", k))
        except Exception as e:
            print(f"실패: {e}")

    # workout-service 쪽 후보도 시도 (존재 여부만 확인)
    for path in ["/workout-service/workout/exercises"]:
        section(f"EXERCISE_SOURCE: connectapi {path}")
        try:
            data = garth.connectapi(path)
            txt = json.dumps(data, ensure_ascii=False)[:2000]
            print(f"응답(앞 2000자): {txt}")
        except Exception as e:
            print(f"실패: {e}")

    # 키워드 검색
    section("EXERCISE_CANDIDATES")
    if not exercises_flat:
        print("수집된 exercise 목록이 없음 — 후보 검색 불가.")
        return
    for kor, keywords in EXERCISE_KEYWORDS.items():
        print(f"\n### {kor}")
        seen = set()
        for kw in keywords:
            for cat, name in exercises_flat:
                if kw in name.upper() and (cat, name) not in seen:
                    seen.add((cat, name))
                    print(f"  {cat} / {name}")
        if not seen:
            print("  (매칭 없음)")


def flatten_exercises(data):
    """Exercises.json 구조를 모른 채로 (category, name) 쌍을 최대한 뽑아낸다."""
    flat = []

    def walk(node, cat=None):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    walk(v, cat=k if cat is None else cat)
                elif isinstance(v, str) and k.lower() in ("name", "exercisename", "key"):
                    flat.append((cat or "?", v))
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, str):
                    flat.append((cat or "?", item))
                else:
                    walk(item, cat)

    walk(data)
    return flat


# ────────────────────────── phase: dryrun / upload ──────────────────────────

def build_payloads():
    """schema_ref 관찰 결과가 확정된 뒤에 구현된다 (2차 커밋)."""
    raise NotImplementedError(
        "페이로드 빌더는 schema mine 결과 확인 후 구현됩니다. "
        "지금 이 단계를 실행하면 안 됩니다."
    )


def phase_dryrun(_accounts):
    build_payloads()


def phase_upload(_accounts):
    build_payloads()


# ────────────────────────── main ──────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True, choices=["mine", "dryrun", "upload"])
    ap.add_argument("--account", default="both", choices=["ryu", "ek", "both"])
    args = ap.parse_args()

    accounts = ["ryu", "ek"] if args.account == "both" else [args.account]

    if args.phase == "mine":
        phase_mine(accounts)
    elif args.phase == "dryrun":
        phase_dryrun(accounts)
    else:
        phase_upload(accounts)


if __name__ == "__main__":
    main()
