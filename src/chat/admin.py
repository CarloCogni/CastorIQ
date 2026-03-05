# chat/admin.py
from django.contrib import admin

from .models import ChatSession, Message, MessageFeedback


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ("role", "content", "has_proposal", "created_at")
    can_delete = False


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "project", "mode", "is_active", "updated_at")
    list_filter = ("mode", "is_active", "project")
    search_fields = ("title", "user__username")
    inlines = [MessageInline]
    actions = ["generate_titles"]

    @admin.action(description="Auto-generate titles from first message")
    def generate_titles(self, request, queryset):
        for session in queryset:
            session.generate_title()


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    search_fields = ("content", "role", "session__title")
    list_display = ("session", "role", "content_excerpt", "has_proposal", "created_at")
    list_filter = ("role", "has_proposal", "created_at")
    readonly_fields = ("retrieved_context",)

    def content_excerpt(self, obj):
        return obj.content[:50] + "..."


@admin.register(MessageFeedback)
class MessageFeedbackAdmin(admin.ModelAdmin):
    list_display = ("message", "user", "rating", "created_at")
    list_filter = ("rating",)
    search_fields = ("comment",)
