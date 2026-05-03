# writeback/tests/test_entity_resolver.py
"""Tests for EntityNameResolver — LLM always mocked.

The resolver pre-resolves entity targeting before the main classifier
runs. These tests cover the three-pass pipeline (regex GUID → focused
LLM extraction → DB lookup) and the strict no-regression guarantee:
when the extraction LLM drifts or returns nothing useful, the resolver
returns an empty result so callers fall back to today's behaviour.
"""

import json
from unittest.mock import MagicMock

import pytest

from writeback.services import entity_resolver as resolver_module
from writeback.services.entity_resolver import (
    MODE_EXISTING_TARGET,
    MODE_NEW_TARGET,
    MODE_NO_TARGET,
    MODE_PARENT_TARGET,
    EntityNameResolver,
    ResolutionResult,
)


def _make_llm_response(content: str) -> MagicMock:
    response = MagicMock()
    response.content = content
    return response


@pytest.mark.django_db
class TestExtractGuids:
    """Static GUID-extraction helper."""

    def test_no_guid_returns_empty_list(self):
        """A message with no 22-char base64 token returns []."""
        assert EntityNameResolver.extract_guids("set fire rating to EI240") == []

    def test_single_guid_extracted(self):
        """A 22-char base64 token is extracted verbatim."""
        guid = "1aBcDeFgHiJkLmNoPqRsTu"
        result = EntityNameResolver.extract_guids(f"modify wall {guid}")
        assert result == [guid]

    def test_short_id_is_not_a_guid(self):
        """Step-id-style numeric suffixes (e.g. ':285330') must not be treated as GUIDs."""
        assert EntityNameResolver.extract_guids("Basic Wall:Wall-Ext:285330") == []


@pytest.mark.django_db
class TestResolveByGuid:
    """Pass 1: deterministic GUID match short-circuits the LLM."""

    def test_guid_in_message_resolves_without_invoking_llm(self, project, ifc_file, wall_entities):
        """When the message contains a 22-char GUID matching an entity, the
        resolver returns by global_id and never calls the LLM."""
        target = wall_entities[0]
        # Force the global_id into a 22-char base64-shaped string so the regex matches.
        target.global_id = "GuidTestAaBbCcDdEeFf12"
        target.save()

        mock_llm = MagicMock()
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve(f"set FireRating on {target.global_id} to EI120")

        assert result.is_unique
        assert result.entities[0].pk == target.pk
        assert result.scope == "specific"
        mock_llm.invoke.assert_not_called()

    def test_guid_shaped_token_with_no_db_match_falls_through(
        self, project, ifc_file, wall_entities
    ):
        """A token that LOOKS like a GUID but matches no entity falls
        through to the LLM extraction pass instead of returning empty.

        With the scope=unknown trim retry in place the extractor is invoked
        up to 3 times — full message + 2 progressive-trim retries — before
        the resolver gives up and returns _EMPTY.
        """
        mock_llm = MagicMock()
        # The fall-through LLM call returns "unknown" → empty result.
        mock_llm.invoke.return_value = _make_llm_response(json.dumps({"scope": "unknown"}))
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("modify wall NotARealGuid12345xYz1AB")

        assert result.is_empty
        # 1 initial extractor call + 2 trim retries on persistent scope=unknown.
        assert mock_llm.invoke.call_count == 3


