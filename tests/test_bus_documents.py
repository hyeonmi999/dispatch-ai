import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.embeddings import Embeddings

from app.core import rag
from app.domains.bus.adapter import BusAdapter
from app.core.documents import read_document_manifest, managed_document_dir


@pytest.fixture(scope="module", autouse=True)
def sample_model():
    """문서 API 검증에는 버스 예측 모델 학습이 필요하지 않습니다."""
    yield


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
    client = TestClient(app)
    # 기존 테스트는 관리자 문서 관리 요청을 전제로 합니다.
    client.params = {"role": "admin"}
    return client, root, bus.knowledge_base


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
    assert (managed_document_dir(root) / "sample.txt").read_text(encoding="utf-8") == "문서검증표지"
    assert knowledge.ask("문서검증표지", use_llm=False)["sources"][0]["document"] == fields["title"]

    path = f"{endpoint}/sample.txt"
    response = client.put(path, json={**fields, "title": "새 제목", "grade": "내부", "active": False})
    assert response.status_code == 200, response.text
    assert read_document_manifest(managed_document_dir(root))["sample.txt"]["grade"] == "내부"
    assert not knowledge.ask("문서검증표지", use_llm=False)["found"]

    response = client.put(path, json={**fields, "title": "새 제목", "active": True})
    assert response.status_code == 200
    assert knowledge.ask("문서검증표지", use_llm=False)["sources"][0]["document"] == "새 제목"
    assert client.delete(path).status_code == 204
    assert not (managed_document_dir(root) / "sample.txt").exists()
    assert "sample.txt" not in read_document_manifest(managed_document_dir(root))
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


def test_grade_and_active_changes_reuse_index(tmp_path, monkeypatch):
    client, root, kb = document_client(tmp_path, monkeypatch)
    fields = {"title": "빠른 변경", "type": "내부자료", "grade": "공개"}
    client.post("/api/bus/documents", data=fields,
                files={"file": ("speed.txt", "문서검증표지", "text/plain")})
    assert kb.search("문서검증표지", "bge")
    fingerprint = rag.docs_fingerprint(root)
    store = kb.get_store("bge")
    for active, grade in ((False, "공개"), (True, "제한"), (True, "공개")):
        response = client.put("/api/bus/documents/speed.txt",
                              json={**fields, "active": active, "grade": grade})
        assert response.status_code == 200
        assert rag.docs_fingerprint(root) == fingerprint
        assert kb.get_store("bge") is store
        assert bool(kb.search("문서검증표지", "bge")) == (active and grade == "공개")
    changed = client.put("/api/bus/documents/speed.txt",
                         json={**fields, "active": True, "title": "제목 수정"})
    assert changed.status_code == 200
    assert rag.docs_fingerprint(root) != fingerprint


def test_management_permissions_storage_and_basic_protection(tmp_path, monkeypatch):
    client, root, _ = document_client(tmp_path, monkeypatch)
    (root / "basic.txt").write_text("기본 법규", encoding="utf-8")
    endpoint = "/api/bus/documents"
    fields = {"title": "비공개", "type": "내부자료", "grade": "제한"}
    assert client.post(endpoint, data=fields,
                       files={"file": ("private.txt", "문서검증표지", "text/plain")}).status_code == 201
    assert not (root / "private.txt").exists()
    assert (managed_document_dir(root) / "private.txt").exists()
    assert client.delete(endpoint + "/basic.txt").status_code == 403
    assert (root / "basic.txt").exists()
    for role in ("citizen", "staff"):
        assert [item["file"] for item in client.get(endpoint, params={"role": role}).json()["items"]] == ["basic.txt"]
        assert client.post(endpoint, params={"role": role}, data=fields,
                           files={"file": ("denied.txt", "내용", "text/plain")}).status_code == 403
        assert client.put(endpoint + "/private.txt", params={"role": role},
                          json={**fields, "active": True}).status_code == 403
        assert client.delete(endpoint + "/private.txt", params={"role": role}).status_code == 403
    client.params = {}
    assert client.delete(endpoint + "/private.txt").status_code == 403
    assert len(client.get(endpoint, params={"role": "admin", "limit": 1}).json()["items"]) == 1
    assert client.get(endpoint, params={"role": "admin", "limit": 10001}).status_code == 422


def test_create_and_update_invalid_fields_use_same_status(tmp_path, monkeypatch):
    client, _, _ = document_client(tmp_path, monkeypatch)
    fields = {"title": "검증", "type": "법령", "grade": "공개"}
    client.post("/api/bus/documents", data=fields,
                files={"file": ("sample.txt", "내용", "text/plain")})
    invalid = {**fields, "grade": "없는 등급"}
    created = client.post("/api/bus/documents", data=invalid,
                          files={"file": ("invalid.txt", "내용", "text/plain")})
    updated = client.put("/api/bus/documents/sample.txt", json={**invalid, "active": True})
    assert created.status_code == updated.status_code == 422


def test_disabled_document_is_indexed_once_and_can_be_enabled(tmp_path, monkeypatch):
    client, root, kb = document_client(tmp_path, monkeypatch)
    fields = {"title": "처음부터 비활성", "type": "법령", "grade": "공개"}
    endpoint = "/api/bus/documents"
    client.post(endpoint, data=fields, files={"file": ("inactive.txt", "문서검증표지", "text/plain")})
    client.put(endpoint + "/inactive.txt", json={**fields, "active": False})
    assert not kb.search("문서검증표지", "bge")
    cached = kb.get_store("bge")
    assert cached.index.ntotal == 1
    client.put(endpoint + "/inactive.txt", json={**fields, "active": True})
    assert kb.get_store("bge") is cached
    assert kb.search("문서검증표지", "bge")[0]["document"] == fields["title"]


