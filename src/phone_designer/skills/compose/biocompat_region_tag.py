"""biocompat_region_tag — atomic, metadata-only compose skill.

Tag the selected faces with a biocompatibility class (USP Class VI, ISO 10993-5
cytotoxicity, ISO 10993-10 irritation/sensitization, or none) so downstream
material-selection, finishing, and supplier-qualification skills know which
faces contact patient / user tissue and therefore must use a qualified
material + sealant + surface finish.

Storage:
    body._pd_biocompat : dict[str, list[EntityRef]]
        Keyed by an internal label (default = biocompat class), e.g.
        ``"USP_VI"`` → list of EntityRef. Mirrors the convention used by
        compose.class_a_surface_tag and compose.tag_face.

Geometry is unchanged. Post-condition is body_present only.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, EntityRef, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


BiocompatClass = Literal["USP_VI", "ISO10993_5", "ISO10993_10", "none"]


# ── public helpers (importable by downstream skills / tests) ────────────────

def get_biocompat_tags(body) -> dict[str, list[EntityRef]]:
    """Return ``body._pd_biocompat`` (empty dict if absent; never mutates)."""
    return getattr(body, "_pd_biocompat", {}) or {}


def set_biocompat_tags(body, tags: dict[str, list[EntityRef]]) -> None:
    """Setter for ``body._pd_biocompat``. Best-effort — silently no-ops on
    objects that reject attribute assignment (e.g. ``__slots__``)."""
    if body is None:
        return
    try:
        body._pd_biocompat = tags
    except Exception:
        pass


def clear_biocompat_tags(body) -> None:
    """Drop every biocompatibility flag on ``body``."""
    if body is None:
        return
    try:
        if hasattr(body, "_pd_biocompat"):
            body._pd_biocompat = {}
    except Exception:
        pass


@skill(
    name="biocompat_region_tag",
    category="compose",
    level="atomic",
    summary="Tag the selected faces with a biocompatibility class (USP Class VI / "
            "ISO 10993-5 / ISO 10993-10 / none) via body._pd_biocompat. Geometry "
            "is unchanged — downstream material and finishing skills consult the flag.",
    selector_kinds=["faces"],
    history_rules={"biocompat_tagged_faces": HistoryRule.MODIFIED_INHERIT},
    produces_features=["biocompat_attribute"],
    preserves=["body_topology", "outer_envelope"],
    manufacturing={
        "injection_mold_pa": {"extras": {"biocompat_aware": True,
                                          "medical_grade": True}},
        "cnc_3axis":         {"extras": {"biocompat_aware": True}},
    },
    failure_modes=["fm.no_match"],
    cost_hint=0.02,
    post_conditions=[PostCondition(kind="body_present")],
)
class BiocompatRegionTag(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        biocompat_class: BiocompatClass = Field(
            default="USP_VI",
            description="Biocompatibility class for the patient-contact region.",
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
                f"biocompat_region_tag: selector matched 0 faces — "
                f"biocompat_class='{args.biocompat_class}', "
                f"selector={args.face_selector.model_dump()}"
            )

        refs: list[EntityRef] = []
        for f in faces:
            c = _face_center(f)
            a = _face_area(f)
            refs.append(EntityRef(
                tag=args.biocompat_class,
                kind="face",
                bbox_center=(round(c[0], 3), round(c[1], 3), round(c[2], 3)),
                measure=round(a, 3),
            ))

        # Merge into existing biocompat store, keyed by class.
        existing = dict(get_biocompat_tags(body))
        prior = list(existing.get(args.biocompat_class, []))
        prior.extend(refs)
        existing[args.biocompat_class] = prior
        set_biocompat_tags(body, existing)

        history = EntityHistoryMap(
            rules={"biocompat_tagged_faces": HistoryRule.MODIFIED_INHERIT},
            inherited=refs,
        )
        return SkillResult(
            body=body,
            history=history,
            extras={
                "biocompat_class": args.biocompat_class,
                "face_count": len(faces),
            },
        )
