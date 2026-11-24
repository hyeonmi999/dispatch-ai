"""
배차 상담 챗봇 채점 (F-33 문제집) — 질문마다 맞는 기능을 불렀는지 확인

문제집 형식 (docs/contracts.md 4번)
  expect_tools도 지원하며 ["none"]은 기능 없음과 out_of_scope를 요구합니다.
  must는 그룹별 하나 이상·그룹 전체 일치, forbid는 금지어 부재를 검사합니다.
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
import hashlib
from collections import Counter
import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import agent  # noqa: E402
from app.domains.bus.adapter import BusAdapter  # noqa: E402


def expected_tools(item: dict) -> list[str]:
    """요청 형식과 기존 tools 형식을 공통 호출 목록으로 읽습니다."""
    if "tools" not in item and "expect_tools" not in item:
        raise ValueError("tools 또는 expect_tools가 필요합니다.")
    values = []
    for field in ("tools", "expect_tools"):
        if field not in item:
            continue
        tools = item[field]
        if not isinstance(tools, list) or any(not isinstance(t, str) for t in tools):
            raise ValueError(f"{field}는 기능 이름 목록이어야 합니다.")
        if field == "expect_tools" and tools == ["none"]:
            tools = []
        elif any(t not in agent.HANDLERS for t in tools) or (field == "expect_tools" and not tools):
            raise ValueError(f"{field}의 기능 이름을 확인하세요. 범위 밖은 expect_tools: [\"none\"]입니다.")
        values.append(tools)
    if any(v != values[0] for v in values[1:]):
        raise ValueError("tools와 expect_tools가 서로 다릅니다.")
    if item.get("expect_tools") == ["none"] and item.get("intent", "out_of_scope") != "out_of_scope":
        raise ValueError("none의 intent는 out_of_scope여야 합니다.")
    return values[0]


def keyword_rules(item: dict) -> tuple[list, list]:
    groups, forbidden = item.get("must", []), item.get("forbid", [])
    if not isinstance(groups, list) or any(
        not isinstance(g, list) or not g or any(not isinstance(w, str) or not w.strip() for w in g)
        for g in groups
    ):
        raise ValueError("must는 비어 있지 않은 단어 그룹의 목록이어야 합니다.")
    if not isinstance(forbidden, list) or any(not isinstance(w, str) or not w.strip() for w in forbidden):
        raise ValueError("forbid는 단어 목록이어야 합니다.")
    return groups, forbidden


def validate_items(items: list) -> None:
    if not isinstance(items, list) or not items:
        raise ValueError("문제집은 비어 있지 않은 목록이어야 합니다.")
    ids = set()
    for item in items:
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip() for k in ("id", "q")):
            raise ValueError("문항의 id와 q를 확인하세요.")
        if item["id"] in ids:
            raise ValueError("문항 번호가 중복됩니다.")
        ids.add(item["id"])
        expected_tools(item)
        keyword_rules(item)
        if "intent" in item and (not isinstance(item["intent"], str) or item["intent"] not in set(agent.HANDLERS) | {"clarify", "out_of_scope"}):
            raise ValueError("알 수 없는 intent입니다.")
        if not isinstance(item.get("slots", {}), dict):
            raise ValueError("slots는 객체여야 합니다.")
        history = item.get("history", [])
        if not isinstance(history, list) or any(not isinstance(h, dict) or h.get("role") not in ("user", "assistant")
            or not isinstance(h.get("content"), str) for h in history):
            raise ValueError("history의 role과 content를 확인하세요.")


def judge(item: dict, res: dict) -> dict:
    expected = expected_tools(item)
    groups, forbidden = keyword_rules(item)
    called = [t["name"] for t in res["tools"]]
    wrong = []
    if called != expected:
        wrong.append(f"tools {called}")
    intent = item.get("intent")
    if intent is None and item.get("expect_tools") == ["none"]:
        intent = "out_of_scope"
    if intent is not None and res["intent"] != intent:
        wrong.append(f"intent {res['intent']}")
    for key, value in item.get("slots", {}).items():
        if res["slots"].get(key) != value:
            wrong.append(f"{key} {res['slots'].get(key)!r}")
    answer = res.get("answer") or ""
    missing = [g for g in groups if not any(w in answer for w in g)]
    bad = [w for w in forbidden if w in answer]
    if missing:
        wrong.append(f"must {missing}")
    if bad:
        wrong.append(f"forbid {bad}")
    return {"ok": not wrong, "wrong": wrong, "missing": missing, "forbidden": bad}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name")
    p.add_argument("--check-only", action="store_true", help="모델 없이 문제집 형식만 검사")
    p.add_argument("--file", default="eval/agent_eval.json")
    p.add_argument("--today", default=None, help="'내일' 같은 말을 풀 기준일 (YYYY-MM-DD). 기본은 오늘")
    args = p.parse_args()

    path = ROOT / args.file
    if not path.exists():
        sys.exit(f"문제집이 없습니다: {path}")
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
        validate_items(items)
    except (ValueError, OSError) as error:
        p.error(str(error))
    if args.check_only:
        counts = Counter(t for item in items for t in (expected_tools(item) or ["none"]))
        print(json.dumps({"status": "format_check_only", "total": len(items), "per_tool": dict(counts)}, ensure_ascii=False))
        return
    if not args.name:
        p.error("채점 실행에는 --name이 필요합니다.")
    # 날짜만 주면 정오 기준 (0시로 두면 길안내 문항이 심야 기준으로 계산됨)
    now = dt.datetime.combine(dt.date.fromisoformat(args.today), dt.time(12)) if args.today else dt.datetime.now()

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
        "scoring_version": "agent-eval-v2",
        "dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "tool_execution_errors": sum(any(not t.get("ok", True) for t in r["got_tools"]) for r in rows),
    }
    print("=" * 60, "\n", json.dumps(summary, ensure_ascii=False))
    out = ROOT / "eval/results"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.name}.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
