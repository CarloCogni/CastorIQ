# writeback/services/generator.py
"""Generate: the one model call that writes ``select`` and ``modify`` (spec C-1..C-4).

The prompt is instructions + grounding + helper signatures + api sheet. The
model answers with one fenced Python block; :func:`extract_code_block` pulls
it out and checks with ``ast`` that both functions are defined. The output is
never JSON: code inside a JSON string forces escaped newlines that small
models mangle.

There is one repair prompt for every failure kind: the previous code plus the
error string. The grounding is already in the prompt, so no failure kind
needs a special one.
"""

from __future__ import annotations

import ast
import inspect
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm import (
    BYOKAuthError,
    BYOKRateLimitError,
    LLMConfigurationError,
    LLMMasterKillError,
    TokenBudgetExceededError,
    get_llm,
    resolve_model_name,
    safe_invoke,
)
from ifc_processor.services import castor_select

from .api_sheet import API_SHEET
from .errors import ModelUnavailableError
from .grounding import Grounding

logger = logging.getLogger(__name__)

#: The one context-window constant (spec G-3); Ollama's default of 4k would
#: silently truncate the api sheet. Drops to 6144 if the bake-off shows the
#: 14B model spilling out of 12 GB at this size.
NUM_CTX = 8192
#: A select/modify block is a few dozen lines; this caps a runaway generation
#: (cloud providers receive it as max_tokens).
NUM_PREDICT = 1536
#: Hard wall-clock cap per model call. The streaming client timeout never
#: fires while tokens keep arriving, so a looping model could hold a worker
#: for half an hour; the bake-off saw exactly that once.
CALL_TIMEOUT_SECONDS = 240

REQUIRED_FUNCTIONS = frozenset({"select", "modify"})

#: Typed provider errors the views translate themselves (budget, BYOK, kill switch).
_PASSTHROUGH_ERRORS = (
    LLMConfigurationError,
    LLMMasterKillError,
    TokenBudgetExceededError,
    BYOKAuthError,
    BYOKRateLimitError,
)

SYSTEM_PROMPT = """\
You write Python for IfcOpenShell 0.8 that changes an IFC building model.

Answer with exactly one ```python block defining two functions and nothing else:

def select(model) -> list:
    # return the entities the request targets; read-only
def modify(model, targets) -> None:
    # change those entities and only those; never widen the selection

Rules:
- Use only names that appear in the model facts below: storey names, space names and
  property-set names are exact strings. Do not invent storeys, spaces or psets.
  A property listed as "not yet set" may be added with pset.edit_pset. Write the value to
  the property the request names, never to a different property with a similar meaning.
- Already in scope, no import needed: the helpers listed below, `ifcopenshell`,
  `ifcopenshell.api`, `element` (ifcopenshell.util.element) and the api modules of the
  reference (pset, root, spatial, aggregate, type, attribute, classification, material,
  group), called as written there with the model implied. Raw ifcopenshell is allowed when
  no helper fits.
- modify() may narrow `targets` (by type or name) but must never touch other entities.
- Never change geometry or placement. Never call model.write() or ifcopenshell.open().
- Set property values with pset.edit_pset; remove a property by setting it to None.
  Get the pset with pset.add_pset: it returns the existing pset or creates it. An existing
  pset may be shared with other entities: unshare it first, as in the example, or the edit
  changes every entity on it. Never edit a pset reached through the type object.
- To create: select() returns model.by_type("IfcProject"), or the storey a new space
  belongs to; modify() calls root.create_entity(ifc_class="<IfcClass>", name="<name>"),
  never model.create_entity. A new space goes under its storey with
  aggregate.assign_object(products=[space], relating_object=storey); elements go into a
  zone with group.assign_group(products=targets, group=zone); zones and groups are never
  placed in a storey. To delete, select() returns the entities: root.remove_product for
  elements and spaces, group.remove_group for zones and groups.
- Strings, numbers and booleans are plain Python values; IfcOpenShell wraps them.
- Keep the code short. No comments explaining the request, no prints, no return value from modify.

One example of the shape only (helpers plus raw api). Its names are placeholders: take the
real storey, type, pset, property and value from the request and the model facts.

```python
def select(model):
    items = by_type(elements_in_storey(model, "<storey name>"), "<IfcType>")
    return by_name(items, "<name substring>")

def modify(model, targets):
    for item in targets:
        own = pset.add_pset(product=item, name="<Pset name>")
        if len(element.get_elements_by_pset(own)) > 1:
            own = pset.unshare_pset(products=[item], pset=own)[0]
        pset.edit_pset(pset=own, properties={"<Property>": "<value>"})
```

Creating, deleting, moving between storeys, grouping, classifying and assigning materials are
all changes you can make. Answer with one line and no code ONLY when the request is a greeting,
a question, a change to geometry, size, position or shape, or names nothing to change or
create:

REJECT: <one short sentence saying why>
"""

