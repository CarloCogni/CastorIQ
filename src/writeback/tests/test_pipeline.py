# writeback/tests/test_pipeline.py
"""The pipeline loop with a canned generator and the real sandbox (spec C-4, V-2).

Each test hands the pipeline a scripted sequence of model answers and checks
what came out: a proposal outcome, a repair, a rejection quoting the last
error, or the no-change case.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.files import File

from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from writeback.services.emitters import CancellationError, CapturingEmitter
from writeback.services.errors import ModificationError, NoChangeError
from writeback.services.explainer import Explanation
from writeback.services.pipeline import EXTRACTION_ERROR, ZERO_TARGETS_ERROR, ModifyPipeline

WALL1_GUID = "2O2Fr$t4X7Zf8NOew3FLOH"
FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "ifc_processor" / "tests" / "fixtures" / "simple_wall.ifc"
)

RENAME = """```python
def select(model):
    return [model.by_guid("2O2Fr$t4X7Zf8NOew3FLOH")]

def modify(model, targets):
    for wall in targets:
        wall.Name = "Wall-Renamed"
```"""

NO_TARGETS = """```python
def select(model):
    return []

def modify(model, targets):
    pass
```"""

WIDENING = """```python
def select(model):
    return []

def modify(model, targets):
    pass
```""".replace("return []", 'return [s for s in model.by_type("IfcSite")]').replace(
    "pass", 'for w in model.by_type("IfcWall"):\n        w.Name = "Widened"'
)

NO_CHANGE = """```python
def select(model):
    return [model.by_guid("2O2Fr$t4X7Zf8NOew3FLOH")]

def modify(model, targets):
    for wall in targets:
        wall.Name = wall.Name
```"""


CREATE_ZONE = """```python
def select(model):
    return model.by_type("IfcProject")

def modify(model, targets):
    root.create_entity(ifc_class="IfcZone", name="Acoustic Zone 1")
```"""

RAW_CREATE_ZONE = CREATE_ZONE.replace(
    'root.create_entity(ifc_class="IfcZone", name="Acoustic Zone 1")',
    'model.create_entity("IfcZone", Name="Acoustic Zone 1")',
)


MISPLACED_ZONE = """```python
def select(model):
    return model.by_type("IfcProject")

def modify(model, targets):
    zone = root.create_entity(ifc_class="IfcZone", name="Acoustic Zone 1")
    for storey in model.by_type("IfcBuildingStorey"):
        aggregate.assign_object(products=[zone], relating_object=storey)
```"""


class ScriptedGenerator:
    """Returns canned answers in order; records the repair errors it was given."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.errors: list[str] = []
        self.model_name = "scripted"

    def generate(self, request, grounding) -> str:
        return self.answers.pop(0)

    def repair(self, request, grounding, code, error) -> str:
        self.errors.append(error)
        return self.answers.pop(0)


@pytest.fixture
def ifc_file():
    ifc_file = IFCFileFactory()
    with open(FIXTURE_PATH, "rb") as fh:
        ifc_file.file.save("wall.ifc", File(fh), save=True)
    IFCEntityFactory(
        ifc_file=ifc_file, global_id=WALL1_GUID, ifc_type="IfcWall", name="TestWall-001"
    )
    return ifc_file


@pytest.fixture(autouse=True)
def canned_explainer():
    with patch(
        "writeback.services.pipeline.explain",
        return_value=Explanation("Renames one wall.", "scripted"),
    ):
        yield


def _run(ifc_file, generator, request="Rename the wall to Wall-Renamed"):
    emitter = CapturingEmitter()
    pipeline = ModifyPipeline(ifc_file.project, generator=generator)
    return pipeline.run(request, ifc_file=ifc_file, emitter=emitter), emitter


@pytest.mark.slow
@pytest.mark.django_db
def test_first_attempt_success_keeps_the_scratch_and_reports_the_diff(ifc_file):
    """One generate, one run, one verify; the scratch copy exists and holds the change."""
    outcome, emitter = _run(ifc_file, ScriptedGenerator(RENAME))

    assert outcome.attempts == 1
    assert outcome.targets == [WALL1_GUID]
    assert [r.after for r in outcome.rows] == ["Wall-Renamed"]
    assert outcome.rows[0].flagged is False
    assert Path(outcome.scratch_path).exists()
    assert Path(outcome.scratch_path).name.startswith(".wall.proposal-")
    assert outcome.explanation == "Renames one wall."
    assert len(outcome.base_fingerprint) == 64
    phases = [(e["phase"], e["status"]) for e in emitter.events]
    assert phases == [
        ("ground", "running"),
        ("ground", "done"),
        ("generate", "running"),
        ("generate", "done"),
        ("run", "running"),
        ("run", "done"),
        ("verify", "running"),
        ("verify", "done"),
    ]
    assert emitter.events[-1]["detail"] == {"targets": 1, "flags": 0}
    Path(outcome.scratch_path).unlink()


