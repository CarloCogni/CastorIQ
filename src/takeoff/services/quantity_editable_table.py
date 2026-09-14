# takeoff/services/quantity_editable_table.py
"""R5D-QTO-WORKSPACE-05 — durable save/resume of an editable quantity table.

Persists working-table choices (columns, filter, prep settings, measurements,
units, assignments, reviews) keyed to a specific IFC file identity.
Rebuilds from that IFC on open; never silently rebinds to a newer model.

Distinct from QuantityPreparationConfig (settings-only drafts) and from
FiveDModelVersion (immutable Save version). Not BOQ / writeback.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from ifc_processor.models import IFCFile
from takeoff.models import QuantityEditableTable
from takeoff.services.measurement_target import build_measurement_target_key
from takeoff.services.quantity_output_units import (
    CONTRACT_VERSION as UNITS_CONTRACT,
)
from takeoff.services.quantity_output_units import (
    QuantityOutputUnitsService,
)
from takeoff.services.quantity_output_units import (
    session_key_for_project as units_session_key,
)
from takeoff.services.quantity_prep_row_mapping import (
    CONTRACT_VERSION_V1 as MAPPING_CONTRACT,
)
from takeoff.services.quantity_prep_row_mapping import (
    load_payload as load_mapping_payload,
)
from takeoff.services.quantity_prep_row_mapping import (
    save_payload as save_mapping_payload,
)
from takeoff.services.quantity_prep_row_measurement import (
    CONTRACT_VERSION_V1 as MEASUREMENT_CONTRACT,
)
from takeoff.services.quantity_prep_row_measurement import (
    load_payload as load_measurement_payload,
)
from takeoff.services.quantity_prep_row_measurement import (
    save_payload as save_measurement_payload,
)
from takeoff.services.quantity_prep_row_review import (
    CONTRACT_VERSION_V1 as REVIEW_CONTRACT,
)
from takeoff.services.quantity_prep_row_review import (
    load_payload as load_review_payload,
)
from takeoff.services.quantity_prep_row_review import (
    save_payload as save_review_payload,
)
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui

logger = logging.getLogger(__name__)

CONTRACT_VERSION = QuantityEditableTable.CONTRACT_VERSION_V1
EDITABLE_TABLE_QUERY_PARAM = "editable_table"
ACTIVE_SESSION_PREFIX = "qty_editable_active"
DIRTY_SESSION_PREFIX = "qty_editable_dirty"
NAME_MAX_LENGTH = 120

# Presentation-only exclusion from Columns picker (scanning retained).
COLUMNS_UI_EXCLUDED_SOURCE_PROPS: frozenset[str] = frozenset(
    {
        "Other.Category",
        "Other.Family",
        "Other.Family Name",
        "Other.Family and Type",
        "Type.Other.Category",
        "Type.Other.Family",
        "Type.Other.Family Name",
        "Type.Other.Family and Type",
    }
)


class SourceMismatchError(ValueError):
    """Saved table source IFC is missing or content hash no longer matches."""


class StaleRevisionError(ValueError):
    """Client revision is behind the stored revision."""


class InvalidPayloadError(ValueError):
    """Saved-state payload failed validation."""


def active_session_key(project_id: UUID | str) -> str:
    """Session key for the currently open editable table id + revision."""
    return f"{ACTIVE_SESSION_PREFIX}:{project_id}"


def dirty_session_key(project_id: UUID | str) -> str:
    """Session key for unsaved-working-table dirty flag."""
    return f"{DIRTY_SESSION_PREFIX}:{project_id}"


def mark_editable_table_dirty(session: MutableMapping[str, Any], project_id: UUID | str) -> None:
    """Mark the working table dirty after a user mutation."""
    session[dirty_session_key(project_id)] = True
    if hasattr(session, "modified"):
        session.modified = True


def clear_editable_table_dirty(session: MutableMapping[str, Any], project_id: UUID | str) -> None:
    """Clear dirty after a successful save or discard."""
    session[dirty_session_key(project_id)] = False
    if hasattr(session, "modified"):
        session.modified = True


def is_editable_table_dirty(session: Mapping[str, Any], project_id: UUID | str) -> bool:
    """True when session holds unsaved editable-table changes."""
    return bool(session.get(dirty_session_key(project_id)))


def get_active_editable_binding(
    session: Mapping[str, Any], project_id: UUID | str
) -> dict[str, Any] | None:
    """Return ``{id, revision, name}`` for the open table, if any."""
    raw = session.get(active_session_key(project_id))
    if not isinstance(raw, Mapping):
        return None
    tid = str(raw.get("id") or "").strip()
    if not tid:
        return None
    try:
        rev = int(raw.get("revision") or 0)
    except (TypeError, ValueError):
        rev = 0
    return {
        "id": tid,
        "revision": rev,
        "name": str(raw.get("name") or "").strip(),
    }


def set_active_editable_binding(
    session: MutableMapping[str, Any],
    project_id: UUID | str,
    *,
    table_id: UUID | str,
    revision: int,
    name: str,
) -> None:
    """Remember which editable table is open in this session."""
    session[active_session_key(project_id)] = {
        "id": str(table_id),
        "revision": int(revision),
        "name": str(name or "").strip()[:NAME_MAX_LENGTH],
    }
    if hasattr(session, "modified"):
        session.modified = True


def clear_active_editable_binding(
    session: MutableMapping[str, Any], project_id: UUID | str
) -> None:
    """Forget the open editable table binding."""
    session.pop(active_session_key(project_id), None)
    if hasattr(session, "modified"):
        session.modified = True


def resolve_source_ifc(project: Any, *, ifc_file_id: UUID | str | None = None) -> IFCFile | None:
    """Return a completed project IFC by id, or the latest completed when id is None."""
    qs = IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
    if ifc_file_id:
        return qs.filter(pk=ifc_file_id).first()
    return qs.order_by("-created_at").first()


def verify_source_identity(
    *,
    project: Any,
    ifc_file_id: UUID | str,
    expected_hash: str,
) -> IFCFile:
    """Load and verify the pinned IFC; raise SourceMismatchError on failure."""
    expected = str(expected_hash or "").strip().lower()
    ifc = resolve_source_ifc(project, ifc_file_id=ifc_file_id)
    if ifc is None:
        raise SourceMismatchError(
            "The source IFC for this saved table is missing. "
            "Castor will not silently open it against a different model."
        )
    actual = str(getattr(ifc, "file_hash", "") or "").strip().lower()
    if not expected or not actual or actual != expected:
        raise SourceMismatchError(
            "The source IFC content has changed since this table was saved. "
            "Castor will not silently rebind your assignments to a different model."
        )
    return ifc


def _mt_key_from_row(row: Mapping[str, Any], grain: str) -> str:
    return str(
        row.get("measurement_target_key")
        or build_measurement_target_key(
            grain=grain,
            ifc_class=str(row.get("ifc_class") or ""),
            element_type_id=row.get("element_type_id"),
            type_name=str(row.get("type_name") or ""),
        )
    )


def _capture_query_state(query: Mapping[str, Any]) -> dict[str, Any]:
    """Persist supported GET presentation/settings (no page index)."""
    out: dict[str, Any] = {}
    if hasattr(query, "lists"):
        for key, values in query.lists():  # type: ignore[attr-defined]
            k = str(key)
            if k in {"prep_page", "csrfmiddlewaretoken", "editable_table", "sem_cols_add"}:
                continue
            cleaned = [str(v) for v in values if str(v).strip() != ""]
            if not cleaned:
                continue
            if k.startswith("basis_") or k.startswith("field_") or k.startswith("source_"):
                out[k] = cleaned[0]
            elif k in {
                "sem_cols",
                "semantic_field",
                "semantic_value",
                "semantic_op",
                "semantic_value_type",
                "semantic_sort",
                "semantic_sort_dir",
                "prep_page_size",
                "prep_config",
                "col_order",
                "table_layout",
                "semantic_classes",
                "hierarchy_expanded",
                "col_calc",
                "hierarchy_sort",
                "hierarchy_sort_dir",
            }:
                if k == "sem_cols":
                    from takeoff.services.ifc_semantic_fields import parse_sem_cols

                    cols = parse_sem_cols(query)
                    if cols:
                        out["sem_cols"] = ",".join(cols)
                elif k == "col_order":
                    from takeoff.services.quantity_table_layout import (
                        ensure_core_columns,
                        parse_col_order_raw,
                    )

                    order = ensure_core_columns(parse_col_order_raw(cleaned[0]))
                    out["col_order"] = ",".join(order)
                    out["table_layout"] = "v2"
                elif k == "semantic_classes":
                    from takeoff.services.quantity_entity_filter import parse_selected_classes

                    classes = parse_selected_classes(query)
                    if classes:
                        out["semantic_classes"] = ",".join(classes)
                else:
                    out[k] = cleaned[0]
    else:
        for k, v in query.items():
            key = str(k)
            if key in {"prep_page", "csrfmiddlewaretoken", "editable_table", "sem_cols_add"}:
                continue
            if v is None or str(v).strip() == "":
                continue
            out[key] = str(v)
    # REVIEW-08: always persist canonical column layout.
    from takeoff.services.quantity_table_layout import parse_table_layout

    layout = parse_table_layout(query if hasattr(query, "get") else out)
    out["col_order"] = layout["col_order_param"]
    out["table_layout"] = "v2"
    sem = layout.get("sem_cols") or []
    if sem:
        out["sem_cols"] = ",".join(sem)
    elif "sem_cols" in out and not sem:
        # Keep empty sem_cols omitted for cleaner saved state.
        out.pop("sem_cols", None)
    # COLUMNS-10: persist calculation + hierarchy sort with the layout.
    from takeoff.services.quantity_column_calc import col_calc_param, parse_col_calc
    from takeoff.services.quantity_hierarchy_sort import parse_hierarchy_sort

    calc = parse_col_calc(query if hasattr(query, "get") else out)
    add_calc = ""
    if hasattr(query, "get"):
        add_calc = str(query.get("col_calc_add") or "").strip()
    if add_calc and ":" in add_calc:
        ck, op = add_calc.rsplit(":", 1)
        if ck.strip() and op.strip():
            calc[ck.strip()] = op.strip().lower()
    calc_s = col_calc_param(calc)
    if calc_s:
        out["col_calc"] = calc_s
    else:
        out.pop("col_calc", None)
    hsort = parse_hierarchy_sort(query if hasattr(query, "get") else out)
    if hsort.get("key"):
        out["hierarchy_sort"] = hsort["key"]
        out["hierarchy_sort_dir"] = hsort.get("dir") or "asc"
    else:
        out.pop("hierarchy_sort", None)
        out.pop("hierarchy_sort_dir", None)
    return out


def query_dict_from_saved(saved_query: Mapping[str, Any]) -> dict[str, str]:
    """Flatten saved query map for redirect / runtime."""
    return {str(k): str(v) for k, v in saved_query.items() if v is not None and str(v) != ""}


def query_state_matches_saved(
    query: Mapping[str, Any], saved_query: Mapping[str, Any] | None
) -> bool:
    """True when current GET presentation matches the last saved query capture."""
    current = _capture_query_state(query)
    saved = {
        str(k): str(v) for k, v in (saved_query or {}).items() if v is not None and str(v) != ""
    }
    return current == saved


class QuantityEditableTableService:
    """Save, list, open, and update durable editable quantity tables."""

    def __init__(self, project: Any, user: Any | None = None) -> None:
        self.project = project
        self.user = user

    def list_tables(self) -> list[QuantityEditableTable]:
        """Tables for this project the user may open (project access assumed)."""
        return list(
            QuantityEditableTable.objects.filter(project=self.project)
            .select_related("created_by", "ifc_file")
            .order_by("-updated_at")[:100]
        )

    def get_table(self, table_id: UUID | str) -> QuantityEditableTable | None:
        """Return a project-scoped table or None."""
        return (
            QuantityEditableTable.objects.filter(project=self.project, pk=table_id)
            .select_related("ifc_file", "created_by")
            .first()
        )

    def capture_working_state(
        self,
        *,
        session: MutableMapping[str, Any],
        query: Mapping[str, Any],
        ifc_file: IFCFile,
    ) -> dict[str, Any]:
        """Build a durable payload from the current session + query + prep rows."""
        runtime = build_qty_prep_session_ui(
            project=self.project,
            user=self.user,
            session=session,
            query=query,
            ifc_file=ifc_file,
        )
        qty_prep = runtime["qty_prep"]
        grain = str(qty_prep.get("prep_row_grain") or "type")
        # Include instance leaves + visible hierarchy parents + legacy aggregates so
        # both new instance assignments and legacy type/class annotations capture.
        rows: list[dict[str, Any]] = []
        seen_rk: set[str] = set()
        for bucket in (
            qty_prep.get("prep_rows_export"),
            qty_prep.get("prep_rows"),
            qty_prep.get("prep_rows_aggregate_legacy"),
        ):
            for r in bucket or []:
                if not isinstance(r, dict) or r.get("is_load_more"):
                    continue
                rk = str(r.get("row_key") or "")
                if not rk or rk in seen_rk:
                    continue
                seen_rk.add(rk)
                rows.append(r)
        row_by_key = {str(r.get("row_key") or ""): r for r in rows if r.get("row_key")}

        def _grain_for_row(r: dict[str, Any]) -> str:
            level = str(r.get("level") or "")
            if level == "instance":
                return "instance"
            if level == "type":
                return "type"
            if level == "class":
                return "ifc_class"
            return grain if grain in {"type", "ifc_class", "instance"} else "ifc_class"

        mt_by_row = {
            str(r.get("row_key") or ""): (
                str(r.get("measurement_target_key") or "")
                or _mt_key_from_row(r, _grain_for_row(r))
            )
            for r in rows
            if r.get("row_key")
        }

        mapping_ann = load_mapping_payload(session, self.project.pk).get("annotations") or {}
        review_ann = load_review_payload(session, self.project.pk).get("annotations") or {}
        measure_choices = load_measurement_payload(session, self.project.pk).get("choices") or {}
        units = QuantityOutputUnitsService(self.project, self.user, session).get_output_units()
        class_units = QuantityOutputUnitsService(
            self.project, self.user, session
        ).get_class_units()

        assignments: dict[str, Any] = {}
        unmatched_assignments: list[str] = []
        ambiguous_assignments: list[str] = []
        for row_key, fields in mapping_ann.items():
            rk = str(row_key)
            row = row_by_key.get(rk)
            if row is None:
                unmatched_assignments.append(rk)
                continue
            mt = mt_by_row.get(rk) or _mt_key_from_row(row, grain)
            if mt in assignments and assignments[mt] != fields:
                ambiguous_assignments.append(mt)
                continue
            assignments[mt] = fields

        reviews: dict[str, Any] = {}
        unmatched_reviews: list[str] = []
        for row_key, payload in review_ann.items():
            rk = str(row_key)
            row = row_by_key.get(rk)
            if row is None:
                unmatched_reviews.append(rk)
                continue
            mt = mt_by_row.get(rk) or _mt_key_from_row(row, grain)
            reviews[mt] = payload

        measurements = {
            str(k): v
            for k, v in measure_choices.items()
            if isinstance(v, Mapping) and str(k).startswith("mt1|")
        }

        query_state = _capture_query_state(query)
        from takeoff.services.quantity_hierarchy import load_expanded_keys

        expanded = sorted(load_expanded_keys(session, self.project.pk))
        if expanded:
            query_state["hierarchy_expanded"] = ",".join(expanded)

        return {
            "contract_version": CONTRACT_VERSION,
            "source": {
                "ifc_file_id": str(ifc_file.pk),
                "file_hash": str(ifc_file.file_hash or ""),
                "ifc_file_name": str(ifc_file.name or ""),
            },
            "query": query_state,
            "output_units": {
                "contract_version": UNITS_CONTRACT,
                "units": dict(units or {}),
                "class_units": {
                    cls: dict(fam) for cls, fam in (class_units or {}).items()
                },
            },
            "measurements": {
                "contract_version": MEASUREMENT_CONTRACT,
                "choices": measurements,
            },
            "assignments": {
                "contract_version": MAPPING_CONTRACT,
                "by_measurement_target": assignments,
                "unmatched_row_keys": unmatched_assignments,
                "ambiguous_targets": ambiguous_assignments,
            },
            "reviews": {
                "contract_version": REVIEW_CONTRACT,
                "by_measurement_target": reviews,
                "unmatched_row_keys": unmatched_reviews,
            },
            "captured_at": timezone.now().isoformat(),
        }

    def validate_state(self, state: Mapping[str, Any], *, strict: bool = True) -> dict[str, Any]:
        """Validate and normalize a saved-state payload."""
        if not isinstance(state, Mapping):
            raise InvalidPayloadError("Saved state must be an object.")
        version = str(state.get("contract_version") or "").strip()
        if version != CONTRACT_VERSION:
            raise InvalidPayloadError(
                f"Unsupported editable-table contract {version!r}; expected {CONTRACT_VERSION}."
            )
        source = state.get("source")
        if not isinstance(source, Mapping):
            raise InvalidPayloadError("Saved state is missing source identity.")
        ifc_id = str(source.get("ifc_file_id") or "").strip()
        file_hash = str(source.get("file_hash") or "").strip()
        if not ifc_id or not file_hash:
            raise InvalidPayloadError("Saved state must include ifc_file_id and file_hash.")

        query = state.get("query")
        if not isinstance(query, Mapping):
            raise InvalidPayloadError("Saved state query must be an object.")

        def _section(name: str, default_contract: str) -> dict[str, Any]:
            raw = state.get(name)
            if raw is None:
                return {"contract_version": default_contract}
            if not isinstance(raw, Mapping):
                if strict:
                    raise InvalidPayloadError(f"{name} must be an object.")
                return {"contract_version": default_contract}
            return dict(raw)

        return {
            "contract_version": CONTRACT_VERSION,
            "source": {
                "ifc_file_id": ifc_id,
                "file_hash": file_hash,
                "ifc_file_name": str(source.get("ifc_file_name") or ""),
            },
            "query": {str(k): str(v) for k, v in query.items()},
            "output_units": _section("output_units", UNITS_CONTRACT),
            "measurements": _section("measurements", MEASUREMENT_CONTRACT),
            "assignments": _section("assignments", MAPPING_CONTRACT),
            "reviews": _section("reviews", REVIEW_CONTRACT),
            "captured_at": str(state.get("captured_at") or ""),
        }

    @transaction.atomic
    def save_new(
        self,
        *,
        name: str,
        session: MutableMapping[str, Any],
        query: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Create a new editable table from the current working state."""
        label = str(name or "").strip()[:NAME_MAX_LENGTH]
        if not label:
            return {"result": None, "error": "Name is required."}
        ifc = resolve_source_ifc(self.project)
        if ifc is None:
            return {"result": None, "error": "No completed IFC is available to save against."}
        if not str(ifc.file_hash or "").strip():
            return {
                "result": None,
                "error": "Source IFC has no content hash; cannot create a durable saved table.",
            }
        state = self.validate_state(
            self.capture_working_state(session=session, query=query, ifc_file=ifc)
        )
        table = QuantityEditableTable.objects.create(
            project=self.project,
            created_by=self.user if getattr(self.user, "pk", None) else None,
            updated_by=self.user if getattr(self.user, "pk", None) else None,
            name=label,
            contract_version=CONTRACT_VERSION,
            ifc_file=ifc,
            ifc_file_hash=str(ifc.file_hash),
            ifc_file_name=str(ifc.name or ""),
            revision=1,
            state=state,
        )
        set_active_editable_binding(
            session,
            self.project.pk,
            table_id=table.pk,
            revision=table.revision,
            name=table.name,
        )
        clear_editable_table_dirty(session, self.project.pk)
        logger.info("editable table created id=%s project=%s", table.pk, self.project.pk)
        return {"result": table, "error": None}

    @transaction.atomic
    def save_update(
        self,
        *,
        table_id: UUID | str,
        expected_revision: int,
        session: MutableMapping[str, Any],
        query: Mapping[str, Any],
        name: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing table; reject stale revisions without mutating state."""
        table = (
            QuantityEditableTable.objects.select_for_update()
            .filter(project=self.project, pk=table_id)
            .first()
        )
        if table is None:
            return {"result": None, "error": "Saved table not found."}
        try:
            exp = int(expected_revision)
        except (TypeError, ValueError):
            return {"result": None, "error": "Revision is required."}
        if exp != int(table.revision):
            raise StaleRevisionError(
                "This table was saved from another session. "
                "Reload it before saving again — your current session changes were not overwritten."
            )
        try:
            ifc = verify_source_identity(
                project=self.project,
                ifc_file_id=table.ifc_file_id,
                expected_hash=table.ifc_file_hash,
            )
        except SourceMismatchError as exc:
            return {"result": None, "error": str(exc)}

        try:
            state = self.validate_state(
                self.capture_working_state(session=session, query=query, ifc_file=ifc)
            )
        except InvalidPayloadError as exc:
            return {"result": None, "error": str(exc)}

        if name is not None:
            label = str(name).strip()[:NAME_MAX_LENGTH]
            if label:
                table.name = label
        table.state = state
        table.revision = int(table.revision) + 1
        table.updated_by = self.user if getattr(self.user, "pk", None) else None
        table.ifc_file_name = str(ifc.name or "")
        table.save(
            update_fields=[
                "name",
                "state",
                "revision",
                "updated_by",
                "ifc_file_name",
                "updated_at",
            ]
        )
        set_active_editable_binding(
            session,
            self.project.pk,
            table_id=table.pk,
            revision=table.revision,
            name=table.name,
        )
        clear_editable_table_dirty(session, self.project.pk)
        logger.info("editable table updated id=%s rev=%s", table.pk, table.revision)
        return {"result": table, "error": None}

    def restore_into_session(
        self,
        *,
        table: QuantityEditableTable,
        session: MutableMapping[str, Any],
    ) -> dict[str, Any]:
        """Verify source, rebuild prep, restore overlays by measurement_target_key."""
        try:
            ifc = verify_source_identity(
                project=self.project,
                ifc_file_id=table.ifc_file_id,
                expected_hash=table.ifc_file_hash,
            )
        except SourceMismatchError as exc:
            return {"result": None, "error": str(exc), "query": {}, "ifc_file": None}

        try:
            state = self.validate_state(table.state or {})
        except InvalidPayloadError as exc:
            return {"result": None, "error": str(exc), "query": {}, "ifc_file": None}

        query = query_dict_from_saved(state.get("query") or {})
        runtime = build_qty_prep_session_ui(
            project=self.project,
            user=self.user,
            session={},
            query=query,
            ifc_file=ifc,
        )
        qty_prep = runtime["qty_prep"]
        grain = str(qty_prep.get("prep_row_grain") or "type")
        rows = []
        seen_rk: set[str] = set()
        for bucket in (
            qty_prep.get("prep_rows_export"),
            qty_prep.get("prep_rows"),
            qty_prep.get("prep_rows_aggregate_legacy"),
        ):
            for row in bucket or []:
                if not isinstance(row, dict) or row.get("is_load_more"):
                    continue
                rk = str(row.get("row_key") or "")
                if not rk or rk in seen_rk:
                    continue
                seen_rk.add(rk)
                rows.append(row)
        rows_by_mt: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            level = str(row.get("level") or "")
            g = (
                "instance"
                if level == "instance"
                else (
                    "type"
                    if level == "type"
                    else (
                        "ifc_class"
                        if level == "class"
                        else (grain if grain != "hierarchy" else "ifc_class")
                    )
                )
            )
            mt = str(row.get("measurement_target_key") or "") or _mt_key_from_row(row, g)
            rows_by_mt.setdefault(mt, []).append(row)

        measure_raw = state.get("measurements") or {}
        choices_in = measure_raw.get("choices") if isinstance(measure_raw, Mapping) else {}
        measure_out: dict[str, Any] = {}
        if isinstance(choices_in, Mapping):
            for k, v in choices_in.items():
                if str(k) in rows_by_mt and isinstance(v, Mapping):
                    measure_out[str(k)] = dict(v)
        save_measurement_payload(
            session,
            self.project.pk,
            {"contract_version": MEASUREMENT_CONTRACT, "choices": measure_out},
        )

        runtime2 = build_qty_prep_session_ui(
            project=self.project,
            user=self.user,
            session=session,
            query=query,
            ifc_file=ifc,
        )
        qty2 = runtime2["qty_prep"]
        rows2 = []
        seen2: set[str] = set()
        for bucket in (
            qty2.get("prep_rows_export"),
            qty2.get("prep_rows"),
            qty2.get("prep_rows_aggregate_legacy"),
        ):
            for row in bucket or []:
                if not isinstance(row, dict) or row.get("is_load_more"):
                    continue
                rk = str(row.get("row_key") or "")
                if not rk or rk in seen2:
                    continue
                seen2.add(rk)
                rows2.append(row)
        rows_by_mt2: dict[str, list[dict[str, Any]]] = {}
        for row in rows2:
            level = str(row.get("level") or "")
            g = (
                "instance"
                if level == "instance"
                else (
                    "type"
                    if level == "type"
                    else (
                        "ifc_class"
                        if level == "class"
                        else (
                            str(qty2.get("prep_row_grain") or grain)
                            if str(qty2.get("prep_row_grain") or "") != "hierarchy"
                            else "ifc_class"
                        )
                    )
                )
            )
            mt = str(row.get("measurement_target_key") or "") or _mt_key_from_row(row, g)
            rows_by_mt2.setdefault(mt, []).append(row)

        assign_raw = state.get("assignments") or {}
        by_mt = assign_raw.get("by_measurement_target") if isinstance(assign_raw, Mapping) else {}
        mapping_ann: dict[str, Any] = {}
        unmatched: list[str] = []
        ambiguous: list[str] = []
        if isinstance(by_mt, Mapping):
            for mt, fields in by_mt.items():
                targets = rows_by_mt2.get(str(mt)) or []
                if not targets:
                    unmatched.append(str(mt))
                    continue
                if len(targets) > 1:
                    ambiguous.append(str(mt))
                    continue
                rk = str(targets[0].get("row_key") or "")
                if not rk:
                    unmatched.append(str(mt))
                    continue
                mapping_ann[rk] = fields
        save_mapping_payload(
            session,
            self.project.pk,
            {"contract_version": MAPPING_CONTRACT, "annotations": mapping_ann},
        )

        review_raw = state.get("reviews") or {}
        rev_by_mt = (
            review_raw.get("by_measurement_target") if isinstance(review_raw, Mapping) else {}
        )
        review_ann: dict[str, Any] = {}
        if isinstance(rev_by_mt, Mapping):
            for mt, payload in rev_by_mt.items():
                targets = rows_by_mt2.get(str(mt)) or []
                if len(targets) != 1:
                    continue
                rk = str(targets[0].get("row_key") or "")
                if rk:
                    review_ann[rk] = payload
        save_review_payload(
            session,
            self.project.pk,
            {"contract_version": REVIEW_CONTRACT, "annotations": review_ann},
        )

        units_raw = state.get("output_units") or {}
        units_map = units_raw.get("units") if isinstance(units_raw, Mapping) else {}
        class_units_raw = (
            units_raw.get("class_units") if isinstance(units_raw, Mapping) else {}
        )
        session[units_session_key(self.project.pk)] = {
            "contract_version": UNITS_CONTRACT,
            "units": dict(units_map) if isinstance(units_map, Mapping) else {},
            "class_units": (
                dict(class_units_raw) if isinstance(class_units_raw, Mapping) else {}
            ),
        }
        if hasattr(session, "modified"):
            session.modified = True

        set_active_editable_binding(
            session,
            self.project.pk,
            table_id=table.pk,
            revision=table.revision,
            name=table.name,
        )
        clear_editable_table_dirty(session, self.project.pk)

        return {
            "result": table,
            "error": None,
            "query": query,
            "ifc_file": ifc,
            "restore_report": {
                "unmatched_assignment_targets": unmatched,
                "ambiguous_assignment_targets": ambiguous,
            },
        }
