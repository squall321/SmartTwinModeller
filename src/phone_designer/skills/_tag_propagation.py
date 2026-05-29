"""Tag propagation v2 — input body 의 tag 들을 output body 로 옮긴다.

[[lat.md/persistent-naming]] 의 tagged selector v1 의 한계 (bbox-center
nearest-match 가 형상 변화로 어긋남) 를 보완. SkillBase.apply() 가 _apply 직후
호출하므로 모든 skill 에 자동 적용된다.

규칙 (skill.spec.history_rules 의 HistoryRule value 들에서 파생):
    - MODIFIED_INHERIT  → input ref → 가장 가까운 output face (≤ TOL_MM, surface
                          type 일치) 로 tag 이전. 일치 face 없으면 drop.
    - SPLIT_BRANCH      → TOL_MM 안의 모든 output face 에 tag 복사.
    - CONSUMED          → tag drop.
    - GENERATED_NEW     → input ref 없으므로 무관 (skip).

규칙이 없는 (history_rules == {}) skill 은 안전 측 default: MODIFIED_INHERIT-
for-all. inspect_geometry / tag_face 처럼 형상 그대로 두는 skill 도 자연스럽게
tag 가 유지된다.

robustness:
    어떤 OCCT 호출이 실패해도 raise 하지 않는다. logging.warning 후 input tags
    를 그대로 set 한다 (best-effort). skill 자체의 정상 실행을 가리지 않는다.
"""
from __future__ import annotations

import logging
from typing import Any

from phone_designer.skills._history import EntityRef, HistoryRule


# 거리 임계값 (mm). 5mm — pocket / fillet / chamfer 정도의 mutation 까지 잡는다.
_TOL_MM = 5.0
_TOL_MM_SQ = _TOL_MM * _TOL_MM

_log = logging.getLogger(__name__)


def _surface_type_of(face) -> str:
    """face 의 surface type — string. 실패 시 'unknown'."""
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface

        from phone_designer.skills.inspect.inspect_geometry import _surface_type_map

        surf = BRepAdaptor_Surface(face)
        stype_id = int(surf.GetType())
        return _surface_type_map().get(stype_id, f"unknown({stype_id})")
    except Exception:
        return "unknown"


def _face_center_safe(face) -> tuple[float, float, float] | None:
    try:
        from phone_designer.skills._resolvers import _face_center
        return _face_center(face)
    except Exception:
        return None


def _ref_surface_type(ref: EntityRef, candidate_faces: list,
                      centers: list[tuple[float, float, float]]) -> str | None:
    """ref 의 surface type 을 측정하지 않는다 (input body 가 사라졌음). 대신
    input ref 의 bbox_center 근처에 있는 input 식별이 어렵기 때문에 mapping 을
    포기하고, output 후보 face 들의 surface type 으로 판단한다 → 호출자가 None
    이면 surface_type filter 를 skip 한다."""
    return None


def _classify_rules(history_rules: dict[str, HistoryRule]) -> str:
    """4 rule value 들의 dominant behavior 를 1 단어로.

    Returns:
        "modified": MODIFIED_INHERIT 가 있다 — 일반 tag 전이
        "split":    SPLIT_BRANCH 있다 — 한 input → 여러 output 복사
        "consumed": CONSUMED 만 있다 — drop
        "default":  rules empty — MODIFIED_INHERIT-for-all (보수적 보존)
    """
    if not history_rules:
        return "default"
    values = set(history_rules.values())
    if HistoryRule.SPLIT_BRANCH in values:
        return "split"
    if HistoryRule.MODIFIED_INHERIT in values:
        return "modified"
    # MODIFIED 도 SPLIT 도 없고 CONSUMED 만 (또는 GENERATED_NEW 만) 인 경우.
    # GENERATED_NEW only → 새로운 entity 추가만 — input tag 는 보존해야 자연.
    if HistoryRule.CONSUMED in values and HistoryRule.MODIFIED_INHERIT not in values:
        if HistoryRule.GENERATED_NEW in values:
            # 일부 consumed + 일부 새로 생긴 — 보수적으로 modified 처리 (보존 시도)
            return "modified"
        return "consumed"
    # GENERATED_NEW only 등 — 보존
    return "modified"


def _match_face_for_ref(
    ref: EntityRef,
    candidate_centers: list[tuple[float, float, float]],
    candidate_types: list[str],
) -> int | None:
    """ref 와 가장 가까운 후보 face 의 index. ≤ _TOL_MM, surface_type 일치
    (ref.kind 가 face 이고 후보 face 가 동일 type). type 정보가 없으면 거리만.

    Returns:
        candidate index 또는 None
    """
    best = None
    best_d2 = float("inf")
    cx, cy, cz = ref.bbox_center
    for i, c in enumerate(candidate_centers):
        d2 = (c[0] - cx) ** 2 + (c[1] - cy) ** 2 + (c[2] - cz) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best = i
    if best is None or best_d2 > _TOL_MM_SQ:
        return None
    return best


