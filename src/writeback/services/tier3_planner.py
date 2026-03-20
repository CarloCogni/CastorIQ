import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm import get_llm

logger = logging.getLogger(__name__)

TIER3_SYSTEM_PROMPT = """\
You are an IFC (Industry Foundation Classes) code generator.
The user's request requires direct IfcOpenShell Python code because it cannot
be handled by property changes or structured operation plans.

Return ONLY valid JSON (no markdown, no explanation) with ALL four keys:

{
  "tier": 3,
  "code": "<Python code as a single string — see template below>",
  "explanation": "<REQUIRED — 1-2 sentence summary of what the code will do>",
  "confidence": <REQUIRED — 0.0 to 1.0>
}

CRITICAL: All four keys (tier, code, explanation, confidence) are MANDATORY.
Do NOT omit any key. Responses missing keys will be rejected.

═══════════════════════════════════════════════════════════════
CODE TEMPLATE — your code MUST follow this exact structure:
═══════════════════════════════════════════════════════════════

def modify_ifc(model):
    \"\"\"
    Modify the IFC model in-place.

    Args:
        model: An ifcopenshell.file object, already opened.

    Returns:
        dict with:
            "summary": str — human-readable summary of what was done
            "changes": list of dicts, each with:
                - "global_id": str (GlobalId of affected entity, or "NEW" for created entities)
                - "entity_name": str
                - "ifc_type": str
                - "description": str (what changed)
                - "old_value": str
                - "new_value": str
    \"\"\"
    import ifcopenshell
    import ifcopenshell.api
    import ifcopenshell.util.element

    changes = []

    # ... your logic here ...

    return {
        "summary": "...",
        "changes": changes,
    }

═══════════════════════════════════════════════════════════════
ALLOWED IMPORTS (inside the function body only):
═══════════════════════════════════════════════════════════════
- ifcopenshell
- ifcopenshell.api
- ifcopenshell.util.element
- ifcopenshell.util.placement (for spatial operations)
- ifcopenshell.guid (for generating new GlobalIds)
- math, re, json (standard library)

FORBIDDEN — never use:
- os, sys, subprocess, shutil, pathlib
- open(), __import__, exec, eval, compile
- socket, urllib, http, requests
- Any file I/O or network access

═══════════════════════════════════════════════════════════════
EXAMPLES:
═══════════════════════════════════════════════════════════════

--- Example 1: Create new entities ---

User: "Create a new IfcSpace called 'Office-101' on Level 1"

{
  "tier": 3,
  "code": "def modify_ifc(model):\\n    import ifcopenshell\\n    import ifcopenshell.api\\n\\n    changes = []\\n\\n    # Find the target storey\\n    storey = None\\n    for s in model.by_type('IfcBuildingStorey'):\\n        if 'Level 1' in (s.Name or '') or '1' in (s.LongName or ''):\\n            storey = s\\n            break\\n\\n    if storey is None:\\n        raise ValueError('Could not find Level 1 storey')\\n\\n    # Create the space\\n    space = ifcopenshell.api.run('root.create_entity', model, ifc_class='IfcSpace')\\n    ifcopenshell.api.run('attribute.edit_attributes', model, product=space, attributes={'Name': 'Office-101', 'LongName': 'Office 101', 'Description': 'Office space'})\\n\\n    # Assign to storey\\n    ifcopenshell.api.run('spatial.assign_container', model, products=[space], relating_structure=storey)\\n\\n    changes.append({\\n        'global_id': space.GlobalId,\\n        'entity_name': 'Office-101',\\n        'ifc_type': 'IfcSpace',\\n        'description': 'Created new IfcSpace and assigned to Level 1',\\n        'old_value': '(none)',\\n        'new_value': 'Office-101 on Level 1',\\n    })\\n\\n    return {'summary': 'Created IfcSpace Office-101 on Level 1', 'changes': changes}",
  "explanation": "Creates a new IfcSpace entity named Office-101 and assigns it to the Level 1 storey.",
  "confidence": 0.85
}

--- Example 2: Delete entities ---

User: "Delete all IfcFurnishingElement entities"

{
  "tier": 3,
  "code": "def modify_ifc(model):\\n    import ifcopenshell\\n    import ifcopenshell.api\\n\\n    changes = []\\n    elements = model.by_type('IfcFurnishingElement')\\n\\n    for elem in list(elements):\\n        changes.append({\\n            'global_id': elem.GlobalId,\\n            'entity_name': elem.Name or '(unnamed)',\\n            'ifc_type': elem.is_a(),\\n            'description': 'Deleted entity',\\n            'old_value': elem.Name or elem.GlobalId,\\n            'new_value': '(deleted)',\\n        })\\n        ifcopenshell.api.run('root.remove_product', model, product=elem)\\n\\n    return {'summary': f'Deleted {len(changes)} furnishing elements', 'changes': changes}",
  "explanation": "Removes all IfcFurnishingElement entities from the model.",
  "confidence": 0.9
}

--- Example 3: Move entities between storeys ---

User: "Move door D-03 from Level 1 to Level 2"

{
  "tier": 3,
  "code": "def modify_ifc(model):\\n    import ifcopenshell\\n    import ifcopenshell.api\\n\\n    changes = []\\n\\n    # Find the door\\n    door = None\\n    for d in model.by_type('IfcDoor'):\\n        if d.Name and 'D-03' in d.Name:\\n            door = d\\n            break\\n\\n    if door is None:\\n        raise ValueError('Door D-03 not found')\\n\\n    # Find target storey\\n    target_storey = None\\n    for s in model.by_type('IfcBuildingStorey'):\\n        if 'Level 2' in (s.Name or '') or '2' in (s.LongName or ''):\\n            target_storey = s\\n            break\\n\\n    if target_storey is None:\\n        raise ValueError('Level 2 storey not found')\\n\\n    # Get current storey for change tracking\\n    old_storey = '(unknown)'\\n    for rel in model.by_type('IfcRelContainedInSpatialStructure'):\\n        if door in rel.RelatedElements:\\n            old_storey = rel.RelatingStructure.Name or '(unknown)'\\n            break\\n\\n    # Reassign\\n    ifcopenshell.api.run('spatial.assign_container', model, products=[door], relating_structure=target_storey)\\n\\n    changes.append({\\n        'global_id': door.GlobalId,\\n        'entity_name': door.Name,\\n        'ifc_type': 'IfcDoor',\\n        'description': 'Moved to Level 2',\\n        'old_value': f'Storey: {old_storey}',\\n        'new_value': f'Storey: {target_storey.Name}',\\n    })\\n\\n    return {'summary': f'Moved door D-03 to Level 2', 'changes': changes}",
  "explanation": "Moves door D-03 from its current storey to Level 2.",
  "confidence": 0.85
}

--- Example 4: Manage relationships ---

User: "Add IfcRelAggregates relationship to group all columns under the building"

{
  "tier": 3,
  "code": "def modify_ifc(model):\\n    import ifcopenshell\\n    import ifcopenshell.api\\n\\n    changes = []\\n    columns = list(model.by_type('IfcColumn'))\\n\\n    if not columns:\\n        raise ValueError('No columns found in model')\\n\\n    building = model.by_type('IfcBuilding')\\n    if not building:\\n        raise ValueError('No IfcBuilding found')\\n    building = building[0]\\n\\n    # Create aggregation relationship\\n    ifcopenshell.api.run('aggregate.assign_object', model, products=columns, relating_object=building)\\n\\n    for col in columns:\\n        changes.append({\\n            'global_id': col.GlobalId,\\n            'entity_name': col.Name or '(unnamed)',\\n            'ifc_type': 'IfcColumn',\\n            'description': f'Aggregated under {building.Name}',\\n            'old_value': '(no aggregation)',\\n            'new_value': f'Aggregated under {building.Name}',\\n        })\\n\\n    return {'summary': f'Aggregated {len(columns)} columns under {building.Name}', 'changes': changes}",
  "explanation": "Groups all columns under the building using IfcRelAggregates.",
  "confidence": 0.8
}

═══════════════════════════════════════════════════════════════
RULES:
═══════════════════════════════════════════════════════════════

1. The function receives an already-opened ifcopenshell model. Do NOT call ifcopenshell.open().
2. Do NOT call model.write() — saving is handled externally.
3. Always record EVERY change in the changes list. This is critical for traceability.
4. For new entities, use "NEW" as old_value or describe what was created.
5. For deleted entities, record the entity info BEFORE deleting it.
6. Use ifcopenshell.api.run() for all structural operations — never manipulate IFC entities directly.
7. Validate inputs: raise ValueError with a clear message if expected entities are not found.
8. Keep the code simple and linear. Avoid complex class hierarchies or abstractions.
9. Use model.by_type() and model.by_guid() for lookups — never iterate model directly.
10. All imports must be INSIDE the function body.
11. The function must be completely self-contained — no closures, no external state.
12. Be conservative with confidence. Complex spatial operations → lower confidence.
13. When in doubt about an ifcopenshell.api call, use the most common patterns shown in examples.
"""

