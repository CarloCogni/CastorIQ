# ifc_processor/tests/test_tier2_writer.py
"""Tests for Tier2Writer — all IfcOpenShell I/O is mocked."""

from unittest.mock import MagicMock, patch

import pytest

from ifc_processor.services.ifc_writer import IFCWriteError
from ifc_processor.services.tier2_writer import PlanStepResult, Tier2Writer

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_ifc_model():
    """A mock IfcOpenShell model."""
    model = MagicMock()
    model.begin_transaction.return_value = None
    model.undo.return_value = None
    return model


@pytest.fixture
def mock_writer(mock_ifc_model, tmp_path):
    """Tier2Writer with mocked Tier1Writer and IFC model."""
    fake_ifc = tmp_path / "model.ifc"
    fake_ifc.write_bytes(b"ISO-10303-21;\nDATA;\nENDSEC;\nEND-ISO-10303-21;")

    with patch("ifc_processor.services.tier2_writer.Tier1Writer") as MockT1:
        t1_instance = MockT1.return_value
        t1_instance.model = mock_ifc_model
        t1_instance.save.return_value = None

        writer = Tier2Writer.__new__(Tier2Writer)
        writer.ifc_path = fake_ifc
        writer.t1 = t1_instance
        writer.model = mock_ifc_model
        yield writer


def _make_element(global_id: str = "GUID-001", name: str = "Wall-001", ifc_type: str = "IfcWall"):
    """Create a mock IFC element."""
    elem = MagicMock()
    elem.Name = name
    elem.is_a.return_value = ifc_type
    elem.GlobalId = global_id
    elem.HasAssociations = []
    return elem


# ── PlanStepResult ──────────────────────────────────────────────────────────


class TestPlanStepResult:
    """Tests for the PlanStepResult dataclass."""

    def test_ok_true_when_no_error(self):
        """ok property returns True when error is empty."""
        result = PlanStepResult(step_index=0, operation="ADD_PSET")
        assert result.ok is True

    def test_ok_false_when_error_present(self):
        """ok property returns False when error is set."""
        result = PlanStepResult(step_index=0, operation="ADD_PSET", error="something went wrong")
        assert result.ok is False

    def test_default_changes_is_empty_list(self):
        """changes defaults to empty list."""
        result = PlanStepResult(step_index=1, operation="REMOVE_PSET")
        assert result.changes == []


# ── Tier2Writer.save ────────────────────────────────────────────────────────


class TestTier2WriterSave:
    """Tests for Tier2Writer.save()."""

    def test_save_delegates_to_tier1(self, mock_writer):
        """save() calls the underlying Tier1Writer.save()."""
        mock_writer.save()
        mock_writer.t1.save.assert_called_once()


# ── Tier2Writer.add_pset ────────────────────────────────────────────────────


class TestAddPset:
    """Tests for Tier2Writer.add_pset()."""

    def test_add_pset_creates_pset_when_not_exists(self, mock_writer, mock_ifc_model):
        """add_pset creates a new pset and populates it when it doesn't exist."""
        element = _make_element()
        mock_writer.t1._get_element.return_value = element

        with patch("ifc_processor.services.tier2_writer.element_util.get_psets", return_value={}):
            with patch("ifc_processor.services.tier2_writer.ifcopenshell.api.run") as mock_run:
                mock_pset = MagicMock()
                mock_run.return_value = mock_pset

                changes = mock_writer.add_pset(
                    global_ids=["GUID-001"],
                    pset_name="Pset_Custom",
                    properties={"Prop1": "Value1"},
                )

        assert len(changes) == 1
        assert changes[0].pset == "Pset_Custom"
        assert changes[0].property == "Prop1"
        assert changes[0].old_value == "(none)"
        assert changes[0].new_value == "Value1"

    def test_add_pset_merges_when_pset_exists(self, mock_writer, mock_ifc_model):
        """add_pset merges new properties when pset already exists."""
        element = _make_element()
        mock_writer.t1._get_element.return_value = element
        existing_pset_element = MagicMock()
        mock_writer.t1._find_pset_element.return_value = existing_pset_element

        existing_psets = {"Pset_Custom": {"ExistingProp": "OldValue", "id": 1}}

        with patch(
            "ifc_processor.services.tier2_writer.element_util.get_psets",
            return_value=existing_psets,
        ):
            with patch("ifc_processor.services.tier2_writer.ifcopenshell.api.run"):
                changes = mock_writer.add_pset(
                    global_ids=["GUID-001"],
                    pset_name="Pset_Custom",
                    properties={"NewProp": "NewValue"},
                )

        assert len(changes) == 1
        assert changes[0].property == "NewProp"

    def test_add_pset_skips_when_all_props_already_exist(self, mock_writer, mock_ifc_model):
        """add_pset returns empty list if all properties already exist."""
        element = _make_element()
        mock_writer.t1._get_element.return_value = element
        mock_writer.t1._find_pset_element.return_value = MagicMock()

        existing_psets = {"Pset_Custom": {"Prop1": "Value1"}}

        with patch(
            "ifc_processor.services.tier2_writer.element_util.get_psets",
            return_value=existing_psets,
        ):
            changes = mock_writer.add_pset(
                global_ids=["GUID-001"],
                pset_name="Pset_Custom",
                properties={"Prop1": "Value1"},  # Already exists
            )

        assert changes == []


