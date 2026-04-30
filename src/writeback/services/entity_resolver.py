# writeback/services/entity_resolver.py
"""
Pre-LLM entity-name resolution for the writeback pipeline.

Runs BEFORE the main classifier. Three deterministic-or-narrow steps,
each with a clean fallback to today's behaviour:

    1. Regex GUID extraction       — free, deterministic.
    2. Focused LLM extraction call — narrow output surface (4 fields).
    3. DB lookup via FilterEngine  — deterministic safety net.

Fail-soft: when any step yields nothing useful, ``resolve()`` returns an
empty :class:`ResolutionResult` and callers run the classifier with the
full project context, exactly as before this refactor.

The architectural goal is to shrink the main classifier's per-call job:
instead of "identify entity AND operation AND property AND value AND
tier in one shot," the resolver pre-pins the entity. Small open-weight
models drift less when each LLM call has a single, narrow purpose.
"""

import json
import logging
import re
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm import get_llm
from ifc_processor.models import IFCEntity

from .filter_engine import FilterEngine

logger = logging.getLogger(__name__)


# 22-char base64 IFC GlobalId, as emitted by IfcOpenShell.
_GUID_PATTERN = re.compile(r"\b[0-9A-Za-z_$]{22}\b")

# Revit step-ID suffix: a colon followed by 5–7 digits (e.g. ":285330").
# Common in Revit-exported IFC entity names where it disambiguates instances
# of the same family/type. Used as a deterministic safety net when the
# extraction LLM drops one of multiple entity references in the message.
_STEP_ID_PATTERN = re.compile(r":\d{5,7}\b")


# Resolver targeting modes. The default ``EXISTING_TARGET`` mode looks up
# entities that already live in the DB. The remaining modes are wired in
# later by the V2 pipeline; today they short-circuit to an empty result so
# callers using the default path are unaffected.
MODE_EXISTING_TARGET = "EXISTING_TARGET"  # Find a real entity by user reference.
MODE_PARENT_TARGET = "PARENT_TARGET"  # Find an existing spatial parent for a CREATE.
MODE_NEW_TARGET = "NEW_TARGET"  # Validate names for CREATE; do not match the DB.
MODE_NO_TARGET = "NO_TARGET"  # Skip resolution entirely (free-form Tier 3).

VALID_MODES = frozenset({MODE_EXISTING_TARGET, MODE_PARENT_TARGET, MODE_NEW_TARGET, MODE_NO_TARGET})


# Minimum length / token count for a TRIMMED entity-name fragment to be
# tried against the DB. The original (full) extracted name is always tried
# first regardless of length; only progressive-trim fallbacks are gated by
# this floor. Prevents the "Fire Zone A" → "A" → 34 unrelated matches
# catastrophe seen in the writeback rewrite logs.
_TRIM_FLOOR_CHARS = 4
_TRIM_FLOOR_TOKENS = 2

# Maximum match count for a TRIMMED entity-name fragment to be accepted.
# Above this, the trim went too aggressive and pulled in unrelated rows;
# the resolver continues to the next candidate or falls through. The
# ORIGINAL extracted name is exempt — if the user named a wide category,
# that's their request, not noise.
_TRIM_MATCH_CAP = 5


