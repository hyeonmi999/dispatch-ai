# 공통 룰 적용 가이드

실무 프로젝트에서 쓰던 개발 룰 중 어느 프로젝트에나 통하는 원칙만 골라 이 프로젝트(Python · FastAPI · Vue · MariaDB, 로컬 실행, 3명)에 맞게 다시 쓴 것입니다. 인증·배포·운영 서버용 규칙은 뺐고, 이 프로젝트에 필요한 데이터·ML 실험 규칙과 Windows 규칙을 더했습니다.

## 파일 6개

| 파일 | 저장소 위치 | 누가 읽나 | 언제 쓰나 |
|---|---|---|---|
| 팀 개발 룰 | `CONTRIBUTING.md` | 사람 | 코딩·리뷰할 때 기준. 코딩·API·DB·ML 실험·LLM·테스트·보안·환경·리뷰 10개 장 |
| AI 작업 규칙 | `CLAUDE.md` | AI 코딩 도구 | 도구가 저장소를 열면 자동으로 읽음. 지시할 때만 커밋, 검증 후에만 완료 보고 등 11개 |
| PR 템플릿 | `.github/pull_request_template.md` | 사람 | GitHub에서 PR을 만들면 본문에 자동으로 들어감 |
| 계획서 템플릿 | `docs/plans/_TEMPLATE.md` | 사람 · AI | 하루 이상 걸리는 작업을 시작할 때 복사해서 씀 |
| 교훈 기록 | `docs/lessons.md` | 사람 · AI | 버그를 고치거나 오래 헤맨 뒤 한 줄 기록, 작업 전 관련 줄 읽기 |
| 줄바꿈 설정 | `.gitattributes` | Git | 커밋할 때 Windows·Mac 줄바꿈을 자동으로 맞춤 |

## 적용 방법

1. 오정훈이 6개 파일을 PR로 올립니다(F-21). `.gitattributes`는 기존 파일 줄바꿈이 한꺼번에 바뀌므로 커밋을 따로 나눕니다.
2. 세 명이 PR에서 룰을 읽고, 바꾸고 싶은 항목은 PR 댓글로 남깁니다.
3. 머지되면 각자 `git checkout dev && git pull`. 이후로는 별도 설정 없이 적용됩니다.
   - Claude Code: 저장소의 `CLAUDE.md`를 자동으로 읽습니다.
   - Cursor: 프로젝트 규칙(Rules)에 `CLAUDE.md` 내용을 붙여 넣습니다.
   - GitHub: PR을 만들면 템플릿이 자동으로 채워집니다.
4. 룰을 바꾸고 싶으면 `CONTRIBUTING.md`를 고치는 PR을 올립니다.

PR 머지 전에 먼저 써 보고 싶으면 아래 원문을 복사하거나, 첨부한 `공통룰_파일.zip`을 프로젝트 폴더에 풀면 됩니다. zip 안의 폴더 구조가 저장소 위치와 같습니다.

## 작업 중 룰이 쓰이는 순간

```mermaid
flowchart LR
  A["작업 시작"] --> B["계획서 작성<br/>_TEMPLATE.md"]
  B --> C["관련 교훈 읽기<br/>lessons.md"]
  C --> D["코딩<br/>CONTRIBUTING.md<br/>AI는 CLAUDE.md"]
  D --> E["완료 확인<br/>pytest · 서버 · 호출처"]
  E --> F["커밋<br/>.gitattributes 자동"]
  F --> G["PR<br/>pull_request_template.md"]
  G --> H["리뷰<br/>CRITICAL · HIGH 0건이면 머지"]
  H -.->|버그 · 삽질| I["교훈 한 줄<br/>lessons.md"]
```

## 꼭 지킬 것 10개

| 번호 | 룰 | 자세히 |
|---|---|---|
| 1 | `main`에 직접 push하지 않고 PR로만 머지, 1명 리뷰 | CONTRIBUTING 1장 |
| 2 | 내가 바꾼 파일만 이름을 지정해 커밋 (`git add .` 금지) | CONTRIBUTING 1장 |
| 3 | pytest 통과 + 서버를 띄워 직접 확인한 뒤에만 "완료" | CONTRIBUTING 1장 |
| 4 | 같은 기능을 두 곳에 만들지 않음. 공통 계층 먼저 확인 | CONTRIBUTING 2장 |
| 5 | 에러 응답은 `{"status", "detail"}` 형식, 새 API는 `response_model` 명시 | CONTRIBUTING 3장 |
| 6 | SQL에 값을 문자열로 이어 붙이지 않음 | CONTRIBUTING 4장 |
| 7 | 학습·평가는 날짜 순서로 나누고, 예측 시점에 모르는 값을 쓰지 않음 | CONTRIBUTING 5장 |
| 8 | 챗봇·RAG 답변의 숫자는 계산 결과에서만, 근거 없으면 "찾을 수 없음" | CONTRIBUTING 6장 |
| 9 | 파일을 읽고 쓸 때 `encoding="utf-8"` | CONTRIBUTING 9장 |
| 10 | `.env`, `data/`, `models/`는 커밋하지 않음 | CONTRIBUTING 8장 |

## 파일 원문
