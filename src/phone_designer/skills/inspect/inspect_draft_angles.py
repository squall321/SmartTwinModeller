"""inspect_draft_angles — atomic, read-only DFM inspect.

Per-face draft-angle report against a mold ``pull_direction``. Unlike
``inspect_undercut_zones_v2`` (one binary sample at the UV midpoint —
documented false-negative on curved faces whose midpoint normal is
roughly perpendicular to the pull, e.g. a horizontal bore), this skill
samples a ``samples_per_face`` × ``samples_per_face`` UV grid of surface
normals via ``BRepLProp_SLProps`` and reports the signed draft angle
distribution of every wall face.

Signed draft convention (see ``_draft_math.signed_draft_deg``):
    draft = 90° − angle(outward_normal, pull) = asin(dot(n̂, p̂))
    positive  → wall opens toward the pull (moldable)
    0         → dead-vertical wall
    negative  → undercut (needs side-action tooling)

Per face we report min/mean draft over the samples and a verdict:
    ``undercut``         min draft < 0 (negative draft anywhere on face)
    ``below_min_draft``  0 ≤ min draft < ``min_draft_deg``
    ``ok``               min draft ≥ ``min_draft_deg``

Faces whose normal is within 5° of ±pull_direction are skipped — those
are top/bottom faces seen straight down the pull axis, not drafted
walls. The skip test requires EVERY sampled normal to be within 5°, so
a curved face that merely passes through the perpendicular (the v2
false-negative case) is still analysed.

extras["draft_report"] schema:
    {
      "pull_direction":  [px, py, pz],     # unit
      "min_draft_deg":   float,
      "faces": [
        {"face_index":   int,              # index in _all_faces order
         "min_deg":      float,
         "mean_deg":     float,
         "verdict":      "ok"|"below_min_draft"|"undercut",
         "worst_uv_xyz": [x, y, z]},       # sample point with min draft
        ...
      ],
      "counts": {"ok": int, "below_min_draft": int, "undercut": int},
      "skipped_face_count": int,
      "guarded": bool,                     # True → face-count guard tripped
    }

Body unchanged — post ``body_present``.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._face_count_guard import (
    DEFAULT_MAX_FACE_COUNT as _DEFAULT_MAX_FACE_COUNT,
)
from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.inspect._draft_math import signed_draft_deg, unit


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


# Faces whose every sampled normal is within this many degrees of the
# ±pull axis are top/bottom faces, not drafted walls — skipped.
_SKIP_PULL_ANGLE_DEG = 5.0

# Negative-draft verdict threshold. Analytic surface normals are exact to
# ~1e-12, so anything below -1e-4 deg is a real overhang, while a dead
# vertical wall's -0.0 noise stays "below_min_draft" rather than "undercut".
_NEG_DRAFT_EPS_DEG = 1e-4


def _sample_normals_xyz(
    face, side: int,
) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    """Sample a ``side`` × ``side`` UV grid on ``face``.

    Returns ``[((nx, ny, nz), (x, y, z)), ...]`` — outward unit normal and
    3D point per sample. Normals come from ``BRepLProp_SLProps`` with the
    face orientation flag applied (REVERSED → flip), same pattern as
    ``gdt_flatness``'s UV-grid sampler. Samples where the normal is
    undefined (poles, degenerate parameterisations) are dropped.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.TopAbs import TopAbs_REVERSED

    adaptor = BRepAdaptor_Surface(face)
    u0 = adaptor.FirstUParameter()
    u1 = adaptor.LastUParameter()
    v0 = adaptor.FirstVParameter()
    v1 = adaptor.LastVParameter()
    flip = face.Orientation() == TopAbs_REVERSED

    out: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    for i in range(side):
        for j in range(side):
            # Cell-centred — avoids degenerate parameter edges (gdt_flatness).
            fu = (i + 0.5) / side
            fv = (j + 0.5) / side
            u = u0 + (u1 - u0) * fu
            v = v0 + (v1 - v0) * fv
            try:
                slp = BRepLProp_SLProps(adaptor, u, v, 1, 1e-6)
                if not slp.IsNormalDefined():
                    continue
                n = slp.Normal()
                p = slp.Value()
            except Exception:
                continue
            nx, ny, nz = n.X(), n.Y(), n.Z()
            if flip:
                nx, ny, nz = -nx, -ny, -nz
            out.append(((nx, ny, nz), (p.X(), p.Y(), p.Z())))
    return out


