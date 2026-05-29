"""tag_inventory — atomic, read-only.

현재 body 에 부착된 모든 tag 를 나열한다. 각 tag 마다:

  - tag name
  - set_by_skill: 어느 skill 이 이 tag 를 부착했는지 (기록되어 있으면)
  - history:     EntityRef.bbox_center / measure / kind 등 부착 당시 metadata
  - current_resolution:
      matched      : 지금 body 에서 tagged selector 로 다시 잡았을 때 face 갯수
      face_centers : 매칭된 face 들의 중심 좌표 list

용도:
  - LLM 가 plan 중 "지금 어떤 tag 가 살아있나" 확인 (compose_tag_face 부른 뒤).
  - tag 가 mutation 으로 drift 했는지 진단 (history.bbox_center vs current center).
  - debug — 왜 tagged selector 가 0 매칭이냐는 질문에 답.

body 가 mutate 되지 않음 (post body_present 만 필요).

extras["tags"] = [
    {
      "tag":            str,
      "set_by_skill":   str,           # 알 수 없으면 "unknown"
      "history": [                     # 부착 당시 EntityRef 들
          {"kind": "face", "bbox_center": [x,y,z], "measure": float,
           "convexity": str},
          ...
      ],
      "current_resolution": {
          "matched":      int,
          "face_centers": [[x,y,z], ...],
          "surface_types": [str, ...],
      },
    },
    ...
]
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _surface_type_name(face) -> str:
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import (
            GeomAbs_BezierSurface, GeomAbs_BSplineSurface, GeomAbs_Cone,
            GeomAbs_Cylinder, GeomAbs_OffsetSurface, GeomAbs_OtherSurface,
            GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_SurfaceOfExtrusion,
            GeomAbs_SurfaceOfRevolution, GeomAbs_Torus,
        )
        mp = {
            GeomAbs_Plane: "plane",
            GeomAbs_Cylinder: "cylinder",
            GeomAbs_Cone: "cone",
            GeomAbs_Sphere: "sphere",
            GeomAbs_Torus: "torus",
            GeomAbs_BSplineSurface: "bspline",
            GeomAbs_BezierSurface: "bezier",
            GeomAbs_OffsetSurface: "offset",
            GeomAbs_SurfaceOfExtrusion: "extrusion",
            GeomAbs_SurfaceOfRevolution: "revolution",
            GeomAbs_OtherSurface: "other",
        }
        surf = BRepAdaptor_Surface(face)
        return mp.get(int(surf.GetType()), "unknown")
    except Exception:
        return "unknown"


def _ref_to_history(ref) -> dict:
    """EntityRef → JSON dict for the `history` list entry."""
    return {
        "kind": getattr(ref, "kind", "unknown"),
        "bbox_center": [
            round(c, 4) for c in (getattr(ref, "bbox_center", (0.0, 0.0, 0.0)) or (0.0, 0.0, 0.0))
        ],
        "measure": round(float(getattr(ref, "measure", 0.0) or 0.0), 4),
        "convexity": getattr(ref, "convexity", "n/a"),
    }


@skill(
    name="tag_inventory",
    category="inspect",
    level="atomic",
    summary="List every tag currently attached to the body. For each tag, "
            "return the EntityRef metadata captured at tag-set time (history) "
            "and a fresh resolution of the tagged selector against the current "
            "body (current_resolution.matched + face_centers). Useful to see "
            "which tags are still live after geometry mutations.",
    selector_kinds=[],
    history_rules={},
    produces_features=["tag_inventory_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class TagInventory(SkillBase):
    class Args(BaseModel):
        pass

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import (
            _face_center,
            resolve_faces,
        )
        from phone_designer.skills._selectors import TaggedSelector
        from phone_designer.skills.compose.tag_face import get_tags

        tags_store = get_tags(body)
        shape = _occt_shape(body)

        out: list[dict] = []
        for tag_name, refs in tags_store.items():
            history = [_ref_to_history(r) for r in (refs or [])]

            # set_by_skill — 현재 인프라가 트래킹 안 함. 가능한 추론:
            # EntityRef 에 set_by_skill attribute 가 있으면 그 값 (downstream skill
            # 들이 직접 채울 수도 있으니까), 아니면 "tag_face" (유일한 tag 생성기) 추정.
            set_by_skill = "unknown"
            for r in (refs or []):
                v = getattr(r, "set_by_skill", None)
                if v:
                    set_by_skill = v
                    break
            if set_by_skill == "unknown":
                # tag_face 가 현 단계의 유일한 tagger — heuristic default.
                set_by_skill = "tag_face"

            # current_resolution — tagged selector 로 다시 잡기
            face_centers: list[list[float]] = []
            surface_types: list[str] = []
            matched = 0
            try:
                sel = TaggedSelector(tag=tag_name)
                faces = resolve_faces(shape, sel, body=body)
                matched = len(faces)
                for f in faces:
                    try:
                        c = _face_center(f)
                        face_centers.append(
                            [round(c[0], 4), round(c[1], 4), round(c[2], 4)]
                        )
                    except Exception:
                        face_centers.append([0.0, 0.0, 0.0])
                    surface_types.append(_surface_type_name(f))
            except Exception:
                # resolver 실패 — best-effort, matched 0 으로 둔다.
                matched = 0

            out.append({
                "tag": tag_name,
                "set_by_skill": set_by_skill,
                "history": history,
                "current_resolution": {
                    "matched": matched,
                    "face_centers": face_centers,
                    "surface_types": surface_types,
                },
            })

        extras = {"tags": out}
        return SkillResult(body=body, history=EntityHistoryMap(), extras=extras)
