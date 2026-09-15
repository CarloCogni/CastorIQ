# writeback/consumers.py
"""
WebSocket consumers for the Modify and Conflicts tabs.

ProposalConsumer — streams the modification proposal pipeline.
ScanConsumer     — streams the conflict scan pipeline.

Protocol (ProposalConsumer):
    Client → Server:  {"action": "propose", "message": "<text>", "session_id": "<uuid>",
                       "skip_guardian": false}
    Client → Server:  {"action": "cancel"}
    Server → Client:  {"type": "phase", "phase": "ground|generate|run|verify|guardian",
                       "status": "running|done|error", "detail": {targets, flags, verdict}}
    Server → Client:  {"type": "proposal", "status": "proposed", "proposal": {...}}
    Server → Client:  {"type": "proposal", "status": "no_change", "message": "<text>"}
    Server → Client:  {"type": "cancelled"}
    Server → Client:  {"type": "error", "message": "<text>"}
    Server → Client:  {"type": "done"}

A propose runs as an asyncio task: Channels dispatches frames one at a time,
so a ``cancel`` frame is only seen while the propose is running if the
propose does not hold the dispatch loop. One propose per connection at a
time; a second one while the first runs is answered with an error.

Protocol (ScanConsumer):
    Client → Server:  {"action": "start_scan", "skip_low_value": true}
    Client → Server:  {"action": "cancel"}
    Server → Client:  {"type": "phase", "phase": "<name>", "status": "running|done|error", ...}
    Server → Client:  {"type": "scan_complete", "stats": {...}}
    Server → Client:  {"type": "cancelled"}
    Server → Client:  {"type": "error", "message": "<text>"}
    Server → Client:  {"type": "done"}
"""

import asyncio
import logging
import threading

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from core.consumers import CastorConsumerMixin, capture_consumer_errors

logger = logging.getLogger(__name__)


