# castor/intelligence/service.py
"""ProjectIntelligenceService — natural-language Q&A over the project schedule.

Pipeline:
  1. Embed the user's question.
  2. Retrieve top-k tasks by cosine similarity (TaskEmbedding).
  3. Build a compact schedule context string.
  4. Call the LLM with a schedule-focused system prompt.
  5. Return {answer, tasks_cited, coverage, error}.
"""

from __future__ import annotations

import logging
from textwrap import dedent

from langchain_core.messages import HumanMessage
from pgvector.django import CosineDistance

from core.llm import cached_system, get_llm
from embeddings.services.embedding_service import EmbeddingService
from castor.intelligence.embedder import ScheduleEmbedder, _task_text
from castor.scheduling.models import TaskEmbedding

logger = logging.getLogger(__name__)

_TOP_K = 12
_MIN_COVERAGE = 5  # warn if fewer than this many tasks are embedded

_SYSTEM_PROMPT = dedent("""\
    You are Castor Schedule Intelligence, an AI assistant specialised in construction \
    project scheduling and 4D BIM analysis.

    You are given a set of schedule tasks retrieved from the project's task database. \
    Each task entry shows its name, stage, status, planned dates, actual progress, \
    and cost. Answer the user's question using ONLY the provided task context.

    Rules:
    1. Never invent data. If the context doesn't contain the answer, say so clearly.
    2. Cite task names when making specific claims (e.g. "Task: Foundation Excavation").
    3. Use precise construction terminology (critical path, float, SPI, EV, etc.).
    4. Format answers in clean Markdown. Use tables for comparisons.
    5. When asked about delays or risks, reason from status, actual dates, and progress.
    6. Keep answers concise — one to three paragraphs unless a table helps clarity.\
""")


def _context_block(tasks) -> str:
    """Format retrieved tasks into a numbered context string for the LLM."""
    lines = []
    for i, task in enumerate(tasks, 1):
        lines.append(f"[{i}] {_task_text(task)}")
        lines.append("")
    return "\n".join(lines)


class ProjectIntelligenceService:
    """Answer natural-language questions about a project's schedule.

    Usage::

        svc = ProjectIntelligenceService(project, request.user)
        result = svc.ask("Which tasks are delayed and by how much?")
        # result = {answer: str, tasks_cited: list[str], coverage: int, error: str|None}
    """

    def __init__(self, project, user) -> None:
        self.project = project
        self.user = user
        self._embed_svc = EmbeddingService()
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            self._llm = get_llm(user=self.user, purpose="ask", temperature=0.2)
        return self._llm

    def _ensure_coverage(self) -> int:
        """Return number of embedded tasks; trigger incremental embed if stale."""
        coverage = TaskEmbedding.objects.filter(task__project=self.project).count()
        if coverage < _MIN_COVERAGE:
            logger.info(
                "Intelligence: only %d embeddings for project %s — running embedder",
                coverage,
                self.project.pk,
            )
            result = ScheduleEmbedder().embed_project(str(self.project.pk))
            coverage = result.get("embedded", 0) + result.get("skipped", 0)
        return coverage

    def ask(self, question: str) -> dict:
        """Answer *question* about the project schedule.

        Returns::

            {
                "answer":      str,
                "tasks_cited": list[str],   # task names in the context
                "coverage":    int,          # total embedded tasks
                "error":       str | None,
            }
        """
        if not question or not question.strip():
            return {"answer": "", "tasks_cited": [], "coverage": 0, "error": "Empty question."}

        try:
            coverage = self._ensure_coverage()
        except Exception:
            logger.exception("ensure_coverage failed for project %s", self.project.pk)
            coverage = 0

        if coverage == 0:
            return {
                "answer": "No schedule tasks have been embedded yet. "
                "Please upload a schedule and run the embedding step first.",
                "tasks_cited": [],
                "coverage": 0,
                "error": None,
            }

        try:
            query_vector = self._embed_svc.embed_query(question)
        except Exception as exc:
            logger.exception("embed_query failed")
            return {"answer": "", "tasks_cited": [], "coverage": coverage, "error": str(exc)}

        embeddings = (
            TaskEmbedding.objects.filter(task__project=self.project)
            .annotate(distance=CosineDistance("vector", query_vector))
            .select_related("task")
            .order_by("distance")[:_TOP_K]
        )

        tasks = [e.task for e in embeddings]
        if not tasks:
            return {
                "answer": "I could not find any relevant tasks for that question.",
                "tasks_cited": [],
                "coverage": coverage,
                "error": None,
            }

        context = _context_block(tasks)
        human_text = f"Schedule context:\n\n{context}\n\nQuestion: {question}"

        try:
            llm = self._get_llm()
            system_msg = cached_system(llm, _SYSTEM_PROMPT)
            response = llm.invoke([system_msg, HumanMessage(content=human_text)])
            answer = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.exception("LLM invocation failed for project %s", self.project.pk)
            return {"answer": "", "tasks_cited": [], "coverage": coverage, "error": str(exc)}

        return {
            "answer": answer,
            "tasks_cited": [t.name for t in tasks],
            "coverage": coverage,
            "error": None,
        }
