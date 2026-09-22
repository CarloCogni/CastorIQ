# benchmarks/ifc_parser/fetch_fixtures.py
"""Download the benchmark IFC corpus from the ifc-lite fixture manifest.

Reads ifc-lite/tests/models/manifest.json (GitHub release assets, per-file
sha256) and downloads the matching files into a local, gitignored corpus
directory. Idempotent: files already present with a matching hash are skipped.

Usage:
    uv run python benchmarks/ifc_parser/fetch_fixtures.py \
        [--manifest ifc-lite/tests/models/manifest.json] \
        [--dest fixtures/parser_corpus] \
        [--only "ara3d/*"] [--skip-larger-than 100]
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import logging
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "ifc-lite" / "tests" / "models" / "manifest.json"
DEFAULT_DEST = REPO_ROOT / "fixtures" / "parser_corpus"
DEFAULT_FILTER = "ara3d/*"
CHUNK_SIZE = 1 << 20  # 1 MiB


def sha256_of(path: Path) -> str:
    """Return the hex sha256 digest of a file, streamed in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def candidate_urls(base_url: str, entry: dict) -> list[str]:
    """Return download URL candidates for a manifest entry.

    ifc-lite release assets are content-addressed: the asset name IS the
    file's sha256 (see ifc-lite/scripts/fixtures/fetch-fixtures.mjs). Fall
    back to the quoted basename in case a future release uses plain names.
    """
    basename = entry["path"].rsplit("/", 1)[-1]
    return [
        f"{base_url}/{entry['sha256']}",
        f"{base_url}/{urllib.parse.quote(basename)}",
    ]


def download(url: str, dest: Path) -> bool:
    """Stream a URL to dest via a .part temp file. Returns False on HTTP error."""
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url) as response, part.open("wb") as out:
            while chunk := response.read(CHUNK_SIZE):
                out.write(chunk)
    except urllib.error.URLError as exc:
        logger.debug("Download failed for %s: %s", url, exc)
        part.unlink(missing_ok=True)
        return False
    part.replace(dest)
    return True


def select_entries(
    manifest: dict,
    only: str,
    skip_larger_than_mb: float | None,
) -> list[dict]:
    """Filter manifest entries by glob pattern, extension, and size cap."""
    selected: list[dict] = []
    for entry in manifest["files"]:
        rel_path = entry["path"]
        if not rel_path.lower().endswith(".ifc"):
            continue  # .ifcx / .ifczip are out of scope for ifcopenshell
        if not fnmatch.fnmatch(rel_path.lower(), only.lower()):
            continue
        size_mb = entry["size"] / 1e6
        if skip_larger_than_mb is not None and size_mb > skip_larger_than_mb:
            logger.info(
                "Skipping %s (%.0f MB > %.0f MB cap)", rel_path, size_mb, skip_larger_than_mb
            )
            continue
        selected.append(entry)
    return selected


def fetch_corpus(
    manifest_path: Path,
    dest_dir: Path,
    only: str = DEFAULT_FILTER,
    skip_larger_than_mb: float | None = 100.0,
) -> int:
    """Download all matching corpus files. Returns the number of failures."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base_url = manifest["base_url"].rstrip("/")
    entries = select_entries(manifest, only, skip_larger_than_mb)
    if not entries:
        logger.warning("No manifest entries match filter %r", only)
        return 1

    dest_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for entry in entries:
        rel_path: str = entry["path"]
        dest = dest_dir / rel_path.rsplit("/", 1)[-1]
        if dest.exists() and sha256_of(dest) == entry["sha256"]:
            logger.info("OK (cached)  %s", dest.name)
            continue

        logger.info("Downloading  %s (%.1f MB)", rel_path, entry["size"] / 1e6)
        if not any(download(url, dest) for url in candidate_urls(base_url, entry)):
            logger.error("FAILED       %s — no candidate URL worked", rel_path)
            failures += 1
            continue

        actual = sha256_of(dest)
        if actual != entry["sha256"]:
            logger.error("FAILED       %s — sha256 mismatch (got %s)", rel_path, actual[:12])
            dest.unlink(missing_ok=True)
            failures += 1
            continue
        logger.info("OK           %s", dest.name)
    return failures


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Fetch the benchmark IFC corpus.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument(
        "--only", default=DEFAULT_FILTER, help="Glob filter on manifest paths (default: ara3d/*)"
    )
    parser.add_argument(
        "--skip-larger-than",
        type=float,
        default=100.0,
        metavar="MB",
        help="Skip files larger than this many MB (default 100; pass 0 to disable)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    if not args.manifest.exists():
        logger.error(
            "Manifest not found: %s (is the ifc-lite submodule checked out?)", args.manifest
        )
        return 1

    cap = args.skip_larger_than or None
    failures = fetch_corpus(args.manifest, args.dest, args.only, cap)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