TIER3_USER_TEMPLATE = """\
{user_message}

The project contains these relevant entities:
{entity_context}

Generate self-contained IfcOpenShell code to fulfill this request. Return JSON only.
"""


class Tier3Planner:
    """Generates sandboxed IfcOpenShell Python code from natural language requests."""

    def __init__(self, user=None):
        self.llm = get_llm(user=user, temperature=0.1, format_json=True)

    def generate_code(self, user_message: str, entity_context: str) -> dict:
        """Invoke LLM to produce a modify_ifc() function. Returns validated JSON with code, explanation, confidence."""
        messages = [
            SystemMessage(content=TIER3_SYSTEM_PROMPT),
            HumanMessage(
                content=TIER3_USER_TEMPLATE.format(
                    user_message=user_message,
                    entity_context=entity_context,
                )
            ),
        ]

        response = self.llm.invoke(messages)

        try:
            result = json.loads(response.content)
        except json.JSONDecodeError as e:
            logger.error(f"Tier3 planner returned invalid JSON: {response.content}")
            raise CodeGenerationError(f"Could not parse response as JSON: {e}") from e

        self._validate_result(result)

        # Normalize confidence
        raw_conf = result.get("confidence", 0.0)
        if isinstance(raw_conf, (int, float)) and raw_conf <= 1.0:
            result["confidence"] = round(raw_conf * 100)
        else:
            result["confidence"] = int(raw_conf)

        logger.info(
            f"Tier3 code generated: confidence={result['confidence']}%, "
            f"code length={len(result.get('code', ''))}"
        )

        return result

    def _validate_result(self, result: dict) -> None:
        if not isinstance(result, dict):
            raise CodeGenerationError("Response must be a JSON object")

        if result.get("tier") != 3:
            raise CodeGenerationError(f"Expected tier 3, got {result.get('tier')}")

        code = result.get("code", "")
        if not code or not isinstance(code, str):
            raise CodeGenerationError("Response must include non-empty 'code' string")

        if "def modify_ifc" not in code:
            raise CodeGenerationError("Code must define a 'modify_ifc' function")

        if "return" not in code:
            raise CodeGenerationError("Function must return a result dict")

        # Safety: check for forbidden patterns
        self._check_forbidden_patterns(code)

        # Graceful defaults — some models omit these despite being asked
        if "explanation" not in result:
            result["explanation"] = result.get("summary", "Tier 3 code generation")
            logger.warning("LLM omitted 'explanation' — using fallback")

        if "confidence" not in result:
            result["confidence"] = 0.5
            logger.warning("LLM omitted 'confidence' — defaulting to 50%%")

    @staticmethod
    def _check_forbidden_patterns(code: str) -> None:
        """Reject code with obviously dangerous patterns."""
        forbidden = [
            (r"\bimport\s+os\b", "import os"),
            (r"\bimport\s+sys\b", "import sys"),
            (r"\bimport\s+subprocess\b", "import subprocess"),
            (r"\bimport\s+shutil\b", "import shutil"),
            (r"\bimport\s+pathlib\b", "import pathlib"),
            (r"\bimport\s+socket\b", "import socket"),
            (r"\bimport\s+urllib\b", "import urllib"),
            (r"\bimport\s+http\b", "import http"),
            (r"\bimport\s+requests\b", "import requests"),
            (r"\b__import__\s*\(", "__import__()"),
            (r"\bexec\s*\(", "exec()"),
            (r"\beval\s*\(", "eval()"),
            (r"\bcompile\s*\(", "compile()"),
            (r"\bglobals\s*\(", "globals()"),
            (r"\bgetattr\s*\(", "getattr()"),
            (r"\bsetattr\s*\(", "setattr()"),
            (r"(?<!\w)open\s*\(", "open()"),
            (r"\bmodel\.write\b", "model.write() — saving is handled externally"),
        ]

        for pattern, label in forbidden:
            if re.search(pattern, code):
                raise CodeGenerationError(
                    f"Generated code contains forbidden pattern: {label}. "
                    f"Code generation rejected for safety."
                )


class CodeGenerationError(Exception):
    """Raised when the LLM produces invalid or unsafe code."""

    pass
