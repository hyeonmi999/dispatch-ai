"""eval_rag.py --retrieval-only 순수 함수 테스트 (임베딩·LLM 없이 가짜 kb 사용)"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from eval_rag import retrieval_eval, retrieval_rank  # noqa: E402

DOC = "여객자동차 운수사업법 시행규칙"


def hit(article, document=DOC, score=0.5):
    return {"document": document, "article": article, "page": None, "url": None, "score": score, "text": ""}


def gold(article, document=DOC):
    return [{"document": document, "article": article}]


class FakeKB:
    def __init__(self, hits):
        self.hits, self.calls = hits, []

    def search(self, query, embedding, k=4, role="citizen"):
        self.calls.append((query, embedding, k, role))
        return self.hits[:k]


def test_rank_1_3_none():
    g = gold("제31조(")
    assert retrieval_rank(g, [hit("제31조(가)"), hit("제32조(나)")]) == 1
    assert retrieval_rank(g, [hit("제1조("), hit("제2조("), hit("제31조(가)")]) == 3
    assert retrieval_rank(g, [hit("제1조(")]) is None


def test_empty_hits():
    assert retrieval_rank(gold("제31조("), []) is None


def test_prefix_hang_ho_mok():
    g = gold("제33조(사업계획의 변경신고) ① 제2호 마목")
    assert retrieval_rank(g, [hit("제33조(사업계획의 변경신고) ① 제2호 마목")]) == 1
    assert retrieval_rank(g, [hit("제33조(사업계획의 변경신고) ① 제2호 바목")]) is None
    assert retrieval_rank(gold("제33조(사업계획의 변경신고)"), [hit("제33조(사업계획의 변경신고) ② ")]) == 1


def test_article_boundaries():
    assert retrieval_rank(gold("제3조("), [hit("제33조(변경신고)")]) is None
    assert retrieval_rank(gold("제32조("), [hit("제32조의2(특례)")]) is None
    assert retrieval_rank(gold("제32조("), [hit("제32조(기준) ①")]) == 1


def test_whitespace_normalization_and_none():
    assert retrieval_rank(gold("제2조( 정의 )", "시내버스  준공영제"), [hit("제2조(  정의 )", "서울특별시 시내버스 \n준공영제 조례")]) == 1
    assert retrieval_rank(gold(None), [hit(None)]) == 1
    assert retrieval_rank(gold("제1조("), [hit(None, None)]) is None


def test_any_gold_element_matches():
    g = [{"document": "A", "article": "본문 2"}, {"document": "A", "article": "본문 3"}]
    assert retrieval_rank(g, [hit("본문 1", "A"), hit("본문 3", "A")]) == 2


def test_eval_excludes_refuse_and_computes_mrr():
    items = [
        {"id": "A1", "type": "answer", "q": "q1", "gold_articles": gold("제31조(")},
        {"id": "A2", "type": "answer", "q": "q2", "gold_articles": gold("제44조(")},
        {"id": "A3", "type": "answer", "q": "q3"},  # gold 없음 → 제외
        {"id": "R1", "type": "refuse", "q": "r"},
    ]
    kb = FakeKB([hit("제9조("), hit("제31조(a)"), hit("제5조("), hit("제6조(")])
    s, rows = retrieval_eval(kb, items, 4)
    assert [r["id"] for r in rows] == ["A1", "A2"]
    assert s["n"] == 2 and s["k"] == 4 and s["mode"] == "retrieval_only"
    assert rows[0]["rank"] == 2 and rows[1]["rank"] is None
    assert rows[0]["hit_at_1"] is False and rows[0]["hit_at_3"] is True and rows[0]["hit_at_k"] is True
    assert s["mrr"] == pytest.approx(0.25) and s["hit_at_k_n"] == "1/2" and s["hit_at_1_n"] == "0/2"
    assert rows[0]["retrieved"][1] == f"{DOC} 제31조(a)" and len(rows[0]["scores"]) == 4
    assert kb.calls[0] == ("q1", "bge", 4, "citizen")


def test_eval_k_below_3_uses_top3_for_hit3_and_slice_for_rank():
    items = [{"id": "A1", "type": "answer", "q": "q", "gold_articles": gold("제31조(")}]
    kb = FakeKB([hit("제1조("), hit("제2조("), hit("제31조(a)")])
    s, rows = retrieval_eval(kb, items, 1)
    assert kb.calls[0][2] == 3
    assert rows[0]["rank"] is None and rows[0]["hit_at_3"] is True and rows[0]["hit_at_k"] is False
    assert len(rows[0]["retrieved"]) == 1 and s["mrr"] == 0


def test_eval_empty_search_and_no_targets():
    items = [{"id": "A1", "type": "answer", "q": "q", "gold_articles": gold("제31조(")}]
    s, rows = retrieval_eval(FakeKB([]), items, 4)
    assert rows[0]["rank"] is None and rows[0]["retrieved"] == [] and rows[0]["scores"] == []
    s, rows = retrieval_eval(FakeKB([]), [{"id": "R1", "type": "refuse", "q": "x"}], 4)
    assert rows == [] and s["n"] == 0
