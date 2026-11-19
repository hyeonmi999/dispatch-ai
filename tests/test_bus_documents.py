from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.embeddings import Embeddings

from app.core import rag
from app.domains.bus.adapter import BusAdapter
from app.core.documents import read_document_manifest


class TestEmbeddings(Embeddings):
    __test__ = False

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        return [1.0, 0.0] if "문서검증표지" in text else [0.0, 1.0]


def document_client(tmp_path, monkeypatch):
    bus = BusAdapter()
    root = tmp_path / "bus"
    root.mkdir()
    bus.knowledge_base.docs_dir = root
    monkeypatch.setattr(rag, "VECTORSTORE_DIR", tmp_path / "index")
    monkeypatch.setattr(rag, "get_embedder", lambda _: TestEmbeddings())
    app = FastAPI()
    app.include_router(bus.router())
    return TestClient(app), root, bus.knowledge_base


def test_document_lifecycle_updates_cached_index(tmp_path, monkeypatch):
    client, root, knowledge = document_client(tmp_path, monkeypatch)
    (root / "legacy.txt").write_text("기존 문서", encoding="utf-8")
    endpoint = "/api/bus/documents"
    fields = {"title": "등록한 문서", "type": "법령", "grade": "공개"}

    legacy = client.get(endpoint).json()["items"][0]
    assert legacy["active"] and legacy["grade"] == "공개"
    assert knowledge.search("기존 문서", "bge", 1)[0]["document"] == "legacy"

    response = client.post(endpoint, data=fields, files={
        "file": ("sample.txt", "문서검증표지", "text/plain"),
    })
    assert response.status_code == 201, response.text
    assert (root / "sample.txt").read_text(encoding="utf-8") == "문서검증표지"
    assert knowledge.ask("문서검증표지", use_llm=False)["sources"][0]["document"] == fields["title"]

    path = f"{endpoint}/sample.txt"
    response = client.put(path, json={**fields, "title": "새 제목", "grade": "내부", "active": False})
    assert response.status_code == 200, response.text
    assert read_document_manifest(root)["sample.txt"]["grade"] == "내부"
    assert not knowledge.ask("문서검증표지", use_llm=False)["found"]

    response = client.put(path, json={**fields, "title": "새 제목", "active": True})
    assert response.status_code == 200
    assert knowledge.ask("문서검증표지", use_llm=False)["sources"][0]["document"] == "새 제목"
    assert client.delete(path).status_code == 204
    assert not (root / "sample.txt").exists()
    assert "sample.txt" not in read_document_manifest(root)
    assert not knowledge.ask("문서검증표지", use_llm=False)["found"]


def test_unsafe_upload_does_not_write_files(tmp_path, monkeypatch):
    client, root, _ = document_client(tmp_path, monkeypatch)
    fields = {"title": "금지된 파일", "type": "법령", "grade": "공개"}
    for name in ("sample.exe", "../outside.txt", r"..\outside.txt", "CON.txt"):
        response = client.post("/api/bus/documents", data=fields, files={
            "file": (name, b"invalid", "application/octet-stream"),
        })
        assert response.status_code == 400, response.text
    assert list(root.iterdir()) == []
    assert not (tmp_path / "outside.txt").exists()


def _role_client(tmp_path, monkeypatch):
    from app.api import routes

    client, root, knowledge = document_client(tmp_path, monkeypatch)
    client.app.include_router(routes.router)
    bus = BusAdapter()
    bus.knowledge_base = knowledge
    monkeypatch.setattr(routes, "get_domain", lambda domain: bus)
    sent_contexts = []

    def answer(_llm, _system, message):
        sent_contexts.append(message)
        return "검색된 문서에 근거한 답변입니다."

    monkeypatch.setattr(rag.llm_module, "generate", answer)
    for grade, name in (("내부", "a-internal"), ("제한", "b-restricted"), ("공개", "c-public")):
        response = client.post("/api/bus/documents", data={
            "title": name, "type": "내부자료", "grade": grade,
        }, files={"file": (f"{name}.txt", f"문서검증표지 {name}", "text/plain")})
        assert response.status_code == 201, response.text
    assert {item["metadata"]["grade"] for item in rag.load_documents(root)} == {"공개", "내부", "제한"}
    return client, sent_contexts


