# ifc_processor/tests/test_ifc_writer.py
"""Tests for Tier1Writer and ifc_writer helpers — IfcOpenShell always mocked."""

from unittest.mock import MagicMock, patch

import pytest

from ifc_processor.services.ifc_writer import IFCWriteError, Tier1Writer

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_pset_element(prop_name: str, prop_value=None, nominal_type: str = "IfcText"):
    """Build a minimal mock IfcPropertySet element with one property."""
    nominal = MagicMock()
    nominal.is_a.return_value = nominal_type
    nominal.wrappedValue = prop_value

    prop_entity = MagicMock()
    prop_entity.Name = prop_name
    prop_entity.is_a.return_value = "IfcPropertySingleValue"
    prop_entity.NominalValue = nominal

    pset_element = MagicMock()
    pset_element.Name = "Pset_WallCommon"
    pset_element.is_a.return_value = "IfcPropertySet"
    pset_element.HasProperties = [prop_entity]

    return pset_element, prop_entity


def _make_element(global_id: str = "GUID-001", name: str = "Wall-001"):
    """Build a minimal mock IFC element."""
    element = MagicMock()
    element.GlobalId = global_id
    element.Name = name
    element.is_a.return_value = "IfcWall"
    return element


@pytest.fixture
def mock_ifc_model():
    """A mock IfcOpenShell file object with one element."""
    model = MagicMock()
    element = _make_element()

    pset_element, prop_entity = _make_pset_element("FireRating", "EI60")

    # Simulate model.by_guid returning our element
    model.by_guid.return_value = element

    # Simulate element.IsDefinedBy for _find_pset_element
    rel = MagicMock()
    rel.is_a.return_value = True
    rel.is_a.side_effect = lambda x: x == "IfcRelDefinesByProperties"
    rel.RelatingPropertyDefinition = pset_element
    element.IsDefinedBy = [rel]

    return model, element, pset_element, prop_entity


@pytest.fixture
def writer(mock_ifc_model, tmp_path):
    """Tier1Writer backed by a mocked IfcOpenShell model."""
    model, element, pset_element, prop_entity = mock_ifc_model
    fake_ifc = tmp_path / "test.ifc"
    fake_ifc.write_bytes(b"ISO-10303-21;\nDATA;\nENDSEC;\nEND-ISO-10303-21;")

    with patch("ifc_processor.services.ifc_writer.ifcopenshell.open", return_value=model):
        w = Tier1Writer(str(fake_ifc))

    w._mock_model = model
    w._mock_element = element
    w._mock_pset = pset_element
    w._mock_prop = prop_entity
    return w


# ── _resolve_property_name ────────────────────────────────────────────────────


class TestResolvePropertyName:
    """Tests for Tier1Writer._resolve_property_name()."""

    def test_exact_match_returns_property_name(self, writer):
        """Exact key match returns the property name unchanged."""
        pset_dict = {"FireRating": "EI60", "IsExternal": True}
        result = writer._resolve_property_name(pset_dict, "FireRating")
        assert result == "FireRating"

    def test_case_insensitive_match_returns_real_key(self, writer):
        """Case-insensitive match returns the actual dict key."""
        pset_dict = {"FireRating": "EI60", "IsExternal": True}
        result = writer._resolve_property_name(pset_dict, "firerating")
        assert result == "FireRating"

    def test_no_match_returns_none(self, writer):
        """Returns None when neither exact nor case-insensitive match exists."""
        pset_dict = {"FireRating": "EI60"}
        result = writer._resolve_property_name(pset_dict, "Acoustic")
        assert result is None

    def test_skips_id_key(self, writer):
        """The IfcOpenShell injected 'id' key is ignored in case-insensitive fallback."""
        pset_dict = {"id": 42, "FireRating": "EI60"}
        result = writer._resolve_property_name(pset_dict, "id")
        # 'id' should not be returned via fallback path either
        assert result == "id"  # exact match IS allowed; only fallback skips it


# ── _coerce_for_ifc_type ─────────────────────────────────────────────────────


