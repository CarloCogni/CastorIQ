# facilities/consumers.py
"""WebSocket consumer for the Facilities Export Reconciliation pipeline (M2).

Streams the export pipeline's phase events to the browser so the Export IFC
modal can render live progress (plan → snapshot → apply → validate → save →
commit → done) without polling. Falls back gracefully: the HTTP POST endpoint
in :class:`facilities.views.ExportApplyView` is the non-WebSocket path.

Protocol
--------
Client → Server::

    {"action": "start_export", "ifc_file_id": "<uuid>"}
    {"action": "cancel"}

Server → Client::

    {"type": "phase",   "phase": "<name>", "status": "running|done|error", ...}
    {"type": "export_complete", "job_id": "<uuid>", "commit_hash": "<sha>"}
    {"type": "cancelled"}
    {"type": "error", "message": "<text>"}
    {"type": "done"}
"""

import logging
import threading

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from core.consumers import CastorConsumerMixin, capture_consumer_errors

logger = logging.getLogger(__name__)


class ExportConsumer(CastorConsumerMixin, AsyncJsonWebsocketConsumer):
    """Streams :class:`ExportReconciliationService.export` phases over WebSocket."""

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
            "ExportConsumer connected: user=%s project=%s",
            self.user.username,
            self.project_id,
        )

    @capture_consumer_errors
    async def disconnect(self, close_code: int) -> None:
        cancel_event = getattr(self, "_cancel_event", None)
        if cancel_event is not None:
            cancel_event.set()
        logger.debug(
            "ExportConsumer disconnected: user=%s code=%s",
            getattr(self.user, "username", "?"),
            close_code,
        )

    @capture_consumer_errors
    async def receive_json(self, content: dict, **kwargs) -> None:
        action = content.get("action")
        if action == "start_export":
            await self._handle_start_export(content)
        elif action == "cancel":
            self._cancel_event.set()
            logger.debug("ExportConsumer: cancel requested by %s", self.user.username)
        else:
            await self.safe_send_json({"type": "error", "message": f"Unknown action: {action}"})

    # ── Private handlers ────────────────────────────────────────────────────

    async def _handle_start_export(self, content: dict) -> None:
        ifc_file_id = (content.get("ifc_file_id") or "").strip()
        if not ifc_file_id:
            await self.safe_send_json({"type": "error", "message": "ifc_file_id is required."})
            return

        self._cancel_event.clear()

        try:
            job_info = await self._run_export(ifc_file_id)
        except Exception as e:
            from facilities.services.export_reconciliation_service import ExportError
            from writeback.services.emitters import CancellationError

            if isinstance(e, CancellationError):
                await self.safe_send_json({"type": "cancelled"})
                return
            if isinstance(e, ExportError):
                # ExportError is a domain failure surfaced to the user — log
                # at warning so it shows up in ErrorLog but doesn't poison the
                # critical-error feed for the operator.
                await self.log_consumer_exception(
                    e, method="_handle_start_export", severity="warning"
                )
                await self.safe_send_json({"type": "error", "message": str(e)})
                await self.safe_send_json({"type": "done"})
                return
            logger.exception("Export consumer error: %s", e)
            await self.log_consumer_exception(e, method="_handle_start_export")
            await self.safe_send_json({"type": "error", "message": str(e)})
            await self.safe_send_json({"type": "done"})
            return

        await self.safe_send_json({"type": "export_complete", **job_info})
        await self.safe_send_json({"type": "done"})

    # Mirrors the writeback ProposalConsumer rationale: `thread_sensitive=False`
    # keeps long-running writer I/O off the single shared sync thread so other
    # tabs' connect()/auth checks don't starve during an in-flight export.
    @sync_to_async(thread_sensitive=False)
    def _run_export(self, ifc_file_id: str) -> dict:
        from environments.models import Project
        from facilities.services.export_reconciliation_service import (
            ExportReconciliationService,
        )
        from ifc_processor.models import IFCFile
        from writeback.services.emitters import WebSocketEmitter

        project = Project.objects.select_related("owner").get(pk=self.project_id)
        ifc_file = IFCFile.objects.select_related("project").get(pk=ifc_file_id, project=project)

        emitter = WebSocketEmitter(
            self.send_json,
            cancel_event=self._cancel_event,
            scope=self.scope,
            consumer_name=f"{type(self).__module__}.{type(self).__name__}",
        )
        svc = ExportReconciliationService(project)
        job = svc.export(ifc_file=ifc_file, user=self.user, emitter=emitter)
        return {
            "job_id": str(job.pk),
            "commit_hash": job.commit_hash,
            "delta_count": job.delta_count,
            "entity_count": job.entity_count,
            "status": job.status,
        }

    @sync_to_async
    def _check_project_access(self) -> bool:
        from environments.models import Project
        from environments.services import ProjectAccessService

        try:
            project = Project.objects.select_related("owner").get(pk=self.project_id)
        except Project.DoesNotExist:
            return False
        # Export rewrites the IFC file — EDITOR+ only.
        return ProjectAccessService.can_modify(self.user, project)


# ─── Work Orders (M3.D) ────────────────────────────────────────────────────


