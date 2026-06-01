"""apply_anodize — atomic.

Catalog-driven anodize finish for an aluminium housing. Tags the body with
``body._pd_anodize`` (per-region metadata) and OPTIONALLY grows the OUTER
envelope by the catalog `dimensional_growth_um_per_side` via a uniform
``BRepOffsetAPI_MakeOffsetShape`` skin offset.

Why "optionally grow":
    Anodize Type II (25 μm) grows the substrate ~12.5 μm per side. For
    consumer-electronics tolerances this is usually negligible, but for
    tight bearing fits or threaded interfaces the post-anodize envelope
    matters. The skill therefore exposes ``grow_geometry: bool`` (default
    False) so callers explicitly opt in.

Catalog: catalogs/finishes/anodize.yaml — keyed by ``anodize_spec``.

Post-condition: ``body_present`` — when grow_geometry=False this is pure
metadata; when True the offset succeeds and the body is present.
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


def get_anodize_tags(body) -> list[dict]:
    """Safe getter for body._pd_anodize (list of region dicts)."""
    return list(getattr(body, "_pd_anodize", []) or [])


def set_anodize_tags(body, tags: list[dict]) -> None:
    if body is None:
        return
    try:
        body._pd_anodize = tags
    except Exception:
        pass


@skill(
    name="apply_anodize",
    category="modify/finish",
    level="atomic",
    summary="Catalog-driven anodize finish (Type II / Type III / dyed). "
            "Tags faces with the anodize spec and optionally grows the body "
            "envelope by the per-side dimensional growth (BRepOffsetAPI_"
            "MakeOffsetShape skin offset).",
    selector_kinds=["faces"],
    history_rules={
        "anodize_tagged_faces": HistoryRule.MODIFIED_INHERIT,
        "all_faces": HistoryRule.MODIFIED_INHERIT,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.anodize_spec_in_catalog",
    ],
    produces_features=["anodize_metadata"],
    preserves=["body_topology"],
    manufacturing={
        "cnc_3axis": {"extras": {"post_machining_anodize": True}},
        "die_cast_al": {"extras": {"post_machining_anodize": True}},
    },
    failure_modes=[
        "fm.anodize_spec_unknown",
        "fm.offset_self_intersection",
    ],
    cost_hint=0.10,
    post_conditions=[PostCondition(kind="body_present")],
)
class ApplyAnodize(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        anodize_spec: str = Field(
            description="Key in catalogs/finishes/anodize.yaml — e.g. "
                        "'Anodize_Type_II'.",
        )
        grow_geometry: bool = Field(
            default=False,
            description="If True, also expand body by "
                        "dimensional_growth_um_per_side (skin offset).",
        )
        offset_tolerance_mm: float = Field(
            default=0.01, gt=0, le=1.0,
            description="OCCT tolerance for the grow-offset (if grow_geometry).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        from phone_designer.skills._resolvers import (
            _face_area, _face_center, resolve_faces,
        )

        catalog = _load("finishes", "anodize")
        finishes = catalog.get("finishes", {})
        if args.anodize_spec not in finishes:
            raise ValueError(
                f"apply_anodize: unknown anodize_spec '{args.anodize_spec}' — "
                f"catalog keys: {sorted(finishes.keys())}"
            )
        entry = finishes[args.anodize_spec]

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"apply_anodize: face_selector matched 0 faces — "
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
            "anodize_spec": args.anodize_spec,
            "process": entry.get("process"),
            "thickness_um": entry.get("thickness_um"),
            "hardness_hv": entry.get("hardness_hv"),
            "color": entry.get("color"),
            "max_substrate_temp_c": entry.get("max_substrate_temp_c"),
            "dimensional_growth_um_per_side":
                entry.get("dimensional_growth_um_per_side"),
            "regions": region_records,
        }
        tags = get_anodize_tags(body)
        tags.append(record)
        set_anodize_tags(body, tags)

        result_shape = shape
        if args.grow_geometry:
            from OCP.BRepOffset import BRepOffset_Skin
            from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
            from OCP.GeomAbs import GeomAbs_Intersection

            growth_um = float(entry.get("dimensional_growth_um_per_side", 0.0))
            growth_mm = growth_um / 1000.0
            if growth_mm > 1e-7:
                maker = BRepOffsetAPI_MakeOffsetShape()
                try:
                    maker.PerformByJoin(
                        shape,
                        growth_mm,
                        args.offset_tolerance_mm,
                        BRepOffset_Skin,
                        False,
                        False,
                        GeomAbs_Intersection,
                        False,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"apply_anodize: skin offset failed ({growth_mm} mm): {exc}"
                    )
                if not maker.IsDone():
                    raise RuntimeError(
                        f"apply_anodize: offset IsDone=False (growth={growth_mm} mm)"
                    )
                result_shape = maker.Shape()

        new_body = Part(result_shape) if args.grow_geometry else body
        # Re-attach tags to the new Part (offset returned a fresh shape).
        if args.grow_geometry:
            set_anodize_tags(new_body, tags)

        history = EntityHistoryMap(
            rules={
                "anodize_tagged_faces": HistoryRule.MODIFIED_INHERIT,
                "all_faces": HistoryRule.MODIFIED_INHERIT,
            },
        )
        return SkillResult(
            body=new_body,
            history=history,
            extras={
                "anodize_spec": args.anodize_spec,
                "face_count": len(faces),
                "grew_geometry": args.grow_geometry,
                "growth_um_per_side":
                    entry.get("dimensional_growth_um_per_side"),
            },
        )
