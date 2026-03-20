# core/management/commands/compress_dropzone.py
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                  CASTOR — compress_dropzone Management Command               ║
║           Zero-friction staging environment for LLM context assembly         ║
╚══════════════════════════════════════════════════════════════════════════════╝

PURPOSE:
    Provides a zero-configuration command to compile and compress arbitrary
    developer files (scripts, scratchpads, raw data) placed into a dedicated
    staging directory (`.castor/dropzone/`).

WORKFLOW:
    1. Drop any text-based files into `[PROJECT_ROOT]/.castor/dropzone/`.
    2. Run `python manage.py compress_dropzone`.
    3. The command strips comments (for .py files), collapses whitespace,
       and merges everything into `[PROJECT_ROOT]/.castor/_compressed_dropzone.txt`.
    4. Paste the output into your LLM session.

VERSION CONTROL STRATEGY:
    To ensure team members at Castoe understand this workflow without
    cluttering the repo with personal staging files, commit a `.gitignore`
    inside `.castor/` with the following:

        *
        !.gitignore
        !dropzone/
        !dropzone/.gitkeep
"""

import logging
import os
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

# DRY: Reusing the token and parsing utilities already established in Castor
from core.management.commands.dump_context import (
    collapse_blank_lines,
    estimate_tokens,
    format_tokens,
    strip_comments,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Django management command to compress developer staging files for LLM context.
    """

    help = "Compiles and compresses arbitrary files dropped into .castor/dropzone/"

    def handle(self, *args: Any, **options: Any) -> None:
        """
        Execute the command pipeline: resolve paths, validate state, process files, and output.
        """
        project_root: Path = settings.BASE_DIR.parent
        castor_dir: Path = project_root / ".castor"
        dropzone_dir: Path = castor_dir / "dropzone"
        output_file: Path = castor_dir / "_compressed_dropzone.txt"

        if not self._ensure_dropzone_exists(dropzone_dir):
            return

        files_to_process = self._gather_files(dropzone_dir)
        if not files_to_process:
            logger.info("No files found in %s to compress.", dropzone_dir.relative_to(project_root))
            return

        self._process_and_write(files_to_process, dropzone_dir, output_file, project_root)

    def _ensure_dropzone_exists(self, dropzone_dir: Path) -> bool:
        """
        Verify the dropzone directory exists, creating it if necessary.
        """
        if dropzone_dir.exists():
            return True

        dropzone_dir.mkdir(parents=True, exist_ok=True)
        logger.warning(
            "Created missing dropzone directory at %s. Drop files there and rerun.", dropzone_dir
        )
        return False

    def _gather_files(self, dropzone_dir: Path) -> list[Path]:
        """
        Traverse the dropzone directory and collect all target files, ignoring hidden ones.
        """
        target_files: list[Path] = []
        for root, _, files in os.walk(dropzone_dir):
            for file in sorted(files):
                if file.startswith("."):
                    continue
                target_files.append(Path(root) / file)

        return target_files

    def _process_and_write(
        self, files: list[Path], dropzone_dir: Path, output_file: Path, project_root: Path
    ) -> None:
        """
        Read, compress, and aggregate files, then write the result to the output file.
        """
        logger.info("Scanning Dropzone: %s", dropzone_dir.relative_to(project_root))
        sections: list[str] = []
        file_count: int = 0

        for file_path in files:
            rel_path = file_path.relative_to(project_root)
            content = self._read_and_compress_file(file_path)

            line_count = content.count("\n")
            token_est = estimate_tokens(content)

            sections.append(
                f"\n--- DROPZONE: {rel_path.name} ({line_count} lines, ~{format_tokens(token_est)} tok) ---\n\n"
                f"{content}\n"
            )
            file_count += 1
            logger.info("Processed %s", file_path.name)

        full_output = "\n".join(sections)
        total_tokens = estimate_tokens(full_output)

        self._write_output(output_file, full_output, file_count, total_tokens, project_root)

    def _read_and_compress_file(self, file_path: Path) -> str:
        """
        Read a file's content and apply token-saving compressions based on file type.
        """
        try:
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except OSError as e:
            logger.error("Failed to read %s: %s", file_path.name, e)
            return f"# Error reading: {e}"

        _, ext = os.path.splitext(file_path.name)
        if ext.lower() == ".py":
            content = strip_comments(content)

        return collapse_blank_lines(content)

    def _write_output(
        self,
        output_file: Path,
        content: str,
        file_count: int,
        total_tokens: int,
        project_root: Path,
    ) -> None:
        """
        Write the aggregated string to the destination file.
        """
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(content)

            logger.info(
                "Successfully compressed %d files (~%s tokens total). Saved to: %s",
                file_count,
                format_tokens(total_tokens),
                output_file.relative_to(project_root),
            )
        except OSError as e:
            logger.error("Error writing output file: %s", e)
