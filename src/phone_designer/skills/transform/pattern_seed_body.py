"""pattern_seed_body — atomic. Linear/circular pattern of THE working solid.

Patterns the ACTUAL input body (the "seed") into ``count`` rigid copies and FUSES
them into one result — the body-level array (SolidWorks Linear/Circular Pattern of a
solid body, Fusion Rectangular/Circular Pattern, NX Pattern Feature) applied to the
anonymous working solid, not to a sketch feature.

DISTINCT from the ``modify_pattern`` skills, which are LOCKED to a
``profile_diameter_mm`` + a {pocket, hole, boss} feature carved into a base — those
pattern a FEATURE. This patterns the whole seed solid itself, so it works on an
imported part, a from-scratch loft/sweep, or any modeled body.

Two modes:
  * ``linear``  — copy i sits at ``i * spacing_mm`` along the unit of ``direction``
    (i = 0..count-1); i=0 is the seed in place. Reuses ``make_translation_trsf``.
  * ``circular`` — copy i is rotated ``i * step`` about ``axis`` THROUGH ``center_mm``
    (step = total_angle_deg / count for a full 360° ring, else
    total_angle_deg/(count-1) for an open arc so both ends land). Rotation about an
    off-origin centre is composed T(c)·R·T(-c) (the move_body convention), reusing
    ``make_axis_rotation_trsf`` / ``make_translation_trsf``.

Each copy is materialised with ``apply_transform_shape`` (deep copy), then all copies
are fused pairwise with ``BRepAlgoAPI_Fuse``. If ANY fuse fails (IsDone False /
raise / null), the whole result falls back to a ``build_compound`` of the copies —
an honest multi-body container rather than a silent lie. ``is_solid`` is gated on
volume>1e-6.

Verified law (box 10×10×10, vol 1000):
  * linear count=3 spacing=20 (disjoint)   -> total vol 3000
  * linear count=3 spacing=5  (overlapping, boxes at x=0,5,10 span 0..20)
                                             -> UNION vol 2000 (a 20×10×10 bar)
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult

_EPS_VOL = 1e-6


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


def _fuse_all(shapes: list):
    """Fuse a list of shapes pairwise. Returns (shape, ok).

    ok=False if ANY pairwise fuse raised / IsDone False / produced a null — the
    caller then falls back to a compound rather than trusting a partial union.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    if not shapes:
        return None, False
    acc = shapes[0]
    for nxt in shapes[1:]:
        try:
            f = BRepAlgoAPI_Fuse(acc, nxt)
            f.Build()
            if not f.IsDone():
                return None, False
            acc = f.Shape()
            if acc is None or acc.IsNull():
                return None, False
        except Exception:  # noqa: BLE001
            return None, False
    return acc, True