def fm_wo_group_name(project_id) -> str:
    """Channel group name for a project's WO live updates.

    Public helper so the service layer can call ``group_send`` to the same
    name the consumer subscribes to without redefining the convention.
    """
    return f"fm-wo-{project_id}"


class WorkOrderConsumer(CastorConsumerMixin, AsyncJsonWebsocketConsumer):
    """Push-only WebSocket — broadcasts WO transitions to every connected client.

    Joins a per-project channel group on connect; the service layer fans out
    transition events to that group via :func:`fm_wo_group_name`. Mutations
    still go through the HTTP transition endpoint so CSRF, auth, and role
    gating live in one place.
    """

    @capture_consumer_errors
    async def connect(self) -> None:
        self.user = self.scope["user"]
        self.project_id = self.scope["url_route"]["kwargs"]["project_id"]

        if self.user.is_anonymous:
            await self.close(code=4001)
            return

        if not await self._check_project_access():
            await self.close(code=4003)
            return

        self.group_name = fm_wo_group_name(self.project_id)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        logger.debug(
            "WorkOrderConsumer connected: user=%s project=%s",
            self.user.username,
            self.project_id,
        )

    @capture_consumer_errors
    async def disconnect(self, close_code: int) -> None:
        group_name = getattr(self, "group_name", None)
        if group_name is not None and self.channel_layer is not None:
            await self.channel_layer.group_discard(group_name, self.channel_name)

    @capture_consumer_errors
    async def receive_json(self, content: dict, **kwargs) -> None:
        action = content.get("action")
        if action == "ping":
            await self.safe_send_json({"type": "pong"})
        elif action == "subscribe":
            await self.safe_send_json({"type": "subscribed", "project_id": self.project_id})
        else:
            await self.safe_send_json({"type": "error", "message": f"Unknown action: {action}"})

    # Group-message handler — invoked by ``channel_layer.group_send`` with
    # ``{"type": "wo_updated", "payload": {...}}``. Channels dispatches the
    # ``type`` value to a method of the same name (dots replaced by
    # underscores), so service-layer broadcasts use ``"type": "wo_updated"``.
    async def wo_updated(self, event: dict) -> None:
        await self.safe_send_json({"type": "wo.updated", **event["payload"]})

    @sync_to_async
    def _check_project_access(self) -> bool:
        from environments.models import Project
        from environments.services import ProjectAccessService

        try:
            project = Project.objects.select_related("owner").get(pk=self.project_id)
        except Project.DoesNotExist:
            return False
        # READER+ may subscribe — viewers should see live updates even if
        # they cannot fire transitions themselves.
        return ProjectAccessService.can_access(self.user, project)

    # ar_updated — Action Request side push (M4.C). Same channel group as
    # WO updates because the FM Requests page is part of the same workspace.
    # Service layer fires this with ``"type": "ar_updated"`` + an HTML payload.
    async def ar_updated(self, event: dict) -> None:
        await self.safe_send_json({"type": "ar.updated", **event["payload"]})


# ─── Occupant Portal (M4.C) ────────────────────────────────────────────────


def fm_portal_group_name(user_id, project_id) -> str:
    """Channel group for an occupant's portal status updates.

    Per-user, per-project — narrow blast radius. The submitting tenant gets
    AR triage / dismiss / escalate pushes for **their own** requests; other
    occupants on the same project see nothing.
    """
    return f"fm-portal-{user_id}-{project_id}"


class OccupantPortalConsumer(CastorConsumerMixin, AsyncJsonWebsocketConsumer):
    """Push-only WebSocket — broadcasts AR status updates to the submitter."""

    @capture_consumer_errors
    async def connect(self) -> None:
        self.user = self.scope["user"]
        self.project_id = self.scope["url_route"]["kwargs"]["project_id"]

        if self.user.is_anonymous:
            await self.close(code=4001)
            return

        if not await self._check_project_access():
            await self.close(code=4003)
            return

        self.group_name = fm_portal_group_name(self.user.pk, self.project_id)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        logger.debug(
            "OccupantPortalConsumer connected: user=%s project=%s",
            self.user.username,
            self.project_id,
        )

    @capture_consumer_errors
    async def disconnect(self, close_code: int) -> None:
        group_name = getattr(self, "group_name", None)
        if group_name is not None and self.channel_layer is not None:
            await self.channel_layer.group_discard(group_name, self.channel_name)

    @capture_consumer_errors
    async def receive_json(self, content: dict, **kwargs) -> None:
        action = content.get("action")
        if action == "ping":
            await self.safe_send_json({"type": "pong"})
        else:
            await self.safe_send_json({"type": "error", "message": f"Unknown action: {action}"})

    async def ar_updated(self, event: dict) -> None:
        """Server-rendered AR status card pushed by the service layer."""
        await self.safe_send_json({"type": "ar.updated", **event["payload"]})

    @sync_to_async
    def _check_project_access(self) -> bool:
        from environments.models import Project
        from environments.services import ProjectAccessService

        try:
            project = Project.objects.select_related("owner").get(pk=self.project_id)
        except Project.DoesNotExist:
            return False
        return ProjectAccessService.can_access(self.user, project)
