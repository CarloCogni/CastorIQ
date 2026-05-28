# facilities/models/explore.py
"""Explore module domain models.

Persistence layer for the Explore sub-tab — the floor-plan + photo / 360°
viewer embedded as an iframe under ``facilities/static/facilities/explore/``.
The JavaScript module is the source of truth at runtime; on every change it
emits ``STATE_CHANGED`` via postMessage, and the host page round-trips the
working set through these models so it survives across browsers / devices.

Four models cover the working set:

``ExplorePhase``
    Per-project palette of room-state phases (Construction / Fit-out /
    Occupied by default; users can rename / recolour / add). A point's pin
    colour comes from its assigned phase.

``ExploreFloorPlan``
    Optional uploaded plan image attached to an IFC building storey. If no
    plan is uploaded, the module shows its empty state for that floor.

``ExplorePoint``
    A user-placed pin on a floor plan. Coordinates are percentages of the
    plan image box so they hold at any zoom. Can be linked to an IFC space
    (``ifc_entity``) for cross-tool focus via GlobalID.

``ExploreMedia``
    A photo or 360° panorama attached to a point. Versioned by upload time
    so the user can browse a room's photo history and compare snapshots
    side-by-side.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models import UUIDModel
from environments.models import Project
from ifc_processor.models import IFCEntity, IFCSpatialElement

DEFAULT_PHASES = (
    ("Construction", "#d4903a", 10),
    ("Fit-out", "#4fc4cf", 20),
    ("Occupied", "#42b880", 30),
)


class ExplorePhase(UUIDModel):
    """A named room-state phase (with a pin colour) scoped to a project.

    Mirrors the client-side phase list in Pavla's ``state.js``. The first
    access for a project seeds the three defaults via ``ExplorePhaseService``.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="explore_phases",
        verbose_name="Project",
    )
    name = models.CharField(
        max_length=80,
        verbose_name="Name",
        help_text="Phase label (e.g. 'Construction', 'Fit-out', 'Occupied')",
    )
    color = models.CharField(
        max_length=20,
        default="#8a8da6",
        verbose_name="Pin Colour",
        help_text="CSS colour for pins in this phase",
    )
    position = models.PositiveSmallIntegerField(
        default=0,
        verbose_name="Position",
        help_text="Display order in the palette",
    )

    class Meta:
        ordering = ["position", "name"]
        verbose_name = "Explore Phase"
        verbose_name_plural = "Explore Phases"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"],
                name="unique_explore_phase_per_project",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.color})"


class ExploreFloorPlan(UUIDModel):
    """An uploaded floor-plan image attached to an IFC building storey.

    OneToOne with the storey so each floor has at most one plan. ``knockout``
    persists Pavla's white-background removal toggle; ``original_image``
    keeps the pre-knockout image so the user can revert.
    """

    storey = models.OneToOneField(
        IFCSpatialElement,
        on_delete=models.CASCADE,
        related_name="explore_floor_plan",
        verbose_name="Storey",
        help_text="The IfcBuildingStorey this plan belongs to",
    )
    image = models.ImageField(
        upload_to="facilities/explore/plans/%Y/%m/",
        verbose_name="Plan Image",
    )
    original_image = models.ImageField(
        upload_to="facilities/explore/plans/%Y/%m/",
        null=True,
        blank=True,
        verbose_name="Original Plan Image",
        help_text="Pre-knockout image (kept so the user can revert)",
    )
    knockout = models.BooleanField(
        default=False,
        verbose_name="White Knock-out Applied",
        help_text="True when the user has stripped the white background",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Uploaded By",
    )

    class Meta:
        verbose_name = "Explore Floor Plan"
        verbose_name_plural = "Explore Floor Plans"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Floor plan: {self.storey}"


