# 가민 건강봇 — 프로젝트 가이드 (Claude용)

류우·은경의 가민 워치 데이터로 매일 아침 **건강/훈련 브리핑**을 텔레그램으로 보내는 봇.
GitHub Actions에서 **완전 클라우드 자동 실행**된다(PC 불필요). 저장소가 본체.

## ⚠️ 보안 (가장 중요)
- 이 저장소는 **공개(public)**다. 코드엔 비밀이 없고, 비밀은 전부 **GitHub Secrets**(암호화)에 있다.
- **절대 비밀을 커밋하지 말 것.** `.env`, `service_account.json`, `.garth_tokens_*/`는 `.gitignore`로 막혀 있다.
- 커밋 전 항상: `git diff --cached --name-only` 로 비밀파일이 없는지 확인할 것. (사용자와의 약속)

## 구조
- `step3_collect_and_save.py` — 가민 데이터 수집(garth) → 구글시트 저장. 가민 종합지표(훈련준비도·HRV상태·바디배터리·부하비율·VO2max·레이스예측) + 운동추세(28일)도 수집.
- `step4_telegram_bot.py` — 텔레그램 문진. **`run_session_multi(users, budget, on_done)`** = 두 사용자 동시 대화형 문진(단일 getUpdates 루프, 하나씩 질문→누르면 다음, 각자 완료 즉시 on_done 콜백).
- `step5_analyze_report.py` — Claude API로 분석 리포트 생성/발송. `report_user(name, info)` = 단일 사용자. 리포트는 **쉬운 말 필수**(전문용어 금지, 프롬프트에 번역표 있음). 결정중심(🟢🟡🔴) + 경고감지 + 운동/술/체중 추세 + **목표 대회(RACES) 기반 훈련 추천**.
- `cloud_run.py` — **오케스트레이터**. `morning`/`lunch` 모드. pending(시트 '브리핑상태'≠발송/종료) → 동시 문진 → 완료자 즉시 브리핑(가민수집 1회 → 저장 → report_user → 상태'발송'). 미응답: morning이면 점심 재시도 안내, lunch면 '종료' 멘트.

## 클라우드 (GitHub Actions)
- 워크플로우 `.github/workflows/daily.yml`:
  - cron `50 23 * * *` = **08:50 KST 아침**(대기 70분, ~10시까지)
  - cron `30 2 * * *` = **11:30 KST 점심**(대기 90분, ~13시까지)
  - 수동: Actions → Run workflow → mode `morning`/`lunch` (대기 20분, 테스트용)
- Secrets 4개: `ENV_B64`(.env), `SERVICE_ACCOUNT_B64`, `GARTH_RYU_B64`, `GARTH_EK_B64` (각각 base64; 토큰은 tar+base64). 워크플로우가 복원.
- TZ=Asia/Seoul로 실행(한국 날짜 기준).

## 데이터
- 구글시트 **"가민건강코치"** — 탭 "류우"/"은경". 헤더 37개. **헤더는 step3/4/5에서 동일해야 함**, 새 컬럼은 **맨 뒤에 추가**(과거 데이터 컬럼 밀림 방지). 마지막=`브리핑상태`(''/발송/종료).
- 사용자: 류우(Epix Pro Gen 2), 은경(FR265S).

## 목표 대회 (step5 `RACES`) — 훈련 추천 기준
- 6/27 15km 트레일(확정), 10월 제주 UTMB 20K·무주 GWTS 20K(날짜 미정). 정확 날짜 확정 시 갱신.
- 단순 유지가 아니라 **훈련 자극** 추천(언덕·템포·인터벌·롱런, 트레일 특화).

## 수정/배포 흐름
1. 코드 수정 → 2. 비밀 스캔 → 3. `git push origin main` → 4. 클라우드 자동 반영(다음 스케줄부터).
- 테스트는 Actions의 Run workflow(mode morning)로. 로컬 실행엔 비밀파일 필요(이 저장소엔 없음).

## 로컬 개발 메모 (Windows)
- 한글 출력 깨지면 `PYTHONIOENCODING=utf-8`. 가민 로그인은 토큰(`.garth_tokens_*`) 기반(비번 새 로그인은 차단 위험).
- garth/가민 비공식 API라 가끔 엔드포인트가 바뀔 수 있음(`test_v2_metrics.py`로 점검).