class TestCoerceForIfcType:
    """Tests for Tier1Writer._coerce_for_ifc_type() — pure function, no mocks."""

    def _make_single_value_prop(self, type_name: str):
        """Helper: make a mock IfcPropertySingleValue with given nominal type."""
        nominal = MagicMock()
        nominal.is_a.return_value = type_name

        prop = MagicMock()
        prop.is_a.return_value = "IfcPropertySingleValue"
        prop.NominalValue = nominal
        return prop

    def _make_writer(self):
        """Minimal writer without real file (for pure coerce tests)."""
        with patch("ifc_processor.services.ifc_writer.ifcopenshell.open", return_value=MagicMock()):
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as f:
                f.write(b"ISO-10303-21;\nDATA;\nENDSEC;\nEND-ISO-10303-21;")
                tmp = f.name
        with patch("ifc_processor.services.ifc_writer.ifcopenshell.open", return_value=MagicMock()):
            return Tier1Writer(tmp)

    def test_none_prop_entity_returns_value_unchanged(self, writer):
        """None prop_entity passes through the value as-is."""
        result = writer._coerce_for_ifc_type(None, "hello")
        assert result == "hello"

    def test_ifc_boolean_true_string_coerces_to_true(self, writer):
        """'true' string for IfcBoolean becomes Python True."""
        prop = self._make_single_value_prop("IfcBoolean")
        result = writer._coerce_for_ifc_type(prop, "true")
        assert result is True

    def test_ifc_boolean_false_string_coerces_to_false(self, writer):
        """'false' string for IfcBoolean becomes Python False."""
        prop = self._make_single_value_prop("IfcBoolean")
        result = writer._coerce_for_ifc_type(prop, "false")
        assert result is False

    def test_ifc_real_string_coerces_to_float(self, writer):
        """'3.5' string for IfcReal becomes 3.5 float."""
        prop = self._make_single_value_prop("IfcReal")
        result = writer._coerce_for_ifc_type(prop, "3.5")
        assert result == pytest.approx(3.5)
        assert isinstance(result, float)

    def test_ifc_integer_string_coerces_to_int(self, writer):
        """'42' string for IfcInteger becomes 42 int."""
        prop = self._make_single_value_prop("IfcInteger")
        result = writer._coerce_for_ifc_type(prop, "42")
        assert result == 42
        assert isinstance(result, int)

    def test_ifc_length_measure_coerces_to_float(self, writer):
        """IfcLengthMeasure coerces to float."""
        prop = self._make_single_value_prop("IfcLengthMeasure")
        result = writer._coerce_for_ifc_type(prop, "2.5")
        assert result == pytest.approx(2.5)

    def test_ifc_text_returns_string_as_is(self, writer):
        """IfcText returns the string value unchanged."""
        prop = self._make_single_value_prop("IfcText")
        result = writer._coerce_for_ifc_type(prop, "hello")
        assert result == "hello"

    def test_ifc_enumerated_value_wraps_in_list(self, writer):
        """IfcPropertyEnumeratedValue wraps value in uppercase list."""
        prop = MagicMock()
        prop.is_a.return_value = "IfcPropertyEnumeratedValue"
        result = writer._coerce_for_ifc_type(prop, "notfire")
        assert isinstance(result, list)
        assert result[0] == "NOTFIRE"

    def test_ifc_enumerated_value_already_list_unchanged(self, writer):
        """Already-list value for IfcPropertyEnumeratedValue passes through."""
        prop = MagicMock()
        prop.is_a.return_value = "IfcPropertyEnumeratedValue"
        result = writer._coerce_for_ifc_type(prop, ["FIRE"])
        assert result == ["FIRE"]

    def test_ifc_list_value_wraps_scalar_in_list(self, writer):
        """IfcPropertyListValue wraps scalar in a list."""
        prop = MagicMock()
        prop.is_a.return_value = "IfcPropertyListValue"
        result = writer._coerce_for_ifc_type(prop, "item")
        assert result == ["item"]

    def test_unknown_ifc_class_returns_value_unchanged(self, writer):
        """Unknown IFC property class returns value as-is."""
        prop = MagicMock()
        prop.is_a.return_value = "IfcSomeUnknownType"
        result = writer._coerce_for_ifc_type(prop, "value")
        assert result == "value"


# ── set_property ─────────────────────────────────────────────────────────────


