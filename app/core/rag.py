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
    "당신은 시내버스 배차 담당자에게 운수 규정을 안내하는 한국어 어시스턴트입니다. 다음 규칙을 반드시 지키세요.\n"
    "1. [근거]에 적힌 내용만 사용하세요. 근거에 없는 숫자·기준·금액·기관·날짜는 절대 만들지 마세요.\n"
    "2. 질문의 대상(시내버스·시외버스·마을버스·택시, 서울·다른 지역 등)과 근거의 대상이 다르면 그 근거는 쓰지 마세요.\n"
    "3. 답변에 사용한 근거 번호를 문장 끝에 [1]처럼 표시하세요.\n"
    f"4. 질문에 직접 답하는 내용이 [근거]에 없으면 다른 설명 없이 정확히 '{NOT_FOUND_ANSWER}'라고만 쓰세요.\n"
    "5. 추론 과정은 쓰지 말고 최종 답변만 3~5문장으로 쓰세요."
)

# LLM이 형식을 어기고 "명시되지 않았습니다"처럼 돌려 말해도 근거 없음으로 처리
NOT_FOUND_HINTS = ("찾을 수 없", "명시되지 않", "언급되지 않", "나와 있지 않", "포함되어 있지 않",
                   "정보가 없", "확인할 수 없", "규정되어 있지 않", "제공되지 않")


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
            print(f"BGE-M3 임베딩 로딩: {BGE_EMBEDDING_MODEL_NAME} ({llm_module.get_device()})")
            embedder = HuggingFaceEmbeddings(
                model_name=BGE_EMBEDDING_MODEL_NAME,
                cache_folder=str(HF_CACHE_DIR),
                # bge-m3는 pytorch_model.bin만 있어 transformers가 safetensors 변환 스레드를 띄우고
                # 그 스레드 때문에 프로세스 종료가 멈추는 문제가 있어 변환을 끕니다.
                model_kwargs={"device": llm_module.get_device(), "model_kwargs": {"use_safetensors": False}},
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


# ------------------------------------------------
# 긴 조항 → 항(①) · 호(1.) · 목(가.) 단위 분할
# ------------------------------------------------
# 시행규칙 제33조처럼 시내버스·시외버스·택시 규정이 한 조항에 섞인 긴 조문을 통째로 주면
# 작은 LLM이 다른 제도의 숫자를 가져다 답하는 문제가 있었음 (예: 시내버스 질문에 시외버스 기준).
# 그래서 항 → 호 → 목 순서로 잘게 나누고, 각 조각 앞에 상위 문장(리드 문)을 붙여 문맥을 유지합니다.
CHUNK_VERSION = 2
CHUNK_LIMIT = 900
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
GANADA = "가나다라마바사아자차카타파하"


def _split_markers(text: str, kind: str):
    """번호가 1·2·3…(가·나·다…) 순서대로 나오는 지점만 경계로 인정 (날짜 '2014. 12.' 등 오인 방지)"""
    if kind == "hang":
        cands = [(m.start(), m.group(), CIRCLED.index(m.group())) for m in re.finditer(f"[{CIRCLED}]", text)]
        first = 0
    elif kind == "ho":
        cands = [(m.start(1), f"제{m.group(1)}호", int(m.group(1))) for m in re.finditer(r"(?:^|\s)(\d{1,2})\. (?=\S)", text)]
        first = 1
    else:
        cands = [(m.start(1), f"{m.group(1)}목", GANADA.index(m.group(1))) for m in re.finditer(f"(?:^|\\s)([{GANADA}])\\. ", text)]
        first = 0
    seq, expect = [], first
    for pos, label, n in cands:
        if n == expect:
            seq.append((pos, label))
            expect += 1
    if len(seq) < 2:
        return None
    lead = text[:seq[0][0]].strip()
    parts = [(lab, text[pos:(seq[i + 1][0] if i + 1 < len(seq) else len(text))].strip())
             for i, (pos, lab) in enumerate(seq)]
    return lead, parts


def split_hierarchy(text: str, levels=("hang", "ho", "mok"), crumbs=(), labels=()) -> list[tuple]:
    """→ [(labels, 상위 리드 문장들, 본문)]"""
    if len(text) <= CHUNK_LIMIT:
        return [(labels, crumbs, text)]
    if not levels:
        return [(labels, crumbs, part) for part in split_long(text, size=CHUNK_LIMIT)]
    found = _split_markers(text, levels[0])
    if found is None:
        return split_hierarchy(text, levels[1:], crumbs, labels)
    lead, parts = found
    sub_crumbs = crumbs + ((lead[:250],) if lead else ())
    out = []
    for lab, seg in parts:
        out += split_hierarchy(seg, levels[1:], sub_crumbs, labels + (lab,))
    return out


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
        body = re.sub(r"<[^>]*>", "", body)          # <개정 2019. 12. 26.> 같은 연혁 표기 제거
        for i, (labels, crumbs, part) in enumerate(split_hierarchy(body)):
            label = f"{art['article']} {' '.join(labels)}".strip()
            context = " … ".join(crumbs)
            chunks.append({
                "text": f"[{title}] {label}\n" + (f"(상위 문장) {context}\n" if context else "") + part,
                "metadata": {"document": title, "article": label, "page": art["page"],
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
    h = hashlib.md5(f"chunk-v{CHUNK_VERSION}".encode())
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

        # 같은 조항이 여러 Chunk로 검색되면 하나의 근거로 합침 (출처 중복 표시 방지)
        merged: dict[tuple, dict] = {}
        for h in relevant:
            key = (h["document"], h["article"])
            if key in merged:
                merged[key]["text"] += "\n" + h["text"].split("\n", 1)[-1]
            else:
                merged[key] = dict(h)
        relevant = list(merged.values())

        sources = [self._source(i, h) for i, h in enumerate(relevant, start=1)]

        # 2) LLM 없이 근거 조항만 필요한 경우 (예: 권고 카드에 빠르게 첨부)
        if not use_llm:
            return self._result(question, None, True, sources, None, embedding, t0)

        context = "\n\n".join(f"[{s['id']}] {h['text']}" for s, h in zip(sources, relevant))
        user = f"[근거]\n{context}\n\n[질문]\n{question.strip()}"
        answer = llm_module.generate(llm, SYSTEM_MESSAGE, user)

        # 3) LLM이 근거 부족으로 판단하면 출처 없이 "찾을 수 없음"
        if any(h in answer for h in NOT_FOUND_HINTS):
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
