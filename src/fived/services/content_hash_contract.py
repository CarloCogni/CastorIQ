# fived/services/content_hash_contract.py
"""Canonical content-hash contract for frozen FiveDModelVersion snapshots.

HASH-1: creation and verification share one payload builder. Row order is
Python-sorted by persisted ``source_row_key`` (never DB retrieval order or
ephemeral prep_rows order). Values are JSON-normalized before digesting.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

# Historical create path (16e5589 / early B4C): settings + rows in prep_rows
# iteration order; readiness omitted unless the B4C code path included it.
# Prep order was never persisted → cannot re-verify non-empty legacy rows.
CONTENT_HASH_CONTRACT_V1 = "fived_content_hash_v1"

# HASH-1 current through UNIT-02: settings + deterministic Python-sorted row
# fingerprints + readiness artifact when present.
CONTENT_HASH_CONTRACT_V2 = "fived_content_hash_v2"

# UNIT-03: v2 fields plus measurement/conversion identity (model vs output).
CONTENT_HASH_CONTRACT_V3 = "fived_content_hash_v3"

CURRENT_CONTENT_HASH_CONTRACT = CONTENT_HASH_CONTRACT_V3

ROW_FINGERPRINT_FIELDS: tuple[str, ...] = (
    "source_row_key",
    "ifc_class",
    "type_name",
    "quantity_basis",
    "total_quantity",
    "classification_code",
    "package_mapping",
    "work_package",
    "classification_origin",
    "package_mapping_origin",
    "work_package_origin",
    "session_review_status",
)

ROW_FINGERPRINT_FIELDS_V3_EXTRA: tuple[str, ...] = (
    "measurement_type",
    "quantity_source",
    "model_total",
    "model_unit",
    "output_total",
    "output_unit",
    "conversion_version",
)


class ContentHashStatus(StrEnum):
    """Outcome of verifying a stored content_hash against persisted data."""

    VERIFIED = "verified"
    UNVERIFIABLE_LEGACY = "unverifiable_legacy"
    FAILED_INTEGRITY = "failed_integrity"


@dataclass(frozen=True, slots=True)
class ContentHashAssessment:
    """Classification of a version's stored content_hash."""

    status: ContentHashStatus
    reason: str
    contract_version: str
    expected_hash: str | None = None


def canonicalize_json_value(value: Any) -> Any:
    """Normalize a value for deterministic JSON hashing.

    UUIDs → str, Decimals → canonical string, floats → stable decimal strings,
    dates/datetimes → ISO-8601, dicts/lists recursively normalized. Booleans
    and None are preserved. Dict keys become sorted strings at dump time.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return str(value)
        # str(float) then Decimal avoids binary float JSON drift across reloads.
        return format(Decimal(str(value)), "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): canonicalize_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonicalize_json_value(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def row_fingerprint_from_mapping(row: Mapping[str, Any]) -> dict[str, Any]:
    """Build one canonical row fingerprint from a mapping (prep or ORM values)."""
    out: dict[str, Any] = {}
    for field in ROW_FINGERPRINT_FIELDS:
        out[field] = canonicalize_json_value(row.get(field))
    return out


def row_fingerprint_v3_from_mapping(row: Mapping[str, Any]) -> dict[str, Any]:
    """V3 fingerprint: v2 fields + measurement/conversion identity."""
    out = row_fingerprint_from_mapping(row)
    for field in ROW_FINGERPRINT_FIELDS_V3_EXTRA:
        out[field] = canonicalize_json_value(row.get(field))
    return out


def _conversion_fields_from_provenance(prov: Mapping[str, Any] | None) -> dict[str, Any]:
    """Extract UNIT-03 conversion identity from quantity_provenance JSON."""
    data = prov if isinstance(prov, Mapping) else {}
    uc = data.get("unit_conversion") if isinstance(data.get("unit_conversion"), Mapping) else {}
    return {
        "measurement_type": data.get("measurement_type") or uc.get("measurement_type") or "",
        "quantity_source": data.get("quantity_source") or "",
        "model_total": uc.get("model_total"),
        "model_unit": uc.get("model_unit") or "",
        "output_total": uc.get("output_total"),
        "output_unit": uc.get("output_unit") or "",
        "conversion_version": uc.get("version") or "",
    }


def row_fingerprint_from_model(row: Any) -> dict[str, Any]:
    """Build one canonical row fingerprint from a FiveDModelRow instance."""
    raw = {field: getattr(row, field) for field in ROW_FINGERPRINT_FIELDS}
    return row_fingerprint_from_mapping(raw)


def row_fingerprint_v3_from_model(row: Any) -> dict[str, Any]:
    """V3 fingerprint from ORM row + quantity_provenance.unit_conversion."""
    raw = {field: getattr(row, field) for field in ROW_FINGERPRINT_FIELDS}
    prov = getattr(row, "quantity_provenance", None) or {}
    conv = _conversion_fields_from_provenance(prov if isinstance(prov, Mapping) else {})
    if not conv.get("quantity_source"):
        conv["quantity_source"] = getattr(row, "quantity_source", "") or ""
    raw.update(conv)
    return row_fingerprint_v3_from_mapping(raw)


def sort_row_fingerprints(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic Python sort — independent of Postgres collation."""
    return sorted(
        rows,
        key=lambda r: (str(r.get("source_row_key") or ""),),
    )


