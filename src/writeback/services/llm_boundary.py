# writeback/services/llm_boundary.py
"""
Schema-validated LLM call boundary — errors as data, single self-correct retry.

Every pipeline stage that consumes LLM JSON goes through
:func:`call_structured`: invoke → tolerant parse (code-fence strip only) →
stage normalizers (pure shape adapters) → pydantic validation. On failure,
the pydantic errors are mapped to structured :class:`BoundaryError` rows and
injected into ONE retry prompt so the model can self-correct — the
errors-as-data pattern from ifc-lite's MCP tool surface. A second failure
raises :class:`BoundaryValidationError` carrying the structured errors, which
downstream lands on the FailureRecord as data instead of an opaque string.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ValidationError

from core.llm import cached_system

logger = logging.getLogger(__name__)

_RETRY_TEMPLATE = (
    "{user_prompt}\n\n"
    "Your previous answer failed validation.\n"
    "Previous answer: {raw}\n"
    "Validation errors: {errors}\n"
    "Emit corrected JSON only — no commentary, no markdown."
)


@dataclass(frozen=True)
class BoundaryError:
    """One structured validation failure at the LLM boundary."""

    code: str  # NOT_JSON | MISSING_FIELD | BAD_ENUM | BAD_SHAPE | UNGROUNDED
    path: str  # e.g. "segments[0].kind"
    hint: str  # actionable message, injected into the retry prompt


class BoundaryValidationError(Exception):
    """The LLM output failed validation after all repair rounds."""

    def __init__(self, stage: str, errors: list[BoundaryError], raw: str) -> None:
        details = "; ".join(f"{e.path}: {e.hint}" for e in errors) or "unparseable output"
        super().__init__(f"Stage '{stage}' produced invalid output: {details}")
        self.stage = stage
        self.errors = errors
        self.raw = raw

    def errors_as_dicts(self) -> list[dict]:
        """Serializable error rows for FailureRecord / retry context."""
        return [asdict(e) for e in self.errors]


def call_structured(
    llm,
    *,
    stage: str,
    system_prompt: str,
    user_prompt: str,
    schema: type[BaseModel],
    normalizers: Sequence[Callable[[object], object]] = (),
    prior_errors: Sequence[BoundaryError] = (),
    max_repair_rounds: int = 1,
) -> BaseModel:
    """Invoke the LLM and return a validated schema instance.

    Args:
        llm:              A langchain chat model (from ``core.llm.get_llm``).
        stage:            Pipeline stage name for logging / failure records.
        system_prompt:    Stage system prompt (cached via ``cached_system``).
        user_prompt:      The rendered user prompt for this call.
        schema:           Pydantic v2 model the final payload must satisfy.
        normalizers:      Pure functions applied to the parsed JSON before
                          validation — the stage's known drift-shape adapters.
        prior_errors:     Structured errors from an earlier failed attempt
                          (retry-of-failure flow); injected into the first prompt.
        max_repair_rounds: Retries after the first failed attempt.

    Raises:
        BoundaryValidationError: validation still failing after retries.
    """
    prompt = user_prompt
    if prior_errors:
        prompt = _RETRY_TEMPLATE.format(
            user_prompt=user_prompt,
            raw="(previous session)",
            errors=json.dumps([asdict(e) for e in prior_errors]),
        )

    raw = ""
    errors: list[BoundaryError] = []
    for attempt in range(1 + max_repair_rounds):
        response = llm.invoke([cached_system(llm, system_prompt), HumanMessage(content=prompt)])
        raw = getattr(response, "content", "") or ""

        errors, result = _parse_and_validate(raw, schema, normalizers)
        if result is not None:
            if attempt > 0:
                logger.info("Stage %s self-corrected on retry %d.", stage, attempt)
            return result

        logger.warning(
            "Stage %s output failed validation (attempt %d/%d): %s",
            stage,
            attempt + 1,
            1 + max_repair_rounds,
            [f"{e.path}: {e.code}" for e in errors],
        )
        prompt = _RETRY_TEMPLATE.format(
            user_prompt=user_prompt,
            raw=raw[:2000],
            errors=json.dumps([asdict(e) for e in errors]),
        )

    raise BoundaryValidationError(stage=stage, errors=errors, raw=raw)


# ── Internals ──────────────────────────────────────────────────────


def _parse_and_validate(
    raw: str,
    schema: type[BaseModel],
    normalizers: Sequence[Callable[[object], object]],
) -> tuple[list[BoundaryError], BaseModel | None]:
    """Parse → normalize → validate. Returns (errors, model-or-None)."""
    try:
        data = json.loads(_strip_code_fences(raw))
    except (json.JSONDecodeError, TypeError):
        return [
            BoundaryError(
                code="NOT_JSON",
                path="$",
                hint="The response was not valid JSON. Emit a single JSON object.",
            )
        ], None

    for normalizer in normalizers:
        data = normalizer(data)

    try:
        return [], schema.model_validate(data)
    except ValidationError as e:
        return [_to_boundary_error(row) for row in e.errors()], None


def _strip_code_fences(raw: str) -> str:
    """Remove a surrounding markdown code fence — the only tolerated repair."""
    text = (raw or "").strip()
    if not text.startswith("```"):
        return text
    first_newline = text.find("\n")
    if first_newline == -1:
        return text
    body = text[first_newline + 1 :]
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3]
    return body.strip()


def _to_boundary_error(row: dict) -> BoundaryError:
    """Map one pydantic error row to a BoundaryError."""
    error_type = row.get("type", "")
    if error_type == "missing":
        code = "MISSING_FIELD"
    elif "enum" in error_type or error_type == "literal_error":
        code = "BAD_ENUM"
    else:
        code = "BAD_SHAPE"
    return BoundaryError(
        code=code,
        path=_render_loc(row.get("loc", ())),
        hint=str(row.get("msg", "")),
    )


def _render_loc(loc: tuple) -> str:
    """Render a pydantic loc tuple as "segments[0].kind"."""
    parts: list[str] = []
    for item in loc:
        if isinstance(item, int):
            parts.append(f"[{item}]")
        elif parts:
            parts.append(f".{item}")
        else:
            parts.append(str(item))
    return "".join(parts) or "$"
