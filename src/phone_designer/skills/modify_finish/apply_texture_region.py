"""apply_texture_region — atomic.

Catalog-driven tool-side texture (VDI 3400 / Mold-Tech) for an injection-
mold or die-cast face. Tags faces with ``body._pd_texture`` recording the
target Ra, etch depth, and ADDITIONAL draft (``draft_delta_deg``) required
above the base draft for safe ejection without scuffing the texture.

Catalog: catalogs/finishes/textures.yaml — keyed by ``texture_spec``.

The skill is metadata-only (no geometry change) — downstream draft-check
skills should read ``body._pd_texture`` and increase their required-draft
threshold by ``draft_delta_deg`` for the tagged faces.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def _load(family: str, name: str):
    import pathlib
    import yaml
    # parents: [0]=modify_finish, [1]=skills, [2]=phone_designer, [3]=src,
    # [4]=project root (where catalogs/ lives).
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load(
        (root / "catalogs" / family / f"{name}.yaml").read_text()
    )


def get_texture_tags(body) -> list[dict]:
    return list(getattr(body, "_pd_texture", []) or [])


def set_texture_tags(body, tags: list[dict]) -> None:
    if body is None:
        return
    try:
        body._pd_texture = tags
    except Exception:
        pass


@skill(
    name="apply_texture_region",
    category="modify/finish",
    level="atomic",
    summary="Catalog-driven tool-side texture (VDI 3400 / Mold-Tech). Tags "
            "faces with target Ra, etch depth, and additional draft delta "
            "(deg) required above the base draft for safe ejection.",
    selector_kinds=["faces"],
    history_rules={
        "texture_tagged_faces": HistoryRule.MODIFIED_INHERIT,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.texture_spec_in_catalog",
    ],
    produces_features=["texture_metadata", "draft_requirement_delta"],
    preserves=["body_topology", "outer_envelope"],
    manufacturing={
        "die_cast_al": {"extras": {
            "draft_delta_deg": "args.texture_spec.draft_delta_deg",
        }},
        "injection_mold_pa": {"extras": {
            "draft_delta_deg": "args.texture_spec.draft_delta_deg",
        }},
    },
    failure_modes=["fm.texture_spec_unknown", "fm.no_match"],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class ApplyTextureRegion(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        texture_spec: str = Field(
            description="Key in catalogs/finishes/textures.yaml — e.g. "
                        "'VDI3400_27' or 'MoldTech_MT11020'.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import (
            _face_area, _face_center, resolve_faces,
        )

        catalog = _load("finishes", "textures")
        textures = catalog.get("textures", {})
        if args.texture_spec not in textures:
            raise ValueError(
                f"apply_texture_region: unknown texture_spec "
                f"'{args.texture_spec}' — catalog keys: {sorted(textures.keys())}"
            )
        entry = textures[args.texture_spec]

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"apply_texture_region: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )

        region_records = []
        for f in faces:
            c = _face_center(f)
            a = _face_area(f)
            region_records.append({
                "center": (round(c[0], 3), round(c[1], 3), round(c[2], 3)),
                "area_mm2": round(a, 3),
            })

        record = {
            "texture_spec": args.texture_spec,
            "process": entry.get("process"),
            "grade": entry.get("grade"),
            "ra_um": entry.get("ra_um"),
            "etch_depth_um": entry.get("etch_depth_um"),
            "draft_delta_deg": entry.get("draft_delta_deg"),
            "regions": region_records,
        }
        tags = get_texture_tags(body)
        tags.append(record)
        set_texture_tags(body, tags)

        history = EntityHistoryMap(
            rules={"texture_tagged_faces": HistoryRule.MODIFIED_INHERIT},
        )
        return SkillResult(
            body=body,
            history=history,
            extras={
                "texture_spec": args.texture_spec,
                "face_count": len(faces),
                "ra_um": entry.get("ra_um"),
                "draft_delta_deg": entry.get("draft_delta_deg"),
            },
        )
