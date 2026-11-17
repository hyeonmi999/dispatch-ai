# AI 어시스턴트 · 실행 환경 (F-21 ~ F-29)

> **담당:** 오정훈
> **상태:** 진행
> **작성일:** 2026-11-16 · **최종 업데이트:** 2026-11-17

## 목표

[담당 가이드](../guides/guide-ai-platform-oh.md)의 6개 기능과, 뒤에 추가한 길안내(F-28)를 로컬 실행 기준으로 끝냅니다. 말로 물으면 예측·권고·법규·길안내·시뮬레이션·최적화 기능을 대신 불러 답하는 챗봇(F-25)과 채팅 화면(F-27)이 중심이고, 나머지는 그 바탕(규격 F-21, 설치·실행 F-22, 이력 DB F-24)과 품질(법규 정확도 F-26)입니다.

Task는 **남을 기다리지 않고 할 수 있는 것부터** 순서를 잡았습니다. Task 1~6과 9는 혼자 진행했고, Task 7은 박성훈 PR #1 머지, Task 8은 김주혜 문제집이 필요합니다.

팀원에게 넘기는 제안은 따로 적었습니다: [박성훈용](park-next.md) · [김주혜용](kim-next.md).

## 설계 결정

- **로컬 전용** — 인증·배포·운영 서버 설정은 만들지 않습니다. DB·API는 `127.0.0.1`에만 묶습니다.
- **모델은 Qwen3-1.7B 하나로 통일** — 세 명 PC에서 모두 돌아가는 크기. 모델 비교·속도 측정은 하지 않습니다.
- **Python은 3.11 또는 3.12** — 교수님 가이드 기준은 3.11이지만 내 PC(Windows)는 3.12로 검증했습니다. 설치 스크립트는 둘 다 받습니다.
- **의도 분류는 키워드 규칙** — 1.7B에 기능 선택을 맡기면 불안정합니다([기능별 설계](../guides/algorithms.md) F-25, 후보 A). 놓친 표현은 키워드를 추가해 고칩니다.
- **답변은 문장 틀이 기본, LLM 문장은 선택** — 문장 틀은 숫자 오류가 0입니다. `use_llm`이면 LLM이 문장을 다듬되, 답변 속 숫자가 기능 결과와 하나라도 다르면 버리고 문장 틀 답변을 씁니다(`llm_rejected`).
- **챗봇은 기존 기능을 그대로 부름** — 예측·권고·법규 로직을 `agent.py`에 다시 만들지 않습니다. 도메인 어댑터(`predict`, `advise`, `guide`, `knowledge_base.ask`)를 호출만 합니다.
- **길안내는 챗봇이 말로 설명, 지도는 누를 때만 팝업** — 따로 패널을 두지 않습니다. 답변 아래 '지도로 보기'를 누르면 경로 지도와 경로 카드가 뜹니다.
- **길안내는 서울 버스 전체 노선, 환승 한 번까지** — 처음에는 예측하는 5개 노선만 알았는데, "가락시장에서 서울역"처럼 답 못 하는 질문이 많아 전체 노선의 정류장 목록(723개 노선, 12,970곳)을 받아 넓혔습니다. 혼잡도는 예측하는 5개 노선에만 있고 나머지는 "혼잡도 예측 없음"으로 표시합니다. 버스만 안내하며 지하철은 다루지 않습니다.
- **프롬프트는 `app/core/prompts.py` 한 곳** (CONTRIBUTING 6장).
- **법규 정확도는 해석 쪽부터, 한 번에 하나만** — 기준선에서 검색은 맞는 조항을 찾았고, 찾고도 거절한 문항이 문제였습니다.

## 영향 범위

