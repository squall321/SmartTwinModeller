"""apply_plating — atomic.

Catalog-driven metallic plating (electroless / electrolytic). Tags the body
with ``body._pd_plating`` and optionally grows the geometry by the deposit
thickness. For ``double_sided=True`` the growth is applied uniformly to
every face (full skin offset); for ``double_sided=False`` only the selected
faces are tagged and the grow step uses the same skin offset but the user
is opting in for the asymmetric case (kept as tag-only).

Catalog: catalogs/finishes/plating.yaml — keyed by ``plating_spec``.
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


def get_plating_tags(body) -> list[dict]:
    return list(getattr(body, "_pd_plating", []) or [])


def set_plating_tags(body, tags: list[dict]) -> None:
    if body is None:
        return
    try:
        body._pd_plating = tags
    except Exception:
        pass


@skill(
    name="apply_plating",
    category="modify/finish",
    level="atomic",
    summary="Catalog-driven metallic plating (electroless Ni, hard Cr, Zn-Cr, "
            "Au flash). Tags faces with the plating spec and optionally grows "
            "the body envelope by the catalog deposit thickness. "
            "double_sided=True applies growth via uniform skin offset.",
    selector_kinds=["faces"],
    history_rules={
        "plating_tagged_faces": HistoryRule.MODIFIED_INHERIT,
        "all_faces": HistoryRule.MODIFIED_INHERIT,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.plating_spec_in_catalog",
    ],
    produces_features=["plating_metadata"],
    preserves=["body_topology"],
    manufacturing={
        "cnc_3axis": {"extras": {"masking_required": True}},
    },
    failure_modes=[
        "fm.plating_spec_unknown",
        "fm.offset_self_intersection",
    ],
    cost_hint=0.12,
    post_conditions=[PostCondition(kind="body_present")],
)
class ApplyPlating(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        plating_spec: str = Field(
            description="Key in catalogs/finishes/plating.yaml — e.g. "
                        "'Nickel_Electroless_15um'.",
        )
        double_sided: bool = Field(
            default=True,
            description="True = full immersion (grow uniformly); False = "
                        "selective plating (tag only — no geometry growth).",
        )
        grow_geometry: bool = Field(
            default=False,
            description="If True AND double_sided, expand body by deposit "
                        "thickness via skin offset.",
        )
        offset_tolerance_mm: float = Field(default=0.01, gt=0, le=1.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        from phone_designer.skills._resolvers import (
            _face_area, _face_center, resolve_faces,
        )

        catalog = _load("finishes", "plating")
        finishes = catalog.get("finishes", {})
        if args.plating_spec not in finishes:
            raise ValueError(
                f"apply_plating: unknown plating_spec '{args.plating_spec}' — "
                f"catalog keys: {sorted(finishes.keys())}"
            )
        entry = finishes[args.plating_spec]

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"apply_plating: face_selector matched 0 faces — "
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
            "plating_spec": args.plating_spec,
            "process": entry.get("process"),
            "thickness_um": entry.get("thickness_um"),
            "hardness_hv": entry.get("hardness_hv"),
            "color": entry.get("color"),
            "corrosion_rating": entry.get("corrosion_rating"),
            "double_sided": args.double_sided,
            "regions": region_records,
        }
        tags = get_plating_tags(body)
        tags.append(record)
        set_plating_tags(body, tags)

        result_shape = shape
        grew = False
        if args.grow_geometry and args.double_sided:
            from OCP.BRepOffset import BRepOffset_Skin
            from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
            from OCP.GeomAbs import GeomAbs_Intersection

            growth_mm = float(entry.get("thickness_um", 0.0)) / 1000.0
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
                        f"apply_plating: skin offset failed ({growth_mm} mm): {exc}"
                    )
                if not maker.IsDone():
                    raise RuntimeError(
                        f"apply_plating: offset IsDone=False (growth={growth_mm} mm)"
                    )
                result_shape = maker.Shape()
                grew = True

        new_body = Part(result_shape) if grew else body
        if grew:
            set_plating_tags(new_body, tags)

        history = EntityHistoryMap(
            rules={
                "plating_tagged_faces": HistoryRule.MODIFIED_INHERIT,
                "all_faces": HistoryRule.MODIFIED_INHERIT,
            },
        )
        return SkillResult(
            body=new_body,
            history=history,
            extras={
                "plating_spec": args.plating_spec,
                "face_count": len(faces),
                "double_sided": args.double_sided,
                "grew_geometry": grew,
                "thickness_um": entry.get("thickness_um"),
            },
        )
