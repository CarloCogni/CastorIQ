# scripts/generate_schema_data.py
"""Convert ifc-lite's generated IFC schema tables into Python data modules.

Source: ifc-lite/packages/data/src/ifc-schema/generated/{entities,psets}-<schema>.ts
— themselves auto-generated from buildingSMART's IDS-Audit-tool (MIT). The TS
files are JSON-adjacent literals; this script normalizes them (quote keys,
strip trailing commas, undefined→null), validates the result, and emits
importable dict modules under src/ifc_processor/schema_data/.

Usage:
    uv run python scripts/generate_schema_data.py

Re-run whenever the ifc-lite clone is updated. Output files are committed.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "ifc-lite" / "packages" / "data" / "src" / "ifc-schema" / "generated"
OUTPUT_DIR = REPO_ROOT / "src" / "ifc_processor" / "schema_data"

SCHEMAS = ("ifc2x3", "ifc4", "ifc4x3")

_KEY_RE = re.compile(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:")
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")

GENERATED_HEADER = '''\
# ifc_processor/schema_data/{stem}.py
"""IFC {schema} {what} table — GENERATED FILE, do not edit.

Data provenance: buildingSMART IDS-Audit-tool (MIT), via the generated
tables in ifc-lite (ifc-lite/packages/data/src/ifc-schema/generated/).
Regenerate with: uv run python scripts/generate_schema_data.py
"""

'''


def parse_ts_array(text: str) -> list[dict[str, Any]]:
    """Extract the single exported array literal from a generated TS file."""
    start = text.index("= [") + 2
    end = text.rindex("];") + 1
    literal = text[start:end]
    literal = literal.replace("undefined", "null")
    literal = _KEY_RE.sub(r'\1"\2":', literal)
    literal = _TRAILING_COMMA_RE.sub(r"\1", literal)
    return json.loads(literal)


def build_entities(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{name: {parent, abstract, predefined_types, attributes}} in source order."""
    return {
        row["name"]: {
            "parent": row.get("parent"),
            "abstract": bool(row.get("abstract")),
            "predefined_types": list(row.get("predefinedTypes") or ()),
            "attributes": list(row.get("attributes") or ()),
        }
        for row in rows
    }


def build_psets(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{pset_name: {applicable: [...], properties: {prop: {kind, data_type, enum}}}}."""
    psets: dict[str, dict[str, Any]] = {}
    for row in rows:
        properties: dict[str, dict[str, Any]] = {}
        for prop in row.get("properties") or ():
            entry: dict[str, Any] = {"kind": prop.get("kind", "single")}
            if prop.get("dataType"):
                entry["data_type"] = prop["dataType"]
            if prop.get("enumeration"):
                entry["enum"] = list(prop["enumeration"])
            properties[prop["name"]] = entry
        psets[row["name"]] = {
            "applicable": list(row.get("applicableEntities") or ()),
            "properties": properties,
        }
    return psets


def validate(schema: str, entities: dict, psets: dict) -> None:
    """Spot-check invariants that would break every downstream consumer."""
    assert len(entities) > 400, f"{schema}: suspiciously few entities ({len(entities)})"
    assert len(psets) > 100, f"{schema}: suspiciously few psets ({len(psets)})"
    for name, record in entities.items():
        parent = record["parent"]
        assert parent is None or parent in entities, f"{schema}: {name} has unknown parent {parent}"
    assert "IfcWall" in entities and "IfcWallStandardCase" in entities or schema == "ifc4x3"
    door_common = psets.get("Pset_DoorCommon", {})
    assert "FireRating" in door_common.get("properties", {}), f"{schema}: Pset_DoorCommon broken"


def write_module(path: Path, header: str, var_name: str, data: dict[str, dict]) -> None:
    """One record per line: readable, diff-able, and cheap to import."""
    lines = [header, f"{var_name}: dict[str, dict] = {{"]
    for name, record in data.items():
        lines.append(f"    {name!r}: {record!r},")
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("wrote %s (%d records)", path.name, len(data))


def generate(schema: str) -> None:
    entity_rows = parse_ts_array((SOURCE_DIR / f"entities-{schema}.ts").read_text(encoding="utf-8"))
    pset_rows = parse_ts_array((SOURCE_DIR / f"psets-{schema}.ts").read_text(encoding="utf-8"))
    entities = build_entities(entity_rows)
    psets = build_psets(pset_rows)
    validate(schema, entities, psets)

    label = schema.upper()
    write_module(
        OUTPUT_DIR / f"entities_{schema}.py",
        GENERATED_HEADER.format(stem=f"entities_{schema}", schema=label, what="entity hierarchy"),
        "ENTITIES",
        entities,
    )
    write_module(
        OUTPUT_DIR / f"psets_{schema}.py",
        GENERATED_HEADER.format(stem=f"psets_{schema}", schema=label, what="standard property-set"),
        "PSETS",
        psets,
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    if not SOURCE_DIR.exists():
        logger.error("ifc-lite generated tables not found at %s", SOURCE_DIR)
        return 1
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for schema in SCHEMAS:
        generate(schema)
    return 0


if __name__ == "__main__":
    sys.exit(main())
