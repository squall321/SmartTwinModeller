"""light_pipe_routing_channel — atomic. Catalog-driven swept light-pipe tunnel.

Sweeps a catalog light-pipe cross-section (round Ø or rectangular slot) along
a 2D path on a planar ±Z face and subtracts the resulting tube from the body.
The cross-section comes from ``catalogs/optical/light_pipes.yaml``:

  - "round" pipes: cross_section_d_mm   → CircleSketch
  - "slot"  pipes: length_mm, width_mm  → SlotSketch (capsule)

The 2D path_points are face-local (x, y) coordinates relative to the target
face centroid. They are lifted to 3D world coordinates at the face's Z plane
(slightly embedded so the swept profile is fully inside the body) and the
sweep is delegated to ``swept_pocket_along_curve``.

v1 supports planar +Z / -Z target faces.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._resolvers import (
    _face_center,
    _face_normal_at_center,
    resolve_faces,
)
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def _load(family: str, name: str):
    import pathlib

    import yaml
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load((root / "catalogs" / family / f"{name}.yaml").read_text())


@skill(
    name="light_pipe_routing_channel",
    category="modify/pocket",
    level="atomic",
    summary="Catalog-driven light-pipe routing tunnel: sweeps a round or "
            "slot cross-section (catalogs/optical/light_pipes.yaml) along a "
            "2D face-local polyline. Internally delegates to "
            "swept_pocket_along_curve. v1 supports planar +Z/-Z host faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":   HistoryRule.MODIFIED_INHERIT,
        "swept_walls":   HistoryRule.GENERATED_NEW,
        "swept_floor":   HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.pipe_spec_in_catalog",
        "pc.path_has_two_or_more_points",
    ],
    produces_features=["light_pipe_routing_channel"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.4,
                              "extras": {"min_endmill_diameter_mm": "pipe_d_mm"}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.pipe_spec_unknown",
        "fm.path_outside_face",
        "fm.path_self_intersect",
    ],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class LightPipeRoutingChannel(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        path_points: list[tuple[float, float]] = Field(
            min_length=2,
            description="≥2 face-local (x, y) points relative to the target "
                        "face centroid (in mm).",
        )
        pipe_spec: str = Field(
            description="Catalog key from catalogs/optical/light_pipes.yaml "
                        "(e.g. 'Round_Ø1.2', 'Round_Ø1.5', 'Round_Ø2.0', 'Slot_2x6').",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.modify_pocket.swept_pocket_along_curve import (
            SweptPocketAlongCurve,
        )

        catalog = _load("optical", "light_pipes")
        pipes = catalog.get("pipes", {})
        if args.pipe_spec not in pipes:
            raise ValueError(
                f"light_pipe_routing_channel: unknown pipe_spec "
                f"'{args.pipe_spec}' — catalog keys: {sorted(pipes.keys())}"
            )
        entry = pipes[args.pipe_spec]
        kind = str(entry.get("cross_section", "round")).lower()

        # Build the cross-section sketch spec from the catalog entry.
        if kind == "round":
            d = float(entry["cross_section_d_mm"])
            profile = {"kind": "circle", "diameter_mm": d}
            embed_extent = d
        elif kind == "slot":
            length = float(entry["length_mm"])
            width = float(entry["width_mm"])
            # SlotSketch requires length >= width.
            slot_l = max(length, width)
            slot_w = min(length, width)
            profile = {
                "kind": "slot",
                "length_mm": slot_l,
                "width_mm": slot_w,
            }
            embed_extent = slot_w
        else:
            raise ValueError(
                f"light_pipe_routing_channel: unsupported cross_section "
                f"'{kind}' in pipe '{args.pipe_spec}'"
            )

        # Resolve the host face so we can convert face-local (x, y) → world (X, Y, Z).
        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"light_pipe_routing_channel: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "light_pipe_routing_channel v1: planar +Z/-Z target faces only"
            )

        # Embed half the cross-section size beneath the face so the swept
        # profile sits inside the body.
        nz_sign = 1.0 if normal[2] > 0 else -1.0
        face_z = center[2]
        embed_z = face_z - nz_sign * (embed_extent / 2.0)

        world_path = [
            (center[0] + px, center[1] + py, embed_z)
            for (px, py) in args.path_points
        ]

        # Delegate the actual sweep + cut to swept_pocket_along_curve.
        inner_result = SweptPocketAlongCurve().apply(body, {
            "face_selector": args.face_selector.model_dump(),
            "profile_sketch": profile,
            "path_points": world_path,
            "path_type": "polyline",
            "floor_blend_r_mm": 0.0,
        })

        history = EntityHistoryMap(
            rules={
                "target_face":  HistoryRule.MODIFIED_INHERIT,
                "swept_walls":  HistoryRule.GENERATED_NEW,
                "swept_floor":  HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(
            body=inner_result.body,
            history=history,
            extras={
                "pipe_spec": args.pipe_spec,
                "cross_section": kind,
            },
        )
