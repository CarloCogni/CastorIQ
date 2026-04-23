# facilities/services/ifc_mappers/__init__.py
"""Declarative mappers between FacilityAsset fields and IFC write operations.

Three layers:

``asset_field_map``
    The single source of truth for FacilityAsset ↔ IFC plumbing: which DB
    field maps to which IFC operation (SET_ATTRIBUTE vs ADD_PSET), which pset
    and property (or attribute) is targeted, and how to read the value back
    out of an ``IFCEntity`` at promote-seeding time.

``classification_mapper``
    Builds ``SET_CLASSIFICATION`` payloads from ``ClassificationReference``
    add/remove actions.

``dispatcher``
    Runtime adapter: given a pending ``FMDelta`` and a ``Tier2Writer``, call
    the right writer method with the payload unpacked.
"""

from .asset_field_map import (
    ASSET_FIELD_TO_IFC,
    Mapping,
    delta_payload_for_field,
    field_names,
    read_from_ifc_entity,
)
from .classification_mapper import classification_delta_payload
from .dispatcher import apply_delta

__all__ = [
    "ASSET_FIELD_TO_IFC",
    "Mapping",
    "apply_delta",
    "classification_delta_payload",
    "delta_payload_for_field",
    "field_names",
    "read_from_ifc_entity",
]
