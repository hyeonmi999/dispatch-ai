# 🚌 버스 배차 최적화 도우미 — 서비스·물류 배차 AI

> AI서비스사례연구 기말 프로젝트 · 조 **Oh!gimbak**
>
> 수요(승객 수)를 예측하고, 규정 문서(RAG)를 근거로 배차 간격·증감차를 **권고**합니다.
> AI는 보조 역할이며 **모든 권고는 `needs_review=true`** — 최종 결정은 배차 담당자가 합니다.

- **공통 아키텍처**: 수요 예측(Forecaster) · 규정 지식베이스(RAG) · 배차 권고(Advisor) + 도메인 어댑터 레지스트리
- **1차 스프린트 (구현)**: 서울 시내버스 5개 노선 (143·272·160·150·271), 2025-09-29 ~ 2026-10-18 실데이터
- **2차 스프린트 (인터페이스만)**: 물류(화물 차량 배차) · 서비스 기사 출동 배차 → API가 `501 not_implemented` 반환

---

## 1. 아키텍처

```mermaid
flowchart LR
    subgraph FE["Vue 3 + Vite 대시보드 (Naive UI · ECharts · Leaflet)"]
        MAP["서울 지도 노선도<br/>구간별 혼잡도·혼잡 지점"]
        CHART["24시간 실제 vs 모델 vs 베이스라인<br/>정류장 구간별 혼잡도"]
        CARD["배차 권고 카드<br/>근거·신뢰도·승인/보류"]
        QA["규정 Q&A"]
    end

    subgraph API["FastAPI  /api/{domain}/predict · ask · advise"]
        REG["DomainRegistry"]
        subgraph CORE["공통 계층 (도메인 무관)"]
            FC["Forecaster<br/>학습·평가(MAE vs 베이스라인)"]
            KB["KnowledgeBase (RAG)<br/>조항 단위 chunk · FAISS<br/>임계값 미만 → 찾을 수 없음"]
            ADV["Advisor<br/>예측 + 규정 근거<br/>confidence · needs_review"]
            LLM["LLM 선택<br/>Qwen3-1.7B(MPS) / gpt-4o-mini"]
        end
        subgraph DOM["도메인 어댑터"]
            BUS["bus ✅<br/>XGBoost · 혼잡도 · 권고 규칙"]
            LOG["logistics ⏳ 501"]
            FS["field_service ⏳ 501"]
        end
    end

    subgraph DATA["데이터"]
        SEOUL["서울 열린데이터광장<br/>OA-21219 승차 · OA-21220 운행횟수<br/>OA-21218 구간 재차인원"]
        KMA["기상청 ASOS 서울(108)"]
        BUSAPI["서울 버스노선정보 API<br/>노선 경로·정류소 좌표"]
        DOCS["docs/bus<br/>여객자동차법·시행령·시행규칙<br/>서울시 조례 · 보도자료"]
    end

    DB[("MariaDB (Docker)<br/>prediction_log · advisory_log · qa_log")]

    FE -->|/api 프록시| API
    REG --> BUS & LOG & FS
    BUS --> FC & ADV
    ADV --> KB --> LLM
    SEOUL & KMA --> FC
    BUSAPI --> MAP
    DOCS --> KB
    API --> DB
```

### 요청 흐름 — `/api/bus/advise`
1. 저장된 예측(store)에서 노선·날짜·시간의 정류장별 예상 승차를 조회
2. **승차 → 구간 재차인원 환산**: 최근 8주 구간 데이터(OA-21218)로 만든 비율 × 예측 노선 승차
3. **혼잡도 = 버스 1대당 재차인원 ÷ 정원(55명)** → 여유 < 0.45 ≤ 보통 < 0.8 ≤ 혼잡 < 1.0 ≤ 매우혼잡
4. 최대 혼잡 구간 기준 권고: ≥ 0.8 증차(간격 단축, 목표 혼잡도 0.7) / < 0.25 감차 검토 / 그 외 유지
   → 노선 기본정보의 **최소·최대 배차간격 범위 안으로 제한**, 이미 최소 간격이면 "별도 대책(구간 맞춤버스 등)"
5. 권고 유형에 맞는 질문으로 **/ask와 같은 RAG 경로**를 호출해 규정 근거(문서명·조항) 첨부
6. 신뢰도(노선별 검증 오차 기반, 근거 없으면 감점) + `needs_review=true` → MariaDB 저장 → 담당자 승인/보류/반려

