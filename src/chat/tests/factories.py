# chat/tests/factories.py
"""Factory Boy factories for chat models."""

import factory

from environments.tests.factories import ProjectFactory


class ChatSessionFactory(factory.django.DjangoModelFactory):
    """Factory for chat.ChatSession."""

    class Meta:
        model = "chat.ChatSession"

    project = factory.SubFactory(ProjectFactory)
    user = factory.LazyAttribute(lambda o: o.project.owner)
    title = factory.Sequence(lambda n: f"Test Session {n}")
    mode = "ask"
    is_active = True


class MessageFactory(factory.django.DjangoModelFactory):
    """Factory for chat.Message."""

    class Meta:
        model = "chat.Message"

    session = factory.SubFactory(ChatSessionFactory)
    role = "user"
    content = factory.Sequence(lambda n: f"Test message content {n}")
    retrieved_context = factory.LazyFunction(list)
    has_proposal = False
