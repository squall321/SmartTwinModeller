"""tag_face — atomic. selector 가 매칭한 face 들에 명명 tag 부착.

[[lat.md/persistent-naming]] 의 tagged selector 의 source.

저장 방식 (Phase 4 v1):
  body._pd_tags: dict[str, list[EntityRef]]
  - Python attribute — build123d Part 가 받음
  - SkillBase.apply 가 후속 skill 에 자동 propagate (input → result)
  - 형상이 크게 mutate 되면 EntityRef 의 bbox_center 가 어긋날 수 있음 →
    tagged selector 가 nearest-match (Phase 4 v2) 로 처리

본 v1 의 한계:
  - tag 적용 시점의 face 위치만 기록. 후속 skill 이 face 위치 옮기면 tag 가 어긋남.
    fallback chain (tagged → face_named → position) 동작.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, EntityRef, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def get_tags(body) -> dict[str, list[EntityRef]]:
    """body._pd_tags 안전한 getter (없으면 빈 dict 반환, 절대 body 를 mutate 안 함)."""
    return getattr(body, "_pd_tags", {}) or {}


def set_tags(body, tags: dict[str, list[EntityRef]]) -> None:
    """body._pd_tags setter."""
    if body is None:
        return
    try:
        body._pd_tags = tags
    except Exception:
        # build123d Part 가 __slots__ 갖고 있으면 fail — 우회 위해 별도 dict 로
        # (현재 build123d 0.10 의 Part 는 일반 obj 라 attribute 받음)
        pass


def clear_tags(body) -> None:
    """body 의 모든 tag 제거. propagation 가 CONSUMED 로 판단했을 때 사용."""
    if body is None:
        return
    try:
        if hasattr(body, "_pd_tags"):
            body._pd_tags = {}
    except Exception:
        pass


@skill(
    name="tag_face",
    category="compose",
    level="atomic",
    summary="Attach a named tag to the faces matched by a selector. "
            "Tagged selector can later reference these faces by name (e.g., for fillet/chamfer).",
    selector_kinds=["faces"],
    history_rules={"tagged_faces": HistoryRule.MODIFIED_INHERIT},
    produces_features=["tag_attribute"],
    preserves=["body_topology"],
    manufacturing={},   # 공정에 영향 없음 (metadata only)
    failure_modes=["fm.no_match"],
    cost_hint=0.02,
    # tag_face does not mutate geometry — just confirm the body survives.
    post_conditions=[PostCondition(kind="body_present")],
)
class TagFace(SkillBase):
    class Args(BaseModel):
        selector: SelectorRef
        tag: str = Field(min_length=1, max_length=64)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import (
            resolve_faces,
            _face_center,
            _face_area,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.selector, body=body)
        if not faces:
            raise RuntimeError(
                f"tag_face: selector matched 0 faces — tag='{args.tag}', "
                f"selector={args.selector.model_dump()}"
            )

        # 매칭 face 의 EntityRef 생성
        refs = []
        for f in faces:
            center = _face_center(f)
            area = _face_area(f)
            refs.append(EntityRef(
                tag=args.tag,
                kind="face",
                bbox_center=(round(center[0], 3), round(center[1], 3), round(center[2], 3)),
                measure=round(area, 3),
            ))

        # body 의 tag store 에 추가 (기존 tags 보존)
        existing = dict(get_tags(body))   # copy
        existing[args.tag] = refs
        set_tags(body, existing)

        # body 는 그대로 (topology 변화 없음). history 에 tag 기록만.
        history = EntityHistoryMap(
            rules={"tagged_faces": HistoryRule.MODIFIED_INHERIT},
            inherited=refs,
        )
        return SkillResult(body=body, history=history)