# ── Tier2Writer.remove_pset ─────────────────────────────────────────────────


class TestRemovePset:
    """Tests for Tier2Writer.remove_pset()."""

    def test_remove_pset_records_removed_props(self, mock_writer, mock_ifc_model):
        """remove_pset records all removed properties in changeset."""
        element = _make_element()
        mock_writer.t1._get_element.return_value = element
        pset_element = MagicMock()
        mock_writer.t1._find_pset_element.return_value = pset_element

        existing_psets = {"Pset_WallCommon": {"FireRating": "EI60", "IsExternal": True}}

        with patch(
            "ifc_processor.services.tier2_writer.element_util.get_psets",
            return_value=existing_psets,
        ):
            with patch("ifc_processor.services.tier2_writer.ifcopenshell.api.run"):
                changes = mock_writer.remove_pset(
                    global_ids=["GUID-001"],
                    pset_name="Pset_WallCommon",
                )

        assert len(changes) == 2
        assert all(c.new_value == "(removed)" for c in changes)

    def test_remove_pset_raises_when_pset_not_found(self, mock_writer, mock_ifc_model):
        """remove_pset raises IFCWriteError when pset doesn't exist."""
        element = _make_element()
        mock_writer.t1._get_element.return_value = element

        with patch("ifc_processor.services.tier2_writer.element_util.get_psets", return_value={}):
            with pytest.raises(IFCWriteError, match="not found"):
                mock_writer.remove_pset(
                    global_ids=["GUID-001"],
                    pset_name="Pset_Missing",
                )


# ── Tier2Writer.set_classification ─────────────────────────────────────────


class TestSetClassification:
    """Tests for Tier2Writer.set_classification()."""

    def test_set_classification_creates_reference(self, mock_writer, mock_ifc_model):
        """set_classification assigns a classification reference."""
        element = _make_element()
        mock_writer.t1._get_element.return_value = element
        mock_ifc_model.by_type.return_value = []  # No existing classifications

        with patch("ifc_processor.services.tier2_writer.ifcopenshell.api.run") as mock_run:
            mock_classification = MagicMock()
            mock_run.return_value = mock_classification

            changes = mock_writer.set_classification(
                global_ids=["GUID-001"],
                system_name="Uniclass",
                reference="Ss_20_10",
                name="Walls",
            )

        assert len(changes) == 1
        assert changes[0].pset == "(classification)"
        assert changes[0].property == "Uniclass"
        assert changes[0].new_value == "Ss_20_10"


# ── Tier2Writer.set_material ────────────────────────────────────────────────


class TestSetMaterial:
    """Tests for Tier2Writer.set_material()."""

    def test_set_material_creates_new_material(self, mock_writer, mock_ifc_model):
        """set_material creates material and assigns it."""
        element = _make_element()
        mock_writer.t1._get_element.return_value = element
        mock_ifc_model.by_type.return_value = []  # No existing materials

        with patch("ifc_processor.services.tier2_writer.ifcopenshell.api.run") as mock_run:
            with patch(
                "ifc_processor.services.tier2_writer.ifcopenshell.util.element.get_material",
                return_value=None,
            ):
                mock_material = MagicMock()
                mock_material.Name = "Concrete"
                mock_run.return_value = mock_material

                changes = mock_writer.set_material(
                    global_ids=["GUID-001"],
                    material_name="Concrete",
                )

        assert len(changes) == 1
        assert changes[0].pset == "(material)"
        assert changes[0].property == "Material"
        assert changes[0].new_value == "Concrete"

    def test_set_material_removes_existing_before_assigning(self, mock_writer, mock_ifc_model):
        """set_material removes existing material assignment before assigning new one."""
        element = _make_element()
        mock_writer.t1._get_element.return_value = element
        mock_ifc_model.by_type.return_value = []

        existing_material = MagicMock()
        existing_material.Name = "OldMaterial"

        with patch("ifc_processor.services.tier2_writer.ifcopenshell.api.run") as mock_run:
            with patch(
                "ifc_processor.services.tier2_writer.ifcopenshell.util.element.get_material",
                return_value=existing_material,
            ):
                mock_run.return_value = MagicMock()
                mock_writer.set_material(["GUID-001"], "NewMaterial")

        # unassign_material should have been called
        call_names = [c[0][0] for c in mock_run.call_args_list]
        assert any("unassign" in name for name in call_names)