class ProposalConsumer(CastorConsumerMixin, AsyncJsonWebsocketConsumer):
    """
    Async WebSocket consumer that streams the proposal pipeline to the browser.

    One consumer instance per browser tab connection.
    Auth is handled by AuthMiddlewareStack in asgi.py.
    """

    @capture_consumer_errors
    async def connect(self) -> None:
        self.user = self.scope["user"]
        self.project_id = self.scope["url_route"]["kwargs"]["project_id"]
        self._cancel_event = threading.Event()
        self._task: asyncio.Task | None = None

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

    @capture_consumer_errors
    async def disconnect(self, close_code: int) -> None:
        # Abort a running pipeline at its next emit: a closed tab must not keep
        # the model busy on a proposal nobody will see.
        cancel_event = getattr(self, "_cancel_event", None)
        if cancel_event is not None:
            cancel_event.set()
        logger.debug(
            "ProposalConsumer disconnected: user=%s code=%s",
            getattr(self.user, "username", "?"),
            close_code,
        )

    @capture_consumer_errors
    async def receive_json(self, content: dict, **kwargs) -> None:
        action = content.get("action")
        if action == "propose":
            if self._task is not None and not self._task.done():
                await self.safe_send_json(
                    {"type": "error", "message": "A request is already running; press Stop first."}
                )
                return
            # A task, not an await: the dispatch loop stays free for a cancel frame.
            self._task = asyncio.create_task(self._handle_propose(content))
        elif action == "cancel":
            self._cancel_event.set()
            logger.debug("ProposalConsumer: cancel requested by %s", self.user.username)
        else:
            await self.safe_send_json({"type": "error", "message": f"Unknown action: {action}"})

    # ── Private handlers ───────────────────────────────────────────────────────

    async def _handle_propose(self, content: dict) -> None:
        """Run the proposal pipeline and stream phase events to the client."""
        message_text = (content.get("message") or "").strip()
        if not message_text:
            await self.safe_send_json({"type": "error", "message": "Message is required."})
            return

        # Reset cancel state for each new request
        self._cancel_event.clear()

        session_id = content.get("session_id")
        conflict_ids_raw = content.get("conflict_ids", "")
        skip_guardian = bool(content.get("skip_guardian"))

        try:
            proposal, superseded_ids = await self._run_pipeline(
                message_text, session_id, conflict_ids_raw, skip_guardian
            )
            # The card render is inside the guard: a serialiser error must
            # reach the client as an error frame, not close the socket.
            serialized = await self._serialize_result(proposal)
            serialized["superseded_ids"] = superseded_ids
            await self.safe_send_json({"type": "proposal", **serialized})
            await self.safe_send_json({"type": "done"})
        except Exception as e:
            from writeback.services.emitters import CancellationError
            from writeback.services.modification_service import ModificationError, NoChangeError

            if isinstance(e, CancellationError):
                await self.safe_send_json({"type": "cancelled"})
                return
            if isinstance(e, NoChangeError):
                await self.safe_send_json(
                    {"type": "proposal", "status": "no_change", "message": str(e)}
                )
                await self.safe_send_json({"type": "done"})
                return
            logger.exception("Proposal pipeline error: %s", e)
            await self.log_consumer_exception(e, method="_handle_propose")
            failure_id = (
                getattr(e, "failure_record_id", None) if isinstance(e, ModificationError) else None
            )
            if failure_id:
                failure_data = await self._load_failure_data(failure_id)
                await self.safe_send_json(
                    {"type": "failure", "failure": failure_data, "message": str(e)}
                )
            else:
                await self.safe_send_json({"type": "error", "message": str(e)})

    # `thread_sensitive=False` puts the LLM-bound work on the asgiref shared
    # thread pool instead of the single shared sync thread. Without this, a
    # stuck Ollama call holds the only sync thread and starves every other
    # @sync_to_async — including new WebSocket auth checks — so new tabs can't
    # even connect until the in-flight call returns or hits the 60s timeout.
    @sync_to_async(thread_sensitive=False)
    def _run_pipeline(
        self,
        message_text: str,
        session_id: str | None,
        conflict_ids_raw: str = "",
        skip_guardian: bool = False,
    ):
        """
        Synchronous pipeline execution wrapped for async use.

        Resolves the session, builds the WebSocket emitter and hands the
        request to ``ModificationService.propose_in_session``, which owns the
        chat messages, the supersede and the link on both transports.
        """
        from chat.models import ChatSession
        from environments.models import Project
        from writeback.services.emitters import WebSocketEmitter
        from writeback.services.modification_service import ModificationError, ModificationService

        try:
            project = Project.objects.select_related("owner").get(pk=self.project_id)
        except Project.DoesNotExist:
            raise ModificationError("Project not found.")

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

        emitter = WebSocketEmitter(
            self.send_json,
            cancel_event=self._cancel_event,
            scope=self.scope,
            consumer_name=f"{type(self).__module__}.{type(self).__name__}",
        )
        return ModificationService(project, user=self.user).propose_in_session(
            session,
            message_text,
            self.user,
            conflict_ids=conflict_ids_raw,
            skip_guardian=skip_guardian,
            emitter=emitter,
        )

    @sync_to_async
    def _load_failure_data(self, failure_id: str) -> dict:
        """Fetch FailureRecord and return a serialized dict for the client."""
        from metacastor.models import FailureRecord

        try:
            rec = FailureRecord.objects.get(pk=failure_id)
        except FailureRecord.DoesNotExist:
            return {
                "id": failure_id,
                "error_type": "UNKNOWN",
                "category": "NON_RETRYABLE",
                "diagnosis": "Error details unavailable.",
                "failure_phase": "UNKNOWN",
                "is_retryable": False,
            }
        return {
            "id": str(rec.id),
            "error_type": rec.error_type,
            "category": rec.category,
            "diagnosis": rec.diagnosis,
            "failure_phase": rec.failure_phase,
            "is_retryable": rec.category == "RETRYABLE",
        }

    @sync_to_async
    def _serialize_result(self, proposal) -> dict:
        """The one serialiser plus the rendered card, same as ModifyView._handle_propose()."""
        from writeback.services.proposal_serializer import render_card, serialize_proposal

        card = serialize_proposal(proposal)
        card["html"] = render_card(proposal, proposal.ifc_file.project, card)
        return {"status": "proposed", "proposal": card}

    @sync_to_async
    def _check_project_access(self) -> bool:
        from environments.models import Project
        from environments.services import ProjectAccessService

        try:
            project = Project.objects.select_related("owner").get(pk=self.project_id)
            # Modify-mode proposals mutate the IFC — EDITOR+ only.
            return ProjectAccessService.can_modify(self.user, project)
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


