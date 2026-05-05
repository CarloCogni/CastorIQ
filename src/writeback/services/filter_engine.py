# writeback/services/filter_engine.py
"""
Filter resolution engine for IFC entity selection.

Translates structured filter expressions from the LLM
into Django ORM queries. No AI here — just query building.
"""

import logging
import re

from django.db.models import QuerySet

from ifc_processor.models import IFCEntity

logger = logging.getLogger(__name__)


class FilterEngine:
    """
    Resolves entity filters against the database.

    Supports:
        - ifc_type: exact match (e.g. "IfcWall")
        - storey: match on spatial container storey name
        - property_match: key-value conditions on JSON properties
        - name_pattern: glob-style name matching (e.g. "D-*")
        - global_ids: explicit list of entity IDs
        - tag: substring match on the IFC Tag attribute
        - ifc_description: substring match on the IFC Description attribute

    Usage:
        engine = FilterEngine(project)
        entities = engine.resolve({
            "ifc_type": "IfcWall",
            "property_match": {"Pset_WallCommon.IsExternal": True}
        })
    """

    def __init__(self, project):
        self.project = project

    def resolve(self, filter_spec: dict) -> QuerySet:
        """
        Resolve a filter specification to a queryset of IFC entities.

        Args:
            filter_spec: Dict with optional keys: ifc_type, storey,
                         property_match, name_pattern, global_ids,
                         tag, ifc_description

        Returns:
            Filtered IFCEntity queryset.

        Raises:
            ValueError: If filter matches zero entities.
        """
        if not filter_spec:
            raise ValueError("Empty filter — refusing to match all entities.")

        qs = IFCEntity.objects.filter(
            ifc_file__project=self.project,
            ifc_file__status="completed",
        )

        qs = self._apply_ifc_type(qs, filter_spec.get("ifc_type"))
        qs = self._apply_storey(qs, filter_spec.get("storey"))
        qs = self._apply_name_pattern(qs, filter_spec.get("name_pattern"))
        qs = self._apply_global_ids(qs, filter_spec.get("global_ids"))
        qs = self._apply_tag(qs, filter_spec.get("tag"))
        qs = self._apply_ifc_description(qs, filter_spec.get("ifc_description"))
        qs = self._apply_property_match(qs, filter_spec.get("property_match"))

        count = qs.count()
        if count == 0:
            raise ValueError(f"Filter matched 0 entities. Filter: {filter_spec}")

        logger.info(f"Filter resolved to {count} entities: {filter_spec}")
        return qs

    # ── Individual Filters ─────────────────────────────────

    def _apply_ifc_type(self, qs: QuerySet, ifc_type: str | None) -> QuerySet:
        if not ifc_type:
            return qs
        # Case-insensitive startswith to catch e.g. IfcWallStandardCase
        return qs.filter(ifc_type__istartswith=ifc_type)

    def _apply_storey(self, qs: QuerySet, storey: str | None) -> QuerySet:
        if not storey:
            return qs
        return qs.filter(
            spatial_container__spatial_type="building_storey",
            spatial_container__entity__name__icontains=storey,
        )

    def _apply_name_pattern(self, qs: QuerySet, pattern: str | None) -> QuerySet:
        if not pattern:
            return qs
        # Substring match by default — Revit-exported names are commonly
        # prefixed (e.g. "Basic Wall:_Dekkeforkant 255mm:386330"), so an
        # anchored prefix glob like "_Dekkeforkant*" would never match.
        # No wildcards → plain icontains. Wildcards → unanchored regex.
        if "*" not in pattern:
            return qs.filter(name__icontains=pattern)
        regex = re.escape(pattern).replace(r"\*", ".*")
        return qs.filter(name__iregex=regex)

    def _apply_global_ids(self, qs: QuerySet, global_ids: list | None) -> QuerySet:
        if not global_ids:
            return qs
        return qs.filter(global_id__in=global_ids)

    def _apply_tag(self, qs: QuerySet, tag: str | None) -> QuerySet:
        if not tag:
            return qs
        # Tags are user-defined element ids (typically the source-app id).
        # Exact-equal is the common case; fall back to icontains for partial.
        if "*" in tag:
            regex = re.escape(tag).replace(r"\*", ".*")
            return qs.filter(tag__iregex=regex)
        return qs.filter(tag__iexact=tag)

    def _apply_ifc_description(self, qs: QuerySet, description: str | None) -> QuerySet:
        if not description:
            return qs
        return qs.filter(ifc_description__icontains=description)

    def _apply_property_match(self, qs: QuerySet, property_match: dict | None) -> QuerySet:
        """
        Filter by JSON property values.

        property_match keys use dot notation: "Pset_WallCommon.IsExternal"
        which maps to a key inside the properties JSONField.

        Type-aware comparison:
          * bool / str / int → exact JSON containment (fast, indexable).
          * float            → 5% relative tolerance, evaluated in Python.
            IFC files routinely store full-precision floats (e.g. Revit
            exports ThermalTransmittance as 0.235926059936681), so a user
            who types "0.24" must still match. Absolute floor of 1e-6
            covers values near zero.
        """
        if not property_match:
            return qs

        for key, value in property_match.items():
            if isinstance(value, bool) or not isinstance(value, float):
                # bool / int / str → exact JSON containment.
                qs = qs.filter(properties__contains={key: value})
                continue

            # Float path: tolerant comparison evaluated in Python.
            tolerance = max(0.05 * abs(value), 1e-6)
            candidate_qs = qs.filter(properties__has_key=key)
            matching_ids: list = []
            for entity_id, props in candidate_qs.values_list("id", "properties"):
                stored = (props or {}).get(key)
                try:
                    stored_f = float(stored)
                except (TypeError, ValueError):
                    continue
                if abs(stored_f - value) <= tolerance:
                    matching_ids.append(entity_id)
            qs = qs.filter(id__in=matching_ids)

        return qs
