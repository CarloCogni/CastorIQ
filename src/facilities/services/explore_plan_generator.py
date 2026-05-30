# facilities/services/explore_plan_generator.py
"""IFC-to-2D plan generation for the Explore module.

The Explore module lets a user generate a top-down floor plan of a single
:class:`IFCSpatialElement` building storey by slicing the IFC model with a
horizontal plane at a chosen height above the storey's elevation. The
output is a PNG written to :attr:`ExploreFloorPlan.generated_image` and
shown by the viewer iframe when ``image_source == 'generated'``.

How the slice works
-------------------

1. Open the project's IFC file via ``ifcopenshell.open`` and find the
   ``IfcBuildingStorey`` whose ``GlobalId`` matches our spatial element.
2. Walk the storey's spatially-contained elements; keep only those whose
   ``is_a()`` matches one of the user-chosen kinds (walls, columns/beams,
   doors/windows, stairs/railings).
3. For each element, ask ``ifcopenshell.geom.create_shape`` for a world-
   coordinate triangulation. For every triangle we test whether the
   horizontal plane ``z = storey.elevation + cut_height`` crosses it — if
   so, the two edge–plane intersections form one segment of the outline.
4. We collect every such segment, fit the bounding box to a fixed-width
   canvas, project (X, Y) into pixels (flipping Y so north stays up), and
   draw each segment with Pillow ``ImageDraw``.

Units are tricky: IFC files use whatever ``IfcUnitAssignment`` declares
(usually mm in Europe, m in some North American models, or even feet).
We always convert the user-input cut height from metres to the IFC unit
via ``ifcopenshell.util.unit.calculate_unit_scale`` so the slice plane
sits at the right elevation regardless of source-file units.

This is a pure-Python slice (no PythonOCC needed) — only ``ifcopenshell``
for parsing/triangulation and Pillow for rasterisation. Speed scales with
total triangle count for the selected elements; expect 5–30 s for a
mid-size building storey.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from django.core.files.base import ContentFile
from PIL import Image, ImageDraw

from facilities.models.explore import ExploreFloorPlan
from ifc_processor.models import IFCSpatialElement

logger = logging.getLogger(__name__)

# Map of UI 'kind' tokens to IFC entity type names. Each kind expands to
# every concrete subtype + the bare type (some old IFC2x3 files use the
# IfcWallStandardCase / IfcSlabStandardCase concrete forms).
KIND_TO_IFC_TYPES = {
    "walls": ("IfcWall", "IfcWallStandardCase", "IfcCurtainWall"),
    "columns_beams": ("IfcColumn", "IfcBeam", "IfcMember"),
    "doors_windows": ("IfcDoor", "IfcWindow"),
    "stairs_railings": (
        "IfcStair",
        "IfcStairFlight",
        "IfcRailing",
        "IfcRamp",
        "IfcRampFlight",
    ),
}

# Always present in the output regardless of user input — without walls the
# plan has no visible structure.
DEFAULT_KINDS = ("walls",)

# Render config: white sheet, black lines, generous canvas so a single
# wall doesn't end up 2 px thick after downscaling in the viewer.
CANVAS_WIDTH_PX = 1600
CANVAS_MARGIN_PX = 60
STROKE_WIDTH_PX = 2
BG_COLOR = (255, 255, 255)
FG_COLOR = (12, 12, 12)
MIN_BBOX_M = 0.5  # guard against zero-sized slices (cut plane misses everything)


@dataclass(frozen=True)
class PlanGenerationResult:
    """Bundle returned by :func:`generate_plan_png` for the view to persist."""

    content: ContentFile
    cut_height_mm: int
    included_kinds: list[str]
    segment_count: int
    bounds_m: tuple[float, float, float, float]  # (xmin, ymin, xmax, ymax) in metres
    triangle_count: int


class PlanGenerationError(RuntimeError):
    """Raised when the IFC slice cannot be produced — surfaced as a toast."""


def generate_plan_png(
    storey: IFCSpatialElement,
    cut_height_m: float,
    included_kinds: list[str] | None,
) -> PlanGenerationResult:
    """Produce a PNG of the slice of ``storey`` at the requested cut height.

    ``cut_height_m`` is the height in metres above the storey's elevation
    where the horizontal cut plane sits (1.2 m is the architectural default
    so doors and windows are captured). ``included_kinds`` is a list of
    kind tokens; walls are always included even if not requested.
    """
    if cut_height_m <= 0:
        raise PlanGenerationError("Cut height must be greater than 0.")
    if cut_height_m > 50:
        raise PlanGenerationError("Cut height looks unreasonable (> 50 m).")

    # Lazy import: ifcopenshell is heavy and only needed when the user
    # actually presses Generate (parser already pulls it in elsewhere, but
    # we keep the import inside the function so unit tests / migrations
    # that touch this module don't pay the cost.)
    try:
        import ifcopenshell  # noqa: PLC0415
        import ifcopenshell.geom  # noqa: PLC0415
        import ifcopenshell.util.unit  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover — ifcopenshell is in deps
        raise PlanGenerationError(
            "ifcopenshell is not installed on this server."
        ) from exc

    kinds = _normalize_kinds(included_kinds)
    ifc_types = _ifc_types_for_kinds(kinds)
    if not ifc_types:
        raise PlanGenerationError("Pick at least one element kind to include.")

    ifc_file_path = _resolve_ifc_path(storey)
    try:
        ifc = ifcopenshell.open(ifc_file_path)
    except Exception as exc:  # pragma: no cover — depends on file health
        raise PlanGenerationError(
            f"Could not open IFC file: {exc}"
        ) from exc

    # Unit scale: factor that turns IFC values into metres. mm-based files
    # return 0.001 here; metre files return 1.0; feet files ~0.3048.
    unit_scale_to_m = ifcopenshell.util.unit.calculate_unit_scale(ifc)
    if not unit_scale_to_m or unit_scale_to_m <= 0:
        unit_scale_to_m = 1.0
    logger.debug("IFC unit scale to metres: %s", unit_scale_to_m)

    # Find the IfcBuildingStorey by GlobalId — our DB row's entity.global_id
    storey_gid = storey.entity.global_id if storey.entity_id else None
    if not storey_gid:
        raise PlanGenerationError("Storey has no IFC GlobalId.")
    try:
        ifc_storey = ifc.by_guid(storey_gid)
    except RuntimeError as exc:
        raise PlanGenerationError(
            f"Storey {storey_gid} not found in IFC file."
        ) from exc
    if ifc_storey is None or not ifc_storey.is_a("IfcBuildingStorey"):
        raise PlanGenerationError(
            f"Storey {storey_gid} is not an IfcBuildingStorey in the IFC file."
        )

    # Storey elevation in IFC units → metres
    raw_elev = getattr(ifc_storey, "Elevation", None) or 0.0
    storey_elev_m = float(raw_elev) * unit_scale_to_m
    cut_plane_z_m = storey_elev_m + cut_height_m
    logger.info(
        "Generating plan for storey %s — elevation %.3f m, cut %.3f m → plane Z=%.3f m",
        storey_gid,
        storey_elev_m,
        cut_height_m,
        cut_plane_z_m,
    )

    # Gather elements contained in this storey (IfcRelContainedInSpatialStructure)
    contained = _collect_contained_elements(ifc_storey, ifc_types)
    if not contained:
        raise PlanGenerationError(
            "No matching elements found in this storey — try other element kinds."
        )

    # ifcopenshell.geom configuration: world coords means the triangulation
    # is already placed correctly relative to project origin, which is what
    # the cut plane expects.
    settings = ifcopenshell.geom.settings()
    try:
        settings.set(settings.USE_WORLD_COORDS, True)
    except Exception:
        # Newer ifcopenshell APIs renamed the constant; ignore if absent.
        pass

    segments_m: list[tuple[tuple[float, float], tuple[float, float]]] = []
    triangle_count = 0
    failed_shapes = 0

    for element in contained:
        try:
            shape = ifcopenshell.geom.create_shape(settings, element)
        except Exception as exc:  # noqa: BLE001
            failed_shapes += 1
            logger.debug(
                "Skipping %s %s — shape failed: %s",
                element.is_a(),
                getattr(element, "GlobalId", "?"),
                exc,
            )
            continue
        geom = getattr(shape, "geometry", shape)
        verts_flat = list(geom.verts)
        faces_flat = list(geom.faces)
        if not verts_flat or not faces_flat:
            continue
        # Pack flat lists to triples once — avoid per-triangle indexing churn.
        verts = [
            (verts_flat[i] * unit_scale_to_m,
             verts_flat[i + 1] * unit_scale_to_m,
             verts_flat[i + 2] * unit_scale_to_m)
            for i in range(0, len(verts_flat), 3)
        ]
        for i in range(0, len(faces_flat), 3):
            a, b, c = verts[faces_flat[i]], verts[faces_flat[i + 1]], verts[faces_flat[i + 2]]
            triangle_count += 1
            seg = _intersect_triangle_with_plane(a, b, c, cut_plane_z_m)
            if seg is not None:
                segments_m.append(seg)

    if failed_shapes:
        logger.info("Skipped %d shapes that failed to triangulate", failed_shapes)
    if not segments_m:
        raise PlanGenerationError(
            "Cut plane did not intersect any element. "
            "Try a different cut height (e.g. 1.0 m) or include more element kinds."
        )

    # Bounding box in metres
    xs = [p[0] for seg in segments_m for p in seg]
    ys = [p[1] for seg in segments_m for p in seg]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    span_x = max(xmax - xmin, MIN_BBOX_M)
    span_y = max(ymax - ymin, MIN_BBOX_M)

    # Fit to canvas; preserve aspect ratio.
    usable_w = CANVAS_WIDTH_PX - 2 * CANVAS_MARGIN_PX
    scale = usable_w / span_x
    canvas_w = int(round(span_x * scale + 2 * CANVAS_MARGIN_PX))
    canvas_h = int(round(span_y * scale + 2 * CANVAS_MARGIN_PX))
    # Sanity floor on height (e.g. long thin corridors) so the image isn't
    # one pixel tall.
    canvas_h = max(canvas_h, 200)

    img = Image.new("RGB", (canvas_w, canvas_h), BG_COLOR)
    draw = ImageDraw.Draw(img)
    for (x0, y0), (x1, y1) in segments_m:
        px0 = CANVAS_MARGIN_PX + (x0 - xmin) * scale
        py0 = canvas_h - CANVAS_MARGIN_PX - (y0 - ymin) * scale
        px1 = CANVAS_MARGIN_PX + (x1 - xmin) * scale
        py1 = canvas_h - CANVAS_MARGIN_PX - (y1 - ymin) * scale
        draw.line([(px0, py0), (px1, py1)], fill=FG_COLOR, width=STROKE_WIDTH_PX)

    bio = io.BytesIO()
    img.save(bio, format="PNG", optimize=True)
    bio.seek(0)
    filename = f"plan_{storey.pk}.png"
    return PlanGenerationResult(
        content=ContentFile(bio.read(), name=filename),
        cut_height_mm=int(round(cut_height_m * 1000)),
        included_kinds=list(kinds),
        segment_count=len(segments_m),
        bounds_m=(xmin, ymin, xmax, ymax),
        triangle_count=triangle_count,
    )


# ── Internals ────────────────────────────────────────────────────────────


def _resolve_ifc_path(storey: IFCSpatialElement) -> str:
    """Return the on-disk path to the IFC file backing this storey."""
    if not storey.ifc_file_id:
        raise PlanGenerationError("Storey is not linked to an IFC file.")
    file_field = storey.ifc_file.file
    if not file_field:
        raise PlanGenerationError("IFC file for this storey has no stored file.")
    try:
        return file_field.path
    except (ValueError, NotImplementedError) as exc:
        raise PlanGenerationError(
            "IFC file is not stored on the local filesystem."
        ) from exc


def _normalize_kinds(raw: list[str] | None) -> tuple[str, ...]:
    """Clamp the requested kind tokens to known ones; walls are always on."""
    selected = set(DEFAULT_KINDS)
    for token in raw or []:
        if token in KIND_TO_IFC_TYPES:
            selected.add(token)
    # Preserve a stable order so the JSON shape on the DB row is comparable.
    order = ("walls", "columns_beams", "doors_windows", "stairs_railings")
    return tuple(k for k in order if k in selected)


def _ifc_types_for_kinds(kinds: tuple[str, ...]) -> frozenset[str]:
    """Flatten kind tokens to the IFC entity type names we filter by."""
    out: set[str] = set()
    for kind in kinds:
        out.update(KIND_TO_IFC_TYPES.get(kind, ()))
    return frozenset(out)


def _collect_contained_elements(ifc_storey, ifc_types: frozenset[str]) -> list:
    """Walk ``IfcRelContainedInSpatialStructure`` rels off the storey.

    Doors and windows are usually nested in walls via ``IfcRelAggregates``
    or ``IfcRelVoidsElement`` rather than directly contained, so we also
    recurse into aggregates one level deep — enough to surface openings
    while keeping the walk cheap.
    """
    found: list = []
    visited: set[int] = set()

    def _consider(element) -> None:
        if id(element) in visited:
            return
        visited.add(id(element))
        if element.is_a() in ifc_types:
            found.append(element)
        # Look one level into aggregates (e.g. IfcCurtainWall → IfcPlate)
        for rel in getattr(element, "IsDecomposedBy", []) or []:
            for child in getattr(rel, "RelatedObjects", []) or []:
                if child.is_a() in ifc_types:
                    found.append(child)

    for rel in getattr(ifc_storey, "ContainsElements", []) or []:
        for el in getattr(rel, "RelatedElements", []) or []:
            _consider(el)
            # Walls have their openings (doors/windows) as voided elements;
            # those openings carry a HasFillings relation back to the actual
            # IfcDoor / IfcWindow instance.
            for void_rel in getattr(el, "HasOpenings", []) or []:
                opening = getattr(void_rel, "RelatedOpeningElement", None)
                if opening is None:
                    continue
                for fill_rel in getattr(opening, "HasFillings", []) or []:
                    filler = getattr(fill_rel, "RelatedBuildingElement", None)
                    if filler is not None and filler.is_a() in ifc_types:
                        _consider(filler)

    return found


def _intersect_triangle_with_plane(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
    c: tuple[float, float, float],
    z: float,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Intersect a 3D triangle with the horizontal plane ``z = const``.

    Returns the resulting 2D line segment (in the XY plane, in metres) or
    ``None`` when the triangle lies wholly above / below the plane or only
    touches it at a single vertex. Triangles lying *on* the plane are
    ignored — they would otherwise emit each of their three edges as a
    "slice" and create a dense outline around horizontal slabs that we
    don't render at this layer.
    """
    za, zb, zc = a[2], b[2], c[2]
    # All on one side → no intersection (use tiny epsilon for numerical safety).
    eps = 1e-9
    sides = [za - z, zb - z, zc - z]
    if all(s > eps for s in sides) or all(s < -eps for s in sides):
        return None
    # Planar triangle (rare, e.g. flat slab cap) — skip.
    if all(abs(s) < eps for s in sides):
        return None

    pts: list[tuple[float, float]] = []
    edges = ((a, b), (b, c), (c, a))
    for p, q in edges:
        zp, zq = p[2], q[2]
        # Edge crosses the plane (strict crossing)
        if (zp - z) * (zq - z) < 0:
            t = (z - zp) / (zq - zp)
            pts.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
        elif abs(zp - z) < eps:
            # Vertex exactly on the plane — count it once.
            pts.append((p[0], p[1]))

    # Deduplicate the rare case where a vertex was tagged twice.
    deduped: list[tuple[float, float]] = []
    for pt in pts:
        if not any(abs(pt[0] - q[0]) < 1e-6 and abs(pt[1] - q[1]) < 1e-6 for q in deduped):
            deduped.append(pt)
    if len(deduped) < 2:
        return None
    return (deduped[0], deduped[1])


def apply_generated_plan(
    plan: ExploreFloorPlan,
    result: PlanGenerationResult,
    user,
) -> None:
    """Persist a generation result onto the floor-plan row.

    Switches ``image_source`` to ``GENERATED`` so the viewer picks the new
    image up immediately; the uploaded image (if any) is preserved so the
    user can flip back via the source pills.
    """
    from django.utils import timezone

    plan.generated_image.save(result.content.name, result.content, save=False)
    plan.image_source = ExploreFloorPlan.ImageSource.GENERATED
    plan.cut_height_mm = result.cut_height_mm
    plan.included_kinds = result.included_kinds
    plan.generated_at = timezone.now()
    if user and getattr(user, "is_authenticated", False) and not plan.uploaded_by_id:
        plan.uploaded_by = user
    plan.save()
