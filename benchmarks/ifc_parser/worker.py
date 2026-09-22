# benchmarks/ifc_parser/worker.py
"""Benchmark worker: one IFC file, one cold iteration, JSON result on stdout.

Run as a subprocess by runner.py so each iteration gets a cold process
(honest cold-start timing) and a real wall-clock timeout on Windows.

Phases:
    open     — ifcopenshell.open()                      → "Time to open model"
    extract  — Castor IFCParser property/description extraction (no DB)
    geom     — ifcopenshell.geom iterator drain          → mesh count
    execute_all = open + extract + geom                  → "Time to execute all"

The LAST line of stdout is exactly one JSON object; all logging goes to
stderr. Each phase is independently guarded so a geometry crash still
reports the open/extract numbers.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"


def _setup_django() -> None:
    """Initialize Django so Castor's parser module can be imported.

    Connections are lazy, so no database needs to be running — we only
    import model classes, never query them.
    """
    import django

    sys.path.insert(0, str(SRC_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    django.setup()


def _safe_set(settings: Any, key: str, value: Any) -> None:
    """Set an ifcopenshell.geom setting across 0.7.x/0.8.x name drift.

    Mirrors src/facilities/services/explore_plan_generator.py::_safe_set.
    """
    try:
        settings.set(key, value)
        return
    except Exception:
        pass
    constant = getattr(settings, key.replace("-", "_").upper(), None)
    if constant is not None:
        try:
            settings.set(constant, value)
        except Exception:
            logger.debug("Could not set geom setting %s", key)


def run_open(path: str) -> tuple[Any, float]:
    """Open the IFC file, returning (model, elapsed_ms)."""
    import ifcopenshell

    start = time.perf_counter()
    model = ifcopenshell.open(path)
    return model, (time.perf_counter() - start) * 1000


def count_entities(model: Any) -> int:
    """Total STEP instances in the model (web-ifc's 'Total ifc entities')."""
    try:
        return len(model)
    except TypeError:
        return sum(1 for _ in model)


def run_castor_extract(model: Any) -> tuple[int, float, list[str]]:
    """Run Castor's per-element extraction (properties, container, description).

    Uses the real IFCParser methods on a DB-less instance — no ORM writes.
    Returns (extracted_entity_count, elapsed_ms, warnings).
    """
    import ifcopenshell.util.element as element_util

    from ifc_processor.services.parser import IFCParser

    parser = IFCParser(ifc_file=None)
    warnings: list[str] = []
    extracted = 0
    start = time.perf_counter()
    for ifc_type in parser.RELEVANT_TYPES:
        try:
            elements = model.by_type(ifc_type)
        except Exception:
            continue  # type not in this schema version
        for element in elements:
            try:
                element_type = element_util.get_type(element)
                properties = parser._get_properties(element, element_type)
                parser._get_container_gid(element)
                parser._generate_description(element, properties)
                extracted += 1
            except Exception as exc:
                gid = getattr(element, "GlobalId", "?")
                warnings.append(f"extract {ifc_type} #{gid}: {exc}")
    return extracted, (time.perf_counter() - start) * 1000, warnings


def run_geometry(model: Any, threads: int) -> tuple[int, float, list[str]]:
    """Tessellate all products via the geom iterator.

    Returns (mesh_count, elapsed_ms, warnings).
    """
    import ifcopenshell.geom

    settings = ifcopenshell.geom.settings()
    _safe_set(settings, "use-world-coords", True)
    _safe_set(settings, "weld-vertices", True)

    warnings: list[str] = []
    start = time.perf_counter()
    iterator = ifcopenshell.geom.iterator(settings, model, threads)
    meshes = 0
    if iterator.initialize():
        while True:
            try:
                shape = iterator.get()
                if shape is not None:
                    meshes += 1
            except Exception as exc:
                warnings.append(f"geom shape: {exc}")
            if not iterator.next():
                break
    return meshes, (time.perf_counter() - start) * 1000, warnings


def benchmark_file(path: str, do_extract: bool, do_geom: bool, threads: int) -> dict:
    """Run all phases on one file and assemble the result payload."""
    result: dict[str, Any] = {"ok": True, "warnings": []}

    try:
        model, t_open = run_open(path)
    except Exception as exc:
        return {"ok": False, "stage": "open", "error": f"{type(exc).__name__}: {exc}"}
    result["t_open_ms"] = t_open
    result["entity_count"] = count_entities(model)
    result["schema"] = getattr(model, "schema", "?")

    result["t_extract_ms"] = 0.0
    result["extracted_entity_count"] = 0
    if do_extract:
        try:
            extracted, t_extract, warnings = run_castor_extract(model)
            result["t_extract_ms"] = t_extract
            result["extracted_entity_count"] = extracted
            result["warnings"] += warnings
        except Exception as exc:
            result["ok"] = False
            result["stage"] = "extract"
            result["error"] = f"{type(exc).__name__}: {exc}"
            return result

    result["t_geom_ms"] = 0.0
    result["mesh_count"] = 0
    if do_geom:
        try:
            meshes, t_geom, warnings = run_geometry(model, threads)
            result["t_geom_ms"] = t_geom
            result["mesh_count"] = meshes
            result["warnings"] += warnings
        except Exception as exc:
            result["ok"] = False
            result["stage"] = "geom"
            result["error"] = f"{type(exc).__name__}: {exc}"
            return result

    result["t_execute_all_ms"] = result["t_open_ms"] + result["t_extract_ms"] + result["t_geom_ms"]
    return result


def main() -> int:
    """CLI entry point: benchmark one file, emit one JSON line."""
    parser = argparse.ArgumentParser(description="Benchmark a single IFC file (one iteration).")
    parser.add_argument("ifc_path")
    parser.add_argument("--no-geom", action="store_true")
    parser.add_argument("--no-castor-extract", action="store_true")
    parser.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    if not args.no_castor_extract:
        try:
            _setup_django()
        except Exception as exc:
            print(json.dumps({"ok": False, "stage": "django-setup", "error": str(exc)}))
            return 1

    result = benchmark_file(
        args.ifc_path,
        do_extract=not args.no_castor_extract,
        do_geom=not args.no_geom,
        threads=args.threads,
    )
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
