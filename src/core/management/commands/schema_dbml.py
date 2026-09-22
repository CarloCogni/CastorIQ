# core/management/commands/schema_dbml.py
"""Generate a dbdiagram.io-ready DBML file for Castor's own apps only.

Thin wrapper over django_dbml's `dbml` command that (a) defaults the app list to
Castor's own apps so Django/third-party models are excluded, (b) strips the
descending-order `-` prefix from Meta.indexes fields, which upstream mishandles
(KeyError: '-created_at'), and (c) drops `ref:` lines pointing at excluded
built-in tables (e.g. auth.Group) that would otherwise break dbdiagram.io.
"""

import io
from contextlib import redirect_stdout
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django_dbml.management.commands.dbml import Command as DbmlCommand


class Command(DbmlCommand):
    """Emit DBML for Castor's apps only, working around the descending-index bug."""

    help = "Generate DBML for Castor's apps only (excludes Django built-ins)."

    def handle(self, *app_labels: str, **options: object) -> None:
        """Sanitize indexes, delegate to upstream, then filter dangling refs."""
        self._strip_descending_index_prefixes()
        if not app_labels:
            app_labels = self._local_app_labels()

        # Capture upstream's output so we can post-filter it before writing.
        output_file = options.pop("output_file", None)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            super().handle(*app_labels, **options)
        dbml = self._drop_dangling_refs(buffer.getvalue())

        if output_file:
            Path(output_file).write_text(dbml, encoding="utf-8")
        else:
            self.stdout.write(dbml)

    @staticmethod
    def _drop_dangling_refs(dbml: str) -> str:
        """Remove `ref:` lines whose endpoint table is not defined in this DBML.

        Excluding the built-in apps leaves relations that still point at them
        (e.g. the custom User's M2M to auth.Group). dbdiagram.io reads the
        `auth.` prefix as a schema and rejects the reference, so drop any ref
        whose table is absent from the output."""
        defined = {line.split()[1] for line in dbml.splitlines() if line.startswith("Table ")}
        kept: list[str] = []
        for line in dbml.splitlines():
            if line.startswith("ref: "):
                body = line.removeprefix("ref: ")
                separator = " > " if " > " in body else " - "
                left, right = body.split(separator, 1)
                endpoints = {left.rsplit(".", 1)[0], right.rsplit(".", 1)[0]}
                if not endpoints <= defined:
                    continue
            kept.append(line)
        return "\n".join(kept)

    def get_tl_module_name(self, model: object) -> str:
        """Group/color tables by their owning app label.

        Upstream derives the group from the module path, which mislabels apps
        that split models into a package (e.g. facilities/models/assets.py would
        group under "models"). The app label is the correct, stable choice."""
        return model._meta.app_config.label

    def _local_app_labels(self) -> tuple[str, ...]:
        """Return the labels of apps that live inside the project tree.

        Detected by filesystem location rather than a hardcoded list, so the
        set stays correct as apps are added, renamed, or relabelled (e.g.
        the `scheduling` app whose label is `castor_scheduling`)."""
        base = Path(settings.BASE_DIR).resolve()
        return tuple(
            config.label
            for config in apps.get_app_configs()
            if self._is_local(Path(config.path).resolve(), base)
        )

    @staticmethod
    def _is_local(app_path: Path, base: Path) -> bool:
        """True when the app lives in the project tree and not in site-packages."""
        return app_path.is_relative_to(base) and "site-packages" not in app_path.parts

    def _strip_descending_index_prefixes(self) -> None:
        """Remove leading '-' from Meta.indexes fields so upstream's
        ``_forward_fields_map`` lookup succeeds. This is a one-shot process, so
        mutating the in-memory Index objects is harmless; index direction is not
        rendered by DBML anyway."""
        for model in apps.get_models():
            for index in model._meta.indexes:
                index.fields = [field.removeprefix("-") for field in index.fields]
