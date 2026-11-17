# 기능 규격 (F-21)

기능끼리 주고받는 데이터 형식입니다. 형식을 바꾸려면 이 문서와 쓰는 쪽 코드를 **같은 PR**에서 고칩니다.
실제 필드 전체는 서버를 띄운 뒤 `http://127.0.0.1:8000/docs`에서 볼 수 있고, 이 문서는 다른 사람이 기대도 되는 부분만 적습니다.

| 규격 | 만드는 사람 | 쓰는 사람 | 상태 |
|---|---|---|---|
| `POST /api/bus/simulate` | 박성훈 | 챗봇 | PR #1 기준으로 옮겨 적음. 박성훈 확인 필요 |
| `POST /api/bus/optimize` | 박성훈 | 챗봇, 발표 | PR #1 기준으로 옮겨 적음. 박성훈 확인 필요 |
| `POST /api/bus/chat` | 오정훈 | 채팅 화면 | 구현됨 (예측·권고·규정·길안내). 시뮬레이션·최적화는 연결 전 |
| `POST /api/bus/guide` · `GET /api/bus/stops/search` | 오정훈 | 챗봇, 길안내 팝업 | 구현됨 |
| `GET /api/bus/routes/{route}/info` | 오정훈 | 챗봇 | 구현됨 |
| `eval/agent_eval.json` 형식 | 김주혜 | 챗봇 채점 | 초안. 김주혜 확인 필요 |
| `/api/bus/documents` | 김주혜 | 문서 관리 화면 | 초안. 김주혜 확인 필요 |
| `role` 값 (열람 등급) | 김주혜 | 챗봇, 법규 질의응답 | 초안. 김주혜 확인 필요 |

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
- 챗봇이 쓰는 필드: `current`, `simulated`, `bounds`, `fleet.buses_needed`, `fleet.authorized_buses`, `fleet.over`.
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
       "assumption": "...", "cut_notice": null, "needs_review": true}
```

- `action`: `increase` · `decrease` · `keep`.
- 승객 수 상한 비율표가 없으면 503입니다 (`python scripts/calibrate_p90.py` 먼저 실행).
- 챗봇이 쓰는 필드: `kpi` 전체, `plan[].route·hour·current_trips·recommended_trips·action`, `binding`, `cut_notice`.
- 이력 테이블 `optimization_run`에는 요청(`date`, `hours`, `weights`)과 `plan`, `kpi`, `binding`, `solve_seconds`를 저장합니다.

## 3. `POST /api/bus/chat` — 배차 상담 챗봇 (F-25)

```
요청: {"message": "160번 10월 28일 오후 3시에 혼잡해?",
       "history": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}],
       "llm": "qwen", "embedding": "bge", "use_llm": false, "role": "public",
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
| `use_llm` | `false`(기본)면 정해진 문장 틀로 답함. `true`면 규정 답변은 LLM이 요약하고, 예측·권고 답변은 LLM이 문장을 다듬음 |
| `location` | 현재 위치. 길안내 질문에서 출발 정류장을 말하지 않았을 때만 씀. 서버에 저장하지 않음 |
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
       "origin": {"label": "정릉우체국앞", "stop": "정릉우체국앞", "lat": 37.6, "lng": 127.0, "walk_m": 0, "far": false},
       "destination": {"stop": "종로3가역.탑골공원", "lat": 37.57, "lng": 126.99},
       "options": [{"kind": "direct", "routes": ["143"], "total_min": 27,
                    "max_congestion": 0.321, "level": "여유",
                    "fastest": true, "least_crowded": true, "recommended": true,
                    "legs": [{"route": "143", "board": {"name": "...", "seq": 6, "lat": 0, "lng": 0},
                              "alight": {"name": "...", "seq": 17, "lat": 0, "lng": 0}, "direction": "개포동",
                              "stops": 11, "walk_m": 0, "walk_min": 0, "interval_min": 8.6, "wait_min": 4.3,
                              "ride_min": 23, "congestion": 0.321, "level": "여유"}]}],
       "recommendation": {"index": 0, "reason": "fastest", "slower_by_min": 0},
       "assumption": "..."}
```

- 이 서비스의 노선이 지나는 정류장 사이에서 직행 또는 환승 한 번 경로만 찾습니다. 경로가 없으면 `options`는 빈 목록입니다.
- 정류장 이름은 일부만 적어도 됩니다 ("종로3가역" → "종로3가역.탑골공원"). 없는 이름이면 422.
- `origin.far`가 `true`면 걸어갈 거리(500m) 안에 정류장이 없어 가장 가까운 정류장에서 타는 경로입니다. 이때 걷는 시간은 `total_min`에 넣지 않습니다.
- 요청한 날짜가 예측 범위 밖이면 범위 안의 가장 가까운 같은 요일로 계산하고, 응답의 `date`에 계산한 날짜를, `requested_date`에 원래 요청한 날짜를 넣습니다. 길은 날짜와 상관없이 같고 혼잡도·배차간격만 그날 기준입니다.
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

## 5. `/api/bus/documents` — 법규 문서 관리 (F-36) · 초안

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/bus/documents?limit=50` | 문서 목록. 없으면 빈 목록 |
| POST | `/api/bus/documents` | 파일(PDF·TXT) + `title`, `kind`, `access` 추가 |
| PUT | `/api/bus/documents/{id}` | `title`, `kind`, `access` 수정 |
| DELETE | `/api/bus/documents/{id}` | 삭제. 없으면 404 |

```
문서 1건: {"id": "...", "title": "여객자동차 운수사업법 시행규칙", "file": "....pdf",
           "kind": "법령", "access": "public", "created_at": "2026-11-16T10:00:00"}
```

- 문서를 추가·수정·삭제하면 검색 인덱스를 다시 만들어야 합니다. 인덱스가 최신인지는 `GET /api/health`가 아니라 문서 목록 응답에 넣는 것으로 제안합니다.

## 6. `role` 값 — 열람 등급 (F-37) · 초안

| `role` (요청) | 볼 수 있는 문서 `access` |
|---|---|
| `public` (기본) | `public` |
| `staff` | `public`, `internal` |
| `admin` | `public`, `internal`, `restricted` |

- `/api/bus/ask`와 `/api/bus/chat` 요청에 `role`을 넣습니다. 생략하면 `public`.
- 볼 수 없는 문서는 검색 단계에서 빼고, 남은 근거가 없으면 "찾을 수 없음"으로 답합니다.
- 지금 `/chat`은 `role`을 받기만 하고 쓰지 않습니다. F-37이 들어오면 검색에 넘깁니다.
- 로컬 전용이라 로그인은 없습니다. `role`은 화면에서 고르는 값입니다.
