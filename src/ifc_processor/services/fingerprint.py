# ifc_processor/services/fingerprint.py
"""File fingerprint for optimistic concurrency on IFC files.

A proposal pins the SHA-256 of the file it was built against; approval
recomputes it and refuses to apply when the bytes moved underneath. Pure
library code — no Django — so the sandbox child and the benchmark can use it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1024 * 1024


def compute_fingerprint(ifc_path: str | Path) -> str:
    """SHA-256 of the file's bytes, read in 1 MiB chunks."""
    digest = hashlib.sha256()
    with open(ifc_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()