# Narrow-purpose extraction prompt. Output surface is intentionally tiny
# (4 fields, all optional) so that even small models stay on schema —
# empirically, the entity-extraction part of the main classifier already
# works in failed runs; the drift is in the operation/property/value
# fields. By isolating extraction we keep what works and protect it from
# the cognitive overload of the bigger classifier prompt.
EXTRACTION_SYSTEM_PROMPT = """\
You extract entity targeting from an IFC modification request.

Return ONLY valid JSON, with this schema:

{
  "ifc_type": "<IfcWall, IfcDoor, IfcWindow, IfcSlab, ... or null>",
  "entity_name": "<exact substring of an entity name from the message, or null>",
  "entity_names": ["<name>", "<name>", ...]  or null,
  "filter_hints": {"<Pset.Property>": <value>}  or null,
  "scope": "specific" | "specific_multi" | "all_of_type" | "filtered" | "unknown"
}

Rules:
- "specific":        user names ONE entity (e.g. "wall: Basic Wall:...:285330"). Set entity_name.
- "specific_multi":  user names MULTIPLE entities ("wall 1 and wall 2", "doors A, B and C").
                     Set entity_names to the list of name fragments, copied verbatim.
                     YOU MUST LIST EVERY ENTITY THE USER MENTIONED — never truncate.
                     If you see "and", "&", or commas between entity references, the
                     scope is specific_multi and the list must contain ALL of them.
- "all_of_type":     user targets ALL entities of a type ("all walls"). Set ifc_type, leave entity_name null.
- "filtered":        user gives a property predicate ("all external walls", "non-load-bearing
                     beams"). Set ifc_type and filter_hints. Negation phrases
                     ("non-X", "non-load-bearing", "internal", "interior") map to
                     the inverted boolean (LoadBearing=false, IsExternal=false).
- "unknown":         request has no entity targeting at all.

Do NOT infer property/operation/value here. Only entity targeting.
Omit any field you cannot determine.

## entity_name — STRICT extraction rules

Copy ONLY the entity's actual name as it would appear in the IFC file.

STRIP from the entity_name:
- Conversational lead-in: "wall:", "door:", "the wall", "for the", "of", etc.
- IFC type tokens that the user typed for clarity: "IfcWall ", "IfcDoor ", etc.
  (the type goes in the separate "ifc_type" field, NOT in entity_name)
- Quotation marks, leading/trailing punctuation.

KEEP the full identifier string itself, including its internal colons,
hyphens, and trailing IDs (e.g. ":285330"). Those are part of the name.

Counter-example (this is WRONG):
  Input:  "change firerating to EI240 of wall: IfcWall Basic Wall:Wall-Ext_102Bwk-75Ins-100LBlk-12P:285330"
  Wrong: {"entity_name": "wall: IfcWall Basic Wall:Wall-Ext_102Bwk-75Ins-100LBlk-12P:285330"}
  Right: {"entity_name": "Basic Wall:Wall-Ext_102Bwk-75Ins-100LBlk-12P:285330"}

## Examples

User: "change firerating to EI240 of wall: IfcWall Basic Wall:Wall-Ext_102Bwk-75Ins-100LBlk-12P:285330"
Output: {"scope": "specific", "ifc_type": "IfcWall", "entity_name": "Basic Wall:Wall-Ext_102Bwk-75Ins-100LBlk-12P:285330"}

User: "set fire rating on all walls to EI120"
Output: {"scope": "all_of_type", "ifc_type": "IfcWall"}

User: "set FireRating to EI120 on all external walls"
Output: {"scope": "filtered", "ifc_type": "IfcWall", "filter_hints": {"Pset_WallCommon.IsExternal": true}}

User: "rename door D-007 to D-007-Updated"
Output: {"scope": "specific", "ifc_type": "IfcDoor", "entity_name": "D-007"}

User: "change fire rating to EI120 on Wall-001 and Wall-002"
Output: {"scope": "specific_multi", "ifc_type": "IfcWall", "entity_names": ["Wall-001", "Wall-002"]}

User: "set FireRating to EI60 on all non-load-bearing walls"
Output: {"scope": "filtered", "ifc_type": "IfcWall", "filter_hints": {"Pset_WallCommon.LoadBearing": false}}

User: "change ThermalTransmittance to 0.18 on all internal walls"
Output: {"scope": "filtered", "ifc_type": "IfcWall", "filter_hints": {"Pset_WallCommon.IsExternal": false}}

User: "set IsExternal to true on wall :285330 and the window :286105"
Output: {"scope": "specific_multi", "entity_names": [":285330", ":286105"]}

User: "change ThermalTransmittance to 0.20 on all walls with ThermalTransmittance 0.24"
Output: {"scope": "filtered", "ifc_type": "IfcWall", "filter_hints": {"Pset_WallCommon.ThermalTransmittance": 0.24}}

User: "update the model"
Output: {"scope": "unknown"}
"""


_EXTRACTION_USER_TEMPLATE = """\
## User Request
{user_message}

## Task
Extract entity targeting (JSON only).
"""


