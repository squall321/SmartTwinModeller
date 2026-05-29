"""o_ring_groove_axial_spec — atomic.

Face-seal annular O-ring groove per ISO 3601-2 (axial / static face seal),
sized from the AS568 catalog. The groove is a ring-shaped pocket sunk into
a planar face — used between two flanges that bolt together face-to-face.

Geometry:
    cs                     = catalog.cs_mm
    groove_width           = catalog.face_groove_width_mm  (≈ 1.36 * cs)
    groove_depth           = catalog.face_groove_depth_mm  (≈ 0.76 * cs)
    ring mean diameter Dm  = catalog.id_mm + cs            (= centerline)
    groove inner diameter  = Dm - groove_width / 2
    groove outer diameter  = Dm + groove_width / 2

Args:
    face_selector: SelectorRef — planar face on which to cut the groove.
    o_ring_spec:   AS568 designation, e.g. "AS568-013".
    center_xy:     (x, y) groove center in the face's local frame, expressed
                   as world XY offset from the face center (matches
                   `o_ring_groove` v1 convention; v1 only supports ±Z faces).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def _load(name):
    import yaml, pathlib
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load((root / "catalogs" / "standards" / f"{name}.yaml").read_text())


@skill(
    name="o_ring_groove_axial_spec",
    category="modify/pocket",
    level="atomic",
    summary="Face-seal annular O-ring groove on a planar face, sized from "
            "the AS568 catalog per ISO 3601-2. Selects ring by dash size; "
            "groove width/depth/mean-Ø computed from the catalog.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "groove_walls":    HistoryRule.GENERATED_NEW,
        "groove_floor":    HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.o_ring_spec_known",
        "pc.face_selector_matches_one",
    ],
    produces_features=["o_ring_groove", "face_seal_groove"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.3, "min_fillet_r_mm": 0.2},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_recommended": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.unknown_o_ring_spec",
        "fm.groove_outside_face",
    ],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class ORingGrooveAxialSpec(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        o_ring_spec: str = Field(..., description='AS568 dash, e.g. "AS568-013"')
        center_xy: tuple[float, float] = (0.0, 0.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Align, Cylinder, Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        from phone_designer.skills._resolvers import (
            resolve_faces, _face_center, _face_normal_at_center,
        )

        catalog = _load("o_rings_as568")["o_rings"]
        if args.o_ring_spec not in catalog:
            raise ValueError(
                f"o_ring_groove_axial_spec: unknown o_ring_spec "
                f"'{args.o_ring_spec}'. Known: {sorted(catalog.keys())}"
            )
        spec = catalog[args.o_ring_spec]
        cs = float(spec["cs_mm"])
        id_mm = float(spec["id_mm"])
        groove_w = float(spec["face_groove_width_mm"])
        groove_d = float(spec["face_groove_depth_mm"])

        mean_d = id_mm + cs           # ring mean diameter (centerline)
        outer_d = mean_d + groove_w   # groove outer Ø
        inner_d = mean_d - groove_w   # groove inner Ø
        if inner_d <= 0:
            raise ValueError(
                f"o_ring_groove_axial_spec: derived inner Ø ({inner_d}) <= 0 "
                f"for spec {args.o_ring_spec}"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"o_ring_groove_axial_spec: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)

        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "o_ring_groove_axial_spec v1: only planar ±Z faces supported"
            )

        outer = Cylinder(
            radius=outer_d / 2.0, height=groove_d + 1.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        inner = Cylinder(
            radius=inner_d / 2.0, height=groove_d + 2.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )

        if normal[2] > 0:
            base_z = center[2] - groove_d
        else:
            base_z = center[2]

        cx = center[0] + args.center_xy[0]
        cy = center[1] + args.center_xy[1]

        outer.position = (cx, cy, base_z)
        inner.position = (cx, cy, base_z - 0.5)

        ring_cut = BRepAlgoAPI_Cut(outer.wrapped, inner.wrapped)
        ring_cut.Build()
        if not ring_cut.IsDone():
            raise RuntimeError("o_ring_groove_axial_spec: ring tool failed")
        ring_tool = ring_cut.Shape()

        cut = BRepAlgoAPI_Cut(shape, ring_tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("o_ring_groove_axial_spec: body cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "groove_walls":    HistoryRule.GENERATED_NEW,
                "groove_floor":    HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
