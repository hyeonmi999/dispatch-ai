# ==================================================
# 규정 지식베이스 (RAG)
# ==================================================
# docs/<domain>/ 의 PDF·TXT → 조항 단위 Chunk → Embedding → FAISS
#
# 처리 흐름
#   문서 로드 (PDF: 정렬 추출, 목차·부칙 제외 / TXT: 문서명·출처 헤더)
#     ↓
#   조항(제N조) 단위 분할 → 긴 조항은 다시 800자 단위로 분할
#     ↓
#   Embedding (bge-m3 기본 / text-embedding-3-small 선택)
#     ↓
#   FAISS (내적 = 코사인 유사도) 저장: vectorstore/<domain>/<embedding>/
#
# 질의
#   검색 → 유사도 임계값 미만이면 LLM 호출 없이 "찾을 수 없음"
#   → 근거 Context만으로 LLM 답변 → 출처(문서명·조항) 함께 반환

import hashlib
import json
import re
import threading
import time
from pathlib import Path

from app.config import (
    BGE_EMBEDDING_MODEL_NAME,
    HF_CACHE_DIR,
    NOT_FOUND_ANSWER,
    OPENAI_API_KEY,
    OPENAI_EMBEDDING_MODEL_NAME,
    RAG_SCORE_THRESHOLD,
    RAG_TOP_K,
    VECTORSTORE_DIR,
)
from app.core import llm as llm_module
from app.core.llm import LLMConfigError

EMBEDDING_CHOICES = ("bge", "openai")

SYSTEM_MESSAGE = (
    "당신은 대중교통 운영 규정을 안내하는 한국어 AI 어시스턴트입니다. "
    "반드시 제공된 [근거]에 있는 내용만으로 답변하세요. "
    "[근거]에 없는 내용은 추측하거나 일반 지식을 사용하지 마세요. "
    "답변 문장 끝에 사용한 근거 번호를 [1]처럼 표시하세요. "
    f"질문과 관련된 내용이 [근거]에 없으면 정확히 '{NOT_FOUND_ANSWER}'라고만 답변하세요. "
    "추론 과정은 출력하지 말고 최종 답변만 3~5문장 이내로 제시하세요."
)


# ==================================================
# 1. Embedding 모델 (한 번만 로드)
# ==================================================

_embedders = {}
_embed_lock = threading.Lock()


def get_embedder(name: str):
    if name not in EMBEDDING_CHOICES:
        raise LLMConfigError(f"지원하지 않는 임베딩입니다: {name} (선택: {', '.join(EMBEDDING_CHOICES)})")

    with _embed_lock:
        if name in _embedders:
            return _embedders[name]

        if name == "openai":
            if not OPENAI_API_KEY:
                raise LLMConfigError(
                    "OPENAI_API_KEY가 설정되지 않아 text-embedding-3-small을 사용할 수 없습니다. "
                    "임베딩을 bge로 선택하세요."
                )
            from langchain_openai import OpenAIEmbeddings
            embedder = OpenAIEmbeddings(model=OPENAI_EMBEDDING_MODEL_NAME, api_key=OPENAI_API_KEY)
        else:
            from langchain_huggingface import HuggingFaceEmbeddings
            print(f"BGE-M3 임베딩 로딩: {BGE_EMBEDDING_MODEL_NAME} ({llm_module.DEVICE})")
            embedder = HuggingFaceEmbeddings(
                model_name=BGE_EMBEDDING_MODEL_NAME,
                cache_folder=str(HF_CACHE_DIR),
                model_kwargs={"device": llm_module.DEVICE},
                encode_kwargs={"normalize_embeddings": True},
            )
        _embedders[name] = embedder
        return embedder


# ==================================================
# 2. 문서 로드 + 조항 단위 분할
# ==================================================

ARTICLE_RE = re.compile(r"^\s*(제\s?\d+\s?조(?:의\s?\d+)?)\s*(\([^)]*\))?\s*(.*)$")
PAGE_HEADER_RE = re.compile(r"^\s*법제처\s+\d+\s+국가법령정보센터\s*$")
ADDENDA_RE = re.compile(r"^\s*부\s*칙")


def clean_title(stem: str) -> str:
    """'여객자동차 운수사업법 시행규칙(국토교통부령)(제01621호)(20260915)' → '여객자동차 운수사업법 시행규칙'"""
    return re.sub(r"\(.*$", "", stem).strip()


def split_long(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    if len(text) <= size:
        return [text]
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    splitter = RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=overlap, separators=["\n", ". ", " ", ""])
    return splitter.split_text(text)


