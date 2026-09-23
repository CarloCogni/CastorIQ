# scheduling/services/governance/policy.py
"""Code-level trusted binding policy identifier and statements (E2-A)."""

from __future__ import annotations

TRUSTED_BINDING_POLICY_ID = "trusted-binding-v1"

TRUSTED_BINDING_POLICY: dict[str, str | tuple[str, ...]] = {
    "id": TRUSTED_BINDING_POLICY_ID,
    "accepted_rule": "TaskEntityBinding.needs_review=False",
    "review_rule": "TaskEntityBinding.needs_review=True",
    "property_metadata_rule": "IFC Activity ID and similar properties are evidence hints only",
    "m2m_rule": "Task.ifc_entities M2M is compatibility storage only; never primary truth",
    "review_only_methods": (
        "normalized",
        "heuristic",
        "embedding",
    ),
    "multiple_accepted_tasks_rule": (
        "One entity may map to multiple accepted tasks unless explicit conflict evidence applies"
    ),
    "conflict_rule": (
        "possible_conflict requires explicit deterministic evidence such as overlapping "
        "accepted task date ranges on the same entity"
    ),
}