class ScanConsumer(CastorConsumerMixin, AsyncJsonWebsocketConsumer):
    """
    Async WebSocket consumer that streams the conflict scan pipeline to the browser.

    One consumer instance per browser tab connection. Auth handled by
    AuthMiddlewareStack in asgi.py.
    """

    @capture_consumer_errors
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
            "ScanConsumer connected: user=%s project=%s",
            self.user.username,
            self.project_id,
        )

    @capture_consumer_errors
    async def disconnect(self, close_code: int) -> None:
        # Signal any running scan to abort at the next emit boundary. Without
        # this, the sync_to_async thread keeps invoking Ollama after the client
        # is gone, leaking the thread and queueing up behind a zombie request.
        cancel_event = getattr(self, "_cancel_event", None)
        if cancel_event is not None:
            cancel_event.set()
        logger.debug(
            "ScanConsumer disconnected: user=%s code=%s",
            getattr(self.user, "username", "?"),
            close_code,
        )

    @capture_consumer_errors
    async def receive_json(self, content: dict, **kwargs) -> None:
        action = content.get("action")
        if action == "start_scan":
            await self._handle_start_scan(content)
        elif action == "cancel":
            self._cancel_event.set()
            logger.debug("ScanConsumer: cancel requested by %s", self.user.username)
        else:
            await self.safe_send_json({"type": "error", "message": f"Unknown action: {action}"})

    # ── Private handlers ───────────────────────────────────────────────────────

    async def _handle_start_scan(self, content: dict) -> None:
        """Run the conflict scan pipeline and stream phase events to the client."""
        skip_low_value = content.get("skip_low_value", True)

        # Clear stale cancel state so a prior cancel doesn't kill this run.
        self._cancel_event.clear()

        try:
            stats = await self._run_scan(skip_low_value)
        except Exception as e:
            from writeback.services.emitters import CancellationError

            if isinstance(e, CancellationError):
                logger.debug("Conflict scan cancelled for user=%s", self.user.username)
                await self.safe_send_json({"type": "cancelled"})
                return
            logger.exception("Conflict scan pipeline error: %s", e)
            await self.log_consumer_exception(e, method="_handle_start_scan")
            await self.safe_send_json({"type": "error", "message": str(e)})
            await self.safe_send_json({"type": "done"})
            return

        await self.safe_send_json({"type": "scan_complete", "stats": stats})
        await self.safe_send_json({"type": "done"})

    # See _run_pipeline above — same reason: keep LLM-bound work off the
    # single shared sync thread so connect()/auth on other tabs isn't blocked.
    @sync_to_async(thread_sensitive=False)
    def _run_scan(self, skip_low_value: bool) -> dict:
        """Synchronous scan execution wrapped for async use."""
        from environments.models import Project
        from writeback.services.conflict_scan_service import ConflictScanService
        from writeback.services.emitters import WebSocketEmitter

        try:
            project = Project.objects.select_related("owner").get(pk=self.project_id)
        except Project.DoesNotExist:
            raise ValueError("Project not found.")

        emitter = WebSocketEmitter(
            self.send_json,
            cancel_event=self._cancel_event,
            scope=self.scope,
            consumer_name=f"{type(self).__module__}.{type(self).__name__}",
        )
        svc = ConflictScanService(project, self.user, skip_low_value=skip_low_value)
        return svc.full_scan(emitter=emitter)

    @sync_to_async
    def _check_project_access(self) -> bool:
        from environments.models import Project
        from environments.services import ProjectAccessService

        try:
            project = Project.objects.select_related("owner").get(pk=self.project_id)
            # Conflict scans are read-only reporting; allow any member.
            return ProjectAccessService.can_access(self.user, project)
        except Project.DoesNotExist:
            return False


