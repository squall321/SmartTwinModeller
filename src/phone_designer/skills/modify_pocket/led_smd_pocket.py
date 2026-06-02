"""led_smd_pocket — atomic. SMD LED chip recess on a planar ±Z face.

Catalog-driven cuboidal recess sized to a standard SMD LED chip
(0402 / 0603 / 0805 / 1206 / 5050 / 3535) from
catalogs/optical/leds.yaml. The pocket is sized to the LED body
footprint plus a small lateral side clearance, and is recessed by the
LED package height. When ``with_pad_relief=True`` a slightly wider /
deeper relief is subtracted on top so the PCB pads under the LED have
clearance to the housing wall.

v1 supports planar +Z / -Z target faces.
"""
from __future__ import annotations

from typing import Any

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


def _load(family: str, name: str):
    import pathlib

    import yaml
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load((root / "catalogs" / family / f"{name}.yaml").read_text())


@skill(
    name="led_smd_pocket",
    category="modify/pocket",
    level="atomic",
    summary="Recess sized for a standard SMD LED chip "
            "(0402/0603/0805/1206/5050/3535 per catalogs/optical/leds.yaml). "
            "Body recess is sized to LED footprint + side clearance × LED "
            "package height; if with_pad_relief is True, an extra shallow "
            "relief is cut to clear the PCB landing pads. v1 supports "
            "planar +Z/-Z faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "led_pocket":      HistoryRule.GENERATED_NEW,
        "pad_relief":      HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.led_spec_in_catalog",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["led_smd_pocket"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.4,
                              "extras": {"flat_floor_required": True}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.led_spec_unknown",
        "fm.pocket_exits_body",
    ],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class LedSmdPocket(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of LED center, offset from face centroid.",
        )
        led_spec: str = Field(
            description="Catalog key from catalogs/optical/leds.yaml "
                        "(e.g. '0402', '0603', '0805', '1206', '5050', '3535').",
        )
        with_pad_relief: bool = Field(
            default=True,
            description="If true, cut a slightly wider/shallower relief on top "
                        "of the main LED pocket so the PCB SMT pads underneath "
                        "the LED clear the housing wall.",
        )
        side_clearance_mm: float = Field(
            default=0.1, ge=0.0, le=2.0,
            description="Lateral clearance added to each side of the LED body.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt

        catalog = _load("optical", "leds")
        leds = catalog.get("leds", {})
        if args.led_spec not in leds:
            raise ValueError(
                f"led_smd_pocket: unknown led_spec '{args.led_spec}' — "
                f"catalog keys: {sorted(leds.keys())}"
            )
        entry = leds[args.led_spec]
        led_l = float(entry["length_mm"])
        led_w = float(entry["width_mm"])
        led_h = float(entry["height_mm"])
        # Pad geometry — used to size the optional relief.
        pad_size = entry.get("pad_size_mm", [0.0, 0.0])
        pad_lx = float(pad_size[0]) if len(pad_size) >= 1 else 0.0
        pad_ly = float(pad_size[1]) if len(pad_size) >= 2 else 0.0
        pad_spacing = float(entry.get("pad_spacing_mm", 0.0))

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"led_smd_pocket: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "led_smd_pocket v1: planar +Z/-Z target faces only"
            )

        nz_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        face_z = center[2]

        overshoot = 0.05

        # ── Main LED pocket: footprint + 2 * side_clearance, depth = led_h.
        pocket_lx = led_l + 2.0 * args.side_clearance_mm
        pocket_ly = led_w + 2.0 * args.side_clearance_mm
        pocket_depth = led_h
        # MakeBox(p1, p2): two opposite corners. Cut down INTO the body.
        # Overshoot the top of the box above the face for a clean opening but
        # do NOT overshoot the floor — the inside-body depth must equal led_h.
        z_top = face_z + nz_sign * overshoot
        z_bot = face_z - nz_sign * pocket_depth
        zmin = min(z_top, z_bot)
        zmax = max(z_top, z_bot)
        p1 = gp_Pnt(cx - pocket_lx / 2.0, cy - pocket_ly / 2.0, zmin)
        p2 = gp_Pnt(cx + pocket_lx / 2.0, cy + pocket_ly / 2.0, zmax)
        mk = BRepPrimAPI_MakeBox(p1, p2)
        mk.Build()
        if not mk.IsDone():
            raise RuntimeError("led_smd_pocket: main pocket box build failed")
        tool = mk.Shape()

        # ── Optional pad relief: a slightly wider, shallow extension on top
        # that lets the PCB SMT pads sit clear of the housing wall.
        if args.with_pad_relief:
            # Span the two pads end-to-end along the LED long axis, with a
            # margin equal to the pad's own length in each direction. Width
            # = max(pad_y, led_w + 2*side_clearance) so the relief encloses
            # the pads laterally too.
            relief_span = pad_spacing + pad_lx if pad_spacing > 0 else led_l + 2.0 * pad_lx
            relief_lx = max(relief_span, pocket_lx)
            relief_ly = max(pad_ly, pocket_ly) + 2.0 * args.side_clearance_mm
            relief_depth = max(0.15, led_h * 0.25)   # ≥ 0.15 mm step
            # Stacked on top of main pocket: starts at the body face, ends
            # at relief_depth below it. The main pocket already cuts deeper
            # below, so the union is a stepped well.
            # Top overshoot only — relief inside-body depth = relief_depth.
            z_top_r = face_z + nz_sign * overshoot
            z_bot_r = face_z - nz_sign * relief_depth
            rzmin = min(z_top_r, z_bot_r)
            rzmax = max(z_top_r, z_bot_r)
            pr1 = gp_Pnt(cx - relief_lx / 2.0, cy - relief_ly / 2.0, rzmin)
            pr2 = gp_Pnt(cx + relief_lx / 2.0, cy + relief_ly / 2.0, rzmax)
            relief_mk = BRepPrimAPI_MakeBox(pr1, pr2)
            relief_mk.Build()
            if not relief_mk.IsDone():
                raise RuntimeError("led_smd_pocket: pad relief box build failed")
            fuse = BRepAlgoAPI_Fuse(tool, relief_mk.Shape())
            fuse.Build()
            if not fuse.IsDone():
                raise RuntimeError("led_smd_pocket: pocket+relief fuse failed")
            tool = fuse.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("led_smd_pocket: boolean cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "led_pocket":      HistoryRule.GENERATED_NEW,
                "pad_relief":      HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
