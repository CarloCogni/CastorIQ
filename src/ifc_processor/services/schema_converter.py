# ifc_processor/services/schema_converter.py
"""IFC schema migration service — converts legacy IFC files to IFC4."""

import logging
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import ifcopenshell
import ifcopenshell.util.schema
import ifcopenshell.validate

from ifc_processor.models import IFCFile
from ifc_processor.services.processor import IFCProcessingService

logger = logging.getLogger(__name__)


@runtime_checkable
class ProgressEmitter(Protocol):
    """Minimal progress-event protocol — satisfied by WebSocketEmitter and NullEmitter."""

    def emit(
        self,
        phase: str,
        status: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None: ...


class _NullEmitter:
    """Silent emitter used when no emitter is supplied."""

    def emit(self, phase: str, status: str, message: str, detail: dict | None = None) -> None:
        logger.debug("conversion phase=%s status=%s: %s", phase, status, message)


class IFCSchemaConverterService:
    """Migrates an IFCFile from a legacy schema (e.g. IFC2X3) to IFC4.

    Overwrites the file on disk, then re-runs the full processing pipeline
    (parse + embed) so the DB stays in sync.
    """

    TARGET_SCHEMA = "IFC4"

    def __init__(self, ifc_file: IFCFile) -> None:
        self.ifc_file = ifc_file

    def convert(self, emitter: ProgressEmitter | None = None) -> dict:
        """Run conversion. Returns result dict with keys: success, warnings, error.

        Args:
            emitter: Optional progress emitter. Defaults to silent logging.
        """
        em = emitter or _NullEmitter()
        source_schema = self.ifc_file.schema_version or "unknown"
        logger.info(
            "Starting schema conversion %s → %s for file %s",
            source_schema,
            self.TARGET_SCHEMA,
            self.ifc_file.pk,
        )
        try:
            file_path = Path(self.ifc_file.file.path)

            em.emit("open", "running", f"Opening {source_schema} file…")
            ifc_source = ifcopenshell.open(str(file_path))
            source_count = len(list(ifc_source))
            em.emit("open", "done", f"Opened — {source_count} entities found")

            # Migrate entities into a new IFC4 file
            em.emit("migrate", "running", f"Migrating {source_count} entities to IFC4…")
            ifc4 = ifcopenshell.file(schema=self.TARGET_SCHEMA)
            migrator = ifcopenshell.util.schema.Migrator()
            migrated = 0
            for entity in ifc_source:
                try:
                    migrator.migrate(entity, ifc4)
                    migrated += 1
                except Exception as e:
                    logger.debug("Entity migration skipped: %s", e)
            em.emit("migrate", "done", f"Migrated {migrated} entities")

            # Validate
            em.emit("validate", "running", "Validating converted model…")
            validation_logger = ifcopenshell.validate.json_logger()
            ifcopenshell.validate.validate(ifc4, validation_logger)
            warnings = [
                s["message"] for s in validation_logger.statements if s.get("level") == "WARNING"
            ]
            warn_summary = f"{len(warnings)} warning(s)" if warnings else "No issues found"
            em.emit("validate", "done", f"Validation complete — {warn_summary}")

            # Write converted file under new name, then remove the original
            converted_name = f"{file_path.stem}-converted{file_path.suffix}"
            converted_path = file_path.parent / converted_name

            em.emit("write", "running", "Writing converted file to disk…")
            ifc4.write(str(converted_path))
            file_path.unlink()
            logger.info("Converted file written to %s (original removed)", converted_path)
            em.emit("write", "done", f"Saved as {converted_name}")

            # Update DB record to point at the new file
            old_relative = Path(self.ifc_file.file.name)
            self.ifc_file.file.name = str(old_relative.parent / converted_name)
            self.ifc_file.name = converted_name
            self.ifc_file.save(update_fields=["file", "name"])

            # Re-run full pipeline (parse + embed)
            em.emit("reprocess", "running", "Re-indexing file (parse + embed)…")
            self.ifc_file.status = IFCFile.Status.PENDING
            self.ifc_file.error_message = ""
            self.ifc_file.save(update_fields=["status", "error_message"])

            processor = IFCProcessingService(self.ifc_file)
            success = processor.run_pipeline()

            if not success:
                em.emit("reprocess", "error", "Re-indexing failed")
                return {
                    "success": False,
                    "error": self.ifc_file.error_message or "Re-processing failed after conversion",
                }

            em.emit("reprocess", "done", f"Re-indexed — {self.ifc_file.entity_count} entities")
            return {
                "success": True,
                "warnings": warnings,
                "entity_count": self.ifc_file.entity_count,
            }

        except Exception as e:
            logger.exception("Schema conversion failed for %s: %s", self.ifc_file.pk, e)
            return {"success": False, "error": str(e)}