def build_canonical_hash_payload(
    *,
    settings: Mapping[str, Any],
    row_fingerprints: list[dict[str, Any]],
    semantic_source_readiness: Mapping[str, Any] | None = None,
    include_readiness: bool = True,
) -> dict[str, Any]:
    """Assemble the canonical hash payload (shared by create and verify)."""
    payload: dict[str, Any] = {
        "settings": canonicalize_json_value(dict(settings or {})),
        "rows": sort_row_fingerprints(list(row_fingerprints)),
    }
    if (
        include_readiness
        and isinstance(semantic_source_readiness, Mapping)
        and semantic_source_readiness
    ):
        payload["semantic_source_readiness"] = canonicalize_json_value(
            dict(semantic_source_readiness)
        )
    return payload


def digest_canonical_payload(payload: Mapping[str, Any]) -> str:
    """SHA-256 hex digest of a canonical payload."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_content_hash(
    *,
    settings: Mapping[str, Any],
    row_fingerprints: list[dict[str, Any]],
    semantic_source_readiness: Mapping[str, Any] | None = None,
    include_readiness: bool = True,
) -> str:
    """Compute content hash from already-built fingerprints."""
    return digest_canonical_payload(
        build_canonical_hash_payload(
            settings=settings,
            row_fingerprints=row_fingerprints,
            semantic_source_readiness=semantic_source_readiness,
            include_readiness=include_readiness,
        )
    )


def fingerprints_from_version_rows(
    version: Any,
    *,
    contract_version: str | None = None,
) -> list[dict[str, Any]]:
    """Load row fingerprints from persisted rows (order ignored; sorted later)."""
    contract = (
        contract_version or getattr(version, "content_hash_contract_version", "") or ""
    ).strip()
    if contract == CONTENT_HASH_CONTRACT_V3:
        return [row_fingerprint_v3_from_model(row) for row in version.rows.all()]
    return [row_fingerprint_from_model(row) for row in version.rows.all()]


def compute_version_content_hash(
    version: Any,
    *,
    contract_version: str | None = None,
) -> str | None:
    """Recompute digest for a version under the given (or stored) contract.

    Returns None when the historical contract cannot be reproduced from
    persisted inputs (caller should treat as unverifiable legacy).
    """
    stored_contract = (
        contract_version or getattr(version, "content_hash_contract_version", "") or ""
    ).strip()
    settings = version.settings_snapshot or {}
    readiness = version.semantic_source_readiness_snapshot

    if stored_contract == CONTENT_HASH_CONTRACT_V3:
        rows = fingerprints_from_version_rows(version, contract_version=CONTENT_HASH_CONTRACT_V3)
        return compute_content_hash(
            settings=settings,
            row_fingerprints=rows,
            semantic_source_readiness=(
                readiness if isinstance(readiness, Mapping) and readiness else None
            ),
            include_readiness=True,
        )

    if stored_contract == CONTENT_HASH_CONTRACT_V2:
        rows = fingerprints_from_version_rows(version, contract_version=CONTENT_HASH_CONTRACT_V2)
        return compute_content_hash(
            settings=settings,
            row_fingerprints=rows,
            semantic_source_readiness=(
                readiness if isinstance(readiness, Mapping) and readiness else None
            ),
            include_readiness=True,
        )

    if stored_contract == CONTENT_HASH_CONTRACT_V1:
        # Explicit v1: settings + rows only, Python sort (best reconstructable form).
        rows = fingerprints_from_version_rows(version, contract_version=CONTENT_HASH_CONTRACT_V1)
        return compute_content_hash(
            settings=settings,
            row_fingerprints=rows,
            semantic_source_readiness=None,
            include_readiness=False,
        )

    if not stored_contract:
        # Pre-HASH-1 rows: original digest used non-persisted prep_rows order.
        # Empty-row snapshots are order-independent and can still verify as v1.
        rows = fingerprints_from_version_rows(version, contract_version="")
        if not rows:
            return compute_content_hash(
                settings=settings,
                row_fingerprints=[],
                semantic_source_readiness=None,
                include_readiness=False,
            )
        return None

    logger.warning(
        "unknown content_hash_contract_version=%s version=%s",
        stored_contract,
        getattr(version, "pk", None),
    )
    return None


def assess_version_content_hash(version: Any) -> ContentHashAssessment:
    """Classify stored content_hash: verified / unverifiable legacy / failed."""
    stored = (getattr(version, "content_hash", None) or "").strip()
    contract = (getattr(version, "content_hash_contract_version", None) or "").strip()

    if not stored:
        return ContentHashAssessment(
            status=ContentHashStatus.FAILED_INTEGRITY,
            reason="stored content_hash is empty",
            contract_version=contract or "(none)",
            expected_hash=None,
        )

    if not contract:
        expected = compute_version_content_hash(version, contract_version="")
        if expected is None:
            return ContentHashAssessment(
                status=ContentHashStatus.UNVERIFIABLE_LEGACY,
                reason=(
                    "Legacy snapshot has no content_hash_contract_version; "
                    "creation hashed rows in ephemeral prep_rows order which "
                    "was never persisted, so the original digest cannot be "
                    "reproduced from frozen rows alone"
                ),
                contract_version="(none)",
                expected_hash=None,
            )
        if expected == stored:
            return ContentHashAssessment(
                status=ContentHashStatus.VERIFIED,
                reason="legacy empty-row snapshot matches reconstructable v1 formula",
                contract_version=CONTENT_HASH_CONTRACT_V1,
                expected_hash=expected,
            )
        # Non-empty contract missing but empty-row path didn't match — still
        # treat non-reproducible prep-order hashes as unverifiable when rows exist.
        if fingerprints_from_version_rows(version):
            return ContentHashAssessment(
                status=ContentHashStatus.UNVERIFIABLE_LEGACY,
                reason=(
                    "Legacy snapshot content_hash was produced with non-persisted "
                    "prep_rows ordering (and possibly pre-normalization floats); "
                    "original digest cannot be reproduced"
                ),
                contract_version="(none)",
                expected_hash=None,
            )
        return ContentHashAssessment(
            status=ContentHashStatus.FAILED_INTEGRITY,
            reason="reconstructable legacy digest does not match stored content_hash",
            contract_version=CONTENT_HASH_CONTRACT_V1,
            expected_hash=expected,
        )

    expected = compute_version_content_hash(version, contract_version=contract)
    if expected is None:
        return ContentHashAssessment(
            status=ContentHashStatus.UNVERIFIABLE_LEGACY,
            reason=f"contract {contract} cannot be evaluated from persisted inputs",
            contract_version=contract,
            expected_hash=None,
        )
    if expected == stored:
        return ContentHashAssessment(
            status=ContentHashStatus.VERIFIED,
            reason=f"stored hash matches contract {contract}",
            contract_version=contract,
            expected_hash=expected,
        )
    return ContentHashAssessment(
        status=ContentHashStatus.FAILED_INTEGRITY,
        reason=f"stored hash does not match contract {contract}",
        contract_version=contract,
        expected_hash=expected,
    )


def verify_version_content_hash(version: Any) -> bool:
    """Return True only when assessment status is VERIFIED."""
    return assess_version_content_hash(version).status == ContentHashStatus.VERIFIED