@pytest.mark.slow
@pytest.mark.django_db
def test_no_code_block_is_repaired_with_the_extraction_error(ifc_file):
    """A prose answer costs one repair whose error is the extraction message."""
    generator = ScriptedGenerator("I cannot help with that.", RENAME)

    outcome, _ = _run(ifc_file, generator)

    assert outcome.attempts == 2
    assert generator.errors == [EXTRACTION_ERROR]
    Path(outcome.scratch_path).unlink()


@pytest.mark.slow
@pytest.mark.django_db
def test_zero_targets_is_repaired_with_the_zero_target_error_and_no_scratch_survives(ifc_file):
    """select() returning nothing is one error string; the failed run's scratch is deleted."""
    generator = ScriptedGenerator(NO_TARGETS, RENAME)

    outcome, _ = _run(ifc_file, generator)

    assert generator.errors == [ZERO_TARGETS_ERROR]
    scratches = list(Path(ifc_file.file.path).parent.glob(".wall.proposal-*.ifc"))
    assert scratches == [Path(outcome.scratch_path)]
    Path(outcome.scratch_path).unlink()


@pytest.mark.slow
@pytest.mark.django_db
def test_scope_violation_is_repaired(ifc_file):
    """modify() touching a wall select() did not return is a scope error fed back."""
    generator = ScriptedGenerator(WIDENING, RENAME)

    outcome, _ = _run(ifc_file, generator)

    assert outcome.attempts == 2
    assert "outside the selection" in generator.errors[0]
    assert WALL1_GUID in generator.errors[0]
    Path(outcome.scratch_path).unlink()


@pytest.mark.slow
@pytest.mark.django_db
def test_three_failures_end_in_a_rejection_quoting_the_last_error(ifc_file):
    """Three zero-target runs: no loop, a ModificationError with the zero-target text."""
    generator = ScriptedGenerator(NO_TARGETS, NO_TARGETS, NO_TARGETS)

    with pytest.raises(ModificationError, match="after 3 attempts") as excinfo:
        _run(ifc_file, generator)

    assert ZERO_TARGETS_ERROR in str(excinfo.value)
    assert len(generator.errors) == 2
    assert list(Path(ifc_file.file.path).parent.glob(".wall.proposal-*.ifc")) == []


@pytest.mark.slow
@pytest.mark.django_db
def test_no_change_raises_no_change_error_without_a_repair(ifc_file):
    """Targets selected, nothing changed: an 'already so' outcome, zero repairs."""
    generator = ScriptedGenerator(NO_CHANGE)

    with pytest.raises(NoChangeError, match="Already so"):
        _run(ifc_file, generator)

    assert generator.errors == []
    assert list(Path(ifc_file.file.path).parent.glob(".wall.proposal-*.ifc")) == []


@pytest.mark.django_db
def test_no_processed_entities_is_a_modification_error():
    """A file with no index rows cannot be grounded."""
    ifc_file = IFCFileFactory()
    with pytest.raises(ModificationError, match="No processed IFC entities"):
        ModifyPipeline(ifc_file.project, generator=ScriptedGenerator()).run("x", ifc_file=ifc_file)


@pytest.mark.django_db
def test_reject_line_ends_the_request_after_one_call(ifc_file):
    """A declared refusal is not a failure: no repair, no scratch, the reason in the message."""
    generator = ScriptedGenerator("REJECT: moving a wall changes geometry, which is out of scope.")

    with patch("writeback.services.pipeline.ModifyPipeline._record_failure", return_value=None):
        with pytest.raises(ModificationError, match="Declined: moving a wall changes geometry"):
            _run(ifc_file, generator, request="move wall :285330 1 meter east")

    assert generator.errors == []
    assert generator.answers == []
    assert list(Path(ifc_file.file.path).parent.glob(".wall.proposal-*.ifc")) == []


