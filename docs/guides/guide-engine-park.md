# 박성훈 · 배차 엔진

## 담당 업무

| 업무 | 한 줄 설명 | 난이도 |
|---|---|---|
| 승객 수 상한 예측 (F-11) | "많이 몰리면 몇 명까지 타나"를 노선·시간대별로 계산 | ★★☆☆☆ |
| 배차간격 변경 시뮬레이션 (F-12) | "버스를 몇 분마다 보내면 얼마나 덜 붐비나"를 계산 | ★★☆☆☆ |
| 전 노선 배차 최적화 알고리즘 (F-13) | 5개 노선에 가진 버스를 한꺼번에 나눠 주는 계산 | ★★★★★ |
| 배차 방식 성능 비교 (F-14) | 지금 방식과 새 방식을 지난 두 달 기록으로 채점 | ★★★★☆ |
| 시뮬레이션·최적화 결과 화면 (F-15) | 위 결과를 대시보드에서 보는 화면 2개 | ★★★☆☆ |

별 합계 16개
`feat/park-engine`

## Step 1. 승객 수 상한 예측 (F-11)

- 할 일: 노선·시간대마다 "보통 몇 명"과 "많이 몰리면 몇 명"을 같이 낸다.
- 결과물: 노선·시간대별 상한 비율표 파일 1개
- 확인: 지난 기록에서 실제 승객이 상한을 넘은 날이 10% 안팎이면 완료

<details markdown="1">
<summary>AI에게 붙여넣을 지시문</summary>

```
docs/guides/guide-engine-park.md의 Step 1을 읽어줘.
data/processed/bus_real_store.parquet에서 split=="valid"인 행만 써서,
노선·시간대별로 (실제 승차 합계 / 예측 합계)의 90% 분위수를 구하는 scripts/calibrate_p90.py를 만들고 싶어.
- 정류장이 아니라 노선·날짜·시간 단위로 먼저 합계를 낸 뒤 비율을 구해
- 결과는 models/bus/real/p90.json에 저장 (encoding="utf-8")
- 표본이 10개 미만인 시간대는 그 노선 전체 값으로 대체
- 마지막에 "P90을 적용하면 실제가 넘는 비율"을 출력해서 검증
코드는 아직 쓰지 말고 계획부터 짧게 알려줘.
```

</details>

<details markdown="1">
<summary>기술 세부 내용</summary>

- 지금 예측값(`pred`)은 평균치. 검증 기간의 실제÷예측 비율에서 90% 분위수(P90)를 구해 `pred × 비율`로 상한을 만든다. 재학습 없음.
- 파일: `scripts/calibrate_p90.py`, `models/bus/real/p90.json` (`{"143": {"7": 1.08, ...}}`)
- 알고리즘 후보와 결정 방법: [기능별 설계](algorithms.md)의 F-11

</details>

## Step 2. 배차간격 변경 시뮬레이션 (F-12)

- 할 일: 노선·시간·배차간격을 넣으면 바꾸기 전과 후의 붐빔 정도를 보여 준다.
- 결과물: 시뮬레이션 기능(API) 1개, 자동 테스트 2개
- 확인: 간격을 줄이면 붐빔 수치가 내려가고, 노선이 허용하지 않는 간격은 오류로 막히면 완료

<details markdown="1">
<summary>AI에게 붙여넣을 지시문</summary>

```
docs/guides/guide-engine-park.md의 Step 2를 읽어줘.
app/domains/bus/advisor.py의 analyze_day()가 구간별 onboard(예상 재차인원)와 trips를 계산하는 방식을 먼저 설명해줘.
그다음 이걸 재사용해서 simulate(route, date, hour, interval)를 app/domains/bus/simulator.py에 만들고,
adapter.py router() 안에 POST /api/bus/simulate를 추가하는 계획을 알려줘.
interval은 노선 기본정보의 최소~최대 배차간격 범위만 허용해. 범위를 벗어나면 400.
```

</details>

<details markdown="1">
<summary>기술 세부 내용</summary>

- 간격 → 시간당 운행횟수(60÷간격) → 구간별 혼잡도. 혼잡도 계산은 `BusAdvisor.analyze_day()` 재사용.
- 파일: `app/domains/bus/simulator.py`, 엔드포인트는 `adapter.py`의 `router()` 안 `advise_all` 옆
- 응답 예시

```json
{"route": "143", "date": "2026-10-28", "hour": 7,
 "current": {"interval": 7.5, "max_congestion": 0.92, "level": "매우혼잡"},
 "simulated": {"interval": 6.0, "max_congestion": 0.74, "level": "보통"},
 "segments": [{"seq": 12, "from_name": "...", "congestion": 0.74}],
 "needs_review": true}
```

</details>

## Step 3. 전 노선 배차 최적화 알고리즘 (F-13)

- 할 일: 날짜와 시간대를 고르면 5개 노선의 시간당 버스 횟수를 가진 버스 대수 안에서 한꺼번에 정한다.
- 결과물: 최적화 기능(API) 1개, 자동 테스트 2개
- 확인: 어떤 노선도 가진 버스 대수를 넘지 않고, 붐비는 노선·시간 수가 지금보다 줄면 완료
- 시작 전 정할 것: 노선 정보 엑셀의 "운행시간"이 편도인지 왕복인지, 160번 버스 대수를 어떻게 채울지

<details markdown="1">
<summary>AI에게 붙여넣을 지시문 (두 번에 나눠서)</summary>

```
docs/guides/guide-engine-park.md의 Step 3을 읽어줘.
OR-Tools CP-SAT로 이 문제를 푸는 방법을 처음 보는 사람 기준으로 설명해줘.
특히 "과밀량 = max(0, 재차인원 - 0.7*55*x)"를 CP-SAT에서 어떻게 표현하는지 알려줘. 코드는 아직 쓰지 마.
```

