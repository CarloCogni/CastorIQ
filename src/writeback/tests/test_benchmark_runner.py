# writeback/tests/test_benchmark_runner.py
"""The runner resolves GlobalId-free expectations through the index and scores outcomes.

The pipeline is replaced by a canned object so no LLM or sandbox runs; the
index rows are real.
"""

from unittest.mock import MagicMock, patch

import pytest

from ifc_processor.services.ifc_diff import IfcDiff, PropertyChange
from ifc_processor.tests.factories import IFCEntityFactory, IFCSpatialElementFactory
from writeback.services.benchmark.corpus import parse_corpus
from writeback.services.benchmark.runner import BenchmarkRunner, _value_matches, resolve_targets
from writeback.services.errors import ModelUnavailableError, ModificationError, NoChangeError
from writeback.services.pipeline import PipelineOutcome
from writeback.services.verifier import aggregate_rows, flag_rows


@pytest.fixture(autouse=True)
def written_copy_is_clean():
    """Integrity re-reads the scratch copy from disk; the canned outcomes have none."""
    with patch("writeback.services.benchmark.runner.diff_files", return_value=IfcDiff()) as reread:
        yield reread


@pytest.fixture
def house(ifc_file):
    """Two storeys, one space, five walls (two internal), one door in the space."""
    ground_e = IFCEntityFactory(
        ifc_file=ifc_file, ifc_type="IfcBuildingStorey", name="Ground Floor", properties={}
    )
    ground = IFCSpatialElementFactory(
        ifc_file=ifc_file, entity=ground_e, spatial_type="building_storey"
    )
    roof_e = IFCEntityFactory(
        ifc_file=ifc_file, ifc_type="IfcBuildingStorey", name="Roof", properties={}
    )
    IFCSpatialElementFactory(ifc_file=ifc_file, entity=roof_e, spatial_type="building_storey")
    space_e = IFCEntityFactory(
        ifc_file=ifc_file, ifc_type="IfcSpace", name="1 - Living room", properties={}
    )
    space = IFCSpatialElementFactory(
        ifc_file=ifc_file, entity=space_e, spatial_type="space", parent=ground
    )
    walls = []
    for i in range(3):
        walls.append(
            IFCEntityFactory(
                ifc_file=ifc_file,
                ifc_type="IfcWall",
                name=f"Basic Wall:Ext:28533{i}",
                global_id=f"W-EXT-{i}",
                spatial_container=ground,
                properties={"Pset_WallCommon.IsExternal": True},
            )
        )
    for i in range(2):
        walls.append(
            IFCEntityFactory(
                ifc_file=ifc_file,
                ifc_type="IfcWallStandardCase",
                name=f"Basic Wall:Partn:28579{i}",
                global_id=f"W-INT-{i}",
                spatial_container=ground,
                properties={"Pset_WallCommon.IsExternal": False},
            )
        )
    IFCEntityFactory(
        ifc_file=ifc_file,
        ifc_type="IfcDoor",
        name="Door-1",
        global_id="D-1",
        spatial_container=space,
        properties={},
    )
    return ifc_file


def _case(tmp_path, block: str):
    path = tmp_path / "c.txt"
    path.write_text(f"# 1.1 — t\n{block}\nprompt\n", encoding="utf-8")
    return parse_corpus(path)[0]


def _outcome(
    ifc_file, targets, diff, request="set FireRating to EI60 on all walls", scratch_path=None
):
    return PipelineOutcome(
        ifc_file=ifc_file,
        code="c",
        targets=targets,
        diff=diff,
        rows=flag_rows(aggregate_rows(diff), request),
        scratch_path=scratch_path or str(ifc_file.file.path) + ".nope",
        base_fingerprint="f",
        explanation="e",
        explainer_model="m",
        attempts=2,
        grounding=MagicMock(),
    )


