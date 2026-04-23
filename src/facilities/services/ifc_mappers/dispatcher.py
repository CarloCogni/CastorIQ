# facilities/services/ifc_mappers/dispatcher.py
"""Runtime adapter between ``FMDelta`` rows and the IFC writers.

One function: :func:`apply_delta`. It pattern-matches on the delta's operation
and calls the right :class:`ifc_processor.services.tier2_writer.Tier2Writer`
method with the payload unpacked. This is the only place in the FM codebase
that speaks the writer's API — if the writer signatures change, only this
file follows.

Payload contracts (set at delta-creation time by the mappers):

* ``SET_PROPERTY`` / ``ADD_PSET``:
  ``{"pset": str, "property": str, "value": <json-safe>, "old_value": <json-safe>}``
* ``SET_ATTRIBUTE``:
  ``{"attribute": str, "value": <json-safe>, "old_value": <json-safe>}``
* ``SET_CLASSIFICATION``:
  ``{"action": "add"|"remove", "system": str, "code": str, "name": str, ...}``
"""

from __future__ import annotations

import logging

from facilities.models import FMDelta
from ifc_processor.services.ifc_writer import EntityChange
from ifc_processor.services.tier2_writer import Tier2Writer

logger = logging.getLogger(__name__)


class UnknownOperationError(ValueError):
    """Raised when a delta's operation has no dispatch branch."""


class UnsupportedOperationError(NotImplementedError):
    """Raised when a delta's operation is defined but the writer cannot execute it yet."""


def apply_delta(writer: Tier2Writer, delta: FMDelta) -> list[EntityChange]:
    """Execute ``delta`` against the in-memory IFC model held by ``writer``.

    Returns the list of :class:`EntityChange` records the writer produced,
    which the export service accumulates into the final Git commit's
    ``diff_data`` payload.

    Raises :class:`UnknownOperationError` for unclassified operations,
    :class:`UnsupportedOperationError` for known-but-not-yet-wired ones
    (e.g. classification removal in v1), and propagates
    :class:`ifc_processor.services.ifc_writer.IFCWriteError` unchanged.
    """
    op = delta.operation
    payload = delta.payload or {}
    guid = delta.entity_guid

    if op == FMDelta.Operation.SET_PROPERTY:
        return writer.t1.set_property(
            global_ids=[guid],
            pset=payload["pset"],
            prop=payload["property"],
            value=payload["value"],
        )

    if op == FMDelta.Operation.ADD_PSET:
        # Single-property merge into the target pset. Tier2Writer.add_pset
        # creates the pset on first call and merges on subsequent calls,
        # so one delta per property is the right grain.
        return writer.add_pset(
            global_ids=[guid],
            pset_name=payload["pset"],
            properties={payload["property"]: payload["value"]},
        )

    if op == FMDelta.Operation.SET_ATTRIBUTE:
        return writer.t1.set_attribute(
            global_ids=[guid],
            attribute=payload["attribute"],
            value=payload["value"],
        )

    if op == FMDelta.Operation.SET_CLASSIFICATION:
        action = payload.get("action", "add")
        if action == "add":
            return writer.set_classification(
                global_ids=[guid],
                system_name=payload["system"],
                reference=payload["code"],
                name=payload.get("name", ""),
            )
        # v1 only emits "add" from the UI; "remove" is held for a later pass.
        raise UnsupportedOperationError(
            f"SET_CLASSIFICATION action={action!r} is not supported in M2 v1"
        )

    raise UnknownOperationError(f"FMDelta has unknown operation {op!r}")
