# chat/services/ask_benchmark/fixtures.py
"""Sha256-addressed benchmark fixtures resolved from the engine_web-ifc clone.

The corpus files are real public IFC models committed in ThatOpen's
engine_web-ifc repository (``tests/ifcfiles/public/``). They are looked up in
the local clone at the repo root first, verified by hash, and downloaded from
GitHub into ``fixtures/ask_corpus/`` (gitignored — ``*.ifc`` is ignored
globally) only when the clone is absent. Nothing is ever committed.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 1 << 20  # 1 MiB
_RAW_BASE = "https://raw.githubusercontent.com/ThatOpen/engine_web-ifc/main/tests/ifcfiles/public"


@dataclass(frozen=True)
class FixtureSpec:
    """One benchmark model: filename, integrity hash, and rough size."""

    filename: str
    sha256: str
    size_bytes: int

    @property
    def url(self) -> str:
        return f"{_RAW_BASE}/{urllib.parse.quote(self.filename)}"


# Chosen for schema and discipline spread: IFC4 house, small IFC4 sample,
# IFC2X3 office, IFC2X3 residential duplex (the classic FM handover model).
FIXTURES: dict[str, FixtureSpec] = {
    "fzk-haus": FixtureSpec(
        filename="AC20-FZK-Haus.ifc",
        sha256="70cc8ff245fc0894201d96496c031005a5cbd7a96b22d8a1b87c5a883fb77994",
        size_bytes=2_570_803,
    ),
    "open-house": FixtureSpec(
        filename="IfcOpenHouse_IFC4.ifc",
        sha256="6f79a3ded398d19220723897f4b7ea4bd3d8f2a586f7df5253a716e1722a0e2c",
        size_bytes=116_158,
    ),
    "office-a": FixtureSpec(
        filename="Office_A_20110811.ifc",
        sha256="3ccc7df480d7ae9d1af148d13634fe1af8f1a75949fdf3cb1f87c60464df4700",
        size_bytes=4_099_307,
    ),
    "duplex": FixtureSpec(
        filename="duplex.ifc",
        sha256="8355749520aa37e6b7596870b2ea92bc534a0f144c016d197c89946e19f7da34",
        size_bytes=2_419_670,
    ),
}


def _repo_root() -> Path:
    # BASE_DIR is <repo>/src; the clone and the corpus cache live at the root.
    return Path(settings.BASE_DIR).parent


def _clone_path(spec: FixtureSpec) -> Path:
    return _repo_root() / "engine_web-ifc" / "tests" / "ifcfiles" / "public" / spec.filename


def _cache_path(spec: FixtureSpec) -> Path:
    return _repo_root() / "fixtures" / "ask_corpus" / spec.filename


def sha256_of(path: Path) -> str:
    """Hex sha256 of a file, streamed in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _download(spec: FixtureSpec, dest: Path) -> bool:
    """Stream the fixture from GitHub to dest via a .part file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(spec.url) as response, part.open("wb") as out:
            while chunk := response.read(_CHUNK_SIZE):
                out.write(chunk)
    except urllib.error.URLError as exc:
        logger.warning("Fixture download failed for %s: %s", spec.url, exc)
        part.unlink(missing_ok=True)
        return False
    part.replace(dest)
    return True


def resolve_fixture(name: str) -> Path:
    """Return a verified local path for a fixture, downloading if needed.

    Resolution order: local engine_web-ifc clone, then the gitignored
    download cache, then a fresh download. Every path is hash-verified
    before it is returned.

    Raises:
        KeyError: Unknown fixture name.
        FileNotFoundError: No source produced a file with the expected hash.
    """
    spec = FIXTURES[name]

    for candidate in (_clone_path(spec), _cache_path(spec)):
        if candidate.exists():
            if sha256_of(candidate) == spec.sha256:
                return candidate
            logger.warning("Hash mismatch for %s — ignoring this copy", candidate)

    cache = _cache_path(spec)
    if _download(spec, cache) and sha256_of(cache) == spec.sha256:
        return cache
    cache.unlink(missing_ok=True)

    raise FileNotFoundError(
        f"Fixture {name!r} ({spec.filename}) unavailable: not in the engine_web-ifc "
        f"clone and download from {spec.url} failed or did not match sha256."
    )