def _diff(rows=(), added=None, removed=None):
    return {
        "schema_changed": False,
        "type_count_delta": {},
        "added_global_ids": [],
        "removed_global_ids": [],
        "geometry_changed": [],
        "property_changes": list(rows),
        "attribute_changes": [],
        "added_objects": added or {},
        "removed_objects": removed or {},
    }


def _runner(house, outcome=None, error=None):
    with patch("writeback.services.benchmark.runner.ModifyPipeline"):
        runner = BenchmarkRunner(house.project, ifc_file=house)
    runner.pipeline = MagicMock()
    if error is not None:
        runner.pipeline.run.side_effect = error
    else:
        runner.pipeline.run.return_value = outcome
    return runner


# ── resolve_targets ───────────────────────────────────────────────


@pytest.mark.django_db
def test_resolve_targets_includes_subclasses_and_filters_by_container_name_and_property(house):
    """IfcWall x5 in the storey; named narrows; where reads the flat properties."""
    from writeback.services.benchmark.corpus import TargetExpectation

    assert (
        len(resolve_targets(house, TargetExpectation("IfcWall", 5, container="Ground Floor"))) == 5
    )
    assert resolve_targets(house, TargetExpectation("IfcWall", 1, named=":285331")) == {"W-EXT-1"}
    assert resolve_targets(
        house,
        TargetExpectation(
            "IfcWall", 2, where_key="Pset_WallCommon.IsExternal", where_value="false"
        ),
    ) == {"W-INT-0", "W-INT-1"}


@pytest.mark.django_db
def test_resolve_targets_walks_from_a_space_to_its_storey(house):
    """A door indexed under a space still counts as 'in "Ground Floor"'."""
    from writeback.services.benchmark.corpus import TargetExpectation

    assert resolve_targets(house, TargetExpectation("IfcDoor", 1, container="Ground Floor")) == {
        "D-1"
    }
    assert resolve_targets(house, TargetExpectation("IfcDoor", 1, container="1 - Living room")) == {
        "D-1"
    }


# ── scoring ───────────────────────────────────────────────────────


@pytest.mark.django_db
def test_change_case_passes_when_targets_diff_and_integrity_hold(house, tmp_path):
    """The right five walls, the expected row with count 5, nothing outside: pass."""
    case = _case(
        tmp_path,
        '# targets: IfcWall x5 in "Ground Floor"\n# diff: Pset_WallCommon.FireRating = EI60 x5',
    )
    ids = [f"W-EXT-{i}" for i in range(3)] + [f"W-INT-{i}" for i in range(2)]
    diff = _diff(
        [
            {
                "global_id": g,
                "pset": "Pset_WallCommon",
                "prop": "FireRating",
                "before": None,
                "after": "EI60",
            }
            for g in ids
        ]
    )

    scratch = tmp_path / ".house.proposal-abcd1234.ifc"
    scratch.write_bytes(b"x")

    result = _runner(house, _outcome(house, ids, diff, scratch_path=str(scratch))).run_case(case)

    assert result.outcome == "proposal"
    assert result.targets_match is True
    assert result.diff_match is True
    assert result.integrity_ok is True
    assert result.attempts == 2 and result.repairs == 1
    assert result.passed
    assert not scratch.exists()  # deleted after scoring


@pytest.mark.django_db
def test_integrity_re_reads_the_written_copy_and_flags_a_stray_change(house, tmp_path):
    """The column is measured from the bytes on disk, not from the pipeline's own gate."""
    case = _case(tmp_path, "# targets: IfcWall x1")
    stray = IfcDiff(
        attribute_changes=[PropertyChange("W-EXT-1", "", "Name", "a", "b")],
    )
    with patch("writeback.services.benchmark.runner.diff_files", return_value=stray) as reread:
        result = _runner(house, _outcome(house, ["W-EXT-0"], _diff())).run_case(case)

    reread.assert_called_once_with(house.file.path, str(house.file.path) + ".nope")
    assert result.integrity_ok is False
    assert "W-EXT-1" in result.integrity_detail
    assert not result.passed


