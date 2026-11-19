# 배차 엔진 다음 작업 제안 (박성훈용)

> **담당:** 박성훈 (제안: 오정훈)
> **상태:** 계획 (제안 단계 — 할지 말지, 순서는 박성훈이 정합니다)
> **작성일:** 2026-11-16 · **최종 업데이트:** 2026-11-16

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
| `app/domains/bus/advisor.py` | 시계 시각 기준 구간 혼잡도 조회 함수 (P-3) |
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

**왜:** 챗봇이 "150번 내일 8시 간격을 5분으로 바꾸면?", "내일 출근 시간 전체 노선 최적화해줘"를 알아보는 데까지는 됐지만, 부를 메서드가 없어 "아직 연결되지 않았습니다"라고 답합니다. 이 Task가 끝나면 오정훈이 챗봇 쪽(`agent.py`의 `_simulate`·`_optimize`)을 붙입니다.

**읽을 파일:** `app/domains/bus/adapter.py`(라우터의 `simulate_interval`, `optimize_dispatch`와 `predict()`·`advise()` 메서드), `app/core/domain.py`, `tests/test_simulate.py`, `tests/test_optimize.py`

**구현:**
- [ ] `app/core/domain.py`의 `DomainAdapter`에 `simulate(self, payload: dict) -> dict`, `optimize(self, payload: dict) -> dict` 추가. 기본 구현은 `raise DomainNotImplemented(self.name, "simulate")` (이미 있는 `guide()`와 같은 모양)
- [ ] `BusAdapter.simulate(payload)`: `BusSimulateRequest(**payload)`로 검증 → `_require_model()` → `_check_route()` → `simulate(self.advisor, ...)` → `ValueError`는 `HTTPException(422)`
- [ ] `BusAdapter.optimize(payload)`: `BusOptimizeRequest(**payload)`로 검증 → `_require_model()` → `_require_p90()` → `optimize(...)`
- [ ] 라우터의 `simulate_interval`·`optimize_dispatch`는 `return self.simulate(req.model_dump())`처럼 메서드를 부르기만 함 (로직 중복 금지)
- [ ] 테스트: 메서드를 직접 불러 API 응답과 같은 값이 나오는지 1건씩

**검증:**
- [ ] `python -m pytest -q` 통과 (기존 시뮬레이션·최적화 테스트가 그대로 통과해야 함)
- [ ] `POST /api/bus/simulate`·`/optimize` 응답이 바꾸기 전과 같음 (필드 비교)

**팀원 확인:**
- [ ] 오정훈 확인 완료 (챗봇에서 부를 수 있는지)
- **확인일:** 

### Task P-2: 최적화 결과를 이력으로 남기기 (F-24 연결) ★★☆☆☆

**왜:** 가중치만 바꿔 여러 번 돌린 결과를 나란히 보거나, 발표에서 "이렇게 썼다"는 기록을 보여 주려면 실행마다 한 줄이 남아야 합니다. 테이블(`optimization_run`)과 저장 함수는 이미 있습니다.

**읽을 파일:** `app/db/database.py`의 `OptimizationRun`, `app/db/repository.py`의 `save_optimization_run`·`list_optimization_runs`, `adapter.py`의 `advise_all`(저장을 붙인 기존 예시)

**구현:**
- [ ] `optimize_dispatch` 라우터를 `async def`로 바꾸고, 계산은 `await run_in_threadpool(self.optimize, req.model_dump())`로 실행 (계산이 길어 다른 요청을 막지 않게)
- [ ] 계산 뒤 `run_id = await repo.save_optimization_run("bus", result)` → 응답에 `run_id`, `db_saved` 추가. `BusOptimizeResponse`에 두 필드(`run_id: int | None = None`, `db_saved: bool = False`) 추가
- [ ] `GET /api/bus/optimize/runs?limit=20`: `repo.list_optimization_runs("bus", limit)` 결과를 `{"db": db.state, "items": [...]}`로 반환. `limit`은 `Query(20, ge=1, le=100)`
- [ ] DB가 꺼져 있어도 최적화는 그대로 동작 (`db_saved: false`). 저장 실패가 계산 결과를 막지 않아야 함
- [ ] (선택) `OptimizePanel.vue`에 최근 실행 목록: 날짜·시간대·가중치·과밀 전후. `api.js`에 `optimizeRuns()` 추가. 로딩·에러·빈 상태 표시
- [ ] 테스트: DB 없이 `db_saved`가 `false`이고 나머지 응답은 그대로 / `runs`는 빈 목록

**검증:**
- [ ] `python -m pytest -q` 통과
- [ ] MariaDB를 켜고 최적화 1회 → `optimization_run`에 한 줄, `GET /api/bus/optimize/runs`에 보임
- [ ] MariaDB를 끄고 최적화 1회 → 결과는 같고 `db_saved: false`
- [ ] `docs/contracts.md` 2번에 `run_id`·`db_saved`와 `runs` API를 추가

