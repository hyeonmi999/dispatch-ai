"""
검색 방식 비교 실험 (계획서 docs/plans/oh-evaluation.md Task E-8)

조각(근거 단위)·문제집·프롬프트는 그대로 두고 검색 설정만 바꿔 법규 40문항을 다시 채점합니다.
- 세부 글: 조각 하나를 "조각 전체 + 항·호·목 단위의 글"로 임베딩 (RAG_SEARCH_UNIT_LIMIT, 0이면 조각 전체만)
- k: LLM에 넘기는 조각 수 (RAG_TOP_K)
- 임계값: 이 유사도보다 낮은 조각은 근거에서 뺌 (RAG_SCORE_THRESHOLD)

서비스 색인(vectorstore/bus)은 건드리지 않고 vectorstore/_experiment/ 아래에 설정별로 따로 만듭니다.
채점 함수는 scripts/eval_rag.py의 것을 그대로 씁니다.

사용
  python scripts/experiment_retrieval.py --name rag40_retrieval_exp                 # 검색만 (LLM 없음)
  python scripts/experiment_retrieval.py --name rag40_answer_exp --answer --variants base_k4,units250_k4
                                                                                    # 로컬 모델 답변까지 (변형 하나에 6~10분)
주의: 채점·학습 스크립트를 돌릴 때는 API 서버를 끕니다 (AGENTS.md 2장).
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import eval_rag  # noqa: E402

from app.config import DOCS_DIR, RAG_SCORE_THRESHOLD, RAG_SEARCH_UNIT_LIMIT, RAG_TOP_K, VECTORSTORE_DIR  # noqa: E402
from app.core import rag  # noqa: E402

EMBEDDING = "bge"
BASE_THRESHOLD = RAG_SCORE_THRESHOLD[EMBEDDING]
OLD_ITEMS = 20          # 문제집 앞 20문항이 기존 문항 (여러 번 채점하며 프롬프트를 고친 문항), 뒤 20문항이 새 문항

# 이름: (세부 글 글자 수 상한, k, 임계값)
VARIANTS = {
    "base_k4": (0, 4, BASE_THRESHOLD),               # 바꾸기 전
    "base_k8": (0, 8, BASE_THRESHOLD),               # 실험 A: 조각 수만 늘림
    "base_k12": (0, 12, BASE_THRESHOLD),
    "units200_k4": (200, 4, BASE_THRESHOLD),         # 세부 글 크기를 바꿔도 결과가 같은지
    "units250_k4": (250, 4, BASE_THRESHOLD),         # 실험 D: 세부 글
    "units400_k4": (400, 4, BASE_THRESHOLD),
    "units250_k8": (250, 8, BASE_THRESHOLD),
    "units250_k4_th060": (250, 4, 0.60),             # 세부 글 + 임계값을 올림
    "base_k4_th060": (0, 4, 0.60),
}
RETRIEVAL_DEFAULT = ("base_k4", "base_k8", "base_k12", "units200_k4", "units250_k4", "units400_k4", "units250_k8")
ANSWER_DEFAULT = ("base_k4", "base_k8", "units250_k4")


def knowledge_base(unit_limit: int, threshold: float) -> rag.KnowledgeBase:
    """실험 설정을 rag 모듈에 넣고, 그 설정의 색인 폴더를 쓰는 지식베이스를 돌려줌"""
    rag.RAG_SEARCH_UNIT_LIMIT = {**RAG_SEARCH_UNIT_LIMIT, EMBEDDING: unit_limit}
    rag.RAG_SCORE_THRESHOLD = {**RAG_SCORE_THRESHOLD, EMBEDDING: threshold}
    rag.VECTORSTORE_DIR = VECTORSTORE_DIR / "_experiment" / f"units{unit_limit}"
    return rag.KnowledgeBase("bus", DOCS_DIR / "bus")


def _split(rows: list[dict], old_ids: set) -> tuple[list, list]:
    return [r for r in rows if r["id"] in old_ids], [r for r in rows if r["id"] not in old_ids]


def retrieval_block(kb, items: list[dict], k: int, threshold: float, old_ids: set) -> tuple[dict, list[dict]]:
    """검색만 채점: 정답 조항이 상위 k개 안에 있는지, 임계값을 넘어 근거로 전달되는지, 거절형이 검색 단계에서 걸러지는지"""
    summary, rows = eval_rag.retrieval_eval(kb, items, k)
    for row in rows:
        # 정답 조각이 상위 k개 안에 있어도 임계값보다 낮으면 ask()가 근거에서 뺌
        row["delivered"] = bool(row["rank"]) and row["scores"][row["rank"] - 1] >= threshold
    refuse = []
    for it in items:
        if it["type"] == "refuse":
            hits = kb.search(it["q"], EMBEDDING, k=1, role="citizen")
            top = hits[0]["score"] if hits else None
            refuse.append({"id": it["id"], "top_score": top, "gated": top is None or top < threshold})
    old, new = _split(rows, old_ids)
    manifest = json.loads((kb.index_dir(EMBEDDING) / "manifest.json").read_text(encoding="utf-8"))
    summary = {
        **{key: summary[key] for key in ("n", "hit_at_1_n", "hit_at_3_n", "hit_at_k_n", "mrr")},
        "hit_at_k_old": f"{sum(r['hit_at_k'] for r in old)}/{len(old)}",
        "hit_at_k_new": f"{sum(r['hit_at_k'] for r in new)}/{len(new)}",
        "delivered_n": f"{sum(r['delivered'] for r in rows)}/{len(rows)}",
        "missed": {r["id"]: r["rank"] for r in rows if not r["hit_at_k"]},
        "refuse_gated_n": f"{sum(r['gated'] for r in refuse)}/{len(refuse)}",
        "refuse_gated": [r["id"] for r in refuse if r["gated"]],
        "chunks": manifest["chunks"], "vectors": manifest.get("vectors", manifest["chunks"]),
    }
    return summary, rows + refuse


def answer_block(kb, items: list[dict], k: int, old_ids: set) -> tuple[dict, list[dict]]:
    """로컬 모델 답변까지 채점 (eval_rag.py와 같은 판정). 같은 프롬프트였는지 비교할 수 있게 프롬프트 해시도 남김"""
    from app.core import llm as llm_module

    sent = []
    original = llm_module.generate

    def recording(provider, system, user, *args, **kwargs):
        sent.append(hashlib.sha256(f"{system}\n{user}".encode("utf-8")).hexdigest()[:16])
        return original(provider, system, user, *args, **kwargs)

    llm_module.generate = recording
    rows, t0 = [], time.time()
    try:
        for it in items:
            sent.clear()
            res = kb.ask(it["q"], llm="qwen", embedding=EMBEDDING, k=k)
            verdict = eval_rag.judge(it, res)
            rows.append({"id": it["id"], "type": it["type"], "q": it["q"], "answer": res["answer"], "found": res["found"],
                         "note": res.get("note"), "sources": [f"{s['document']} {s['article']}" for s in res.get("sources", [])],
                         "prompt_sha": sent[0] if sent else None, **verdict})
            mark = "✓" if verdict["ok"] else ("✗ 환각" if verdict["hallucination"] else "✗")
            print(f"  {mark} {it['id']:4s} {(res['answer'] or '')[:60]!r}", flush=True)
    finally:
        llm_module.generate = original

    def count(group, kind):
        sel = [r for r in group if r["type"] == kind]
        return f"{sum(r['ok'] for r in sel)}/{len(sel)}"

    old, new = _split(rows, old_ids)
    summary = {
        "model": llm_module.LOCAL_MODEL_NAME, "adapter": llm_module.LOCAL_ADAPTER,
        "answer_correct": count(rows, "answer"), "refusal_correct": count(rows, "refuse"),
        "hallucinations": sum(r["hallucination"] for r in rows),
        "old": {"answer_correct": count(old, "answer"), "refusal_correct": count(old, "refuse"),
                "hallucinations": sum(r["hallucination"] for r in old)},
        "new": {"answer_correct": count(new, "answer"), "refusal_correct": count(new, "refuse"),
                "hallucinations": sum(r["hallucination"] for r in new)},
        "wrong": [r["id"] for r in rows if not r["ok"]],
        "llm_calls": sum(r["prompt_sha"] is not None for r in rows),
        "seconds": round(time.time() - t0),
    }
    return summary, rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True, help="결과 파일 이름 (eval/results/<이름>.json)")
    p.add_argument("--answer", action="store_true", help="로컬 모델 답변까지 채점 (없으면 검색만)")
    p.add_argument("--variants", help=f"쉼표로 구분. 선택: {', '.join(VARIANTS)}")
    p.add_argument("--embed-device", choices=("cpu",), default=None,
                   help="임베딩 모델을 올릴 장치. 8GB GPU에서 --answer가 메모리 부족으로 죽으면 cpu (질문 임베딩만 하므로 느려지지 않음)")
    args = p.parse_args()

    names = args.variants.split(",") if args.variants else (ANSWER_DEFAULT if args.answer else RETRIEVAL_DEFAULT)
    unknown = [n for n in names if n not in VARIANTS]
    if unknown:
        p.error(f"모르는 변형: {unknown}")

    dataset_path = ROOT / "eval/rag_eval.json"
    items = json.loads(dataset_path.read_text(encoding="utf-8"))
    eval_rag.validate_items(items)
    old_ids = {it["id"] for it in items[:OLD_ITEMS]}
    out = ROOT / "eval/results" / f"{args.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.embed_device:
        # 임베딩 모델만 지정한 장치에 미리 올려 둠 (rag가 한 번 만든 임베더를 계속 씀). LLM은 원래대로 GPU
        from app.core import llm as llm_module
        device, llm_module.get_device = llm_module.get_device, lambda: args.embed_device
        try:
            rag.get_embedder(EMBEDDING)
        finally:
            llm_module.get_device = device
    result = {"summary": {
        "name": args.name, "mode": "answer" if args.answer else "retrieval_only", "embedding": EMBEDDING, "role": "citizen",
        "embed_device": args.embed_device or "기본(GPU가 있으면 GPU)",
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(), "scoring_version": "literal-v1",
        "chunk_version": rag.CHUNK_VERSION, "service_setting": {"unit_limit": RAG_SEARCH_UNIT_LIMIT[EMBEDDING], "k": RAG_TOP_K,
                                                                "threshold": BASE_THRESHOLD},
        "variants": {},
    }, "rows": {}}

    for name in names:
        unit_limit, k, threshold = VARIANTS[name]
        print(f"=== {name}: 세부 글 {unit_limit}자 · k={k} · 임계값 {threshold}", flush=True)
        kb = knowledge_base(unit_limit, threshold)
        retrieval, rows = retrieval_block(kb, items, k, threshold, old_ids)
        block = {"unit_limit": unit_limit, "k": k, "threshold": threshold, "retrieval": retrieval}
        if args.answer:
            block["answer"], rows = answer_block(kb, items, k, old_ids)
        result["summary"]["variants"][name] = block
        result["rows"][name] = rows
        # 변형 하나가 끝날 때마다 저장 (중간에 죽어도 끝난 변형은 남음)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({key: value for key, value in block.items() if key != "retrieval"} | {"retrieval": retrieval},
                         ensure_ascii=False), flush=True)
    print("=" * 60, "\n결과:", out)


if __name__ == "__main__":
    main()
