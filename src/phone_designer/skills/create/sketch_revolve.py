"""sketch_revolve — atomic create skill (2026-07).

FROM-SCRATCH "draw a 2D lathe profile (with arcs) → revolve into a solid of
revolution". An LLM client builds a closed 2D profile on the +x HALF-PLANE
(a composite of line/arc/spline, or a primitive) and revolves it about the world
Z axis — a bushing, a turned shaft, a chamfered ring, etc.

This fills a real gap: ``revolve_boss`` / ``revolve_pocket`` REQUIRE an existing
body (they fuse a revolved boss / cut a revolved pocket via BRepAlgoAPI); calling
them from scratch FAILS. ``sketch_revolve`` reuses the SAME revolve core
(``revolve_pocket._build_revolved_solid``) but returns the revolved solid directly
— no base body, no boolean.

AXIS CONVENTION (structurally pinned — validated up front, NOT silently honoured):
the revolve core orients the profile into the world XZ plane, which is consistent
ONLY with a ±Z revolution axis through x=y=0. A non-Z axis silently produces a
zero-volume garbage solid (or crashes), so this skill REFUSES it with
``fm.unsupported_revolve_axis``. The profile must lie on the +x half-plane
(local x ≥ 0, where x → radial distance, y → axial height); a profile CROSSING the
axis is ``fm.profile_crosses_axis`` (a self-intersecting revolve). ``is_solid`` is
gated on ``volume_mm3 > 0``. Example chamfered bushing::

    {"sketch": {"kind": "composite", "segments": [
        {"kind": "line", "start": [6, 0],  "end": [10, 0]},
        {"kind": "line", "start": [10, 0], "end": [10, 8]},
        {"kind": "arc",  "start": [10, 8], "end": [6, 8], "radius": 4, "ccw": false},
        {"kind": "line", "start": [6, 8],  "end": [6, 0]}]},
     "angle_deg": 360}
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field, TypeAdapter

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_pocket._sketch import SketchSpec

_EPS_VOL = 1e-6
_EPS_AXIS = 1e-6


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return abs(float(g.Mass()))


def _bbox_mm(shape) -> list[float]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bb)
    except Exception:
        BRepBndLib.Add_s(shape, bb)
    if bb.IsVoid():
        return [0.0, 0.0, 0.0]
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return [round(xmax - xmin, 3), round(ymax - ymin, 3), round(zmax - zmin, 3)]


def _local_x_range(sketch):
    """(xmin, xmax) of the LOCAL profile face — the radial distance to the axis.
    Exact because local-x is preserved by the revolve core's orientation and the
    +Z-axis lock guarantees no x/y axis shift."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    from phone_designer.skills.modify_pocket._sketch_to_solid import _build_planar_face
    face = _build_planar_face(sketch)
    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(face, bb)
    except Exception:
        BRepBndLib.Add_s(face, bb)
    if bb.IsVoid():
        return None
    xmin, _, _, xmax, _, _ = bb.Get()
    return float(xmin), float(xmax)