### 새 도메인 추가 방법 (공통 계층 수정 없음)
1. `app/domains/<name>/adapter.py`에 `DomainAdapter` 상속 클래스 작성 (`predict`, `advise`, 요청 스키마)
2. `app/domains/__init__.py`의 `register_all()`에 한 줄 등록
3. `docs/<name>/`에 규정 문서 추가 → `/api/<name>/ask` 자동 동작

---

## 2. 폴더 구조

```
ai서비스 기말과제/
├── app/                        FastAPI 백엔드
│   ├── main.py                 앱 생성·예외(501/400)·라우터 등록
│   ├── config.py               경로·모델명·임계값 (.env로 덮어쓰기)
│   ├── api/routes.py           공통 API: /api/{domain}/predict·ask·advise, 이력·검토
│   ├── core/                   ── 공통 계층 (도메인 무관) ──
│   │   ├── domain.py           DomainAdapter 인터페이스, NotImplementedDomain
│   │   ├── registry.py         도메인 레지스트리
│   │   ├── forecaster.py       Forecaster 인터페이스, MAE·개선율 계산
│   │   ├── rag.py              KnowledgeBase (문서 로드·조항 분할·FAISS·근거 기반 답변)
│   │   ├── llm.py              Qwen3(로컬) / OpenAI 선택
│   │   ├── advisor.py          권고 공통 처리 (근거·신뢰도·needs_review)
│   │   └── schemas.py
│   ├── domains/
│   │   ├── bus/                ── 1차 스프린트 ──
│   │   │   ├── data.py         wide→long 변환, 노선정보·좌표 로드
│   │   │   ├── features.py     달력·공휴일·방학·날씨·lag 피처
│   │   │   ├── forecaster.py   XGBoost 학습·시간순 분할·배치 추론
│   │   │   ├── advisor.py      혼잡도·권고 규칙
│   │   │   └── adapter.py      버스 어댑터 + 지도용 API
│   │   ├── logistics/          인터페이스·스키마만 (501)
│   │   └── field_service/      인터페이스·스키마만 (501)
│   └── db/                     SQLAlchemy async 모델·저장소
├── scripts/                    데이터 수집·학습·인덱싱 CLI
├── frontend/                   Vue 3 + Vite 대시보드
├── docs/bus/                   RAG 규정 문서 (PDF·TXT)
├── docker/                     MariaDB (교수님 maria_db.yml 템플릿 기반)
├── tests/                      pytest (합성 데이터)
└── data/ models/ vectorstore/ hf_models/   ← 생성물, 커밋 제외
```

---

## 3. 실행 방법

### 3-0. 사전 준비
| 항목 | 비고 |
|---|---|
| Python 3.11 | `python3.11 --version` |
| Node.js 20+ | 프론트엔드 |
| Docker Desktop | MariaDB |
| `brew install libomp` | macOS에서 XGBoost 실행에 필요 |
| (선택) 공공데이터포털 인증키 | 실데이터 날씨·노선 좌표 수집 시 |

### 3-1. 설치
```bash
cd "ai서비스 기말과제"
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 키 입력 (OpenAI는 선택)
cd frontend && npm install && cd ..
```

### 3-2. 빠른 실행 — 합성 데이터만으로 (실데이터·API 키 없이)
```bash
python scripts/make_sample_data.py           # data/sample 생성 (실데이터와 같은 스키마 + 가상 노선 좌표)
python scripts/train_bus.py --mode sample    # 학습 → 모델/베이스라인 MAE·개선율 출력
python scripts/build_index.py                # 규정 문서 FAISS 인덱스 (bge-m3 최초 1회 다운로드 ~2.3GB)
```
`.env`에서 `DATA_MODE=sample`로 바꾼 뒤 아래 3-4로 서버를 띄웁니다. (실데이터가 없으면 `auto`도 sample로 동작)

