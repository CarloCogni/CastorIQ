# writeback/services/benchmark/rav/coverage.py
"""Retrieval coverage of the conflict scanner: which key entities it can see.

Before the model compares anything, the scanner decides which requirement
chunks each entity is shown (``ConflictScanService.build_retrieval_map``). An
entity that no chunk reaches cannot be found in conflict, whatever the model
does, so this report bounds recall without a single model call.

For every entity in the RAV key it records the documents whose requirement
chunks reach it and the documents whose key cases target it. Two totals:

* **reached**: at least one requirement chunk reaches the entity;
* **reached by every constraining document**: each document with a key case
  for the entity reaches it, which is what a complete scan needs.

``coverage_from_map`` is pure and unit-tested; ``retrieval_coverage`` reads the
project's index and embeddings (stored vectors only, no Ollama call).
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .corpus import RavCorpus
from .runner import _document_stem

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EntityCoverage:
    """One key entity: the documents that target it and the ones that reach it."""

    group: str
    global_id: str
    required_documents: tuple[str, ...]
    reached_by: tuple[str, ...]

    @property
    def reached(self) -> bool:
        """At least one requirement chunk reaches the entity."""
        return bool(self.reached_by)

    @property
    def fully_reached(self) -> bool:
        """Every document with a key case for the entity reaches it."""
        return set(self.required_documents) <= set(self.reached_by)


@dataclass
class CoverageReport:
    """Coverage of the key entities for one retrieval setting."""

    entity_first: bool
    entities: list[EntityCoverage]
    pairs_by_pass: dict[str, int] = field(default_factory=dict)
    non_key_reached: dict[str, int] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """``entity-first`` or ``embedding-only``."""
        return "entity-first" if self.entity_first else "embedding-only"

    def reached(self) -> tuple[int, int]:
        """(key entities reached by any requirement chunk, key entities)."""
        return sum(e.reached for e in self.entities), len(self.entities)

    def fully_reached(self) -> tuple[int, int]:
        """(key entities reached by every document that constrains them, key entities)."""
        return sum(e.fully_reached for e in self.entities), len(self.entities)

    def by_group(self) -> dict[str, tuple[int, int, int]]:
        """group → (reached, reached by every constraining document, entities)."""
        out: dict[str, tuple[int, int, int]] = {}
        for entity in self.entities:
            reached, full, total = out.get(entity.group, (0, 0, 0))
            out[entity.group] = (
                reached + entity.reached,
                full + entity.fully_reached,
                total + 1,
            )
        return out

    def as_dict(self) -> dict:
        """JSON-ready form for the run artifact."""
        reached, total = self.reached()
        full, _ = self.fully_reached()
        return {
            "setting": self.label,
            "entity_first": self.entity_first,
            "key_entities": total,
            "reached": reached,
            "reached_by_every_constraining_document": full,
            "pairs_by_pass": self.pairs_by_pass,
            "non_key_reached": self.non_key_reached,
            "entities": [
                {
                    "group": e.group,
                    "global_id": e.global_id,
                    "required_documents": list(e.required_documents),
                    "reached_by": list(e.reached_by),
                }
                for e in self.entities
            ],
        }


def coverage_from_map(
    corpus: RavCorpus,
    entity_documents: dict[str, set[str]],
    *,
    entity_first: bool,
    pairs_by_pass: dict[str, int] | None = None,
    non_key_reached: dict[str, int] | None = None,
) -> CoverageReport:
    """Coverage of the key from ``global_id → documents that reach it``."""
    required: dict[str, set[str]] = {}
    for case in corpus.cases:
        for global_id in case.global_ids:
            required.setdefault(global_id, set()).add(case.document)

    entities = [
        EntityCoverage(
            group=group,
            global_id=global_id,
            required_documents=tuple(sorted(required.get(global_id, set()))),
            reached_by=tuple(sorted(entity_documents.get(global_id, set()))),
        )
        for group, ids in corpus.groups.items()
        for global_id in ids
    ]
    return CoverageReport(
        entity_first=entity_first,
        entities=entities,
        pairs_by_pass=dict(pairs_by_pass or {}),
        non_key_reached=dict(non_key_reached or {}),
    )


def retrieval_coverage(project, corpus: RavCorpus, *, entity_first: bool) -> CoverageReport:
    """Build the scanner's retrieval map for ``project`` and report key coverage."""
    from writeback.services.conflict_scan_service import ConflictScanService

    service = ConflictScanService(project, None, entity_first=entity_first)
    mapping = service.build_retrieval_map()
    key_ids = {global_id for ids in corpus.groups.values() for global_id in ids}

    entity_documents = {
        entity.global_id: {_document_stem(chunk.document.name) for chunk in chunks}
        for entity, chunks in mapping.items()
    }
    non_key = Counter(entity.ifc_type for entity in mapping if entity.global_id not in key_ids)
    report = coverage_from_map(
        corpus,
        entity_documents,
        entity_first=entity_first,
        pairs_by_pass=dict(service.retrieval_stats),
        non_key_reached=dict(sorted(non_key.items())),
    )
    logger.info("RAV coverage [%s]: %d/%d key entities reached", report.label, *report.reached())
    return report


def render_coverage(reports: list[CoverageReport]) -> str:
    """A plain-text table: one column per setting, one row per key group."""
    groups = list(reports[0].by_group()) if reports else []
    header = f"{'key group':<18}" + "".join(f"{r.label:>32}" for r in reports)
    lines = ["", "Retrieval coverage (no model call)", header, "-" * len(header)]
    for group in groups:
        cells = []
        for report in reports:
            reached, full, total = report.by_group()[group]
            cells.append(f"{reached}/{total} reached, {full}/{total} by all docs")
        lines.append(f"{group:<18}" + "".join(f"{c:>32}" for c in cells))
    lines.append("-" * len(header))
    reached_cells = [f"{a}/{b}" for a, b in (r.reached() for r in reports)]
    full_cells = [f"{a}/{b}" for a, b in (r.fully_reached() for r in reports)]
    lines.append(f"{'reached':<18}" + "".join(f"{c:>32}" for c in reached_cells))
    lines.append(f"{'by all docs':<18}" + "".join(f"{c:>32}" for c in full_cells))
    for report in reports:
        lines.append(
            f"{report.label}: pairs by pass {report.pairs_by_pass}; "
            f"non-key entities reached {report.non_key_reached}"
        )
    return "\n".join(lines)


def write_coverage_json(reports: list[CoverageReport], path: str | Path, key: str) -> Path:
    """Write the coverage artifact."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": "rav-retrieval-coverage",
        "key": key,
        "settings": [r.as_dict() for r in reports],
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target
