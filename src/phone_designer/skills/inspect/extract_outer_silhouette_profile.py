"""extract_outer_silhouette_profile — atomic, read-only (phase-1, 2026-06-14).

PILLAR FREEFORM headline detector. Recover the TRUE extruded outer outline of a
prismatic-but-non-rectangular part (rounded plate, D-shaped chassis, oval boss
plate) so box-mode reconstruction can extrude that real silhouette instead of a
bbox-sized rectangular block.

Motivation
----------
pythonocc__11752 is a 1280×144×133 body whose cross-section, swept along X, is a
ROUNDED ~92 mm outline — not a 144 mm rectangle. The default box base wraps the
bbox, so its corners sit ~70 mm away from the rounded surface (box-mode honest
hausdorff 569 mm, vol_delta 98.6 %). The body IS prismatic along X; we just need
to recover the swept profile.

Algorithm
---------
1. bbox + the three candidate prismatic axes (X / Y / Z). The longest extent is
   tried first (a prism is usually long along its sweep axis).
2. For each candidate axis, recover the planar cross-section as ONE closed loop
   (``recover_section_loop`` from ``_section_curve.py``) at 25 / 50 / 75 % along
   the axis through the body centroid.
3. CONSTANT-CROSS-SECTION test — the body is prismatic along the axis iff all
   three slices agree:
     * same loop kind (polygon / bspline_closed / composite),
     * |area| equal within ``area_rel_tol`` (default 2 %),
     * same 2D bounding box within ``bbox_rel_tol`` (default 2 %).
   The first axis that passes wins (longest-extent-first ordering).
4. Emit ``extras['base_profile']``:
     {
       "prismatic": bool,
       "axis": "X" | "Y" | "Z" | None,
       "sketch": <executable sketch dict | None>,   # the recovered mid-slice loop
       "height_mm": float,                          # extent along the axis
       "area_mm2": float,                           # |area| of the loop
       "in_plane_axes": {"u": [...], "v": [...]},   # section (u,v) world frame
       "section_origin_world": [x, y, z],           # centroid-plane origin
       "bbox_world": [xmin, ymin, zmin, xmax, ymax, zmax],
       "n_slices_agree": int,
       "reason": str | None,                        # why not prismatic
     }
   The recovered ``sketch`` is the EXACTLY shape ``_section_curve`` emits — a
   polygon / bspline_closed / composite dict consumable by ``_sketch_to_solid``
   and the new ``extrude_profile_world`` create skill. Its 2D coords live in the
   section's (u, v) in-plane frame centred on ``section_origin_world``.

NOTE: read-only. The body is never modified. The same logic is exposed as the
module-level ``detect_outer_silhouette_profile(shape, ...)`` helper so the
planner (``_pick_base_shape``) can call it directly without going through the
skill registry.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._face_count_guard import DEFAULT_MAX_FACE_COUNT
from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.inspect._section_curve import recover_section_loop

# Slice fractions along the candidate axis (through the centroid plane).
_SLICE_FRACS = (0.25, 0.5, 0.75)
_AXES: tuple[tuple[str, tuple[float, float, float], int], ...] = (
    ("X", (1.0, 0.0, 0.0), 0),
    ("Y", (0.0, 1.0, 0.0), 1),
    ("Z", (0.0, 0.0, 1.0), 2),
)


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _bbox(shape) -> tuple[float, float, float, float, float, float] | None:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    if bb.IsVoid():
        return None
    return bb.Get()


def _loop_bbox2d(sketch: dict) -> tuple[float, float, float, float] | None:
    """2D bounding box (umin, vmin, umax, vmax) of an executable sketch dict.

    Works for the three loop kinds ``recover_section_loop`` emits — polygon
    (``vertices``), bspline_closed (``fit_points``), composite (``segments``
    of line/spline points).
    """
    pts: list[tuple[float, float]] = []
    kind = sketch.get("kind")
    if kind == "polygon":
        pts = list(sketch.get("vertices", []))
    elif kind == "bspline_closed":
        pts = list(sketch.get("fit_points", []))
    elif kind == "composite":
        for seg in sketch.get("segments", []):
            if "start" in seg and "end" in seg:
                pts.append(tuple(seg["start"]))
                pts.append(tuple(seg["end"]))
            elif "points" in seg:
                pts.extend(tuple(p) for p in seg["points"])
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _slices_agree(
    slices: list[dict],
    area_rel_tol: float,
    bbox_rel_tol: float,
) -> tuple[bool, str | None]:
    """All recovered slices describe the SAME cross-section (constant prism)?"""
    good = [s for s in slices if s.get("sketch") and s.get("area_mm2", 0.0) > 0.0]
    if len(good) < len(slices):
        return (False, "missing_or_empty_slice")

    kinds = {s["loop_kind"] for s in good}
    if len(kinds) != 1:
        return (False, f"loop_kind_varies:{sorted(kinds)}")

    areas = [float(s["area_mm2"]) for s in good]
    amin, amax = min(areas), max(areas)
    if amax <= 0.0:
        return (False, "zero_area")
    if (amax - amin) / amax > area_rel_tol:
        return (False, f"area_varies:{amin:.1f}..{amax:.1f}")

    boxes = [_loop_bbox2d(s["sketch"]) for s in good]
    if any(b is None for b in boxes):
        return (False, "no_loop_bbox")
    spans_u = [b[2] - b[0] for b in boxes]
    spans_v = [b[3] - b[1] for b in boxes]
    for spans in (spans_u, spans_v):
        smin, smax = min(spans), max(spans)
        if smax <= 1e-9:
            continue
        if (smax - smin) / smax > bbox_rel_tol:
            return (False, f"loop_bbox_varies:{smin:.1f}..{smax:.1f}")

    return (True, None)


def detect_outer_silhouette_profile(
    shape,
    *,
    area_rel_tol: float = 0.02,
    bbox_rel_tol: float = 0.02,
    min_aspect: float = 1.15,
    max_face_count: int | None = DEFAULT_MAX_FACE_COUNT,
    simplify_tol_mm: float = 0.01,
) -> dict[str, Any]:
    """Detect the dominant prismatic axis + recover its swept outer profile.

    Returns the ``base_profile`` dict (see module docstring). ``prismatic`` is
    False (with a ``reason``) whenever no axis passes the constant-cross-section
    test — the planner then falls back to the box base.

    ``min_aspect`` requires the candidate sweep axis to be at least this many
    times longer than the larger perpendicular extent — a near-cubic body has
    no meaningful "sweep" direction and the box base already captures it. Pure
    geometric thresholds, never per-file.
    """
    occ = _occt_shape(shape)
    none_result = {
        "prismatic": False,
        "axis": None,
        "sketch": None,
        "height_mm": 0.0,
        "area_mm2": 0.0,
        "in_plane_axes": None,
        "section_origin_world": None,
        "bbox_world": None,
        "n_slices_agree": 0,
        "reason": "no_bbox",
    }

    bb = _bbox(occ)
    if bb is None:
        return none_result
    xmin, ymin, zmin, xmax, ymax, zmax = bb
    extents = (xmax - xmin, ymax - ymin, zmax - zmin)
    cx = (xmin + xmax) * 0.5
    cy = (ymin + ymax) * 0.5
    cz = (zmin + zmax) * 0.5
    centroid = (cx, cy, cz)

    from phone_designer.skills.inspect._section_curve import _inplane_frame

    # True body volume — used to pick the TIGHTEST prismatic axis (the swept
    # profile whose prism volume best matches the real solid). A box is
    # "prismatic" along all three axes; the tightest one captures the most
    # shape (e.g. a thin rounded plate sweeps its rounded face along the SHORT
    # axis, not the longest). None if the volume probe fails → fall back to the
    # longest-extent axis ordering.
    body_vol = _body_volume(occ)

    # GLOBAL aspect gate: a near-cubic body (all three extents similar) has no
    # meaningful sweep direction — the box base already captures it. A prism
    # OR a thin plate has at least one extent that differs; the actual sweep
    # axis may be the LONG one (a bar) OR the SHORT one (a thin rounded plate
    # swept face-on), so we try every axis and let the tightest-volume pick +
    # the planner's revert-guard choose. Reject only the genuinely cubic case.
    ext_max = max(extents)
    ext_min = min(e for e in extents if e > 1e-9) if any(e > 1e-9 for e in extents) else 0.0
    if ext_min > 1e-9 and ext_max / ext_min < min_aspect:
        return {**none_result, "reason": "low_aspect_cubic",
                "bbox_world": [float(b) for b in bb]}

    # Evaluate every axis; collect those passing the constant-cross-section
    # test. Longest-extent first only as a stable tie-break.
    order = sorted(_AXES, key=lambda a: extents[a[2]], reverse=True)

    last_reason = "no_prismatic_axis"
    candidates: list[dict] = []
    for name, n_unit, idx in order:
        length = extents[idx]
        if length <= 1e-6:
            continue

        lo = (xmin, ymin, zmin)[idx]
        slices: list[dict] = []
        for frac in _SLICE_FRACS:
            origin = list(centroid)
            origin[idx] = lo + length * frac
            res = recover_section_loop(
                occ, tuple(origin), n_unit,
                max_face_count=max_face_count,
                simplify_tol_mm=simplify_tol_mm,
            )
            if res.get("skipped"):
                last_reason = "section_skipped_too_big"
                slices = []
                break
            slices.append(res)
        if not slices:
            continue

        ok, reason = _slices_agree(slices, area_rel_tol, bbox_rel_tol)
        if not ok:
            last_reason = f"{name}:{reason}"
            continue

        # PRISMATIC along this axis. Use the centroid (50 %) slice as the
        # canonical profile (its origin is the body centroid plane).
        mid = slices[1]
        u, v = _inplane_frame(n_unit)
        section_origin = list(centroid)
        section_origin[idx] = lo + length * 0.5
        area = float(mid["area_mm2"])
        prism_vol = area * float(length)
        candidates.append({
            "prismatic": True,
            "axis": name,
            "sketch": mid["sketch"],
            "height_mm": float(length),
            "area_mm2": area,
            "in_plane_axes": {"u": list(u), "v": list(v)},
            "section_origin_world": [float(c) for c in section_origin],
            "bbox_world": [float(b) for b in bb],
            "n_slices_agree": len(slices),
            "reason": None,
            "_prism_vol": prism_vol,
        })

    if not candidates:
        return {**none_result, "reason": last_reason,
                "bbox_world": [float(b) for b in bb]}

    # Pick the TIGHTEST prismatic axis: the swept profile whose prism volume is
    # closest to the true body volume (captures the most real shape — rounded
    # face vs plain rectangular section). Fall back to longest-extent ordering
    # (candidates[0]) when the volume probe was unavailable.
    if body_vol is not None and body_vol > 0.0:
        best = min(candidates, key=lambda c: abs(c["_prism_vol"] - body_vol))
    else:
        best = candidates[0]
    best.pop("_prism_vol", None)
    return best


def _body_volume(shape) -> float | None:
    try:
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps

        p = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, p)
        v = float(p.Mass())
        return v if v > 0.0 else None
    except Exception:
        return None


@skill(
    name="extract_outer_silhouette_profile",
    category="inspect",
    level="atomic",
    summary="Detect the dominant prismatic axis (constant cross-section over "
            "25/50/75% slices) and recover the swept OUTER silhouette as an "
            "executable sketch + height. Lets box-mode RE extrude the true "
            "rounded/D-shaped outline instead of a bbox rectangle. Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["base_profile"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class ExtractOuterSilhouetteProfile(SkillBase):
    class Args(BaseModel):
        area_rel_tol: float = Field(
            default=0.02, ge=0.0, le=1.0,
            description="Max relative |area| spread across the 25/50/75% "
                        "slices for the body to count as prismatic.",
        )
        bbox_rel_tol: float = Field(
            default=0.02, ge=0.0, le=1.0,
            description="Max relative 2D loop-bbox span spread across slices.",
        )
        min_aspect: float = Field(
            default=1.15, ge=1.0,
            description="Sweep axis must be at least this many times longer "
                        "than the larger perpendicular extent (near-cubic "
                        "bodies have no meaningful sweep direction).",
        )
        simplify_tol_mm: float = Field(
            default=0.01, ge=0.0,
            description="Douglas-Peucker tolerance forwarded to "
                        "recover_section_loop for straight polyline runs.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("extract_outer_silhouette_profile: body is None")
        shape = _occt_shape(body)
        profile = detect_outer_silhouette_profile(
            shape,
            area_rel_tol=args.area_rel_tol,
            bbox_rel_tol=args.bbox_rel_tol,
            min_aspect=args.min_aspect,
            simplify_tol_mm=args.simplify_tol_mm,
        )
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"base_profile": profile},
        )
