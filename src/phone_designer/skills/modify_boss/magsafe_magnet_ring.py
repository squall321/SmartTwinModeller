"""magsafe_magnet_ring — macro. MagSafe-style annular magnet ring on a face.

Adds an annular boss (raised ring) at `center_xy` on the selected planar face,
then drills `magnet_count` axial cylindrical magnet pockets evenly spaced
around the ring (one pocket per magnet at the ring mid-radius).

Net effect on volume: ring fuse (+) followed by pocket cuts (-). The boss
volume far exceeds the per-magnet pocket volume in normal use, so the post
condition is `volume_changed` (either direction is acceptable, but practical
configurations net positive).

Magnet pocket dimensions come from the magnet catalog
(`catalogs/<magnet_catalog_family>/<...>.yaml`, default family = "ndfeb"): the
pocket diameter = `magnet_d_mm + 0.05 mm` (glue clearance) and the pocket
depth = `min(magnet_t_mm, ring_height)` so we never punch through the boss.

v1 supports planar +Z / -Z target faces only. Center XY is FACE-LOCAL,
relative to the face center.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def _load(family: str, name: str):
    import pathlib

    import yaml
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load((root / "catalogs" / family / f"{name}.yaml").read_text())


@skill(
    name="magsafe_magnet_ring",
    category="modify/boss",
    level="macro",
    summary="MagSafe-style annular magnet ring: annular boss on a planar face "
            "plus a circular pattern of axial magnet pockets evenly distributed "
            "around the ring mid-radius. Magnet sizing comes from "
            "catalogs/<magnet_catalog_family>/*.yaml.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":  HistoryRule.MODIFIED_INHERIT,
        "ring_top":     HistoryRule.GENERATED_NEW,
        "ring_walls":   HistoryRule.GENERATED_NEW,
        "magnet_pockets": HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.magnet_spec_in_catalog",
        "pc.ring_inner_lt_outer",
    ],
    produces_features=["magsafe_magnet_ring"],
    preserves=["outer_envelope_outside_ring"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"flat_bottom_endmill": True}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_for_press_fit": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.magnet_spec_unknown",
        "fm.ring_inner_ge_outer",
        "fm.magnet_pocket_too_large_for_ring_width",
    ],
    cost_hint=0.4,
    post_conditions=[PostCondition(kind="volume_changed")],
    expansion=["union", "hole_array"],
)
class MagsafeMagnetRing(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        center_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of the ring center",
        )
        inner_d_mm: float = Field(default=45.0, gt=0, le=300)
        outer_d_mm: float = Field(default=55.0, gt=0, le=300)
        ring_height_mm: float = Field(
            default=1.2, gt=0, le=20,
            description="annular boss height (axial thickness of the ring)",
        )
        magnet_count: int = Field(default=18, ge=2, le=200)
        magnet_spec: str = Field(
            default="N42_D5x2",
            description="catalog key (e.g. 'N42_D5x2')",
        )
        magnet_catalog_family: str = Field(
            default="ndfeb",
            description="catalog subfolder under catalogs/",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import (
            BRepAlgoAPI_Cut,
            BRepAlgoAPI_Fuse,
        )
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        from phone_designer.skills._resolvers import (
            _face_center,
            _face_normal_at_center,
            resolve_faces,
        )

        if args.inner_d_mm >= args.outer_d_mm:
            raise ValueError(
                f"magsafe_magnet_ring: inner_d ({args.inner_d_mm}) >= outer_d "
                f"({args.outer_d_mm}) — ring width must be positive"
            )

        # Catalog: catalogs/magnets/<family>.yaml — same convention as
        # magnet_pocket_axial (folder fixed to "magnets", `family` arg is the
        # file name).
        catalog = _load("magnets", args.magnet_catalog_family)
        magnets = catalog.get("magnets", {})
        if args.magnet_spec not in magnets:
            raise ValueError(
                f"magsafe_magnet_ring: unknown magnet_spec "
                f"'{args.magnet_spec}' in family "
                f"'{args.magnet_catalog_family}' — catalog keys: "
                f"{sorted(magnets.keys())}"
            )
        entry = magnets[args.magnet_spec]
        magnet_d = float(entry["magnet_d_mm"])
        magnet_t = float(entry["magnet_t_mm"])

        # Sanity check: pocket must fit inside the ring radial width.
        ring_width_mm = (args.outer_d_mm - args.inner_d_mm) / 2.0
        pocket_d = magnet_d + 0.05  # glue clearance
        if pocket_d >= ring_width_mm * 2.0:
            raise ValueError(
                f"magsafe_magnet_ring: magnet pocket Ø {pocket_d:.3f} mm too "
                f"large for ring width {ring_width_mm:.3f} mm (outer-inner)/2"
            )

        # Pocket depth: cap at ring height so pockets stop inside the boss.
        pocket_depth = min(magnet_t, args.ring_height_mm)

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"magsafe_magnet_ring: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "magsafe_magnet_ring v1: planar +Z/-Z target faces only"
            )

        nz_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.center_xy[0]
        cy = center[1] + args.center_xy[1]
        face_z = center[2]

        # ── 1) Build annular boss = outer cylinder − inner cylinder ────────
        # Boss grows ALONG the face normal (out of the body).
        outward_dir = gp_Dir(0.0, 0.0, nz_sign)
        outer_ax = gp_Ax2(gp_Pnt(cx, cy, face_z), outward_dir)
        outer_mk = BRepPrimAPI_MakeCylinder(
            outer_ax, args.outer_d_mm / 2.0, args.ring_height_mm,
        )
        outer_mk.Build()
        if not outer_mk.IsDone():
            raise RuntimeError("magsafe_magnet_ring: outer cylinder failed")

        # Make the inner cylinder a touch taller so the boolean cut leaves a
        # clean through-hole (no zero-thickness sliver at the top rim).
        inner_h = args.ring_height_mm + 0.5
        inner_base_z = face_z - nz_sign * 0.25  # start slightly BELOW face
        inner_ax = gp_Ax2(gp_Pnt(cx, cy, inner_base_z), outward_dir)
        inner_mk = BRepPrimAPI_MakeCylinder(
            inner_ax, args.inner_d_mm / 2.0, inner_h,
        )
        inner_mk.Build()
        if not inner_mk.IsDone():
            raise RuntimeError("magsafe_magnet_ring: inner cylinder failed")

        ring_cut = BRepAlgoAPI_Cut(outer_mk.Shape(), inner_mk.Shape())
        ring_cut.Build()
        if not ring_cut.IsDone():
            raise RuntimeError("magsafe_magnet_ring: annular ring tool failed")
        ring_tool = ring_cut.Shape()

        fuse = BRepAlgoAPI_Fuse(shape, ring_tool)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("magsafe_magnet_ring: ring fuse failed")
        shape = fuse.Shape()

        # ── 2) Circular pattern of axial magnet pockets ────────────────────
        # Pockets sit at the ring MID-RADIUS. They open at the new top face
        # of the boss (face_z + nz_sign * ring_height) and grow back INTO the
        # boss opposite the normal.
        pitch_r = (args.inner_d_mm + args.outer_d_mm) / 4.0
        boss_top_z = face_z + nz_sign * args.ring_height_mm
        pocket_axis = gp_Dir(0.0, 0.0, -nz_sign)
        step_deg = 360.0 / args.magnet_count
        for k in range(args.magnet_count):
            theta = math.radians(k * step_deg)
            px = cx + pitch_r * math.cos(theta)
            py = cy + pitch_r * math.sin(theta)
            p_ax = gp_Ax2(gp_Pnt(px, py, boss_top_z), pocket_axis)
            cyl_mk = BRepPrimAPI_MakeCylinder(p_ax, pocket_d / 2.0, pocket_depth)
            cyl_mk.Build()
            if not cyl_mk.IsDone():
                raise RuntimeError(
                    f"magsafe_magnet_ring: magnet pocket #{k} build failed"
                )
            cut = BRepAlgoAPI_Cut(shape, cyl_mk.Shape())
            cut.Build()
            if not cut.IsDone():
                raise RuntimeError(
                    f"magsafe_magnet_ring: magnet pocket #{k} cut failed"
                )
            shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":    HistoryRule.MODIFIED_INHERIT,
                "ring_top":       HistoryRule.GENERATED_NEW,
                "ring_walls":     HistoryRule.GENERATED_NEW,
                "magnet_pockets": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(shape), history=history)