def test_citizen_cannot_receive_internal_or_restricted_sources(tmp_path, monkeypatch):
    client, contexts = _role_client(tmp_path, monkeypatch)
    request = {"question": "문서검증표지", "top_k": 1}
    for body in (request, {**request, "role": "citizen"}):
        response = client.post("/api/bus/ask", json=body)
        assert response.status_code == 200, response.text
        assert [source["document"] for source in response.json()["sources"]] == ["c-public"]
        assert "c-public" in contexts[-1]
        assert "a-internal" not in contexts[-1] and "b-restricted" not in contexts[-1]


def test_admin_can_receive_internal_and_restricted_sources(tmp_path, monkeypatch):
    client, contexts = _role_client(tmp_path, monkeypatch)
    request = {"question": "문서검증표지", "top_k": 3}
    for role, expected in (
        ("staff", {"a-internal", "c-public"}),
        ("admin", {"a-internal", "b-restricted", "c-public"}),
    ):
        response = client.post("/api/bus/ask", json={**request, "role": role})
        assert response.status_code == 200, response.text
        assert {source["document"] for source in response.json()["sources"]} == expected
        assert all(title in contexts[-1] for title in expected)
        assert ("b-restricted" in contexts[-1]) == (role == "admin")


# ==================================================
# 역할별 검색 보완 (오정훈, 이슈 #7 · #8)
# ==================================================

class GradedEmbeddings(Embeddings):
    """질문과 제한 문서는 같은 방향, 공개 문서('공개본')는 유사도 0.9 → 제한 문서가 항상 앞 순위"""
    __test__ = False

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        return [0.9, 0.4359] if "공개본" in text else [1.0, 0.0]


def test_citizen_finds_public_document_behind_many_restricted_ones(tmp_path, monkeypatch):
    # 재현(#8): 후보를 k*3개만 보고 거르면, 제한 문서 조각이 12개 이상 앞에 있을 때 공개 문서를 놓쳤음
    client, root, knowledge = document_client(tmp_path, monkeypatch)
    monkeypatch.setattr(rag, "get_embedder", lambda _: GradedEmbeddings())
    endpoint = "/api/bus/documents"
    assert client.post(endpoint, data={"title": "공개 절차", "type": "법령", "grade": "공개"},
                       files={"file": ("pub.txt", "증차 절차 공개본", "text/plain")}).status_code == 201
    for i in range(20):
        assert client.post(endpoint, data={"title": f"제한 {i}", "type": "내부자료", "grade": "제한"},
                           files={"file": (f"sec{i:02d}.txt", f"증차 절차 내부 검토 {i}", "text/plain")}).status_code == 201

    result = knowledge.ask("증차 절차", use_llm=False, role="citizen")
    assert result["found"] and [s["document"] for s in result["sources"]] == ["공개 절차"]
    # 관리자는 앞 순위의 제한 문서를 그대로 받음
    assert all(s["document"].startswith("제한") for s in knowledge.ask("증차 절차", use_llm=False, role="admin")["sources"])
    # 볼 수 있는 문서가 하나도 없는 역할은 색인 끝까지 봐도 빈 결과 (끝없이 돌지 않음)
    client.delete(f"{endpoint}/pub.txt")
    assert knowledge.search("증차 절차", "bge", 4, role="citizen") == []


def test_markdown_files_are_not_indexed_as_documents(tmp_path, monkeypatch):
    # 재현(#7): docs/bus/SOURCES.md(출처 목록)가 규정 문서로 색인돼 답변 근거로 뽑혔음
    _, root, knowledge = document_client(tmp_path, monkeypatch)
    (root / "law.txt").write_text("문서검증표지 본문", encoding="utf-8")
    (root / "SOURCES.md").write_text("문서검증표지 출처 목록", encoding="utf-8")
    before = rag.docs_fingerprint(root)
    assert {c["metadata"]["file"] for c in rag.load_documents(root)} == {"law.txt"}
    assert [h["document"] for h in knowledge.search("문서검증표지", "bge", 5)] == ["law"]
    (root / "NOTES.md").write_text("메모", encoding="utf-8")
    assert rag.docs_fingerprint(root) == before               # .md를 바꿔도 색인을 다시 만들지 않음
