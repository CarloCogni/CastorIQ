# facilities/models/__init__.py
"""FM domain models.

Internally partitioned by concern (assets, work, maintenance, systems, sensors,
docs, costs, exports). Each submodule is added in its own milestone.

M1 adds the Asset Register (``assets`` submodule). Subsequent milestones will
add ``work``, ``maintenance``, ``systems``, ``sensors``, ``docs``, ``costs``,
and ``exports`` without flattening the current layout.
"""

from .assets import (
    AssetInventory,
    Classification,
    ClassificationReference,
    FacilityAsset,
)

__all__ = [
    "AssetInventory",
    "Classification",
    "ClassificationReference",
    "FacilityAsset",
]
