"""optical_window_dome — atomic. Outward optical window dome (spherical cap).

Fuses a small spherical-cap dome onto a planar ±Z host face — used for
the polished outer optical window above a proximity sensor, ambient
light sensor, PPG sensor, etc. The dome sits ABOVE the host face (volume
is added, not removed) so post_condition is ``volume_changed``.

If ``polish_tag`` is True the newly generated dome face gets a
``surface_finish`` "polish" metadata record (Ra ≈ 0.05 µm) on
``body._pd_finish["polish"]`` so downstream DFM / process planning can
schedule a polishing step on it. Geometry itself is unchanged by the
tag.

v1 supports planar +Z / -Z host faces.
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


@skill(
    name="optical_window_dome",
    category="modify/curvature",
    level="atomic",
    summary="Small outward spherical-cap dome fused onto a planar ±Z face — "
            "an optical window for proximity / PPG / ALS sensors. Optionally "
            "tags the dome face with surface_finish='polish' for downstream "
            "DFM. v1 supports planar +Z/-Z host faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":  HistoryRule.MODIFIED_INHERIT,
        "dome_cap":     HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.diameter_positive",
    ],
    produces_features=["optical_window_dome", "dome_cap"],
    preserves=["body_topology_outside_dome"],
    manufacturing={
        "cnc_5axis":         {"min_wall_mm": 0.5,
                              "extras": {"polish_after_machining": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5,
                              "extras": {"tool_polish_grade": "SPI_A1"}},
    },
    failure_modes=[
        "fm.dome_rise_too_large",
        "fm.dome_off_face",
    ],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_changed")],
)
class OpticalWindowDome(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of dome center, offset from face centroid.",
        )
        window_d_mm: float = Field(
            gt=0, le=80,
            description="Optical window diameter (base of the spherical cap).",
        )
        dome_height_mm: float = Field(
            default=0.3, gt=0.0, le=10.0,
            description="Spherical-cap sag (axial rise above the host face).",
        )
        polish_tag: bool = Field(
            default=True,
            description="If true, attach surface_finish='polish' metadata "
                        "(Ra ≈ 0.05 µm) so downstream DFM schedules polishing.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import (
            BRepPrimAPI_MakeBox,
            BRepPrimAPI_MakeSphere,
        )
        from OCP.gp import gp_Pnt

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"optical_window_dome: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "optical_window_dome v1: planar +Z/-Z host faces only"
            )

        nz_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        face_z = center[2]

        # Spherical-cap geometry: cap of base radius `a` and sag `h` lives on
        # a sphere of radius R = (a² + h²) / (2h).
        a = args.window_d_mm / 2.0
        h = args.dome_height_mm
        if h >= a + 1e-6:
            raise ValueError(
                f"optical_window_dome: dome_height_mm ({h}) must be < "
                f"window radius ({a}) to form a spherical cap"
            )
        R = (a * a + h * h) / (2.0 * h)

        # Sphere centered so its top sits at face_z + nz_sign * h.
        # For an outward dome (cap on the +nz side), sphere center is at
        # face_z + nz_sign * h - nz_sign * R = face_z - nz_sign * (R - h).
        sph_cz = face_z - nz_sign * (R - h)
        sph_mk = BRepPrimAPI_MakeSphere(gp_Pnt(cx, cy, sph_cz), R)
        sph_mk.Build()
        if not sph_mk.IsDone():
            raise RuntimeError("optical_window_dome: sphere build failed")
        sphere = sph_mk.Shape()

        # Slab to keep only the cap above the host face (a thin slice of
        # thickness h in the +nz direction). Box must be wider than the
        # sphere bbox in XY so the intersection captures the full cap.
        slab_xy = max(args.window_d_mm * 2.0, 2.0 * R + 2.0)
        z_face = face_z
        z_far = face_z + nz_sign * (h + 0.05)
        zmin = min(z_face, z_far)
        zmax = max(z_face, z_far)
        p1 = gp_Pnt(cx - slab_xy / 2.0, cy - slab_xy / 2.0, zmin)
        p2 = gp_Pnt(cx + slab_xy / 2.0, cy + slab_xy / 2.0, zmax)
        slab_mk = BRepPrimAPI_MakeBox(p1, p2)
        slab_mk.Build()
        if not slab_mk.IsDone():
            raise RuntimeError("optical_window_dome: slab build failed")
        slab = slab_mk.Shape()

        common = BRepAlgoAPI_Common(sphere, slab)
        common.Build()
        if not common.IsDone():
            raise RuntimeError("optical_window_dome: cap intersect failed")
        cap = common.Shape()

        fuse = BRepAlgoAPI_Fuse(shape, cap)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("optical_window_dome: body+cap fuse failed")
        new_shape = fuse.Shape()

        out_body = Part(new_shape)

        extras: dict[str, Any] = {
            "dome_center": (round(cx, 4), round(cy, 4),
                              round(face_z + nz_sign * h, 4)),
            "dome_radius_R_mm": round(R, 4),
        }

        # ── Optional polish surface-finish metadata.
        if args.polish_tag:
            from phone_designer.skills._resolvers import _all_faces, _face_area
            from phone_designer.skills.modify_finish.surface_finish_tag import (
                get_finish_tags,
                set_finish_tags,
            )

            # Find newly created spherical face: nearest non-planar face whose
            # area approximates the spherical-cap analytical area 2πRh.
            target_area = 2.0 * 3.141592653589793 * R * h
            dome_center_world = (cx, cy, face_z + nz_sign * h)
            best = None
            best_score = None
            for f in _all_faces(new_shape):
                try:
                    from OCP.BRepAdaptor import BRepAdaptor_Surface
                    from OCP.GeomAbs import GeomAbs_Sphere
                    surf = BRepAdaptor_Surface(f)
                    if int(surf.GetType()) != int(GeomAbs_Sphere):
                        continue
                    a_f = _face_area(f)
                except Exception:
                    continue
                score = abs(a_f - target_area)
                if best is None or score < best_score:
                    best = f
                    best_score = score

            existing = dict(get_finish_tags(out_body))
            polish_records = list(existing.get("polish", []))
            polish_records.append({
                "center": (round(dome_center_world[0], 3),
                            round(dome_center_world[1], 3),
                            round(dome_center_world[2], 3)),
                "area_mm2": round(2.0 * 3.141592653589793 * R * h, 3),
                "target_ra_um": 0.05,
            })
            existing["polish"] = polish_records
            set_finish_tags(out_body, existing)
            extras["polish_tagged"] = best is not None

        history = EntityHistoryMap(
            rules={
                "target_face":  HistoryRule.MODIFIED_INHERIT,
                "dome_cap":     HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=out_body, history=history, extras=extras)
