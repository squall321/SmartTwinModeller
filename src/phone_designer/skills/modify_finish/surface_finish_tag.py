"""surface_finish_tag — atomic. Face 에 마감 (polish/matte/texture) 메타데이터 부착.

[[lat.md/persistent-naming]] 의 tag_face 와 비슷한 metadata-only skill 이지만,
역할이 다르다:
    tag_face            : 후속 skill 이 selector 로 face 를 다시 찾기 위한 이름표.
    surface_finish_tag  : 제조 공정 (polish/matte/texture A/B) 의 target Ra 메타데이터.

저장 위치:
    body._pd_finish: dict[str, list[FinishRecord_dict]]
    - key       : finish_type ("polish" | "matte" | "texture_a" | "texture_b")
    - records   : 매칭된 face 의 (center, area_mm2, target_ra_um) dict 리스트

geometry 는 절대 변경하지 않는다. PostCondition 은 body_present 만.

NOTE: tag_face._pd_tags 와 별도 namespace 라서 둘 다 동시 부착 가능.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


FinishType = Literal["polish", "matte", "texture_a", "texture_b"]


# ----- public helpers (importable by tests / downstream skills) -----

def get_finish_tags(body) -> dict[str, list[dict]]:
    """body._pd_finish 안전 getter (없으면 빈 dict)."""
    return getattr(body, "_pd_finish", {}) or {}


def set_finish_tags(body, tags: dict[str, list[dict]]) -> None:
    """body._pd_finish setter (build123d Part 호환)."""
    if body is None:
        return
    try:
        body._pd_finish = tags
    except Exception:
        # Part 가 __slots__ 면 swallow (현 build123d 0.10 에서는 일반 obj).
        pass


@skill(
    name="surface_finish_tag",
    category="modify/finish",
    level="atomic",
    summary="Attach surface-finish metadata (polish/matte/texture_a/texture_b + "
            "target Ra in µm) to faces matched by a selector. Geometry is NOT "
            "changed — pure metadata for downstream DFM / process planning.",
    selector_kinds=["faces"],
    history_rules={"finish_tagged_faces": HistoryRule.MODIFIED_INHERIT},
    produces_features=["finish_metadata"],
    preserves=["body_topology", "outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"extras": {"finish_ra_um": "args.target_ra_um"}},
        "die_cast_al":       {"extras": {"finish_ra_um": "args.target_ra_um"}},
        "injection_mold_pa": {"extras": {"finish_ra_um": "args.target_ra_um"}},
    },
    failure_modes=["fm.no_match"],
    cost_hint=0.02,
    # No geometry change — only check body survives.
    post_conditions=[PostCondition(kind="body_present")],
)
class SurfaceFinishTag(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        finish_type: FinishType
        target_ra_um: float = Field(
            default=0.8, ge=0.01, le=50.0,
            description="목표 Ra (μm). polish: 0.05~0.4, matte: 0.4~1.6, "
                        "texture_a/b: 1.6~12.5 권장.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import (
            _face_area,
            _face_center,
            resolve_faces,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"surface_finish_tag: face_selector matched 0 faces — "
                f"finish_type='{args.finish_type}', "
                f"selector={args.face_selector.model_dump()}"
            )

        records = []
        for f in faces:
            c = _face_center(f)
            a = _face_area(f)
            records.append({
                "center": (round(c[0], 3), round(c[1], 3), round(c[2], 3)),
                "area_mm2": round(a, 3),
                "target_ra_um": round(args.target_ra_um, 4),
            })

        # 기존 finish store 와 병합 (같은 finish_type 은 append).
        existing = dict(get_finish_tags(body))    # shallow copy
        prior = list(existing.get(args.finish_type, []))
        prior.extend(records)
        existing[args.finish_type] = prior
        set_finish_tags(body, existing)

        history = EntityHistoryMap(
            rules={"finish_tagged_faces": HistoryRule.MODIFIED_INHERIT},
        )
        return SkillResult(
            body=body,
            history=history,
            extras={
                "finish_type": args.finish_type,
                "target_ra_um": args.target_ra_um,
                "face_count": len(faces),
            },
        )