class TestSetProperty:
    """Tests for Tier1Writer.set_property() — uses mocked IFC model."""

    def test_set_property_missing_pset_raises_for_non_standard_pset(self, writer, mock_ifc_model):
        """For NON-standard psets, set_property still raises when pset missing —
        upsert is only allowed for standard IFC psets where the pset can be
        auto-created safely."""
        with patch(
            "ifc_processor.services.ifc_writer.element_util.get_psets",
            return_value={},
        ):
            with pytest.raises(IFCWriteError, match="not found"):
                writer.set_property(
                    global_ids=["GUID-001"],
                    pset="Pset_CustomFireCompliance",  # non-standard
                    prop="Standard",
                    value="FS-2026",
                )

    def test_set_property_missing_property_raises_for_non_standard_pset(
        self, writer, mock_ifc_model
    ):
        """For NON-standard psets, set_property raises when the specific
        property is absent. Standard psets get upsert; custom psets stay strict."""
        with patch(
            "ifc_processor.services.ifc_writer.element_util.get_psets",
            return_value={"Pset_CustomFireCompliance": {"OtherProp": True}},
        ):
            with pytest.raises(IFCWriteError, match="not found"):
                writer.set_property(
                    global_ids=["GUID-001"],
                    pset="Pset_CustomFireCompliance",
                    prop="Standard",
                    value="FS-2026",
                )

    def test_set_property_upserts_when_pset_missing_on_standard_pset(self, writer, mock_ifc_model):
        """For standard psets, set_property auto-creates the pset and adds
        the property. The returned EntityChange has old_value='(none)'."""
        from ifc_processor.services.ifc_writer import EntityChange

        with patch(
            "ifc_processor.services.ifc_writer.element_util.get_psets",
            return_value={},  # pset missing
        ):
            with patch(
                "ifc_processor.services.ifc_writer.coerce_from_registry",
                return_value="EI120",
            ):
                with patch("ifc_processor.services.ifc_writer.ifcopenshell.api.run"):
                    changes = writer.set_property(
                        global_ids=["GUID-001"],
                        pset="Pset_WallCommon",
                        prop="FireRating",
                        value="EI120",
                    )

        assert len(changes) == 1
        assert isinstance(changes[0], EntityChange)
        assert changes[0].old_value == "(none)"
        assert changes[0].new_value == "EI120"

    def test_set_property_upserts_when_property_missing_on_standard_pset(
        self, writer, mock_ifc_model
    ):
        """Standard pset present, property missing → set_property adds the
        property via pset.edit_pset (which auto-adds when missing)."""
        from ifc_processor.services.ifc_writer import EntityChange

        with patch(
            "ifc_processor.services.ifc_writer.element_util.get_psets",
            return_value={"Pset_WallCommon": {"IsExternal": True}},
        ):
            with patch(
                "ifc_processor.services.ifc_writer.coerce_from_registry",
                return_value="EI120",
            ):
                with patch("ifc_processor.services.ifc_writer.ifcopenshell.api.run"):
                    changes = writer.set_property(
                        global_ids=["GUID-001"],
                        pset="Pset_WallCommon",
                        prop="FireRating",
                        value="EI120",
                    )

        assert len(changes) == 1
        assert isinstance(changes[0], EntityChange)
        assert changes[0].old_value == "(none)"
        assert changes[0].new_value == "EI120"

    def test_set_property_returns_entity_change_list(self, writer, mock_ifc_model):
        """set_property on a found property returns a list of EntityChange objects."""
        from ifc_processor.services.ifc_writer import EntityChange

        model, element, pset_element, prop_entity = mock_ifc_model

        with patch(
            "ifc_processor.services.ifc_writer.element_util.get_psets",
            return_value={"Pset_WallCommon": {"FireRating": "EI60"}},
        ):
            with patch(
                "ifc_processor.services.ifc_writer.coerce_from_registry", return_value="EI120"
            ):
                with patch("ifc_processor.services.ifc_writer.ifcopenshell.api.run"):
                    changes = writer.set_property(
                        global_ids=["GUID-001"],
                        pset="Pset_WallCommon",
                        prop="FireRating",
                        value="EI120",
                    )

        assert len(changes) == 1
        assert isinstance(changes[0], EntityChange)
        assert changes[0].new_value == "EI120"
        assert changes[0].old_value == "EI60"


# ── add_property ──────────────────────────────────────────────────────────────


class TestAddProperty:
    """Tests for Tier1Writer.add_property()."""

    def test_add_property_raises_when_already_exists(self, writer):
        """add_property raises IFCWriteError when the property already exists."""
        with patch(
            "ifc_processor.services.ifc_writer.element_util.get_psets",
            return_value={"Pset_WallCommon": {"FireRating": "EI60"}},
        ):
            with pytest.raises(IFCWriteError, match="already exists"):
                writer.add_property(
                    global_ids=["GUID-001"],
                    pset="Pset_WallCommon",
                    prop="FireRating",
                    value="EI120",
                )


