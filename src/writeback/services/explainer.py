# writeback/services/explainer.py
"""The blind explanation (spec V-4): one sentence from the code and the diff.

The model never sees the request, so it cannot parrot it; the user compares
the sentence with what they asked. Wrapped so it can never block a proposal.
The Modify model writes it; if the bake-off shows the coder's prose is poor,
the ``purpose`` in :func:`explain` is pointed at the Ask model — one line, no
setting.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm import get_llm, resolve_model_name, safe_invoke

from .generator import NUM_CTX
from .verifier import DiffRow

logger = logging.getLogger(__name__)

#: What the card and the chat show when the explanation is empty. The stored
#: explanation stays empty so the commit subject falls back to the request.
UNAVAILABLE = "Explanation unavailable."
#: One sentence; caps a runaway generation. Cloud providers receive it as max_tokens.
NUM_PREDICT = 160
CALL_TIMEOUT_SECONDS = 90

SYSTEM_PROMPT = """\
You are shown Python code that was run against an IFC building model, the entities it
selected, and the measured before/after difference. Write ONE plain-English sentence
stating what the change did: the property or attribute, the value, and how many entities
of which type. Say only what the diff shows. Do not guess why. Do not mention code or Python.
"""

_USER_TEMPLATE = """\
## Code
```python
{code}
```

## Selected entities
{targets}

## Measured diff
{rows}

One sentence:
"""


@dataclass(frozen=True)
class Explanation:
    text: str
    model: str


def explain(code: str, rows: list[DiffRow], target_summary: str, user=None) -> Explanation:
    """One sentence on what the change did, never raising; empty text when the model failed.

    The call carries the same ``num_ctx`` as the code call so Ollama keeps the
    one loaded runner instead of reloading the model between the two calls.
    """
    model = _model_name(user)
    try:
        llm = get_llm(
            user=user, purpose="modify", temperature=0.0, num_ctx=NUM_CTX, num_predict=NUM_PREDICT
        )
        prompt = _USER_TEMPLATE.format(code=code, targets=target_summary, rows=render_rows(rows))
        response = safe_invoke(
            llm.invoke,
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)],
            timeout=CALL_TIMEOUT_SECONDS,
        )
        return Explanation(_one_sentence(getattr(response, "content", "")), model)
    except Exception as e:  # noqa: BLE001 — advisory, must never block the card
        logger.warning("Blind explanation failed (non-blocking): %s", e)
        return Explanation("", model)


def render_rows(rows: list[DiffRow]) -> str:
    """The aggregated diff as text lines for a prompt or a log."""
    lines = []
    for row in rows:
        noun = "entity" if row.count == 1 else "entities"
        if row.kind == "added":
            lines.append(f"- entity added: {row.after}")
        elif row.kind == "removed":
            lines.append(f"- entity removed: {row.before}")
        elif row.pset and not row.prop:
            verb = "added" if row.after else "removed"
            lines.append(f"- property set {row.pset} {verb} on {row.count} {noun}")
        else:
            lines.append(f"- {row.label}: {row.before!r} → {row.after!r} on {row.count} {noun}")
    return "\n".join(lines) or "- (no change)"


# ── Internals ──────────────────────────────────────────────────────


def _model_name(user) -> str:
    try:
        return resolve_model_name(user, "modify")
    except Exception:  # noqa: BLE001
        return "unknown"


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _one_sentence(content) -> str:
    """Whitespace-normalised, unquoted, cut after the first sentence boundary."""
    text = content if isinstance(content, str) else str(content)
    text = " ".join(text.strip().split()).strip("`\"' ")
    return _SENTENCE_END.split(text, maxsplit=1)[0]
