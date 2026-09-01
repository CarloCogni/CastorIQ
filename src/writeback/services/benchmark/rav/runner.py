# writeback/services/benchmark/rav/runner.py
"""Run the conflict scanner with chosen settings and score it against the key.

Scoring is pure and separated from running (``score_findings``) so it can be
unit-tested without a database or an LLM, and so a saved run artifact can be
re-scored against an updated key.

Matching rule: a finding matches a key case when its entity is in the case's
GlobalId set, its property canonicalises to the case's property, and it cites
a chunk from the case's document. The document constraint matters because two
documents can legitimately disagree about the same property (the fire strategy
calls the external walls non-load-bearing, the structural notes call them
load-bearing) and the key labels each statement separately.

The scanner attributes a finding to the chunk named by the LLM's own
``source_chunk_index``, which is sometimes wrong. Strict scoring charges that
as a miss plus a false positive; ``score_findings(..., match_document=False)``
scores the same findings ignoring the document, so the gap between the two
numbers is exactly the cost of misattribution.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .corpus import KeyCase, RavCorpus, canonical_property

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanSettings:
    """The knobs the ablation sweeps. Defaults mirror production."""

    confidence_threshold: float = 0.7
    type_gate: bool = True
    keyword_filter: bool = True
    entity_relevance_threshold: float | None = None
    entity_top_k: int | None = None
    skip_low_value: bool = True

    def label(self) -> str:
        """Short tag for tables: ``default`` or the knobs that differ from it."""
        parts = []
        if not self.type_gate:
            parts.append("no-type-gate")
        if not self.keyword_filter:
            parts.append("no-keyword-filter")
        if self.confidence_threshold != 0.7:
            parts.append(f"conf={self.confidence_threshold:g}")
        if self.entity_relevance_threshold is not None:
            parts.append(f"dist={self.entity_relevance_threshold:g}")
        if self.entity_top_k is not None:
            parts.append(f"k={self.entity_top_k}")
        if not self.skip_low_value:
            parts.append("all-types")
        return "+".join(parts) or "default"

    def as_dict(self) -> dict:
        return {
            "confidence_threshold": self.confidence_threshold,
            "type_gate": self.type_gate,
            "keyword_filter": self.keyword_filter,
            "entity_relevance_threshold": self.entity_relevance_threshold,
            "entity_top_k": self.entity_top_k,
            "skip_low_value": self.skip_low_value,
        }


@dataclass(frozen=True)
class Finding:
    """One scanner output, flattened to what scoring needs."""

    global_id: str
    ifc_type: str
    property: str
    document: str
    ifc_value: str
    document_value: str
    confidence: float
    title: str = ""

    @classmethod
    def from_conflict(cls, conflict) -> Finding:
        """Flatten a persisted ``Conflict`` row."""
        return cls(
            global_id=conflict.ifc_entity.global_id,
            ifc_type=conflict.ifc_entity.ifc_type,
            property=canonical_property(conflict.property_name or ""),
            document=_document_stem(conflict.document_chunk.document.name),
            ifc_value=conflict.ifc_value or "",
            document_value=conflict.document_value or "",
            confidence=float(conflict.confidence or 0.0),
            title=conflict.title or "",
        )

    def as_dict(self) -> dict:
        return {
            "global_id": self.global_id,
            "ifc_type": self.ifc_type,
            "property": self.property,
            "document": self.document,
            "ifc_value": self.ifc_value,
            "document_value": self.document_value,
            "confidence": round(self.confidence, 3),
            "title": self.title,
        }


@dataclass
class CaseScore:
    """Per-entity outcome of one key case."""

    case: KeyCase
    hits: list[str] = field(default_factory=list)  # GlobalIds correctly flagged
    misses: list[str] = field(default_factory=list)  # expected conflict, not flagged
    false_alarms: list[str] = field(default_factory=list)  # no_conflict case, flagged anyway

    @property
    def passed(self) -> bool:
        return not self.misses and not self.false_alarms

    def as_dict(self) -> dict:
        return {
            **self.case.as_dict(),
            "hits": self.hits,
            "misses": self.misses,
            "false_alarms": self.false_alarms,
            "passed": self.passed,
        }


@dataclass
class ScoreSheet:
    """Everything one run produced, scored."""

    case_scores: list[CaseScore]
    unmatched: list[Finding]  # findings that hit no key case at all
    findings: list[Finding]

    # ── Counts ────────────────────────────────────────────

    @property
    def true_positives(self) -> int:
        return sum(len(s.hits) for s in self.case_scores if s.case.is_conflict)

    @property
    def false_negatives(self) -> int:
        return sum(len(s.misses) for s in self.case_scores)

    @property
    def false_positives(self) -> int:
        """Flags on aligned requirements plus findings that match nothing."""
        return sum(len(s.false_alarms) for s in self.case_scores) + len(self.unmatched)

    @property
    def precision(self) -> float:
        denominator = self.true_positives + self.false_positives
        return self.true_positives / denominator if denominator else 0.0

    @property
    def recall(self) -> float:
        denominator = self.true_positives + self.false_negatives
        return self.true_positives / denominator if denominator else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def recall_by_severity(self) -> dict[str, tuple[int, int]]:
        """severity -> (hits, opportunities) over conflict cases."""
        table: dict[str, list[int]] = {}
        for score in self.case_scores:
            if not score.case.is_conflict:
                continue
            bucket = table.setdefault(score.case.severity, [0, 0])
            bucket[0] += len(score.hits)
            bucket[1] += len(score.case.global_ids)
        return {k: (v[0], v[1]) for k, v in table.items()}

    def negatives_held(self) -> tuple[int, int]:
        """(entities on no_conflict cases left alone, total such entities)."""
        negatives = [s for s in self.case_scores if not s.case.is_conflict]
        total = sum(len(s.case.global_ids) for s in negatives)
        alarms = sum(len(s.false_alarms) for s in negatives)
        return total - alarms, total

    def as_dict(self) -> dict:
        held, total = self.negatives_held()
        return {
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "recall_by_severity": {
                k: {"hits": h, "of": n} for k, (h, n) in self.recall_by_severity().items()
            },
            "negatives_held": held,
            "negatives_total": total,
            "cases": [s.as_dict() for s in self.case_scores],
            "unmatched_findings": [f.as_dict() for f in self.unmatched],
            "findings": [f.as_dict() for f in self.findings],
        }


def score_findings(
    corpus: RavCorpus, findings: list[Finding], *, match_document: bool = True
) -> ScoreSheet:
    """Match findings to key cases and tally hits, misses and false alarms.

    A single finding may satisfy at most one case; when a finding could match
    several (same entity, property and document), the first case in key order
    wins, so the key author controls the tie-break by ordering.

    ``match_document=False`` relaxes the source-document constraint — used as a
    secondary score isolating the scanner's chunk-misattribution problem.
    """
    scores = [CaseScore(case=c) for c in corpus.cases]
    consumed: set[int] = set()

    for score in scores:
        case = score.case
        flagged: set[str] = set()
        for index, finding in enumerate(findings):
            if index in consumed:
                continue
            if not _matches(case, finding, match_document=match_document):
                continue
            consumed.add(index)
            flagged.add(finding.global_id)

        if case.is_conflict:
            score.hits = sorted(flagged)
            score.misses = sorted(set(case.global_ids) - flagged)
        else:
            score.false_alarms = sorted(flagged)

    unmatched = [f for i, f in enumerate(findings) if i not in consumed]
    return ScoreSheet(case_scores=scores, unmatched=unmatched, findings=findings)


def _matches(case: KeyCase, finding: Finding, *, match_document: bool = True) -> bool:
    return (
        finding.global_id in case.global_ids
        and finding.property == case.property
        and (not match_document or finding.document == case.document)
    )


def _document_stem(name: str) -> str:
    """``fire-safety-strategy.pdf`` -> ``fire-safety-strategy``."""
    return name.rsplit(".", 1)[0] if "." in name else name


class RavRunner:
    """Run one scan with given settings and collect its findings.

    Only the ``Conflict`` rows attached to the scan's own ``ScanRun`` are
    collected, so leftovers from earlier scans never leak into a score. Rows the
    run created are deleted afterwards unless ``keep_conflicts`` is set — a
    benchmark project's Conflicts tab should not fill up with ablation noise.
    """

    def __init__(self, project, user=None, *, keep_conflicts: bool = False) -> None:
        self.project = project
        self.user = user
        self.keep_conflicts = keep_conflicts

    def run(self, corpus: RavCorpus, settings: ScanSettings) -> tuple[ScoreSheet, dict]:
        """Execute a full scan and score it. Returns (sheet, run_stats)."""
        from writeback.models import Conflict, ScanRun
        from writeback.services.conflict_scan_service import ConflictScanService
        from writeback.services.emitters import NullEmitter

        service = ConflictScanService(
            self.project,
            self.user,
            skip_low_value=settings.skip_low_value,
            confidence_threshold=settings.confidence_threshold,
            type_gate=settings.type_gate,
            keyword_filter=settings.keyword_filter,
            entity_relevance_threshold=settings.entity_relevance_threshold,
            entity_top_k=settings.entity_top_k,
        )

        started = time.perf_counter()
        stats = service.full_scan(emitter=NullEmitter())
        duration = time.perf_counter() - started

        scan_run = ScanRun.objects.filter(project=self.project).order_by("-created_at").first()
        conflicts = list(
            Conflict.objects.filter(scan_run=scan_run).select_related(
                "ifc_entity", "document_chunk__document"
            )
        )
        findings = [Finding.from_conflict(c) for c in conflicts]
        logger.info(
            "RAV scan [%s]: %d entities, %d findings in %.1fs",
            settings.label(),
            stats.get("entities_scanned", 0),
            len(findings),
            duration,
        )

        if not self.keep_conflicts:
            Conflict.objects.filter(scan_run=scan_run).delete()

        run_stats = {
            **stats,
            "duration_seconds": round(duration, 2),
            "scan_run_id": str(scan_run.id) if scan_run else "",
            "llm_model": scan_run.llm_model_used if scan_run else "",
        }
        return score_findings(corpus, findings), run_stats