@pytest.mark.django_db
def test_integrity_fails_when_the_written_copy_cannot_be_read(house, tmp_path):
    case = _case(tmp_path, "# targets: IfcWall x1")
    with patch("writeback.services.benchmark.runner.diff_files", side_effect=OSError("gone")):
        result = _runner(house, _outcome(house, ["W-EXT-0"], _diff())).run_case(case)
    assert result.integrity_ok is False and "gone" in result.integrity_detail


@pytest.mark.django_db
def test_a_change_case_the_model_refused_stays_in_the_targets_and_diff_denominators(
    house, tmp_path
):
    """A rejection on a change case is a failed selection and a failed diff, not a missing score."""
    case = _case(tmp_path, "# targets: IfcWall x5\n# diff: Pset_WallCommon.FireRating = EI60 x5")
    result = _runner(
        house, error=ModificationError("Could not produce a valid change after 3 attempts.")
    ).run_case(case)

    assert result.outcome == "rejected"
    assert result.targets_match is False
    assert result.diff_match is False
    assert "got a rejection" in result.targets_detail
    assert not result.passed


@pytest.mark.django_db
def test_a_change_case_that_is_already_so_stays_in_the_targets_and_diff_denominators(
    house, tmp_path
):
    """'Already so' on a change case changed nothing: false on both columns, like a rejection."""
    case = _case(tmp_path, "# targets: IfcWall x5\n# diff: Pset_WallCommon.FireRating = EI60 x5")
    targets = [f"W-INT-{i}" for i in range(3)] + ["W-EXT-0", "W-EXT-1"]
    result = _runner(house, error=NoChangeError("Already so", targets=targets)).run_case(case)

    assert result.outcome == "no_change"
    assert result.targets_match is False
    assert result.diff_match is False
    assert result.targets_detail.startswith("already so:")
    assert not result.passed


@pytest.mark.django_db
def test_a_model_that_cannot_answer_is_a_harness_error_not_a_refusal(house, tmp_path):
    """A timeout on a reject case is not a correct refusal; it leaves every score alone."""
    case = _case(tmp_path, "# reject:")
    result = _runner(
        house, error=ModelUnavailableError("The Modify model did not answer within 240 s.")
    ).run_case(case)

    assert result.outcome == "error"
    assert "ModelUnavailableError" in result.error
    assert result.targets_match is None and result.diff_match is None
    assert not result.passed


@pytest.mark.django_db
def test_wrong_selection_fails_targets_match(house, tmp_path):
    """Three walls selected where the corpus says five: targets-match false, detail says so."""
    case = _case(tmp_path, "# targets: IfcWall x5\n# diff: Pset_WallCommon.FireRating = EI60 x5")
    ids = [f"W-EXT-{i}" for i in range(3)]
    diff = _diff(
        [
            {
                "global_id": g,
                "pset": "Pset_WallCommon",
                "prop": "FireRating",
                "before": None,
                "after": "EI60",
            }
            for g in ids
        ]
    )

    result = _runner(house, _outcome(house, ids, diff)).run_case(case)

    assert result.targets_match is False
    assert "2 missing" in result.targets_detail
    assert result.diff_match is False
    assert not result.passed


@pytest.mark.django_db
def test_corpus_expectation_that_does_not_resolve_is_reported(house, tmp_path):
    """A count the index cannot produce is a corpus error, surfaced on the case."""
    case = _case(tmp_path, "# targets: IfcWall x9")
    result = _runner(house, _outcome(house, ["W-EXT-0"], _diff())).run_case(case)
    assert result.targets_match is False
    assert "index resolves it to 5" in result.targets_detail