@skill(
    name="inspect_draft_angles",
    category="inspect",
    level="atomic",
    summary="Per-face signed draft-angle report vs a mold pull_direction. "
            "Samples an NxN UV grid of surface normals per face "
            "(BRepLProp_SLProps) and reports min/mean draft + verdict "
            "(ok / below_min_draft / undercut). Catches curved-face "
            "undercuts that the 1-sample inspect_undercut_zones_v2 check "
            "misses. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["draft_angle_report"],
    preserves=["body_topology"],
    manufacturing={
        "die_cast_al":       {"min_draft_deg": 1.0},
        "injection_mold_pa": {"min_draft_deg": 0.5},
    },
    failure_modes=["fm.zero_pull_vector"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class InspectDraftAngles(SkillBase):
    class Args(BaseModel):
        pull_direction: list[float] = Field(
            default=[0.0, 0.0, 1.0], min_length=3, max_length=3,
            description="Mold opening direction. Must be non-zero; "
                        "normalised internally.",
        )
        min_draft_deg: float = Field(
            default=1.0, ge=0.0,
            description="Walls with 0 <= min draft < this are flagged "
                        "below_min_draft.",
        )
        samples_per_face: int = Field(
            default=5, ge=2, le=32,
            description="UV grid side per face — samples_per_face^2 normals "
                        "are evaluated on each face (default 5x5).",
        )
        max_face_count: int | None = Field(
            default=_DEFAULT_MAX_FACE_COUNT,
            description="If the body has more than this many faces, skip the "
                        "analysis and return a guarded draft_report "
                        "(guarded=True, faces=[]). Set to None to disable. "
                        f"Default {_DEFAULT_MAX_FACE_COUNT} (see "
                        "_face_count_guard; override with "
                        "PHONE_DESIGNER_MAX_FACE_COUNT).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces

        if body is None:
            raise ValueError("inspect_draft_angles: body is None")

        px, py, pz = (float(c) for c in args.pull_direction)
        pdir = unit((px, py, pz))
        if pdir is None:
            raise ValueError(
                "inspect_draft_angles: pull_direction must be non-zero",
            )

        shape = _occt_shape(body)
        faces = _all_faces(shape)

        report: dict[str, Any] = {
            "pull_direction": [round(c, 6) for c in pdir],
            "min_draft_deg": float(args.min_draft_deg),
            "faces": [],
            "counts": {"ok": 0, "below_min_draft": 0, "undercut": 0},
            "skipped_face_count": 0,
            "guarded": False,
        }

        # ── face-count guard (classify_pockets pattern) ─────────────────────
        if args.max_face_count is not None and len(faces) > args.max_face_count:
            report["guarded"] = True
            report["face_count"] = len(faces)
            report["limit"] = args.max_face_count
            report["advice"] = (
                "decimate the input mesh (mesh_decimate skill) or "
                "simplify_to_canonical the BREP before calling "
                "inspect_draft_angles."
            )
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={"draft_report": report},
            )

        side = int(args.samples_per_face)
        counts = report["counts"]
        for idx, face in enumerate(faces):
            samples = _sample_normals_xyz(face, side)
            drafts: list[tuple[float, tuple[float, float, float]]] = []
            for normal, xyz in samples:
                d = signed_draft_deg(normal, pdir)
                if d is None:
                    continue
                drafts.append((d, xyz))
            if not drafts:
                # No usable normal anywhere — cannot judge; count as skipped.
                report["skipped_face_count"] += 1
                continue

            # Skip top/bottom faces: normal within 5° of ±pull everywhere on
            # the face (|signed draft| > 85° ⟺ unsigned pull angle < 5°).
            # Requiring EVERY sample keeps curved faces that merely pass
            # through the perpendicular (v2's false-negative bore case) in.
            if all(abs(d) > 90.0 - _SKIP_PULL_ANGLE_DEG for d, _ in drafts):
                report["skipped_face_count"] += 1
                continue

            min_deg, worst_xyz = min(drafts, key=lambda t: t[0])
            mean_deg = sum(d for d, _ in drafts) / len(drafts)
            if min_deg < -_NEG_DRAFT_EPS_DEG:
                verdict = "undercut"
            elif min_deg < args.min_draft_deg:
                verdict = "below_min_draft"
            else:
                verdict = "ok"
            counts[verdict] += 1
            report["faces"].append({
                "face_index": idx,
                "min_deg": round(min_deg, 4),
                "mean_deg": round(mean_deg, 4),
                "verdict": verdict,
                "worst_uv_xyz": [round(c, 4) for c in worst_xyz],
            })

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"draft_report": report},
        )
