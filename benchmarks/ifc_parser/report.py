# benchmarks/ifc_parser/report.py
"""Write benchmark results as results.md (web-ifc-diffable) and results.csv."""

from __future__ import annotations

import csv
import json
import logging
import os
import platform
from pathlib import Path

from reference import ReferenceCounts, cross_check
from runner import FileResult

logger = logging.getLogger(__name__)

MD_HEADER = (
    "| filename | Size (mb) | Time to open model (ms) | Time to execute all (ms) "
    "| Total ifc entities | Total mesh objects | Errors |"
)

CSV_COLUMNS = [
    "filename",
    "size_mb",
    "t_open_median_ms",
    "t_open_mean_ms",
    "t_extract_median_ms",
    "t_geom_median_ms",
    "t_execute_all_median_ms",
    "t_execute_all_mean_ms",
    "t_execute_all_min_ms",
    "t_execute_all_max_ms",
    "high_variance",
    "total_ifc_entities",
    "total_mesh_objects",
    "extracted_entity_count",
    "ref_entities",
    "ref_meshes",
    "entity_delta",
    "mesh_delta_pct",
    "iterations_ok",
    "schema",
    "errors",
]


def _system_info_line() -> str:
    """One-line system description, mirroring benchmark.md's header."""
    info = {
        "cpuName": platform.processor() or platform.machine(),
        "cores": os.cpu_count(),
        "platform": platform.platform(),
        "python": platform.python_version(),
    }
    return f"# System informations \n {json.dumps(info)}\n _________ "


def _error_cell(result: FileResult, ref_notes: list[str]) -> str:
    """Assemble the Errors column: run failures, cross-check notes, variance."""
    parts = result.errors + ref_notes
    if result.high_variance:
        parts.append("HIGH_VARIANCE †")
    return "; ".join(parts)


def write_markdown(
    results: list[FileResult],
    reference: dict[str, ReferenceCounts],
    output_path: Path,
) -> None:
    """Write results.md with the exact 7-column web-ifc-style table."""
    lines = [
        _system_info_line(),
        MD_HEADER,
        "|-------|-------|-------|-------|-------|-------|-------|",
    ]
    total_open = 0.0
    total_all = 0.0
    for result in results:
        notes, _ = cross_check(result.path.name, result.entity_count, result.mesh_count, reference)
        open_ms = result.medians.get("t_open_ms", 0.0)
        all_ms = result.medians.get("t_execute_all_ms", 0.0)
        total_open += open_ms
        total_all += all_ms
        lines.append(
            f"| {result.path.name} | {result.size_mb:.0f} | {open_ms:.0f} | {all_ms:.0f} "
            f"| {result.entity_count} | {result.mesh_count} | {_error_cell(result, notes)} |"
        )
    lines += [
        "#Totals",
        f"*Total Time to Open*:{total_open:.0f}",
        f"*Total Time*:{total_all:.0f}",
        "",
        "† HIGH_VARIANCE: spread across iterations exceeded 20% of the median — treat timings as noisy.",
        "MESH_DELTA notes are informational: mesh counts are not expected to match web-ifc exactly "
        "(different tessellation engines). ENTITY_MISMATCH indicates a potential correctness bug.",
    ]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Wrote %s", output_path)


def write_csv(
    results: list[FileResult],
    reference: dict[str, ReferenceCounts],
    output_path: Path,
) -> None:
    """Write results.csv with full per-file statistics."""
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for result in results:
            notes, ref = cross_check(
                result.path.name, result.entity_count, result.mesh_count, reference
            )
            row: dict[str, object] = {
                "filename": result.path.name,
                "size_mb": round(result.size_mb, 2),
                "high_variance": result.high_variance,
                "total_ifc_entities": result.entity_count,
                "total_mesh_objects": result.mesh_count,
                "extracted_entity_count": result.extracted_entity_count,
                "iterations_ok": result.iterations_ok,
                "schema": result.schema,
                "errors": "; ".join(result.errors + notes),
            }
            for key, prefix in (
                ("t_open_ms", "t_open"),
                ("t_extract_ms", "t_extract"),
                ("t_geom_ms", "t_geom"),
                ("t_execute_all_ms", "t_execute_all"),
            ):
                row[f"{prefix}_median_ms"] = round(result.medians.get(key, 0.0), 1)
            row["t_open_mean_ms"] = round(result.means.get("t_open_ms", 0.0), 1)
            row["t_execute_all_mean_ms"] = round(result.means.get("t_execute_all_ms", 0.0), 1)
            row["t_execute_all_min_ms"] = round(result.minima.get("t_execute_all_ms", 0.0), 1)
            row["t_execute_all_max_ms"] = round(result.maxima.get("t_execute_all_ms", 0.0), 1)
            if ref is not None:
                row["ref_entities"] = ref.entities
                row["ref_meshes"] = ref.meshes
                row["entity_delta"] = result.entity_count - ref.entities
                if ref.meshes:
                    row["mesh_delta_pct"] = round(
                        100 * (result.mesh_count - ref.meshes) / ref.meshes, 1
                    )
            writer.writerow({col: row.get(col, "") for col in CSV_COLUMNS})
    logger.info("Wrote %s", output_path)
