# facilities/services/ifc_mappers/asset_field_map.py
"""Declarative mapping between ``FacilityAsset`` fields and IFC writes.

This is the single source of truth for which FM-side columns round-trip into
the IFC file and how. It drives **both** directions:

* **Export (DB → IFC).** When ``asset_service.update_asset`` mutates a mapped
  field, an ``FMDelta`` row is queued whose payload is built by
  :func:`delta_payload_for_field`. At export time, the dispatcher reads the
  same map to pick a ``Tier2Writer`` method.
* **Promote seeding (IFC → DB).** When ``asset_service.bulk_promote`` wraps
  an existing ``IFCEntity`` in a new ``FacilityAsset``, :func:`read_from_ifc_entity`
  walks the same map to pre-populate DB columns from the entity's ``name`` and
  ``properties`` JSON — closing debt #6 (IFC pset ↔ DB overlap) bidirectionally.

Only the keys in :data:`ASSET_FIELD_TO_IFC` are FM-tracked for export. DB-only
fields (``responsible_party``, ``condition_score``, ``notes``,
``expected_service_life_years``, ``barcode``, ``decommissioned_at``) are
deliberately absent.
"""

from __future__ import annotations

from dataclasses import dataclass

# ──────────────────────────────────────────────────────────────────────────────
# Mapping dataclass
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Mapping:
    """How a FacilityAsset field round-trips to an IFC operation.

    Exactly one of the two shapes is populated per row:

    * ``operation == "SET_ATTRIBUTE"`` — a direct IfcRoot attribute change.
      Only ``attribute`` is set; ``pset`` and ``prop`` are empty.
    * ``operation == "ADD_PSET"`` — merge a property into a property set.
      Both ``pset`` and ``prop`` are set; ``attribute`` is empty.

    ``SET_PROPERTY`` is left out of the map on purpose: ``Tier2Writer.add_pset``
    handles the "create-or-merge" semantic the FM flow wants, so every pset-
    bound field uses ``ADD_PSET`` regardless of whether the pset pre-exists.
    The dispatcher still knows how to execute a ``SET_PROPERTY`` delta —
    nothing stops a future milestone from using it directly.
    """

    operation: str
    pset: str = ""
    prop: str = ""
    attribute: str = ""


# ──────────────────────────────────────────────────────────────────────────────
# The map
# ──────────────────────────────────────────────────────────────────────────────

ASSET_FIELD_TO_IFC: dict[str, Mapping] = {
    # ── SET_ATTRIBUTE ───────────────────────────────────────────────────────
    # `asset_tag → IfcRoot.Tag` descoped from v1: the ingest parser does not
    # persist the instance-level IfcRoot.Tag on IFCEntity today. Can land in a
    # later milestone with a parser extension + re-parse.
    "name": Mapping("SET_ATTRIBUTE", attribute="Name"),
    # ── ADD_PSET (standard IFC4 property sets) ──────────────────────────────
    "manufacturer": Mapping("ADD_PSET", pset="Pset_ManufacturerOccurrence", prop="Manufacturer"),
    "model_number": Mapping("ADD_PSET", pset="Pset_ManufacturerOccurrence", prop="ModelLabel"),
    "serial_number": Mapping("ADD_PSET", pset="Pset_ManufacturerOccurrence", prop="SerialNumber"),
    "commissioning_date": Mapping("ADD_PSET", pset="Pset_AssetCommon", prop="CommissioningDate"),
    "warranty_start": Mapping("ADD_PSET", pset="Pset_Warranty", prop="WarrantyStartDate"),
    "warranty_end": Mapping("ADD_PSET", pset="Pset_Warranty", prop="WarrantyEndDate"),
}


# ──────────────────────────────────────────────────────────────────────────────
# Public helpers
# ──────────────────────────────────────────────────────────────────────────────


def field_names() -> frozenset[str]:
    """Return the set of FacilityAsset field names tracked for export.

    ``asset_service._record_delta_for_field`` uses this to decide whether a
    mutation is FM-trackable or purely DB-only.
    """
    return frozenset(ASSET_FIELD_TO_IFC.keys())


def delta_payload_for_field(field: str, old_value, new_value) -> tuple[str, dict]:
    """Build ``(operation, payload)`` for an ``FMDelta`` from a field mutation.

    The payload shape is the contract the dispatcher expects — see the module
    docstring of :mod:`facilities.services.ifc_mappers.dispatcher`.

    Raises :class:`KeyError` if ``field`` is not in :data:`ASSET_FIELD_TO_IFC`.
    Callers should check :func:`field_names` first.
    """
    mapping = ASSET_FIELD_TO_IFC[field]
    payload: dict[str, object] = {
        "value": _json_safe(new_value),
        "old_value": _json_safe(old_value),
    }
    if mapping.operation == "SET_ATTRIBUTE":
        payload["attribute"] = mapping.attribute
    else:  # ADD_PSET (and any future SET_PROPERTY use)
        payload["pset"] = mapping.pset
        payload["property"] = mapping.prop
    return mapping.operation, payload


def read_from_ifc_entity(entity) -> dict[str, object]:
    """Seed a dict of FacilityAsset field values from an ``IFCEntity``.

    Reads only the fields mapped in :data:`ASSET_FIELD_TO_IFC`. Skips fields
    for which the IFC carries no source value so that callers can simply merge
    the returned dict over a blank row:

    >>> seeded = read_from_ifc_entity(entity)
    >>> for k, v in seeded.items():
    ...     setattr(asset, k, v)

    For attribute-bound fields (``name`` → ``IfcRoot.Name``) we read from
    ``entity.name``. For pset-bound fields we walk ``entity.properties``,
    which the parser stores as a flat dict keyed by ``"Pset_Name.Property"``.
    Type-level pset values prefixed with ``"Type."`` are used only as a
    fallback — the occurrence pset takes precedence if both exist.
    """
    seeded: dict[str, object] = {}
    props = entity.properties or {}

    for field, mapping in ASSET_FIELD_TO_IFC.items():
        if mapping.operation == "SET_ATTRIBUTE":
            if mapping.attribute == "Name":
                if entity.name:
                    seeded[field] = entity.name
            # asset_tag / other attributes: no source data today (descoped).
            continue

        # ADD_PSET: occurrence wins, type-level is fallback.
        occurrence_key = f"{mapping.pset}.{mapping.prop}"
        type_key = f"Type.{mapping.pset}.{mapping.prop}"
        value = props.get(occurrence_key, props.get(type_key))
        if value in (None, ""):
            continue
        seeded[field] = _coerce_pset_value_for_field(field, value)

    return seeded


# ──────────────────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────────────────


def _json_safe(value):
    """Normalize a Python value for JSONField storage.

    The parser's ``_serialize_value`` already coerces IFC values to JSON-safe
    types before they land in ``IFCEntity.properties``. On the write side we
    normalize dates/datetimes to ISO strings and leave everything else alone.
    """
    if value is None:
        return None
    # Lazy import to avoid pulling datetime at module load for tests that
    # stub this module.
    from datetime import date, datetime

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _coerce_pset_value_for_field(field: str, raw):
    """Best-effort coerce an ``IFCEntity.properties`` value into the field's Python type.

    FacilityAsset date fields are ``DateField``; pset values read from the IFC
    arrive as ISO strings (the parser's ``_serialize_value`` produces that for
    IfcDate-like values). Everything else is pass-through.
    """
    date_fields = {"commissioning_date", "warranty_start", "warranty_end"}
    if field in date_fields and isinstance(raw, str):
        from datetime import date

        try:
            return date.fromisoformat(raw[:10])
        except (ValueError, TypeError):
            return None
    return raw
