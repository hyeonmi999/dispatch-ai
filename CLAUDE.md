# CLAUDE.md — dispatch-ai

AI 코딩 도구(Claude Code 등)가 이 저장소에서 작업할 때 따르는 규칙입니다. 사람용 팀 룰은 [CONTRIBUTING.md](CONTRIBUTING.md)이고, 이 파일은 그 위에 AI 작업에만 필요한 규칙을 더합니다.

## 프로젝트
- 서울 시내버스 배차 AI: 수요 예측(XGBoost) → 혼잡도 → 배차 권고·최적화 → 규정 RAG 근거. 모든 권고는 `needs_review=true`.
- 스택: Python 3.11 · FastAPI · SQLAlchemy async + MariaDB(Docker, 로컬) · FAISS + bge-m3 · Qwen3(로컬) / OpenAI(선택) · Vue 3 + Vite(JS) · pytest
- **로컬 전용**: 배포·인증·외부 공개를 전제로 한 작업을 하지 않습니다.
- 구조와 실행 방법: [README.md](README.md)

## 실행·검증 명령
```bash
python -m pytest -q                              # 합성 데이터 테스트
python -m uvicorn app.main:app --port 8000       # API (http://localhost:8000/docs)
cd frontend && npm run dev                       # 화면 (http://localhost:5173)
python scripts/eval_rag.py --name <이름>          # RAG 평가셋 재측정
```

## AI 작업 규칙
1. **커밋·푸시는 사용자가 지시할 때만** 합니다. 커밋할 때는 이번 작업에서 바꾼 파일만 이름을 지정해 스테이징하고, 다른 사람의 미커밋 변경이 보이면 손대지 말고 알립니다. 커밋 메시지는 `CONTRIBUTING.md` 1장 형식(`YYYY-MM-DD-이름 : 대주제 - 소주제 - 간단한 내용`)을 따릅니다.
2. **요청 범위만** 고칩니다. 관련 없는 리팩터링, 의존성 추가, 파일 삭제를 하지 않습니다.
3. **"완료"는 검증 후에만** 말합니다. pytest 통과, 서버 기동, 바꾼 API·화면을 한 번 호출해 본 결과까지 확인합니다. 실패는 실패로 보고합니다.
4. **시그니처·스키마를 바꾸기 전에 모든 호출처를 검색**합니다(`app/`, `scripts/`, `tests/`, `frontend/src/api.js`).
5. **테이블·컬럼·파일 이름은 실제 정의를 읽고** 씁니다(`app/db/database.py`, `app/config.py`). 짐작으로 만들지 않습니다.
6. **계획과 다른 문제를 발견하면 멈추고 보고**합니다. 임의로 설계를 바꾸지 않습니다.
7. **결정적 작업은 코드로**: 데이터 집계·검증·비교는 스크립트로 돌리고 결과를 파일로 남깁니다. 수치를 추측해서 쓰지 않습니다.
8. **ML 누수 주의**: 피처를 추가·변경할 때 예측 시점(실측 마지막 날 + 21일)에 알 수 없는 값을 쓰지 않았는지 확인합니다.
9. **LLM 답변 경로**: 수치는 도구·API 결과만 인용하고, 근거가 없으면 "찾을 수 없음"으로 답하게 합니다. 프롬프트를 바꾸면 평가셋을 다시 돌립니다.
10. **Windows 호환**: 파일 I/O에는 `encoding="utf-8"`, 경로는 `pathlib`, 셸 스크립트는 `.sh`와 `.ps1`을 쌍으로 둡니다.
11. **비밀값 금지**: `.env`의 값을 코드·로그·커밋·외부 요청에 넣지 않습니다.

## 문서 위치
- 계획서: `docs/plans/<기능>.md` ([템플릿](docs/plans/_TEMPLATE.md))
- 교훈: [docs/lessons.md](docs/lessons.md) — 작업 영역과 관련된 "방지 규칙"을 먼저 읽고 시작합니다.
- 평가 결과: `eval/results/`
