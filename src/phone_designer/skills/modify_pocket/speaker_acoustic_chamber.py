"""speaker_acoustic_chamber — atomic. Microspeaker back-volume chamber.

Cylindrical acoustic chamber for a microspeaker driver. The chamber is a
stepped (shouldered) pocket:
  - Top step: a wide, shallow shelf at `frame_d_mm` × `frame_shelf_h_mm` that
    receives the driver frame.
  - Main chamber: a deeper cylindrical cavity at `chamber_d_min_mm` that holds
    the back volume.

Driver geometry comes from catalogs/microspeakers/<family>.yaml keyed by
`speaker_spec`. v1 supports planar +Z/-Z target faces.
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
    name="speaker_acoustic_chamber",
    category="modify/pocket",
    level="atomic",
    summary="Stepped cylindrical pocket forming a microspeaker back-volume "
            "chamber. The top step is a shallow shelf for the driver frame; "
            "below it sits the main acoustic chamber. Catalog-driven "
            "(catalogs/microspeakers/<family>.yaml). v1 supports planar "
            "+Z/-Z faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":         HistoryRule.MODIFIED_INHERIT,
        "chamber_walls":       HistoryRule.GENERATED_NEW,
        "chamber_bottom":      HistoryRule.GENERATED_NEW,
        "frame_shelf":         HistoryRule.GENERATED_NEW,
        "consumed_volume":     HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.speaker_spec_in_catalog",
    ],
    produces_features=["acoustic_chamber", "driver_frame_shelf"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.6,
                              "extras": {"max_aspect_ratio": 3.0}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.speaker_spec_unknown",
        "fm.chamber_exits_body",
    ],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class SpeakerAcousticChamber(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of chamber center",
        )
        speaker_spec: str = Field(
            description="Catalog key (e.g. '11x15_Square')",
        )
        catalog_family: str = Field(
            default="standard",
            description="Catalog subfolder under catalogs/microspeakers/",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        catalog = _load("microspeakers", args.catalog_family)
        speakers = catalog.get("speakers", {})
        if args.speaker_spec not in speakers:
            raise ValueError(
                f"speaker_acoustic_chamber: unknown speaker_spec "
                f"'{args.speaker_spec}' in family '{args.catalog_family}' — "
                f"catalog keys: {sorted(speakers.keys())}"
            )
        entry = speakers[args.speaker_spec]
        chamber_d = float(entry["chamber_d_min_mm"])
        chamber_h = float(entry["chamber_h_mm"])
        frame_d = float(entry["frame_d_mm"])
        frame_shelf_h = float(entry["frame_shelf_h_mm"])

        if frame_d <= chamber_d:
            raise ValueError(
                f"speaker_acoustic_chamber: catalog frame_d ({frame_d}) "
                f"must be > chamber_d ({chamber_d}) for shelf to exist"
            )
        if frame_shelf_h >= chamber_h:
            raise ValueError(
                f"speaker_acoustic_chamber: frame_shelf_h ({frame_shelf_h}) "
                f">= chamber_h ({chamber_h}) — chamber would be all shelf"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"speaker_acoustic_chamber: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "speaker_acoustic_chamber v1: planar +Z/-Z target faces only"
            )

        nz_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        axis_dir = gp_Dir(0.0, 0.0, -nz_sign)

        # Stage 1: frame shelf (wide, shallow) opens at the face.
        shelf_base = gp_Pnt(cx, cy, center[2])
        shelf_ax = gp_Ax2(shelf_base, axis_dir)
        shelf_mk = BRepPrimAPI_MakeCylinder(shelf_ax, frame_d / 2.0, frame_shelf_h)
        shelf_mk.Build()
        if not shelf_mk.IsDone():
            raise RuntimeError(
                "speaker_acoustic_chamber: frame shelf cylinder build failed"
            )
        cut_a = BRepAlgoAPI_Cut(shape, shelf_mk.Shape())
        cut_a.Build()
        if not cut_a.IsDone():
            raise RuntimeError(
                "speaker_acoustic_chamber: shelf boolean cut failed"
            )
        new_shape = cut_a.Shape()

        # Stage 2: main chamber — narrower, deeper. Starts at the face level
        # (overlaps shelf region, that's fine for a Cut) and runs to chamber_h.
        chamber_base = gp_Pnt(cx, cy, center[2])
        chamber_ax = gp_Ax2(chamber_base, axis_dir)
        chamber_mk = BRepPrimAPI_MakeCylinder(
            chamber_ax, chamber_d / 2.0, chamber_h,
        )
        chamber_mk.Build()
        if not chamber_mk.IsDone():
            raise RuntimeError(
                "speaker_acoustic_chamber: main chamber cylinder build failed"
            )
        cut_b = BRepAlgoAPI_Cut(new_shape, chamber_mk.Shape())
        cut_b.Build()
        if not cut_b.IsDone():
            raise RuntimeError(
                "speaker_acoustic_chamber: chamber boolean cut failed"
            )
        new_shape = cut_b.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "chamber_walls":   HistoryRule.GENERATED_NEW,
                "chamber_bottom":  HistoryRule.GENERATED_NEW,
                "frame_shelf":     HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
