"""emi_gasket_groove — atomic.

Closed-loop EMI/EMC gasket groove sized from the gasket catalog. Reads the
gasket cross-section (``cs_mm``) and recommended groove width/depth from
catalogs/finishes/emi_gaskets.yaml, then sweeps a closed groove following
``path_points`` on a planar host face (delegates to
``modify_pocket._closed_path_sweep.build_closed_groove``).

Convention (matches the other axial / face-seal skills in this package):
    - Host face must be planar with normal along ±Z in world coordinates.
    - ``path_points`` is the groove CENTERLINE polyline in the face's local
      XY frame (origin = face centroid). The loop is implicitly closed.

Sizing comes from the catalog. To override, the caller can pass
``width_override_mm`` / ``depth_override_mm`` (e.g. for tighter compression
or a non-standard cs in the same family).
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def _load(family: str, name: str):
    import pathlib
    import yaml
    # parents: [0]=modify_pocket, [1]=skills, [2]=phone_designer, [3]=src,
    # [4]=project root (where catalogs/ lives).
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load(
        (root / "catalogs" / family / f"{name}.yaml").read_text()
    )


@skill(
    name="emi_gasket_groove",
    category="modify/pocket",
    level="atomic",
    summary="Closed-loop EMI/EMC gasket groove sized from the gasket catalog "
            "(Chomerics CHO-FORM, Parker E-Foam). Follows an arbitrary 2D "
            "centerline path on a planar ±Z host face.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "groove_walls":    HistoryRule.GENERATED_NEW,
        "groove_floor":    HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.path_has_three_or_more_points",
        "pc.gasket_spec_in_catalog",
    ],
    produces_features=["emi_gasket_groove", "closed_path_groove"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.3, "min_fillet_r_mm": 0.2},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_recommended": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.gasket_spec_unknown",
        "fm.path_self_intersect",
        "fm.groove_outside_face",
    ],
    cost_hint=0.30,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class EmiGasketGroove(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        path_points: list[tuple[float, float]] = Field(min_length=3)
        gasket_spec: str = Field(
            description="Key in catalogs/finishes/emi_gaskets.yaml — e.g. "
                        "'Chomerics_CHO-FORM_5550_1.0mm'.",
        )
        width_override_mm: Optional[float] = Field(
            default=None, gt=0, le=20.0,
            description="Override catalog recommended_groove_w_mm.",
        )
        depth_override_mm: Optional[float] = Field(
            default=None, gt=0, le=20.0,
            description="Override catalog recommended_groove_d_mm.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills.modify_pocket._closed_path_sweep import (
            build_closed_groove,
        )

        catalog = _load("finishes", "emi_gaskets")
        gaskets = catalog.get("gaskets", {})
        if args.gasket_spec not in gaskets:
            raise ValueError(
                f"emi_gasket_groove: unknown gasket_spec '{args.gasket_spec}' "
                f"— catalog keys: {sorted(gaskets.keys())}"
            )
        entry = gaskets[args.gasket_spec]

        width = float(
            args.width_override_mm
            if args.width_override_mm is not None
            else entry["recommended_groove_w_mm"]
        )
        depth = float(
            args.depth_override_mm
            if args.depth_override_mm is not None
            else entry["recommended_groove_d_mm"]
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"emi_gasket_groove: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]

        cutter = build_closed_groove(
            face=target_face,
            path_points=list(args.path_points),
            width_mm=width,
            depth_mm=depth,
        )

        cut = BRepAlgoAPI_Cut(shape, cutter)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("emi_gasket_groove: body cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "groove_walls":    HistoryRule.GENERATED_NEW,
                "groove_floor":    HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(
            body=Part(new_shape),
            history=history,
            extras={
                "gasket_spec": args.gasket_spec,
                "cs_mm": entry.get("cs_mm"),
                "groove_w_mm": width,
                "groove_d_mm": depth,
                "conductivity_db": entry.get("conductivity_db"),
            },
        )
