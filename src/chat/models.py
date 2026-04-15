"""Chat models - conversation management."""

from django.conf import settings
from django.db import models

from core.models import UUIDModel
from environments.models import Project


class ChatSession(UUIDModel):
    """A chat session within a project."""

    class Mode(models.TextChoices):
        ASK = "ask", "Ask"
        MODIFY = "modify", "Modify"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="chat_sessions",
        verbose_name="Project",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_sessions",
        verbose_name="User",
    )

    title = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Title",
        help_text="Auto-generated from first message",
    )
    mode = models.CharField(
        max_length=20,
        choices=Mode.choices,
        default=Mode.ASK,
        db_index=True,
        verbose_name="Mode",
    )

    # Active session tracking
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="Active",
        help_text="Whether this is the current active session",
    )

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Chat Session"
        verbose_name_plural = "Chat Sessions"
        indexes = [
            models.Index(fields=["project", "user", "-updated_at"]),
            models.Index(fields=["project", "mode", "is_active"]),
        ]

    def __str__(self):
        return f"{self.get_mode_display()}: {self.title or 'Untitled'}"

    def generate_title(self):
        """Generate title from first message."""
        first_msg = self.messages.filter(role=Message.Role.USER).first()
        if first_msg:
            self.title = first_msg.content[:50] + ("..." if len(first_msg.content) > 50 else "")
            self.save(update_fields=["title"])


class Message(UUIDModel):
    """A message in a chat session."""

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        SYSTEM = "system", "System"

    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name="Session",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        db_index=True,
        verbose_name="Role",
    )
    content = models.TextField(
        verbose_name="Content",
    )

    # Retrieved context for transparency
    retrieved_context = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Retrieved Context",
        help_text="Sources used to generate the response",
    )

    # Conversation compaction
    is_compaction_summary = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Compaction Summary",
        help_text="Whether this message is a compacted summary of older messages",
    )
    compacted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Compacted At",
        help_text="When this message was summarized into a compaction summary",
    )

    # For modification messages
    has_proposal = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Has Proposal",
        help_text="Whether this message contains a modification proposal",
    )

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Message"
        verbose_name_plural = "Messages"
        indexes = [
            models.Index(fields=["session", "created_at"]),
            models.Index(fields=["session", "role"]),
        ]

    def __str__(self):
        return f"{self.get_role_display()}: {self.content[:30]}..."


class MessageFeedback(UUIDModel):
    """User feedback on assistant responses."""

    class Rating(models.TextChoices):
        POSITIVE = "positive", "Positive"
        NEGATIVE = "negative", "Negative"

    message = models.OneToOneField(
        Message,
        on_delete=models.CASCADE,
        related_name="feedback",
        verbose_name="Message",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="message_feedbacks",
        verbose_name="User",
    )
    rating = models.CharField(
        max_length=20,
        choices=Rating.choices,
        verbose_name="Rating",
    )
    comment = models.TextField(
        blank=True,
        verbose_name="Comment",
        help_text="Optional feedback details",
    )

    class Meta:
        verbose_name = "Message Feedback"
        verbose_name_plural = "Message Feedbacks"
        indexes = [
            models.Index(fields=["rating", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.rating} on {self.message_id}"