@skill(
    name="pattern_seed_body",
    category="transform",
    level="atomic",
    summary="Linear OR circular pattern of THE working solid (the seed body), "
            "fusing the copies into one result. Unlike modify_pattern (locked to "
            "profile_diameter_mm + {pocket,hole,boss}), this patterns the ACTUAL "
            "input solid. mode='linear' -> copy i at i*spacing_mm along direction; "
            "mode='circular' -> copy i rotated i*step about axis through center_mm "
            "(full 360° ring or an open arc via total_angle_deg). Copies fused with "
            "BRepAlgoAPI_Fuse; falls back to a multi-body compound if a fuse fails. "
            "is_solid gated on volume>0.",
    selector_kinds=[],
    history_rules={"body_faces": HistoryRule.MODIFIED_INHERIT,
                   "seam_edges": HistoryRule.GENERATED_NEW},
    produces_features=[],
    preserves=[],
    manufacturing={},
    failure_modes=["fm.pattern_no_body", "fm.zero_direction", "fm.zero_axis",
                   "fm.pattern_failed"],
    cost_hint=0.2,
    result_grade="measured",
    post_conditions=[PostCondition(kind="body_present")],
)
class PatternSeedBody(SkillBase):
    class Args(BaseModel):
        mode: Literal["linear", "circular"] = Field(
            description="'linear' = repeat along a direction at a fixed spacing; "
                        "'circular' = repeat around an axis.")
        count: int = Field(
            ge=1,
            description="Total number of instances INCLUDING the seed (i=0 in "
                        "place). count=1 returns the seed unchanged.")
        # ── linear ──────────────────────────────────────────────────────────
        direction: tuple[float, float, float] = Field(
            default=(1.0, 0.0, 0.0),
            description="[linear] Repeat direction (need not be unit). Copy i is "
                        "translated i*spacing_mm along its unit vector.")
        spacing_mm: float = Field(
            default=0.0,
            description="[linear] Centre-to-centre step along direction, per "
                        "instance.")
        # ── circular ────────────────────────────────────────────────────────
        axis: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 1.0),
            description="[circular] Rotation axis direction (need not be unit).")
        center_mm: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 0.0),
            description="[circular] A point the rotation axis passes through.")
        total_angle_deg: float = Field(
            default=360.0,
            description="[circular] Total sweep. 360 (default) = full ring, step = "
                        "total/count (no overlap at the seam). <360 = open arc, "
                        "step = total/(count-1) so the last copy lands at "
                        "total_angle_deg.")

        @model_validator(mode="after")
        def _check(self):
            if self.mode == "linear":
                d = self.direction
                if math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2) < 1e-12:
                    raise ValueError(
                        "fm.zero_direction: linear pattern needs a non-zero "
                        "direction vector.")
            else:
                a = self.axis
                if math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2) < 1e-12:
                    raise ValueError(
                        "fm.zero_axis: circular pattern needs a non-zero axis "
                        "vector.")
            return self

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        from phone_designer.skills.assembly._compound import (
            apply_transform_shape,
            build_compound,
            make_axis_rotation_trsf,
            make_translation_trsf,
        )

        if body is None:
            raise ValueError(
                "fm.pattern_no_body: pattern_seed_body needs an input seed body.")
        shape = body.wrapped if hasattr(body, "wrapped") else body

        # ── generate the per-instance transforms (i=0 is identity, the seed) ──
        trsfs = []
        if args.mode == "linear":
            dx, dy, dz = args.direction
            n = math.sqrt(dx * dx + dy * dy + dz * dz)
            ux, uy, uz = dx / n, dy / n, dz / n
            for i in range(args.count):
                d = i * args.spacing_mm
                trsfs.append(make_translation_trsf(ux * d, uy * d, uz * d))
        else:  # circular
            ax, ay, az = args.axis
            cx, cy, cz = args.center_mm
            full = abs(args.total_angle_deg - 360.0) < 1e-9
            if args.count <= 1:
                step_deg = 0.0
            elif full:
                step_deg = args.total_angle_deg / args.count
            else:
                step_deg = args.total_angle_deg / (args.count - 1)
            to_c = make_translation_trsf(-cx, -cy, -cz)
            back = make_translation_trsf(cx, cy, cz)
            for i in range(args.count):
                if i == 0:
                    trsfs.append(make_translation_trsf(0.0, 0.0, 0.0))
                    continue
                rot = make_axis_rotation_trsf(
                    ax, ay, az, math.radians(i * step_deg))
                # rotate about an axis through center_mm: T(c)·R·T(-c).
                trsfs.append(back.Multiplied(rot).Multiplied(to_c))

        # ── materialise every copy (deep-copy transform) ──────────────────────
        try:
            copies = [apply_transform_shape(shape, t) for t in trsfs]
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"fm.pattern_failed: {type(exc).__name__}: {exc}")

        # ── fuse all copies; fall back to a compound if any fuse fails ────────
        if len(copies) == 1:
            out, fused = copies[0], True
        else:
            out, fused = _fuse_all(copies)
            if not fused or out is None or out.IsNull():
                out = build_compound(copies)
                fused = False

        vol = _volume(out)
        if out is None or out.IsNull() or vol <= _EPS_VOL:
            raise ValueError(
                f"fm.pattern_failed: result volume {vol:.6g}mm³ ≈ 0.")

        return SkillResult(
            body=Part(out),
            history=EntityHistoryMap(rules={
                "body_faces": HistoryRule.MODIFIED_INHERIT,
                "seam_edges": HistoryRule.GENERATED_NEW,
            }),
            extras={"transform": {
                "op": "pattern_seed_body",
                "mode": args.mode,
                "count": args.count,
                "fused": fused,
                "n_bodies": 1 if fused else len(copies),
                "direction": list(args.direction) if args.mode == "linear" else None,
                "spacing_mm": args.spacing_mm if args.mode == "linear" else None,
                "axis": list(args.axis) if args.mode == "circular" else None,
                "center_mm": list(args.center_mm) if args.mode == "circular" else None,
                "total_angle_deg": args.total_angle_deg if args.mode == "circular" else None,
                "volume_mm3": round(vol, 3),
                "bbox_mm": _bbox_mm(out),
            }},
        )
