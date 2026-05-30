"""gating_position_candidates — atomic, read-only inspect.

Propose the best ``num_candidates`` gating positions for an injection-molded
part. For every face of the body we compute:

    * flow length = max distance from the face center to any other face
      center (a crude but stable proxy for fill-front travel),
    * cosmetic side = whether the face is on the A-surface (visible to the
      viewer per ``cosmetic_side_classify``).

Score:

    score = -flow_length_penalty - cosmetic_penalty
          = -(flow_length_mm / bbox_diagonal)
            - (1.0 if on_cosmetic else 0.0)

Higher score = better gating location (short flow path, not on A-side).
We sort candidates descending by score and return the top N.

extras:
    extras["gate_candidates"] = [
        {"face_idx": int,
         "position": [x,y,z],
         "flow_length_mm": float,
         "on_cosmetic": bool,
         "score": float,
         "area_mm2": float},
        ...
    ]
    extras["gate_material"] = "abs"|"pa"|"pc"
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


# Per-material penalty multipliers used when ranking flow length against the
# cosmetic constraint. Higher value = the material is more sensitive to long
# flow paths (so flow length dominates the score).
_MATERIAL_FLOW_WEIGHT = {
    "abs": 1.0,
    "pa":  1.2,   # PA fills harder/cools faster — penalise long flow more
    "pc":  1.4,
}


@skill(
    name="gating_position_candidates",
    category="inspect",
    level="atomic",
    summary="Rank candidate gating positions for an injection-molded part by "
            "flow length and cosmetic visibility. Returns the top "
            "num_candidates faces with shortest flow path that are NOT on the "
            "A-surface. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["gate_candidates"],
    preserves=["body_topology"],
    manufacturing={
        "injection_mold_pa": {"extras": {"flow_aware": True}},
    },
    failure_modes=["fm.no_faces_found"],
    cost_hint=0.5,
    post_conditions=[PostCondition(kind="body_present")],
)
class GatingPositionCandidates(SkillBase):
    class Args(BaseModel):
        material: Literal["abs", "pa", "pc"] = "abs"
        num_candidates: int = Field(default=3, ge=1, le=50)
        viewer_position: tuple[float, float, float] = Field(
            default=(100.0, 0.0, 50.0),
            description="Viewer position passed through to cosmetic_side_classify.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import (
            _all_faces, _face_area, _face_center,
        )
        from phone_designer.skills.inspect.cosmetic_side_classify import (
            CosmeticSideClassify,
        )

        shape = _occt_shape(body)
        faces = _all_faces(shape)
        if not faces:
            raise RuntimeError("gating_position_candidates: body has no faces")

        # 1. Cosmetic classification (delegate to existing skill).
        cos_result = CosmeticSideClassify().apply(
            body, {"viewer_position": args.viewer_position},
        )
        a_faces = set(cos_result.extras.get("a_faces", []))

        # 2. Face centers + areas.
        centers: list[tuple[float, float, float]] = []
        areas: list[float] = []
        for f in faces:
            try:
                c = _face_center(f)
            except Exception:
                c = (0.0, 0.0, 0.0)
            try:
                a = _face_area(f)
            except Exception:
                a = 0.0
            centers.append(c)
            areas.append(a)

        # 3. Body bbox diagonal — used to normalise flow length.
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib

        bb = Bnd_Box()
        try:
            BRepBndLib.AddOptimal_s(shape, bb)
        except Exception:
            BRepBndLib.Add_s(shape, bb)
        if bb.IsVoid():
            diag = 1.0
        else:
            xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
            diag = math.sqrt(
                (xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2,
            )
            diag = max(diag, 1.0)

        flow_w = _MATERIAL_FLOW_WEIGHT.get(args.material, 1.0)

        # 4. For each face, flow length = max distance to any other face center.
        all_candidates: list[dict[str, Any]] = []
        for i, c in enumerate(centers):
            max_d = 0.0
            for j, c2 in enumerate(centers):
                if i == j:
                    continue
                dx = c[0] - c2[0]
                dy = c[1] - c2[1]
                dz = c[2] - c2[2]
                d = math.sqrt(dx * dx + dy * dy + dz * dz)
                if d > max_d:
                    max_d = d
            on_cosm = i in a_faces
            score = -(max_d / diag) * flow_w - (1.0 if on_cosm else 0.0)
            all_candidates.append({
                "face_idx": i,
                "position": [round(v, 4) for v in c],
                "flow_length_mm": round(max_d, 4),
                "on_cosmetic": bool(on_cosm),
                "score": round(score, 6),
                "area_mm2": round(areas[i], 4),
            })

        # 5. Sort by score descending and take top N.
        all_candidates.sort(key=lambda d: d["score"], reverse=True)
        top = all_candidates[: args.num_candidates]

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "gate_candidates": top,
                "gate_material": args.material,
            },
        )