### 3-3. 실데이터로 실행
1. **노선 기본정보 엑셀(수동)**: [서울 열린데이터광장](https://data.seoul.go.kr)에서 「서울시 버스노선 기본정보」, 「서울시 버스노선 ID 정보」를 받아 `data/raw/`에 저장
   - `서울시버스노선기본정보(20260902).xlsx`, `서울시버스노선ID정보(20260902).xlsx`
2. 나머지는 스크립트가 받습니다.
```bash
python scripts/download_seoul_bus.py         # OA-21219·21220 1년치 + OA-21218 8주치, 노선 필터링 후 원본 삭제 (~1.5GB 다운로드 → 18MB)
python scripts/fetch_weather.py --start 2025-09-01   # 기상청 ASOS 서울 시간자료 (DATA_GO_KR_API_KEY 필요)
python scripts/fetch_route_geo.py            # 노선 경로·정류소 좌표 (DATA_GO_KR_API_KEY 필요)
python scripts/train_bus.py --mode real      # 약 3분 (M 시리즈 기준)
python scripts/build_index.py
```

### 3-4. 서버 실행
```bash
# MariaDB (Docker Desktop 실행 후)
cp docker/.env.example docker/.env
docker compose -p dispatch_ai -f docker/maria_db.yml up -d

# 백엔드 (프로젝트 루트)
python -m uvicorn app.main:app --port 8000      # API 문서: http://localhost:8000/docs

# 프론트엔드 (새 터미널)
cd frontend && npm run dev                      # http://localhost:5173
```
- DB가 꺼져 있어도 API는 동작하고 응답에 `"db_saved": false`가 붙습니다.
- Qwen3-1.7B는 첫 질문 때 로드됩니다 (최초 다운로드 ~4GB, Apple Silicon MPS 사용). 이후 답변 10~20초.
- LLM을 `openai`로 고르면 `.env`의 `OPENAI_API_KEY`가 필요합니다. 없으면 400과 함께 안내 문구를 반환합니다.

### 3-5. 테스트
```bash
python -m pytest -q      # 합성 데이터 기준 11개: 시간순 분할·lag 누수·지표·API 계약·501·OpenAI 키 오류
```

---

## 4. 데이터

| 데이터 | 출처 | 사용 |
|---|---|---|
| OA-21219 정류장별 승차 승객수 (1일 1시간) | 서울 열린데이터광장 | **예측 대상 y** |
| OA-21220 정류장별 운행횟수 (1일 1시간) | 〃 | 피처(배차 계획) · 혼잡도 분모 |
| OA-21218 구간별 재차인원 (1일 1시간) | 〃 (최근 8주) | 승차 → 구간 재차인원 환산 비율 |
| 버스노선 기본정보 · 노선ID | 〃 (엑셀) | 기준 배차간격, 최소·최대 배차, 인가대수, 운행시간 |
| 기상청 ASOS 서울(108) 시간자료 | 공공데이터포털 API | 기온·강수·습도 피처 |
| 버스노선정보조회 (노선 경로·정류소) | 공공데이터포털 API (`ws.bus.go.kr`) | 지도 |
| 한국 공휴일 | `holidays` 패키지 | 공휴일·연휴 전날 피처 |

- **OA-12913**(월 단위 합계): 시간대 정보가 없어 학습에 사용하지 않음 (보조 확인용)
- **OA-21217**(구간 운행시간): 2025년 12월 이후 갱신이 멈춰 기간이 맞지 않아 제외
- **원천 결측일**: 승차 18일, 운행 24일이 원천 파일에 없음 (예: 2025-11-28 ~ 12-04) → 0이 아닌 결측으로 처리
- **공공데이터포털에서 신청한 서비스**: 서울특별시_버스노선정보조회, 서울특별시_버스위치정보조회(2차 스프린트 실시간용), 서울특별시_정류소정보조회, 기상청_지상(종관, ASOS) 시간자료 조회서비스

### RAG 문서 수집 (`docs/bus/`)
| 문서 | 받는 곳 |
|---|---|
| 여객자동차 운수사업법 · 시행령 · 시행규칙 | [국가법령정보센터](https://www.law.go.kr) → 법령 검색 → 저장(PDF) |
| 서울특별시 시내버스 준공영제 운영 조례, 재정지원 및 안전 운행기준 조례, 정류소 정비·관리 조례, 용산구 버스노선조정위원회 조례 | [자치법규정보시스템](https://www.elis.go.kr) → PDF |
| 서울시 교통 보도자료 8건 (다람쥐버스, 혼잡도 안내, 노선 조정, 집중배차 등) | `python scripts/fetch_press_docs.py` (교통분야 누리집·내 손안에 서울) |

- PDF는 텍스트 추출이 가능한 파일이어야 합니다 (스캔 이미지 X). 파일명이 답변의 **출처 문서명**으로 표시됩니다.
- 법령 PDF는 목차와 부칙을 제외하고 **조항(제N조) 단위**로 나눠 인덱싱합니다. `docs/`가 바뀌면 다음 질의 때 인덱스를 자동 재생성합니다.
- 서울시의 배차간격·증차 "내부 지침" 원문은 공개본이 없어, 권고 근거는 법령(사업계획 변경 기준)과 정책 사례(보도자료)로 제시합니다.

---

## 5. 수요 예측 모델

- **y**: 노선·정류장·시간대별 승차 승객 수 / **X**: 노선·정류장(범주형), 정류장 순서, 시간, 요일, 주말, 공휴일, 연휴 전날, 방학, 기온·강수·습도, 운행횟수, 7·14·21·28일 전 같은 시간 승객 수와 평균
- **모델**: XGBoost 단독 (`hist`, 범주형 직접 처리, early stopping)
- **시간 순서 분할 (무작위 분할 없음)**

| 구간 | 기간 | 용도 |
|---|---|---|
| train | 2025-09-29 ~ 2026-08-17 | 학습 |
| early stop | 2026-08-18 ~ 2026-08-31 | 트리 수 결정 (검증 구간은 보지 않음) |
| **valid** | **2026-09-01 ~ 2026-10-18** | 최종 평가 (추석 연휴 포함) |
| forecast | 2026-10-19 ~ 2026-10-25 | 향후 7일 예측 (1주 전 운행횟수를 배차 계획으로 가정) |

- 누수 방지: lag는 최소 7일 전 값만 사용, 학습 코드에서 `train.max < early.min ≤ early.max < valid.min`을 assert
- **베이스라인**: 전주 같은 요일·같은 시간대 승객 수

### 검증 결과 (실데이터, `models/bus/real/metrics.json`)
| 단위 | 모델 MAE | 베이스라인 MAE | 개선율 |
|---|---|---|---|
| 정류장·시간대 (n=526,802) | **4.48명** | 6.74명 | **33.5%** |
| 노선 전체·시간대 합계 (n=5,397) | **159.4명** | 321.6명 | **50.4%** |
| 평일 / 토요일 / 일·공휴일 (정류장·시간) | 4.91 / 4.20 / 3.58명 | 7.07 / 5.67 / 6.36명 | 30.6 / 25.9 / 43.7% |

중요 피처: 4주 평균 lag(0.65) > 공휴일(0.15) > 14일 lag > 운행횟수 — 공휴일(추석)에서 전주 값 베이스라인이 크게 틀리는 것을 모델이 보정합니다.

> 합성 데이터 결과(참고): 정류장·시간대 MAE 3.14 vs 5.54명 (개선 43.4%)

---

## 6. API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/domains` | 등록 도메인·상태 |
| GET | `/api/{domain}/schema` | 도메인별 요청 스키마 |
| POST | `/api/{domain}/predict` | 수요 예측 `{"route":"143","date":"2026-10-13","hour":8}` |
| POST | `/api/{domain}/ask` | 규정 질의응답 `{"question":"…","llm":"qwen","embedding":"bge"}` → 답변 + 출처(문서명·조항·쪽) / 근거 없으면 `found:false`, "찾을 수 없음" |
| POST | `/api/{domain}/advise` | 배차 권고 `{"route":"143","date":"2026-10-13","hour":7,"use_llm":true}` → 예상 승객·혼잡도·권고 간격·추가 차량·근거·신뢰도·`needs_review` |
| GET | `/api/advisories` | 권고 이력 (MariaDB) |
| POST | `/api/advisories/{id}/review` | 담당자 검토 `{"status":"approved|held|rejected"}` |
| GET | `/api/bus/meta`, `/api/bus/overview`, `/api/bus/routes/{route}/geometry·timeseries·stops` | 지도·차트용 |

`logistics`, `field_service`의 predict/advise(문서가 없을 때 ask 포함)는 `501 {"status":"not_implemented"}`를 반환합니다.

---

## 7. 프론트엔드 (Vue 3 + Vite)

화면 구성: 도메인 선택(물류·서비스 "준비 중") · LLM/임베딩 선택 · 날짜·시간 슬라이더(재생) · **서울 지도 노선도(구간별 혼잡도 색, 혼잡 지점 빨간 점)** · 노선 목록 · 모델 성능 카드 · 24시간 실제/모델/베이스라인 그래프 · 정류장 구간별 혼잡도 · 선택 노선 배차 현황·추가 배차 필요성 · 배차 권고 카드(근거·신뢰도·승인/보류/반려) · 규정 Q&A · 권고 이력

### UI 템플릿·라이브러리 선택 이유
조사한 후보 (2026-10 기준, GitHub API·npm 확인)

| 후보 | 라이선스 | 검토 결과 |
|---|---|---|
| vue-element-admin (★90k) | MIT | Vue 2 기반, 2024년 이후 갱신 정체 → 제외 |
| vue-vben-admin (★33k) | MIT | 모노레포·권한·다국어까지 포함한 대형 템플릿, 데모 규모에 과함 |
| vue-pure-admin (★20k) | MIT | Element Plus 기반 풀 어드민, 마찬가지로 과함 |
| soybean-admin (★15k) | MIT | **Naive UI 기반** 깔끔한 레이아웃 — 레이아웃 아이디어만 참고 |
| **Naive UI** 2.45 | **MIT** | ✅ 선택: Vue 3 전용, TypeScript, 트리셰이킹, 날짜·슬라이더·탭·접기 등 필요한 컴포넌트 완비, 테마 토큰으로 색 일관성 |
| **ECharts** 6 + vue-echarts 8 | **Apache-2.0 / MIT** | ✅ 차트: 툴팁·기준선(markLine)·SVG 렌더 |
| **Leaflet** 1.9 | **BSD-2-Clause** | ✅ 지도: 키 없이 폴리라인·마커 |
| Esri World Light Gray Canvas | Esri 이용약관 (출처 표기) | 배경지도. CARTO 무료 타일은 현재 API 키를 요구해 교체 |

→ 무거운 어드민 템플릿 대신 **Naive UI 컴포넌트 + 직접 구성한 3단 레이아웃**을 택했습니다. 이번 과제의 핵심 화면이 지도·차트·권고 카드라서 로그인·권한·라우팅이 필요 없고, 의존성이 적어야 설치·발표가 안정적이기 때문입니다. 모든 라이브러리는 MIT/Apache-2.0/BSD 계열로 과제·비상업 사용에 제약이 없습니다.

색 규칙: 혼잡 단계는 상태 팔레트(여유 초록·보통 노랑·혼잡 주황·매우혼잡 빨강)를 쓰고 항상 라벨과 함께 표시, 그래프 계열색(실제·모델·베이스라인)은 색각이상 대비 검증을 통과한 조합을 사용했습니다.

---

## 8. 검증 체크리스트

| 항목 | 결과 |
|---|---|
| 새 venv 설치 후 README 절차대로 백엔드·프론트 실행 | ✅ |
| 샘플 데이터만으로 학습 → /predict → /ask → /advise | ✅ (`pytest` 자동 검증) |
| 모델 MAE · 베이스라인 MAE · 개선율 출력·저장 | ✅ 학습 로그 + `metrics.json` |
| 시간 순서 분할 (미래 누수 없음) | ✅ assert + 테스트 |
| /ask 출처 첨부, 근거 없는 질문 "찾을 수 없음" | ✅ 유사도 임계값(bge 0.55) + LLM 판단 2단계 |
| /advise: 예상 승객·혼잡도·권고 간격·근거·needs_review | ✅ |
| Qwen3 ↔ OpenAI 전환, 키 없으면 명확한 오류 | ✅ 400 `OPENAI_API_KEY가 설정되지 않았습니다…` |
| logistics·field_service "미구현" 응답 | ✅ 501 |
| 예측·권고 MariaDB 저장 | ✅ `prediction_log`, `advisory_log`, `qa_log` |
| GitHub private, .env·데이터·모델 미커밋 | ✅ `.gitignore` |

---

## 9. 한계와 다음 단계

- **macOS OpenMP 충돌**: xgboost(Homebrew libomp)와 torch(내장 libomp)를 한 프로세스에 올리면 segfault → 학습 스크립트는 torch 없이, API 서버는 xgboost 없이 동작하도록 분리하고 **예측은 학습 시 배치로 미리 계산**합니다 (향후 7일 포함).
- 운행횟수를 "사전에 정해진 배차 계획"으로 보고 같은 시간대 값을 피처로 사용합니다. 미래 날짜는 1주 전 같은 요일 값을 계획으로 가정합니다.
- 혼잡도는 승차 예측에 구간 환산 비율(최근 8주 평균)을 곱해 추정하므로, 노선 개편·이벤트 등 구조 변화에는 둔감합니다. 정원 55명·임계값은 `app/config.py`에서 조정합니다.
- Qwen3-1.7B는 한국어 법령 요약 품질에 한계가 있어, 출처 조항 원문(스니펫)을 함께 보여 줍니다.
- 2차 스프린트: 버스위치정보 API로 실시간 차량 위치 오버레이, 물류·출동 도메인 어댑터 구현.