# ── remove_property ───────────────────────────────────────────────────────────


class TestRemoveProperty:
    """Tests for Tier1Writer.remove_property()."""

    def test_remove_property_raises_when_not_found(self, writer):
        """remove_property raises IFCWriteError when the property doesn't exist."""
        with patch(
            "ifc_processor.services.ifc_writer.element_util.get_psets",
            return_value={"Pset_WallCommon": {}},
        ):
            with pytest.raises(IFCWriteError, match="not found"):
                writer.remove_property(
                    global_ids=["GUID-001"],
                    pset="Pset_WallCommon",
                    prop="FireRating",
                )

    def test_remove_property_succeeds_when_property_exists(self, writer, mock_ifc_model):
        """remove_property returns EntityChange list when property exists."""
        from ifc_processor.services.ifc_writer import EntityChange

        model, element, pset_element, prop_entity = mock_ifc_model

        with patch(
            "ifc_processor.services.ifc_writer.element_util.get_psets",
            return_value={"Pset_WallCommon": {"FireRating": "EI60"}},
        ):
            with patch("ifc_processor.services.ifc_writer.ifcopenshell.api.run"):
                changes = writer.remove_property(
                    global_ids=["GUID-001"],
                    pset="Pset_WallCommon",
                    prop="FireRating",
                )

        assert len(changes) == 1
        assert isinstance(changes[0], EntityChange)
        assert changes[0].new_value == "(removed)"


# ── set_attribute ─────────────────────────────────────────────────────────────


class TestSetAttribute:
    """Tests for Tier1Writer.set_attribute()."""

    def test_set_attribute_returns_entity_change(self, writer):
        """set_attribute returns a list with one EntityChange on success."""
        from ifc_processor.services.ifc_writer import EntityChange

        with patch("ifc_processor.services.ifc_writer.ifcopenshell.api.run"):
            changes = writer.set_attribute(
                global_ids=["GUID-001"],
                attribute="Name",
                value="Wall-Renamed",
            )

        assert len(changes) == 1
        assert isinstance(changes[0], EntityChange)
        assert changes[0].new_value == "Wall-Renamed"

    def test_set_attribute_description_routes_through_edit_attributes(self, writer):
        """SET_ATTRIBUTE on Description must call attribute.edit_attributes."""
        with patch("ifc_processor.services.ifc_writer.ifcopenshell.api.run") as mock_run:
            changes = writer.set_attribute(
                global_ids=["GUID-001"],
                attribute="Description",
                value="Exterior load-bearing wall",
            )

        assert len(changes) == 1
        assert changes[0].property == "Description"
        assert changes[0].new_value == "Exterior load-bearing wall"
        mock_run.assert_called_once()
        # The api.run call should target attribute.edit_attributes with the new value.
        args, kwargs = mock_run.call_args
        assert args[0] == "attribute.edit_attributes"
        assert kwargs["attributes"] == {"Description": "Exterior load-bearing wall"}

    def test_set_attribute_tag_routes_through_edit_attributes(self, writer):
        """SET_ATTRIBUTE on Tag must call attribute.edit_attributes."""
        with patch("ifc_processor.services.ifc_writer.ifcopenshell.api.run") as mock_run:
            changes = writer.set_attribute(
                global_ids=["GUID-001"],
                attribute="Tag",
                value="REV-285330",
            )

        assert len(changes) == 1
        assert changes[0].property == "Tag"
        assert changes[0].new_value == "REV-285330"
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == "attribute.edit_attributes"
        assert kwargs["attributes"] == {"Tag": "REV-285330"}


# ── _get_element ──────────────────────────────────────────────────────────────


class TestGetElement:
    """Tests for Tier1Writer._get_element()."""

    def test_get_element_raises_when_not_found(self, writer, mock_ifc_model):
        """_get_element raises IFCWriteError when by_guid returns None."""
        model, element, pset_element, prop_entity = mock_ifc_model
        model.by_guid.return_value = None

        with pytest.raises(IFCWriteError, match="not found"):
            writer._get_element("NONEXISTENT-GUID")