**팀원 확인:**
- [ ] 오정훈 확인 완료
- **확인일:** 

### Task P-3: 길안내가 "지금 타는 버스"의 혼잡도를 쓰게 하기 ★★★☆☆

**왜:** PR #1에서 혼잡도의 시간 기준을 "기점 출발 시각"으로 바로잡았습니다. 그런데 길안내(`app/domains/bus/guide.py`의 `_leg`)는 승객이 정류장에서 타는 **시계 시각**의 시간대로 구간 혼잡도를 읽습니다. 노선 중간·끝 정류장에서는 그 시각에 오는 버스가 한두 시간 전에 기점을 떠난 버스라서, 다른 시간대의 값을 읽게 됩니다.

**읽을 파일:** `docs/decisions.md`의 2026-11-13 "혼잡도의 시간 기준" 행, `app/domains/bus/advisor.py`의 `analyze_day`, `app/domains/bus/guide.py`의 `_leg`

**구현:**
- [ ] `BusAdvisor`에 함수 추가: 노선·날짜·시계 시각·정류장 순번을 받아 "그 시각에 그 정류장을 지나는 버스"의 기점 출발 시간대를 돌려줌. 계산은 `운행시간 × (순번 ÷ 전체 정류장 수)`만큼 거슬러 올라가는 방식으로 시작 (구간별 실제 소요시간 자료가 없으므로)
- [ ] `guide.py`의 `_leg`가 타는 정류장 기준으로 그 시간대의 구간 혼잡도를 읽도록 변경. 환승 뒤 구간은 "앞 구간 탑승 시간만큼 지난 시각"으로 계산
- [ ] `ASSUMPTION` 문구에서 "환승 뒤에도 같은 시간대로 계산했습니다"를 실제 계산에 맞게 수정
- [ ] 테스트: 기점 근처 정류장과 종점 근처 정류장에서 같은 시계 시각에 다른 출발 시간대를 읽는지

**검증:**
- [ ] `python -m pytest -q` 통과 (`tests/test_guide.py` 포함)
- [ ] 실데이터 한 노선에서 종점 근처 정류장의 값이 바꾸기 전·후 어떻게 달라지는지 숫자로 기록 (`docs/decisions.md`에 한 줄)

**팀원 확인:**
- [ ] 오정훈 확인 완료 (길안내 답변·팝업이 그대로 동작)
- **확인일:** 

### Task P-4: 배차 방식 성능 비교를 화면에서 보기 (선택) ★★☆☆☆

**왜:** 현행·규칙·그리디·정수계획 비교표(`eval/results/backtest.json`)는 발표의 핵심 근거인데 지금은 파일로만 있습니다. 대시보드에서 바로 보이면 시연 중에 화면을 바꾸지 않아도 됩니다.

**읽을 파일:** `eval/results/backtest.json`, `frontend/src/components/MetricsCard.vue`, `frontend/src/layout.js`

**구현:**
- [ ] `GET /api/bus/backtest`: `eval/results/backtest.json`을 읽어 방식별 과밀 비율·총 운행 증감·새 인가대수 초과 건수를 반환. 파일이 없으면 404와 "python scripts/backtest.py를 먼저 실행" 안내. 파일은 `encoding="utf-8"`로 읽기
- [ ] 패널 1개(또는 `MetricsCard`에 표 추가): 방식 4개를 한 표에, 평가 구간과 "평가 구간을 여러 번 채점함" 주의 문구를 함께 표시
- [ ] 로딩·에러·빈 상태 표시. 색은 `theme.js`·`style.css` 토큰만 사용

**검증:**
- [ ] `python -m pytest -q` 통과 (정상 1건 + 파일 없음 1건), `npm run build` 성공
- [ ] 화면의 숫자가 `backtest.json`과 같음

**팀원 확인:**
- [ ] 김주혜 확인 완료 (발표 대본에서 쓸 수 있는지)
- **확인일:** 

---

## 남은 문제 · 다음 단계

- **PR #1과 `feat/oh-assistant`의 충돌** — 두 브랜치가 `adapter.py`, `bus/schemas.py`, `layout.js`, `README.md`, `config.py`를 함께 고쳤습니다. 내용은 겹치지 않습니다(오정훈 쪽은 길안내·챗봇 추가). PR #1이 먼저 `dev`에 들어가면 오정훈이 자기 브랜치에서 충돌을 정리합니다.
- **`predict` 응답에 `congestion` 추가됨** — `hour`를 준 경우 그 시간대의 최대 혼잡도(`{"ratio", "level"}`)를 함께 돌려줍니다. `analyze_day`의 `hourly["max_congestion"]`을 읽으므로, 그 열의 이름이나 뜻을 바꾸면 알려 주세요.
- **P-3은 정확도 개선이지 필수는 아님** — 시간이 없으면 길안내 답변의 "혼잡도는 예측값" 안내로 두고 넘어가도 됩니다.
