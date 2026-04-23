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

logger = logging.getLogger(__name__)


class ExportConsumer(AsyncJsonWebsocketConsumer):
    """Streams :class:`ExportReconciliationService.export` phases over WebSocket."""

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

    async def disconnect(self, close_code: int) -> None:
        cancel_event = getattr(self, "_cancel_event", None)
        if cancel_event is not None:
            cancel_event.set()
        logger.debug(
            "ExportConsumer disconnected: user=%s code=%s",
            getattr(self.user, "username", "?"),
            close_code,
        )

    async def receive_json(self, content: dict, **kwargs) -> None:
        action = content.get("action")
        if action == "start_export":
            await self._handle_start_export(content)
        elif action == "cancel":
            self._cancel_event.set()
            logger.debug("ExportConsumer: cancel requested by %s", self.user.username)
        else:
            await self.send_json({"type": "error", "message": f"Unknown action: {action}"})

    # ── Private handlers ────────────────────────────────────────────────────

    async def _handle_start_export(self, content: dict) -> None:
        ifc_file_id = (content.get("ifc_file_id") or "").strip()
        if not ifc_file_id:
            await self.send_json({"type": "error", "message": "ifc_file_id is required."})
            return

        self._cancel_event.clear()

        try:
            job_info = await self._run_export(ifc_file_id)
        except Exception as e:
            from facilities.services.export_reconciliation_service import ExportError
            from writeback.services.emitters import CancellationError

            if isinstance(e, CancellationError):
                await self.send_json({"type": "cancelled"})
                return
            if isinstance(e, ExportError):
                await self.send_json({"type": "error", "message": str(e)})
                await self.send_json({"type": "done"})
                return
            logger.exception("Export consumer error: %s", e)
            await self.send_json({"type": "error", "message": str(e)})
            await self.send_json({"type": "done"})
            return

        await self.send_json({"type": "export_complete", **job_info})
        await self.send_json({"type": "done"})

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

        emitter = WebSocketEmitter(self.send_json, cancel_event=self._cancel_event)
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
