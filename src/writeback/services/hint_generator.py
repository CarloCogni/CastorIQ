# writeback/services/hint_generator.py
"""Generate user-facing hints for V2 router rejections.

Three strategies, tried in order:
  1. Templated     — static per-category template, zero cost.
  2. Registry      — fuzzy-match the user's wording against the standard
                     pset registry / project's IFC entity names. Zero
                     cost, can't hallucinate, surfaces "did you mean...?".
  3. LLM-fallback  — narrow LLM call gated by a category whitelist + a
                     2-second hard timeout + registry validation of the
                     suggestion + cache. The whitelist starts empty so
                     this never fires unless explicitly enabled.

The strategies are orthogonal to the writeback Tier 1 / Tier 2 / Tier 3
execution pipeline — they only run after the router has already chosen
Tier 0 (REJECT) and never produce a proposal or modify the IFC.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from re import compile as re_compile

from django.conf import settings
from django.core.cache import cache
from langchain_core.messages import HumanMessage, SystemMessage

from core.llm import safe_invoke
from ifc_processor.models import IFCEntity
from ifc_processor.services.ifc_standard_psets import (
    STANDARD_PSETS,
    lookup_property,
)

from .tier_router import (
    REJECT_CATEGORY_ENTITY_NOT_FOUND,
    REJECT_CATEGORY_OUT_OF_SCOPE,
    REJECT_CATEGORY_PSET_UNKNOWN,
    REJECT_CATEGORY_UNCLEAR,
)

logger = logging.getLogger(__name__)


# Source markers on a Hint identify which strategy produced it.
SOURCE_TEMPLATED = "templated"
SOURCE_REGISTRY = "registry"
SOURCE_LLM = "llm"
SOURCE_NONE = ""


# Similarity floors. Below the floor → no hint (don't show a wild guess).
# Property floor is loose because the prefix-gate in ``_best_match`` is
# the strong signal — character-level ratio is just a tiebreaker among
# prefix-matched candidates.
_PROPERTY_SIMILARITY_FLOOR = 0.4
_ENTITY_SIMILARITY_FLOOR = 0.45


# Pattern used to extract a Pset.Property reference from an LLM hint
# string for registry validation.
_PSET_PROP_RE = re_compile(r"\bPset_[A-Za-z0-9]+\.[A-Za-z0-9_]+\b")


@dataclass(frozen=True)
class Hint:
    """A user-facing rejection hint plus the strategy that produced it."""

    text: str
    source: str

    @property
    def is_empty(self) -> bool:
        return not self.text


_EMPTY_HINT = Hint("", SOURCE_NONE)


# Minimum leading-prefix overlap (case-insensitive, alpha-only) for a
# candidate property name to be eligible for a "did you mean" hint. Three
# characters is enough to catch real cognates ("FireResistanceDuration"
# vs "FireRating" both share "fir") and reject false friends ("Inspector"
# vs "Spectrum" share nothing at the start).
_PROPERTY_PREFIX_LEN = 3


def _alpha_prefix(s: str, n: int) -> str:
    """Return the first ``n`` alphabetic characters of ``s``, lowercased.

    Skips leading whitespace and non-alpha characters; stops at the first
    non-alpha after the run starts. Tolerates Inputs like ``" thermal mass"``
    (returns ``"the"``) and ``"FireResistanceDuration"`` (returns ``"fir"``).
    """
    if not s or n <= 0:
        return ""
    out: list[str] = []
    started = False
    for c in s:
        if c.isalpha():
            out.append(c.lower())
            started = True
            if len(out) >= n:
                break
        elif started:
            break
    return "".join(out)


class HintGenerator:
    """Compose a one-line hint for a router rejection.

    Stateless across calls; instantiate once per ``ModificationService``.
    """

    def __init__(self, project, user=None) -> None:
        self.project = project
        self._user = user

    # ── Public surface ────────────────────────────────────────────────

    def suggest(self, *, reason_category: str, payload: dict) -> Hint:
        """Try Templated → Registry → LLM-fallback in order. First non-empty wins."""
        templated = self._try_templated(reason_category, payload)
        if not templated.is_empty:
            return templated

        registry = self._try_registry(reason_category, payload)
        if not registry.is_empty:
            return registry

        return self._try_llm(reason_category, payload)

    # ── Strategy 1: Templated ─────────────────────────────────────────

    def _try_templated(self, category: str, payload: dict) -> Hint:
        if category == REJECT_CATEGORY_OUT_OF_SCOPE:
            # The triage's reason is already specific (e.g. "Geometric
            # modifications are out of scope" vs "This is a query, not a
            # modification"). Pick the templated suffix that matches the
            # reason — query rejects get an Ask-tab pointer; everything
            # else (geometry, etc.) gets the scope reminder. Keyword
            # search is narrow on purpose: "modification" appears in
            # geometry's reason too ("Geometric modifications") so we
            # match on the verbs / nouns specific to queries.
            reason_lower = (payload.get("reason") or "").lower()
            is_query_like = (
                "query" in reason_lower
                or "show me" in reason_lower
                or "information request" in reason_lower
                or "ask " in reason_lower
                or "not a modification" in reason_lower
            )
            if is_query_like:
                hint_text = (
                    "If you wanted to ask about the model rather than change "
                    "it, switch to the Ask tab."
                )
            else:
                hint_text = (
                    "Castor handles properties, attributes, classifications, materials, "
                    "and zone/space creation — geometry editing isn't supported."
                )
            return Hint(text=hint_text, source=SOURCE_TEMPLATED)

        if category == REJECT_CATEGORY_UNCLEAR:
            missing = payload.get("missing") or []
            if "value" in missing and "target" in missing:
                hint = (
                    "Tell me which entities to change AND the new value, "
                    "e.g. 'set FireRating to EI120 on all walls'."
                )
            elif "value" in missing:
                hint = "Add the new value, e.g. 'set FireRating to EI120 on wall :285330'."
            elif "target" in missing:
                hint = (
                    "Add the entity reference, e.g. 'on wall :285330' or 'on all external walls'."
                )
            elif "request" in missing or not missing:
                hint = (
                    "Phrase the change as 'set <property> to <value> on <entity>' "
                    "or 'rename <entity> to <new name>'."
                )
            else:
                hint = f"Provide the missing piece(s): {', '.join(missing)}."
            return Hint(text=hint, source=SOURCE_TEMPLATED)

        # Other categories don't have a useful one-size template — defer
        # to Strategy 2 (registry-grounded) for actionable suggestions.
        return _EMPTY_HINT

    # ── Strategy 2: Registry-grounded ─────────────────────────────────

    def _try_registry(self, category: str, payload: dict) -> Hint:
        if category == REJECT_CATEGORY_PSET_UNKNOWN:
            return self._suggest_property(payload)
        if category == REJECT_CATEGORY_ENTITY_NOT_FOUND:
            return self._suggest_entity(payload)
        return _EMPTY_HINT

    def _suggest_property(self, payload: dict) -> Hint:
        """For a property the registry doesn't know, suggest a near match.

        Searches every property name in ``STANDARD_PSETS`` for a similar
        spelling. When an ``ifc_type_hint`` is present, the canonical
        ``Pset_<Type>Common`` (e.g. ``Pset_WallCommon`` for ``IfcWall``)
        is searched first; broader matches and the global scan are
        fallback layers. This mirrors the tier_router's pset-inference
        priority and avoids "Pset_CurtainWallCommon over Pset_WallCommon"
        false positives.
        """
        target_prop = (payload.get("property") or "").strip()
        if not target_prop or target_prop == "?":
            return _EMPTY_HINT

        ifc_type = (payload.get("ifc_type_hint") or "").strip()

        # Layered candidate set: (1) canonical Pset_<Type>Common first,
        # (2) other psets whose name STARTS with Pset_<Type>, (3) global.
        layers: list[list[tuple[str, str]]] = []
        if ifc_type:
            short = ifc_type.replace("Ifc", "").replace("StandardCase", "")
            canonical_pset = f"Pset_{short}Common"
            type_prefix = f"Pset_{short}".lower()

            layer_canonical: list[tuple[str, str]] = []
            layer_prefix: list[tuple[str, str]] = []
            for pset_name, props in STANDARD_PSETS.items():
                if pset_name == canonical_pset:
                    bucket = layer_canonical
                elif pset_name.lower().startswith(type_prefix):
                    bucket = layer_prefix
                else:
                    continue
                for prop_name in props:
                    bucket.append((pset_name, prop_name))
            if layer_canonical:
                layers.append(layer_canonical)
            if layer_prefix:
                layers.append(layer_prefix)

        # Final fallback layer: every standard pset.
        global_layer: list[tuple[str, str]] = []
        for pset_name, props in STANDARD_PSETS.items():
            for prop_name in props:
                global_layer.append((pset_name, prop_name))
        layers.append(global_layer)

        # Try each layer in priority order; the first one that produces
        # a match above the similarity floor wins.
        for layer in layers:
            best = self._best_match(target_prop, [c[1] for c in layer])
            if best is None:
                continue
            idx, score = best
            if score < _PROPERTY_SIMILARITY_FLOOR:
                continue
            suggested_pset, suggested_prop = layer[idx]
            return Hint(
                text=(
                    f"Did you mean `{suggested_pset}.{suggested_prop}`? "
                    f"That's the closest standard property to {target_prop!r}."
                ),
                source=SOURCE_REGISTRY,
            )

        return _EMPTY_HINT

    def _suggest_entity(self, payload: dict) -> Hint:
        """For a target_phrase that matched 0 entities, suggest a near match.

        Pulls candidate names from this project's processed IFCEntity
        rows and uses difflib for similarity. Capped to keep memory
        bounded on large projects.
        """
        target = (payload.get("target_phrase") or "").strip()
        if not target or target == "the requested entities":
            return _EMPTY_HINT

        names = list(
            IFCEntity.objects.filter(
                ifc_file__project=self.project,
                ifc_file__status="completed",
            )
            .exclude(name="")
            .values_list("name", flat=True)[:500]
        )
        if not names:
            return _EMPTY_HINT

        best = self._best_match(target, names)
        if best is None:
            return _EMPTY_HINT
        idx, score = best
        if score < _ENTITY_SIMILARITY_FLOOR:
            return _EMPTY_HINT

        return Hint(
            text=f"Did you mean `{names[idx]}`?",
            source=SOURCE_REGISTRY,
        )

    @staticmethod
    def _best_match(needle: str, haystack: list[str]) -> tuple[int, float] | None:
        """Return ``(index, ratio)`` for the haystack entry most similar
        to ``needle``. ``None`` if haystack is empty or no candidate
        passes the prefix gate.

        Gating: each candidate must share its first ``_PROPERTY_PREFIX_LEN``
        alphabetic characters with the needle (case-insensitive). This is
        what distinguishes cognates ("FireResistanceDuration"/"FireRating"
        both start with "fir") from false friends ("Inspector"/"Spectrum"
        — "ins" vs "spe"). Among candidates that pass the gate, the one
        with the highest difflib ratio wins.
        """
        if not haystack:
            return None
        needle_lower = needle.lower()
        needle_prefix = _alpha_prefix(needle, _PROPERTY_PREFIX_LEN)
        best_idx = -1
        best_ratio = 0.0
        for i, candidate in enumerate(haystack):
            if needle_prefix and _alpha_prefix(candidate, _PROPERTY_PREFIX_LEN) != needle_prefix:
                continue  # prefix mismatch — false-friend filter
            ratio = difflib.SequenceMatcher(None, needle_lower, candidate.lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_idx = i
        if best_idx < 0:
            return None
        return best_idx, best_ratio

    # ── Strategy 3: LLM-fallback (gated, defensive) ───────────────────

    def _try_llm(self, category: str, payload: dict) -> Hint:
        """Optional LLM-generated hint, gated by category whitelist + flags.

        Only fires when:
          * ``settings.WRITEBACK_HINT_LLM_FALLBACK`` is True (default).
          * ``category`` is in ``settings.WRITEBACK_HINT_LLM_CATEGORIES`` (empty by default).
          * The cache miss path runs to completion within 2 seconds.
          * The LLM's suggestion passes registry validation.

        Any failure falls back to an empty Hint silently.
        """
        if not getattr(settings, "WRITEBACK_HINT_LLM_FALLBACK", False):
            return _EMPTY_HINT
        whitelist = tuple(getattr(settings, "WRITEBACK_HINT_LLM_CATEGORIES", ()))
        if category not in whitelist:
            return _EMPTY_HINT

        cache_key = self._cache_key(category, payload)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            raw = safe_invoke(
                self._llm_invoke,
                category,
                payload,
                timeout=2,
                thread_name_prefix="hint-llm",
            )
        except FuturesTimeout:
            logger.warning("Hint LLM timed out (>2s) for category=%s", category)
            return _EMPTY_HINT
        except Exception as e:
            logger.warning("Hint LLM error for category=%s: %s", category, e)
            return _EMPTY_HINT

        suggestion = self._parse_and_validate(raw)
        if not suggestion:
            return _EMPTY_HINT

        hint = Hint(text=suggestion, source=SOURCE_LLM)
        cache.set(cache_key, hint, timeout=3600)
        return hint

    def _llm_invoke(self, category: str, payload: dict) -> str:
        """Invoke the focused hint LLM. Returns raw response content."""
        # Local import keeps the hot path (Strategy 1+2) free of LangChain
        # imports when WRITEBACK_HINT_LLM_FALLBACK is off.
        from core.llm import get_llm

        llm = get_llm(user=self._user, temperature=0.0, format_json=True)
        system = (
            "You suggest a one-sentence hint to help a user re-phrase a rejected "
            'BIM modification request. Output ONLY JSON: {"suggestion": "<≤120 chars>"}.\n'
            "If you can't suggest anything specific and useful, return an empty "
            "string for suggestion. Do NOT invent property or entity names — "
            "only refer to names you can derive from the rejection payload."
        )
        user_msg = (
            f"Rejection category: {category}\nPayload: {json.dumps(payload, default=str)}\nHint:"
        )
        response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user_msg)])
        return getattr(response, "content", "") or ""

    def _parse_and_validate(self, raw: str) -> str:
        """Parse the LLM JSON and validate any pset/property reference.

        Drops the suggestion if it cites a Pset_*.PropertyName pair that
        isn't in the registry — better no hint than a fabricated one.
        """
        if not raw or not raw.strip():
            return ""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Hint LLM returned non-JSON: %r", raw[:200])
            return ""
        if not isinstance(data, dict):
            return ""
        text = (data.get("suggestion") or "").strip()
        if not text:
            return ""
        # Validate every Pset_X.Y reference in the suggestion.
        for match in _PSET_PROP_RE.findall(text):
            pset_name, prop_name = match.split(".", 1)
            if lookup_property(pset_name, prop_name) is None:
                logger.info(
                    "Hint LLM suggestion mentions unknown %s.%s — dropping",
                    pset_name,
                    prop_name,
                )
                return ""
        return text[:200]  # belt-and-braces length cap

    @staticmethod
    def _cache_key(category: str, payload: dict) -> str:
        normalized = json.dumps(payload, sort_keys=True, default=str)
        digest = hashlib.md5(normalized.encode("utf-8")).hexdigest()
        return f"writeback:hint:{category}:{digest}"
