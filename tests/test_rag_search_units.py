"""검색용 세부 글 (오정훈, 계획서 oh-evaluation.md Task E-8)

조각(근거 단위)은 그대로 두고, 긴 조각은 항·호·목 단위의 글도 함께 임베딩해 조각의 점수를 그중 최댓값으로 봄.
재현하는 문제: 정답 문장이 큰 조각 안에 묻혀, 그 문장만 따로 조각이 된 다른 조항보다 순위가 밀림 (40문항의 A14·A24).
"""
import json

import pytest
from langchain_core.embeddings import Embeddings

from app.core import rag

MARK = "우회운행"


@pytest.fixture(scope="module", autouse=True)
def sample_model():
    """검색 검증에는 버스 예측 모델 학습이 필요하지 않습니다."""
    yield


class DensityEmbeddings(Embeddings):
    """글에서 표지어가 차지하는 비중이 클수록 질문과 가까움 → 긴 글에 묻힌 문장은 점수가 낮음"""
    __test__ = False

    def embed_documents(self, texts):
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return [1.0, 0.0]

    @staticmethod
    def _vector(text):
        share = min(1.0, 40 * text.count(MARK) / max(len(text), 1))
        return [share, (1 - share * share) ** 0.5]


BIG = (f"다음 각 호의 기준에 따른다.\n1. {'신설하려는 경우의 기준 ' * 12}\n"
       f"2. {MARK}노선 지정은 도로상태를 고려한다.\n3. {'연장하려는 경우의 기준 ' * 12}")
SMALL = f"{MARK} 안내 " + "다른 내용 " * 18


def _knowledge(tmp_path, monkeypatch, limit):
    root = tmp_path / "bus"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(rag, "VECTORSTORE_DIR", tmp_path / "index")
    monkeypatch.setattr(rag, "get_embedder", lambda _: DensityEmbeddings())
    monkeypatch.setattr(rag, "RAG_SEARCH_UNIT_LIMIT", {"bge": limit, "openai": 0})
    return root, rag.KnowledgeBase("bus", root)


def test_search_units_keep_whole_chunk_first_and_add_focused_pieces():
    body = (f"② 사업계획 변경은 다음 각 호의 기준에 따른다. 1. {'가' * 300} "
            f"2. {MARK}노선은 도로상태를 고려한다. 3. {'나' * 300}")
    text = f"[시행규칙] 제32조(기준) ②\n{rag.CRUMB_PREFIX}윗문장\n{body}"
    units = rag.search_units(text, 250)
    assert units[0] == text                                   # 첫 글은 조각 전체 (이전 방식과 같은 글)
    assert f"[시행규칙] 제32조(기준) ② 제2호\n2. {MARK}노선은 도로상태를 고려한다." in units
    assert all(rag.CRUMB_PREFIX not in unit for unit in units[1:])     # 세부 글에는 윗문장을 붙이지 않음
    assert all(unit.startswith("[시행규칙] 제32조(기준) ②") for unit in units)
    assert len(set(units)) == len(units)
    assert rag.search_units(text, 0) == [text]                # 0이면 조각 전체만
    short = "[시행규칙] 제1조(목적)\n이 규칙은 목적을 정한다."
    assert rag.search_units(short, 250) == [short]            # 짧은 조각은 글이 하나


def test_split_hierarchy_default_limit_is_unchanged():
    # 조각을 만드는 쪽은 그대로: 900자 이하 조항은 나누지 않음
    body = "① " + "가" * 400 + " ② " + "나" * 400
    assert rag.split_hierarchy(body) == [((), (), body)]
    assert [labels for labels, _, _ in rag.split_hierarchy(body, limit=250)][:2] == [("①",), ("①",)]


def test_buried_sentence_is_found_through_focused_unit(tmp_path, monkeypatch):
    # 재현: 조각 전체만 임베딩하면 긴 조각에 묻힌 정답 문장이 짧은 다른 조각에 밀림
    root, kb = _knowledge(tmp_path, monkeypatch, limit=0)
    (root / "big.txt").write_text(BIG, encoding="utf-8")
    (root / "small.txt").write_text(SMALL, encoding="utf-8")
    assert [h["document"] for h in kb.search(MARK, "bge", 2)] == ["small", "big"]

    monkeypatch.setattr(rag, "RAG_SEARCH_UNIT_LIMIT", {"bge": 250, "openai": 0})
    assert kb.index_needs_rebuild("bge")                      # 설정이 바뀌면 색인을 다시 만듦
    hits = kb.search(MARK, "bge", 2)
    assert [h["document"] for h in hits] == ["big", "small"]
    assert hits[0]["text"] == f"[big]\n{BIG.strip()}"         # 근거로 넘기는 글은 조각 전체 그대로
    assert hits[0]["score"] > 0.9
    assert not kb.index_needs_rebuild("bge")


def test_one_chunk_appears_once_and_k_counts_chunks(tmp_path, monkeypatch):
    root, kb = _knowledge(tmp_path, monkeypatch, limit=250)
    (root / "big.txt").write_text(BIG, encoding="utf-8")
    for i in range(5):
        (root / f"small{i}.txt").write_text(SMALL + str(i), encoding="utf-8")
    hits = kb.search(MARK, "bge", 4)
    documents = [h["document"] for h in hits]
    assert len(documents) == 4 and len(set(documents)) == 4   # 같은 조각의 세부 글이 여러 자리를 차지하지 않음
    assert documents[0] == "big"
    manifest = json.loads((kb.index_dir("bge") / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["chunks"] == 6 and manifest["vectors"] > manifest["chunks"]
    assert manifest["search_unit_limit"] == 250
    sources = kb.ask(MARK, use_llm=False)["sources"]
    assert [s["document"] for s in sources].count("big") == 1


def test_focused_units_follow_document_grade(tmp_path, monkeypatch):
    # 세부 글로 찾힌 조각에도 열람 등급이 그대로 적용됨
    root, kb = _knowledge(tmp_path, monkeypatch, limit=250)
    (root / "big.txt").write_text(BIG, encoding="utf-8")
    (root / "small.txt").write_text(SMALL, encoding="utf-8")
    (root / "documents.json").write_text(json.dumps({"big.txt": {"grade": "제한"}}, ensure_ascii=False), encoding="utf-8")
    assert [h["document"] for h in kb.search(MARK, "bge", 4, role="citizen")] == ["small"]
    assert [h["document"] for h in kb.search(MARK, "bge", 4, role="admin")] == ["big", "small"]


def test_index_without_unit_setting_is_kept_when_setting_is_off(tmp_path, monkeypatch):
    # 세부 글을 쓰지 않는 임베딩(OpenAI)은 예전에 만든 색인을 그대로 씀 (다시 만들면 외부 호출·비용이 듦)
    root, kb = _knowledge(tmp_path, monkeypatch, limit=0)
    (root / "law.txt").write_text(SMALL, encoding="utf-8")
    kb.build("bge")
    path = kb.index_dir("bge") / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    del manifest["search_unit_limit"], manifest["vectors"]    # 이 기능 이전의 색인 모양
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    assert not kb.index_needs_rebuild("bge")
    monkeypatch.setattr(rag, "RAG_SEARCH_UNIT_LIMIT", {"bge": 250, "openai": 0})
    assert kb.index_needs_rebuild("bge")
