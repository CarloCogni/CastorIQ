# Pre-commit review follow-ups (2026-08-05)

Findings from the pre-commit review of the journal-pipeline / deterministic-Ask /
Explore-3D changeset that were consciously deferred. Each is real but either a design
change or polish; none blocks correctness of the committed code. Delete entries as
they are resolved; delete the file when empty.

## Correctness (design-level)

- **Takeoff vs Ask disagree on type-inherited quantities.** `takeoff/services/quantities.py`
  `_extract_quantity`/`_extract_material` read `nested_view` output but never look under
  `nested["Type"]`, while Ask's `get_prop` falls back to `Type.<pset>.<prop>`. A Revit
  export with Qto psets on the wall *type* makes Ask report an area Takeoff shows as 0%
  coverage. Fix belongs in `_extract_quantity` step 1/2 + `_extract_material`.
- **Takeoff per-type sums can mix units.** `quantities.py` freezes `type_agg[t]["unit"]`
  from the first entity but the fallback chains can yield m² for one wall and m³ for the
  next; the hybrid number feeds `total_cost_estimate`. Needs per-chain (not per-entity)
  aggregation or a unit check before summing.
- **Per-source retrieval floor pollutes doc questions.** `rag_service._apply_threshold`
  returns the closest 3 per source even when they score as noise; 3 junk IFC hits then
  flip the 70% IFC quota in `_pack_with_quota` for purely documentary questions. The floor
  should apply to the merged candidate set.
- **CODE_* failure labels unreachable.** `journal_executor` wraps `CodeSandboxError` into
  `JournalExecutionError`, whose patterns only cover "Generated code failed"/"budget"/…, so
  `CODE_SANDBOX_VIOLATION` etc. land on `IFC_WRITE_GENERIC`. Also six dead
  `EXCEPTION_PATTERNS` rows for the deleted `Tier3ExecutionError`/`Tier3TimeoutError`.

## Performance

- `rag_service._retrieve_vector_context`: add
  `.select_related("spatial_container__entity", "spatial_container__parent__entity")` on the
  IFC hits — `_get_entity_storey_name` currently walks the spatial chain one query per hop.

## Explore 3D polish

- Highlight desyncs from the table on type chips, breadcrumb, search, clear-filter and
  pagination (`_entity_table.html` bare `hx-get`s never call `Explore3D.highlightFilterSet`);
  only tree clicks are wired.
- 3D-pane open/width localStorage keys (`explore3d:open`/`explore3d:width`) are global —
  auto-opens a 900 MB model because a toy project left the pane open. Key by project pk.
- Parent-side `castor:viewer-error` handler is dead: the iframe `load` listener hides the
  loading overlay before a WASM/IFC failure can fire (embed's own overlay covers it).
- Focus is a toggle in the embed but Explore always sends highlight-then-focus, so the
  exit-focus branch is unreachable from Explore's Focus chip.

## Tests / hygiene

- The deterministic branch of `RAGService.generate_answer` (`_answer_deterministic`, bypass
  flag, emit ordering) has no test that patches `match_and_execute`.
- `svc.git = mock_git` in `test_modification_service_execute.py` / 
  `test_execution_lifecycle_sync.py` is a dead assignment (real seam is `svc.execution.git`).
- `TestRetrievalThreshold` should pin `settings.RAG_DISTANCE_CEILING` instead of relying on
  the ambient env value.
- `ask_help_modal.html` "every answer cites…" bullet overpromises for deterministic answers
  (they return `context_items=[]`); `type_breakdown` facts block truncates at 30 types with
  no "…and N more" marker.
- `ifc_writer.set_attribute` guard: `hasattr` passes for entity methods (`id`, `is_a`) and
  only `AttributeError` is suppressed — a `RuntimeError` from IfcOpenShell now escapes
  unclassified.