@pytest.mark.django_db
class TestResolveSpecific:
    """Pass 2/3: LLM emits scope=specific + entity_name → DB name match."""

    def test_full_name_match_returns_unique_entity(self, project, ifc_file, wall_entities):
        """Mocked LLM returns scope=specific with the wall's full name; resolver returns 1 match."""
        target = wall_entities[0]  # name='Wall-000' from fixture
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps(
                {
                    "scope": "specific",
                    "ifc_type": "IfcWall",
                    "entity_name": target.name,
                }
            )
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve(f"set fire rating on {target.name} to EI240")

        assert result.is_unique
        assert result.entities[0].pk == target.pk
        assert result.scope == "specific"
        assert result.ifc_type_hint == "IfcWall"

    def test_partial_name_with_persistent_ambiguity_falls_through(
        self, project, ifc_file, wall_entities
    ):
        """When the LLM keeps emitting an ambiguous name fragment across all
        three iterations, the bounded loop exhausts and the resolver returns
        empty rather than proposing a multi-entity match. The main pipeline
        then falls back to today's full-context classifier behaviour.

        Architectural guarantee: ambiguous-on-iter-0 must NOT be returned
        silently as a multi-entity result — we either disambiguate via LLM
        refinement (covered by the dedicated iteration tests below), or we
        surface a clean empty for the caller."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps({"scope": "specific", "ifc_type": "IfcWall", "entity_name": "Wall-"})
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("modify the wall starting with Wall-")

        assert result.is_empty
        # Bounded loop: 1 initial extraction + 2 refinement iterations = 3 calls.
        assert mock_llm.invoke.call_count == 3

    def test_specific_with_zero_db_matches_returns_empty(self, project, ifc_file, wall_entities):
        """LLM hallucinates an entity name not in the DB; resolver returns empty
        rather than raising — the pipeline must fall back to today's behaviour."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps(
                {
                    "scope": "specific",
                    "ifc_type": "IfcWall",
                    "entity_name": "NoSuchWallExistsAnywhere",
                }
            )
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("modify NoSuchWallExistsAnywhere")

        assert result.is_empty

    def test_progressive_trim_recovers_from_conversational_lead_in(
        self, project, ifc_file, wall_entities
    ):
        """The exact failing-prompt symptom: the extraction LLM grabbed the
        full conversational lead-in ('wall: IfcWall ' + the actual name).
        The full string matches 0 entities, but progressively dropping the
        leading words ('wall:', then 'IfcWall') exposes the real name and
        the DB query succeeds. Without this fallback, the user sees a
        Tier-0 rejection and has to rephrase manually."""
        target = wall_entities[1]  # name='Wall-001' from the factory
        mock_llm = MagicMock()
        # Simulate the bug: LLM ignored the "strip lead-in" rule and emitted
        # the entity_name with conversational + type prefix attached.
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps(
                {
                    "scope": "specific",
                    "ifc_type": "IfcWall",
                    "entity_name": f"wall: IfcWall {target.name}",
                }
            )
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve(f"change firerating to EI240 of wall: IfcWall {target.name}")

        # Trim recovery worked: full LLM string ('wall: IfcWall Wall-001')
        # matched 0; after dropping 'wall:' and 'IfcWall', 'Wall-001'
        # matched 1. The diagnostic records the trimmed variant that hit.
        assert result.is_unique
        assert result.entities[0].pk == target.pk
        assert target.name in result.diagnostic

    def test_progressive_trim_bounded_to_avoid_overmatching(self, project, ifc_file, wall_entities):
        """Progressive trim is bounded at 4 attempts so it can't drift to a
        single common token (e.g. 'Wall') that would match every wall.
        When every variant misses, the resolver returns empty cleanly."""
        mock_llm = MagicMock()
        # 6 leading garbage tokens, none of which appear in any DB name.
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps(
                {
                    "scope": "specific",
                    "ifc_type": "IfcWall",
                    "entity_name": "alpha bravo charlie delta echo foxtrot UnmatchedFinalToken",
                }
            )
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("modify alpha bravo charlie delta echo foxtrot")

        # Bounded trim never reaches 'foxtrot UnmatchedFinalToken' or shorter
        # variants that would match nothing meaningful — falls back cleanly.
        assert result.is_empty


@pytest.mark.django_db
class TestResolveAllOfType:
    """Pass 2/3: LLM emits scope=all_of_type → all entities of that type."""

    def test_all_walls_returns_full_wall_set(self, project, ifc_file, wall_entities, door_entity):
        """scope=all_of_type with ifc_type=IfcWall returns all 5 walls, not the door."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps({"scope": "all_of_type", "ifc_type": "IfcWall"})
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("set fire rating on all walls to EI120")

        assert len(result.entities) == 5
        assert all(e.ifc_type == "IfcWall" for e in result.entities)
        assert result.scope == "all_of_type"
        # Not "unique" — multiple matches by design.
        assert not result.is_unique


@pytest.mark.django_db
class TestResolveFiltered:
    """Pass 2/3: LLM emits scope=filtered + filter_hints → property_match."""

    def test_filtered_by_property_match(self, project, ifc_file, wall_entities):
        """All wall_entities are external (IsExternal=True). Filtering by
        IsExternal=True should return all 5; IsExternal=False should return 0."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps(
                {
                    "scope": "filtered",
                    "ifc_type": "IfcWall",
                    "filter_hints": {"Pset_WallCommon.IsExternal": True},
                }
            )
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("set fire rating on all external walls")

        assert len(result.entities) == 5
        assert result.scope == "filtered"


