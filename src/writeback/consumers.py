# writeback/consumers.py
"""
WebSocket consumers for the Modify and Conflicts tabs.

ProposalConsumer — streams the modification proposal pipeline.
ScanConsumer     — streams the conflict scan pipeline.

Protocol (ProposalConsumer):
    Client → Server:  {"action": "propose", "message": "<text>", "session_id": "<uuid>"}
    Server → Client:  {"type": "phase", "phase": "<name>", "status": "running|done|error", ...}
    Server → Client:  {"type": "proposal", "status": "proposed", "proposal": {...}}
    Server → Client:  {"type": "error", "message": "<text>"}
    Server → Client:  {"type": "done"}

Protocol (ScanConsumer):
    Client → Server:  {"action": "start_scan", "skip_low_value": true}
    Server → Client:  {"type": "phase", "phase": "<name>", "status": "running|done|error", ...}
    Server → Client:  {"type": "scan_complete", "stats": {...}}
    Server → Client:  {"type": "error", "message": "<text>"}
    Server → Client:  {"type": "done"}
"""

import json
import logging

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger(__name__)


class ProposalConsumer(AsyncJsonWebsocketConsumer):
    """
    Async WebSocket consumer that streams the proposal pipeline to the browser.

    One consumer instance per browser tab connection.
    Auth is handled by AuthMiddlewareStack in asgi.py.
    """

    async def connect(self) -> None:
        self.user = self.scope["user"]
        self.project_id = self.scope["url_route"]["kwargs"]["project_id"]

        if self.user.is_anonymous:
            await self.close(code=4001)
            return

        has_access = await self._check_project_access()
        if not has_access:
            await self.close(code=4003)
            return

        await self.accept()
        logger.debug(
            "ProposalConsumer connected: user=%s project=%s",
            self.user.username,
            self.project_id,
        )

    async def disconnect(self, close_code: int) -> None:
        logger.debug(
            "ProposalConsumer disconnected: user=%s code=%s",
            getattr(self.user, "username", "?"),
            close_code,
        )

    async def receive_json(self, content: dict, **kwargs) -> None:
        action = content.get("action")
        if action == "propose":
            await self._handle_propose(content)
        else:
            await self.send_json({"type": "error", "message": f"Unknown action: {action}"})

    # ── Private handlers ───────────────────────────────────────────────────────

    async def _handle_propose(self, content: dict) -> None:
        """Run the proposal pipeline and stream phase events to the client."""
        message_text = (content.get("message") or "").strip()
        if not message_text:
            await self.send_json({"type": "error", "message": "Message is required."})
            return

        session_id = content.get("session_id")
        conflict_ids_raw = content.get("conflict_ids", "")

        try:
            result, proposals = await self._run_pipeline(message_text, session_id, conflict_ids_raw)
        except Exception as e:
            logger.exception("Proposal pipeline error: %s", e)
            await self.send_json({"type": "error", "message": str(e)})
            return

        serialized = await self._serialize_result(result, proposals)
        await self.send_json({"type": "proposal", **serialized})
        await self.send_json({"type": "done"})

    @sync_to_async
    def _run_pipeline(self, message_text: str, session_id: str | None, conflict_ids_raw: str = ""):
        """
        Synchronous pipeline execution wrapped for async use.

        Creates the user message, runs ModificationService.propose() with a
        WebSocketEmitter, saves the assistant message, and returns the raw result.
        """
        from chat.models import ChatSession, Message
        from environments.models import Project
        from writeback.services.emitters import WebSocketEmitter
        from writeback.services.modification_service import ModificationError, ModificationService

        # Resolve project
        try:
            project = Project.objects.select_related("owner").get(pk=self.project_id)
        except Project.DoesNotExist:
            raise ModificationError("Project not found.")

        # Resolve or create session
        if session_id:
            try:
                session = ChatSession.objects.get(
                    pk=session_id,
                    project=project,
                    user=self.user,
                    mode=ChatSession.Mode.MODIFY,
                )
            except ChatSession.DoesNotExist:
                session = self._get_or_create_session(project, self.user)
        else:
            session = self._get_or_create_session(project, self.user)

        # Save user message
        user_msg = Message.objects.create(
            session=session,
            role=Message.Role.USER,
            content=message_text,
        )

        emitter = WebSocketEmitter(self.send_json)
        svc = ModificationService(project, user=self.user)

        try:
            result = svc.propose(
                user_message=message_text,
                user=self.user,
                message_obj=user_msg,
                emitter=emitter,
            )
        except ModificationError as e:
            Message.objects.create(
                session=session,
                role=Message.Role.ASSISTANT,
                content=f"⚠️ {e}",
            )
            raise

        # Normalize to list
        proposals = result if isinstance(result, list) else [result]
        is_chain = isinstance(result, list)

        # Save assistant message
        explanations = [p.explanation for p in proposals]
        assistant_msg = Message.objects.create(
            session=session,
            role=Message.Role.ASSISTANT,
            content=" + ".join(explanations) if is_chain else explanations[0],
        )

        linked_ids = [i.strip() for i in conflict_ids_raw.split(",") if i.strip()] if conflict_ids_raw else []
        for p in proposals:
            p.message = assistant_msg
            if linked_ids:
                p.linked_conflict_ids = linked_ids
                p.save(update_fields=["message", "linked_conflict_ids"])
            else:
                p.save(update_fields=["message"])

        # Auto-title
        if session.title == "New Modification":
            session.title = message_text[:50]
            session.save(update_fields=["title"])

        return result, proposals

    @sync_to_async
    def _serialize_result(self, result, proposals: list) -> dict:
        """Replicate the serialization from ModifyView._handle_propose()."""
        is_chain = isinstance(result, list)

        serialized = []
        for p in proposals:
            try:
                diff_preview = json.loads(p.diff_preview)
            except (json.JSONDecodeError, TypeError):
                diff_preview = []

            entry = {
                "id": str(p.id),
                "tier": p.tier,
                "operation": p.operation,
                "explanation": p.explanation,
                "confidence": p.confidence,
                "affected_count": p.affected_count,
                "diff_preview": diff_preview,
                "conflict_ids": ",".join(str(i) for i in p.linked_conflict_ids),
                "guardian": {
                    "status": p.verification_status,
                    "result": p.verification_result,
                    "source": p.verification_source,
                },
            }

            if p.tier == 2 and p.intent_json and "plan" in p.intent_json:
                entry["plan_steps"] = [
                    {
                        "step": s.get("step", i + 1),
                        "operation": s.get("operation", ""),
                        "explanation": s.get("explanation", ""),
                    }
                    for i, s in enumerate(p.intent_json["plan"])
                ]

            if p.tier == 3 and p.intent_json:
                if "code" in p.intent_json:
                    entry["code"] = p.intent_json["code"]
                if "review" in p.intent_json:
                    entry["review"] = p.intent_json["review"]

            serialized.append(entry)

        if is_chain:
            return {"status": "proposed", "chain": True, "proposals": serialized}

        return {"status": "proposed", "proposal": serialized[0]}

    @sync_to_async
    def _check_project_access(self) -> bool:
        from environments.models import Project

        try:
            project = Project.objects.select_related("owner").get(pk=self.project_id)
            return project.user_has_access(self.user)
        except Project.DoesNotExist:
            return False

    @staticmethod
    def _get_or_create_session(project, user):
        from chat.models import ChatSession

        session = (
            ChatSession.objects.filter(
                project=project,
                user=user,
                mode=ChatSession.Mode.MODIFY,
            )
            .order_by("-updated_at")
            .first()
        )
        if not session:
            session = ChatSession.objects.create(
                project=project,
                user=user,
                mode=ChatSession.Mode.MODIFY,
                title="New Modification",
            )
        return session


