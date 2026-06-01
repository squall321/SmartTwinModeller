"""mems_mic_boot_pocket — atomic. Catalog-driven MEMS microphone boot pocket.

Stamps a three-stage pocket onto a planar face that hosts a bottom-port MEMS
microphone:
  1. Package recess — rectangular footprint matching the MEMS package
     (catalog `package_w_mm` × `package_l_mm`), depth = `package_h_mm`.
  2. Boot cavity   — small cylindrical acoustic volume aligned with the
     package sound port (catalog `boot_cavity_d_mm`, depth `boot_cavity_h_mm`)
     under the package recess.
  3. O-ring groove — annular groove concentric with the sound port,
     dimensioned from catalog `oring_id_mm` and `oring_cs_mm`. The groove
     forms the gas seal between the MEMS port and the PCB pad.

Catalog: catalogs/audio/mems_mics.yaml. v1 supports planar +Z/-Z target faces.
"""
from __future__ import annotations

from typing import Any, Literal

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
    name="mems_mic_boot_pocket",
    category="modify/pocket",
    level="atomic",
    summary="Three-stage MEMS microphone boot pocket: rectangular package "
            "recess, cylindrical acoustic boot cavity aligned with the "
            "package sound port, and an O-ring groove concentric with the "
            "port. Catalog-driven (catalogs/audio/mems_mics.yaml). v1 "
            "supports planar +Z/-Z target faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":       HistoryRule.MODIFIED_INHERIT,
        "package_recess":    HistoryRule.GENERATED_NEW,
        "boot_cavity":       HistoryRule.GENERATED_NEW,
        "port_oring_groove": HistoryRule.GENERATED_NEW,
        "consumed_volume":   HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.mems_spec_in_catalog",
    ],
    produces_features=["mems_mic_boot", "mic_port_oring_seal"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.4,
                              "extras": {"max_aspect_ratio": 6.0}},
        "injection_mold_pa": {"min_wall_mm": 0.6, "min_draft_deg": 0.5},
        "die_cast_al":       {"min_wall_mm": 0.8, "min_draft_deg": 1.0},
    },
    failure_modes=[
        "fm.mems_spec_unknown",
        "fm.pocket_exits_body",
    ],
    cost_hint=0.32,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class MemsMicBootPocket(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of package centre",
        )
        mic_kind: Literal["sph0645", "im69d130", "mp34dt06"] = "sph0645"
        catalog_family: str = Field(
            default="audio/mems_mics",
            description="Catalog path under catalogs/ (split on '/').",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import (
            BRepPrimAPI_MakeBox,
            BRepPrimAPI_MakeCylinder,
        )
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        # Catalog load — supports nested family path like "audio/mems_mics".
        if "/" in args.catalog_family:
            family, name = args.catalog_family.split("/", 1)
        else:
            family, name = "audio", args.catalog_family
        catalog = _load(family, name)
        mics = catalog.get("mics", {})
        if args.mic_kind not in mics:
            raise ValueError(
                f"mems_mic_boot_pocket: unknown mic_kind '{args.mic_kind}' "
                f"— catalog keys: {sorted(mics.keys())}"
            )
        entry = mics[args.mic_kind]
        pkg_w = float(entry["package_w_mm"])
        pkg_l = float(entry["package_l_mm"])
        pkg_h = float(entry["package_h_mm"])
        port_d = float(entry["sound_port_d_mm"])
        port_off = float(entry["sound_port_offset_mm"])
        boot_d = float(entry["boot_cavity_d_mm"])
        boot_h = float(entry["boot_cavity_h_mm"])
        or_id = float(entry["oring_id_mm"])
        or_cs = float(entry["oring_cs_mm"])

        if boot_d <= port_d:
            raise ValueError(
                f"mems_mic_boot_pocket: catalog boot_cavity_d ({boot_d}) "
                f"must be > sound_port_d ({port_d})"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"mems_mic_boot_pocket: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "mems_mic_boot_pocket v1: planar +Z/-Z target faces only"
            )
        nz_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]

        # Stage 1: package recess box, depth = pkg_h.
        # Box spans pkg_w × pkg_l in XY centred on (cx, cy), extends from
        # face level downward (along -nz_sign) by pkg_h.
        x0 = cx - pkg_w / 2.0
        y0 = cy - pkg_l / 2.0
        if nz_sign > 0:
            z0 = center[2] - pkg_h
        else:
            z0 = center[2]
        recess = BRepPrimAPI_MakeBox(
            gp_Pnt(x0, y0, z0), pkg_w, pkg_l, pkg_h,
        )
        recess.Build()
        if not recess.IsDone():
            raise RuntimeError(
                "mems_mic_boot_pocket: package recess box build failed",
            )
        cut1 = BRepAlgoAPI_Cut(shape, recess.Shape())
        cut1.Build()
        if not cut1.IsDone():
            raise RuntimeError(
                "mems_mic_boot_pocket: recess boolean cut failed",
            )
        new_shape = cut1.Shape()

        # Stage 2: acoustic boot cavity (cylinder) aligned with the sound port
        # offset, sitting *under* the package recess (continuing in -normal).
        port_x = cx + port_off
        port_y = cy
        boot_base_z = z0 if nz_sign > 0 else (center[2] + pkg_h)
        # Boot cavity drills further INTO the body.
        boot_dir = gp_Dir(0.0, 0.0, -nz_sign)
        boot_ax = gp_Ax2(gp_Pnt(port_x, port_y, boot_base_z), boot_dir)
        boot_mk = BRepPrimAPI_MakeCylinder(boot_ax, boot_d / 2.0, boot_h)
        boot_mk.Build()
        if not boot_mk.IsDone():
            raise RuntimeError(
                "mems_mic_boot_pocket: boot cavity cylinder build failed",
            )
        cut2 = BRepAlgoAPI_Cut(new_shape, boot_mk.Shape())
        cut2.Build()
        if not cut2.IsDone():
            raise RuntimeError(
                "mems_mic_boot_pocket: boot cavity cut failed",
            )
        new_shape = cut2.Shape()

        # Stage 3: O-ring groove around the port — annular groove at the
        # package-recess floor (z = boot_base_z), opening DOWNWARD (further
        # into the body) by `or_cs` (cross-section depth).
        # Build outer / inner cylinders and subtract.
        groove_outer_r = (or_id + 2.0 * or_cs) / 2.0
        groove_inner_r = or_id / 2.0
        groove_depth = or_cs
        groove_dir = gp_Dir(0.0, 0.0, -nz_sign)
        groove_base = gp_Pnt(port_x, port_y, boot_base_z)
        groove_ax = gp_Ax2(groove_base, groove_dir)
        outer_mk = BRepPrimAPI_MakeCylinder(
            groove_ax, groove_outer_r, groove_depth,
        )
        outer_mk.Build()
        inner_mk = BRepPrimAPI_MakeCylinder(
            groove_ax, groove_inner_r, groove_depth,
        )
        inner_mk.Build()
        if not outer_mk.IsDone() or not inner_mk.IsDone():
            raise RuntimeError(
                "mems_mic_boot_pocket: O-ring groove cylinder build failed",
            )
        annulus = BRepAlgoAPI_Cut(outer_mk.Shape(), inner_mk.Shape())
        annulus.Build()
        if not annulus.IsDone():
            raise RuntimeError(
                "mems_mic_boot_pocket: O-ring annulus boolean failed",
            )
        cut3 = BRepAlgoAPI_Cut(new_shape, annulus.Shape())
        cut3.Build()
        if not cut3.IsDone():
            raise RuntimeError(
                "mems_mic_boot_pocket: O-ring groove cut failed",
            )
        new_shape = cut3.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":       HistoryRule.MODIFIED_INHERIT,
                "package_recess":    HistoryRule.GENERATED_NEW,
                "boot_cavity":       HistoryRule.GENERATED_NEW,
                "port_oring_groove": HistoryRule.GENERATED_NEW,
                "consumed_volume":   HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
