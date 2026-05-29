"""class_a_surface_tag — atomic, metadata-only compose skill.

Attach a ``class_a`` flag to a body's selected faces so downstream skills
(finishing, polishing, decal placement, parting-line avoidance) know which
faces are cosmetic *Class-A* surfaces — those that the end user will see and
where blemishes, parting marks or ejector pins are forbidden.

Storage
    body._pd_class_a : dict[str, list[EntityRef]]
        Keyed by an internal label (default ``"class_a"``) so callers may
        attach multiple A-surface groups (e.g. ``"class_a_front"``,
        ``"class_a_rear"``). Mirrors the ``_pd_tags`` convention used by
        ``compose.tag_face``.

This skill is geometry-free — it never mutates the body. Post-condition is
``body_present`` only.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, EntityRef, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


# ──────────────────────────────────────────────────────────────────────────────
# Helpers — public so other skills (finishing, mold parting, decal) can read /
# write the class-A flag without re-importing the skill class.


def get_class_a_tags(body) -> dict[str, list[EntityRef]]:
    """Return ``body._pd_class_a`` (empty dict if absent; never mutates body)."""
    return getattr(body, "_pd_class_a", {}) or {}


def set_class_a_tags(body, tags: dict[str, list[EntityRef]]) -> None:
    """Setter for ``body._pd_class_a``. Best-effort — silently no-ops on
    objects that reject attribute assignment (e.g. __slots__)."""
    if body is None:
        return
    try:
        body._pd_class_a = tags
    except Exception:
        pass


def clear_class_a_tags(body) -> None:
    """Drop every class-A flag on ``body``. Used when a downstream skill
    invalidates the cosmetic classification (large topology mutation)."""
    if body is None:
        return
    try:
        if hasattr(body, "_pd_class_a"):
            body._pd_class_a = {}
    except Exception:
        pass


@skill(
    name="class_a_surface_tag",
    category="compose",
    level="atomic",
    summary="Tag the selected faces as Class-A (cosmetic) surfaces via "
            "body._pd_class_a. Geometry is unchanged — downstream finishing "
            "and mold-parting skills consult the flag.",
    selector_kinds=["faces"],
    history_rules={"tagged_faces": HistoryRule.MODIFIED_INHERIT},
    produces_features=["class_a_attribute"],
    preserves=["body_topology"],
    manufacturing={
        "injection_mold_pa": {"extras": {"class_a_aware": True}},
        "die_cast_al":       {"extras": {"class_a_aware": True}},
    },
    failure_modes=["fm.no_match"],
    cost_hint=0.02,
    post_conditions=[PostCondition(kind="body_present")],
)
class ClassASurfaceTag(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        label: str = Field(default="class_a", min_length=1, max_length=64)

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
                f"class_a_surface_tag: selector matched 0 faces — "
                f"selector={args.face_selector.model_dump()}"
            )

        refs: list[EntityRef] = []
        for f in faces:
            center = _face_center(f)
            area = _face_area(f)
            refs.append(EntityRef(
                tag=args.label,
                kind="face",
                bbox_center=(round(center[0], 3), round(center[1], 3), round(center[2], 3)),
                measure=round(area, 3),
            ))

        existing = dict(get_class_a_tags(body))
        existing[args.label] = refs
        set_class_a_tags(body, existing)

        history = EntityHistoryMap(
            rules={"tagged_faces": HistoryRule.MODIFIED_INHERIT},
            inherited=refs,
        )
        return SkillResult(body=body, history=history)
