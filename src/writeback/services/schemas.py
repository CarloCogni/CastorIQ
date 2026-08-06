# writeback/services/schemas.py
"""
Pydantic v2 schemas for LLM boundary validation.

One schema per pipeline stage. Schemas validate the payload AFTER the
stage's normalizers ran, so they describe the canonical shape — the
tolerated drift shapes live in the normalizers, the contract lives here.

Schemas here are deliberately permissive on presence: they enforce
STRUCTURE (types, list/dict shape) — the failures that a self-correcting
LLM retry can genuinely fix — while semantic presence/grounding checks
(with their user-facing messages) stay in the stage finalizers. Resolver /
T3-op-planner schemas land in Phase 2b and Phase 4 of the journal migration.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

# Pydantic Literal needs static values; VALID_KINDS is the runtime source
# of truth, so validate membership with a field validator instead.
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ifc_processor.services.tier3_writer import CREATABLE_CLASSES

from .tier_router import VALID_KINDS


class TriageSegment(BaseModel):
    """One action segment from the triage stage."""

    kind: str
    target_phrase: str = ""
    value_phrase: str = ""
    reason: str = ""
    missing: list[str] = Field(default_factory=list)

    @field_validator("kind")
    @classmethod
    def _kind_must_be_valid(cls, value: str) -> str:
        if value not in VALID_KINDS:
            raise ValueError(f"kind must be one of {sorted(VALID_KINDS)}, got {value!r}")
        return value


class TriageOutput(BaseModel):
    """Canonical triage payload: at least one valid segment."""

    segments: list[TriageSegment] = Field(min_length=1)


# ── Slot extractor (Stage 2) ──────────────────────────────────────
# One schema per triage kind. Fields are permissive-typed; the slot
# extractor's finalizers own the semantic checks (presence, grounding,
# enum membership) so their precise user-facing rejection messages are
# preserved. The dict/list constraints below are the structural teeth:
# a `properties` list or a `names` string is exactly the drift an LLM
# retry can correct, so those raise (→ boundary retry) rather than being
# silently coerced.


class PropertySlots(BaseModel):
    """PROPERTY kind — a single property change.

    ``operation`` distinguishes an edit from a removal. Without it the model
    was forced to produce a ``value`` for every request, so "remove Reference
    from all walls" became ``Reference = 'all walls'``. Permissive-typed like
    its siblings; the finalizer maps anything unrecognised back to SET.
    """

    operation: str = ""
    pset: str = ""
    property: str = ""
    value: Any = None


class AttributeSlots(BaseModel):
    """ATTRIBUTE kind — a top-level IFC attribute change."""

    attribute: str = ""
    value: Any = None


class PsetSlots(BaseModel):
    """PSET kind — add/remove pset, set material, or set classification.

    All operation-specific fields are optional here; the finalizer
    enforces which are required for the extracted ``operation``.
    """

    operation: str = ""
    pset_name: str = ""
    properties: dict = Field(default_factory=dict)
    material_name: str = ""
    system_name: str = ""
    reference: str = ""


class CreateSlots(BaseModel):
    """CREATE kind — new non-physical entities."""

    entity_class: str = ""
    names: list[str] = Field(default_factory=list)
    parent_phrase: str = ""


class RelationshipSlots(BaseModel):
    """RELATIONSHIP kind — where an existing entity should move to.

    Only spatial container moves are typed today, so ``relation`` is a
    single-member Literal; group/aggregate moves still fall back to
    generated code.
    """

    destination_phrase: str = ""
    relation: Literal["container"] = "container"


# ── Entity resolver (Stage 3) ─────────────────────────────────────


# ── Tier 3 typed ops (Stage 4, RED) ───────────────────────────────
# The op set is deliberately closed and the class list allow-listed: the
# LLM chooses *which* of a handful of pre-coded operations to run, never
# what code executes. `parent_relation` carries a "container" member that
# nothing accepts yet — it is reserved so typing RELATIONSHIP later does
# not need a schema-version bump.


class T3CreateEntityOp(BaseModel):
    """Create one non-physical entity."""

    op: Literal["CREATE_ENTITY"]
    ifc_class: str
    name: str = Field(min_length=1, max_length=255)
    long_name: str = ""
    description: str = ""
    parent_global_id: str = ""
    parent_relation: Literal["none", "aggregate", "container", "group"] = "none"
    member_global_ids: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("ifc_class")
    @classmethod
    def _class_must_be_creatable(cls, value: str) -> str:
        if value not in CREATABLE_CLASSES:
            raise ValueError(
                f"ifc_class must be one of {sorted(CREATABLE_CLASSES)} "
                f"(non-physical entities only), got {value!r}"
            )
        return value


class T3DeleteEntityOp(BaseModel):
    """Delete one existing entity by GlobalId."""

    op: Literal["DELETE_ENTITY"]
    global_id: str = Field(min_length=1, max_length=64)
    ifc_class: str = ""  # advisory; the writer re-reads is_a() at execution


class T3AssignRelationshipOp(BaseModel):
    """Move one existing entity into a different spatial container.

    Container moves only. Group membership and aggregation still fall back
    to generated code — ``relation`` is a single-member Literal so widening
    it later is additive.
    """

    op: Literal["ASSIGN_RELATIONSHIP"]
    global_id: str = Field(min_length=1, max_length=64)
    destination_global_id: str = Field(min_length=1, max_length=64)
    relation: Literal["container"] = "container"


T3Op = Annotated[
    T3CreateEntityOp | T3DeleteEntityOp | T3AssignRelationshipOp,
    Field(discriminator="op"),
]


class T3OpPlan(BaseModel):
    """Either a list of typed ops, or an explicit 'I can't express this'."""

    ops: list[T3Op] = Field(default_factory=list, max_length=200)
    cannot_express: bool = False
    cannot_express_reason: str = ""
    explanation: str = ""
    confidence: int = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def _exactly_one_outcome(self) -> T3OpPlan:
        if self.cannot_express and self.ops:
            raise ValueError("cannot_express=true must come with an empty ops list")
        if not self.cannot_express and not self.ops:
            raise ValueError("Emit at least one op, or set cannot_express=true with a reason")
        if self.cannot_express and not self.cannot_express_reason.strip():
            raise ValueError("cannot_express requires a non-empty cannot_express_reason")
        return self


class ResolverExtraction(BaseModel):
    """Entity-resolver extraction surface (5 fields, scope-discriminated).

    Deliberately permissive: ``_db_resolve`` isinstance-guards every field,
    so the schema's job is only to guarantee a dict, default a missing
    ``scope`` to ``"unknown"`` (matching ``_db_resolve``'s ``.get`` default
    and the trim-retry treatment), and give the boundary a typed container.
    Tightening the field types would risk failing the whole parse on a
    single odd field and losing the others — a regression for a stage that
    is deliberately fail-soft.
    """

    ifc_type: Any = None
    entity_name: Any = None
    entity_names: Any = None
    filter_hints: Any = None
    scope: str = "unknown"

    model_config = ConfigDict(extra="ignore")
