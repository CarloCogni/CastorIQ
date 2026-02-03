"""Chat models."""

from django.contrib.auth.models import User
from django.db import models

from core.models import TimestampedModel
from environments.models import Environment


class ChatSession(TimestampedModel):
    """A chat session within an environment."""

    environment = models.ForeignKey(
        Environment, on_delete=models.CASCADE, related_name="chat_sessions"
    )
    title = models.CharField(max_length=255, blank=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Chat: {self.title or self.id}"


class Message(TimestampedModel):
    """A message in a chat session."""

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        SYSTEM = "system", "System"

    session = models.ForeignKey(
        ChatSession, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    content = models.TextField()

    # For storing retrieved context
    retrieved_chunks = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.role}: {self.content[:50]}..."


class Feedback(TimestampedModel):
    """User feedback on assistant responses."""

    class Rating(models.TextChoices):
        POSITIVE = "positive", "Positive"
        NEGATIVE = "negative", "Negative"

    message = models.OneToOneField(
        Message, on_delete=models.CASCADE, related_name="feedback"
    )
    rating = models.CharField(max_length=20, choices=Rating.choices)
    correction = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"Feedback: {self.rating} for message {self.message_id}"