# Iterations 1 and 2 reuse the same SYSTEM prompt — only the user message is
# augmented. The model sees its previous answer, the DB's match count, and a
# list of real candidate names drawn deterministically from the database. This
# is the "DB-grounded feedback" that lets a small model self-correct without
# us guessing structure for it.
_REFINEMENT_USER_TEMPLATE = """\
## User Request
{user_message}

## Previous Attempt
You answered: entity_name = "{previous_entity_name}"
That {match_summary}

## Candidate Names From the Database
Here are entity names that share substrings with your previous answer:
{candidates}

## How to Refine

Reconsider the user's request. The previous attempt assumed scope="specific"
(one named entity). That may have been wrong. Choose the scope that actually
matches what the user asked for:

- If the user named ONE specific entity → scope="specific" and copy the right
  entity_name EXACTLY from the candidate list above.
- If the user wanted ALL entities of a type ("all walls", "every door") →
  scope="all_of_type" with ifc_type only; leave entity_name null.
- If the user named MULTIPLE specific entities ("wall 1 and wall 2") →
  scope="specific_multi" with entity_names = [list of their exact name fragments].
- If the user wanted entities matching a property predicate ("all external
  walls", "load-bearing columns") → scope="filtered" with filter_hints.
- {opt_out_reminder}

## Task
Re-extract entity targeting (JSON only).
"""


@dataclass(frozen=True)
class ResolutionResult:
    """Outcome of pre-LLM entity resolution.

    ``entities`` is the candidate set from the database. ``scope`` records
    how the extraction read the user's intent so callers can distinguish
    "user targeted one specific entity" from "user targeted all walls"
    (both may yield non-empty ``entities`` but with different semantics).

    ``misses`` lists entity-name references that the user mentioned but
    that the resolver could not match to any DB entity. It is populated
    by ``_resolve_specific_multi`` for partial-miss cases (some names
    matched, some didn't) so the propose pipeline can surface a warning
    on the resulting proposal. Always empty for ``specific`` /
    ``all_of_type`` / ``filtered`` scopes.
    """

    entities: list[IFCEntity]
    ifc_type_hint: str | None
    scope: str  # "specific" | "specific_multi" | "all_of_type" | "filtered" | "empty"
    diagnostic: str
    misses: tuple[str, ...] = ()  # tuple so the dataclass stays frozen-hashable

    @property
    def is_empty(self) -> bool:
        return not self.entities

    @property
    def is_unique(self) -> bool:
        return len(self.entities) == 1

    @property
    def is_ambiguous(self) -> bool:
        return len(self.entities) > 1


_EMPTY = ResolutionResult(
    entities=[], ifc_type_hint=None, scope="empty", diagnostic="no resolution"
)