| 위치 | 작업 |
|---|---|
| `docs/contracts.md` | 새 문서. 기능끼리 주고받는 형식 |
| `docker/maria_db.yml`, `.env.example` | 포트 `127.0.0.1` 고정, 기본 모델 1.7B |
| `app/config.py` | 챗봇·길안내 상수, Windows 출력 인코딩 보호 |
| `app/core/rag.py`, `forecaster.py`, `scripts/*.py`, `tests/test_forecast.py` | 파일 입출력 `encoding="utf-8"` |
| `scripts/setup.ps1`·`setup.sh`, `run.ps1`·`run.sh` | 새 파일. 설치·실행 |
| `app/core/agent.py`, `app/core/prompts.py` | 새 파일. 챗봇, 프롬프트 모음 |
| `app/domains/bus/guide.py` | 새 파일. 길안내 계산 |
| `app/core/schemas.py`, `app/domains/bus/schemas.py` | 챗봇·길안내 요청·응답 |
| `app/core/domain.py`, `app/domains/bus/adapter.py` | `targets()`, `guide()`, `predict` 응답에 `congestion` 추가, `/api/bus/guide`·`/stops/search` |
| `app/api/routes.py` | `POST /api/{domain}/chat` |
| `app/db/database.py`, `repository.py` | 테이블 2개 추가(`chat_log`, `optimization_run`). **기존 테이블은 수정하지 않으므로 로컬 DB 재생성 불필요** |
| `frontend/src/components/ChatPanel.vue`, `GuideModal.vue`, `BusIcon.vue`, `busIcon.js` | 채팅 패널, 길안내 팝업, 버스 아이콘 |
| `frontend/src/components/MapView.vue`, `Dashboard.vue`, `PanelGrid.vue`, `layout.js`, `api.js` | 지도 번호표 버스 아이콘·경로 표시, 패널 연결 |
| `tests/test_chat.py`, `tests/test_guide.py` | 새 테스트 25개 |
| `scripts/eval_agent.py`, `eval/agent_eval.sample.json`, `eval/results/` | 챗봇 채점 스크립트, 채점 결과 |

## 인터페이스 (다른 사람이 쓰는 부분)

형식 전체는 [docs/contracts.md](../contracts.md)에 있습니다.

```
POST /api/bus/chat
요청: {"message": "160번 10월 28일 오후 3시에 혼잡해?", "history": [], "use_llm": false,
       "location": {"lat": 37.56, "lng": 126.97}}         ← location은 선택 (길안내 출발지)
응답: {"answer": "...", "intent": "predict", "slots": {...},
       "tools": [{"name": "predict", "args": {...}, "ok": true}],
       "sources": [...], "numbers_verified": true, "llm": null, "llm_rejected": false,
       "data": null, "needs_review": true, "chat_id": 12, "db_saved": true}

POST /api/bus/guide
요청: {"origin": "정릉우체국앞", "destination": "종로3가역", "date": "2026-10-25", "hour": 8}
응답: {"origin": {...}, "destination": {...}, "options": [{"kind": "direct", "legs": [...], ...}],
       "recommendation": {"index": 0, "reason": "fastest"}, "assumption": "..."}
```

---

## Task

### Task 1: 기반 정리 · 규격 문서 (F-21 마무리)

