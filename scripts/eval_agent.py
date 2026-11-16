"""
배차 상담 챗봇 채점 (F-33 문제집) — 질문마다 맞는 기능을 불렀는지 확인

문제집 형식 (docs/contracts.md 4번)
  [{"id": "G1", "q": "160번 10월 28일 오후 3시에 혼잡해?", "tools": ["predict"],
    "slots": {"route": "160", "date": "2026-10-28", "hour": 15}},        ← slots·intent는 선택
   {"id": "G3", "q": "271번 몇 명 타?", "tools": [], "intent": "clarify"}]

채점
- 정답: 응답의 tools[].name 목록이 문제집의 tools와 같고, slots·intent를 적었으면 그 값도 같아야 함
- 기능이 오류를 돌려줘도(예측 범위 밖 날짜 등) 맞는 기능을 골랐으면 정답 (기능 선택을 채점하므로)
결과: eval/results/<이름>.json

사용 (서버를 띄우지 않아도 됨. LLM은 쓰지 않음)
  python scripts/eval_agent.py --name agent_1
  python scripts/eval_agent.py --name agent_sample --file eval/agent_eval.sample.json
"""

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import agent  # noqa: E402
from app.domains.bus.adapter import BusAdapter  # noqa: E402


def judge(item: dict, res: dict) -> dict:
    called = [t["name"] for t in res["tools"]]
    wrong = []
    if called != item.get("tools", []):
        wrong.append(f"tools {called}")
    if "intent" in item and res["intent"] != item["intent"]:
        wrong.append(f"intent {res['intent']}")
    for key, value in item.get("slots", {}).items():
        if res["slots"].get(key) != value:
            wrong.append(f"{key} {res['slots'].get(key)!r}")
    return {"ok": not wrong, "wrong": wrong}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--file", default="eval/agent_eval.json")
    p.add_argument("--today", default=None, help="'내일' 같은 말을 풀 기준일 (YYYY-MM-DD). 기본은 오늘")
    args = p.parse_args()

    path = ROOT / args.file
    if not path.exists():
        sys.exit(f"문제집이 없습니다: {path}")
    items = json.loads(path.read_text(encoding="utf-8"))
    now = dt.datetime.fromisoformat(args.today) if args.today else dt.datetime.now()

    adapter = BusAdapter()
    adapter.startup()

    rows, t0 = [], time.time()
    for it in items:
        res = agent.run(adapter, it["q"], history=it.get("history"), use_llm=False, location=it.get("location"), now=now)
        j = judge(it, res)
        rows.append({**it, "got_intent": res["intent"], "got_tools": res["tools"], "got_slots": res["slots"],
                     "numbers_verified": res["numbers_verified"], "answer": res["answer"], **j})
        mark = "O" if j["ok"] else "X"
        print(f"{mark} {it['id']:4s} {it['q'][:40]} -> {res['intent']} {[t['name'] for t in res['tools']]} {'; '.join(j['wrong'])}", flush=True)

    correct = sum(r["ok"] for r in rows)
    summary = {
        "name": args.name, "file": args.file, "today": f"{now:%Y-%m-%d}",
        "correct": f"{correct}/{len(rows)}",
        "accuracy": round(correct / len(rows), 3) if rows else None,
        "numbers_failed": sum(r["numbers_verified"] is False for r in rows),
        "seconds": round(time.time() - t0, 1),
    }
    print("=" * 60, "\n", json.dumps(summary, ensure_ascii=False))
    out = ROOT / "eval/results"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.name}.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
