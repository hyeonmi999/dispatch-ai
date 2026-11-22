# docs/contracts.md의 예시와 실제 응답 스키마가 1:1인지 확인 (규격 문서가 코드와 어긋나지 않게)
import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.domains.bus import schemas as S

CONTRACTS = Path(__file__).resolve().parents[1] / "docs" / "contracts.md"


def _example_keys(title: str) -> set[str]:
    """제목 아래 첫 번째 코드 블록(요청·응답 예시)에 나오는 필드 이름"""
    text = CONTRACTS.read_text(encoding="utf-8")
    start = text.index(title)
    open_ = text.index("```", start)
    close = text.index("```", open_ + 3)
    return set(re.findall(r'"([a-z_]+)":', text[open_ + 3:close]))


def _schema_fields(model: type[BaseModel]) -> set[str]:
    """모델의 필드 이름 (안에 든 모델의 필드까지)"""
    out = set()
    for name, field in model.model_fields.items():
        out.add(name)
        ann = field.annotation
        for sub in (*getattr(ann, "__args__", ()), ann):
            if isinstance(sub, type) and issubclass(sub, BaseModel):
                out |= _schema_fields(sub)
    return out


@pytest.mark.parametrize("title, request_model, response_model", [
    ("## 1. `POST /api/bus/simulate`", S.BusSimulateRequest, S.BusSimulateResponse),
    ("## 2. `POST /api/bus/optimize`", S.BusOptimizeRequest, S.BusOptimizeResponse),
])
def test_contract_example_matches_schema(title, request_model, response_model):
    documented = _example_keys(title)
    actual = _schema_fields(request_model) | _schema_fields(response_model)

    assert documented - actual == set(), "문서에만 있는 필드 (스키마에 없음)"
    assert actual - documented == set(), "스키마에만 있는 필드 (문서 예시에 없음)"
