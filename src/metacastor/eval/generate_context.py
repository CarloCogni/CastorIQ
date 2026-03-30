# metacastor/eval/generate_context.py
"""
One-time helper: reads an IFC fixture file and prints the entity_context string
suitable for pasting into eval_cases.jsonl / dev_cases.jsonl.

Usage (from src/ directory):
    python metacastor/eval/generate_context.py metacastor/eval/fixtures/architecture.ifc

Requires Django settings (for ifc_standard_psets lookup inside build_entity_context).
Ollama does NOT need to be running — IntentClassifier is instantiated without its LLM.
"""

import os
import sys
from collections import namedtuple
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
sys.path.insert(0, str(Path(__file__).parents[2]))  # adds src/ to path
import django

django.setup()

import ifcopenshell  # noqa: E402 — must come after django.setup()

from writeback.services.intent_classifier import IntentClassifier  # noqa: E402

# Duck-typed object matching the IFCEntity ORM interface expected by build_entity_context.
MockEntity = namedtuple("MockEntity", ["ifc_type", "name", "global_id", "properties"])


def load_entities(ifc_path: Path) -> list:
    """Read all IfcElement instances from an IFC file as MockEntity objects."""
    model = ifcopenshell.open(str(ifc_path))
    entities = []
    for el in model.by_type("IfcElement"):
        props: dict = {}
        for rel in getattr(el, "IsDefinedBy", []):
            if not rel.is_a("IfcRelDefinesByProperties"):
                continue
            pset = rel.RelatingPropertyDefinition
            if not pset.is_a("IfcPropertySet"):
                continue
            for p in pset.HasProperties:
                if p.is_a("IfcPropertySingleValue") and p.NominalValue is not None:
                    props[f"{pset.Name}.{p.Name}"] = p.NominalValue.wrappedValue
        entities.append(MockEntity(el.is_a(), el.Name, el.GlobalId, props))
    return entities


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python generate_context.py <path/to/fixture.ifc>", file=sys.stderr)
        sys.exit(1)

    ifc_path = Path(sys.argv[1])
    if not ifc_path.exists():
        print(f"File not found: {ifc_path}", file=sys.stderr)
        sys.exit(1)

    entities = load_entities(ifc_path)
    if not entities:
        print("No IfcElement instances found in file.", file=sys.stderr)
        sys.exit(1)

    # Instantiate without calling __init__ (which would invoke get_llm / Ollama).
    classifier = IntentClassifier.__new__(IntentClassifier)
    context = classifier.build_entity_context(entities)

    print(f"\n# entity_context for: {ifc_path.name}")
    print(f"# {len(entities)} elements found")
    print("-" * 60)
    print(context)


if __name__ == "__main__":
    main()