_USER_TEMPLATE = """\
## Request
{request}

## Model facts (exact strings from the database)
{grounding}

## Helpers (already in scope; call them directly)
{helpers}

## ifcopenshell.api reference
{api_sheet}
"""

_REPAIR_TEMPLATE = """\
{user_prompt}
## Previous attempt
```python
{code}
```

## What went wrong
{error}

Return the complete corrected ```python block.
"""

_FENCE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_REJECT = re.compile(r"^\s*REJECT\s*:\s*(.+?)\s*$", re.MULTILINE)


def extract_rejection(text: str) -> str | None:
    """The reason on a ``REJECT: …`` line, when the model declined and wrote no code."""
    if not text or _FENCE.search(text):
        return None
    match = _REJECT.search(text)
    return match.group(1) if match else None


def render_helper_signatures() -> str:
    """One line per helper: signature plus the first docstring sentence."""
    lines = []
    for name in castor_select.__all__:
        fn = getattr(castor_select, name)
        doc = (inspect.getdoc(fn) or "").split("\n")[0]
        lines.append(f"{name}{inspect.signature(fn, eval_str=True)} — {doc}")
    return "\n".join(lines)


HELPER_SIGNATURES = render_helper_signatures()


def extract_code_block(text: str) -> str | None:
    """The first fenced block that defines both ``select`` and ``modify``, else None.

    A response with no fences at all is tried as-is, so a model that skips
    the fence but writes valid code still gets through.
    """
    if not text:
        return None
    candidates = _FENCE.findall(text) or [text]
    for candidate in candidates:
        code = candidate.strip()
        if _defines_both(code):
            return code
    return None


def _defines_both(code: str) -> bool:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    defined = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    return REQUIRED_FUNCTIONS <= defined


class CodeGenerator:
    """Wraps the Modify model for the generate and repair calls."""

    def __init__(self, user=None) -> None:
        self._user = user
        self._llm = None

    @property
    def model_name(self) -> str:
        return resolve_model_name(self._user, "modify")

    def generate(self, request: str, grounding: Grounding) -> str:
        """First attempt: return the raw model response."""
        return self._invoke(self._user_prompt(request, grounding))

    def repair(self, request: str, grounding: Grounding, code: str, error: str) -> str:
        """Repair attempt: the same prompt plus the previous code and one error string."""
        prompt = _REPAIR_TEMPLATE.format(
            user_prompt=self._user_prompt(request, grounding), code=code, error=error
        )
        return self._invoke(prompt)

    # ── Internals ──────────────────────────────────────────

    @staticmethod
    def _user_prompt(request: str, grounding: Grounding) -> str:
        return _USER_TEMPLATE.format(
            request=request.strip(),
            grounding=grounding.text,
            helpers=HELPER_SIGNATURES,
            api_sheet=API_SHEET,
        )

    def _invoke(self, prompt: str) -> str:
        """One model call. A timeout or an unreachable provider is a :class:`ModelUnavailableError`.

        The typed budget / BYOK / kill-switch errors pass through untouched:
        the views render those with their own wording.
        """
        if self._llm is None:
            self._llm = get_llm(
                user=self._user,
                purpose="modify",
                temperature=0.0,
                num_ctx=NUM_CTX,
                num_predict=NUM_PREDICT,
            )
        logger.info("Modify generation prompt: ~%d tokens", len(prompt) // 4)
        try:
            response = safe_invoke(
                self._llm.invoke,
                [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)],
                timeout=CALL_TIMEOUT_SECONDS,
            )
        except _PASSTHROUGH_ERRORS:
            raise
        except TimeoutError as e:
            raise ModelUnavailableError(
                f"The Modify model did not answer within {CALL_TIMEOUT_SECONDS} s. "
                "Try again, or shorten the request."
            ) from e
        except Exception as e:  # noqa: BLE001 — every transport / provider failure becomes one visible message
            logger.warning("Modify model call failed: %s: %s", type(e).__name__, e)
            raise ModelUnavailableError(
                f"The Modify model could not be reached: {type(e).__name__}: {e}"
            ) from e
        content = getattr(response, "content", response)
        return content if isinstance(content, str) else str(content)
