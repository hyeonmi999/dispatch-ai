# AGENTS.md — dispatch-ai 작업 지침 (AI 코딩 도구 공통)

이 저장소에서 일하는 **모든 AI 코딩 도구**(Claude Code, Cursor, Codex, Gemini CLI, Copilot 등)가 작업 전에 읽는 지침입니다.
사람이 지키는 팀 룰의 원문은 [CONTRIBUTING.md](CONTRIBUTING.md)이고, 이 파일은 "무엇을 어떤 순서로 읽고, 어떻게 끝내는지"를 정합니다.
두 문서가 다르면 CONTRIBUTING.md가 맞습니다. 룰을 바꾸려면 PR로 고칩니다.

---

## 0. 작업을 시작하기 전에 (필수)

아래를 **이 순서로 읽은 뒤** 코드를 고칩니다. 읽지 않고 시작하지 않습니다.

1. **이 파일 전체**
2. **[CONTRIBUTING.md](CONTRIBUTING.md)** — 팀 개발 룰 10개 장 (작업 흐름·코딩·API·DB·ML 실험·LLM·테스트·보안·환경·리뷰)
3. **[docs/contracts.md](docs/contracts.md)** — 기능끼리 주고받는 형식. 남이 쓰는 API·스키마를 건드리기 전에 확인
4. **담당자의 계획서** — `docs/plans/`에서 지금 할 Task를 찾음
   - 오정훈: [oh-assistant.md](docs/plans/oh-assistant.md) · 박성훈: [park-next.md](docs/plans/park-next.md) · 김주혜: [kim-next.md](docs/plans/kim-next.md)
5. **[docs/lessons.md](docs/lessons.md)** — 작업 영역과 같은 분류의 "방지 규칙"
6. **[docs/worklog/](docs/worklog/)** — 담당자 파일의 최근 기록 2~3건 (어디까지 했고 무엇이 남았는지)

그다음 사용자에게 아래를 확인합니다. 이미 대화에서 알 수 있으면 묻지 않습니다.

- **누구의 작업인가** (오정훈 / 박성훈 / 김주혜) → 브랜치 이름, 커밋 메시지의 이름, 작업 기록 파일이 정해집니다.
- **어느 Task인가** → 계획서의 Task 번호. 계획서에 없는 일이면 먼저 계획서에 Task를 추가합니다(하루 이상 걸리는 일).

## 1. 프로젝트 한눈에

- 서울 시내버스 배차 AI: 승객 수 예측(XGBoost) → 혼잡도 → 배차 권고·최적화 → 규정 검색(RAG) 근거 → 챗봇·길안내. 모든 권고는 `needs_review=true`.
- 스택: Python 3.11 또는 3.12 · FastAPI · SQLAlchemy async + MariaDB(Docker, 로컬) · FAISS + bge-m3 · Qwen3-1.7B(로컬) / OpenAI(선택) · Vue 3 + Vite(JS) · pytest
- **로컬 전용**: 배포·인증·외부 공개를 전제로 한 작업을 하지 않습니다.
- 구조와 실행 방법: [README.md](README.md) · 기능 목록: [docs/guides/features.md](docs/guides/features.md)

| 담당 | 영역 | 브랜치 |
|---|---|---|
| 오정훈 | 챗봇·길안내·노선 정보, 설치·실행, 이력 DB, 법규 정확도 | `feat/oh-<기능>` |
| 박성훈 | 예측 상한, 시뮬레이션, 전 노선 최적화, 성능 비교 | `feat/park-<기능>` |
| 김주혜 | 법령 문서, 채점 문제집, 문서 관리·열람 등급, 시연·보고서 | `feat/kim-<기능>` |

## 2. 설치·실행·검증 명령

```bash
# 설치 (처음 한 번, 여러 번 실행해도 됨). 가상환경 → 패키지 → 프론트 패키지 → .env → 데이터 확인 → 길안내용 전체 노선
bash scripts/setup.sh                 # macOS
.\scripts\setup.ps1                   # Windows PowerShell

# 실행: MariaDB → API(127.0.0.1:8000) → 화면(localhost:5173)
bash scripts/run.sh                   # macOS
.\scripts\run.ps1                     # Windows

# 검증
python -m pytest -q                                   # 합성 데이터 테스트 (LLM·외부 연결 없이 돎)
cd frontend && npm run build                          # 프론트 빌드
python scripts/eval_rag.py --name <이름>               # 법규 질의응답 채점 → eval/results/
python scripts/eval_agent.py --name <이름>             # 챗봇 기능 선택 채점 → eval/results/

# 데이터 (필요할 때)
python scripts/fetch_network_stops.py                 # 길안내용 서울 버스 전체 노선 정류장·시간표 (API 키 필요, 약 5분)
python scripts/calibrate_p90.py                       # 배차 최적화용 승객 수 상한 비율표 (PR #1 이후)
```

