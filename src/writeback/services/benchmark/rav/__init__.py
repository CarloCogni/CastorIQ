# writeback/services/benchmark/rav/__init__.py
"""RAV benchmark harness: precision and recall of the conflict scanner.

Runs ``ConflictScanService`` against a project whose documents are the planted
corpus under ``fixtures/benchmark/rav/`` and scores every finding against the
ground-truth key. Each key case is one (document requirement, entity set,
property) triple labelled ``conflict`` or ``no_conflict`` and, for conflicts,
a severity class — ``clear`` (EI30 vs EI60), ``marginal`` (0.117 vs 0.10) or
``missing`` (property absent).

Scored per entity, not per case: a case targeting three walls is three
opportunities for a hit or a miss. Precision, recall and F1 are reported
overall and per severity, which is what makes the mitigation ablation
(``--no-type-gate``, ``--no-keyword-filter``, ``--confidence``) readable.

The three tests every RAV claim has to survive:

* **recall by severity** — is the scanner blind to marginal numeric mismatches?
* **precision on aligned requirements** — does it flag things that already match?
* **the ablation table** — what does each mitigation actually buy?
"""

from .corpus import KeyCase, RavCorpus, RavCorpusError, load_key
from .report import RavReport, diff_rav_runs, render_rav_report
from .runner import RavRunner, ScanSettings, score_findings

__all__ = [
    "KeyCase",
    "RavCorpus",
    "RavCorpusError",
    "RavReport",
    "RavRunner",
    "ScanSettings",
    "diff_rav_runs",
    "load_key",
    "render_rav_report",
    "score_findings",
]
