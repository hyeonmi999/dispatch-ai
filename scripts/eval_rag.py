"""
규정 질의응답(RAG) 평가 — 정답형 12문항 + 거절형 8문항 (eval/rag_eval.json)

채점
- 정답형: 근거를 찾았고(found) 필수 키워드 그룹을 모두 포함하며 금지어(다른 제도 혼동 등)가 없어야 정답
- 거절형: 문서에 없는 내용 → "찾을 수 없음"으로 답해야 정답. 답을 지어내면 '환각'
결과: eval/results/<이름>.json  (질문별 답변·출처·판정 저장 → 전후 비교)

사용
  python scripts/eval_rag.py --name base_1.7b
  python scripts/eval_rag.py --name qwen4b --model Qwen/Qwen3-4B
  python scripts/eval_rag.py --name lora --adapter models/llm/qwen3-1.7b-rag-lora
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import DOCS_DIR, NOT_FOUND_ANSWER  # noqa: E402
from app.core import llm as llm_module  # noqa: E402
from app.core.rag import KnowledgeBase  # noqa: E402


def judge(item: dict, res: dict) -> dict:
    ans = res.get("answer") or ""
    if item["type"] == "refuse":
        ok = not res["found"]
        return {"ok": ok, "hallucination": not ok}
    hit = [any(k in ans for k in group) for group in item["must"]]
    bad = [w for w in item.get("forbid", []) if w in ans]
    cited = any(item["doc"] in (s["document"] or "") for s in res.get("sources", []))
    ok = res["found"] and all(hit) and not bad
    return {"ok": ok, "missing": [g for g, h in zip(item["must"], hit) if not h], "forbidden": bad,
            "cited": cited, "hallucination": bool(bad)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--model", default=None, help="로컬 LLM (기본: 설정값 Qwen/Qwen3-1.7B)")
    p.add_argument("--adapter", default=None, help="LoRA 어댑터 경로")
    p.add_argument("--llm", default="qwen")
    args = p.parse_args()

    if args.model:
        llm_module.set_local_model(args.model, args.adapter)
    elif args.adapter:
        llm_module.set_local_model(llm_module.LOCAL_MODEL_NAME, args.adapter)

    items = json.loads((ROOT / "eval/rag_eval.json").read_text())
    kb = KnowledgeBase("bus", DOCS_DIR / "bus")
    rows, t0 = [], time.time()
    for it in items:
        res = kb.ask(it["q"], llm=args.llm, embedding="bge")
        j = judge(it, res)
        rows.append({**it, "answer": res["answer"], "found": res["found"], "note": res.get("note"),
                     "sources": [f"{s['document']} {s['article']}" for s in res.get("sources", [])], **j})
        mark = "✓" if j["ok"] else ("✗ 환각" if j["hallucination"] else "✗")
        print(f"{mark} {it['id']:4s} {it['q'][:40]} → {(res['answer'] or '')[:70]!r}", flush=True)

    ans = [r for r in rows if r["type"] == "answer"]
    ref = [r for r in rows if r["type"] == "refuse"]
    summary = {
        "name": args.name, "model": llm_module.LOCAL_MODEL_NAME, "adapter": llm_module.LOCAL_ADAPTER,
        "answer_correct": f"{sum(r['ok'] for r in ans)}/{len(ans)}",
        "refusal_correct": f"{sum(r['ok'] for r in ref)}/{len(ref)}",
        "hallucinations": sum(r["hallucination"] for r in rows),
        "seconds": round(time.time() - t0),
    }
    print("=" * 60, "\n", json.dumps(summary, ensure_ascii=False))
    out = ROOT / "eval/results"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.name}.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
