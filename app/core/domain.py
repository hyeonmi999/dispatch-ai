# ==================================================
# 도메인 어댑터 인터페이스
# ==================================================
# 공통 계층(Forecaster / KnowledgeBase / Advisor)은 도메인을 모릅니다.
# 도메인별 차이(입력 스키마, 데이터, 권고 규칙)는 어댑터가 담당합니다.
#
# 새 도메인 추가 방법
#   1) app/domains/<name>/adapter.py 에 DomainAdapter 상속 클래스 작성
#   2) app/domains/__init__.py 의 register_all()에 한 줄 등록
#   3) docs/<name>/ 에 규정 문서(PDF·TXT) 추가
#   → 공통 계층과 API 라우터는 수정할 필요가 없습니다.

from abc import ABC, abstractmethod
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import DOCS_DIR
from app.core.rag import KnowledgeBase


class DomainNotImplemented(Exception):
    """아직 구현되지 않은 도메인 호출 시 발생 (API에서 501로 변환)"""

    def __init__(self, domain: str, feature: str):
        self.domain = domain
        self.feature = feature
        super().__init__(f"'{domain}' 도메인의 {feature} 기능은 아직 구현되지 않았습니다.")


class DomainAdapter(ABC):
    """도메인 어댑터 공통 인터페이스"""

    name: str = ""            # URL 경로에 쓰는 키 (예: bus)
    title: str = ""           # 화면 표시 이름
    description: str = ""
    implemented: bool = True

    # 도메인별 요청 스키마 (/api/{domain}/schema 로 공개)
    predict_schema: type[BaseModel] | None = None
    advise_schema: type[BaseModel] | None = None

    def __init__(self):
        # 규정 지식베이스는 docs/<domain>/ 폴더 기준으로 공통 KnowledgeBase를 재사용
        self.knowledge_base = KnowledgeBase(domain=self.name, docs_dir=DOCS_DIR / self.name)

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "status": "ready" if self.implemented else "not_implemented",
        }

    def schemas(self) -> dict[str, Any]:
        return {
            "predict": self.predict_schema.model_json_schema() if self.predict_schema else None,
            "advise": self.advise_schema.model_json_schema() if self.advise_schema else None,
        }

    @abstractmethod
    def predict(self, payload: dict) -> dict:
        """수요 예측"""

    @abstractmethod
    def advise(self, payload: dict, llm: str, embedding: str, use_llm: bool) -> dict:
        """예측 + 규정 근거를 결합한 배차 권고"""

    def router(self) -> APIRouter | None:
        """도메인 전용 부가 API (예: 버스 지도용 노선 좌표). 없으면 None"""
        return None

    def startup(self) -> None:
        """서버 시작 시 모델·데이터 로드"""


class NotImplementedDomain(DomainAdapter):
    """인터페이스와 스키마만 정의된 도메인 (1차 스프린트 범위 밖)"""

    implemented = False

    def predict(self, payload: dict) -> dict:
        raise DomainNotImplemented(self.name, "predict")

    def advise(self, payload: dict, llm: str, embedding: str, use_llm: bool) -> dict:
        raise DomainNotImplemented(self.name, "advise")