class ScanConsumer(AsyncJsonWebsocketConsumer):
    """
    Async WebSocket consumer that streams the conflict scan pipeline to the browser.

    One consumer instance per browser tab connection. Auth handled by
    AuthMiddlewareStack in asgi.py.
    """

    async def connect(self) -> None:
        self.user = self.scope["user"]
        self.project_id = self.scope["url_route"]["kwargs"]["project_id"]

        if self.user.is_anonymous:
            await self.close(code=4001)
            return

        has_access = await self._check_project_access()
        if not has_access:
            await self.close(code=4003)
            return

        await self.accept()
        logger.debug(
            "ScanConsumer connected: user=%s project=%s",
            self.user.username,
            self.project_id,
        )

    async def disconnect(self, close_code: int) -> None:
        logger.debug(
            "ScanConsumer disconnected: user=%s code=%s",
            getattr(self.user, "username", "?"),
            close_code,
        )

    async def receive_json(self, content: dict, **kwargs) -> None:
        action = content.get("action")
        if action == "start_scan":
            await self._handle_start_scan(content)
        else:
            await self.send_json({"type": "error", "message": f"Unknown action: {action}"})

    # ── Private handlers ───────────────────────────────────────────────────────

    async def _handle_start_scan(self, content: dict) -> None:
        """Run the conflict scan pipeline and stream phase events to the client."""
        skip_low_value = content.get("skip_low_value", True)

        try:
            stats = await self._run_scan(skip_low_value)
        except Exception as e:
            logger.exception("Conflict scan pipeline error: %s", e)
            await self.send_json({"type": "error", "message": str(e)})
            await self.send_json({"type": "done"})
            return

        await self.send_json({"type": "scan_complete", "stats": stats})
        await self.send_json({"type": "done"})

    @sync_to_async
    def _run_scan(self, skip_low_value: bool) -> dict:
        """Synchronous scan execution wrapped for async use."""
        from environments.models import Project
        from writeback.services.conflict_scan_service import ConflictScanService
        from writeback.services.emitters import WebSocketEmitter

        try:
            project = Project.objects.select_related("owner").get(pk=self.project_id)
        except Project.DoesNotExist:
            raise ValueError("Project not found.")

        emitter = WebSocketEmitter(self.send_json)
        svc = ConflictScanService(project, self.user, skip_low_value=skip_low_value)
        return svc.full_scan(emitter=emitter)

    @sync_to_async
    def _check_project_access(self) -> bool:
        from environments.models import Project

        try:
            project = Project.objects.select_related("owner").get(pk=self.project_id)
            return project.user_has_access(self.user)
        except Project.DoesNotExist:
            return False