@pytest.mark.django_db
def test_diff_expectations_match_removals_creations_attributes_and_wildcards(house, tmp_path):
    """Every diff kind is matched against the aggregated rows."""
    case = _case(
        tmp_path,
        "# diff: - Pset_WallCommon.Reference x2\n# diff: + IfcZone x1\n# diff: Name = New x1\n# diff: *.Status = Pending x1\n# diff: Materials = concrete x1",
    )
    diff = _diff(
        [
            {
                "global_id": "W-EXT-0",
                "pset": "Pset_WallCommon",
                "prop": "Reference",
                "before": "x",
                "after": None,
            },
            {
                "global_id": "W-EXT-1",
                "pset": "Pset_WallCommon",
                "prop": "Reference",
                "before": "x",
                "after": None,
            },
            {
                "global_id": "W-EXT-0",
                "pset": "Pset_Custom",
                "prop": "Status",
                "before": None,
                "after": "Pending",
            },
        ],
        added={"Z-1": "IfcZone"},
    )
    diff["attribute_changes"] = [
        {"global_id": "W-EXT-0", "pset": "", "prop": "Name", "before": "Old", "after": "New"},
        {
            "global_id": "W-EXT-0",
            "pset": "",
            "prop": "Materials",
            "before": [],
            "after": ["Concrete C30"],
        },
    ]
    result = _runner(house, _outcome(house, ["W-EXT-0", "W-EXT-1"], diff, request="x")).run_case(
        case
    )
    assert result.diff_match is True, result.diff_detail


@pytest.mark.django_db
def test_value_matches_is_equality_for_scalars_and_containment_for_list_items():
    """EI60 is not REI60; numbers compare as numbers however the file stores them."""
    assert _value_matches("EI60", "ei60")
    assert not _value_matches("REI60", "EI60")
    assert not _value_matches("EI600", "EI60")
    assert not _value_matches("Pending approval", "Pending")
    assert _value_matches(30, "30") and _value_matches(30.0, "30") and _value_matches("30.0", "30")
    assert _value_matches(True, "true") and not _value_matches(True, "1")
    assert _value_matches(None, "none") and not _value_matches(None, "EI60")
    assert _value_matches(["Concrete C30"], "concrete")
    assert not _value_matches(["Brick"], "concrete")


@pytest.mark.django_db
def test_reject_case_passes_on_rejection_and_fails_on_a_proposal(house, tmp_path):
    """A request that must be declined passes when rejected, fails when a proposal appears."""
    case = _case(tmp_path, '# reject: "geometry"')
    rejected = _runner(house, error=ModificationError("Declined: this changes geometry")).run_case(
        case
    )
    assert rejected.outcome == "rejected" and rejected.passed

    wrong_reason = _runner(house, error=ModificationError("Declined: unclear")).run_case(case)
    assert not wrong_reason.passed

    proposed = _runner(house, _outcome(house, ["W-EXT-0"], _diff())).run_case(case)
    assert proposed.outcome == "proposal" and not proposed.passed


@pytest.mark.django_db
def test_no_change_case_passes_only_on_no_change(house, tmp_path):
    """'already so' is its own outcome; a rejection for that case fails."""
    case = _case(tmp_path, "# no-change:")
    ok = _runner(house, error=NoChangeError("Already so", targets=["W-INT-0"])).run_case(case)
    assert ok.outcome == "no_change" and ok.passed and ok.targets_actual == 1
    bad = _runner(house, error=ModificationError("nope")).run_case(case)
    assert not bad.passed


@pytest.mark.django_db
def test_advisory_case_never_fails_and_unexpected_exception_is_an_error(house, tmp_path):
    """Advisory passes whatever happens; a crash is recorded, not raised."""
    advisory = _case(tmp_path, "# advisory: fixture-dependent")
    assert _runner(house, error=ModificationError("x")).run_case(advisory).passed
    crashed = _runner(house, error=RuntimeError("boom")).run_case(_case(tmp_path, "# reject:"))
    assert crashed.outcome == "error" and "boom" in crashed.error and not crashed.passed
