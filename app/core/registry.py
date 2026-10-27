# ==================================================
# 도메인 레지스트리
# ==================================================
# 어댑터를 이름으로 등록·조회합니다.
# API 라우터는 레지스트리만 보고 동작하므로 도메인이 늘어나도 수정이 필요 없습니다.

from app.core.domain import DomainAdapter


class DomainRegistry:
    def __init__(self):
        self._domains: dict[str, DomainAdapter] = {}

    def register(self, adapter: DomainAdapter) -> None:
        if adapter.name in self._domains:
            raise ValueError(f"이미 등록된 도메인입니다: {adapter.name}")
        self._domains[adapter.name] = adapter

    def get(self, name: str) -> DomainAdapter | None:
        return self._domains.get(name)

    def all(self) -> list[DomainAdapter]:
        return list(self._domains.values())


registry = DomainRegistry()
