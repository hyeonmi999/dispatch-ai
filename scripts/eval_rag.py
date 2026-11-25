"""
규정 질의응답(RAG) 평가 — 문항 수는 문제집에서 집계 (eval/rag_eval.json)

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
import hashlib
import re
import unicodedata
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import DOCS_DIR, NOT_FOUND_ANSWER, RAG_TOP_K  # noqa: E402
from app.core.rag import KnowledgeBase  # noqa: E402


def normalize_notation(text: str) -> str:
    """표현의 의미는 바꾸지 않고 단위·구분점·공백 표기만 통일합니다."""
    text = text.translate(str.maketrans({c: "·" for c in "ㆍ･・"}))
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*퍼센트", r"\1%", text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*km(?=$|[^a-z])", r"\1킬로미터", text)
    return re.sub(r"\s+", "", text)


def _contains(answer: str, keyword: str, scoring: str) -> bool:
    if scoring == "literal":
        return keyword in answer
    answer, keyword = normalize_notation(answer), normalize_notation(keyword)
    pattern = re.escape(keyword)
    if keyword and keyword[0].isdigit():
        pattern = r"(?<![\d.+-])" + pattern
    if keyword and keyword[-1].isdigit():
        pattern += r"(?![\d.])"
    return bool(re.search(pattern, answer))


def validate_items(items: list) -> None:
    if not isinstance(items, list) or not items:
        raise ValueError("문제집은 비어 있지 않은 목록이어야 합니다.")
    ids = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("문항은 객체여야 합니다.")
        for key in ("id", "q"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError(f"문항의 {key}를 확인하세요.")
        if item["id"] in ids or item.get("type") not in ("answer", "refuse"):
            raise ValueError("문항 번호 중복 또는 문항 유형 오류입니다.")
        ids.add(item["id"])
        if item["type"] == "answer":
            groups = item.get("must")
            if not isinstance(groups, list) or not groups or any(
                not isinstance(g, list) or not g or any(not isinstance(w, str) or not w.strip() for w in g)
                for g in groups
            ):
                raise ValueError(f"{item['id']}: must 형식을 확인하세요.")
            if not isinstance(item.get("doc"), str) or not item["doc"].strip():
                raise ValueError(f"{item['id']}: doc를 확인하세요.")
            gold = item.get("gold_articles")
            if gold is not None and (not isinstance(gold, list) or not gold or any(
                not isinstance(g, dict) or any(not isinstance(g.get(k), str) or not g[k].strip()
                                              for k in ("document", "article")) for g in gold
            )):
                raise ValueError(f"{item['id']}: gold_articles 형식을 확인하세요.")
            forbidden = item.get("forbid", [])
            if not isinstance(forbidden, list) or any(not isinstance(w, str) or not w.strip() for w in forbidden):
                raise ValueError(f"{item['id']}: forbid 형식을 확인하세요.")


def judge(item: dict, res: dict, scoring: str = "literal", criteria: dict | None = None) -> dict:
    if scoring not in ("literal", "normalized"):
        raise ValueError("알 수 없는 채점 방식입니다.")
    ans = res.get("answer") or ""
    if item["type"] == "refuse":
        ok = not res["found"]
        return {"ok": ok, "hallucination": not ok}
    rules = {**item, **(criteria or {})}
    hit = [any(_contains(ans, k, scoring) for k in group) for group in rules["must"]]
    bad = [w for w in rules.get("forbid", []) if _contains(ans, w, scoring)]
    cited = any(item["doc"] in (s["document"] or "") for s in res.get("sources", []))
    ok = res["found"] and all(hit) and not bad
    return {"ok": ok, "missing": [g for g, h in zip(rules["must"], hit) if not h], "forbidden": bad,
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
    p.add_argument("--name", help="실제 AI 평가 시 결과 파일 이름")
    p.add_argument("--retrieval-only", action="store_true", help="검색만 채점(LLM 실행 없음)")
    p.add_argument("--k", type=int, default=RAG_TOP_K)
    p.add_argument("--scoring", choices=("literal", "normalized"), default="literal")
    p.add_argument("--criteria", type=Path, help="선택적 문항별 강화 기준 JSON")
    p.add_argument("--check-only", action="store_true", help="모델을 실행하지 않고 문제집 형식만 검사")
    p.add_argument("--model", default=None, help="로컬 LLM (기본: 설정값 Qwen/Qwen3-1.7B)")
    p.add_argument("--adapter", default=None, help="LoRA 어댑터 경로")
    p.add_argument("--llm", default="qwen")
    args = p.parse_args()

    dataset_path = ROOT / "eval/rag_eval.json"
    items = json.loads(dataset_path.read_text(encoding="utf-8"))
    validate_items(items)
    overrides, criteria_version, criteria_hash = {}, None, None
    if args.criteria:
        data = args.criteria.read_bytes()
        package = json.loads(data.decode("utf-8"))
        criteria_version = package["version"]
        overrides = package["items"]
        if not isinstance(overrides, dict) or set(overrides) - {it["id"] for it in items}:
            raise ValueError("강화 기준의 문항 번호를 확인하세요.")
        for it in items:
            override = overrides.get(it["id"], {})
            if not isinstance(override, dict) or set(override) - {"must", "forbid"}:
                raise ValueError("강화 기준에는 must와 forbid만 지정할 수 있습니다.")
            validate_items([{**it, **override}])
        criteria_hash = hashlib.sha256(data).hexdigest()
    if args.check_only:
        print(json.dumps({"status": "format_check_only", "total": len(items),
                          "answer": sum(i["type"] == "answer" for i in items),
                          "refuse": sum(i["type"] == "refuse" for i in items),
                          "scoring": args.scoring, "criteria_version": criteria_version}, ensure_ascii=False))
        return
    if args.k < 1:
        p.error("--k는 1 이상이어야 합니다")
    if args.retrieval_only:
        return main_retrieval(p, args)
    if not args.name:
        p.error("실제 AI 평가에는 --name이 필요합니다.")
    from app.core import llm as llm_module  # 늦은 import: 검색 전용·형식 검사는 LLM 모듈을 올리지 않음

    if args.model:
        llm_module.set_local_model(args.model, args.adapter)
    elif args.adapter:
        llm_module.set_local_model(llm_module.LOCAL_MODEL_NAME, args.adapter)

    kb = KnowledgeBase("bus", DOCS_DIR / "bus")
    rows, t0 = [], time.time()
    for it in items:
        res = kb.ask(it["q"], llm=args.llm, embedding="bge")
        j = judge(it, res, scoring=args.scoring, criteria=overrides.get(it["id"]))
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
        "scoring_version": args.scoring + "-v1",
        "criteria_version": criteria_version, "criteria_sha256": criteria_hash,
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
    }
    print("=" * 60, "\n", json.dumps(summary, ensure_ascii=False))
    out = ROOT / "eval/results"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.name}.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