**구현:**
- [ ] `dev`에 `main`을 반영하는 PR (공통 룰 파일이 `dev`에 들어가게)
- [ ] `feat/oh-assistant`를 `dev` 기준으로 맞춤
- [x] `docs/contracts.md` 작성: `simulate`·`optimize`(PR #1 스키마 그대로), `chat`, `guide`, `agent_eval.json` 형식·`documents`·`role` 값(초안)

**검증:**
- [x] `python -m pytest -q` 통과
- [ ] `contracts.md`의 `simulate`·`optimize` 예시가 PR #1의 Pydantic 스키마 필드와 1:1로 맞는지 박성훈과 대조

**팀원 확인:**
- [ ] 박성훈 확인 완료 (`simulate`·`optimize`)
- [ ] 김주혜 확인 완료 (`agent_eval.json`·`documents`·`role`)
- **확인일:** 

### Task 2: 설치·실행 자동화 (F-22)

**구현:**
- [x] `docker/maria_db.yml` 포트를 `127.0.0.1:3379:3306`으로 고정
- [x] `encoding="utf-8"` 추가 (읽기 6곳, 쓰기 7곳, 테스트 1곳)
- [x] `.env.example`의 `LOCAL_MODEL_NAME`을 `Qwen/Qwen3-1.7B`로
- [x] `scripts/setup.ps1`·`setup.sh`: Python 3.11/3.12 확인 → `.venv` → 패키지 → 프론트 패키지 → `.env` → 데이터·모델 확인. NVIDIA 그래픽카드가 있는데 torch가 CPU용이면 GPU용 설치 명령을 안내
- [x] `scripts/run.ps1`·`run.sh`: 같은 포트의 이전 프로세스 확인 → API 서버 + 프론트 동시 실행
- [x] `app/config.py`: Windows에서 출력을 파일로 보낼 때 이모지 때문에 멈추지 않게 처리
- [x] `README.md`, `CONTRIBUTING.md` 갱신 (스크립트 사용법, Python 버전)

**검증:**
- [x] `python -m pytest -q` 통과 (고치기 전에는 Windows에서 17개 중 10개 실패)
- [x] Windows에서 `setup.ps1`을 실제로 실행해 끝까지 통과 (Python 3.12)
- [x] `.sh` 스크립트 문법 확인 (`bash -n`)
- [x] Windows에서 `run.ps1`로 MariaDB·API 서버·화면을 띄움 (API는 `127.0.0.1:8000`에만 열림)
- [x] DB 포트가 `127.0.0.1:3379`에만 열렸는지 확인 (`docker ps`)

**팀원 확인:**
- [ ] macOS 사용 팀원이 `setup.sh` → `run.sh` 실행 확인 완료
- [ ] Docker가 있는 팀원이 DB 포트 확인 완료
- **확인일:** 

### Task 3: 챗봇 — 예측·권고·법규 (F-25 1차, F-24 `chat_log`)

**구현:**
- [x] `app/core/prompts.py`: `rag.py`의 시스템 프롬프트를 옮김
- [x] `app/core/agent.py`: 의도 분류(키워드) → 값 뽑기(노선·날짜·시간·간격) → 되묻기 → 기능 호출 → 문장 틀 답변 → 숫자 검증
- [x] 이전 대화의 값과 의도를 이어 씀 ("그럼 9시는?")
- [x] `ChatRequest`·`ChatResponse`, `POST /{domain}/chat` (`response_model`, `run_in_threadpool`)
- [x] `ChatLog` 테이블, `save_chat` (DB가 꺼져 있으면 `db_saved: false`)
- [x] `tests/test_chat.py` 13개

**검증:**
- [x] `python -m pytest -q` 통과
- [x] 실데이터로 서버를 띄워 호출: 160번 15시 답변(1,509명, 혼잡도 0.83)이 `/predict` 응답과 같음
- [x] DB 없이 `db_saved: false`로 답이 그대로 나옴
- [x] DB를 켠 상태에서 질문 3건 → `chat_log`에 3줄 (의도·부른 기능·숫자 검증 결과 저장), 새 테이블 2개 자동 생성

**팀원 확인:**
- [ ] 확인 완료
- **확인일:** 

### Task 4: 챗봇 대화 화면 (F-27)

**구현:**
- [x] `ChatPanel.vue`: 대화 목록, 입력창, 답변 아래에 부른 기능·숫자 검증 결과·근거(`SourceList` 재사용)
- [x] 로딩·에러·빈 상태, 응답은 `?.`·`?? []`로 읽기, 답변은 텍스트로만 그림
- [x] `layout.js`에 `chat` 패널과 '상담' 화면 배치
- [x] 답변에 이모지 표시(🚌 🟢🟡🟠🔴 ⬆️ 👉), 답변 옆 버스 아이콘(직접 그린 SVG)
- [x] 지도 노선 번호표에 버스 아이콘

**검증:**
- [x] `npm run build` 성공
- [x] 화면에서 질문 → 답변 → 근거·부른 기능 표시 확인
- [x] 화면을 연 뒤 백엔드를 끄고 질문 → 채팅에 에러 상태 표시. 문구는 "status code 502"로 불친절한데, PR #1이 같은 함수(`api.js`의 `errorMessage`)를 읽기 쉬운 문구로 이미 고쳐서 중복 수정하지 않음

**팀원 확인:**
- [ ] 확인 완료
- **확인일:** 

### Task 5: 법규 답변 정확도 개선 (F-26, 기존 20문항 기준)

**구현:**
- [x] 기준선 재현 → `eval/results/5_baseline_recheck_qwen1.7b.json`
- [x] 후보 1 — 프롬프트 규칙 추가 ("근거에 답이 있으면 반드시 답할 것", "인가와 신고를 구분", "조건·비율·횟수를 빠짐없이")
- [x] 후보 1-b — 거절 표현 인식 보강 (`NOT_FOUND_HINTS`에 "발표되지 않", "답변할 수 없", "답할 수 없")
- [ ] 후보 2 — 근거 전달 조정 (남은 2문항이 조건 누락). 같은 20문항을 이미 세 번 채점해서, 더 맞추면 이 문항에만 맞는 답이 될 수 있음 → 김주혜의 새 문항이 온 뒤에 진행
- [ ] 후보 3 — 기존 LoRA 어댑터 비교 (예전 결과만 있음: 6/12)

| 단계 | 답변 정확 | 거절 정확 | 환각 | 판정 |
|---|---|---|---|---|
| 5. 기준선 재현 (1.7B) | 6/12 | 8/8 | 0 | README 2단계와 같음 |
| 6. 후보 1: 프롬프트 규칙 | 10/12 | 7/8 | **1** | 불합격 — 거절 문항 R7이 다른 말로 거절해 환각으로 채점됨 |
| 7. 6 + 거절 표현 보강 | **10/12** | **8/8** | **0** | **채택** |
| (참고) 3. Qwen3-4B | 10/12 | 8/8 | 0 | 모델을 키우지 않고 같은 점수 |

남은 오답 2건: A1(신고라고 답했지만 "10퍼센트 이내"를 빠뜨림), A2(10퍼센트는 답했지만 토요일·방학 조건의 숫자를 빠뜨림).

**검증:**
- [x] 환각 0건 유지
- [x] 거절 정답 8/8 유지
- [x] 정답 수가 기준선(6/12)보다 많음 (10/12)
- [x] `python -m pytest -q` 통과

**팀원 확인:**
- [ ] 김주혜 확인 완료 (채점 결과)
- **확인일:** 

### Task 6: 챗봇 답변 다듬기 — LLM 문장 + 숫자 검증 (F-25 2차)

**구현:**
- [x] `prompts.py`에 챗봇 답변 프롬프트 (역할 → 맥락 → 형식 → 금지 규칙, 기능 결과는 데이터로 분리)
- [x] `agent.py`의 `_rewrite`: LLM이 문장 틀 답변을 다시 쓰고, 숫자가 다르면 버림. LLM이 실패해도 문장 틀로 계속
- [x] `use_llm: false`(기본)면 문장 틀만
- [x] 테스트 3개: 숫자가 같으면 채택 / 지어낸 숫자는 버림 / LLM 실패 시 문장 틀
- [x] 뜻 확인 추가(`keeps_meaning`): 권고 방향(유지·증차·감차)과 혼잡 단계가 원래 답과 다르면 버림. 프롬프트에도 "결론을 바꾸지 말 것" 규칙 추가

**검증:**
- [x] `python -m pytest -q` 통과
- [x] 실제 1.7B(GPU)로 12개 질문 → `eval/results/chat_llm_rewrite_1.json`: 9개 채택, 3개 버림, 숫자 오류 0건, 답변당 약 1.4초(첫 답은 모델 로드로 6~15초)
- [x] 숫자 검증만 있을 때는 12개가 모두 통과했지만, 그중 1건이 권고 '현행 유지'를 "감차해도 돼요"로 바꾼 답이었음 → 뜻 확인을 넣은 뒤 버려짐
- 한계: 채택된 답은 원래 답보다 짧아져 근거(배차간격·혼잡 구간)가 빠지는 경우가 많음. 기본값은 계속 문장 틀(`use_llm: false`)

**팀원 확인:**
- [ ] 확인 완료
- **확인일:** 

### Task 7: 시뮬레이션·최적화 연결 + `optimization_run` (F-25 3차, F-24) — PR #1 머지 후

**구현:**
- [x] `OptimizationRun` 테이블, `save_optimization_run`, `list_optimization_runs` (호출하는 곳은 아직 없음)
- [x] 챗봇이 시뮬레이션·최적화 질문을 알아보고 값(간격, 시간)을 뽑음. 지금은 "아직 연결되지 않았습니다"로 답함
- [ ] `dev` 최신을 받고 `pip install -r requirements.txt`(`ortools`), `python scripts/calibrate_p90.py`, 새 데이터 스냅샷 적용
- [ ] `agent.py`에 `_simulate`·`_optimize` 핸들러 (어댑터에 `simulate()`·`optimize()` 메서드가 필요 → [박성훈용 제안](park-next.md) P-1)
- [ ] `/api/bus/optimize` 호출 뒤 `save_optimization_run` 연결 — 박성훈 라우터를 건드리므로 사전 협의
- [ ] 머지 충돌 정리: `adapter.py`, `schemas.py`, `layout.js`, `README.md`, `config.py`

**검증:**
- [ ] `python -m pytest -q` 통과 (성훈 테스트 포함)
- [ ] `ortools` 설치 뒤 로컬 LLM이 그대로 뜨는지 확인 (`protobuf` 6.x로 내려감)
- [ ] 최적화 1회 실행 → `optimization_run`에 한 줄

**팀원 확인:**
- [ ] 박성훈 확인 완료
- **확인일:** 

### Task 8: 최종 채점 — 김주혜 문제집 도착 후

**구현:**
- [x] `scripts/eval_agent.py`: 문제집을 읽어 기능 선택 정확도 계산 → `eval/results/`
- [x] 내가 만든 15문항(`eval/agent_eval.sample.json`)으로 스크립트 확인: 15/15
- [ ] 김주혜의 `eval/agent_eval.json`(F-33)으로 채점, 놓친 표현을 키워드에 추가
- [ ] F-32 40문항으로 법규 채점 (Task 5 채택안 전·후)
- [ ] `role` 값이 확정되면 `chat`에서 법규 검색으로 전달, F-37 코드 리뷰

**검증:**
- [ ] 챗봇 기능 선택 정확도 90% 이상 (샘플 15문항은 내가 만든 것이라 합격 근거로 쓰지 않음)
- [ ] 40문항에서 환각 0건, 정답 수 증가, 거절 정답 수 유지

**팀원 확인:**
- [ ] 김주혜 확인 완료
- **확인일:** 

### Task 9: 버스 길안내 (F-28, 추가 기능)

**구현:**
- [x] `app/domains/bus/guide.py`: 가까운 정류장 찾기 → 직행·환승 한 번 경로 → 탈 구간의 혼잡도·배차간격·대략의 시간 → 가장 빠른 경로와 덜 붐비는 경로 중 추천
- [x] `POST /api/bus/guide`, `GET /api/bus/stops/search` (`response_model`, `limit` 상한)
- [x] 챗봇 `guide` 의도: "A에서 B 어떻게 가?", "여기서 B 가는 법"(현재 위치), 출발·도착이 빠지면 되묻기
- [x] 화면: 채팅의 '현재 위치 사용' 체크(브라우저 GPS), 답변 아래 '지도로 보기' → 팝업(`GuideModal`)에 경로 점선·승차·환승·도착 표시와 경로 카드
- [x] `tests/test_guide.py` 12개

**검증:**
- [x] `python -m pytest -q` 통과 (전체 42개)
- [x] 실데이터로 확인: 정릉우체국앞 → 종로3가역 직행(143번, 정류장 11개, 약 27분), 없는 정류장("강남역")은 이유와 함께 거절, 멀리 떨어진 위치는 가장 가까운 정류장으로 안내하고 거리 표시
- [x] 화면에서 질문 → 설명 → '지도로 보기' 팝업 확인
- [x] 임시 프로필의 Chrome으로 위치 권한 허용·거부를 각각 확인: 허용이면 가까운 정류장을 찾아 안내하고 팝업에 현재 위치 표시, 거부면 이유를 화면에 보이고 체크가 꺼짐 (위치 좌표는 종각역 근처로 넣어 줌. 실제 GPS 수신은 확인하지 않음)
- [x] "여기서 ○○ 어떻게 가?"를 위치를 켜지 않고 물어도 화면이 위치를 얻어 다시 물음
- [x] 날짜를 말하지 않아 오늘(예측 범위 밖)이 된 경우, 가장 가까운 같은 요일로 계산하고 답변에 그 사실을 적음

**팀원 확인:**
- [ ] 팀 기능 목록에 F-28을 넣는 데 동의 (`features.md`에 행 추가함)
- **확인일:** 

---

### Task 9-1: 길안내를 서울 버스 전체 노선으로 (F-28 확장)

**구현:**
- [x] `scripts/fetch_network_stops.py`: 전체 노선의 정류장 목록 수집 → `data/interim/network/` (API 키 필요, 약 720번 호출, 이어 받기 가능)
- [x] `guide.py`를 색인 방식으로 다시 씀: 정류장 → 지나는 노선, 격자로 가까운 정류장 찾기. 환승은 출발 노선이 지나는 정류장(과 150m 이내 정류장)에서 도착지를 지나는 노선으로 갈아탈 수 있는지 확인
- [x] 이름으로 찾은 정류장 주변 300m도 타고 내릴 곳으로 봄. 답변에 "걸어서 약 Nm", "내려서 약 Nm 걷기" 표시
- [x] 예측하지 않는 노선: 노선 기본정보의 평일 배차간격·운행시간으로 계산, 첫차·막차 밖의 시간대는 제외, 혼잡도는 "예측 없음"
- [x] 팝업 지도는 서버가 준 정류장 좌표(`legs[].path`)로 경로를 그림
- [x] 노선 정보(F-29)도 전체 노선으로: "402번 버스 노선 알려줘"
- [x] 테스트 2개 추가 (가상 노선으로 예측 없는 노선 환승, 운행하지 않는 시간대 제외)

**검증:**
- [x] `python -m pytest -q` 통과 (전체 51개)
- [x] 실데이터: 가락시장 → 서울역(345번 → 472번 환승, 약 82분), 강남역 근처 위치 → 서울역(140번 → 9401번, 약 39분), 잠실역 → 김포공항. 색인 만드는 데 0.4초, 질문당 0.2~1초
- [x] 화면 팝업에 승차·환승·승차·도착 표시
- 한계: 소요 시간이 실제보다 길게 나옵니다(한 바퀴 운행시간을 정류장 수로 나눈 어림값). 지하철이 빠른 구간도 버스로만 안내합니다. 경로 품질을 실제 길찾기 서비스와 비교해 보지는 않았습니다.

**팀원 확인:**
- [ ] 팀원 PC에서 `fetch_network_stops.py` 실행 확인 (각자 API 키 필요)

### Task 10: 노선 정보 안내 (F-29, 추가 기능)

"160번 버스 노선 알려줘"에 답하지 못해 추가했습니다.

**구현:**
- [x] `BusAdapter.route_info()`, `GET /api/bus/routes/{route}/info` (`response_model`). 노선 기본 정보는 `meta`와 같은 함수(`_route_row`)를 씀
- [x] 챗봇 `route_info` 의도: "노선 알려줘", "첫차 몇 시야?", "어디서 어디까지 가?", "배차간격은?" 등 (노선번호를 말했을 때)
- [x] 답변 아래 '대시보드에서 이 노선 보기'
- [x] 테스트 3개, 연습용 채점 문항 3개 추가 (18/18)

**검증:**
- [x] `python -m pytest -q` 통과 (전체 48개)
- [x] 실데이터로 확인: 160번(도봉산역광역환승센터 ↔ 온수동종점, 첫차 03:57, 막차 22:10, 평일 배차간격 8분)
- [x] "143번 노선 변경 절차 알려줘"는 규정 질문으로, "160번 정류장별 승객"은 예측으로 가는지 확인

**팀원 확인:**
- [ ] 확인 완료

---

## 남은 문제 · 다음 단계

- **PR** — #3(`main → dev`)을 먼저, 그다음 #4(`feat/oh-assistant → dev`). 둘 다 리뷰 대기 중입니다.
- **박성훈 PR #1과의 충돌** — 같은 파일(`adapter.py`, `bus/schemas.py`, `layout.js`, `README.md`, `config.py`)을 고쳤습니다. 내용은 겹치지 않지만 머지할 때 손으로 맞춰야 합니다.
- **로컬 데이터가 예전 스냅샷** — 예측 범위가 11/15까지라 "오늘·내일" 질문은 범위 밖이라고 답합니다. 새 스냅샷(`data-snapshot-20261113`)을 받으면 해결됩니다.
- **길안내의 한계** — 버스만(지하철 제외), 환승 한 번까지, 소요 시간은 어림값, 혼잡도는 5개 노선만. 전체 노선 파일(`data/interim/network/`)은 커밋 제외라 팀원마다 스크립트를 한 번 돌려야 하고, 없으면 5개 노선만 안내합니다.
- **키워드 규칙의 한계** — 표현이 다양하면 놓칩니다. 채점 문제집과 화면 사용에서 나온 표현만 추가합니다.
- **법규 20문항을 여러 번 채점함** — 보고서에는 김주혜의 40문항 결과를 최종값으로 씁니다.
- **메모리** — 법규 채점과 API 서버를 같이 띄우면 서버가 죽습니다. 채점할 때는 서버를 끕니다.
- **새 데이터 스냅샷은 아직 받지 않음** — `data-snapshot-20261113`은 PR #1 이후 코드와 함께 써야 하고, 지금 코드에 풀면 혼잡도가 틀리게 나옵니다(릴리스 설명). PR #1을 받을 때 함께 바꿉니다.
- **확인하지 못한 것** — macOS 실행, 실제 GPS 수신(좌표를 넣어 준 상태로만 확인).
