# chat/consumers.py
"""
WebSocket consumer for the Ask (RAG) tab.

AskConsumer — streams the RAG pipeline phases to the browser.

Protocol:
    Client → Server:  {"action": "ask", "message": "<text>", "session_id": "<uuid>", "scope": "auto|ifc|docs"}
    Client → Server:  {"action": "compact", "session_id": "<uuid>"}
    Client → Server:  {"action": "cancel"}
    Server → Client:  {"type": "phase", "phase": "intent|retrieve|generate|compact", "status": "running|done|error", "message": "..."}
    Server → Client:  {"type": "done"}
    Server → Client:  {"type": "compacted", "count": <int>}
    Server → Client:  {"type": "cancelled"}
    Server → Client:  {"type": "error", "message": "<text>"}
"""

import logging
import threading

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger(__name__)


class AskConsumer(AsyncJsonWebsocketConsumer):
    """
    Async WebSocket consumer that streams the RAG pipeline to the browser.

    One consumer instance per browser tab connection.
    Auth is handled by AuthMiddlewareStack in asgi.py.
    """

    async def connect(self) -> None:
        self.user = self.scope["user"]
        self.project_id = self.scope["url_route"]["kwargs"]["project_id"]
        self._cancel_event = threading.Event()

        if self.user.is_anonymous:
            await self.close(code=4001)
            return

        has_access = await self._check_project_access()
        if not has_access:
            await self.close(code=4003)
            return

        await self.accept()
        logger.debug(
            "AskConsumer connected: user=%s project=%s",
            self.user.username,
            self.project_id,
        )

    async def disconnect(self, close_code: int) -> None:
        logger.debug(
            "AskConsumer disconnected: user=%s code=%s",
            getattr(self.user, "username", "?"),
            close_code,
        )

    async def receive_json(self, content: dict, **kwargs) -> None:
        action = content.get("action")
        if action == "ask":
            await self._handle_ask(content)
        elif action == "cancel":
            self._cancel_event.set()
            logger.debug("AskConsumer: cancel requested by %s", self.user.username)
        elif action == "compact":
            await self._handle_compact(content)
        else:
            await self.send_json({"type": "error", "message": f"Unknown action: {action}"})

    # ── Private handlers ───────────────────────────────────────────────────────

    async def _handle_ask(self, content: dict) -> None:
        """Run the RAG pipeline and stream phase events to the client."""
        message_text = (content.get("message") or "").strip()
        if not message_text:
            await self.send_json({"type": "error", "message": "Message is required."})
            return

        # Reset cancel state for each new request
        self._cancel_event.clear()

        session_id = content.get("session_id")
        scope = content.get("scope", "auto")

        try:
            await self._run_pipeline(message_text, session_id, scope)
        except Exception as e:
            from writeback.services.emitters import CancellationError

            if isinstance(e, CancellationError):
                await self.send_json({"type": "cancelled"})
                return
            logger.exception("RAG pipeline error: %s", e)
            await self.send_json({"type": "error", "message": str(e)})
            return

        await self.send_json({"type": "done"})

    async def _handle_compact(self, content: dict) -> None:
        """Compact conversation history for the given session."""
        session_id = content.get("session_id")
        if not session_id:
            await self.send_json({"type": "error", "message": "session_id is required."})
            return

        try:
            count = await self._run_compaction(session_id)
        except Exception as e:
            logger.exception("Compaction error: %s", e)
            await self.send_json({"type": "error", "message": str(e)})
            return

        await self.send_json({"type": "compacted", "count": count})

    @sync_to_async
    def _run_compaction(self, session_id: str) -> int:
        """Synchronous compaction execution wrapped for async use."""
        from chat.models import ChatSession
        from chat.services.compaction_service import CompactionService
        from writeback.services.emitters import WebSocketEmitter

        try:
            session = ChatSession.objects.get(
                pk=session_id,
                project_id=self.project_id,
                user=self.user,
                mode=ChatSession.Mode.ASK,
            )
        except ChatSession.DoesNotExist:
            raise ValueError("Session not found.")

        emitter = WebSocketEmitter(self.send_json, cancel_event=self._cancel_event)
        compactor = CompactionService(user=self.user)
        return compactor.compact_session(session, emitter=emitter)

    @sync_to_async
    def _run_pipeline(self, message_text: str, session_id: str | None, scope: str) -> None:
        """
        Synchronous pipeline execution wrapped for async use.

        Saves the user message, runs RAGService.generate_answer() with a
        WebSocketEmitter, saves the assistant message, and auto-titles the session.
        """
        from chat.models import ChatSession, Message
        from chat.services.rag_service import RAGService
        from environments.models import Project
        from writeback.services.emitters import CancellationError, WebSocketEmitter

        # Resolve project
        try:
            project = Project.objects.select_related("owner").get(pk=self.project_id)
        except Project.DoesNotExist:
            raise ValueError("Project not found.")

        # Resolve or create session
        if session_id:
            try:
                session = ChatSession.objects.get(
                    pk=session_id,
                    project=project,
                    user=self.user,
                    mode=ChatSession.Mode.ASK,
                )
            except ChatSession.DoesNotExist:
                session = self._get_or_create_session(project, self.user)
        else:
            session = self._get_or_create_session(project, self.user)

        # Save user message
        Message.objects.create(
            session=session,
            role=Message.Role.USER,
            content=message_text,
        )

        emitter = WebSocketEmitter(self.send_json, cancel_event=self._cancel_event)
        rag = RAGService(user=self.user)

        try:
            answer_text, context_items, _ = rag.generate_answer(
                project,
                session,
                message_text,
                scope=scope,
                emitter=emitter,
            )
            Message.objects.create(
                session=session,
                role=Message.Role.ASSISTANT,
                content=answer_text,
                retrieved_context=rag._serialize_context(context_items, scope=scope),
            )
        except CancellationError:
            logger.debug("RAG pipeline cancelled for user=%s", self.user.username)
            raise
        except Exception as e:
            Message.objects.create(
                session=session,
                role=Message.Role.ASSISTANT,
                content="⚠️ I encountered an error processing your question. "
                "Please check that Ollama is running and try again.",
            )
            raise e

        # Auto-title on first exchange
        if not session.title or session.title == "New conversation":
            session.generate_title()

    @sync_to_async
    def _check_project_access(self) -> bool:
        from environments.models import Project
        from environments.services import ProjectAccessService

        try:
            project = Project.objects.select_related("owner").get(pk=self.project_id)
            return ProjectAccessService.can_access(self.user, project)
        except Project.DoesNotExist:
            return False

    @staticmethod
    def _get_or_create_session(project, user):
        from chat.models import ChatSession

        session = (
            ChatSession.objects.filter(
                project=project,
                user=user,
                mode=ChatSession.Mode.ASK,
            )
            .order_by("-updated_at")
            .first()
        )
        if not session:
            session = ChatSession.objects.create(
                project=project,
                user=user,
                mode=ChatSession.Mode.ASK,
                title="New conversation",
            )
        return session
