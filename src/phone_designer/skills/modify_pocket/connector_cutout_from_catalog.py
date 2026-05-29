"""connector_cutout_from_catalog — atomic.

Catalog-driven rounded-rectangle (or circular) connector cutout for a host
shell face. Reads cutout outer dimensions, inner corner radius and recess
depth from catalogs/connectors/<family>.yaml keyed by connector_spec
(e.g. "USB_C_Receptacle_v2").

v1 supports planar +Z / -Z target faces. The cutout is positioned at
face-center + position_xy, optionally rotated around the cut axis by
rotation_deg degrees. The cut runs INTO the body along -face_normal.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._resolvers import (
    _face_center,
    _face_normal_at_center,
    resolve_faces,
)
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


# Tolerance for treating cutout_w == cutout_h with inner_radius == w/2 as a
# pure circle (avoids RoundedRectSketch degenerate-corner errors).
_CIRCLE_EPS = 1e-6


def _load(name: str):
    import pathlib

    import yaml
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load(
        (root / "catalogs" / "connectors" / f"{name}.yaml").read_text()
    )


@skill(
    name="connector_cutout_from_catalog",
    category="modify/pocket",
    level="atomic",
    summary="Catalog-driven rounded-rectangle (or circular) cutout for a "
            "connector receptacle (USB-C, USB-A, 3.5 mm audio jack, Apple "
            "Lightning). Reads cutout w/h, inner corner radius and recess "
            "depth from catalogs/connectors/<family>.yaml. v1 supports "
            "planar +Z / -Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":      HistoryRule.MODIFIED_INHERIT,
        "cutout_walls":     HistoryRule.GENERATED_NEW,
        "cutout_bottom":    HistoryRule.GENERATED_NEW,
        "consumed_volume":  HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.connector_spec_in_catalog",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["connector_cutout"],
    preserves=["outer_envelope_outside_cutout"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5, "min_fillet_r_mm": 0.3,
                              "extras": {"max_aspect_ratio": 6.0}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "min_fillet_r_mm": 0.5,
                              "extras": {"post_machine_cutout_edge": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "min_fillet_r_mm": 0.3},
    },
    failure_modes=[
        "fm.connector_spec_unknown",
        "fm.pocket_exits_body",
        "fm.sketch_outside_face",
    ],
    cost_hint=0.18,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class ConnectorCutoutFromCatalog(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of cutout center, offset from face centroid.",
        )
        connector_spec: str = Field(
            description="Connector entry key — e.g. 'USB_C_Receptacle_v2'.",
        )
        catalog_family: str = Field(
            default="usb_c",
            description="Catalog filename stem under catalogs/connectors/.",
        )
        recess_depth_override_mm: Optional[float] = Field(
            default=None, gt=0, le=200,
            description="Override catalog recess_depth_mm.",
        )
        rotation_deg: float = Field(
            default=0.0,
            description="Z-axis (cut-axis) rotation around the cutout center.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

        from phone_designer.skills.modify_pocket._sketch import (
            CircleSketch,
            RoundedRectSketch,
        )
        from phone_designer.skills.modify_pocket._sketch_to_solid import (
            build_pocket_tool,
        )

        # 1) catalog lookup
        catalog = _load(args.catalog_family)
        connectors = catalog.get("connectors", {})
        if args.connector_spec not in connectors:
            raise ValueError(
                f"connector_cutout_from_catalog: unknown connector_spec "
                f"'{args.connector_spec}' in family '{args.catalog_family}' — "
                f"catalog keys: {sorted(connectors.keys())}"
            )
        entry = connectors[args.connector_spec]
        cutout_w = float(entry["cutout_w_mm"])
        cutout_h = float(entry["cutout_h_mm"])
        inner_r = float(entry["inner_radius_mm"])
        cat_depth = float(entry["recess_depth_mm"])
        depth = (
            float(args.recess_depth_override_mm)
            if args.recess_depth_override_mm is not None else cat_depth
        )

        # 2) face resolve + planar check
        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"connector_cutout_from_catalog: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]
        normal = _face_normal_at_center(target_face)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "connector_cutout_from_catalog v1: planar +Z/-Z target faces only"
            )
        center = _face_center(target_face)

        # 3) build sketch — pure circle if w == h and inner_r == w/2, else
        #    rounded rectangle.
        is_circle = (
            abs(cutout_w - cutout_h) < _CIRCLE_EPS
            and abs(inner_r - cutout_w / 2.0) < _CIRCLE_EPS
        )
        if is_circle:
            sketch = CircleSketch(
                diameter_mm=cutout_w,
                center_x_mm=args.position_xy[0],
                center_y_mm=args.position_xy[1],
            )
        else:
            # Clamp inner_r against half-min-side to avoid degenerate fillets.
            max_r = min(cutout_w, cutout_h) / 2.0 - 1e-4
            r = min(inner_r, max_r) if max_r > 0 else inner_r
            if r <= 0:
                raise ValueError(
                    f"connector_cutout_from_catalog: catalog corner radius "
                    f"{inner_r} is non-positive for cutout {cutout_w}x{cutout_h}"
                )
            sketch = RoundedRectSketch(
                length_mm=cutout_w,
                width_mm=cutout_h,
                corner_r_mm=r,
                center_x_mm=args.position_xy[0],
                center_y_mm=args.position_xy[1],
            )

        # 4) build pocket tool via shared helper (handles ±Z face orientation).
        tool = build_pocket_tool(target_face, sketch, depth, direction="into")

        # 5) optional Z-axis rotation around cutout center.
        if abs(args.rotation_deg) > 1e-9:
            cx = center[0] + args.position_xy[0]
            cy = center[1] + args.position_xy[1]
            cz = center[2]
            trsf = gp_Trsf()
            trsf.SetRotation(
                gp_Ax1(gp_Pnt(cx, cy, cz), gp_Dir(0.0, 0.0, 1.0)),
                math.radians(args.rotation_deg),
            )
            tool = BRepBuilderAPI_Transform(tool, trsf, True).Shape()

        # 6) boolean cut.
        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError(
                "connector_cutout_from_catalog: boolean cut failed"
            )
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "cutout_walls":    HistoryRule.GENERATED_NEW,
                "cutout_bottom":   HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