class ExplorePoint(UUIDModel):
    """A user-placed pin on a floor plan.

    Coordinates are stored as percentages of the plan image box (Pavla's
    convention) so they hold at any size. ``client_id`` is the in-iframe
    id Pavla's module generates (``pt-xxxxxxxx``); we keep it so server
    state round-trips cleanly with the in-memory state on the client.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="explore_points",
        verbose_name="Project",
    )
    floor = models.ForeignKey(
        IFCSpatialElement,
        on_delete=models.CASCADE,
        related_name="explore_points",
        verbose_name="Floor (Storey)",
        help_text="The IfcBuildingStorey this point sits on",
    )
    client_id = models.CharField(
        max_length=64,
        db_index=True,
        verbose_name="Client ID",
        help_text="Stable in-iframe id from Pavla's state.js (pt-xxxxxxxx)",
    )
    label = models.CharField(
        max_length=80,
        blank=True,
        verbose_name="Label",
    )

    class Kind(models.TextChoices):
        PHOTO = "photo", "Photo"
        CAMERA = "camera", "Camera"
        SENSOR = "sensor", "Sensor"
        CUSTOM = "custom", "Custom"

    kind = models.CharField(
        max_length=16,
        choices=Kind.choices,
        default=Kind.PHOTO,
        verbose_name="Kind",
        help_text="Point kind — photo points are numbered; others show a symbol",
    )
    symbol = models.CharField(
        max_length=8,
        blank=True,
        verbose_name="Symbol",
        help_text="Glyph for a custom point (one of the preset symbols)",
    )
    x_percent = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        verbose_name="X (% of plan)",
        help_text="0–100, percent of plan image width",
    )
    y_percent = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        verbose_name="Y (% of plan)",
        help_text="0–100, percent of plan image height",
    )
    phase = models.ForeignKey(
        ExplorePhase,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="points",
        verbose_name="Phase",
    )
    ifc_entity = models.ForeignKey(
        IFCEntity,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="explore_points",
        verbose_name="IFC Entity",
        help_text="The IfcSpace this point links to (for cross-tool focus)",
    )
    table_links = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Linked Tables",
        help_text="List of {key, filterBy} per Pavla's PointTable shape",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Created By",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Explore Point"
        verbose_name_plural = "Explore Points"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "client_id"],
                name="unique_explore_point_per_project",
            ),
        ]
        indexes = [
            models.Index(fields=["floor", "phase"]),
            models.Index(fields=["project", "floor"]),
        ]

    def __str__(self) -> str:
        return self.label or f"Point {self.client_id}"


class ExploreMedia(UUIDModel):
    """A photo or 360° panorama attached to an explore point.

    Pavla's iframe stores incoming photos as base64 data URLs in
    ``localStorage``; on round-trip the host page extracts those and writes
    actual files here. The next ``SET_USER_STATE`` from the host swaps the
    data URLs for real URLs in the in-iframe state.
    """

    class MediaType(models.TextChoices):
        PHOTO = "photo", "Photo"
        PANO_360 = "360", "360° Panorama"

    point = models.ForeignKey(
        ExplorePoint,
        on_delete=models.CASCADE,
        related_name="media",
        verbose_name="Point",
    )
    client_id = models.CharField(
        max_length=64,
        db_index=True,
        verbose_name="Client ID",
        help_text="Stable in-iframe id from Pavla's state.js (m-xxxxxxxx)",
    )
    media_type = models.CharField(
        max_length=16,
        choices=MediaType.choices,
        default=MediaType.PHOTO,
        verbose_name="Media Type",
    )
    file = models.ImageField(
        upload_to="facilities/explore/media/%Y/%m/",
        verbose_name="File",
    )
    taken_on = models.DateField(
        null=True,
        blank=True,
        verbose_name="Taken On",
    )
    taken_at = models.TimeField(
        null=True,
        blank=True,
        verbose_name="Taken At",
    )
    phase = models.ForeignKey(
        ExplorePhase,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="media",
        verbose_name="Phase",
    )
    label = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="Label",
    )
    code = models.CharField(
        max_length=80,
        blank=True,
        verbose_name="Photo Code",
        help_text="Free-text photo code (e.g. 'IMG_4821')",
    )
    description = models.TextField(
        blank=True,
        verbose_name="Description",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Uploaded By",
    )

    class Meta:
        ordering = ["-taken_on", "-created_at"]
        verbose_name = "Explore Media"
        verbose_name_plural = "Explore Media"
        constraints = [
            models.UniqueConstraint(
                fields=["point", "client_id"],
                name="unique_explore_media_per_point",
            ),
        ]
        indexes = [
            models.Index(fields=["point", "media_type"]),
        ]

    def __str__(self) -> str:
        return self.label or f"{self.get_media_type_display()} {self.client_id}"


class ExploreFloorSettings(UUIDModel):
    """Per-storey Explore view preferences (independent of whether a plan exists).

    ``hidden`` lets an editor drop an IFC storey from the Explore floor switcher
    when they don't want to work with it there. This is an Explore-side display
    preference only — it does NOT modify the IFC model.
    """

    storey = models.OneToOneField(
        IFCSpatialElement,
        on_delete=models.CASCADE,
        related_name="explore_settings",
        verbose_name="Storey",
        help_text="The IfcBuildingStorey these Explore settings apply to",
    )
    hidden = models.BooleanField(
        default=False,
        verbose_name="Hidden in Explore",
        help_text="When true, this storey is omitted from the Explore floor switcher",
    )

    class Meta:
        verbose_name = "Explore Floor Setting"
        verbose_name_plural = "Explore Floor Settings"

    def __str__(self) -> str:
        return f"Settings: {self.storey} (hidden={self.hidden})"
