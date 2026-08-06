# writeback/tests/test_llm_boundary.py
"""Unit tests for the schema-validated LLM boundary (errors-as-data retry)."""

from types import SimpleNamespace

import pytest

from writeback.services.llm_boundary import (
    BoundaryError,
    BoundaryValidationError,
    call_structured,
)
from writeback.services.schemas import TriageOutput
from writeback.services.triage_classifier import _normalise_payload


class FakeLLM:
    """Returns queued canned responses and records the prompts it saw."""

    def __init__(self, *contents: str) -> None:
        self._contents = list(contents)
        self.prompts: list[str] = []

    def invoke(self, messages):
        self.prompts.append(messages[-1].content)
        return SimpleNamespace(content=self._contents.pop(0))


VALID = '{"segments": [{"kind": "PROPERTY", "target_phrase": "all walls", "value_phrase": "FireRating to EI120"}]}'


def _call(llm, **overrides):
    kwargs = dict(
        stage="triage",
        system_prompt="system",
        user_prompt="user request",
        schema=TriageOutput,
        normalizers=(_normalise_payload,),
    )
    kwargs.update(overrides)
    return call_structured(llm, **kwargs)


def test_valid_json_passes_first_try():
    llm = FakeLLM(VALID)
    output = _call(llm)
    assert output.segments[0].kind == "PROPERTY"
    assert len(llm.prompts) == 1


def test_code_fenced_json_is_tolerated():
    llm = FakeLLM(f"```json\n{VALID}\n```")
    output = _call(llm)
    assert output.segments[0].target_phrase == "all walls"


def test_invalid_first_attempt_self_corrects_on_retry():
    llm = FakeLLM("this is not json at all", VALID)
    output = _call(llm)
    assert output.segments[0].kind == "PROPERTY"
    # Retry prompt carries the structured error back to the model.
    assert len(llm.prompts) == 2
    assert "NOT_JSON" in llm.prompts[1]
    assert "failed validation" in llm.prompts[1]


def test_exhausted_retries_raise_with_structured_errors():
    llm = FakeLLM("garbage", "more garbage")
    with pytest.raises(BoundaryValidationError) as exc_info:
        _call(llm)

    err = exc_info.value
    assert err.stage == "triage"
    assert err.errors[0].code == "NOT_JSON"
    assert err.errors_as_dicts()[0]["path"] == "$"


def test_normalizer_drift_shapes_are_accepted():
    # Top-level list instead of {"segments": [...]} — a known drift shape.
    llm = FakeLLM('[{"kind": "DELETE", "target_phrase": "all chairs", "value_phrase": ""}]')
    output = _call(llm)
    assert output.segments[0].kind == "DELETE"


def test_normalized_but_empty_payload_fails_shape_validation():
    # Segments with invalid kinds are dropped by the normalizer → empty list
    # → schema min_length failure → BAD_SHAPE (after retry also fails).
    llm = FakeLLM(
        '{"segments": [{"kind": "NONSENSE", "target_phrase": "", "value_phrase": ""}]}',
        '{"segments": []}',
    )
    with pytest.raises(BoundaryValidationError) as exc_info:
        _call(llm)
    assert exc_info.value.errors[0].code == "BAD_SHAPE"
    assert exc_info.value.errors[0].path == "segments"


def test_prior_errors_are_injected_into_first_prompt():
    llm = FakeLLM(VALID)
    _call(
        llm,
        prior_errors=(BoundaryError(code="BAD_ENUM", path="segments[0].kind", hint="bad kind"),),
    )
    assert "BAD_ENUM" in llm.prompts[0]
    assert "user request" in llm.prompts[0]
