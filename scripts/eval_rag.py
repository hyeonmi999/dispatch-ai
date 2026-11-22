"""
규정 질의응답(RAG) 평가 — 정답형 12문항 + 거절형 8문항 (eval/rag_eval.json)

채점
- 정답형: 근거를 찾았고(found) 필수 키워드 그룹을 모두 포함하며 금지어(다른 제도 혼동 등)가 없어야 정답
- 거절형: 문서에 없는 내용 → "찾을 수 없음"으로 답해야 정답. 답을 지어내면 '환각'
결과: eval/results/<이름>.json  (질문별 답변·출처·판정 저장 → 전후 비교)

검색 전용(--retrieval-only): 정답형 중 gold_articles가 있는 문항만 채점(거절형 제외), LLM 로드 없음
- 검색은 max(k, 3)개를 가져온다. rank·hit@1·hit@k·MRR은 상위 k개 안에서, hit@3은 k가 3보다 작아도 상위 3개를 본다.
- rank: 상위 k 안에서 처음 일치한 순위(없으면 null). MRR = Σ(1/rank)/n, rank가 null이면 0
- summary.mode == "retrieval_only"로 judge 결과(mode 키 없음)와 구분. 오류는 결과 파일 없이 p.error(종료 코드 2)

사용
  python scripts/eval_rag.py --name base_1.7b
  python scripts/eval_rag.py --retrieval-only [--k 4] [--name rag_retrieval_1]   # 검색만 채점 (LLM 로드 없음)
  python scripts/eval_rag.py --name qwen4b --model Qwen/Qwen3-4B
  python scripts/eval_rag.py --name lora --adapter models/llm/qwen3-1.7b-rag-lora

검색 전용 모드: kb.search(k=max(k, 3))로 검색하므로 k<3이어도 hit@3은 상위 3개를 본다.
rank·hit@k·MRR은 상위 k 안에서만 센다. 대상은 type=="answer"이고 gold_articles가 있는 문항(거절형 제외).
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import DOCS_DIR, NOT_FOUND_ANSWER, RAG_TOP_K  # noqa: E402
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


def _norm(s) -> str:
    return " ".join((s or "").split())


def _gold_match(g: dict, hit: dict) -> bool:
    if _norm(g["document"]) not in _norm(hit.get("document")):
        return False
    return g.get("article") is None or _norm(hit.get("article")).startswith(_norm(g["article"]))


def retrieval_rank(gold_articles: list[dict], hits: list[dict]) -> int | None:
    """hits를 순서대로 보며 gold와 처음 일치하는 순위(1부터). 없으면 None."""
    for i, hit in enumerate(hits, start=1):
        if any(_gold_match(g, hit) for g in gold_articles):
            return i
    return None


def retrieval_eval(kb, items: list[dict], k: int) -> tuple[dict, list[dict]]:
    """검색만 채점(LLM 없음). 정답형 중 gold_articles가 있는 문항이 대상."""
    targets = [it for it in items if it.get("type") == "answer" and it.get("gold_articles")]
    rows = []
    for it in targets:
        hits = kb.search(it["q"], "bge", k=max(k, 3), role="citizen")
        top = hits[:k]
        rank = retrieval_rank(it["gold_articles"], top)
        rows.append({
            "id": it["id"], "q": it["q"], "gold_articles": it["gold_articles"], "rank": rank,
            "hit_at_1": rank is not None and rank <= 1,
            "hit_at_3": retrieval_rank(it["gold_articles"], hits[:3]) is not None,
            "hit_at_k": rank is not None,
            "retrieved": [f"{h['document']} {h['article']}" for h in top],
            "scores": [h["score"] for h in top],
        })
    n = len(rows)
    if n == 0:
        return {"n": 0}, rows
    def cnt(key): return sum(r[key] for r in rows)
    summary = {
        "mode": "retrieval_only", "embedding": "bge", "role": "citizen", "k": k, "n": n,
        "hit_at_1": round(cnt("hit_at_1") / n, 4), "hit_at_3": round(cnt("hit_at_3") / n, 4),
        "hit_at_k": round(cnt("hit_at_k") / n, 4),
        "hit_at_1_n": f"{cnt('hit_at_1')}/{n}", "hit_at_3_n": f"{cnt('hit_at_3')}/{n}",
        "hit_at_k_n": f"{cnt('hit_at_k')}/{n}",
        "mrr": round(sum(1 / r["rank"] for r in rows if r["rank"]) / n, 4),
    }
    return summary, rows


def main_retrieval(p, args):
    name = args.name or "rag_retrieval_1"
    items = json.loads((ROOT / "eval/rag_eval.json").read_text(encoding="utf-8"))
    kb = KnowledgeBase("bus", DOCS_DIR / "bus")
    t0 = time.time()
    summary, rows = retrieval_eval(kb, items, args.k)
    if not rows:
        p.error("gold_articles가 있는 정답형 문항이 없습니다")
    for r in rows:
        top1 = r["retrieved"][0] if r["retrieved"] else "-"
        print(f"{'✓' if r['hit_at_k'] else '✗'} {r['id']:4s} rank={r['rank'] or '-'} {top1}", flush=True)
    summary = {"name": name, **summary, "seconds": round(time.time() - t0)}
    print("=" * 60, "\n", json.dumps(summary, ensure_ascii=False))
    out = ROOT / "eval/results"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", default=None)
    p.add_argument("--retrieval-only", action="store_true", help="검색만 채점(LLM 로드 없음, gold_articles 기준)")
    p.add_argument("--k", type=int, default=RAG_TOP_K, help="검색 상위 k (기본: 설정값 RAG_TOP_K)")
    p.add_argument("--model", default=None, help="로컬 LLM (기본: 설정값 Qwen/Qwen3-1.7B)")
    p.add_argument("--adapter", default=None, help="LoRA 어댑터 경로")
    p.add_argument("--llm", default="qwen")
    args = p.parse_args()
    if args.k < 1:
        p.error("--k는 1 이상이어야 합니다")
    if args.retrieval_only:
        return main_retrieval(p, args)
    if args.name is None:
        p.error("--name 필요")
    from app.core import llm as llm_module  # 늦은 import: 검색 전용 모드는 LLM을 로드하지 않음

    if args.model:
        llm_module.set_local_model(args.model, args.adapter)
    elif args.adapter:
        llm_module.set_local_model(llm_module.LOCAL_MODEL_NAME, args.adapter)

    items = json.loads((ROOT / "eval/rag_eval.json").read_text(encoding="utf-8"))
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
        "name": args.name, "llm": args.llm,
        "model": llm_module.OPENAI_MODEL_NAME if args.llm == "openai" else llm_module.LOCAL_MODEL_NAME,
        "adapter": None if args.llm == "openai" else llm_module.LOCAL_ADAPTER,
        "answer_correct": f"{sum(r['ok'] for r in ans)}/{len(ans)}",
        "refusal_correct": f"{sum(r['ok'] for r in ref)}/{len(ref)}",
        "hallucinations": sum(r["hallucination"] for r in rows),
        "seconds": round(time.time() - t0),
    }
    print("=" * 60, "\n", json.dumps(summary, ensure_ascii=False))
    out = ROOT / "eval/results"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.name}.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