def load_pdf_articles(path: Path) -> list[dict]:
    """법령·조례 PDF를 조항 단위로 분할합니다.

    - 목차의 '제N조(제목)'만 있는 줄은 본문이 없으므로 조항 시작으로 보지 않음
    - '부칙' 이후는 제외 (부칙에도 제1조가 다시 나와 검색을 혼동시킴)
    """
    import pymupdf

    title = clean_title(path.stem)
    lines = []
    with pymupdf.open(path) as doc:
        for page_no, page in enumerate(doc, start=1):
            for line in page.get_text(sort=True).splitlines():
                if PAGE_HEADER_RE.match(line) or line.strip() == title:
                    continue
                lines.append((page_no, line.rstrip()))

    articles, current = [], None
    for page_no, line in lines:
        m = ARTICLE_RE.match(line)
        if m and m.group(3).strip():
            if current:
                articles.append(current)
            label = re.sub(r"\s", "", m.group(1)) + (m.group(2) or "")
            current = {"article": label, "page": page_no, "lines": [m.group(3).strip()]}
            continue
        if current and ADDENDA_RE.match(line):
            articles.append(current)
            current = None
            break
        if current and line.strip():
            current["lines"].append(line.strip())
    if current:
        articles.append(current)

    chunks = []
    for art in articles:
        body = re.sub(r"\s+", " ", " ".join(art["lines"])).strip()
        for i, part in enumerate(split_long(body)):
            chunks.append({
                "text": f"[{title}] {art['article']}\n{part}",
                "metadata": {"document": title, "article": art["article"], "page": art["page"],
                             "part": i, "file": path.name, "url": ""},
            })
    return chunks


def load_txt(path: Path) -> list[dict]:
    """보도자료 TXT: 상단 '문서명/출처/게시일' 헤더 + 본문"""
    raw = path.read_text(encoding="utf-8")
    meta = dict(re.findall(r"^(문서명|출처|게시일):\s*(.*)$", raw, re.M))
    body = re.sub(r"^(문서명|출처|게시일|수집일):.*\n", "", raw, flags=re.M).strip()
    title = meta.get("문서명", path.stem)

    chunks = []
    for i, part in enumerate(split_long(body, size=700, overlap=100)):
        chunks.append({
            "text": f"[{title}]\n{part}",
            "metadata": {"document": title, "article": f"본문 {i + 1}", "page": None, "part": i,
                         "file": path.name, "url": meta.get("출처", ""), "date": meta.get("게시일", "")},
        })
    return chunks


def load_documents(docs_dir: Path) -> list[dict]:
    chunks = []
    for path in sorted(docs_dir.glob("*")):
        if path.suffix.lower() == ".pdf":
            chunks += load_pdf_articles(path)
        elif path.suffix.lower() in {".txt", ".md"} and path.name.lower() != "readme.md":
            chunks += load_txt(path)
    return chunks


def docs_fingerprint(docs_dir: Path) -> str:
    h = hashlib.md5()
    for path in sorted(docs_dir.glob("*")):
        if path.suffix.lower() in {".pdf", ".txt", ".md"}:
            h.update(f"{path.name}:{path.stat().st_size}:{int(path.stat().st_mtime)}".encode())
    return h.hexdigest()


# ==================================================
# 3. KnowledgeBase (도메인 공통)
# ==================================================

