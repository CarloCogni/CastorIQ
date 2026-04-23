# facilities/services/ifc_mappers/classification_mapper.py
"""Builds ``SET_CLASSIFICATION`` delta payloads from classification changes."""

from __future__ import annotations


def classification_delta_payload(reference, action: str) -> dict:
    """Return the payload for a ``SET_CLASSIFICATION`` FMDelta.

    ``reference`` is a :class:`facilities.models.assets.ClassificationReference`;
    ``action`` is either ``"add"`` or ``"remove"``. The payload carries the
    classification system, code, and label — enough for
    :meth:`Tier2Writer.set_classification` to resolve or create the
    ``IfcClassification`` + ``IfcClassificationReference`` on export replay.

    Remove is payload-compatible with add; the dispatcher branches on
    ``action`` at replay time. (``Tier2Writer`` does not yet expose a
    ``remove_classification``; the v1 plan's UI only emits ``add`` — the
    ``action`` key is carried for audit and future extension.)
    """
    if action not in ("add", "remove"):
        raise ValueError(f"classification action must be 'add' or 'remove', got {action!r}")

    system = reference.classification.name
    edition = reference.classification.edition
    return {
        "action": action,
        "system": system if not edition else f"{system} ({edition})",
        "system_name": system,
        "edition": edition,
        "code": reference.code,
        "name": reference.name,
        "reference_id": str(reference.pk),
    }
