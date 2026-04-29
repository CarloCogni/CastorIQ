# facilities/services/_llm_helpers.py
"""Shared helpers for facilities LLM-driven services.

Two patterns surface in M3.E (FM Intent → WO) and M4.B (Occupant Portal):

* a hard wall-clock cap around ``llm.invoke`` because httpx's per-read
  timeout doesn't fire when Ollama dribbles tokens, and
* a `format_json=True` + `json.loads()` shape that expects a dict / object.

This module is the single home for those primitives. It must not import
domain models — it only depends on httpx + concurrent.futures.

Aligns with `feedback_ollama_hard_timeout`:
``client_kwargs.timeout`` is per-read; ``disable_streaming`` is a no-op for
``.invoke()``; only an external executor cap is a real wall-clock guarantee.
"""

from __future__ import annotations

import concurrent.futures
import logging

import httpx

logger = logging.getLogger(__name__)


def invoke_with_wallclock(llm, messages, timeout: float, thread_prefix: str) -> str:
    """Run ``llm.invoke`` in a worker thread with a hard wall-clock timeout.

    The leaked thread is daemonic and the in-flight Ollama request occupies
    one server slot until it returns — those costs are acceptable in
    exchange for a UI that never wedges.

    Raises :class:`httpx.ReadTimeout` on cap. Re-raises any other invoke
    failure unchanged so callers can pattern-match on the underlying error.
    """
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix=thread_prefix
    )
    try:
        future = executor.submit(llm.invoke, messages)
        try:
            response = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            raise httpx.ReadTimeout(f"Hard wall-clock timeout after {timeout:.0f}s") from exc
    finally:
        executor.shutdown(wait=False)
    content = getattr(response, "content", None)
    if content is None:
        raise ValueError("LLM response had no content attribute")
    return str(content)
