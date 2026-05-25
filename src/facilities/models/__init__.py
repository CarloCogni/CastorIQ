# facilities/models/__init__.py
"""FM domain models.

Internally partitioned by concern (assets, work, maintenance, systems, sensors,
docs, costs, exports). Each submodule is added in its own milestone.

M1 adds the Asset Register (``assets`` submodule). M2 adds Export Reconciliation
(``exports``). M3 adds Work Orders (``work``). Subsequent milestones will add
``maintenance``, ``systems``, ``sensors``, ``docs``, ``costs`` without flattening
the current layout.
"""

from .assets import (
    AssetInventory,
    Classification,
    ClassificationReference,
    FacilityAsset,
)
from .explore import (
    DEFAULT_PHASES,
    ExploreFloorPlan,
    ExploreMedia,
    ExplorePhase,
    ExplorePoint,
)
from .exports import (
    DEFAULT_ENABLED_OPERATIONS,
    DEFAULT_ENABLED_PSETS,
    ExportJob,
    ExportProfile,
    FMDelta,
)
from .work import (
    ActionRequest,
    FMIntentProposal,
    ImmutableError,
    Permit,
    WorkOrder,
    WorkOrderAttachment,
    WorkOrderCategory,
    WorkOrderPriority,
    WorkOrderStatus,
    WorkOrderStatusEvent,
)

__all__ = [
    "DEFAULT_ENABLED_OPERATIONS",
    "DEFAULT_ENABLED_PSETS",
    "DEFAULT_PHASES",
    "ActionRequest",
    "AssetInventory",
    "Classification",
    "ClassificationReference",
    "ExploreFloorPlan",
    "ExploreMedia",
    "ExplorePhase",
    "ExplorePoint",
    "ExportJob",
    "ExportProfile",
    "FMDelta",
    "FMIntentProposal",
    "FacilityAsset",
    "ImmutableError",
    "Permit",
    "WorkOrder",
    "WorkOrderAttachment",
    "WorkOrderCategory",
    "WorkOrderPriority",
    "WorkOrderStatus",
    "WorkOrderStatusEvent",
]
