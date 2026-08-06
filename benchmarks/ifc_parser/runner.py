# benchmarks/ifc_parser/runner.py
"""Benchmark orchestration: iterations, subprocess timeouts, aggregation.

Each iteration runs worker.py in a fresh subprocess (cold caches; a real
wall-clock timeout via subprocess.run(timeout=...) — SIGALRM is a no-op
on Windows). A file that times out or crashes is recorded as a failure and
the run continues.
"""

from __future__ import annotations

import json
import logging
import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

WORKER_PATH = Path(__file__).resolve().parent / "worker.py"

# Relative spread (max-min)/median on t_execute_all_ms above which a file
# is flagged as high-variance (noisy machine, JIT/disk-cache effects).
VARIANCE_THRESHOLD = 0.20

TIMING_KEYS = ("t_open_ms", "t_extract_ms", "t_geom_ms", "t_execute_all_ms")


@dataclass
class FileResult:
    """Aggregated benchmark result for one corpus file."""

    path: Path
    size_mb: float
    iterations_ok: int = 0
    iterations_requested: int = 0
    entity_count: int = 0
    mesh_count: int = 0
    extracted_entity_count: int = 0
    schema: str = ""
    medians: dict[str, float] = field(default_factory=dict)
    means: dict[str, float] = field(default_factory=dict)
    minima: dict[str, float] = field(default_factory=dict)
    maxima: dict[str, float] = field(default_factory=dict)
    high_variance: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True if at least one iteration produced full measurements."""
        return self.iterations_ok > 0


def run_worker_once(
    ifc_path: Path,
    timeout_s: float,
    no_geom: bool,
    no_castor_extract: bool,
    threads: int | None,
) -> dict:
    """Run one worker subprocess; return its JSON payload or a failure dict."""
    cmd = [sys.executable, str(WORKER_PATH), str(ifc_path)]
    if no_geom:
        cmd.append("--no-geom")
    if no_castor_extract:
        cmd.append("--no-castor-extract")
    if threads:
        cmd += ["--threads", str(threads)]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_s,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "stage": "timeout", "error": f"timeout after {timeout_s:.0f}s"}

    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if lines:
        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError:
            pass
    stderr_tail = (proc.stderr or "").strip()[-500:]
    return {
        "ok": False,
        "stage": "crash",
        "error": f"exit code {proc.returncode}; stderr: {stderr_tail or '(empty)'}",
    }


def _aggregate(result: FileResult, samples: list[dict]) -> None:
    """Fill median/mean/min/max, counts, and the variance flag from samples."""
    for key in TIMING_KEYS:
        values = [s[key] for s in samples]
        result.medians[key] = statistics.median(values)
        result.means[key] = statistics.fmean(values)
        result.minima[key] = min(values)
        result.maxima[key] = max(values)

    exec_median = result.medians["t_execute_all_ms"]
    if len(samples) > 1 and exec_median > 0:
        spread = (
            result.maxima["t_execute_all_ms"] - result.minima["t_execute_all_ms"]
        ) / exec_median
        result.high_variance = spread > VARIANCE_THRESHOLD

    result.entity_count = samples[0]["entity_count"]
    result.mesh_count = samples[0].get("mesh_count", 0)
    result.extracted_entity_count = samples[0].get("extracted_entity_count", 0)
    result.schema = samples[0].get("schema", "")

    # Counts are deterministic — divergence across iterations is a correctness bug.
    if len({s["entity_count"] for s in samples}) > 1:
        result.errors.append("UNSTABLE_ENTITY_COUNT across iterations")
    if len({s.get("mesh_count", 0) for s in samples}) > 1:
        result.errors.append("UNSTABLE_MESH_COUNT across iterations")

    warnings = {w for s in samples for w in s.get("warnings", [])}
    if warnings:
        preview = "; ".join(sorted(warnings)[:3])
        result.errors.append(f"{len(warnings)} warning(s): {preview}")


def benchmark_one_file(
    ifc_path: Path,
    iterations: int,
    timeout_s: float,
    no_geom: bool = False,
    no_castor_extract: bool = False,
    threads: int | None = None,
) -> FileResult:
    """Run all iterations for one file and aggregate the results."""
    result = FileResult(
        path=ifc_path,
        size_mb=ifc_path.stat().st_size / 1e6,
        iterations_requested=iterations,
    )
    samples: list[dict] = []
    for i in range(iterations):
        logger.info("  iteration %d/%d ...", i + 1, iterations)
        payload = run_worker_once(ifc_path, timeout_s, no_geom, no_castor_extract, threads)
        if payload.get("ok"):
            samples.append(payload)
            continue

        stage = payload.get("stage", "?")
        result.errors.append(f"{stage}: {payload.get('error', 'unknown error')}")
        if stage == "timeout":
            logger.warning("  timed out — skipping remaining iterations for this file")
            break

    result.iterations_ok = len(samples)
    if samples:
        _aggregate(result, samples)
    return result


def run_benchmark(
    corpus_files: list[Path],
    iterations: int,
    timeout_s: float,
    no_geom: bool = False,
    no_castor_extract: bool = False,
    threads: int | None = None,
) -> list[FileResult]:
    """Benchmark every corpus file, smallest first, never halting on failure."""
    ordered = sorted(corpus_files, key=lambda p: p.stat().st_size)
    results: list[FileResult] = []
    for index, path in enumerate(ordered, start=1):
        logger.info(
            "[%d/%d] %s (%.1f MB)", index, len(ordered), path.name, path.stat().st_size / 1e6
        )
        results.append(
            benchmark_one_file(path, iterations, timeout_s, no_geom, no_castor_extract, threads)
        )
    return results
