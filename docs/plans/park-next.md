# 배차 엔진 다음 작업 제안 (박성훈용)

> **담당:** 박성훈 (제안: 오정훈)
> **상태:** P-1~P-4 구현·검증 완료, 팀원 확인 대기 (`feat/park-next`) · P-5 구현·검증 완료 (`feat/park-r3-retrain`)
> **작성일:** 2026-11-16 · **최종 업데이트:** 2026-11-22

## 이 문서를 쓰는 법

AI 코딩 도구에 아래 문장을 붙여 넣고, 하고 싶은 Task 번호만 바꾸면 됩니다.

```
docs/plans/park-next.md를 읽고 Task P-1을 진행해줘.
시작하기 전에 CLAUDE.md, CONTRIBUTING.md, docs/contracts.md를 읽고,
"읽을 파일"에 적힌 코드를 먼저 읽은 뒤 바꿀 내용을 목록으로 보여줘. 확인하고 진행할게.
```

## 목표

배차 엔진(F-11~F-15, PR #1)은 기능이 끝났습니다. 남은 것은 **다른 사람의 기능과 이어 붙이는 부분**입니다. 챗봇(오정훈)이 시뮬레이션·최적화를 부를 수 있게 하고, 최적화 결과를 이력으로 남기고, 길안내가 고친 혼잡도 기준을 제대로 쓰게 하는 작업입니다. 모두 작고, 서로 독립이라 하나씩 할 수 있습니다.

## 설계 결정

- **챗봇은 어댑터 메서드만 부름** — 챗봇(`app/core/agent.py`)은 HTTP를 거치지 않고 `adapter.predict()`·`adapter.advise()`처럼 어댑터 메서드를 부릅니다. 시뮬레이션·최적화는 지금 라우터 함수 안에만 있어서 챗봇이 부를 수 없습니다. 그래서 메서드로 빼야 합니다(P-1).
- **계산 로직은 바꾸지 않음** — `simulator.py`·`optimizer.py`는 그대로 두고, 부르는 자리만 옮깁니다. 응답 형식도 그대로입니다.
- **이력 테이블과 저장 함수는 이미 있음** — `optimization_run` 테이블과 `repo.save_optimization_run()`은 오정훈이 만들어 두었습니다. 호출 한 줄만 붙이면 됩니다(P-2).

## 영향 범위

| 위치 | 작업 |
|---|---|
| `app/domains/bus/adapter.py` | `simulate()`·`optimize()` 메서드 추가, 라우터 함수는 그 메서드를 부르게 변경 (P-1) / 최적화 뒤 저장 호출, 이력 조회 API (P-2) |
| `app/core/domain.py` | 기본 `simulate()`·`optimize()` (지원하지 않는 도메인은 501) (P-1) |
| `app/domains/bus/advisor.py` | 시계 시각 → 기점 출발 시간대 함수 `departure_hour` (P-3) |
| `app/domains/bus/guide.py` | P-3의 함수를 쓰도록 한 곳 변경 (오정훈과 같이) |
| `frontend/src/components/OptimizePanel.vue`, `api.js` | 이력 목록 (P-2, 선택) / 성능 비교 표 (P-4, 선택) |
| `tests/test_simulate.py`, `test_optimize.py`, `test_guide.py` | 테스트 추가 |
| DB | 기존 테이블 변경 없음. 로컬 DB 재생성 불필요 |

## 인터페이스 (다른 사람이 쓰는 부분)

```
# P-1: 챗봇이 부르는 어댑터 메서드 (응답은 지금 API 응답과 같은 dict)
adapter.simulate({"route": "143", "date": "2026-10-28", "hour": 8, "interval": 5}) -> dict
adapter.optimize({"date": "2026-10-28", "hours": [6, 7, 8, 9]}) -> dict
  · 없는 노선·범위 밖 간격·비율표 없음은 지금처럼 HTTPException(404/400/503)으로 올림

# P-2: 최적화 응답에 두 필드 추가 (기존 필드는 그대로)
POST /api/bus/optimize  →  {..., "run_id": 7, "db_saved": true}
GET  /api/bus/optimize/runs?limit=20  →  {"db": {...}, "items": [{"id", "date", "hours", "weights",
        "status", "solve_seconds", "overcrowded_before", "overcrowded_after", "extra_trips", "created_at"}]}
```

형식을 바꾸면 `docs/contracts.md` 1·2번도 같은 PR에서 고칩니다.

---

## Task

### Task P-1: 시뮬레이션·최적화를 어댑터 메서드로 빼기 (챗봇 연결의 선행 작업) ★☆☆☆☆

> **2026-11-19 완료 (오정훈이 PR #4에서 진행).** `BusAdapter.simulate()`·`optimize()`로 옮겼고 라우터는 그 메서드를 부릅니다. 응답 형식은 그대로이고 기존 시뮬레이션·최적화 테스트가 모두 통과합니다. 옮긴 부분을 한 번 확인해 주세요. 아래 항목은 기록으로 남깁니다.
> **2026-11-22 박성훈 확인:** 메서드는 요청 검증 → 모델·비율표 확인 → 계산만 하고 라우터는 메서드를 부르기만 함. 로직 중복 없음.

**왜:** 챗봇이 "150번 내일 8시 간격을 5분으로 바꾸면?", "내일 출근 시간 전체 노선 최적화해줘"를 알아보는 데까지는 됐지만, 부를 메서드가 없어 "아직 연결되지 않았습니다"라고 답합니다. 이 Task가 끝나면 오정훈이 챗봇 쪽(`agent.py`의 `_simulate`·`_optimize`)을 붙입니다.

**읽을 파일:** `app/domains/bus/adapter.py`(라우터의 `simulate_interval`, `optimize_dispatch`와 `predict()`·`advise()` 메서드), `app/core/domain.py`, `tests/test_simulate.py`, `tests/test_optimize.py`

**구현:**
- [x] `app/core/domain.py`의 `DomainAdapter`에 `simulate(self, payload: dict) -> dict`, `optimize(self, payload: dict) -> dict` 추가. 기본 구현은 `raise DomainNotImplemented(self.name, "simulate")` (이미 있는 `guide()`와 같은 모양)
- [x] `BusAdapter.simulate(payload)`: `BusSimulateRequest(**payload)`로 검증 → `_require_model()` → `_check_route()` → `simulate(self.advisor, ...)` → `ValueError`는 `HTTPException(422)`
- [x] `BusAdapter.optimize(payload)`: `BusOptimizeRequest(**payload)`로 검증 → `_require_model()` → `_require_p90()` → `optimize(...)`
- [x] 라우터의 `simulate_interval`·`optimize_dispatch`는 `return self.simulate(req.model_dump())`처럼 메서드를 부르기만 함 (로직 중복 금지)
- [x] 테스트: 메서드를 직접 불러 API 응답과 같은 값이 나오는지 1건씩 (2026-11-22 박성훈 추가: `test_adapter_methods_match_api`)

**검증:**
- [x] `python -m pytest -q` 통과 (기존 시뮬레이션·최적화 테스트가 그대로 통과해야 함)
- [x] `POST /api/bus/simulate`·`/optimize` 응답이 바꾸기 전과 같음 (필드 비교) — 계산 결과 필드는 같음. `/optimize`에는 P-2의 `run_id`·`db_saved`가 더해짐

**팀원 확인:**
- [ ] 오정훈 확인 완료 (챗봇에서 부를 수 있는지)
- **확인일:** 

### Task P-2: 최적화 결과를 이력으로 남기기 (F-24 연결) ★★☆☆☆

**왜:** 가중치만 바꿔 여러 번 돌린 결과를 나란히 보거나, 발표에서 "이렇게 썼다"는 기록을 보여 주려면 실행마다 한 줄이 남아야 합니다. 테이블(`optimization_run`)과 저장 함수는 이미 있습니다.

**읽을 파일:** `app/db/database.py`의 `OptimizationRun`, `app/db/repository.py`의 `save_optimization_run`·`list_optimization_runs`, `adapter.py`의 `advise_all`(저장을 붙인 기존 예시)

**구현:**
- [x] `optimize_dispatch` 라우터를 `async def`로 바꾸고, 계산은 `await run_in_threadpool(self.optimize, req.model_dump())`로 실행 (계산이 길어 다른 요청을 막지 않게)
- [x] 계산 뒤 `run_id = await repo.save_optimization_run("bus", result)` → 응답에 `run_id`, `db_saved` 추가. `BusOptimizeResponse`에 두 필드(`run_id: int | None = None`, `db_saved: bool = False`) 추가
- [x] `GET /api/bus/optimize/runs?limit=20`: `repo.list_optimization_runs("bus", limit)` 결과를 `{"db": db.state, "items": [...]}`로 반환. `limit`은 `Query(20, ge=1, le=100)`
- [x] DB가 꺼져 있어도 최적화는 그대로 동작 (`db_saved: false`). 저장 실패가 계산 결과를 막지 않아야 함
- [x] (선택) `OptimizePanel.vue`에 최근 실행 목록: 날짜·시간대·가중치·과밀 전후. `api.js`에 `optimizeRuns()` 추가. 로딩·에러·빈 상태 표시
- [x] 테스트: DB 없이 `db_saved`가 `false`이고 나머지 응답은 그대로 / `runs`는 빈 목록

**검증:**
- [x] `python -m pytest -q` 통과
- [x] MariaDB를 켜고 최적화 1회 → `optimization_run`에 한 줄, `GET /api/bus/optimize/runs`에 보임 (2026-11-22: `run_id` 1, 화면 "최근 실행 기록"에도 표시)
- [x] MariaDB를 끄고 최적화 1회 → 결과는 같고 `db_saved: false` (서버가 뜬 뒤에 DB를 끄면 이력 조회는 500이 났음 → 503과 이유로 바꾸고 테스트 추가)
- [x] `docs/contracts.md` 2번에 `run_id`·`db_saved`와 `runs` API를 추가

**팀원 확인:**
- [ ] 오정훈 확인 완료
- **확인일:** 

### Task P-3: 길안내가 "지금 타는 버스"의 혼잡도를 쓰게 하기 ★★★☆☆

**왜:** PR #1에서 혼잡도의 시간 기준을 "기점 출발 시각"으로 바로잡았습니다. 그런데 길안내(`app/domains/bus/guide.py`의 `_leg`)는 승객이 정류장에서 타는 **시계 시각**의 시간대로 구간 혼잡도를 읽습니다. 노선 중간·끝 정류장에서는 그 시각에 오는 버스가 한두 시간 전에 기점을 떠난 버스라서, 다른 시간대의 값을 읽게 됩니다.

**읽을 파일:** `docs/decisions.md`의 2026-11-13 "혼잡도의 시간 기준" 행, `app/domains/bus/advisor.py`의 `analyze_day`, `app/domains/bus/guide.py`의 `_leg`

**구현:**
- [x] `BusAdvisor`에 함수 추가: 노선·날짜·시계 시각·정류장 순번을 받아 "그 시각에 그 정류장을 지나는 버스"의 기점 출발 시간대를 돌려줌. 계산은 `운행시간 × (순번 ÷ 전체 정류장 수)`만큼 거슬러 올라가는 방식으로 시작 (구간별 실제 소요시간 자료가 없으므로)
- [x] `guide.py`의 `_leg`가 타는 정류장 기준으로 그 시간대의 구간 혼잡도를 읽도록 변경. 환승 뒤 구간은 "앞 구간 탑승 시간만큼 지난 시각"으로 계산
- [x] `ASSUMPTION` 문구에서 "환승 뒤에도 같은 시간대로 계산했습니다"를 실제 계산에 맞게 수정
- [x] 테스트: 기점 근처 정류장과 종점 근처 정류장에서 같은 시계 시각에 다른 출발 시간대를 읽는지

**검증:**
- [x] `python -m pytest -q` 통과 (`tests/test_guide.py` 포함)
- [x] 실데이터 한 노선에서 종점 근처 정류장의 값이 바꾸기 전·후 어떻게 달라지는지 숫자로 기록 (`docs/decisions.md`에 한 줄) — 5개 노선 × 5곳, 8시 25곳 중 20곳이 다른 시간대, 혼잡도 차이 평균 0.128 (18시 0.148). `scripts/compare_guide_time_basis.py` → `eval/results/guide_time_basis_1·2.json`

**팀원 확인:**
- [ ] 오정훈 확인 완료 (길안내 답변·팝업이 그대로 동작)
- **확인일:** 

### Task P-4: 배차 방식 성능 비교를 화면에서 보기 (선택) ★★☆☆☆

**왜:** 현행·규칙·그리디·정수계획 비교표(`eval/results/backtest.json`)는 발표의 핵심 근거인데 지금은 파일로만 있습니다. 대시보드에서 바로 보이면 시연 중에 화면을 바꾸지 않아도 됩니다.

**읽을 파일:** `eval/results/backtest.json`, `frontend/src/components/MetricsCard.vue`, `frontend/src/layout.js`

**구현:**
- [x] `GET /api/bus/backtest`: `eval/results/backtest.json`을 읽어 방식별 과밀 비율·총 운행 증감·새 인가대수 초과 건수를 반환. 파일이 없으면 404와 "python scripts/backtest.py를 먼저 실행" 안내. 파일은 `encoding="utf-8"`로 읽기
- [x] 패널 1개(또는 `MetricsCard`에 표 추가): 방식 4개를 한 표에, 평가 구간과 "평가 구간을 여러 번 채점함" 주의 문구를 함께 표시
- [x] 로딩·에러·빈 상태 표시. 색은 `theme.js`·`style.css` 토큰만 사용

**검증:**
- [x] `python -m pytest -q` 통과 (정상 1건 + 파일 없음 1건), `npm run build` 성공
- [x] 화면의 숫자가 `backtest.json`과 같음 (2026-11-22 "분석" 화면에서 5행 대조)

**팀원 확인:**
- [ ] 김주혜 확인 완료 (발표 대본에서 쓸 수 있는지)
- **확인일:** 

---

### Task P-5: 예측 오차가 커지면 재학습을 권고하고 담당자가 승인 (요청 R-3) ★★★☆☆

**왜:** 새 주 실측이 들어와도 미리 만들어 둔 3주 앞 예측이 얼마나 빗나갔는지 보는 장치가 없고, 재학습은 손으로 "때가 되면" 합니다. 교수가 예고한 AIOps 주제(예측·실측 비교 → 기준 초과 감지 → 재학습 → 기록)이고, "가치 판단은 AI에게 맡기지 말라"에 따라 **자동 재학습이 아니라 권고 + 사람 승인**으로 합니다. 요청 원문: `docs/requests/2026-11-22-oh.md` R-3.

**설계 (2026-11-26 박성훈 결정):**
- 비교 대상: (a) 실제 운영 — 저장소의 미래 예측(`split=forecast`)과 새로 받은 실측. (b) 다시 돌려 보기 — 평가 구간(BUS_TUNE_END 다음 날 ~)의 주마다 같은 계산. 데이터 갱신은 제출 전주 한 번뿐이라 평소 시연은 (b)
- 기준(`app/config.py`): ① 주간 정류장·시간 MAE가 평가 MAE(`metrics.json` `stop_hour.model_mae`)의 1.2배 초과 ② 그 주에 모델이 작년 같은 요일보다 못함 ③ 노선·시간 합계가 P90 상한 이하인 비율이 85% 미만. 하나라도 걸리면 권고. 실측이 3일 미만이면 판정 보류
- 판정은 스크립트(`scripts/check_drift.py`)가 하고 결과를 `eval/results/drift/<id>.json`에 남김. 서버는 파일을 읽기만 함
- 승인·보류·반려는 새 테이블 `retrain_review`에 기록 (기존 테이블 변경 없음, 로컬 DB 재생성 불필요)
- 승인해도 서버가 학습을 돌리지 않음 (xgboost·torch를 한 프로세스에 못 올림). 실행할 명령과 "학습 구간(BUS_VALID_START·BUS_TUNE_END)을 옮길지는 담당자가 정함"을 안내
- 화면: 분석 화면에 "모델 점검" 패널

**구현:**
- [x] `app/domains/bus/drift.py`: 행 묶음 → 지표·판정 (`judge`), 결과 파일 읽기
- [x] `scripts/check_drift.py` (`--replay`로 평가 구간 주별 다시 돌려 보기)
- [x] `retrain_review` 테이블, `repo.save_retrain_review`·`get_retrain_reviews`
- [x] `GET /api/bus/drift`, `POST /api/bus/drift/{id}/review` (`response_model`, DB 꺼짐 503, 없는 id 404)
- [x] `DriftPanel.vue`, `api.js`, `layout.js` (분석 화면에 "모델 점검")
- [x] 테스트: 판정 규칙 5 · 주 나누기 · 파일 · API 3 (`tests/test_drift.py` 10개) + 계약 예시 ↔ 스키마

**검증:**
- [x] `python -m pytest -q` 243개 통과, `npm run build`·`npm run test:api` 통과
- [x] 실데이터 평가 구간 다시 돌려 보기 (`eval/results/drift/replay_*.json` 5주): 오차 배수 0.98~1.01, 작년 같은 요일 5.42~7.54명 대비 3.83~3.92명, P90 적중 88.2~92.3% → 5주 모두 "그대로". 기준(1.2배·85%)에 오탐 없음. 참고로 README의 10/19~25 실제 예측 검증은 4.09명(1.05배)
- [x] MariaDB를 켜고 화면에서 승인 → `retrain_review`에 한 줄, 다시 보류로 바꾸면 같은 줄이 고쳐짐 (화면 확인용 임시 점검 파일·줄은 확인 뒤 지움)

**팀원 확인:**
- [ ] 오정훈 확인 완료 (챗봇이 판정 결과를 문장으로 설명할 수 있는지)
- **확인일:** 

---

### Task P-6: 화면 탭을 사용자가 만들어 저장 (박성훈 제안, 2026-11-27) ★★☆☆☆

**왜:** 화면 탭(관제·배차 계획·분석·발표·규정 검토·상담)은 정해진 6개뿐이라, 자주 쓰는 패널 조합을 매번 "패널" 메뉴로 다시 켜야 합니다. 시연 때 자기만의 화면을 한 번에 불러오면 편합니다.

**설계:**
- 탭 옆 **＋** → 이름(최대 10자)과 넣을 패널을 골라 저장. 처음에는 지금 켜진 패널이 골라져 있고, 위치는 지금 화면 그대로(새로 고른 패널은 빈자리에 채움)
- 저장한 탭은 기본 탭 뒤에 붙고, 누르면 그 배치를 불러옴. 같은 팝업에서 "지금 화면으로 덮어쓰기"·"삭제"
- 저장 위치는 **이 브라우저의 localStorage**(`dispatch-custom-tabs-v1`). 개인 화면 설정이라 DB·API를 쓰지 않음. 저장소를 못 쓰면 이번 화면에만 적용
- 최대 5개. 나중에 추가된 패널은 저장된 탭에 꺼진 채로 채움 (기존 `loadSaved`와 같은 방식)

**구현:**
- [x] `frontend/src/layout.js`: `loadCustomTabs`·`saveCustomTabs`·`fillLayout`·`layoutWith`
- [x] `Dashboard.vue`: 탭 버튼, ＋ 팝업(이름·패널·저장·덮어쓰기·삭제·이름 바꾸기·순서)

**검증:**
- [x] `npm run build`·`npm run test:api`, 화면에서 만들기 → 다른 탭 → 다시 누르면 불러옴 → 새로고침 뒤에도 남음 → 덮어쓰기 → 삭제. 이름 없음·같은 이름(기본 탭 포함)·패널 없음은 저장하지 않고 이유를 보여 줌. 휴대폰 폭(375)에서 가로 넘침 없음, 콘솔 오류 없음
- [x] (2026-11-27 추가) **자동 저장**: 내 탭을 연 채로 패널을 끌기·크기 조절·키보드 이동·켜고 끄면 그 탭에 바로 저장 (`commitLayout` 한 곳). 고친 버그: 내 탭에서 패널을 더 연 뒤 다른 탭에 갔다 오면 더 연 패널이 사라지던 것 (패널을 바꾸는 순간 탭 선택이 풀리고 탭에는 저장되지 않았음). 기본 탭에서 옮기면 예전처럼 선택만 풀림
- [x] (추가) **새 패널은 빈칸부터**: 위에서부터, 같은 줄이면 왼쪽부터 다른 패널과 겹치지 않는 첫 자리 (`findSpot`·`placePanel`). 내 탭 만들기와 "패널" 메뉴 모두
- [x] (추가) **이름 바꾸기**(같은 이름·빈 이름은 막음) · **순서 바꾸기**(▲▼, 탭 버튼 순서도 함께)
- 확인 (화면 5174): 탭 만들기(노선 현황·배차 현황) → 패널 메뉴로 모델 점검 열기 → 노선 현황 옆 빈칸(x=3, y=0)에 들어가고 탭에 저장 → 관제 → 다시 누르면 그대로 → × 로 닫으면 저장 → 키보드로 옮기면 저장 → 순서·이름 바꾸기 → 콘솔 오류 없음

---

## 남은 문제 · 다음 단계

- **P-5 실제 판정은 제출 전주 한 번** — 새 주 데이터를 받은 뒤 **재학습 전에** `python scripts/check_drift.py`. 실제 예측은 날씨(작년 평균)·운행횟수(계획값) 때문에 다시 돌려 보기보다 오차가 조금 큼(10/19~25 기록 1.05배)
- **P-5 "재학습"의 뜻** — 학습 구간은 BUS_VALID_START 전까지 고정이라 `train_bus.py`를 다시 돌려도 모델 가중치는 거의 그대로이고 예측만 새 실측으로 다시 만듦. 오차가 커진 것을 고치려면 학습 구간을 옮겨야 하는데, 그러면 평가 구간이 바뀌어 성능 비교·오차 설명을 다시 채점해야 함. 그래서 승인 뒤 첫 단계를 "옮길지 정하기"로 둠
- **P-5 판정에 "버스 한 대 인원"(R-2의 말)은 없음** — 실제 예측 주는 구간 재차인원 실측이 재학습 전에는 저장소에 없어 같은 계산을 할 수 없음. 정류장·시간 MAE와 P90 적중률로만 판정
- **P-5 챗봇 연결(오정훈)** — `GET /api/bus/drift`의 `reasons`·`metrics`로 "이번 주 얼마나 빗나갔고 왜 재학습을 권하는지" 답하기. 계약 2-3절
- **PR #1과 `feat/oh-assistant`의 충돌** — 두 브랜치가 `adapter.py`, `bus/schemas.py`, `layout.js`, `README.md`, `config.py`를 함께 고쳤습니다. 내용은 겹치지 않습니다(오정훈 쪽은 길안내·챗봇 추가). PR #1이 먼저 `dev`에 들어가면 오정훈이 자기 브랜치에서 충돌을 정리합니다.
- **`predict` 응답에 `congestion` 추가됨** — `hour`를 준 경우 그 시간대의 최대 혼잡도(`{"ratio", "level"}`)를 함께 돌려줍니다. `analyze_day`의 `hourly["max_congestion"]`을 읽으므로, 그 열의 이름이나 뜻을 바꾸면 알려 주세요.
- **P-3은 정확도 개선이지 필수는 아님** — (2026-11-22 구현함)
- **P-3 한계** — 기점 → 정류장 소요는 공식 시간표 기반 어림이고 출퇴근 1.15배(가정값)가 들어감. 'N시 출발'을 N시 30분으로 봄(`GUIDE_CLOCK_MINUTE`). 시간대 경계 근처에서는 한 시간 차이로 값이 크게 바뀔 수 있음.
- **P-2 이력이 화면 사용만큼 쌓임** — 최적화 화면이 날짜·시간대를 바꿀 때마다 API를 불러 그때마다 한 줄 저장됨. 챗봇이 메서드로 부른 최적화는 저장하지 않음. 정리 기능은 없음. → 2026-11-24: 직전 실행과 같은 조건·같은 결과면 새 줄 없이 직전 id (PR #25 리뷰 LOW 4)
- **PR #25 리뷰 LOW 2·3** — 자정을 넘는 환승의 날짜, 첫차가 아직 오지 않은 정류장에서 노선이 빠지는 것. → 2026-11-25: 기점 출발이 자정을 넘으면 다음 날 값, 첫차가 60분 안에 오면 기다려 탐(`first_bus_at`). 실데이터 150건 호출 5xx 0건
  - 2026-11-28 보완 (PR #37 리뷰 MEDIUM): 첫차가 **자정을 넘겨** 정류장에 닿는 심야버스(기점 23:50 → 정류장 00:20)는 대기로 잡지 못해 #37의 운행 판단과 만나 0~2시에 빠졌음 → 첫차까지 남은 시간을 하루 단위로 돌려 셈(`(first_at - day_clock) % 1440`). 실데이터 심야 18개 노선 정류장 2,228곳 중 1,829곳이 해당, 첫차 10분 전 질의 1,812곳: 고치기 전 77곳만 포함 → 고친 뒤 1,812곳 모두 대기 10분으로 포함. 재현 테스트 `test_night_bus_first_arrival_after_midnight_is_waited_for`
- **0시대 질문은 여전히 전날 출발 버스를 0시로 묶음** — 0시 30분에 노선 가운데에서 타는 버스는 전날 22~23시에 기점을 떠났지만 그날 0시 값을 읽어 대개 "운행 데이터 없음"으로 빠짐. 전날로 넘기려면 막차가 그 정류장을 지나는 시각도 함께 따져야 해서(막차가 이미 지나갔는데 경로를 안내할 수 있음) 이번에는 하지 않음
- **챗봇 답변에는 첫차 대기가 안 보임** — 팝업은 "첫차 04:03쯤"을 보여 주지만 챗봇 문장은 배차간격만 말해, 총 시간(대기 포함)이 왜 긴지 알 수 없음. `app/core/agent.py`(오정훈 담당)에 `first_bus_at` 한 줄을 요청
- **P-4 숫자는 파일 그대로** — 성능 비교를 다시 돌리면(`python scripts/backtest.py`) 화면도 바뀜. 평가 구간을 여러 번 채점했다는 주의 문구를 화면에 함께 표시함.
- **기존 이력 API도 같은 문제 가능성** — `GET /api/advisories` 등은 서버가 뜬 뒤 DB가 꺼지면 500일 수 있음 (확인하지 않음, 오정훈 담당 영역).