class CancellingEmitter(CapturingEmitter):
    """Raises CancellationError at one (phase, status), as the WebSocket emitter does."""

    def __init__(self, phase: str, status: str) -> None:
        super().__init__()
        self._at = (phase, status)

    def emit(self, phase, status, message, detail=None):
        super().emit(phase, status, message, detail)
        if (str(phase), status) == self._at:
            raise CancellationError("client gone")


@pytest.mark.slow
@pytest.mark.django_db
def test_cancellation_after_the_run_deletes_the_scratch(ifc_file):
    """A cancel landing between the run and the outcome leaves no orphan copy behind."""
    emitter = CancellingEmitter("verify", "done")
    pipeline = ModifyPipeline(ifc_file.project, generator=ScriptedGenerator(RENAME))

    with pytest.raises(CancellationError):
        pipeline.run("Rename the wall to Wall-Renamed", ifc_file=ifc_file, emitter=emitter)

    assert list(Path(ifc_file.file.path).parent.glob(".wall.proposal-*.ifc")) == []


@pytest.mark.django_db
def test_a_model_failure_is_a_visible_failure_with_a_record(ifc_file):
    """A timeout raised by the generator ends the request at once, with a failure record."""

    class FailingGenerator(ScriptedGenerator):
        def generate(self, request, grounding) -> str:
            raise ModificationError("The Modify model did not answer within 240 s.")

    with patch(
        "writeback.services.pipeline.ModifyPipeline._record_failure", return_value="rec-1"
    ) as record:
        with pytest.raises(ModificationError, match="did not answer") as excinfo:
            _run(ifc_file, FailingGenerator())

    assert excinfo.value.failure_record_id == "rec-1"
    assert record.called
    assert list(Path(ifc_file.file.path).parent.glob(".wall.proposal-*.ifc")) == []


@pytest.mark.slow
@pytest.mark.django_db
def test_creation_in_sheet_notation_is_one_flagged_added_row(ifc_file):
    """The corpus 10.5 shape: project selected, root.create_entity as the sheet writes it."""
    outcome, emitter = _run(
        ifc_file, ScriptedGenerator(CREATE_ZONE), 'Create a new IfcZone called "Acoustic Zone 1"'
    )

    assert outcome.attempts == 1
    assert [(r.kind, r.prop, r.flagged, r.flag_reason) for r in outcome.rows] == [
        ("added", "IfcZone", True, "added")
    ]
    assert emitter.events[-1]["detail"] == {"targets": 1, "flags": 1}
    Path(outcome.scratch_path).unlink()


@pytest.mark.slow
@pytest.mark.django_db
def test_raw_create_entity_is_repaired_with_the_text_naming_the_right_call(ifc_file):
    """model.create_entity is refused before the child spawns; the error names root.create_entity."""
    generator = ScriptedGenerator(RAW_CREATE_ZONE, CREATE_ZONE)

    outcome, _ = _run(ifc_file, generator, 'Create a new IfcZone called "Acoustic Zone 1"')

    assert outcome.attempts == 2
    assert "root.create_entity" in generator.errors[0]
    Path(outcome.scratch_path).unlink()


@pytest.mark.slow
@pytest.mark.django_db
def test_three_failed_attempts_record_the_last_code_on_the_failure_row(ifc_file):
    """A live failure can be read back: the FailureRecord keeps the block that failed last."""
    from metacastor.models import FailureRecord

    generator = ScriptedGenerator(RAW_CREATE_ZONE, RAW_CREATE_ZONE, RAW_CREATE_ZONE)

    with pytest.raises(ModificationError) as raised:
        _run(ifc_file, generator, 'Create a new IfcZone called "Acoustic Zone 1"')

    record = FailureRecord.objects.get(id=raised.value.failure_record_id)
    assert "model.create_entity" in record.ifc_context["code"]
    assert "root.create_entity" in record.error_detail


@pytest.mark.slow
@pytest.mark.django_db
def test_a_creation_hung_from_an_unselected_storey_is_repaired(ifc_file):
    """The live 7B block: project selected, zone aggregated under a storey. One repair, then clean."""
    generator = ScriptedGenerator(MISPLACED_ZONE, CREATE_ZONE)

    outcome, _ = _run(ifc_file, generator, 'Create a new IfcZone called "Acoustic Zone 1"')

    assert outcome.attempts == 2
    assert "was attached to" in generator.errors[0]
    assert "select() must return the storey, building or project" in generator.errors[0]
    assert [(r.kind, r.prop) for r in outcome.rows] == [("added", "IfcZone")]
    Path(outcome.scratch_path).unlink()
