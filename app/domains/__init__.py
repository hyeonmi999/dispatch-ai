# ==================================================
# 도메인 등록
# ==================================================
# 새 도메인을 추가할 때 이 함수에 한 줄만 추가하면 됩니다.

from app.core.registry import DomainRegistry


def register_all(registry: DomainRegistry) -> None:
    from app.domains.bus.adapter import BusAdapter
    from app.domains.field_service.adapter import FieldServiceAdapter
    from app.domains.logistics.adapter import LogisticsAdapter

    registry.register(BusAdapter())
    registry.register(LogisticsAdapter())
    registry.register(FieldServiceAdapter())