- 가상환경의 파이썬을 씁니다: Windows `.venv\Scripts\python`, macOS `.venv/bin/python`.
- **채점·학습 스크립트를 돌릴 때는 API 서버를 끕니다** (같이 띄우면 메모리가 모자라 서버가 죽음).
- 길안내용 전체 노선 파일(`data/interim/network/`)은 커밋 제외라 PC마다 한 번 받아야 합니다. `.env`의 `DATA_GO_KR_API_KEY`(공공데이터포털 「서울특별시_버스노선정보조회」, 디코딩 키)가 있으면 `setup` 스크립트가 알아서 받습니다. 없으면 길안내가 5개 노선만 압니다.

## 3. 반드시 지키는 룰

원문은 [CONTRIBUTING.md](CONTRIBUTING.md)입니다. 아래는 AI가 가장 자주 어기는 것만 추린 것이고, 나머지 룰도 모두 적용됩니다.

### 3-1. 커밋 룰

- **커밋·푸시는 사용자가 지시할 때만** 합니다.
- 메시지 형식: `YYYY-MM-DD-이름 : 대주제 - 소주제 - 간단한 내용`
  - 날짜는 커밋한 날, 이름은 실명(오정훈·박성훈·김주혜). `:`과 `-`의 앞뒤는 한 칸씩 띄웁니다.
  - 대주제: 룰 · 백엔드 · 프론트 · DB · 데이터 · 모델 · RAG · 테스트 · 환경 · 문서
  - 예: `2026-11-17-오정훈 : 백엔드 - 길안내 - 탑승 시간을 정류장별 공식 시간표와 도로 거리로 계산`
- `git add .` 금지. `git status`로 확인하고 **이번 작업에서 바꾼 파일만 이름을 적어** 스테이징합니다. 다른 사람의 미커밋 변경이 보이면 손대지 말고 알립니다.
- 한 커밋에는 한 가지 주제만 담습니다 (기능 / 화면 / 테스트·채점 / 문서를 나눔).
- `.env`, `data/`, `models/`, `vectorstore/`, `hf_models/`는 커밋하지 않습니다.

### 3-2. 브랜치 · PR 룰

- `main`은 PR 머지로만 바뀝니다. 작업은 `feat/<이름>-<기능>`에서 하고 **`dev`로 PR**을 올립니다. 공유 브랜치에 force push 하지 않습니다.
- PR은 [템플릿](.github/pull_request_template.md)을 채웁니다. 최소 1명 리뷰. CRITICAL·HIGH 지적이 남아 있으면 머지하지 않습니다.
- 받은 뒤에는 다시 설치합니다: `pip install -r requirements.txt`, `cd frontend && npm install`.

### 3-3. 개발 룰 (핵심)

- **요청 범위만** 고칩니다. 관련 없는 리팩터링·의존성 추가·파일 삭제를 하지 않습니다.
- **중복 금지 · 계층 지키기**: 라우터(`app/api/routes.py`)에 로직을 두지 않습니다. 계산은 `app/domains/bus/`, 공통 처리는 `app/core/`, DB는 `app/db/repository.py`. 새로 만들기 전에 비슷한 것이 있는지 먼저 찾습니다.
- **설정값은 `app/config.py` 한 곳**: 코드 중간에 숫자·문자열을 직접 쓰지 않습니다.
- **시그니처·스키마를 바꾸기 전에 모든 호출처를 검색**합니다 (`app/`, `scripts/`, `tests/`, `frontend/src/api.js`). 응답 필드를 바꾸면 `docs/contracts.md`와 화면을 같은 PR에서 고칩니다.
- **API**: 새 엔드포인트는 `response_model`, 에러는 `{"status", "detail"}` 형식, 목록은 `limit` 상한. 권고·최적화 응답에는 `needs_review: true`.
- **DB**: 값은 바인딩으로만 넣습니다. 모델은 `app/db/database.py` 한 곳. **기존 테이블의 컬럼을 바꾸면** PR에 "로컬 DB 재생성 필요"를 적습니다.
- **테이블·컬럼·파일 이름은 실제 정의를 읽고** 씁니다. 짐작으로 만들지 않습니다.
- **ML**: 시간 순서로만 나누고, 예측 시점(실측 마지막 날 + 21일)에 알 수 없는 값을 쓰지 않습니다. 새 방식은 항상 베이스라인과 같은 행에서 비교합니다.
- **LLM · RAG**: 숫자는 기능 결과에서만, 근거가 없으면 "찾을 수 없음". 프롬프트는 `app/core/prompts.py` 한 곳. 프롬프트·모델·검색 방식을 바꾸면 평가셋을 다시 돌려 `eval/results/`에 남깁니다.
- **화면**: API 호출은 `frontend/src/api.js`에만. 로딩·에러·빈 상태를 모두 표시. `v-html` 금지.
- **Windows · macOS**: 파일 입출력에 `encoding="utf-8"`, 경로는 `pathlib`, 스크립트는 `.sh`와 `.ps1`을 쌍으로.
- **비밀값 금지**: `.env`의 값을 코드·로그·커밋·외부 요청·대화 화면에 넣지 않습니다. 새 변수는 `.env.example`에 이름만 추가합니다.

