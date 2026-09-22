# ifc_processor/services/description_builder.py
"""Build the semantic description that gets embedded for RAG retrieval.

Replaces the parser's old keyword-whitelist template. Each entity's text now
carries the facts questions actually ask about — materials (with layer
build-up), classifications, the defining type name, property sets with
provenance, and quantities with units — extracted via IfcOpenShell at parse
time. Behavioural spec for materials/units follows ifc-lite's extractors
(ifc-lite/packages/parser/src/material-*.ts, unit-extractor.ts), reimplemented
here on IfcOpenShell.

The builder is deliberately defensive: every IFC graph walk is wrapped so a
malformed relationship degrades one sentence, never the parse.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import ifcopenshell.util.classification as classification_util
import ifcopenshell.util.element as element_util

from ifc_processor.services.property_access import nested_view

logger = logging.getLogger(__name__)

# Property names from non-standard psets worth surfacing even without schema
# metadata — the old whitelist, kept for custom psets (e.g. authoring-tool
# psets like "AC_Pset_*").
_FALLBACK_KEYWORDS: tuple[str, ...] = (
    "firerating",
    "fire_rating",
    "material",
    "loadbearing",
    "load_bearing",
    "height",
    "width",
    "length",
    "thickness",
    "area",
    "volume",
    "u_value",
    "u-value",
    "acoustic",
    "thermal",
    "resistance",
    "isexternal",
)

# Quantity-name keyword → project unit-type key (IFCFile.project_units).
_QUANTITY_UNIT_KEYS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("area",), "AREAUNIT"),
    (("volume",), "VOLUMEUNIT"),
    (("length", "width", "height", "depth", "thickness", "perimeter"), "LENGTHUNIT"),
    (("weight", "mass"), "MASSUNIT"),
)

_MAX_PROPS_PER_PSET = 12
_MAX_QUANTITIES = 10


class DescriptionBuilder:
    """Assemble entity descriptions for one IFC file (holds the unit table)."""

    def __init__(self, project_units: dict[str, str] | None):
        self.units = project_units or {}

    # ── Public API ─────────────────────────────────────────────────────

    def build(
        self,
        element,
        flat_props: dict[str, Any],
        *,
        type_name: str = "",
        location_text: str = "",
    ) -> str:
        """Return the description text for one element."""
        sentences: list[str] = [self._identity_sentence(element, type_name)]

        if location_text:
            sentences.append(f"Location: {location_text}")

        nested = nested_view(flat_props)
        sentences.extend(self._safe(self._material_sentence, element))
        sentences.extend(self._safe(self._classification_sentence, element))
        sentences.extend(self._property_sentences(nested))
        sentences.extend(self._quantity_sentences(nested))
        sentences.extend(self._dimension_sentence(nested))
        sentences.extend(self._safe(self._group_sentence, element))
        sentences.extend(self._safe(self._opening_sentence, element))

        return ". ".join(s for s in sentences if s) + "."

    # ── Sentence builders ──────────────────────────────────────────────

    @staticmethod
    def _identity_sentence(element, type_name: str) -> str:
        raw_type = element.is_a()
        clean_type = raw_type.replace("Ifc", "")
        human_type = re.sub(r"(?<!^)(?=[A-Z])", " ", clean_type)
        name = element.Name or "Unnamed"

        sentence = f"This is a {clean_type}. It is a {human_type} element named '{name}'"
        if type_name:
            sentence += f", of type '{type_name}'"
        long_name = getattr(element, "LongName", None)
        if long_name and long_name != name:
            sentence += f", also known as '{long_name}'"
        return sentence

    def _material_sentence(self, element) -> str:
        material = element_util.get_material(element, should_skip_usage=True)
        if material is None:
            type_obj = element_util.get_type(element)
            if type_obj is not None:
                material = element_util.get_material(type_obj, should_skip_usage=True)
        if material is None:
            return ""

        if material.is_a("IfcMaterial"):
            return f"Material: {material.Name}"

        if material.is_a("IfcMaterialLayerSet"):
            unit = self.units.get("LENGTHUNIT", "")
            layers = []
            for layer in material.MaterialLayers or ():
                layer_name = layer.Material.Name if layer.Material else "Unknown"
                thickness = getattr(layer, "LayerThickness", None)
                if thickness is not None:
                    layers.append(f"{layer_name} ({self._fmt(thickness)} {unit})".rstrip())
                else:
                    layers.append(layer_name)
            return f"Material layers: {', '.join(layers)}" if layers else ""

        if material.is_a("IfcMaterialProfileSet"):
            names = {p.Material.Name for p in material.MaterialProfiles or () if p.Material}
            return f"Material profiles: {', '.join(sorted(names))}" if names else ""

        if material.is_a("IfcMaterialConstituentSet"):
            names = {c.Material.Name for c in material.MaterialConstituents or () if c.Material}
            return f"Material constituents: {', '.join(sorted(names))}" if names else ""

        if material.is_a("IfcMaterialList"):
            names = {m.Name for m in material.Materials or ()}
            return f"Materials: {', '.join(sorted(names))}" if names else ""

        return ""

    @staticmethod
    def _classification_sentence(element) -> str:
        references = classification_util.get_references(element)
        parts = []
        for ref in references or ():
            identification = getattr(ref, "Identification", None) or getattr(
                ref, "ItemReference", None
            )
            label = " ".join(p for p in (identification, getattr(ref, "Name", None)) if p)
            if label:
                parts.append(label)
        return f"Classification: {'; '.join(parts)}" if parts else ""

    def _property_sentences(self, nested: dict[str, Any]) -> list[str]:
        """Standard *Common psets verbatim; keyword-matched props from the rest."""
        sentences: list[str] = []
        fallback_props: list[str] = []

        for scope_label, psets in self._iter_pset_scopes(nested):
            for pset_name, props in psets.items():
                if pset_name.startswith("Qto_") or not isinstance(props, dict):
                    continue
                if pset_name.startswith("Pset_") and pset_name.endswith("Common"):
                    pairs = [
                        f"{k}={self._fmt(v)}" for k, v in list(props.items())[:_MAX_PROPS_PER_PSET]
                    ]
                    if pairs:
                        sentences.append(f"{scope_label}{pset_name}: {', '.join(pairs)}")
                else:
                    fallback_props.extend(
                        f"{k}={self._fmt(v)}"
                        for k, v in props.items()
                        if any(t in k.lower() for t in _FALLBACK_KEYWORDS)
                    )

        if fallback_props:
            sentences.append(f"Properties: {', '.join(fallback_props[:_MAX_PROPS_PER_PSET])}")
        return sentences

    def _quantity_sentences(self, nested: dict[str, Any]) -> list[str]:
        sentences = []
        for scope_label, psets in self._iter_pset_scopes(nested):
            for pset_name, props in psets.items():
                if not pset_name.startswith("Qto_") or not isinstance(props, dict):
                    continue
                pairs = [
                    f"{k}={self._fmt(v)} {self._quantity_unit(k)}".rstrip()
                    for k, v in list(props.items())[:_MAX_QUANTITIES]
                    if isinstance(v, (int, float))
                ]
                if pairs:
                    sentences.append(f"Quantities ({scope_label}{pset_name}): {', '.join(pairs)}")
        return sentences

    def _dimension_sentence(self, nested: dict[str, Any]) -> list[str]:
        """Bare attribute keys (OverallWidth/OverallHeight on doors/windows)."""
        unit = self.units.get("LENGTHUNIT", "")
        pairs = [
            f"{key}={self._fmt(value)} {unit}".rstrip()
            for key, value in nested.items()
            if isinstance(value, (int, float)) and key.startswith("Overall")
        ]
        return [f"Dimensions: {', '.join(pairs)}"] if pairs else []

    @staticmethod
    def _group_sentence(element) -> str:
        """System and zone membership via IfcRelAssignsToGroup."""
        names = []
        for rel in getattr(element, "HasAssignments", None) or ():
            if not rel.is_a("IfcRelAssignsToGroup"):
                continue
            group = rel.RelatingGroup
            if group.is_a("IfcSystem") or group.is_a("IfcZone"):
                kind = "zone" if group.is_a("IfcZone") else "system"
                if group.Name:
                    names.append(f"{kind} '{group.Name}'")
        return f"Part of {', '.join(names)}" if names else ""

    @staticmethod
    def _opening_sentence(element) -> str:
        """Voids/fills relationships: what a door/window sits in, wall openings."""
        fills = getattr(element, "FillsVoids", None) or ()
        for rel in fills:
            opening = rel.RelatingOpeningElement
            for void_rel in getattr(opening, "VoidsElements", None) or ():
                host = void_rel.RelatingBuildingElement
                host_name = getattr(host, "Name", None)
                if host_name:
                    return f"Fills an opening in {host.is_a().replace('Ifc', '')} '{host_name}'"

        openings = getattr(element, "HasOpenings", None) or ()
        count = len(openings)
        if count:
            return f"Has {count} opening{'s' if count != 1 else ''}"
        return ""

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _iter_pset_scopes(nested: dict[str, Any]):
        """Yield (scope_label, psets) for occurrence then type-level psets."""
        yield "", {k: v for k, v in nested.items() if k != "Type" and isinstance(v, dict)}
        type_scope = nested.get("Type")
        if isinstance(type_scope, dict):
            yield "Type ", {k: v for k, v in type_scope.items() if isinstance(v, dict)}

    def _quantity_unit(self, quantity_name: str) -> str:
        lowered = quantity_name.lower()
        for keywords, unit_key in _QUANTITY_UNIT_KEYS:
            if any(keyword in lowered for keyword in keywords):
                return self.units.get(unit_key, "")
        return ""

    @staticmethod
    def _fmt(value: Any) -> str:
        if isinstance(value, float):
            return f"{value:.4g}"
        return str(value)

    @staticmethod
    def _safe(builder, element) -> list[str]:
        """Run one sentence builder, degrading to nothing on any IFC quirk."""
        try:
            sentence = builder(element)
            return [sentence] if sentence else []
        except Exception as exc:
            logger.debug("Description sentence failed for %s: %s", element, exc)
            return []