class KnowledgeBase:
    def __init__(self, domain: str, docs_dir: Path):
        self.domain = domain
        self.docs_dir = docs_dir
        self._stores = {}
        self._lock = threading.Lock()

    def index_dir(self, embedding: str) -> Path:
        return VECTORSTORE_DIR / self.domain / embedding

    def has_docs(self) -> bool:
        return self.docs_dir.exists() and any(
            p.suffix.lower() in {".pdf", ".txt"} for p in self.docs_dir.glob("*")
        )

    def status(self) -> dict:
        out = {"docs_dir": str(self.docs_dir), "documents": 0, "indexes": {}}
        if self.docs_dir.exists():
            out["documents"] = len([p for p in self.docs_dir.glob("*") if p.suffix.lower() in {".pdf", ".txt"}])
        for emb in EMBEDDING_CHOICES:
            manifest = self.index_dir(emb) / "manifest.json"
            out["indexes"][emb] = json.loads(manifest.read_text()) if manifest.exists() else None
        return out

    # ------------------------------------------------
    # 인덱스 생성 / 로드
    # ------------------------------------------------

    def build(self, embedding: str):
        from langchain_community.vectorstores import FAISS
        from langchain_community.vectorstores.utils import DistanceStrategy
        from langchain_core.documents import Document

        chunks = load_documents(self.docs_dir)
        if not chunks:
            raise FileNotFoundError(f"인덱싱할 문서가 없습니다: {self.docs_dir}")

        t0 = time.time()
        documents = [Document(page_content=c["text"], metadata=c["metadata"]) for c in chunks]
        store = FAISS.from_documents(
            documents, get_embedder(embedding), distance_strategy=DistanceStrategy.MAX_INNER_PRODUCT,
        )
        out = self.index_dir(embedding)
        out.mkdir(parents=True, exist_ok=True)
        store.save_local(str(out))
        manifest = {
            "embedding": embedding,
            "chunks": len(documents),
            "files": sorted({c["metadata"]["file"] for c in chunks}),
            "fingerprint": docs_fingerprint(self.docs_dir),
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        print(f"✅ [{self.domain}/{embedding}] FAISS 인덱스 생성: {len(documents)} chunks ({time.time() - t0:.0f}s)")
        return store

    def get_store(self, embedding: str):
        """인덱스가 없거나 docs가 바뀌었으면 다시 만들고, 있으면 로드합니다."""
        from langchain_community.vectorstores import FAISS
        from langchain_community.vectorstores.utils import DistanceStrategy

        with self._lock:
            if embedding in self._stores:
                return self._stores[embedding]

            out = self.index_dir(embedding)
            manifest = out / "manifest.json"
            fresh = manifest.exists() and json.loads(manifest.read_text()).get("fingerprint") == docs_fingerprint(self.docs_dir)

            if fresh:
                # index.pkl은 이 프로젝트에서 직접 생성한 파일만 로드합니다 (pickle 역직렬화)
                store = FAISS.load_local(
                    str(out), get_embedder(embedding), allow_dangerous_deserialization=True,
                    distance_strategy=DistanceStrategy.MAX_INNER_PRODUCT,
                )
            else:
                store = self.build(embedding)
            self._stores[embedding] = store
            return store

    # ------------------------------------------------
    # 검색 / 질의응답
    # ------------------------------------------------

    def search(self, query: str, embedding: str, k: int = RAG_TOP_K) -> list[dict]:
        store = self.get_store(embedding)
        hits = store.similarity_search_with_score(query, k=k)
        return [
            {
                "document": doc.metadata.get("document"),
                "article": doc.metadata.get("article"),
                "page": doc.metadata.get("page"),
                "url": doc.metadata.get("url") or None,
                "score": round(float(score), 4),
                "text": doc.page_content,
            }
            for doc, score in hits
        ]

    def ask(self, question: str, llm: str = "qwen", embedding: str = "bge",
            k: int = RAG_TOP_K, use_llm: bool = True) -> dict:
        t0 = time.time()
        if not self.has_docs():
            return self._result(question, NOT_FOUND_ANSWER, False, [], llm, embedding, t0,
                                note=f"docs/{self.domain}/ 에 규정 문서가 없습니다.")

        hits = self.search(question, embedding, k)
        threshold = RAG_SCORE_THRESHOLD[embedding]
        relevant = [h for h in hits if h["score"] >= threshold]

        # 1) 검색 단계에서 근거가 없으면 LLM을 부르지 않고 바로 "찾을 수 없음"
        if not relevant:
            best = hits[0]["score"] if hits else None
            return self._result(question, NOT_FOUND_ANSWER, False, [], llm, embedding, t0,
                                note=f"유사도 임계값({threshold}) 이상 근거 없음 (최고 {best})")

        sources = [self._source(i, h) for i, h in enumerate(relevant, start=1)]

        # 2) LLM 없이 근거 조항만 필요한 경우 (예: 권고 카드에 빠르게 첨부)
        if not use_llm:
            return self._result(question, None, True, sources, None, embedding, t0)

        context = "\n\n".join(f"[{s['id']}] {h['text']}" for s, h in zip(sources, relevant))
        user = f"[근거]\n{context}\n\n[질문]\n{question.strip()}"
        answer = llm_module.generate(llm, SYSTEM_MESSAGE, user)

        # 3) LLM이 근거 부족으로 판단하면 출처 없이 "찾을 수 없음"
        if NOT_FOUND_ANSWER in answer or "찾을 수 없" in answer:
            return self._result(question, NOT_FOUND_ANSWER, False, [], llm, embedding, t0,
                                note="LLM이 근거 문서에서 답을 찾지 못함")

        return self._result(question, answer, True, sources, llm, embedding, t0)

    @staticmethod
    def _source(i: int, hit: dict) -> dict:
        snippet = hit["text"].split("\n", 1)[-1]
        return {
            "id": i,
            "document": hit["document"],
            "article": hit["article"],
            "page": hit["page"],
            "url": hit["url"],
            "score": hit["score"],
            "snippet": snippet[:300] + ("…" if len(snippet) > 300 else ""),
        }

    @staticmethod
    def _result(question, answer, found, sources, llm, embedding, t0, note=None) -> dict:
        return {
            "question": question,
            "answer": answer,
            "found": found,
            "sources": sources,
            "llm": llm,
            "embedding": embedding,
            "elapsed_sec": round(time.time() - t0, 2),
            "note": note,
        }
