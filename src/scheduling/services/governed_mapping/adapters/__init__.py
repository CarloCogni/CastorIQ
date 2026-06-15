# scheduling/services/governed_mapping/adapters/__init__.py
"""Source adapters for governed mapping population (DF-D2)."""

from scheduling.services.governed_mapping.adapters.activity_type import ActivityTypeSourceAdapter
from scheduling.services.governed_mapping.adapters.sub_stage_trade import SubStageTradeAdapter

ADAPTER_REGISTRY: dict[str, type] = {
    "sub_stage_trade": SubStageTradeAdapter,
    "activity_type_authoritative": ActivityTypeSourceAdapter,
}

__all__ = ["ADAPTER_REGISTRY", "SubStageTradeAdapter", "ActivityTypeSourceAdapter"]
