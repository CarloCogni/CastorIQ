# writeback/tests/test_explainer.py
"""The blind explanation never sees the request and never raises (spec V-4)."""

from unittest.mock import MagicMock, patch

from writeback.services.explainer import NUM_CTX, explain, render_rows
from writeback.services.verifier import DiffRow


def _rows():
    return [DiffRow("k1", "property", "Pset_WallCommon", "FireRating", None, "EI60", 5, ["W1"] * 5)]


def _explain(content: str):
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content=content)
    with (
        patch("writeback.services.explainer.get_llm", return_value=llm) as get_llm,
        patch("writeback.services.explainer.resolve_model_name", return_value="qwen2.5-coder:7b"),
    ):
        result = explain("def select(model): ...", _rows(), "5 × IfcWall", user=None)
    return result, llm, get_llm


def test_explain_shows_code_and_diff_but_not_the_request():
    """The prompt carries the code and the rows; the request text is nowhere in it."""
    result, llm, get_llm = _explain("Adds FireRating EI60 to five walls.\n")

    human = llm.invoke.call_args.args[0][1].content
    assert "def select(model)" in human
    assert "Pset_WallCommon.FireRating: None → 'EI60' on 5 entities" in human
    assert "5 × IfcWall" in human
    assert result.text == "Adds FireRating EI60 to five walls."
    assert result.model == "qwen2.5-coder:7b"
    assert get_llm.call_args.kwargs["num_ctx"] == NUM_CTX  # one loaded runner per request


def test_explain_keeps_one_sentence():
    """A chatty model is cut at the first sentence boundary; a value's dot is not one."""
    result, _, _ = _explain("Sets FireRating to 'EI60' on 5 walls. This ensures compliance.")
    assert result.text == "Sets FireRating to 'EI60' on 5 walls."

    result, _, _ = _explain("Changed ThermalTransmittance from 0.2359 to 0.18 on 1 wall.")
    assert result.text == "Changed ThermalTransmittance from 0.2359 to 0.18 on 1 wall."


def test_explain_survives_a_raising_model():
    """A raised explainer still yields a card: the text is empty, so the commit uses the request."""
    with (
        patch("writeback.services.explainer.get_llm", side_effect=RuntimeError("ollama down")),
        patch("writeback.services.explainer.resolve_model_name", return_value="m"),
    ):
        result = explain("code", _rows(), "5 × IfcWall")

    assert result.text == ""
    assert result.model == "m"


def test_render_rows_describes_population_and_presence_changes():
    """Added and removed entities, and a whole pset added, render as such."""
    rows = [
        DiffRow("a", "added", "", "", None, "NEW1", 1, ["NEW1"]),
        DiffRow("r", "removed", "", "", "OLD1", None, 1, ["OLD1"]),
        DiffRow("p", "property", "Pset_X", "", None, "Pset_X", 2, ["W1", "W2"]),
    ]
    assert render_rows(rows) == (
        "- entity added: NEW1\n- entity removed: OLD1\n- property set Pset_X added on 2 entities"
    )
