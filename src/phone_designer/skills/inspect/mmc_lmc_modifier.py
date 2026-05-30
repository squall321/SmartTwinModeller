"""mmc_lmc_modifier — atomic, read-only.

ISO 2692 / ASME Y14.5 material-condition modifier — tag a feature
(typically a cylindrical hole or boss face) with its applicable material
condition:

    MMC  Maximum Material Condition (Ⓜ) — bonus tolerance available when
                                           the feature is at its largest
                                           material size (smallest hole,
                                           largest shaft).
    LMC  Least Material Condition (Ⓛ)  — bonus available at smallest
                                           material (largest hole, smallest
                                           shaft).
    RFS  Regardless of Feature Size (Ⓢ) — no bonus; the stated tolerance
                                           always applies.

The skill records the modifier and the as-measured feature size so that
downstream tolerance verifiers can compute the bonus tolerance budget:

    bonus_MMC = abs(measured_size - MMC_size)   (hole: MMC = smallest dia)
    bonus_LMC = abs(measured_size - LMC_size)   (hole: LMC = largest dia)

Storage:
    body._pd_mc_modifiers = [
        {"face_idx":       int,
         "modifier":       "MMC" | "LMC" | "RFS",
         "feature_kind":   "cylinder" | "sphere" | "plane" | "other",
         "measured_size_mm": float,      # diameter for cyl/sphere,
                                          # 0.0 for planar feature
         "is_internal":    bool          # hole vs boss heuristic
        },
        ...
    ]

extras["material_condition"] mirrors the appended entry + the list index.

body unchanged (post ``body_present``).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorBase, selector_from_dict
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _coerce_selector(s: Any) -> SelectorBase:
    if isinstance(s, SelectorBase):
        return s
    if isinstance(s, dict):
        return selector_from_dict(s)
    raise TypeError(f"unsupported selector type: {type(s).__name__}")


def _feature_size(face) -> tuple[str, float, bool]:
    """Return (kind, measured_size_mm, is_internal).

    For cylindrical faces, ``is_internal`` is heuristic: TopAbs_REVERSED
    orientation marks an inward-facing (hole) surface in OCCT cuts.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Sphere
    from OCP.TopAbs import TopAbs_REVERSED

    surf = BRepAdaptor_Surface(face)
    gt = surf.GetType()
    if gt == GeomAbs_Cylinder:
        d = 2.0 * float(surf.Cylinder().Radius())
        internal = bool(face.Orientation() == TopAbs_REVERSED)
        return "cylinder", d, internal
    if gt == GeomAbs_Sphere:
        d = 2.0 * float(surf.Sphere().Radius())
        internal = bool(face.Orientation() == TopAbs_REVERSED)
        return "sphere", d, internal
    if gt == GeomAbs_Plane:
        return "plane", 0.0, False
    return "other", 0.0, False


@skill(
    name="mmc_lmc_modifier",
    category="inspect",
    level="atomic",
    summary="Tag a feature face with a material-condition modifier "
            "(MMC Ⓜ / LMC Ⓛ / RFS Ⓢ) per ISO 2692, recording the measured "
            "feature size for bonus-tolerance computation. Read-only.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["material_condition_modifier"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.no_match"],
    cost_hint=0.03,
    post_conditions=[PostCondition(kind="body_present")],
)
class MmcLmcModifier(SkillBase):
    class Args(BaseModel):
        feature_selector: dict
        modifier: Literal["MMC", "LMC", "RFS"]

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces, resolve_faces

        shape = _occt_shape(body)
        sel = _coerce_selector(args.feature_selector)
        matched = resolve_faces(shape, sel, body=body)
        if not matched:
            raise ValueError(
                f"mmc_lmc_modifier: feature_selector matched 0 faces "
                f"(kind={sel.kind})"
            )
        face = matched[0]

        kind, size, internal = _feature_size(face)

        all_faces = _all_faces(shape)
        face_idx = -1
        for i, af in enumerate(all_faces):
            try:
                if af.IsSame(face):
                    face_idx = i
                    break
            except Exception:
                pass

        entry = {
            "face_idx": face_idx,
            "modifier": args.modifier,
            "feature_kind": kind,
            "measured_size_mm": round(size, 6),
            "is_internal": bool(internal),
        }

        try:
            existing = list(getattr(body, "_pd_mc_modifiers", []) or [])
        except Exception:
            existing = []
        existing.append(entry)
        try:
            body._pd_mc_modifiers = existing
        except Exception:
            pass

        extras = {
            "material_condition": entry,
            "index": len(existing) - 1,
            "total": len(existing),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
