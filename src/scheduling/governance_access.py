# scheduling/governance_access.py
"""View mixins for governance capability enforcement (E2-F)."""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from environments.models import Project
from environments.services.access_service import ProjectAccessService
from scheduling.services.governance.authority import (
    GovernanceAuthorityError,
    GovernanceAuthorityPolicy,
    GovernanceCapability,
)


class GovernanceCapabilityMixin(LoginRequiredMixin):
    """Enforce a specific governance capability or project access tier."""

    governance_capability: GovernanceCapability | None = None
    require_modify: bool = False

    def get_project(self) -> Project:
        project = get_object_or_404(Project.objects.select_related("owner"), pk=self.kwargs["pk"])
        policy = GovernanceAuthorityPolicy(project, self.request.user)
        if self.governance_capability is not None:
            try:
                policy.require(self.governance_capability)
            except GovernanceAuthorityError as exc:
                raise PermissionDenied(exc.result.reason) from exc
        elif self.require_modify:
            if not ProjectAccessService.can_modify(self.request.user, project):
                raise PermissionDenied
        elif not ProjectAccessService.can_access(self.request.user, project):
            raise PermissionDenied
        self.governance_policy = policy
        return project
