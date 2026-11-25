# 기능 규격 (F-21)

기능끼리 주고받는 데이터 형식입니다. 형식을 바꾸려면 이 문서와 쓰는 쪽 코드를 **같은 PR**에서 고칩니다.
실제 필드 전체는 서버를 띄운 뒤 `http://127.0.0.1:8000/docs`에서 볼 수 있고, 이 문서는 다른 사람이 기대도 되는 부분만 적습니다.

| 규격 | 만드는 사람 | 쓰는 사람 | 상태 |
|---|---|---|---|
| `POST /api/bus/simulate` | 박성훈 | 챗봇 | 구현됨 (PR #1). 챗봇 연결됨 |
| `POST /api/bus/optimize` | 박성훈 | 챗봇, 발표 | 구현됨 (PR #1). 챗봇 연결됨. 실행 이력 저장(`run_id`, `db_saved`) 추가 |
| `GET /api/bus/optimize/runs` | 박성훈 | 최적화 화면 | 구현됨 (park-next P-2) |
| `GET /api/bus/backtest` | 박성훈 | 성능 비교 화면, 발표 | 구현됨 (park-next P-4) |
| `POST /api/bus/chat` | 오정훈 | 채팅 화면 | 구현됨 (예측·권고·규정·길안내·노선 정보·시뮬레이션·최적화) |
| `POST /api/bus/guide` · `GET /api/bus/stops/search` | 오정훈 | 챗봇, 길안내 팝업 | 구현됨 |
| `GET /api/bus/routes/{route}/info` | 오정훈 | 챗봇 | 구현됨 |
| `eval/agent_eval.json` 형식 | 김주혜 | 챗봇 채점 | 초안. 김주혜 확인 필요 |
| `/api/bus/documents` | 김주혜 | 문서 관리 화면 | 구현됨 (PR #6). 아래 5번은 구현에 맞춰 고침 |
| `role` 값 (열람 역할) | 김주혜 | 챗봇, 법규 질의응답 | 구현됨 (PR #6). `/ask`와 `/chat` 모두 적용 |

공통
- 에러는 `{"status": "<코드>", "detail": "<설명>"}` 형식입니다.
- 권고·최적화·시뮬레이션·챗봇 응답에는 항상 `needs_review: true`가 있습니다.
- 날짜는 `YYYY-MM-DD`, 시간대는 0~23 정수입니다.

## 1. `POST /api/bus/simulate` — 배차간격 변경 시뮬레이션 (F-12)

```
요청: {"route": "143", "date": "2026-10-28", "hour": 8, "interval": 5}
응답: {"route": "143", "date": "2026-10-28", "hour": 8, "daytype": "weekday", "is_forecast": true,
       "current":   {"interval": 8.6, "trips_per_hour": 7.0, "max_congestion": 0.85, "level": "혼잡"},
       "simulated": {"interval": 5.0, "trips_per_hour": 12.0, "max_congestion": 0.49, "level": "보통"},
       "bounds": {"min_interval": 4, "max_interval": 12},
       "fleet": {"buses_needed_before": 38, "buses_needed": 44, "authorized_buses": 50,
                 "authorized_estimated": false, "over": false},
       "peak_segment": {"seq": 31, "from_name": "..."},
       "segments": [{"seq": 1, "from_name": "...", "congestion_before": 0.21, "level_before": "여유",
                     "congestion": 0.12, "level": "여유"}],
       "assumption": "...", "needs_review": true}
```

- `interval`이 `bounds` 밖이면 400입니다.
- 챗봇이 쓰는 필드: `current`, `simulated`, `fleet.buses_needed_before`·`buses_needed`·`authorized_buses`·`over`, `peak_segment.from_name`.
- 챗봇과 API는 같은 메서드(`BusAdapter.simulate`)를 부릅니다. 범위 밖의 간격은 챗봇에서 오류가 아니라 이유를 담은 답변으로 나옵니다.
- 위 숫자는 형식을 보여 주는 예시입니다.

## 2. `POST /api/bus/optimize` — 전 노선 배차 최적화 (F-13)

```
요청: {"date": "2026-10-28", "hours": [6, 7, 8, 9],
       "weights": {"overload": 10, "trips": 1, "change": 0}}     ← weights는 생략 가능
응답: {"date": "2026-10-28", "daytype": "weekday", "is_forecast": true,
       "method": "cpsat", "status": "optimal", "solve_seconds": 0.4, "weights": {...},
       "plan": [{"route": "143", "hour": 8, "current_trips": 7, "recommended_trips": 9, "change": 2,
                 "action": "increase", "interval": 6.7, "buses_needed": 41, "fleet": 50,
                 "fleet_estimated": false, "max_congestion_before": 0.85, "max_congestion_after": 0.66,
                 "upper_congestion_before": 0.93, "upper_congestion_after": 0.72,
                 "level_after": "보통", "limited_by": null}],
       "kpi": {"overcrowded_before": 6, "overcrowded_after": 1, "extra_trips": 9, "changed": 8,
               "increased": 6, "decreased": 2, "total_trips_before": 120, "total_trips_after": 129},
       "binding": [], "skipped": [{"route": "160", "hour": 6, "reason": "..."}],
       "assumption": "...", "cut_notice": null, "needs_review": true,
       "run_id": 7, "db_saved": true}
```

- `action`: `increase` · `decrease` · `keep`.
- 승객 수 상한 비율표가 없으면 503입니다 (`python scripts/calibrate_p90.py` 먼저 실행).
- 챗봇이 쓰는 필드: `kpi` 전체, `plan[].route·hour·current_trips·recommended_trips·change·action`, `binding`, `cut_notice`.
- 챗봇과 API는 같은 메서드(`BusAdapter.optimize`)를 부릅니다. 챗봇은 "7시부터 9시", "출근 시간"(7~9시), "퇴근 시간"(17~19시)을 `hours`로 바꿉니다.
- 이력 테이블 `optimization_run`에는 요청(`date`, `hours`, `weights`)과 `plan`, `kpi`, `binding`, `solve_seconds`를 저장합니다.
- **API로 부를 때마다 이력에 한 줄 저장**하고 `run_id`(저장한 id)·`db_saved`를 돌려줍니다. DB가 꺼져 있으면 결과는 그대로이고 `run_id: null`, `db_saved: false`입니다. 챗봇이 메서드로 부른 최적화는 저장하지 않습니다.
- 최적화 화면은 날짜·시간대를 바꿀 때마다 API를 부르므로 화면을 쓴 만큼 이력이 쌓입니다. 단 **직전 실행과 날짜·시간대·가중치·결과 요약(`status`, 과밀 전후, 운행 증감)이 모두 같으면 새 줄을 만들지 않고 직전 `run_id`를 돌려줍니다** (같은 조건을 연달아 부른 경우만. 사이에 다른 조건이 있었으면 새 줄).

### 2-1. `GET /api/bus/optimize/runs?limit=20` — 최적화 실행 이력

```
응답: {"db": {"available": true, "error": null},
       "items": [{"id": 7, "date": "2026-10-28", "hours": [6, 7, 8, 9], "weights": {"overload": 10, "trips": 1, "change": 0},
                  "status": "optimal", "solve_seconds": 0.4, "overcrowded_before": 6, "overcrowded_after": 1,
                  "extra_trips": 9, "created_at": "2026-11-22 03:12:45"}]}
```

- 최근 실행부터. `limit`은 1~100(기본 20). DB가 꺼져 있으면 `items`는 빈 목록이고 `db.available: false`.
- `hours`는 요청한 시간대입니다(운행이 없어 `plan`에서 빠진 시간대 포함).

### 2-2. `GET /api/bus/backtest` — 배차 방식 성능 비교 (F-14)

```
응답: {"eval_window": {"start": "2026-09-30", "end": "2026-11-01", "days": 32},
       "tune_window": {"start": "2026-09-01", "end": "2026-09-28", "days": 28}, "crowded_threshold": 0.8,
       "methods": [{"name": "현행", "label": "현행", "weights": null, "reference": false, "why": null,
                    "cells": 3103, "route_days": 160, "overcrowded_cells": 641, "overcrowded_pct": 20.66,
                    "total_trips": 24806.0, "trips_vs_current_pct": 0.0, "changed_cells": 0, "fleet_over_new_days": 0}, ...],
       "pick_order": ["새로 생긴 인가대수 초과 0건", ...], "notes": ["..."], "caution": "..."}
```

- `methods` 순서: 현행 · 규칙 · 그리디 · 최적화 · 감차 허용(`reference: true`, 참고 행). 숫자는 `eval/results/backtest.json`을 그대로 옮긴 값이고, 서버는 다시 계산하지 않습니다.
- 결과 파일이 없으면 404(`python scripts/backtest.py`를 먼저 실행), 형식이 다르면 500.

## 3. `POST /api/bus/chat` — 배차 상담 챗봇 (F-25)

```
요청: {"message": "160번 10월 28일 오후 3시에 혼잡해?",
       "history": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}],
       "llm": "qwen", "embedding": "bge", "use_llm": false, "role": "citizen",
       "location": {"lat": 37.5665, "lng": 126.978}}                    ← 선택. 길안내의 출발지
응답: {"domain": "bus",
       "answer": "160번 2026-10-28 15시 예상 승차는 1,509명입니다. ...",
       "intent": "predict",
       "slots": {"route": "160", "date": "2026-10-28", "hour": 15},
       "tools": [{"name": "predict", "args": {"route": "160", "date": "2026-10-28", "hour": 15}, "ok": true}],
       "sources": [{"id": 1, "document": "...", "article": "...", "page": 3, "url": null, "score": 0.61, "snippet": "..."}],
       "numbers_verified": true, "llm": null, "llm_rejected": false, "data": null, "elapsed_sec": 0.16,
       "needs_review": true, "chat_id": 12, "db_saved": true}
```

| 필드 | 값 |
|---|---|
| `message` | 2~500자 |
| `history` | 최대 10건, 오래된 것부터. 이어 묻기("그럼 4시는?")에 사용. 생략 가능 |
| `use_llm` | `false`(기본)면 정해진 문장 틀로 답함. `true`면 규정 답변은 LLM이 요약하고, 예측·권고 답변은 LLM이 문장을 다듬음. 다듬은 문장의 숫자(값·단위·순서·설명)나 결론(권고 방향·혼잡 단계)이 문장 틀 답변과 다르면 버리고 문장 틀 답변을 씀(`llm_rejected: true`) |
| `location` | 현재 위치. 길안내 질문에서 출발 정류장을 말하지 않았을 때만 씀. 응답의 `slots`·`tools`에는 좌표가 들어 있지만, 대화 기록(`chat_log`)에는 좌표를 빼고 `slots.location_used: true`만 남김 |
| `role` | 문서 열람 역할 `citizen`(기본) · `staff` · `admin`. 규정 검색에 전달 (6번) |
| `intent` | `predict` · `advise` · `ask` · `guide` · `route_info` · `simulate` · `optimize` · `clarify`(빠진 값 되묻기) · `out_of_scope` |
| `tools[].name` | 실제로 부른 기능: `predict`(F-02) · `advise`(F-04) · `ask`(F-05) · `guide`(F-28) · `route_info`(F-29) · `simulate`(F-12) · `optimize`(F-13). 되묻기·범위 밖은 빈 목록 |
| `slots.from_here` | `true`면 "여기서 ○○ 어떻게 가?"처럼 현재 위치에서 출발하는 질문인데 `location`이 없었다는 뜻. 화면은 위치를 얻어 같은 질문을 다시 보냄 |
| `llm_rejected` | `true`면 LLM이 쓴 문장을 버리고 문장 틀로 답함. 버리는 경우: 숫자가 기능 결과와 다름 / 권고 방향이 바뀜(유지 → 감차 등) / 혼잡 단계가 다름 |
| `data` | 길안내 답변일 때만 `/api/bus/guide` 응답 전체. 화면이 '지도로 보기' 팝업에 씀 |
| `tools[].ok` | `false`면 기능이 오류를 돌려줌 (없는 노선, 예측 범위 밖 날짜). `answer`에 이유가 들어감 |
| `numbers_verified` | 답변 속 숫자가 모두 기능 결과에 있으면 `true`. 숫자를 대조하지 않는 답(규정·되묻기·범위 밖)은 `null` |
| `sources` | 규정 질의응답(`/ask`)의 `sources`와 같은 형식 |

- 응답은 항상 200입니다. 기능 오류도 `answer`로 설명합니다. 422는 `message` 길이처럼 요청 형식이 틀렸을 때만 납니다.
- `predict` 응답에 `congestion: {"ratio", "level"}`(그 시간대 가장 붐비는 구간)을 추가했습니다. `hour`를 준 경우에만 들어갑니다.

## 3-1. `POST /api/bus/guide` — 버스 길안내 (F-28)

```
요청: {"origin": "정릉우체국앞", "destination": "종로3가역", "date": "2026-10-25", "hour": 8}
      또는 {"lat": 37.5665, "lng": 126.978, "destination": "종로3가역", "date": "...", "hour": 8}   ← 현재 위치
응답: {"date": "2026-10-25", "hour": 8, "is_forecast": false,
       "origin": {"label": "정릉우체국앞", "stop": "정릉우체국앞", "lat": 37.6, "lng": 127.0, "walk_m": 0, "far": false, "ambiguous": false},
       "destination": {"stop": "종로3가역.탑골공원", "lat": 37.57, "lng": 126.99, "ambiguous": false},
       "options": [{"kind": "direct", "routes": ["143"], "total_min": 27,
                    "max_congestion": 0.321, "level": "여유",
                    "fastest": true, "least_crowded": true, "recommended": true,
                    "legs": [{"route": "143", "board": {"name": "...", "seq": 6, "lat": 0, "lng": 0},
                              "alight": {"name": "...", "seq": 17, "lat": 0, "lng": 0}, "direction": "개포동",
                              "stops": 11, "walk_m": 0, "walk_min": 0, "interval_min": 8.6, "wait_min": 4.3,
                              "ride_min": 23, "congestion": 0.321, "level": "여유", "start_hour": 7}]}],
       "recommendation": {"index": 0, "reason": "fastest", "slower_by_min": 0},
       "walk_only": null,
       "assumption": "..."}
```

- 직행 또는 환승 한 번 경로만 찾습니다(버스만, 지하철 제외). 경로가 없으면 `options`는 빈 목록입니다.
- **아는 노선**: `data/interim/network/`(전체 노선 정류장, `python scripts/fetch_network_stops.py`)가 있으면 서울 버스 723개 노선, 없으면 이 서비스가 예측하는 노선만.
- **혼잡도는 예측하는 노선(143·150·160·271·272)에만 있습니다.** 그 밖의 노선은 `legs[].predicted: false`, `congestion: null`이고 배차간격은 노선 기본정보의 평일 값입니다.
- 이름으로 찾은 정류장 주변 300m의 정류장도 타고 내릴 곳으로 봅니다(길 건너·다른 출구). 그래서 `legs[].walk_m`(타러 가는 거리)과 `legs[].exit_walk_m`(내려서 도착지까지)이 붙습니다.
- `legs[].path`: 타고 가는 정류장들의 `[위도, 경도]`. 화면이 팝업 지도에 그립니다.
- **`legs[].congestion`·`interval_min`의 시간 기준**: 구간 혼잡도·운행횟수는 버스가 기점을 떠난 시간대 기준 값입니다(`docs/decisions.md` 2026-11-13). 그래서 "N시 30분에 출발 → 걸어서 탈 정류장에 닿는 시각"에서 기점 → 그 정류장 소요 시간(공식 시간표, 없으면 운행시간 × 순번 비율)을 빼서, 그 버스가 기점을 떠난 시간대의 값을 읽습니다. 그 시간대가 `legs[].start_hour`입니다(예측하지 않는 노선은 `null`). 갈아탄 뒤 구간은 앞 구간의 걷기·대기·탑승 시간만큼 지난 시각으로 계산합니다.
- `legs[].ride_min`(탑승 시간)은 **정류장별 공식 시간표**(첫차·막차 시각)의 구간 차이로 계산합니다. 새벽 5시까지는 첫차 시간표, 그 뒤는 막차 시간표를 쓰고, 출퇴근 시간(7~9시, 17~19시)에는 1.15를 곱합니다(가정값). 시간표가 없거나 이상한 노선은 도로 거리 ÷ 평균 속도로 계산합니다. `legs[].distance_km`은 도로 거리입니다. 실시간 교통 상황은 반영하지 않습니다.
- 정류장 이름은 일부만 적어도 됩니다 ("종로3가역" → "종로3가역.탑골공원"). 없는 이름이면 422.
- `origin.far`가 `true`면 걸어갈 거리(500m) 안에 정류장이 없어 가장 가까운 정류장에서 타는 경로입니다. 이때 걷는 시간은 `total_min`에 넣지 않습니다.
- 요청한 날짜가 예측 범위 밖이면 범위 안의 가장 가까운 같은 요일로 계산하고, 응답의 `date`에 계산한 날짜를, `requested_date`에 원래 요청한 날짜를 넣습니다. 길은 날짜와 상관없이 같고 혼잡도·배차간격만 그날 기준입니다.
- `walk_only`: 출발지(정류장 또는 현재 위치)가 도착지에서 300m 안이면 `{"walk_m": 45, "walk_min": 1}`이고, 이때 `options`는 빈 목록·`recommendation`은 `null`입니다. 그 밖에는 `null`. `walk_min`은 최소 1이고, 위치가 정류장 좌표와 같으면 `walk_m`이 0입니다(화면은 "이미 도착 정류장에 있습니다"로 표시).
- `origin.ambiguous`·`destination.ambiguous`: 같은 이름의 정류장이 1.5km 넘게 떨어진 여러 곳에 있으면 `true`. 상대편(출발지·도착지)에서 가까운 곳부터 최대 3곳을 찾아보고, 경로가 있는 첫 번째 곳으로 안내합니다. 어디에도 경로가 없으면 가장 가까운 곳 기준으로 답합니다.
- `kind`: `direct` · `transfer`. `recommendation.reason`: `fastest` · `less_crowded`(조금 더 걸리지만 덜 붐빔).
- 소요 시간은 운행시간에서 어림한 값이고, 혼잡도는 F-03의 예측값입니다.
- `GET /api/bus/stops/search?q=종로&limit=10` → `[{"name", "lat", "lng", "routes": ["143", "271"]}]`. `limit` 최대 20.

## 3-2. `GET /api/bus/routes/{route}/info` — 노선 기본 정보 (F-29)

```
응답: {"route": "160", "route_type": "간선", "company": "서울교통네트웍",
       "start": "도봉산역광역환승센터", "end": "온수동종점", "first_bus": "03:57", "last_bus": "22:10",
       "interval_weekday": 8.0, "min_interval": 6.0, "max_interval": 13.0, "run_minutes": 261.0,
       "authorized_buses": 33.0, "authorized_estimated": true, "distance_km": 70.0,
       "stops_count": 120, "key_stops": ["도봉산역광역환승센터", "...", "온수동종점"]}
```

- 없는 노선은 404. `key_stops`는 기점에서 회차 지점까지 고르게 뽑은 정류장입니다.
- 예측하지 않는 노선(전체 노선 파일에만 있는 노선)은 `predicted: false`이고 `min_interval`·`max_interval`·`authorized_buses`·`distance_km`이 `null`입니다.
- 챗봇은 "160번 버스 노선 알려줘", "143번 첫차 몇 시야?", "143번 어디서 어디까지 가?" 같은 질문에 이 기능을 부릅니다.
- `GET /api/bus/meta`의 `routes[]`와 같은 값을 씁니다 (`adapter._route_row`).

## 4. `eval/agent_eval.json` — 챗봇 채점 문제집 (F-33) · 초안

```
[{"id": "G1", "q": "160번 10월 28일 오후 3시에 혼잡해?", "tools": ["predict"],
  "slots": {"route": "160", "date": "2026-10-28", "hour": 15}},
 {"id": "G2", "q": "버스 늘리려면 뭐 해야 돼?", "tools": ["ask"]},
 {"id": "G3", "q": "271번 몇 명 타?", "tools": [], "intent": "clarify"},
 {"id": "G4", "q": "오늘 점심 메뉴 추천해줘", "tools": [], "intent": "out_of_scope"}]
```

- `tools`: 불러야 하는 기능 이름 목록(위 3번의 `tools[].name`과 같은 값). 아무것도 부르면 안 되는 문항은 빈 목록.
- 이어 묻기 문항은 `history`(이전 대화)를, 현재 위치를 쓰는 길안내 문항은 `location`을 함께 적습니다.
- 형식 예시와 채점 방법은 `eval/agent_eval.sample.json`(오정훈이 만든 15문항)과 `scripts/eval_agent.py`를 보면 됩니다. 채점: `python scripts/eval_agent.py --name agent_1`
- `slots`·`intent`는 선택. 적으면 그 값도 채점합니다.
- 날짜는 "내일" 같은 말 대신 `10월 28일`처럼 적습니다 (채점하는 날에 따라 답이 달라지지 않게).
- 채점: 응답의 `tools[].name` 목록이 `tools`와 같으면 정답. 합격 기준 90%.

## 4-1. `eval/rag_eval.json` `gold_articles` · 검색 전용 결과 (E-4)

- 정답형(`type=="answer"`) 항목에만 `gold_articles: [{"document": str, "article": str|null}]`. 키는 항목당 한 번. 거절형은 없음.
- 일치: 공백 정규화 후 `document`는 부분 문자열, `article`은 접두 일치(`null`이면 문서만). 여러 개면 하나만 맞아도 hit. 형식: PDF `"제N조(제목) ① 제2호 마목"`, 보도자료 `"본문 N"`.
- `python scripts/eval_rag.py --retrieval-only [--k 4] [--name rag_retrieval_1]` → `eval/results/<name>.json` = `{"summary","rows"}`. LLM을 로드하지 않음. 검색은 `max(k,3)`개라 hit@3은 k<3이어도 상위 3개 기준.
- `summary`: `name, mode("retrieval_only"), embedding, role, k, n, hit_at_1/3/k(소수 4자리), hit_at_1/3/k_n("a/b"), mrr, seconds`
- `rows[]`: `id, q, gold_articles, rank(상위 k 안 첫 일치, 없으면 null), hit_at_1/3/k(bool), retrieved(["문서 조항"]), scores(retrieved와 같은 길이)`
- 읽는 쪽은 `summary.mode`로 분기. `mode`가 없으면 기존 judge 결과(`answer_correct` 등). 두 키 집합은 섞지 않음. 오류는 결과 파일 없이 `p.error`(종료 코드 2).

## 5. `/api/bus/documents` — 법규 문서 관리 (F-36)

로컬 시연용 `role` 쿼리를 받으며 기본값은 `citizen`입니다. 로그인 인증은 별도 범위입니다.

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/bus/documents?role=citizen&limit=100` | 역할이 볼 수 있는 등급의 목록 `{"items": [...]}`. limit 1~1000 |
| POST | `/api/bus/documents?role=admin` | multipart file·title·type·grade. 201 |
| PUT | `/api/bus/documents/{file}?role=admin` | JSON title·type·grade·active. 200 |
| DELETE | `/api/bus/documents/{file}?role=admin` | 업로드 파일과 목록 항목 삭제. 204 |

- 추가·수정·삭제는 admin만 허용하며 나머지 역할은 403입니다. 목록은 공개·내부·제한 등급을 역할별로 거릅니다.
- 문서 응답은 file·title·type·grade·active·created_at·deletable입니다. 기존 기본 문서의 created_at은 null일 수 있습니다.
- 기본 PDF·TXT는 docs/bus/에서 팀이 공유하고, 화면 업로드는 Git에서 제외한 data/docs/bus/에 저장합니다.
- 운영 목록은 data/docs/bus/documents.json이며 커밋하지 않습니다. 기존 docs/bus/documents.json도 읽되 로컬 운영 목록이 우선합니다.
- 기본 문서는 deletable=false이며 삭제는 403입니다. 제목·등급·사용 여부 변경은 운영 목록에만 저장합니다.
- 업로드·목록 관리에서는 기본·업로드 폴더 전체에서 대소문자를 무시한 같은 파일명을 충돌로 처리합니다(409). 없는 파일은 404입니다. 수동으로 중복 파일이 생기면 검색·상태 확인은 중복된 양쪽 파일을 제외하고 나머지 문서로 동작합니다. 관리 목록의 409를 확인한 뒤 중복을 해소해야 합니다.
- PDF·UTF-8 TXT만 최대 20MB까지 허용합니다. 경로·확장자·내용·크기 오류는 400, 제목·종류·등급 검증은 추가·수정 모두 422입니다.
- 종류는 법령·조례·보도자료·내부자료, 등급은 공개·내부·제한입니다.
- 사용 중지 문서도 색인에는 유지하고, 검색 시 최신 등급·사용 여부를 적용합니다. 정책만 바뀌면 재색인하지 않습니다.
- 파일 추가·삭제·내용 수정이나 제목 변경은 다음 검색에서 재색인합니다. 갱신 판단에는 파일 수정 시각(ns)과 등록 시각(created_at)도 포함해 같은 이름·크기·제목의 재업로드를 구분합니다. Markdown 출처 목록은 검색에서 제외합니다.
- GET /api/bus/knowledge-status?embedding=bge 는 rebuild_required(bool)만 반환합니다. 파일명이나 문서 내용을 노출하지 않습니다. 질문 화면과 챗봇은 이 상태로 검색 색인 갱신 대기를 안내합니다. 안내용 상태 조회는 실패하거나 3초 안에 응답하지 않아도 실제 질문 전송을 계속합니다.
- 기존 업로드는 자동 이동하지 않습니다. 관리자가 기존 docs/bus 자료를 확인한 뒤 새 업로드 경로로 이관해야 합니다.

## 6. `role` 값 — 문서 열람 역할 (F-37)

| `role` (요청) | 화면 표시 | 검색에 쓰는 문서 등급 |
|---|---|---|
| `citizen` (기본) | 시민 | `공개` |
| `staff` | 배차 담당자 | `공개`, `내부` |
| `admin` | 관리자 | `공개`, `내부`, `제한` |

- `/api/bus/ask`와 `/api/bus/chat` 요청에 `role`을 넣습니다. 생략하면 `citizen`, 모르는 값이면 422.
- 볼 수 없는 등급의 문서는 검색 단계에서 빠지고, 남은 근거가 없으면 "찾을 수 없음"으로 답합니다.
- 볼 수 없는 문서가 앞 순위를 차지해도, 허용된 문서가 `k`개 찰 때까지 후보를 늘려 찾습니다(#8에서 고침).
- 배차 권고(`/advise`)에 붙는 근거 조항은 항상 `citizen` 기준(공개 문서)입니다.
- 로그인이 없는 시연용 기능입니다. `role`은 화면에서 고르는 값입니다.