class SchemaConversionConsumer(CastorConsumerMixin, AsyncJsonWebsocketConsumer):
    """
    Async WebSocket consumer that streams the IFC schema conversion pipeline to the browser.

    One consumer instance per conversion page load. Auth handled by AuthMiddlewareStack.

    Protocol:
        Client → Server:  {"action": "start_convert"}
        Server → Client:  {"type": "phase", "phase": "<name>", "status": "running|done|error", "message": "…"}
        Server → Client:  {"type": "convert_complete", "success": true|false, …}
        Server → Client:  {"type": "error", "message": "<text>"}
        Server → Client:  {"type": "done"}
    """

    @capture_consumer_errors
    async def connect(self) -> None:
        self.user = self.scope["user"]
        self.project_id = self.scope["url_route"]["kwargs"]["project_id"]
        self.ifc_file_id = self.scope["url_route"]["kwargs"]["ifc_file_id"]

        if self.user.is_anonymous:
            await self.close(code=4001)
            return

        has_access = await self._check_access()
        if not has_access:
            await self.close(code=4003)
            return

        await self.accept()
        logger.debug(
            "SchemaConversionConsumer connected: user=%s ifc_file=%s",
            self.user.username,
            self.ifc_file_id,
        )

    @capture_consumer_errors
    async def disconnect(self, close_code: int) -> None:
        logger.debug(
            "SchemaConversionConsumer disconnected: user=%s code=%s",
            getattr(self.user, "username", "?"),
            close_code,
        )

    @capture_consumer_errors
    async def receive_json(self, content: dict, **kwargs) -> None:
        action = content.get("action")
        if action == "start_convert":
            await self._handle_start_convert()
        else:
            await self.safe_send_json({"type": "error", "message": f"Unknown action: {action}"})

    # ── Private handlers ───────────────────────────────────────────────────────

    async def _handle_start_convert(self) -> None:
        """Run the conversion pipeline and stream phase events to the client."""
        try:
            result = await self._run_conversion()
        except Exception as e:
            logger.exception("Schema conversion consumer error: %s", e)
            await self.log_consumer_exception(e, method="_handle_start_convert")
            await self.safe_send_json({"type": "error", "message": str(e)})
            await self.safe_send_json({"type": "done"})
            return

        await self.safe_send_json({"type": "convert_complete", **result})
        await self.safe_send_json({"type": "done"})

    @sync_to_async
    def _run_conversion(self) -> dict:
        """Synchronous conversion execution wrapped for async use."""
        from ifc_processor.models import IFCFile
        from ifc_processor.services.schema_converter import IFCSchemaConverterService
        from writeback.services.emitters import WebSocketEmitter

        ifc_file = IFCFile.objects.select_related("project").get(pk=self.ifc_file_id)
        emitter = WebSocketEmitter(
            self.send_json,
            scope=self.scope,
            consumer_name=f"{type(self).__module__}.{type(self).__name__}",
        )
        return IFCSchemaConverterService(ifc_file).convert(emitter=emitter)

    @sync_to_async
    def _check_access(self) -> bool:
        from environments.services import ProjectAccessService
        from ifc_processor.models import IFCFile

        try:
            ifc_file = IFCFile.objects.select_related("project__owner").get(pk=self.ifc_file_id)
            # Schema conversion rewrites the IFC file — EDITOR+ only.
            return ProjectAccessService.can_modify(self.user, ifc_file.project)
        except IFCFile.DoesNotExist:
            return False