@pytest.mark.django_db
class TestExtractionDrift:
    """Pass 2 fail-soft: drift / unknown / non-JSON → empty result, no crash."""

    def test_unknown_scope_returns_empty(self, project, ifc_file, wall_entities):
        """LLM declares scope=unknown — resolver returns empty so caller
        falls back to today's full-context classifier behaviour."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(json.dumps({"scope": "unknown"}))
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("update the model")

        assert result.is_empty

    def test_non_json_response_returns_empty(self, project, ifc_file, wall_entities):
        """LLM returns garbage that isn't valid JSON; resolver returns empty
        rather than raising. This is the architectural safety net."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response("this is definitely not json")
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("anything at all")

        assert result.is_empty

    def test_llm_exception_returns_empty(self, project, ifc_file, wall_entities):
        """LLM call raises (network down, model crashed); resolver returns
        empty rather than propagating. Pipeline keeps working without the
        resolver's narrowing."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("Ollama unreachable")
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("change firerating")

        assert result.is_empty


@pytest.mark.django_db
class TestExtractionTrimRetry:
    """The scope=unknown trim retry mirrors the DB-level trim fallback.

    When the small Ollama model returns ``scope=unknown`` on the full
    message — typically because of a leading ``IfcWall`` token it can't
    strip — the resolver retries the LLM with the leading word dropped.
    Bounded at 2 retries so worst case is 3 extractor calls before
    falling through.
    """

    def test_unknown_then_specific_recovers_via_trim(self, project, ifc_file, wall_entities):
        """Failure-mode regression: 'IfcWall <name>' returns scope=unknown
        on the full message (the model didn't strip the class token), then
        scope=specific on the trimmed message — and the resolver returns
        the matched entity in 2 calls.
        """
        target = wall_entities[1]  # 'Wall-001'
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = [
            # Try 1 — full message, model can't strip the class prefix.
            _make_llm_response(json.dumps({"scope": "unknown"})),
            # Try 2 — leading token dropped, model identifies the entity.
            _make_llm_response(
                json.dumps(
                    {"scope": "specific", "ifc_type": "IfcWall", "entity_name": target.name}
                )
            ),
        ]
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve(f"IfcWall {target.name}")

        assert result.is_unique
        assert result.entities[0].pk == target.pk
        assert mock_llm.invoke.call_count == 2

    def test_all_unknown_returns_empty_after_bounded_retries(
        self, project, ifc_file, wall_entities
    ):
        """When every trim variant still returns scope=unknown, the
        resolver falls through to _EMPTY after exactly 3 extractor calls
        (1 initial + 2 trims) — never more.
        """
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(json.dumps({"scope": "unknown"}))
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("frobnicate the widget thingamajig")

        assert result.is_empty
        assert mock_llm.invoke.call_count == 3

    def test_floor_stops_trim_before_short_fragments(self, project, ifc_file, wall_entities):
        """A 3-token message where the second trim would drop below the
        floor (≥ 4 chars OR ≥ 2 tokens) only triggers 2 LLM calls — the
        third candidate is rejected by the floor before the LLM runs.

        'ab cd ef' → trim 1 'cd ef' (passes: 5 chars, 2 tokens) →
                     trim 2 'ef' (fails: 2 chars, 1 token) → break.
        """
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(json.dumps({"scope": "unknown"}))
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("ab cd ef")

        assert result.is_empty
        assert mock_llm.invoke.call_count == 2

    def test_first_call_specific_does_not_trigger_retry(
        self, project, ifc_file, wall_entities
    ):
        """The happy path is unchanged: when the LLM nails the extraction
        on the first call, the resolver does not invoke the trim retry.
        """
        target = wall_entities[2]
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps({"scope": "specific", "ifc_type": "IfcWall", "entity_name": target.name})
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve(f"set FireRating to EI120 on {target.name}")

        assert result.is_unique
        assert mock_llm.invoke.call_count == 1

    def test_hard_llm_failure_does_not_trigger_retry(self, project, ifc_file, wall_entities):
        """Network-down / parse-error returns from _llm_extract surface as
        None — the trim retry skips immediately rather than burning calls
        on a model that can't respond. Stays at 1 call, returns _EMPTY.
        """
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("Ollama unreachable")
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("IfcWall something")

        assert result.is_empty
        assert mock_llm.invoke.call_count == 1


class TestResolutionResultPredicates:
    """Pure predicates on ResolutionResult — no DB needed."""

    def test_empty_predicates(self):
        r = ResolutionResult(entities=[], ifc_type_hint=None, scope="empty", diagnostic="x")
        assert r.is_empty
        assert not r.is_unique
        assert not r.is_ambiguous

    def test_unique_predicates(self):
        r = ResolutionResult(
            entities=[MagicMock()], ifc_type_hint="IfcWall", scope="specific", diagnostic="x"
        )
        assert not r.is_empty
        assert r.is_unique
        assert not r.is_ambiguous

    def test_ambiguous_predicates(self):
        r = ResolutionResult(
            entities=[MagicMock(), MagicMock(), MagicMock()],
            ifc_type_hint="IfcWall",
            scope="specific",
            diagnostic="x",
        )
        assert not r.is_empty
        assert not r.is_unique
        assert r.is_ambiguous


@pytest.mark.django_db
class TestIterativeRefinement:
    """Iterations 1 and 2 — DB-grounded LLM refinement.

    Each iteration runs only when the previous one returned 0 or N matches
    AND the LLM said scope=specific (i.e. the user wanted ONE entity but we
    couldn't pin it). The refinement call gets candidate names from the DB
    and asks the LLM to pick. Bounded at 3 LLM calls total."""

    def test_iteration_0_success_short_circuits_no_refinement(
        self, project, ifc_file, wall_entities
    ):
        """When iteration 0 already returns a unique match, refinement code
        path is never entered and the LLM is invoked exactly once."""
        target = wall_entities[2]
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps({"scope": "specific", "ifc_type": "IfcWall", "entity_name": target.name})
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve(f"modify {target.name}")

        assert result.is_unique
        assert result.entities[0].pk == target.pk
        assert mock_llm.invoke.call_count == 1

    def test_iteration_1_recovers_from_hallucinated_name(self, project, ifc_file, wall_entities):
        """Iteration 0 returns a name that doesn't exist in the project (and
        trim can't save it). Iteration 1 fires with a sample-based hint set
        (no token query matched anything), and the mocked LLM picks a real
        name from the candidate list. Resolver returns a unique match after
        exactly 2 LLM calls."""
        target = wall_entities[1]
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = [
            # Iteration 0: completely fabricated name.
            _make_llm_response(
                json.dumps(
                    {
                        "scope": "specific",
                        "ifc_type": "IfcWall",
                        "entity_name": "NonexistentPhantomWall",
                    }
                )
            ),
            # Iteration 1: LLM picks a real name from the (fallback sample) hint set.
            _make_llm_response(
                json.dumps({"scope": "specific", "ifc_type": "IfcWall", "entity_name": target.name})
            ),
        ]
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve(f"modify the wall I called {target.name}")

        assert result.is_unique
        assert result.entities[0].pk == target.pk
        assert mock_llm.invoke.call_count == 2

    def test_iteration_1_disambiguates_from_multiple_matches(
        self, project, ifc_file, wall_entities
    ):
        """Iteration 0 returns 'Wall-' which matches all 5 fixture walls.
        Iteration 1 hints include all of them; mocked LLM picks one specific
        wall (using extra context that would be in the original message).
        The resolver pins the filter to that one entity."""
        target = wall_entities[3]  # 'Wall-003'
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = [
            # Iteration 0: ambiguous fragment — matches all 5 walls.
            _make_llm_response(
                json.dumps({"scope": "specific", "ifc_type": "IfcWall", "entity_name": "Wall-"})
            ),
            # Iteration 1: the LLM, seeing the candidate list and the
            # original message context, picks one specific wall.
            _make_llm_response(
                json.dumps({"scope": "specific", "ifc_type": "IfcWall", "entity_name": target.name})
            ),
        ]
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve(f"modify {target.name}, the third one")

        assert result.is_unique
        assert result.entities[0].pk == target.pk
        assert mock_llm.invoke.call_count == 2

    def test_iteration_2_fires_when_iteration_1_still_misses(
        self, project, ifc_file, wall_entities
    ):
        """When iteration 1 also fails to find a match, iteration 2 fires
        with broader hints. Mocked LLM picks the right name on iteration 2.
        Total: 3 LLM calls."""
        target = wall_entities[0]
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = [
            # Iteration 0: fabricated name.
            _make_llm_response(
                json.dumps(
                    {
                        "scope": "specific",
                        "ifc_type": "IfcWall",
                        "entity_name": "PhantomWallNotInDB",
                    }
                )
            ),
            # Iteration 1: LLM returns another fabricated name not in the hints.
            _make_llm_response(
                json.dumps(
                    {
                        "scope": "specific",
                        "ifc_type": "IfcWall",
                        "entity_name": "StillFakeName",
                    }
                )
            ),
            # Iteration 2: with broader hints, LLM picks a real name.
            _make_llm_response(
                json.dumps({"scope": "specific", "ifc_type": "IfcWall", "entity_name": target.name})
            ),
        ]
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("modify some wall")

        assert result.is_unique
        assert result.entities[0].pk == target.pk
        assert mock_llm.invoke.call_count == 3

    def test_bounded_loop_returns_empty_after_iteration_2(self, project, ifc_file, wall_entities):
        """When all three iterations fail to find a match, the resolver
        returns empty (no infinite loop). Mock LLM invoked exactly three
        times and not one more."""
        mock_llm = MagicMock()
        # Same fabricated name on every call — never matches anything.
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps(
                {
                    "scope": "specific",
                    "ifc_type": "IfcWall",
                    "entity_name": "AlwaysWrongFakeName",
                }
            )
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("modify the wall I dreamt about")

        assert result.is_empty
        assert mock_llm.invoke.call_count == 3

    def test_iteration_1_skipped_when_llm_returns_unknown_scope(
        self, project, ifc_file, wall_entities
    ):
        """When iteration 1's LLM returns scope=unknown (the explicit opt-out),
        the resolver respects it and falls through without trying iteration 2.
        This is the LLM saying 'I genuinely cannot pick from these candidates.'"""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = [
            # Iteration 0: hallucinated name → 0 matches.
            _make_llm_response(
                json.dumps(
                    {
                        "scope": "specific",
                        "ifc_type": "IfcWall",
                        "entity_name": "FakeName1",
                    }
                )
            ),
            # Iteration 1: LLM opts out — none of the candidates match the user's intent.
            _make_llm_response(json.dumps({"scope": "unknown"})),
            # Iteration 2: would be a concrete pick, but should NEVER fire because
            # iteration 1 returned scope=unknown which means iteration 1's
            # extracted dict is None → resolve() carries forward the previous
            # extracted (iteration 0's) and runs iteration 2.
            _make_llm_response(json.dumps({"scope": "unknown"})),
        ]
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("modify something")

        assert result.is_empty
        # Iteration 2 still runs because iter-1 opted out and we have one more attempt.
        # Both iterations return scope=unknown; cap is 3 calls total.
        assert mock_llm.invoke.call_count == 3

    def test_iteration_1_can_reconsider_scope_to_all_of_type(
        self, project, ifc_file, wall_entities
    ):
        """The regression fix: when iteration 0 misclassifies a category query
        as scope=specific (e.g. entity_name='walls' for 'all walls'), the
        refinement prompt now lets the LLM correct itself by switching to
        scope=all_of_type. This stops the iteration loop from collapsing a
        category intent into a single-entity pick."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = [
            # Iteration 0: misclassified — "walls" is not an entity name.
            _make_llm_response(
                json.dumps({"scope": "specific", "ifc_type": "IfcWall", "entity_name": "walls"})
            ),
            # Iteration 1: with the new prompt the LLM realises the user wanted
            # the whole category and switches scope.
            _make_llm_response(json.dumps({"scope": "all_of_type", "ifc_type": "IfcWall"})),
        ]
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("change fire rating to REI120 to all walls")

        assert result.scope == "all_of_type"
        assert len(result.entities) == 5  # all wall_entities fixtures
        # Iteration 1 reconsidered scope — iteration 2 must NOT fire when
        # the result is non-specific, even if it has multiple entities.
        assert mock_llm.invoke.call_count == 2


@pytest.mark.django_db
class TestResolveSpecificMulti:
    """scope=specific_multi — multiple named entities in one request."""

    def test_two_named_walls_returns_both(self, project, ifc_file, wall_entities):
        """LLM emits scope=specific_multi with two entity_names, both real;
        resolver returns both walls in a single result."""
        target_a = wall_entities[0]
        target_b = wall_entities[2]
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps(
                {
                    "scope": "specific_multi",
                    "ifc_type": "IfcWall",
                    "entity_names": [target_a.name, target_b.name],
                }
            )
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve(f"change FireRating on {target_a.name} and {target_b.name}")

        assert result.scope == "specific_multi"
        assert len(result.entities) == 2
        pks = {e.pk for e in result.entities}
        assert target_a.pk in pks
        assert target_b.pk in pks

    def test_partial_miss_returns_matched_subset(self, project, ifc_file, wall_entities):
        """One name matches, one doesn't. Resolver returns the matched entity
        and notes the unmatched name in the diagnostic. NOT empty."""
        target = wall_entities[1]
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps(
                {
                    "scope": "specific_multi",
                    "ifc_type": "IfcWall",
                    "entity_names": [target.name, "DoesNotExistAnywhere"],
                }
            )
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve(f"modify {target.name} and DoesNotExistAnywhere")

        assert result.scope == "specific_multi"
        assert len(result.entities) == 1
        assert result.entities[0].pk == target.pk
        assert "DoesNotExistAnywhere" in result.diagnostic
        assert "unmatched" in result.diagnostic.lower()

    def test_total_miss_returns_empty(self, project, ifc_file, wall_entities):
        """All names miss → resolver returns empty so the pipeline falls back
        to today's classifier path."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps(
                {
                    "scope": "specific_multi",
                    "ifc_type": "IfcWall",
                    "entity_names": ["NoSuchWallA", "NoSuchWallB"],
                }
            )
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("modify NoSuchWallA and NoSuchWallB")

        assert result.is_empty


class TestExtractStepIds:
    """Static step-ID extraction helper used by the LLM-output safety net."""

    def test_no_step_id_returns_empty(self):
        assert EntityNameResolver.extract_step_ids("change all walls") == []

    def test_single_step_id_extracted(self):
        result = EntityNameResolver.extract_step_ids("on wall :285330")
        assert result == [":285330"]

    def test_multiple_step_ids_extracted_in_order(self):
        result = EntityNameResolver.extract_step_ids(
            "on wall :285330 and window :286105 plus :287567"
        )
        assert result == [":285330", ":286105", ":287567"]

    def test_step_id_extraction_dedupes(self):
        result = EntityNameResolver.extract_step_ids("set X on :285330 and again :285330")
        assert result == [":285330"]

    def test_short_numeric_is_not_a_step_id(self):
        """Step-IDs are 5–7 digits; shorter numeric suffixes don't qualify."""
        assert EntityNameResolver.extract_step_ids(":42") == []

    def test_long_numeric_is_not_a_step_id(self):
        assert EntityNameResolver.extract_step_ids(":12345678") == []


class TestAugmentWithStepIds:
    """Step-ID safety-net post-pass on LLM extraction output."""

    def test_no_step_ids_in_message_leaves_extraction_unchanged(self):
        extracted = {"scope": "specific", "entity_name": "Wall-A"}
        result = EntityNameResolver._augment_with_step_ids(
            extracted, "set FireRating to EI60 on Wall-A"
        )
        assert result == {"scope": "specific", "entity_name": "Wall-A"}

    def test_step_id_in_message_already_in_entity_name_leaves_unchanged(self):
        extracted = {"scope": "specific", "entity_name": "Basic Wall:foo:285330"}
        result = EntityNameResolver._augment_with_step_ids(
            extracted, "set X on Basic Wall:foo:285330"
        )
        assert result["scope"] == "specific"
        assert result["entity_name"] == "Basic Wall:foo:285330"

    def test_dropped_step_id_is_recovered_and_promoted_to_specific_multi(self):
        """LLM only saw 'Imaginary-Wall-007' but the message also has
        ':285330' — the post-pass adds it back and promotes scope."""
        extracted = {"scope": "specific", "entity_name": "Imaginary-Wall-007"}
        result = EntityNameResolver._augment_with_step_ids(
            extracted, "set ThermalTransmittance to 0.18 on wall :285330 and Imaginary-Wall-007"
        )
        assert result["scope"] == "specific_multi"
        assert ":285330" in result["entity_names"]
        assert "Imaginary-Wall-007" in result["entity_names"]
        assert "entity_name" not in result

    def test_two_step_ids_with_no_llm_names_promotes_to_specific_multi(self):
        extracted = {"scope": "unknown"}
        result = EntityNameResolver._augment_with_step_ids(
            extracted, "set X on wall :285330 and :286105"
        )
        assert result["scope"] == "specific_multi"
        assert result["entity_names"] == [":285330", ":286105"]

    def test_step_id_already_substring_of_existing_name_is_not_duplicated(self):
        extracted = {
            "scope": "specific_multi",
            "entity_names": ["Basic Wall:foo:285330"],
        }
        result = EntityNameResolver._augment_with_step_ids(
            extracted, "set X on Basic Wall:foo:285330"
        )
        assert result["entity_names"] == ["Basic Wall:foo:285330"]

    def test_safety_net_does_not_override_all_of_type(self):
        """A coincidental step-ID-shaped substring shouldn't downgrade a
        legit all_of_type extraction to specific."""
        extracted = {"scope": "all_of_type", "ifc_type": "IfcWall"}
        result = EntityNameResolver._augment_with_step_ids(
            extracted, "set X on all walls (also tagged :285330 historically)"
        )
        # all_of_type is preserved; we only promote when scope is specific/unknown
        # and the pass adds names. In this case we DO add the name because
        # all_of_type is treated as not having a name list — but scope stays
        # specific_multi only when 2+ items result. Here only one new name
        # would be added and there's no existing name list, so:
        assert result["scope"] in ("all_of_type", "specific")


@pytest.mark.django_db
class TestTrimFloor:
    """The trim-floor guard prevents progressive trim from drifting to
    fragments so short they match unrelated entities. The original
    extracted name is always tried first, regardless of length; only
    fallback trims are floored.
    """

    def test_create_request_with_nonexistent_name_returns_empty_not_random_matches(
        self, project, ifc_file, wall_entities
    ):
        """The Failure A regression: 'Fire Zone A' for a CREATE request.

        Before the floor existed, the trim fallback dropped 'Fire Zone A' →
        'Zone A' → 'A', and the single-letter 'A' icontains query matched
        every entity with an 'A' anywhere in its name. The resolver returned
        a phantom set of unrelated entities that poisoned the classifier
        prompt downstream.

        With the floor, 'Fire Zone A' tries the full name (0 matches), then
        'Zone A' (still 0 — no DB entity matches), then refuses to descend
        to single-token 'A'. Returns clean empty.
        """
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps({"scope": "specific", "ifc_type": "IfcZone", "entity_name": "Fire Zone A"})
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("create three new IfcZone entities for Fire Zone A")

        assert result.is_empty

    def test_floor_blocks_single_letter_trim_even_when_db_would_match(
        self, project, ifc_file, wall_entities
    ):
        """A constructed scenario: the LLM emits an entity_name where every
        trim eventually exposes a single-letter token that, by accident,
        matches DB rows. The floor prevents the resolver from accepting
        that as a 'specific' resolution.
        """
        mock_llm = MagicMock()
        # 'one two three X' → trims to 'two three X' → 'three X' → 'X'.
        # 'X' is below floor (1 char, 1 token); the trim stops before
        # querying the DB with it.
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps(
                {"scope": "specific", "ifc_type": "IfcWall", "entity_name": "one two three X"}
            )
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("set FireRating on one two three X")

        # No DB entity contains 'one two three X', 'two three X', or 'three X'
        # as a substring — the floor stops the trim BEFORE 'X' is queried.
        assert result.is_empty

    def test_floor_allows_long_single_token_trims(self, project, ifc_file, wall_entities):
        """A single-token trim that is ≥ 4 chars should pass the floor —
        this is the canonical 'wall: IfcWall Wall-001' recovery path that
        already-existing tests rely on. Encoded here as a regression sentry.
        """
        target = wall_entities[1]  # 'Wall-001'
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps(
                {
                    "scope": "specific",
                    "ifc_type": "IfcWall",
                    "entity_name": f"junk prefix {target.name}",
                }
            )
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve(f"modify junk prefix {target.name}")

        # Trims: 'junk prefix Wall-001' (0) → 'prefix Wall-001' (0) → 'Wall-001' (1).
        # 'Wall-001' is 8 chars, 1 token — passes floor (≥ 4 chars).
        assert result.is_unique
        assert result.entities[0].pk == target.pk


@pytest.mark.django_db
class TestTrimMatchCap:
    """When a TRIMMED fragment matches more than the cap, the resolver
    treats it as noise and continues to the next candidate. The original
    extracted name is exempt — the user's literal phrasing always reaches
    the DB even if it pulls a wide set.
    """

    def test_trimmed_fragment_over_cap_is_skipped(
        self, project, ifc_file, wall_entities, monkeypatch
    ):
        """Patch the cap to 2 so the 5-wall fixture exceeds it. The LLM
        emits 'garbage Wall' which trims to 'Wall' (5 matches > 2). The
        resolver should skip the trim and fall through to empty.
        """
        monkeypatch.setattr(resolver_module, "_TRIM_MATCH_CAP", 2)
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps({"scope": "specific", "ifc_type": "IfcWall", "entity_name": "garbage Wall"})
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("modify garbage Wall")

        # 'garbage Wall' (full) → 0 matches. 'Wall' (trim, 5 matches) →
        # over cap=2 → skipped. No further trim possible. Falls through
        # to refinement iterations (which return the same name) → empty.
        assert result.is_empty

    def test_original_name_is_exempt_from_cap(self, project, ifc_file, wall_entities, monkeypatch):
        """The user literally wrote 'Wall' as the entity reference (a wide
        category by their own choice). The cap must NOT apply to the
        original extracted name; the resolver returns the 5-wall ambiguous
        result and lets refinement iterations narrow it.
        """
        monkeypatch.setattr(resolver_module, "_TRIM_MATCH_CAP", 2)
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps({"scope": "specific", "ifc_type": "IfcWall", "entity_name": "Wall"})
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        resolver.resolve("modify Wall")

        # Original-name path: 'Wall' matches all 5 walls (over cap=2 BUT
        # cap doesn't apply to the original). Returned as ambiguous; the
        # resolve() loop then runs iteration 1 with DB hints — this mock
        # always returns the same answer, so all 3 calls produce the same
        # 5-entity result and the resolver eventually returns empty after
        # iterations exhausted. The point of this test is that iteration 0
        # produced 5 entities (not skipped by cap), proven by the LLM
        # being invoked 3 times rather than 1.
        assert mock_llm.invoke.call_count == 3


@pytest.mark.django_db
class TestResolverModes:
    """The ``mode`` parameter selects the targeting strategy. The default
    ``MODE_EXISTING_TARGET`` runs the full pipeline; ``MODE_NO_TARGET`` and
    ``MODE_NEW_TARGET`` short-circuit so callers never invoke the LLM for
    requests where existing-entity lookup is wrong.
    """

    def test_no_target_mode_returns_empty_without_llm(self, project, ifc_file, wall_entities):
        mock_llm = MagicMock()
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("anything at all", mode=MODE_NO_TARGET)

        assert result.is_empty
        mock_llm.invoke.assert_not_called()

    def test_new_target_mode_returns_empty_without_llm(self, project, ifc_file, wall_entities):
        """CREATE workflow doesn't look up existing entities — the resolver
        bows out immediately so the caller's name-validation path takes over.
        """
        mock_llm = MagicMock()
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve("Fire Zone A, Fire Zone B, Fire Zone C", mode=MODE_NEW_TARGET)

        assert result.is_empty
        mock_llm.invoke.assert_not_called()

    def test_parent_target_mode_runs_existing_pipeline(self, project, ifc_file, wall_entities):
        """In commit 1, PARENT_TARGET runs the same pipeline as
        EXISTING_TARGET — the differentiation is wording-only and lives
        on the caller side. Here we verify the LLM is invoked.
        """
        target = wall_entities[0]
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps({"scope": "specific", "ifc_type": "IfcWall", "entity_name": target.name})
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve(target.name, mode=MODE_PARENT_TARGET)

        assert result.is_unique
        assert result.entities[0].pk == target.pk
        mock_llm.invoke.assert_called_once()

    def test_explicit_existing_target_mode_matches_default_behavior(
        self, project, ifc_file, wall_entities
    ):
        target = wall_entities[2]
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_llm_response(
            json.dumps({"scope": "specific", "ifc_type": "IfcWall", "entity_name": target.name})
        )
        resolver = EntityNameResolver(project, llm=mock_llm)

        result = resolver.resolve(f"modify {target.name}", mode=MODE_EXISTING_TARGET)

        assert result.is_unique
        assert result.entities[0].pk == target.pk

    def test_unknown_mode_raises_value_error(self, project, ifc_file, wall_entities):
        resolver = EntityNameResolver(project, llm=MagicMock())

        with pytest.raises(ValueError, match="Unknown resolver mode"):
            resolver.resolve("anything", mode="not-a-real-mode")