@skill(
    name="sketch_revolve",
    category="create",
    level="atomic",
    summary="FROM-SCRATCH: revolve a closed 2D lathe profile into a solid of "
            "revolution about the world Z axis. The profile is a SketchSpec on the "
            "+x HALF-PLANE (local x≥0: x→radius, y→axial height); for an arbitrary "
            "arc profile use kind='composite' with line/arc/spline segments. "
            "angle_deg in (0,360], default 360. Reuses the revolve core directly "
            "(no base body / no boolean — the clean from-scratch revolve that "
            "revolve_boss/pocket cannot do). Axis is structurally pinned to ±Z "
            "(non-Z → fm.unsupported_revolve_axis); a profile crossing the axis → "
            "fm.profile_crosses_axis. is_solid gated on volume>0.",
    selector_kinds=[],
    history_rules={"output_faces": HistoryRule.GENERATED_NEW},
    produces_features=["sketch_solid"],
    preserves=[],
    manufacturing={},
    failure_modes=["fm.unsupported_revolve_axis", "fm.profile_crosses_axis",
                   "fm.profile_not_closed", "fm.degenerate_profile",
                   "fm.revolve_produced_null"],
    cost_hint=0.3,
    result_grade="measured",
    post_conditions=[PostCondition(kind="body_present")],
)
class SketchRevolve(SkillBase):
    class Args(BaseModel):
        sketch: SketchSpec = Field(
            description="Closed 2D lathe profile on the +x HALF-PLANE (local x≥0 "
                        "so it does not cross the axis). local x→radial distance, "
                        "local y→axial height. Same kinds as sketch_extrude "
                        "(composite with line/arc/spline for arc profiles).")
        axis_origin: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 0.0),
            description="World point on the revolution axis. x and y MUST be 0 "
                        "(axis = world Z line through (0,0,z)); only z may vary.")
        axis_direction: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 1.0),
            description="Revolution axis direction. MUST be +Z (0,0,1) or -Z — the "
                        "profile-orientation convention is structurally pinned to Z.")
        angle_deg: float = Field(
            default=360.0, gt=0.0, le=360.0,
            description="Sweep angle in (0,360]. Default 360 = full solid.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        from phone_designer.skills.modify_pocket.revolve_pocket import (
            _build_revolved_solid,
        )
        sketch = args.sketch
        if isinstance(sketch, dict):
            sketch = TypeAdapter(SketchSpec).validate_python(sketch)

        # ── up-front axis-convention guard (the validators' #1 fix) ──────────
        d = args.axis_direction
        dmag = math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2)
        if dmag < 1e-12 or abs(abs(d[2]) / dmag - 1.0) > 1e-6:
            raise ValueError(
                "fm.unsupported_revolve_axis: revolve is supported only about the "
                f"world Z axis; got axis_direction={d}. Use (0,0,1) or (0,0,-1).")
        if abs(args.axis_origin[0]) > _EPS_AXIS or abs(args.axis_origin[1]) > _EPS_AXIS:
            raise ValueError(
                "fm.unsupported_revolve_axis: axis_origin x and y must be 0 (axis "
                f"= Z line through (0,0,z)); got axis_origin={args.axis_origin}.")

        # ── half-plane guard: profile must not cross the axis (local x≥0) ────
        rng = _local_x_range(sketch)
        if rng is not None and rng[0] < -_EPS_AXIS:
            raise ValueError(
                f"fm.profile_crosses_axis: profile local-x in [{rng[0]:.3f},"
                f"{rng[1]:.3f}] crosses the revolution axis; x≥0 is required.")

        try:
            revolved = _build_revolved_solid(
                sketch, tuple(args.axis_origin), tuple(args.axis_direction),
                args.angle_deg)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"fm.revolve_produced_null: revolve build failed: "
                             f"{type(exc).__name__}: {exc}")

        vol = _volume(revolved)
        if revolved is None or revolved.IsNull() or vol <= _EPS_VOL:
            raise ValueError(
                f"fm.degenerate_profile: revolved volume {vol:.6g}mm³ ≈ 0 — the "
                "profile is degenerate or self-intersecting.")

        return SkillResult(
            body=Part(revolved),
            history=EntityHistoryMap(rules={"output_faces": HistoryRule.GENERATED_NEW}),
            extras={"sketch_solid": {
                "kind": "revolve",
                "profile_kind": getattr(sketch, "kind", "?"),
                "is_solid": True,
                "volume_mm3": round(vol, 3),
                "bbox_mm": _bbox_mm(revolved),
                "axis": {"origin": list(args.axis_origin),
                         "direction": list(args.axis_direction)},
                "angle_deg": args.angle_deg,
                "profile_x_range": [round(rng[0], 3), round(rng[1], 3)] if rng else None,
                "grade": "constructed",
            }},
        )