```
app/domains/bus/optimizer.py를 만들어줘.
- 입력 데이터는 analyze_day()와 route_info_for()에서 가져와 (새로 계산하지 말 것)
- 재차인원은 Step 1의 P90 비율을 곱한 값 사용
- 숫자 상수(0.7, 55)는 app/config.py 값을 써
- 풀이 시간 제한 10초
함수 하나로만 만들고, 내가 확인한 다음 API를 붙일게.
```

</details>

<details markdown="1">
<summary>기술 세부 내용</summary>

- 도구: OR-Tools CP-SAT (`requirements.txt`에 `ortools` 추가)
- 정할 값: 노선 r, 시간 h의 운행횟수 `x[r,h]` (정수)
- 지킬 것: `60/최대배차 ≤ x ≤ 60/최소배차`, `x × 운행시간 / 60 ≤ 인가대수`
- 줄일 것: 과밀량(상한 승객이 `0.7 × 55 × x`를 넘는 만큼) × α + 총 운행횟수 × β + 현행 대비 바뀐 양 × γ
- 파일: `app/domains/bus/optimizer.py`, `POST /api/bus/optimize`
- 요청·응답 예시

```json
요청: {"date": "2026-10-28", "hours": [6,7,8,9], "weights": {"overload": 10, "trips": 1, "change": 2}}
응답: {"plan": [{"route": "160", "hour": 7, "current_trips": 6, "recommended_trips": 8,
                "interval": 7.5, "buses_needed": 35, "fleet": 38,
                "max_congestion_before": 0.96, "max_congestion_after": 0.72}],
       "kpi": {"overcrowded_before": 5, "overcrowded_after": 1, "extra_trips": 6},
       "binding": ["272번 8시: 인가대수 한계"],
       "needs_review": true}
```

- 알고리즘 후보(규칙·그리디·정수계획)와 가중치 정하는 법: [기능별 설계](algorithms.md)의 F-13

</details>

## Step 4. 배차 방식 성능 비교 (F-14)

- 할 일: 지난 두 달을 날짜별로 다시 돌려 "지금 방식 / 규칙 방식 / 최적화 방식"을 실제 승객 수로 채점한다.
- 결과물: 비교표 1장 (발표 슬라이드에 그대로 들어감), 결과 파일 1개
- 확인: 아래 표가 다 채워지면 완료. 결과 파일을 김주혜에게 넘김

| 방식 | 붐빈 노선·시간 비율 | 총 버스 운행 횟수 | 바뀐 곳 수 | 버스 대수 초과 |
|---|---|---|---|---|
| 지금 방식 | | | 0 | |
| 규칙 방식 | | | | |
| 최적화 방식 | | | | 0 |

<details markdown="1">
<summary>AI에게 붙여넣을 지시문</summary>

```
docs/guides/guide-engine-park.md의 Step 4를 읽어줘.
scripts/backtest.py를 만들고 싶어. 검증 기간 날짜마다 현행/규칙/최적화 세 방식의 운행횟수를 정하고,
"실제 승차"로 계산한 혼잡도로 채점해서 표를 출력하고 eval/results/backtest.json에 저장해.
중요: 운행횟수를 정할 때는 예측값(P90)만 쓰고, 채점할 때만 실제값을 써. 실제값이 결정에 섞이면 안 돼.
계획부터 알려줘.
```

</details>

<details markdown="1">
<summary>기술 세부 내용</summary>

- 파일: `scripts/backtest.py` → `eval/results/backtest.json`
- 붐빔 기준: 혼잡도 0.8 이상
- 운행횟수 결정에는 예측값만, 채점에만 실제값 사용 (섞이면 점수가 부풀려짐)
- 채점 지표 정의: [기능별 설계](algorithms.md)의 F-14

</details>

## Step 5. 시뮬레이션·최적화 결과 화면 (F-15)

- 할 일: 대시보드에 화면 2개를 붙인다.
  - 배차간격 시뮬레이션 화면: 노선과 간격을 슬라이더로 바꾸면 전후 붐빔이 바로 바뀜
  - 최적화 결과 화면: 시간대를 고르면 노선별 "지금 → 추천" 버스 횟수 표, 버스가 모자란 곳 강조
- 결과물: 화면 2개
- 확인: 불러오는 중·오류·데이터 없음 상태가 각각 화면에 표시되면 완료

<details markdown="1">
<summary>AI에게 붙여넣을 지시문</summary>

```
frontend/src/components/AdvicePanel.vue와 layout.js가 패널을 어떻게 등록하는지 먼저 설명해줘.
같은 방식으로 WhatIfPanel.vue를 추가하고 싶어. API 호출은 frontend/src/api.js에만 추가하고,
로딩·에러·빈 상태를 모두 보여줘. 색은 theme.js 토큰만 써.
```

</details>

<details markdown="1">
<summary>기술 세부 내용</summary>

- 기존 패널 격자(`PanelGrid.vue`, `layout.js`)에 붙임
- 파일: `WhatIfPanel.vue`, `OptimizePanel.vue`, `frontend/src/api.js`

</details>

## 다른 사람과 주고받는 것

| 상대 | 내가 주는 것 | 받는 것 |
|---|---|---|
| 오정훈 | 시뮬레이션·최적화 기능 (챗봇이 부름). 형식 바뀌면 단톡에 먼저 공지 | 개발 룰, 실행 환경 |
| 김주혜 | 성능 비교 결과 파일 | 버스 운행 변경 시 신고·인가 조건 (최적화 기준에 반영) |
