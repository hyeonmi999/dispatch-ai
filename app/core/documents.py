"""버스 법규 문서 파일과 등록 정보 관리."""

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

MANIFEST_NAME = "documents.json"
DOCUMENT_SUFFIXES = {".pdf", ".txt"}
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

logger = logging.getLogger(__name__)


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


class DocumentList(BaseModel):
    items: list[DocumentInfo]


def read_document_manifest(root: Path) -> dict:
    try:
        return json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


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


class BusDocuments:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.Lock()

    def list(self) -> DocumentList:
        with self.lock:
            records = read_document_manifest(self.root)
            items = []
            for path in sorted(self.root.iterdir()) if self.root.is_dir() else []:
                if path.is_symlink() or not path.is_file() or path.suffix.lower() not in DOCUMENT_SUFFIXES:
                    continue
                item = records.get(path.name, {
                    "title": path.stem, "type": _legacy_type(path.name), "grade": "공개",
                    "active": True, "created_at": None,
                })
                items.append(DocumentInfo(file=path.name, **item))
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
            if any(p.name.casefold() == path.name.casefold() for p in self.root.iterdir()):
                raise HTTPException(409, "같은 이름의 문서가 이미 있습니다.")
            item = DocumentInfo(file=path.name, **fields.model_dump(), active=True, created_at=datetime.now(timezone.utc))
            with path.open("xb") as stream:
                try:
                    stream.write(data)
                    stream.close()
                    records[path.name] = item.model_dump(mode="json", exclude={"file"})
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
            item = DocumentInfo(file=path.name, **fields.model_dump(),
                                created_at=records.get(path.name, {}).get("created_at"))
            records[path.name] = item.model_dump(mode="json", exclude={"file"})
            write_document_manifest(self.root, records)
            return item

    def delete(self, name: str) -> None:
        with self.lock:
            path = self._existing(name)
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
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        return path
