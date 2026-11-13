# AI 개발 환경설정

교수님 「개발 환경 구성 가이드」(Python 3.11 · VS Code · MariaDB · Docker Desktop) 기준입니다. 처음 한 번만 하면 됩니다.

## 1. 프로그램 설치

| 프로그램 | 버전·설정 | 확인 명령 |
|---|---|---|
| Git | Windows는 Git for Windows | `git --version` |
| Python | 3.11.9. 설치 화면에서 "Add python.exe to PATH" 체크. macOS는 `brew install python@3.11` | `py -3.11 --version` (macOS `python3.11 --version`) |
| VS Code | 확장: Python, Jupyter (선택: Pylance) | |
| Node.js | 20 이상 (프론트엔드용) | `node --version` |
| C++ 빌드 도구 | Windows만. Build Tools for Visual Studio 2022 → "C++를 사용한 데스크톱 개발" 설치 후 재부팅. macOS는 `xcode-select --install` | |
| Docker Desktop | DB를 쓸 때만. Windows는 WSL2 먼저 설치(`wsl --install`) | `docker --version` |

Windows PowerShell에서 가상환경 활성화가 막히면 한 번 실행합니다.
```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
```

## 2. GitHub에서 소스 받기

```bash
git clone https://github.com/hyeonmi999/dispatch-ai.git
cd dispatch-ai
git checkout dev
```

## 3. 가상환경 만들기

```bash
py -3.11 -m venv .venv             # Windows
.venv\Scripts\Activate

python3.11 -m venv .venv           # macOS
source .venv/bin/activate
```

프롬프트 앞에 `(.venv)`가 보이면 됩니다. VS Code에서 `Ctrl+Shift+P` → "Python: Select Interpreter" → `.venv` 안의 python을 고릅니다.

## 4. 패키지 설치

```bash
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
cd frontend && npm install && cd ..
```

## 5. 환경변수 파일

```bash
cp .env.example .env               # Windows PowerShell: copy .env.example .env
```

`.env`는 커밋하지 않습니다. AI 모델은 모두 작은 모델 하나로 통일합니다. `.env`에서 `LOCAL_MODEL_NAME=Qwen/Qwen3-1.7B`로 맞춥니다(처음 한 번 약 4GB 다운로드).

## 6. 데이터·모델 받기

GitHub 저장소 → Releases → `data-snapshot-20261107`에서 tar.gz 3개를 받아 프로젝트 폴더에 두고 풉니다.

```bash
tar -xzf dispatch-ai-data.tar.gz
tar -xzf dispatch-ai-models.tar.gz
tar -xzf dispatch-ai-vectorstore.tar.gz
```

`data/`, `models/`, `vectorstore/` 폴더가 생기면 됩니다. 이 폴더들은 커밋하지 않습니다.

## 7. DB 띄우기 (선택)

DB가 없어도 API와 화면은 돌아갑니다. 이력 저장을 확인할 때만 띄웁니다.

```bash
cp docker/.env.example docker/.env
docker compose -p dispatch_ai -f docker/maria_db.yml up -d
```

MySQL Workbench로 `127.0.0.1:3379`에 접속해 `dispatch_ai` DB가 보이면 됩니다.

## 8. 실행 확인

터미널 두 개를 엽니다.

```bash
python -m uvicorn app.main:app --port 8000      # 터미널 1
cd frontend && npm run dev                      # 터미널 2
```

| 확인 | 정상 |
|---|---|
| http://localhost:5173 | 지도와 그래프가 보임 |
| http://localhost:8000/docs | `/api/bus/predict` 실행 시 응답이 나옴 |
| `python -m pytest -q` | 모두 통과 |

## 9. AI 코딩 도구 연결

Claude Code(VS Code 확장) 또는 Cursor를 설치하고, VS Code에서 `dispatch-ai` 폴더를 엽니다. 도구가 저장소의 `CLAUDE.md`(AI 작업 규칙)를 자동으로 읽습니다. 사람용 팀 규칙은 `CONTRIBUTING.md`입니다.

연결 확인용으로 한 번 시켜 봅니다.
```
CLAUDE.md와 README.md를 읽고 이 프로젝트 구조를 5줄로 요약해줘.
```

## 10. 내 작업 브랜치

```bash
git checkout dev && git pull
git checkout -b feat/<이름>-<기능>             # 예: feat/park-engine

git status
git add <바꾼 파일>                             # 바꾼 파일만 지정
git commit -m "feat(optimizer): 인가대수 제약 추가"
git push -u origin feat/<이름>-<기능>           # GitHub에서 dev로 PR
```

`git pull` 뒤에는 `pip install -r requirements.txt`와 `npm install`을 다시 합니다.

## 막혔을 때

에러 메시지 전체를 AI 도구에 붙여넣고 원인을 묻습니다. 같은 에러가 세 번 반복되면 단톡에 올립니다.
