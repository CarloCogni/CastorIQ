# writeback/services/benchmark/__init__.py
"""Natural-language benchmark harness for the writeback pipeline (V3).

Runs a corpus of real user prompts through the real pipeline against a real
model, on a scratch copy of the sample house, and scores:

* **targets match** — did ``select`` return exactly the entities the corpus
  names (resolved through the index)? This is what decides the bake-off.
* **diff match** — does the measured diff contain the rows the corpus expects?
* **integrity** — nothing outside the selection changed, no geometry moved.
  The pipeline gates on this, so it is expected at 100%.
* **reject / no-change** — requests that must be declined, or already hold.

Nothing here writes to a real project file: the scratch copy the pipeline
kept is deleted after scoring, and no proposal row is created.
"""

from .corpus import BenchmarkCase, CorpusError, parse_corpus
from .report import BenchmarkReport, diff_runs, render_report
from .runner import BenchmarkRunner, CaseResult

__all__ = [
    "BenchmarkCase",
    "BenchmarkReport",
    "BenchmarkRunner",
    "CaseResult",
    "CorpusError",
    "diff_runs",
    "parse_corpus",
    "render_report",
]
