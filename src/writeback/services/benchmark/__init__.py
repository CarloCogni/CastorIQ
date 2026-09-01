# writeback/services/benchmark/__init__.py
"""Natural-language benchmark harness for the writeback pipeline.

Runs a corpus of real user prompts through the real pipeline against a real
model, executes the resulting journals against a scratch copy of an IFC file,
and scores two independent things:

* **understanding** — did the pipeline route the request the way the corpus
  says it should? This is what varies between models.
* **fidelity** — did the journal it produced actually land in the file? This
  is model-independent and should stay at 100%; a drop means a writer or
  executor bug, not a comprehension failure.
* **integrity** — did the file change *only* where the journal said? Entity
  population, geometry and every bystander property are diffed against the
  untouched source (``ifc_processor.services.ifc_diff``). Also model-independent
  and expected at 100%.

Nothing here writes to a real project file: every case executes against a
throwaway copy inside a temporary directory.
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
