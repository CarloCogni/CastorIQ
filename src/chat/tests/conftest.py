# chat/tests/conftest.py
"""Shared fixtures for chat tests."""

import pytest

from chat.tests.factories import ChatSessionFactory, MessageFactory
from environments.tests.factories import ProjectFactory, UserFactory


@pytest.fixture
def user():
    """A user fixture built via factory."""
    return UserFactory()


@pytest.fixture
def project(user):
    """A project owned by the test user."""
    return ProjectFactory(owner=user)


@pytest.fixture
def chat_session(project, user):
    """An active chat session in the test project."""
    return ChatSessionFactory(project=project, user=user)


@pytest.fixture
def user_message(chat_session):
    """A user message in the test chat session."""
    return MessageFactory(
        session=chat_session,
        role="user",
        content="What are the fire ratings of the walls?",
    )


@pytest.fixture
def assistant_message(chat_session):
    """An assistant message in the test chat session."""
    return MessageFactory(
        session=chat_session,
        role="assistant",
        content="The walls have a fire rating of EI60.",
    )
