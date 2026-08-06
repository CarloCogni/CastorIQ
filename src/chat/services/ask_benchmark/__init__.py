# chat/services/ask_benchmark/__init__.py
"""Ask (RAG) pipeline benchmark: fixtures, question corpus, scoring, report.

Mirrors the writeback benchmark philosophy (``writeback/services/benchmark``):
nothing is mocked. Real fixtures are parsed by the real pipeline, real
questions run through ``RAGService.generate_answer`` against the live LLM,
and answers are scored against ground truth computed independently with
IfcOpenShell straight from the fixture file.

Two tiers of cases:

- Tier 1 (deterministic): counts, storey lists, schema version — facts with
  one right answer, checked exactly. These are the acceptance gate for the
  deterministic answer layer.
- Tier 2 (narrative): open questions scored loosely against ground-truth
  tokens (material names, space names) — they track retrieval quality.
"""

from chat.services.ask_benchmark.fixtures import FIXTURES, resolve_fixture
from chat.services.ask_benchmark.ground_truth import GroundTruth, compute_ground_truth
from chat.services.ask_benchmark.questions import CASES, AskCase
from chat.services.ask_benchmark.report import AskBenchmarkReport, render_report
from chat.services.ask_benchmark.scoring import CaseResult, score_case

__all__ = [
    "CASES",
    "FIXTURES",
    "AskBenchmarkReport",
    "AskCase",
    "CaseResult",
    "GroundTruth",
    "compute_ground_truth",
    "render_report",
    "resolve_fixture",
    "score_case",
]
