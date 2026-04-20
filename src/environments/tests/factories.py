# environments/tests/factories.py
"""Factory Boy factories for environments models."""

import factory
from django.contrib.auth import get_user_model

User = get_user_model()


class UserFactory(factory.django.DjangoModelFactory):
    """Factory for Django User model."""

    class Meta:
        model = User

    username = factory.Sequence(lambda n: f"user_{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@test.com")
    password = factory.PostGenerationMethodCall("set_password", "testpass123")


class ProjectFactory(factory.django.DjangoModelFactory):
    """Factory for environments.Project.

    Auto-creates the OWNER membership row on the post-generation hook so
    ``ProjectAccessService.can_access(owner, project)`` returns True without
    tests having to remember the bootstrap step.
    """

    class Meta:
        model = "environments.Project"

    name = factory.Sequence(lambda n: f"Project {n}")
    owner = factory.SubFactory(UserFactory)

    @factory.post_generation
    def _bootstrap_owner(obj, create, extracted, **kwargs):  # noqa: N805 — factory_boy passes the generated object
        if not create:
            return
        # Deferred import: avoids a circular import when factories are loaded
        # before the services package.
        from environments.services import ProjectAccessService

        ProjectAccessService.bootstrap_owner_membership(obj)


class ProjectMembershipFactory(factory.django.DjangoModelFactory):
    """Factory for ProjectMembership rows (non-OWNER by default).

    Use ``transfer_ownership`` if you need to move the OWNER — don't try to
    create a second OWNER row via this factory.
    """

    class Meta:
        model = "environments.ProjectMembership"

    project = factory.SubFactory(ProjectFactory)
    user = factory.SubFactory(UserFactory)
    permission = "viewer"


class ProjectRoleFactory(factory.django.DjangoModelFactory):
    """Factory for environments.ProjectRole (7D FM functional roles)."""

    class Meta:
        model = "environments.ProjectRole"

    project = factory.SubFactory(ProjectFactory)
    user = factory.SubFactory(UserFactory)
    role = "facilitiesmanager"
