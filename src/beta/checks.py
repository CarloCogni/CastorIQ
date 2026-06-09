# beta/checks.py
"""
Deploy-time guards for the beta vetting funnel.

These are Django system checks — they run automatically before `migrate`,
`runserver`, `collectstatic`, etc. via `manage.py check`. The point is to
catch operator mistakes (sample-project fixtures missing from the image)
before any user signs up and lands on an empty Sample Project.

Severity is keyed off `settings.DEBUG`:
- DEBUG=False (production-like): missing fixtures are hard errors and block
  the management command — `migrate` fails, the deploy aborts.
- DEBUG=True (local dev): warnings only. Engineers running the dev server
  without the multi-MB IFC binaries still get a clear signal but no block.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.checks import Error, Warning, register

# Avoid importing the management command at module load — Django imports
# checks during settings boot. Duplicate the constants here, kept in sync
# with core/management/commands/provision_sample_project.py.
_SAMPLE_FIXTURE_NAMES = ("architectural.ifc", "structural.ifc")


def _fixtures_root() -> Path:
    return Path(settings.BASE_DIR).parent / "fixtures" / "sample-project"


@register("beta")
def check_sample_project_fixtures(app_configs, **kwargs):
    """Flag missing sample-project IFC fixtures at `manage.py check` time."""
    missing = [_fixtures_root() / name for name in _SAMPLE_FIXTURE_NAMES]
    missing = [p for p in missing if not p.exists()]
    if not missing:
        return []

    msg_lines = [
        "Sample-project IFC fixtures are missing on disk:",
        *(f"  - {p}" for p in missing),
        "",
        "Without these, `provision_sample_project` will fail and newly-approved",
        "beta users will land on an empty Sample Project. See",
        "fixtures/sample-project/PROVENANCE.md for sourcing instructions and",
        "drop the files in before building the image.",
    ]
    msg = "\n".join(msg_lines)

    if settings.DEBUG:
        return [Warning(msg, id="beta.W001")]
    return [Error(msg, id="beta.E001")]
