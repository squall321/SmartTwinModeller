"""offset_sketch — atomic, read-only. Offset (parallel) a 2D sketch profile.

The "Offset Entities" op: grow or shrink a closed sketch profile by a signed
distance, producing a new parallel loop — gasket / seal lips, constant-gap walls,
tool-path outlines, shrink/grow a silhouette. Concave corners are auto-filleted
(GeomAbs_Arc joins), the standard 2D-offset behaviour.

Thin exposure of the existing private ``modify_pocket/_closed_path_sweep._offset_wire``
(BRepOffsetAPI_MakeOffset), promoted to a user-facing atomic. Takes any closed
SketchSpec, offsets its outer wire, and emits a NEW executable SketchSpec dict (a
``polygon`` of the sampled offset loop) in ``extras['sketch']`` — feed it straight
into sketch_extrude / sketch_loft. Read-only: no body in, a sketch out.

``distance_mm`` > 0 grows the profile outward (CCW convention), < 0 shrinks it
inward. Inward offset past the profile's inradius collapses the loop → a structured
refusal (fm.offset_collapsed).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_pocket._sketch import SketchSpec

_SAMPLES_PER_EDGE = 24


def _sample_wire_2d(wire) -> list[tuple[float, float]]:
    """Ordered 2D (x,y) points along a planar wire (z≈0), via BRepTools_WireExplorer."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.gp import gp_Pnt

    pts: list[tuple[float, float]] = []
    ex = BRepTools_WireExplorer(wire)
    while ex.More():
        edge = ex.Current()
        ac = BRepAdaptor_Curve(edge)
        f, l = ac.FirstParameter(), ac.LastParameter()
        n = _SAMPLES_PER_EDGE
        for i in range(n):  # [f, l) — next edge contributes its start
            t = f + (l - f) * (i / n)
            p = gp_Pnt()
            ac.D0(t, p)
            xy = (round(p.X(), 4), round(p.Y(), 4))
            if not pts or (abs(pts[-1][0] - xy[0]) > 1e-4 or abs(pts[-1][1] - xy[1]) > 1e-4):
                pts.append(xy)
        ex.Next()
    return pts


@skill(
    name="offset_sketch",
    category="create",
    level="atomic",
    summary="Offset (parallel) a closed 2D sketch profile by distance_mm (>0 grows "
            "outward, <0 shrinks inward; concave corners auto-filleted). Emits a NEW "
            "executable SketchSpec (polygon) in extras['sketch'] for sketch_extrude/"
            "loft — gasket/seal lips, constant-gap walls, tool-path outlines. "
            "Exposes _offset_wire (BRepOffsetAPI_MakeOffset). Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["offset_sketch"],
    preserves=[],
    manufacturing={},
    failure_modes=["fm.offset_failed", "fm.offset_collapsed"],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class OffsetSketch(SkillBase):
    class Args(BaseModel):
        sketch: SketchSpec = Field(
            description="The closed 2D profile to offset (any closed SketchSpec "
                        "kind).")
        distance_mm: float = Field(
            description="Signed offset. >0 grows the profile outward, <0 shrinks "
                        "inward. Must be non-zero.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from pydantic import TypeAdapter

        from phone_designer.skills.create.sketch_loft import _outer_wire
        from phone_designer.skills.modify_pocket._closed_path_sweep import _offset_wire
        from phone_designer.skills.modify_pocket._sketch_to_solid import (
            _build_planar_face,
        )

        if args.distance_mm == 0.0:
            raise ValueError("fm.offset_failed: distance_mm must be non-zero.")
        sk = args.sketch
        if isinstance(sk, dict):
            sk = TypeAdapter(SketchSpec).validate_python(sk)

        try:
            wire = _outer_wire(_build_planar_face(sk))
            offset = _offset_wire(wire, float(args.distance_mm))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"fm.offset_failed: {type(exc).__name__}: {exc} — an inward offset "
                "may exceed the profile's inradius.")

        pts = _sample_wire_2d(offset)
        if len(pts) < 3:
            raise ValueError(
                "fm.offset_collapsed: the offset loop degenerated to <3 points "
                "(inward offset past the profile inradius).")

        new_sketch = {
            "kind": "polygon",
            "vertices": pts,
            "corner_fillet_r": 0.0,
            "center_x_mm": 0.0,
            "center_y_mm": 0.0,
        }
        # validate it round-trips through the SketchSpec union.
        TypeAdapter(SketchSpec).validate_python(new_sketch)

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "sketch": new_sketch,
                "distance_mm": float(args.distance_mm),
                "n_points": len(pts),
                "grade": "constructed",
            },
        )