class EntityNameResolver:
    """Resolves entity references from a user message before the main classifier runs.

    Usage::

        resolver = EntityNameResolver(project, user=request.user)
        result = resolver.resolve(user_message)
        if not result.is_empty:
            # Use ``result.entities`` to narrow the classifier's context
            ...
    """

    def __init__(self, project, user=None, llm=None, filter_engine: FilterEngine | None = None):
        self.project = project
        self.user = user
        self._llm = llm  # Lazily resolved in resolve() so tests can inject mocks.
        self.filter_engine = filter_engine or FilterEngine(project)

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_llm(user=self.user, temperature=0.1, format_json=True)
        return self._llm

    @staticmethod
    def extract_guids(message: str) -> list[str]:
        """Extract IFC GlobalIds (22-char base64) from a free-text message."""
        return _GUID_PATTERN.findall(message)

    @staticmethod
    def extract_step_ids(message: str) -> list[str]:
        """Extract Revit-style step-ID suffixes (``:NNNNN``) from a message.

        Returns a deduplicated list preserving first-appearance order.
        Used as a safety net when the LLM extractor drops one of several
        entity references; the post-pass adds these back so per-name
        resolution gets a chance to find them.
        """
        seen: set[str] = set()
        out: list[str] = []
        for match in _STEP_ID_PATTERN.findall(message):
            if match not in seen:
                seen.add(match)
                out.append(match)
        return out

    @staticmethod
    def _augment_with_step_ids(extracted: dict, user_message: str) -> dict:
        """Ensure every step-ID present in the message is in ``entity_names``.

        Small models occasionally emit ``entity_names`` with only one item
        when the user typed two or more entity references. Step IDs
        (``:NNNNN``) are deterministic enough to safety-net: any step ID
        in the message that isn't already represented in the LLM's
        extraction is added, and the scope is promoted to ``specific_multi``
        when the augmented set has 2+ items.

        Mutates and returns ``extracted`` for call-site clarity.
        """
        step_ids = EntityNameResolver.extract_step_ids(user_message)
        if not step_ids:
            return extracted

        # Build the existing-names set from whatever shape the LLM produced.
        existing: list[str] = []
        existing_lower: set[str] = set()

        def _push(name: str) -> None:
            n = name.strip()
            if not n:
                return
            key = n.lower()
            if key in existing_lower:
                return
            existing_lower.add(key)
            existing.append(n)

        names_field = extracted.get("entity_names")
        if isinstance(names_field, list):
            for n in names_field:
                if isinstance(n, str):
                    _push(n)
        single_name = extracted.get("entity_name")
        if isinstance(single_name, str):
            _push(single_name)

        # Add any step-IDs not already covered by an existing name.
        added = False
        for sid in step_ids:
            sid_lower = sid.lower()
            if sid_lower in existing_lower:
                continue
            if any(sid_lower in e for e in existing_lower):
                continue
            _push(sid)
            added = True

        if not added:
            return extracted

        # Promote scope when the augmented list crosses the multi threshold.
        if len(existing) >= 2:
            extracted["scope"] = "specific_multi"
            extracted["entity_names"] = existing
            extracted.pop("entity_name", None)
        elif extracted.get("scope") in (None, "unknown"):
            extracted["scope"] = "specific"
            extracted["entity_name"] = existing[0]

        logger.info(
            "Resolver: step-ID safety net augmented entity references → %s (scope=%s)",
            existing,
            extracted.get("scope"),
        )
        return extracted

    def resolve(
        self,
        user_message: str,
        mode: str = MODE_EXISTING_TARGET,
    ) -> ResolutionResult:
        """Run the layered resolution pipeline. Returns ``_EMPTY`` on miss.

        Layers, in order of cost:
          1. GUID regex (free, deterministic).
          2. Iteration 0 — focused LLM extraction + DB lookup with progressive
             trim. Most prompts resolve here.
          3. Iteration 1 — LLM refinement with DB-grounded hints. Fires only
             when the LLM said `scope=specific` but iteration 0 couldn't pin
             a unique match (0 or N matches after trim).
          4. Iteration 2 — final wider refinement. Last LLM call before
             fall-through; broader hint set, opt-out instruction.
          5. Empty fallback — pipeline runs as today.

        Hard cap: 3 LLM calls (the initial extraction + 2 refinement turns).

        ``mode`` selects the targeting strategy. The default
        ``MODE_EXISTING_TARGET`` runs the full pipeline against existing DB
        entities. ``MODE_PARENT_TARGET`` is identical today (commit 2 will
        differentiate input wording). ``MODE_NEW_TARGET`` and
        ``MODE_NO_TARGET`` short-circuit to an empty result; the V2 pipeline
        introduced in the next commit handles them via dedicated paths.
        """
        if mode not in VALID_MODES:
            raise ValueError(
                f"Unknown resolver mode: {mode!r}. Expected one of {sorted(VALID_MODES)}."
            )

        if mode == MODE_NO_TARGET:
            return _EMPTY

        if mode == MODE_NEW_TARGET:
            # CREATE workflow: the proposed names should NOT match existing
            # entities — running this through the lookup pipeline would
            # produce phantom matches via the trim fallback. The V2
            # slot_extractor returns the proposed names directly; the
            # resolver has nothing to do here yet.
            return _EMPTY

        # Pass 1: deterministic GUID extraction. Free, never invokes the LLM.
        guid_matches = self._resolve_by_guid(user_message)
        if guid_matches is not None:
            return guid_matches

        # Iteration 0: focused LLM extraction.
        extracted = self._llm_extract(user_message)
        if extracted is None:
            return _EMPTY
        # Deterministic safety net: if the user mentioned ``:NNNNN`` step IDs
        # that the LLM dropped, add them back so per-name resolution gets a
        # chance to find each. Promotes scope to specific_multi as needed.
        extracted = self._augment_with_step_ids(extracted, user_message)
        result = self._db_resolve(extracted)
        if result.is_unique:
            return result

        # Only iterate when the user wanted a SPECIFIC entity but we couldn't
        # pin one. Filtered / all_of_type / unknown results are meaningful as-is.
        if extracted.get("scope") != "specific":
            return result

        # Iteration 1: tighter hints (token length ≥ 4, cap 20).
        extracted_1 = self._refine_extraction(
            user_message=user_message,
            previous_extracted=extracted,
            previous_result=result,
            token_min_length=4,
            max_hints=20,
            is_final=False,
        )
        if extracted_1 is not None:
            result_1 = self._db_resolve(extracted_1)
            if result_1.is_unique:
                return result_1
            # If iteration 1 reconsidered the scope to a non-specific category
            # (all_of_type / filtered / specific_multi), accept that as-is —
            # we don't iterate further on category-shaped resolutions.
            if extracted_1.get("scope") != "specific":
                return result_1
            # Carry forward into iteration 2's "previous attempt" context.
            extracted = extracted_1
            result = result_1

        # Iteration 2: broader hints (token length ≥ 3, cap 50). Last attempt.
        extracted_2 = self._refine_extraction(
            user_message=user_message,
            previous_extracted=extracted,
            previous_result=result,
            token_min_length=3,
            max_hints=50,
            is_final=True,
        )
        if extracted_2 is not None:
            result_2 = self._db_resolve(extracted_2)
            if result_2.is_unique:
                return result_2
            # Same scope-reconsideration acceptance as iteration 1.
            if extracted_2.get("scope") != "specific":
                return result_2

        logger.info("Resolver: iterations exhausted; falling back to today's pipeline")
        return _EMPTY

    # ── Pass implementations ──────────────────────────────────────────

    def _resolve_by_guid(self, user_message: str) -> ResolutionResult | None:
        guids = self.extract_guids(user_message)
        if not guids:
            return None
        qs = IFCEntity.objects.filter(
            ifc_file__project=self.project,
            ifc_file__status="completed",
            global_id__in=guids,
        )
        entities = list(qs)
        if not entities:
            # GUID-shaped but no DB match (likely a token coincidence) —
            # fall through to LLM extraction rather than returning empty,
            # because the LLM may still find a name reference.
            return None
        logger.info(
            "Resolver: GUID match — %d entit%s", len(entities), "y" if len(entities) == 1 else "ies"
        )
        return ResolutionResult(
            entities=entities,
            ifc_type_hint=None,
            scope="specific",
            diagnostic=f"matched {len(entities)} entit{'y' if len(entities) == 1 else 'ies'} by GUID",
        )

    def _llm_extract(self, user_message: str) -> dict | None:
        """Invoke the focused extraction LLM. Returns parsed dict or None."""
        try:
            response = self.llm.invoke(
                [
                    SystemMessage(content=EXTRACTION_SYSTEM_PROMPT),
                    HumanMessage(
                        content=_EXTRACTION_USER_TEMPLATE.format(user_message=user_message)
                    ),
                ]
            )
        except Exception as e:
            logger.warning("Resolver LLM call failed: %s — falling back to today's pipeline", e)
            return None

        try:
            data = json.loads(response.content)
        except (json.JSONDecodeError, AttributeError, TypeError):
            logger.warning("Resolver LLM returned non-JSON: %r", getattr(response, "content", None))
            return None

        if not isinstance(data, dict):
            return None
        return data

    def _db_resolve(self, extracted: dict) -> ResolutionResult:
        scope = extracted.get("scope", "unknown")
        ifc_type = extracted.get("ifc_type")
        entity_name = extracted.get("entity_name")
        entity_names = extracted.get("entity_names")
        filter_hints = extracted.get("filter_hints")

        if scope == "specific" and isinstance(entity_name, str) and entity_name.strip():
            return self._resolve_specific(ifc_type, entity_name.strip())

        if scope == "specific_multi" and isinstance(entity_names, list) and entity_names:
            cleaned = [n.strip() for n in entity_names if isinstance(n, str) and n.strip()]
            if cleaned:
                return self._resolve_specific_multi(ifc_type, cleaned)

        if scope == "all_of_type" and isinstance(ifc_type, str) and ifc_type.strip():
            return self._resolve_all_of_type(ifc_type.strip())

        if scope == "filtered" and isinstance(filter_hints, dict) and filter_hints:
            return self._resolve_filtered(ifc_type, filter_hints)

        # scope == "unknown", or required hints missing — defer to classifier.
        logger.info(
            "Resolver: extraction returned scope=%r; falling back to today's pipeline", scope
        )
        return _EMPTY

    def _resolve_specific(self, ifc_type: str | None, entity_name: str) -> ResolutionResult:
        # Try the full extracted name first; on 0 matches, progressively drop
        # the leading whitespace-separated token and retry. Small models often
        # include conversational lead-in ("wall:", "the door") or duplicate
        # type tokens ("IfcWall ") that don't appear in the actual entity.name
        # field, so a single icontains query fails even when the rest of the
        # string is a real entity name.
        #
        # Two guards prevent the trim from drifting into noise:
        #   - Trim floor: a fragment must be ≥ _TRIM_FLOOR_CHARS chars OR
        #     have ≥ _TRIM_FLOOR_TOKENS tokens. Single-letter or two-letter
        #     trims ("A", "of") never reach the DB.
        #   - Match-count cap: a TRIMMED fragment matching > _TRIM_MATCH_CAP
        #     entities is treated as noise and skipped. The original name
        #     is exempt — if the user named a wide category, that's fine.
        type_filter = ifc_type.strip() if isinstance(ifc_type, str) and ifc_type.strip() else None
        candidates: list[str] = []
        seen: set[str] = set()

        original = entity_name.strip()
        if original:
            candidates.append(original)
            seen.add(original)

            # Up to 3 progressive-trim fallbacks (4 candidates total).
            remaining = original
            for _ in range(3):
                parts = remaining.split(maxsplit=1)
                if len(parts) < 2:
                    break
                remaining = parts[1].strip()
                if not remaining or remaining in seen:
                    break
                token_count = len(remaining.split())
                if len(remaining) < _TRIM_FLOOR_CHARS and token_count < _TRIM_FLOOR_TOKENS:
                    # Both length and token count decrease monotonically with
                    # each trim — once below the floor, every subsequent trim
                    # is shorter still, so break instead of continuing.
                    logger.info(
                        "Resolver: trim floor — skipping fragment %r (%d chars, %d tokens)",
                        remaining,
                        len(remaining),
                        token_count,
                    )
                    break
                candidates.append(remaining)
                seen.add(remaining)

        for attempt in candidates:
            spec: dict = {"name_pattern": attempt}
            if type_filter:
                spec["ifc_type"] = type_filter
            try:
                qs = self.filter_engine.resolve(spec)
            except ValueError:
                continue  # 0 matches — try the next, more-trimmed variant
            entities = list(qs[:50])

            is_trimmed = attempt != original
            if is_trimmed and len(entities) > _TRIM_MATCH_CAP:
                logger.info(
                    "Resolver: trim noise filter — fragment %r matched %d > %d, skipping",
                    attempt,
                    len(entities),
                    _TRIM_MATCH_CAP,
                )
                continue

            if attempt == original:
                logger.info(
                    "Resolver: specific scope, name=%r, %d candidate%s",
                    attempt,
                    len(entities),
                    "" if len(entities) == 1 else "s",
                )
            else:
                logger.info(
                    "Resolver: name '%s' matched 0; trimmed to '%s' matched %d candidate%s",
                    entity_name,
                    attempt,
                    len(entities),
                    "" if len(entities) == 1 else "s",
                )
            return ResolutionResult(
                entities=entities,
                ifc_type_hint=ifc_type if isinstance(ifc_type, str) else None,
                scope="specific",
                diagnostic=(
                    f"matched {len(entities)} entit{'y' if len(entities) == 1 else 'ies'} "
                    f"by name '{attempt}'"
                ),
            )

        logger.info(
            "Resolver: name '%s' matched 0 entities (all variants); falling back",
            entity_name,
        )
        return _EMPTY

    def _resolve_all_of_type(self, ifc_type: str) -> ResolutionResult:
        try:
            qs = self.filter_engine.resolve({"ifc_type": ifc_type})
        except ValueError:
            return _EMPTY
        entities = list(qs[:200])
        logger.info(
            "Resolver: all_of_type=%s, %d entit%s",
            ifc_type,
            len(entities),
            "y" if len(entities) == 1 else "ies",
        )
        return ResolutionResult(
            entities=entities,
            ifc_type_hint=ifc_type,
            scope="all_of_type",
            diagnostic=f"all {len(entities)} {ifc_type} in project",
        )

    def _resolve_filtered(self, ifc_type: str | None, filter_hints: dict) -> ResolutionResult:
        spec: dict = {"property_match": filter_hints}
        if isinstance(ifc_type, str) and ifc_type.strip():
            spec["ifc_type"] = ifc_type.strip()
        try:
            qs = self.filter_engine.resolve(spec)
        except ValueError:
            return _EMPTY
        entities = list(qs[:200])
        logger.info(
            "Resolver: filtered scope, hints=%r, %d entit%s",
            filter_hints,
            len(entities),
            "y" if len(entities) == 1 else "ies",
        )
        return ResolutionResult(
            entities=entities,
            ifc_type_hint=ifc_type if isinstance(ifc_type, str) else None,
            scope="filtered",
            diagnostic=f"matched {len(entities)} entit{'y' if len(entities) == 1 else 'ies'} by filter",
        )

    def _resolve_specific_multi(
        self, ifc_type: str | None, entity_names: list[str]
    ) -> ResolutionResult:
        """Resolve a list of named entities. Each name goes through the same
        single-name resolution (with progressive trim); per-name results are
        unioned and de-duplicated. Diagnostic notes any names that didn't match
        anything so the user can see what was missed.

        Returns ``_EMPTY`` only when NO name matched anything; partial-miss
        cases still surface the entities that did match (with the misses
        listed in the diagnostic).
        """
        seen: set = set()
        matched: list[IFCEntity] = []
        misses: list[str] = []
        for name in entity_names:
            sub = self._resolve_specific(ifc_type, name)
            if sub.is_empty:
                misses.append(name)
                continue
            for entity in sub.entities:
                if entity.pk not in seen:
                    seen.add(entity.pk)
                    matched.append(entity)

        if not matched:
            logger.info(
                "Resolver: specific_multi matched 0 entities across %d references; falling back",
                len(entity_names),
            )
            return _EMPTY

        diagnostic = (
            f"matched {len(matched)} entit{'y' if len(matched) == 1 else 'ies'} "
            f"across {len(entity_names)} reference{'s' if len(entity_names) != 1 else ''}"
        )
        if misses:
            diagnostic += f"; {len(misses)} unmatched: {misses}"
        logger.info("Resolver: specific_multi — %s", diagnostic)
        return ResolutionResult(
            entities=matched,
            ifc_type_hint=ifc_type if isinstance(ifc_type, str) else None,
            scope="specific_multi",
            diagnostic=diagnostic,
            misses=tuple(misses),
        )

    # ── Iterative refinement (iterations 1 and 2) ────────────────────

    def _refine_extraction(
        self,
        user_message: str,
        previous_extracted: dict,
        previous_result: ResolutionResult,
        token_min_length: int,
        max_hints: int,
        is_final: bool,
    ) -> dict | None:
        """Run one refinement LLM call with DB-grounded hints.

        Returns the refined extraction dict on success, or ``None`` when
        the iteration should not be acted on (no usable previous name, no
        hints, LLM error, non-JSON output, or the LLM opted out with
        ``scope="unknown"``). Callers fall through to the next iteration
        or to the empty result.
        """
        previous_name = (previous_extracted.get("entity_name") or "").strip()
        if not previous_name:
            return None

        ifc_type = previous_extracted.get("ifc_type")
        hints = self._generate_db_hints(
            previous_name=previous_name,
            ifc_type=ifc_type,
            previous_matches=previous_result.entities,
            token_min_length=token_min_length,
            max_hints=max_hints,
        )
        if not hints:
            return None

        iteration_label = "iteration 2" if is_final else "iteration 1"
        logger.info(
            "Resolver %s: previous_name=%r matched %d, %d hints",
            iteration_label,
            previous_name,
            len(previous_result.entities),
            len(hints),
        )

        if previous_result.is_empty:
            match_summary = "matched 0 entities."
        else:
            sample = [e.name for e in previous_result.entities[:5]]
            more = (
                ""
                if len(previous_result.entities) <= 5
                else f" (and {len(previous_result.entities) - 5} more)"
            )
            match_summary = (
                f"matched {len(previous_result.entities)} entities: {', '.join(sample)}{more}."
            )

        opt_out_reminder = (
            "If still uncertain after reviewing these candidates and the scope "
            "options above, return scope='unknown' so the user can be asked for "
            "clarification."
            if is_final
            else "If none of the scope options above fits, return scope='unknown'."
        )

        user_prompt = _REFINEMENT_USER_TEMPLATE.format(
            user_message=user_message,
            previous_entity_name=previous_name,
            match_summary=match_summary,
            candidates="\n".join(f"  - {h}" for h in hints),
            opt_out_reminder=opt_out_reminder,
        )

        try:
            response = self.llm.invoke(
                [
                    SystemMessage(content=EXTRACTION_SYSTEM_PROMPT),
                    HumanMessage(content=user_prompt),
                ]
            )
        except Exception as e:
            logger.warning("Resolver %s LLM call failed: %s", iteration_label, e)
            return None

        try:
            data = json.loads(response.content)
        except (json.JSONDecodeError, AttributeError, TypeError):
            logger.warning(
                "Resolver %s returned non-JSON: %r",
                iteration_label,
                getattr(response, "content", None),
            )
            return None
        if not isinstance(data, dict):
            return None
        if data.get("scope") == "unknown":
            logger.info(
                "Resolver %s: LLM returned scope=unknown — falling through", iteration_label
            )
            return None
        return data

    def _generate_db_hints(
        self,
        previous_name: str,
        ifc_type: str | None,
        previous_matches: list,
        token_min_length: int,
        max_hints: int,
    ) -> list[str]:
        """Tokenise ``previous_name``, query the DB for each token, return up
        to ``max_hints`` real entity names (deduplicated, scoped to the
        project + ifc_type). Falls back to a sample of the project's
        matching-type entities when no token matches anything.

        Each token query is one indexed ``name__icontains`` lookup; tokens
        below ``token_min_length`` are dropped to keep noise low.
        """
        qs = IFCEntity.objects.filter(
            ifc_file__project=self.project,
            ifc_file__status="completed",
        )
        if isinstance(ifc_type, str) and ifc_type.strip():
            qs = qs.filter(ifc_type__istartswith=ifc_type.strip())

        hints: list[str] = []
        seen: set[str] = set()

        # Seed with any ambiguous matches the previous iteration already had.
        for entity in previous_matches:
            name = getattr(entity, "name", None)
            if name and name not in seen:
                seen.add(name)
                hints.append(name)
                if len(hints) >= max_hints:
                    return hints

        tokens = [tok for tok in re.split(r"[\s:]+", previous_name) if len(tok) >= token_min_length]
        for token in tokens:
            if len(hints) >= max_hints:
                break
            for name in qs.filter(name__icontains=token).values_list("name", flat=True)[:max_hints]:
                if name and name not in seen:
                    seen.add(name)
                    hints.append(name)
                    if len(hints) >= max_hints:
                        break

        if hints:
            return hints

        # No token matched anything → seed the LLM with a sample of real
        # entities of the right type so it has SOMETHING to pick from.
        sample_size = min(10, max_hints)
        sample = list(qs.values_list("name", flat=True)[:sample_size])
        return [name for name in sample if name]