def test_legacy_manifest_is_preserved_and_local_override_wins(tmp_path, monkeypatch):
    import json
    client, root, kb = document_client(tmp_path, monkeypatch)
    (root / "legacy.txt").write_text("문서검증표지", encoding="utf-8")
    fields = {"title": "이전 제목", "type": "법령", "grade": "제한", "active": True, "created_at": None}
    manifest = root / "documents.json"
    manifest.write_text(json.dumps({"legacy.txt": fields}, ensure_ascii=False), encoding="utf-8")
    original = manifest.read_bytes()
    assert not kb.search("문서검증표지", "bge", role="citizen")
    assert kb.search("문서검증표지", "bge", role="admin")
    response = client.put("/api/bus/documents/legacy.txt", json={**fields, "title": "로컬 제목", "grade": "공개"})
    assert response.status_code == 200
    assert response.json()["deletable"] is False
    assert manifest.read_bytes() == original
    assert kb.search("문서검증표지", "bge")[0]["document"] == "로컬 제목"


def test_index_status_tracks_content_changes_not_policy(tmp_path, monkeypatch):
    from app.api import routes
    client, root, knowledge = document_client(tmp_path, monkeypatch)
    client.app.include_router(routes.router)
    bus = BusAdapter()
    bus.knowledge_base = knowledge
    monkeypatch.setattr(routes, "get_domain", lambda _: bus)
    endpoint = "/api/bus/knowledge-status"
    assert client.get(endpoint).json() == {"rebuild_required": False}
    fields = {"title": "상태 확인", "type": "법령", "grade": "공개"}
    client.post("/api/bus/documents", data=fields,
                files={"file": ("status.txt", "문서검증표지", "text/plain")})
    assert client.get(endpoint).json() == {"rebuild_required": True}
    knowledge.search("문서검증표지", "bge")
    assert client.get(endpoint).json() == {"rebuild_required": False}
    client.put("/api/bus/documents/status.txt", json={**fields, "grade": "제한", "active": False})
    assert client.get(endpoint).json() == {"rebuild_required": False}
    client.put("/api/bus/documents/status.txt", json={**fields, "title": "제목 변경", "active": True})
    assert client.get(endpoint).json() == {"rebuild_required": True}
    assert client.get(endpoint, params={"embedding": "invalid"}).status_code == 422



def test_reupload_same_file_identity_does_not_reuse_old_content(tmp_path, monkeypatch):
    import os
    client, root, kb = document_client(tmp_path, monkeypatch)
    endpoint = "/api/bus/documents"
    fields = {"title": "자료", "type": "내부자료", "grade": "제한"}
    before = "문서검증표지 비밀내용A"
    after = "문서검증표지 공개내용B"
    assert len(before.encode()) == len(after.encode())
    assert client.post(endpoint, data=fields, files={"file": ("a.txt", before, "text/plain")}).status_code == 201
    assert before in kb.search("문서검증표지", "bge", role="admin")[0]["text"]
    path = managed_document_dir(root) / "a.txt"
    stamp = path.stat()
    assert client.delete(endpoint + "/a.txt").status_code == 204
    assert client.post(endpoint, data={**fields, "grade": "공개"},
                       files={"file": ("a.txt", after, "text/plain")}).status_code == 201
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    # ns 수정 시각까지 같아도 삭제한 제한 문서 내용이 노출되면 안 됩니다.
    hits = kb.search("문서검증표지", "bge", role="citizen")
    assert after in hits[0]["text"]
    assert all(before not in hit["text"] for hit in hits)


@pytest.mark.parametrize("name", ["same.txt", "SAME.TXT"])
def test_duplicate_files_do_not_break_search_or_status(tmp_path, monkeypatch, name):
    client, root, kb = document_client(tmp_path, monkeypatch)
    fields = {"title": "자료", "type": "법령", "grade": "공개"}
    assert client.post("/api/bus/documents", data=fields,
                       files={"file": ("same.txt", "문서검증표지 업로드", "text/plain")}).status_code == 201
    assert kb.search("문서검증표지", "bge")
    (root / name).write_text("문서검증표지 기본 파일", encoding="utf-8")
    (root / "other.txt").write_text("문서검증표지 정상 자료", encoding="utf-8")
    # 관리는 충돌을 알리고 검색은 등급이 모호한 양쪽 파일을 모두 제외합니다.
    assert client.get("/api/bus/documents").status_code == 409
    hits = kb.search("문서검증표지", "bge")
    assert len(hits) == 1 and hits[0]["document"] == "other"
    assert kb.status()["documents"] == 1
    assert kb.has_docs()
    assert not kb.index_needs_rebuild("bge")
    from app.api import routes
    from types import SimpleNamespace
    client.app.include_router(routes.router)
    monkeypatch.setattr(routes, "llm_status", lambda: {})
    monkeypatch.setattr(routes.registry, "all", lambda: [SimpleNamespace(info=kb.status)])
    assert client.get("/api/health").status_code == 200
