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
    """Factory for environments.Project."""

    class Meta:
        model = "environments.Project"

    name = factory.Sequence(lambda n: f"Project {n}")
    owner = factory.SubFactory(UserFactory)


class ProjectMembershipFactory(factory.django.DjangoModelFactory):
    """Factory for environments.ProjectMembership."""

    class Meta:
        model = "environments.ProjectMembership"

    project = factory.SubFactory(ProjectFactory)
    user = factory.SubFactory(UserFactory)
    role = "viewer"