def _split_matches_for_ref(
    ref: EntityRef,
    candidate_centers: list[tuple[float, float, float]],
) -> list[int]:
    """SPLIT_BRANCH — ref 근처 _TOL_MM 안의 모든 후보 face index."""
    out = []
    cx, cy, cz = ref.bbox_center
    for i, c in enumerate(candidate_centers):
        d2 = (c[0] - cx) ** 2 + (c[1] - cy) ** 2 + (c[2] - cz) ** 2
        if d2 <= _TOL_MM_SQ:
            out.append(i)
    return out


def propagate_tags(
    input_body: Any,
    output_body: Any,
    history_rules: dict[str, HistoryRule] | None,
) -> None:
    """SkillBase.apply 가 호출 — input body 의 tag store → output body 로 옮긴다.

    propagation 은 best-effort. 실패해도 raise 하지 않는다 (logging.warning).

    Args:
        input_body: skill _apply 이전의 body (None 이면 create skill — 할 일 없음)
        output_body: skill _apply 후 body (None 이면 skip)
        history_rules: skill.spec.history_rules. None / {} 이면 default 동작.
    """
    if input_body is None or output_body is None:
        return

    try:
        from phone_designer.skills.compose.tag_face import (
            clear_tags,
            get_tags,
            set_tags,
        )

        input_tags = get_tags(input_body)
        if not input_tags:
            # input 에 tag 없음 — output 에도 손 안 댄다 (_apply 가 직접 단 tag
            # 들이 있을 수 있으니 덮어쓰지 않는다).
            return

        mode = _classify_rules(history_rules or {})

        if mode == "consumed":
            # 전부 drop — output tags 도 비운다.
            clear_tags(output_body)
            return

        # output body 의 face 들 한 번만 측정 (모든 tag/ref 가 공유)
        from phone_designer.skills._resolvers import _all_faces

        out_shape = (output_body.wrapped
                     if hasattr(output_body, "wrapped") else output_body)
        candidate_faces = _all_faces(out_shape)
        candidate_centers: list[tuple[float, float, float]] = []
        candidate_types: list[str] = []
        for f in candidate_faces:
            c = _face_center_safe(f)
            candidate_centers.append(c if c is not None else (1e9, 1e9, 1e9))
            candidate_types.append(_surface_type_of(f))

        propagated: dict[str, list[EntityRef]] = {}
        for tag, refs in input_tags.items():
            new_refs: list[EntityRef] = []
            for ref in refs:
                if mode == "split":
                    matches = _split_matches_for_ref(ref, candidate_centers)
                    for idx in matches:
                        f = candidate_faces[idx]
                        c = candidate_centers[idx]
                        try:
                            from phone_designer.skills._resolvers import _face_area
                            a = _face_area(f)
                        except Exception:
                            a = ref.measure
                        new_refs.append(EntityRef(
                            tag=ref.tag,
                            kind=ref.kind,
                            bbox_center=(round(c[0], 3), round(c[1], 3),
                                         round(c[2], 3)),
                            measure=round(a, 3),
                            convexity=ref.convexity,
                        ))
                else:
                    # modified / default
                    idx = _match_face_for_ref(
                        ref, candidate_centers, candidate_types,
                    )
                    if idx is None:
                        # 매칭 없음 — drop (best-effort: tag 가 없어진 face 에
                        # 매달려 있었다는 신호).
                        continue
                    f = candidate_faces[idx]
                    c = candidate_centers[idx]
                    try:
                        from phone_designer.skills._resolvers import _face_area
                        a = _face_area(f)
                    except Exception:
                        a = ref.measure
                    new_refs.append(EntityRef(
                        tag=ref.tag,
                        kind=ref.kind,
                        bbox_center=(round(c[0], 3), round(c[1], 3),
                                     round(c[2], 3)),
                        measure=round(a, 3),
                        convexity=ref.convexity,
                    ))
            if new_refs:
                propagated[tag] = new_refs
            # else: tag dropped silently

        # output body 에 이미 새로 단 tag 가 있을 수 있으니 merge (output 우선)
        out_tags = get_tags(output_body)
        merged = {**propagated, **out_tags}
        set_tags(output_body, merged)
    except Exception as exc:  # pragma: no cover — defensive
        _log.warning("tag propagation failed (best-effort, skipped): %s", exc)
