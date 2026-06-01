"""apply_paint — atomic.

Catalog-driven paint finish (wet-spray / powder). Tags the body with
``body._pd_paint`` and optionally grows the painted regions by the cured
film thickness via a uniform skin offset.

Catalog: catalogs/finishes/paint.yaml — keyed by ``paint_spec``.
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


def get_paint_tags(body) -> list[dict]:
    return list(getattr(body, "_pd_paint", []) or [])


def set_paint_tags(body, tags: list[dict]) -> None:
    if body is None:
        return
    try:
        body._pd_paint = tags
    except Exception:
        pass


@skill(
    name="apply_paint",
    category="modify/finish",
    level="atomic",
    summary="Catalog-driven paint finish (Pantone-keyed wet-spray). Tags "
            "faces with the paint spec and optionally grows the body envelope "
            "by the cured film thickness.",
    selector_kinds=["faces"],
    history_rules={
        "paint_tagged_faces": HistoryRule.MODIFIED_INHERIT,
        "all_faces": HistoryRule.MODIFIED_INHERIT,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.paint_spec_in_catalog",
    ],
    produces_features=["paint_metadata"],
    preserves=["body_topology"],
    manufacturing={
        "cnc_3axis": {"extras": {"requires_primer": True}},
        "die_cast_al": {"extras": {"requires_primer": True}},
        "injection_mold_pa": {"extras": {"surface_prep": "plasma_or_flame"}},
    },
    failure_modes=[
        "fm.paint_spec_unknown",
        "fm.offset_self_intersection",
    ],
    cost_hint=0.10,
    post_conditions=[PostCondition(kind="body_present")],
)
class ApplyPaint(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        paint_spec: str = Field(
            description="Key in catalogs/finishes/paint.yaml — e.g. "
                        "'Paint_Pantone_Cool_Gray_9'.",
        )
        grow_geometry: bool = Field(
            default=False,
            description="If True, expand body by film thickness.",
        )
        offset_tolerance_mm: float = Field(default=0.01, gt=0, le=1.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        from phone_designer.skills._resolvers import (
            _face_area, _face_center, resolve_faces,
        )

        catalog = _load("finishes", "paint")
        finishes = catalog.get("finishes", {})
        if args.paint_spec not in finishes:
            raise ValueError(
                f"apply_paint: unknown paint_spec '{args.paint_spec}' — "
                f"catalog keys: {sorted(finishes.keys())}"
            )
        entry = finishes[args.paint_spec]

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"apply_paint: face_selector matched 0 faces — "
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
            "paint_spec": args.paint_spec,
            "process": entry.get("process"),
            "pantone": entry.get("pantone"),
            "color": entry.get("color"),
            "thickness_um": entry.get("thickness_um"),
            "dimensional_growth_um": entry.get("dimensional_growth_um"),
            "durability_rating": entry.get("durability_rating"),
            "regions": region_records,
        }
        tags = get_paint_tags(body)
        tags.append(record)
        set_paint_tags(body, tags)

        result_shape = shape
        if args.grow_geometry:
            from OCP.BRepOffset import BRepOffset_Skin
            from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
            from OCP.GeomAbs import GeomAbs_Intersection

            growth_mm = float(entry.get("dimensional_growth_um", 0.0)) / 1000.0
            if growth_mm > 1e-7:
                maker = BRepOffsetAPI_MakeOffsetShape()
                try:
                    maker.PerformByJoin(
                        shape, growth_mm, args.offset_tolerance_mm,
                        BRepOffset_Skin, False, False,
                        GeomAbs_Intersection, False,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"apply_paint: skin offset failed ({growth_mm} mm): {exc}"
                    )
                if not maker.IsDone():
                    raise RuntimeError(
                        f"apply_paint: offset IsDone=False (growth={growth_mm} mm)"
                    )
                result_shape = maker.Shape()

        new_body = Part(result_shape) if args.grow_geometry else body
        if args.grow_geometry:
            set_paint_tags(new_body, tags)

        history = EntityHistoryMap(
            rules={
                "paint_tagged_faces": HistoryRule.MODIFIED_INHERIT,
                "all_faces": HistoryRule.MODIFIED_INHERIT,
            },
        )
        return SkillResult(
            body=new_body,
            history=history,
            extras={
                "paint_spec": args.paint_spec,
                "face_count": len(faces),
                "grew_geometry": args.grow_geometry,
                "growth_um_per_side": entry.get("dimensional_growth_um"),
            },
        )