# ── Tier2Writer.copy_properties ─────────────────────────────────────────────


class TestCopyProperties:
    """Tests for Tier2Writer.copy_properties()."""

    def test_copy_properties_raises_when_source_pset_missing(self, mock_writer):
        """copy_properties raises IFCWriteError if source entity lacks the pset."""
        source = _make_element("SRC-001")
        mock_writer.t1._get_element.return_value = source

        with patch("ifc_processor.services.tier2_writer.element_util.get_psets", return_value={}):
            with pytest.raises(IFCWriteError, match="doesn't have property set"):
                mock_writer.copy_properties(
                    source_global_id="SRC-001",
                    target_global_ids=["TGT-001"],
                    pset_name="Pset_WallCommon",
                )

    def test_copy_properties_raises_when_no_matching_props(self, mock_writer):
        """copy_properties raises when no matching properties found."""
        source = _make_element("SRC-001")
        mock_writer.t1._get_element.return_value = source

        # Pset exists but only has 'id' (metadata key), no actual props
        with patch(
            "ifc_processor.services.tier2_writer.element_util.get_psets",
            return_value={"Pset_WallCommon": {"id": 42}},
        ):
            with pytest.raises(IFCWriteError, match="No matching properties"):
                mock_writer.copy_properties(
                    source_global_id="SRC-001",
                    target_global_ids=["TGT-001"],
                    pset_name="Pset_WallCommon",
                )

    def test_copy_properties_delegates_to_add_pset(self, mock_writer):
        """copy_properties ultimately calls add_pset with source props."""
        source = _make_element("SRC-001")
        mock_writer.t1._get_element.return_value = source

        source_psets = {"Pset_WallCommon": {"FireRating": "EI60", "IsExternal": True}}

        with patch(
            "ifc_processor.services.tier2_writer.element_util.get_psets",
            return_value=source_psets,
        ):
            mock_writer.add_pset = MagicMock(return_value=[])
            mock_writer.copy_properties(
                source_global_id="SRC-001",
                target_global_ids=["TGT-001", "TGT-002"],
                pset_name="Pset_WallCommon",
            )

        mock_writer.add_pset.assert_called_once()
        call_kwargs = mock_writer.add_pset.call_args
        assert call_kwargs[0][0] == ["TGT-001", "TGT-002"]
        assert call_kwargs[0][1] == "Pset_WallCommon"


# ── Tier2Writer internal helpers ────────────────────────────────────────────


class TestInternalHelpers:
    """Tests for internal helper methods."""

    def test_find_or_create_classification_returns_existing(self, mock_writer, mock_ifc_model):
        """Returns existing IfcClassification if name matches."""
        existing_cls = MagicMock()
        existing_cls.Name = "Uniclass"
        mock_ifc_model.by_type.return_value = [existing_cls]

        result = mock_writer._find_or_create_classification("Uniclass")
        assert result is existing_cls

    def test_find_or_create_material_returns_existing(self, mock_writer, mock_ifc_model):
        """Returns existing IfcMaterial if name matches."""
        existing_mat = MagicMock()
        existing_mat.Name = "Concrete"
        mock_ifc_model.by_type.return_value = [existing_mat]

        result = mock_writer._find_or_create_material("Concrete")
        assert result is existing_mat

    def test_get_material_name_returns_none_string_when_no_material(self, mock_writer):
        """_get_material_name returns '(none)' when element has no material."""
        element = MagicMock()

        with patch(
            "ifc_processor.services.tier2_writer.ifcopenshell.util.element.get_material",
            return_value=None,
        ):
            result = mock_writer._get_material_name(element)

        assert result == "(none)"