### 3-4. AI가 일하는 방식

- **"완료"는 검증 후에만** 말합니다: pytest 통과, 서버 기동, 바꾼 API·화면을 한 번 직접 호출한 결과까지. 실패는 실패로, 확인하지 못한 것은 못 했다고 그대로 보고합니다.
- **결정적 작업은 코드로**: 집계·검증·비교는 스크립트로 돌리고 결과를 파일로 남깁니다. 수치를 추측해서 쓰지 않습니다.
- **어림값을 쓰기 전에 원천 데이터를 끝까지 확인**합니다. 어림값을 쓰게 되면 믿을 만한 기준과 비교한 수치를 남기고, 가정한 값은 가정이라고 적습니다.
- **계획과 다른 문제를 발견하면 멈추고 보고**합니다. 임의로 설계를 바꾸지 않습니다.
- **버그를 고치면 재현 테스트부터** 추가하고 `docs/lessons.md`에 한 줄 남깁니다.

## 4. 작업을 끝낼 때 (필수)

커밋하기 전에 아래를 모두 합니다.

1. **검증**: `python -m pytest -q`, 화면을 건드렸으면 `npm run build`, 바꾼 API·화면을 직접 한 번 호출
2. **계획서 갱신**: `docs/plans/<계획서>.md`에서 끝낸 항목을 `[x]`로, 검증 결과(숫자)를 적고, 새로 알게 된 한계를 "남은 문제"에 추가
3. **교훈 기록**: 버그를 고쳤거나 오래 헤맸으면 `docs/lessons.md`에 `증상 / 원인 / 방지 규칙` 한 줄
4. **규격 갱신**: API·스키마가 바뀌었으면 `docs/contracts.md`
5. **작업 기록 작성**: `docs/worklog/<이름>.md` 맨 위에 이번 작업을 추가 (형식은 [docs/worklog/README.md](docs/worklog/README.md))
6. **보고**: 무엇을 했고, 어떻게 확인했고, 무엇을 확인하지 못했는지 사용자에게 알림
7. 사용자가 커밋을 지시하면 3-1의 형식으로 커밋. 작업 기록 파일도 같은 PR에 넣습니다.

## 5. 문서 위치

| 문서 | 위치 | 언제 |
|---|---|---|
| 팀 개발 룰 (원문) | [CONTRIBUTING.md](CONTRIBUTING.md) | 코딩·리뷰 기준 |
| 기능 규격 | [docs/contracts.md](docs/contracts.md) | 남이 쓰는 API·형식을 볼 때, 바꿀 때 |
| 계획서 | `docs/plans/` ([템플릿](docs/plans/_TEMPLATE.md)) | 작업 시작·종료 |
| 작업 기록 | `docs/worklog/<이름>.md` | 작업 종료 때마다 |
| 교훈 | [docs/lessons.md](docs/lessons.md) | 시작 전에 읽고, 버그를 고치면 추가 |
| 설계 결정 | `docs/decisions.md` (PR #1 이후) | 알고리즘·설정을 고를 때 |
| 기능 목록 · 담당 가이드 | [docs/guides/](docs/guides/) | 기능 ID, 담당 범위 |
| 평가 결과 | `eval/results/` | 채점·실험 결과 |
