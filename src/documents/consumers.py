# documents/consumers.py
"""
WebSocket consumer for document OCR processing.

OCRConsumer — streams GLM-OCR progress to the browser page by page.

Protocol:
    Client → Server:  {"action": "run_ocr", "document_id": "<uuid>", "force_all_pages": false}
    Server → Client:  {"type": "ocr_progress", "page": 3, "total": 12, "status": "processing"}
    Server → Client:  {"type": "ocr_progress", "page": 3, "total": 12, "status": "done"}
    Server → Client:  {"type": "ocr_complete", "chunks_created": 47, "pages_ocrd": 8}
    Server → Client:  {"type": "ocr_error", "page": 5, "error": "model timeout"}
    Server → Client:  {"type": "ocr_failed", "error": "GLM-OCR model not available"}
    Server → Client:  {"type": "done"}
"""

import logging

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings

logger = logging.getLogger(__name__)


class OCRConsumer(AsyncJsonWebsocketConsumer):
    """
    Async WebSocket consumer that streams OCR processing progress to the browser.

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
            "OCRConsumer connected: user=%s project=%s",
            self.user.username,
            self.project_id,
        )

    async def disconnect(self, close_code: int) -> None:
        logger.debug(
            "OCRConsumer disconnected: user=%s code=%s",
            getattr(self.user, "username", "?"),
            close_code,
        )

    async def receive_json(self, content: dict, **kwargs) -> None:
        action = content.get("action")
        if action == "run_ocr":
            await self._handle_run_ocr(content)
        else:
            await self.send_json({"type": "ocr_failed", "error": f"Unknown action: {action}"})

    # ── Private handlers ───────────────────────────────────────────────────────

    async def _handle_run_ocr(self, content: dict) -> None:
        """Validate the request, run OCR via sync_to_async, and stream results."""
        if not getattr(settings, "GLM_OCR_ENABLED", False):
            await self.send_json({"type": "ocr_failed", "error": "OCR subsystem is disabled."})
            await self.send_json({"type": "done"})
            return

        document_id = content.get("document_id", "").strip()
        if not document_id:
            await self.send_json({"type": "ocr_failed", "error": "document_id is required."})
            await self.send_json({"type": "done"})
            return

        force_all_pages = bool(content.get("force_all_pages", False))

        import asyncio

        loop = asyncio.get_running_loop()

        try:
            result = await self._run_ocr(document_id, force_all_pages, loop)
        except Exception as exc:
            logger.exception("OCR consumer pipeline error: %s", exc)
            await self.send_json({"type": "ocr_failed", "error": str(exc)})
            await self.send_json({"type": "done"})
            return

        await self.send_json(
            {
                "type": "ocr_complete",
                "chunks_created": result.chunks_created,
                "pages_ocrd": result.pages_processed,
            }
        )
        await self.send_json({"type": "done"})

    @sync_to_async
    def _run_ocr(self, document_id: str, force_all_pages: bool, loop):
        """
        Synchronous OCR execution wrapped for async use.

        Resolves the Document, verifies project ownership, checks model availability,
        then calls GLMOCRService.analyze_document() with a progress callback that
        streams page-level events back to the WebSocket.

        Args:
            loop: The running asyncio event loop, captured in the async caller and
                  passed here because get_event_loop() raises RuntimeError in threads.
        """
        import asyncio

        from documents.models import Document
        from documents.services.ocr_service import GLMOCRService

        document = Document.objects.select_related("project").get(
            pk=document_id,
            project_id=self.project_id,
        )

        svc = GLMOCRService()

        if not svc.is_available():
            raise RuntimeError(
                f"GLM-OCR model '{svc._model}' is not available. Run: ollama pull glm-ocr"
            )

        total_pages_ref = [0]  # mutable container for closure

        def progress_callback(page_num: int, total: int, status: str) -> None:
            total_pages_ref[0] = total
            if status == "error":
                coro = self.send_json(
                    {"type": "ocr_error", "page": page_num, "error": "processing error"}
                )
            else:
                coro = self.send_json(
                    {"type": "ocr_progress", "page": page_num, "total": total, "status": status}
                )
            asyncio.run_coroutine_threadsafe(coro, loop)

        return svc.analyze_document(
            document,
            force_all_pages=force_all_pages,
            progress_callback=progress_callback,
        )

    @sync_to_async
    def _check_project_access(self) -> bool:
        from environments.models import Project
        from environments.services import ProjectAccessService

        try:
            project = Project.objects.select_related("owner").get(pk=self.project_id)
            return ProjectAccessService.can_access(self.user, project)
        except Project.DoesNotExist:
            return False
