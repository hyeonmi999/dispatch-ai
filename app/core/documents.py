"""법규 문서 파일과 등록 정보 관리."""

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from tempfile import NamedTemporaryFile
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from app.config import (
    DOCS_DIR, UPLOADED_DOCS_DIR, MAX_DOCUMENT_BYTES, ROLE_GRADES, DOCUMENT_LIST_LIMIT,
    RAG_DOCUMENT_SUFFIXES,
)

MANIFEST_NAME = "documents.json"
DOCUMENT_SUFFIXES = set(RAG_DOCUMENT_SUFFIXES)

logger = logging.getLogger(__name__)
_duplicate_states = {}
_duplicate_state_lock = threading.Lock()


class DocumentFields(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    type: Literal["법령", "조례", "보도자료", "내부자료"]
    grade: Literal["공개", "내부", "제한"]

    @field_validator("title", mode="before")
    @classmethod
    def trim_title(cls, title):
        return title.strip() if isinstance(title, str) else title


class DocumentUpdate(DocumentFields):
    active: bool


class DocumentInfo(DocumentUpdate):
    file: str
    created_at: datetime | None
    deletable: bool = True


class DocumentList(BaseModel):
    items: list[DocumentInfo]


def read_document_manifest(root: Path) -> dict:
    try:
        records = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
        if not isinstance(records, dict) or not all(isinstance(value, dict) for value in records.values()):
            raise ValueError("문서 목록 형식 오류")
        return records
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.exception("문서 목록을 읽지 못했습니다.")
        raise HTTPException(500, "문서 목록을 읽지 못했습니다. 파일을 확인해 주세요.") from exc


def managed_document_dir(root: Path) -> Path:
    """기본 법규는 저장소에, 업로드와 운영 목록은 Git 제외 폴더에 둡니다."""
    if root.resolve().parent == DOCS_DIR.resolve():
        return UPLOADED_DOCS_DIR / root.name
    return root / ".uploads"


def document_records(root: Path) -> dict:
    # 이전 버전의 목록도 읽고 운영 목록의 수정 사항을 우선합니다.
    return {**read_document_manifest(root), **read_document_manifest(managed_document_dir(root))}


def document_files(root: Path, *, strict: bool = True, reveal_duplicates: bool = True) -> list[Path]:
    files = {}
    duplicates = set()
    duplicate_names = set()
    for folder in (root, managed_document_dir(root)):
        for path in sorted(folder.glob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in DOCUMENT_SUFFIXES:
                key = path.name.casefold()
                if key in files:
                    duplicates.add(key)
                    duplicate_names.update((files[key].name, path.name))
                else:
                    files[key] = path
    signature = tuple(sorted(duplicate_names))
    root_key = root.resolve()
    with _duplicate_state_lock:
        if signature:
            if _duplicate_states.get(root_key) != signature:
                logger.warning("중복 문서를 검색에서 제외했습니다: %s", ", ".join(signature))
                _duplicate_states[root_key] = signature
        else:
            _duplicate_states.pop(root_key, None)
    if duplicates and strict:
        detail = "기본 문서와 업로드 문서의 파일명이 중복됩니다."
        if reveal_duplicates:
            detail += " 중복 파일: " + ", ".join(signature) + ". 폴더에서 이름 충돌을 해소해 주세요."
        raise HTTPException(409, detail)
    # 어느 파일의 등급인지 모호하므로 둘 다 검색에서 제외합니다.
    return sorted((path for key, path in files.items() if key not in duplicates),
                  key=lambda path: path.name)


def require_document_admin(role: str) -> None:
    if role != "admin":
        raise HTTPException(403, "문서 변경은 관리자 역할에서만 가능합니다.")


def write_document_manifest(root: Path, records: dict) -> None:
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(records, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        except Exception:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, root / MANIFEST_NAME)
    finally:
        temporary.unlink(missing_ok=True)


def document_path(root: Path, name: str) -> Path:
    name = name.rstrip(" .")
    path = root / name
    if (
        not name or len(name.encode("utf-8")) > 240
        or re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', name)
        or PureWindowsPath(name).is_reserved()
        or path.suffix.lower() not in DOCUMENT_SUFFIXES
        or path.is_symlink()
        or path.resolve().parent != root.resolve()
    ):
        raise HTTPException(400, "안전한 이름의 PDF 또는 TXT 파일을 선택해 주세요.")
    return path


def _legacy_type(name: str) -> str:
    if "조례" in name:
        return "조례"
    if name.startswith("서울시보도자료"):
        return "보도자료"
    return "법령" if name.lower().endswith(".pdf") else "내부자료"


class Documents:
    def __init__(self, root: Path):
        self.base = root
        self.root = managed_document_dir(root)
        self.lock = threading.Lock()

    def list(self, role: str = "citizen", limit: int = DOCUMENT_LIST_LIMIT) -> DocumentList:
        with self.lock:
            records = document_records(self.base)
            items = []
            for path in document_files(self.base, reveal_duplicates=role == "admin"):
                if path.is_symlink() or not path.is_file() or path.suffix.lower() not in DOCUMENT_SUFFIXES:
                    continue
                item = records.get(path.name, {
                    "title": path.stem, "type": _legacy_type(path.name), "grade": "공개",
                    "active": True, "created_at": None,
                })
                if item.get("grade", "공개") in ROLE_GRADES[role]:
                    items.append(DocumentInfo(file=path.name, deletable=path.parent == self.root, **item))
                    if len(items) == limit:
                        break
            return DocumentList(items=items)

    def create(self, name: str, fields: DocumentFields, data: bytes) -> DocumentInfo:
        path = document_path(self.root, name)
        if not data or len(data) > MAX_DOCUMENT_BYTES:
            raise HTTPException(400, "파일은 비어 있지 않아야 하며 최대 20MB까지 올릴 수 있습니다.")
        try:
            if path.suffix.lower() == ".txt":
                if not data.decode("utf-8-sig").strip() or b"\x00" in data:
                    raise ValueError("Invalid TXT")
            else:
                import pymupdf
                with pymupdf.open(stream=data, filetype="pdf") as pdf:
                    if not data.startswith(b"%PDF-") or not pdf.page_count or pdf.needs_pass:
                        raise ValueError("Invalid PDF")
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, "읽을 수 있는 PDF 또는 UTF-8 TXT 파일을 선택해 주세요.") from exc

        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            records = read_document_manifest(self.root)
            if any(p.name.casefold() == path.name.casefold() for p in document_files(self.base)):
                raise HTTPException(409, "같은 이름의 문서가 이미 있습니다.")
            item = DocumentInfo(file=path.name, **fields.model_dump(), active=True, created_at=datetime.now(timezone.utc))
            with path.open("xb") as stream:
                try:
                    stream.write(data)
                    stream.close()
                    records[path.name] = item.model_dump(mode="json", exclude={"file", "deletable"})
                    write_document_manifest(self.root, records)
                except Exception:
                    stream.close()
                    path.unlink(missing_ok=True)
                    raise
            return item

    def update(self, name: str, fields: DocumentUpdate) -> DocumentInfo:
        with self.lock:
            path = self._existing(name)
            records = read_document_manifest(self.root)
            previous = document_records(self.base).get(path.name, {})
            self.root.mkdir(parents=True, exist_ok=True)
            item = DocumentInfo(file=path.name, **fields.model_dump(),
                                deletable=path.parent == self.root, created_at=previous.get("created_at"))
            records[path.name] = item.model_dump(mode="json", exclude={"file", "deletable"})
            write_document_manifest(self.root, records)
            return item

    def delete(self, name: str) -> None:
        with self.lock:
            path = self._existing(name)
            if path.parent != self.root:
                raise HTTPException(403, "기본 법규 문서는 삭제할 수 없습니다. 사용 여부를 변경해 주세요.")
            records = read_document_manifest(self.root)
            with NamedTemporaryFile(dir=self.root, delete=False) as stream:
                temporary = Path(stream.name)
            try:
                path.replace(temporary)
            except OSError:
                temporary.unlink(missing_ok=True)
                raise
            try:
                records.pop(path.name, None)
                write_document_manifest(self.root, records)
            except Exception:
                temporary.replace(path)
                raise
            try:
                temporary.unlink()
            except OSError:
                logger.exception("삭제한 법규 문서의 임시 파일을 제거하지 못했습니다.")

    def _existing(self, name: str) -> Path:
        path = document_path(self.root, name)
        if not path.is_file():
            path = document_path(self.base, name)
        if not path.is_file():
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        return path
