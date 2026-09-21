# takeoff/services/quantity_unit_conversion.py
"""R5D-QTO-UNIT-03 — dimensional quantity unit conversion (Decimal-safe).

Every conversion starts from the raw model value in the model unit.
Never re-converts an already-converted number. Labels alone do not change totals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

CONVERSION_VERSION = "qty-unit-conv-v1"

FAMILY_COUNT = "count"
FAMILY_LENGTH = "length"
FAMILY_AREA = "area"
FAMILY_VOLUME = "volume"

# Canonical tokens used in session / freeze / export
TOKEN_COUNT = "count"
TOKEN_MM = "mm"
TOKEN_CM = "cm"
TOKEN_M = "m"
TOKEN_MM2 = "mm2"
TOKEN_CM2 = "cm2"
TOKEN_M2 = "m2"
TOKEN_MM3 = "mm3"
TOKEN_CM3 = "cm3"
TOKEN_M3 = "m3"

DISPLAY_LABELS: dict[str, str] = {
    TOKEN_COUNT: "count",
    TOKEN_MM: "mm",
    TOKEN_CM: "cm",
    TOKEN_M: "m",
    TOKEN_MM2: "mm²",
    TOKEN_CM2: "cm²",
    TOKEN_M2: "m²",
    TOKEN_MM3: "mm³",
    TOKEN_CM3: "cm³",
    TOKEN_M3: "m³",
}

# Factors to SI family base (m, m², m³). Count is identity.
_TO_BASE: dict[str, Decimal] = {
    TOKEN_COUNT: Decimal("1"),
    TOKEN_MM: Decimal("0.001"),
    TOKEN_CM: Decimal("0.01"),
    TOKEN_M: Decimal("1"),
    TOKEN_MM2: Decimal("0.000001"),
    TOKEN_CM2: Decimal("0.0001"),
    TOKEN_M2: Decimal("1"),
    TOKEN_MM3: Decimal("0.000000001"),
    TOKEN_CM3: Decimal("0.000001"),
    TOKEN_M3: Decimal("1"),
}

_FAMILY_TOKENS: dict[str, frozenset[str]] = {
    FAMILY_COUNT: frozenset({TOKEN_COUNT}),
    FAMILY_LENGTH: frozenset({TOKEN_MM, TOKEN_CM, TOKEN_M}),
    FAMILY_AREA: frozenset({TOKEN_MM2, TOKEN_CM2, TOKEN_M2}),
    FAMILY_VOLUME: frozenset({TOKEN_MM3, TOKEN_CM3, TOKEN_M3}),
}

_TOKEN_FAMILY: dict[str, str] = {
    token: family for family, tokens in _FAMILY_TOKENS.items() for token in tokens
}

# Common IFC / display aliases → canonical token
_ALIAS_TO_TOKEN: dict[str, str] = {
    "count": TOKEN_COUNT,
    "mm": TOKEN_MM,
    "cm": TOKEN_CM,
    "m": TOKEN_M,
    "mm2": TOKEN_MM2,
    "mm²": TOKEN_MM2,
    "mm^2": TOKEN_MM2,
    "cm2": TOKEN_CM2,
    "cm²": TOKEN_CM2,
    "cm^2": TOKEN_CM2,
    "m2": TOKEN_M2,
    "m²": TOKEN_M2,
    "m^2": TOKEN_M2,
    "mm3": TOKEN_MM3,
    "mm³": TOKEN_MM3,
    "mm^3": TOKEN_MM3,
    "cm3": TOKEN_CM3,
    "cm³": TOKEN_CM3,
    "cm^3": TOKEN_CM3,
    "m3": TOKEN_M3,
    "m³": TOKEN_M3,
    "m^3": TOKEN_M3,
}

OUTPUT_CHOICES: dict[str, tuple[str, ...]] = {
    FAMILY_COUNT: (TOKEN_COUNT,),
    FAMILY_LENGTH: (TOKEN_MM, TOKEN_CM, TOKEN_M),
    FAMILY_AREA: (TOKEN_MM2, TOKEN_CM2, TOKEN_M2),
    FAMILY_VOLUME: (TOKEN_MM3, TOKEN_CM3, TOKEN_M3),
}

# Display precision (quantize) — applied only for display/export strings
_DISPLAY_QUANT: dict[str, Decimal] = {
    FAMILY_COUNT: Decimal("1"),
    FAMILY_LENGTH: Decimal("0.01"),
    FAMILY_AREA: Decimal("0.01"),
    FAMILY_VOLUME: Decimal("0.01"),
}


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """Result of converting one raw model quantity."""

    ok: bool
    model_total: Decimal | None
    model_unit: str
    output_total: Decimal | None
    output_unit: str
    factor: Decimal | None
    family: str
    conversion_version: str
    error: str | None = None

    def as_float_output(self) -> float | None:
        """Python float for prep row / ORM storage (None when missing)."""
        if self.output_total is None:
            return None
        return float(self.output_total)

    def as_float_model(self) -> float | None:
        if self.model_total is None:
            return None
        return float(self.model_total)


def canonicalize_unit_token(raw: str | None) -> str:
    """Map display/alias unit string to canonical token, or empty."""
    text = str(raw or "").strip()
    if not text:
        return ""
    if text in _ALIAS_TO_TOKEN:
        return _ALIAS_TO_TOKEN[text]
    lower = text.lower()
    if lower in _ALIAS_TO_TOKEN:
        return _ALIAS_TO_TOKEN[lower]
    return ""


def display_unit_label(token: str | None) -> str:
    """Human label for a canonical token."""
    tok = canonicalize_unit_token(token) or str(token or "").strip()
    return DISPLAY_LABELS.get(tok, tok or "—")


def family_for_unit_token(token: str | None) -> str:
    """Return measure family for a canonical token."""
    return _TOKEN_FAMILY.get(canonicalize_unit_token(token), "")


def family_for_measurement_type(measurement_type: str | None) -> str:
    """Map MEASURE-01 measurement_type to unit family."""
    m = str(measurement_type or "").strip().lower()
    if m in {FAMILY_COUNT, FAMILY_LENGTH, FAMILY_AREA, FAMILY_VOLUME}:
        return m
    return ""


def output_choices_for_family(family: str) -> list[dict[str, str]]:
    """Selectable output units for one family."""
    fam = str(family or "").strip()
    out: list[dict[str, str]] = []
    for token in OUTPUT_CHOICES.get(fam, ()):
        out.append({"token": token, "label": display_unit_label(token)})
    return out


def conversion_example_text(*, model_unit: str | None, output_unit: str | None) -> str:
    """Human example for Units panel matching the selected output unit."""
    model_tok = canonicalize_unit_token(model_unit)
    out_tok = canonicalize_unit_token(output_unit) or model_tok
    if not model_tok or not out_tok:
        return "—"
    if family_for_unit_token(model_tok) == FAMILY_COUNT:
        return "unchanged"
    if model_tok == out_tok:
        return "No conversion"
    # Prefer a readable round input in model units.
    sample_in = {
        TOKEN_MM: Decimal("1000"),
        TOKEN_CM: Decimal("100"),
        TOKEN_M: Decimal("1"),
        TOKEN_MM2: Decimal("1000000"),
        TOKEN_CM2: Decimal("10000"),
        TOKEN_M2: Decimal("1"),
        TOKEN_MM3: Decimal("1000000000"),
        TOKEN_CM3: Decimal("1000000"),
        TOKEN_M3: Decimal("1"),
    }.get(model_tok, Decimal("1"))
    result = convert_quantity(model_total=sample_in, model_unit=model_tok, output_unit=out_tok)
    if not result.ok or result.output_total is None:
        return "No conversion"
    out_disp = result.output_total
    if out_disp == out_disp.to_integral_value():
        out_s = format(out_disp.quantize(Decimal("1")), "f")
    else:
        out_s = format(out_disp.normalize(), "f")
    in_s = (
        format(sample_in.quantize(Decimal("1")), "f")
        if sample_in == sample_in.to_integral_value()
        else format(sample_in, "f")
    )
    return f"{in_s} {display_unit_label(model_tok)} → {out_s} {display_unit_label(out_tok)}"


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def convert_quantity(
    *,
    model_total: Any,
    model_unit: str | None,
    output_unit: str | None,
) -> ConversionResult:
    """Convert raw model_total from model_unit to output_unit.

    Missing model_total → missing output (not zero).
    Zero stays zero. Incompatible dimensions → error, no invented number.
    """
    model_tok = canonicalize_unit_token(model_unit)
    out_tok = canonicalize_unit_token(output_unit) or model_tok
    family = family_for_unit_token(model_tok)
    model_dec = _to_decimal(model_total)

    if model_dec is None:
        return ConversionResult(
            ok=False,
            model_total=None,
            model_unit=model_tok,
            output_total=None,
            output_unit=out_tok or model_tok,
            factor=None,
            family=family,
            conversion_version=CONVERSION_VERSION,
            error="missing_model_total",
        )
    if not model_tok or not out_tok:
        return ConversionResult(
            ok=False,
            model_total=model_dec,
            model_unit=model_tok,
            output_total=None,
            output_unit=out_tok,
            factor=None,
            family=family,
            conversion_version=CONVERSION_VERSION,
            error="unresolved_unit",
        )
    if family_for_unit_token(out_tok) != family_for_unit_token(model_tok):
        return ConversionResult(
            ok=False,
            model_total=model_dec,
            model_unit=model_tok,
            output_total=None,
            output_unit=out_tok,
            factor=None,
            family=family,
            conversion_version=CONVERSION_VERSION,
            error="incompatible_dimension",
        )

    from_base = _TO_BASE[model_tok]
    to_base = _TO_BASE[out_tok]
    factor = from_base / to_base
    output = model_dec * factor
    return ConversionResult(
        ok=True,
        model_total=model_dec,
        model_unit=model_tok,
        output_total=output,
        output_unit=out_tok,
        factor=factor,
        family=family,
        conversion_version=CONVERSION_VERSION,
        error=None,
    )


def format_quantity_display(value: Decimal | float | None, *, family: str) -> str:
    """Round for display/export only; None → empty."""
    if value is None:
        return ""
    dec = _to_decimal(value)
    if dec is None:
        return ""
    if dec == 0:
        return "0"
    quant = _DISPLAY_QUANT.get(family, Decimal("0.01"))
    try:
        rounded = dec.quantize(quant, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        rounded = dec
    text = format(rounded, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def conversion_provenance_payload(result: ConversionResult) -> dict[str, Any]:
    """Structured freeze/export unit_conversion blob."""
    return {
        "version": result.conversion_version,
        "family": result.family,
        "model_total": format(result.model_total, "f") if result.model_total is not None else None,
        "model_unit": result.model_unit,
        "output_total": (
            format(result.output_total, "f") if result.output_total is not None else None
        ),
        "output_unit": result.output_unit,
        "factor": format(result.factor, "f") if result.factor is not None else None,
        "ok": result.ok,
        "error": result.error,
    }
