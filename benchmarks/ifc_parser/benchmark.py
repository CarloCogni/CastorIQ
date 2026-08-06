# benchmarks/ifc_parser/benchmark.py
"""CLI entry point for the IFC parser benchmark harness.

Usage:
    uv run python benchmarks/ifc_parser/benchmark.py \
        [--corpus fixtures/parser_corpus] [--output runs/ifc_parser_bench] \
        [--iterations 3] [--timeout 60] [--skip-larger-than 100] \
        [--only "AC20*"] [--no-geom] [--no-castor-extract] [--threads N] \
        [--reference engine_web-ifc/benchmark.md]

Exit code 0 when the run completes (per-file failures are recorded in the
results table); 1 only on harness-level errors (empty corpus, bad paths).
"""

from __future__ import annotations

import argparse
import fnmatch
import logging
import sys
from pathlib import Path

from reference import DEFAULT_REFERENCE, load_reference
from report import write_csv, write_markdown
from runner import run_benchmark

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = REPO_ROOT / "fixtures" / "parser_corpus"
DEFAULT_OUTPUT = REPO_ROOT / "runs" / "ifc_parser_bench"


def collect_corpus(
    corpus_dir: Path, only: str | None, skip_larger_than_mb: float | None
) -> list[Path]:
    """Glob *.ifc files in the corpus dir, applying name and size filters."""
    files = [p for p in corpus_dir.glob("*.ifc") if p.is_file()]
    if only:
        files = [p for p in files if fnmatch.fnmatch(p.name.lower(), only.lower())]
    if skip_larger_than_mb is not None:
        kept = []
        for path in files:
            size_mb = path.stat().st_size / 1e6
            if size_mb > skip_larger_than_mb:
                logger.info(
                    "Skipping %s (%.0f MB > %.0f MB cap)", path.name, size_mb, skip_larger_than_mb
                )
                continue
            kept.append(path)
        files = kept
    return files


def main() -> int:
    """Run the full benchmark and write results.md + results.csv."""
    parser = argparse.ArgumentParser(description="Benchmark Castor's IFC parsing pipeline.")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--iterations", type=int, default=3, choices=range(1, 11), metavar="N")
    parser.add_argument("--timeout", type=float, default=60.0, metavar="SECONDS")
    parser.add_argument(
        "--skip-larger-than", type=float, default=100.0, metavar="MB", help="0 disables the cap"
    )
    parser.add_argument("--only", default=None, help='Glob on filenames, e.g. "AC20*"')
    parser.add_argument("--no-geom", action="store_true", help="Skip geometry tessellation")
    parser.add_argument(
        "--no-castor-extract", action="store_true", help="Skip Castor extraction (no Django needed)"
    )
    parser.add_argument("--threads", type=int, default=None, help="Geometry iterator threads")
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    if not args.corpus.is_dir():
        logger.error("Corpus directory not found: %s (run fetch_fixtures.py first)", args.corpus)
        return 1
    corpus = collect_corpus(args.corpus, args.only, args.skip_larger_than or None)
    if not corpus:
        logger.error("No .ifc files matched in %s", args.corpus)
        return 1

    reference = load_reference(args.reference)
    results = run_benchmark(
        corpus,
        iterations=args.iterations,
        timeout_s=args.timeout,
        no_geom=args.no_geom,
        no_castor_extract=args.no_castor_extract,
        threads=args.threads,
    )

    args.output.mkdir(parents=True, exist_ok=True)
    write_markdown(results, reference, args.output / "results.md")
    write_csv(results, reference, args.output / "results.csv")

    failed = [r for r in results if not r.ok]
    logger.info("Done: %d file(s), %d failed", len(results), len(failed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
