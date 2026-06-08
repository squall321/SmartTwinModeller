"""base_step_kind_chooser — pick the right plan_from_feature_catalog
``base_step_kind`` for an arbitrary body.

COMPLEX-CAD pass-6 (2026-06-09): until now the in-proc runner hardcoded
``base_step_kind="preserve_brep"`` because that was the right answer for
the industrial parts we audited. For ARBITRARY future shapes the right
choice depends on:
    - face_count (small clean parts ⇒ "box" placeholder works)
    - solidity ratio (|Mass| / bbox_volume; thin shells need preserve_brep)
    - assembly multiplicity (≥3 closed shells ⇒ caller should route to
      ``assembly_reverse_engineer`` instead).

This skill makes the heuristic explicit and overridable. The returned
hint is meant to be consumed by the runner / caller; it does NOT mutate
the body and does NOT invoke plan_from_feature_catalog directly.

extras schema::

    extras["base_step_kind_hint"] = {
        "face_count":      int,
        "solidity_ratio":  float,
        "closed_shells":   int,
        "chosen":          "box" | "preserve_brep" | "assembly",
        "rationale":       str,
    }
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return getattr(body, "wrapped", body)


def _count_faces(shape) -> int:
    try:
        from OCP.TopAbs import TopAbs_FACE
        from OCP.TopExp import TopExp_Explorer
        n = 0
        it = TopExp_Explorer(shape, TopAbs_FACE)
        while it.More():
            n += 1
            it.Next()
        return n
    except Exception:
        return 0


def _count_closed_shells(shape) -> int:
    try:
        from OCP.BRepCheck import BRepCheck_Shell, BRepCheck_Status
        from OCP.TopAbs import TopAbs_SHELL
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS
        n = 0
        it = TopExp_Explorer(shape, TopAbs_SHELL)
        while it.More():
            shell = TopoDS.Shell_s(it.Current())
            checker = BRepCheck_Shell(shell)
            if checker.Closed() == BRepCheck_Status.BRepCheck_NoError:
                n += 1
            it.Next()
        return n
    except Exception:
        return 0


def _bbox_and_signed_volume(shape):
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        bb = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, bb)
        bbox_v = 0.0
        if not bb.IsVoid():
            xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
            bbox_v = float(xmax - xmin) * float(ymax - ymin) * float(zmax - zmin)
        p = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, p)
        return bbox_v, abs(float(p.Mass()))
    except Exception:
        return 0.0, 0.0


@skill(
    name="base_step_kind_chooser",
    category="inspect",
    level="atomic",
    summary="Pick plan_from_feature_catalog.base_step_kind for the given body "
            "based on face_count, solidity ratio, and closed-shell count. "
            "Returns a hint extras['base_step_kind_hint'] — the caller wires "
            "it into PlanFromFeatureCatalog or routes to assembly mode.",
    selector_kinds=[],
    history_rules={},
    produces_features=["base_step_kind_hint"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class BaseStepKindChooser(SkillBase):
    class Args(BaseModel):
        face_count_box_max: int = Field(
            default=50, ge=0,
            description="Bodies with ≤ this many faces qualify for 'box' if "
                        "they are also solid enough (see solidity_box_min).",
        )
        solidity_box_min: float = Field(
            default=0.80, ge=0.0, le=1.0,
            description="Bodies whose |Mass|/bbox_volume is ≥ this AND whose "
                        "face_count ≤ face_count_box_max get base_step_kind='box'. "
                        "Anything thinner (sheets / shells with cavities) gets "
                        "'preserve_brep'.",
        )
        assembly_shell_min: int = Field(
            default=3, ge=1,
            description="Bodies with ≥ this many CLOSED shells are flagged "
                        "'assembly' — the caller should route to "
                        "assembly_reverse_engineer instead of running RE on the "
                        "full compound.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        shape = _occt_shape(body)
        if shape is None:
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={"base_step_kind_hint": {
                    "face_count": 0, "solidity_ratio": 0.0, "closed_shells": 0,
                    "chosen": "preserve_brep",
                    "rationale": "no body — default preserve_brep is harmless",
                }},
            )

        face_count = _count_faces(shape)
        closed_shells = _count_closed_shells(shape)
        bbox_v, mass = _bbox_and_signed_volume(shape)
        solidity = (mass / bbox_v) if bbox_v > 0 else 0.0

        chosen: str
        rationale: str
        if closed_shells >= args.assembly_shell_min:
            chosen = "assembly"
            rationale = (
                f"{closed_shells} closed shells ≥ {args.assembly_shell_min} — "
                "route to assembly_reverse_engineer."
            )
        elif face_count <= args.face_count_box_max and solidity >= args.solidity_box_min:
            chosen = "box"
            rationale = (
                f"face_count {face_count} ≤ {args.face_count_box_max} AND "
                f"solidity {solidity:.2f} ≥ {args.solidity_box_min} — "
                "placeholder slab fits this body."
            )
        else:
            chosen = "preserve_brep"
            rationale = (
                f"face_count {face_count} > {args.face_count_box_max} OR "
                f"solidity {solidity:.2f} < {args.solidity_box_min} — "
                "start from the actual BREP so cuts no-op against real topology."
            )

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"base_step_kind_hint": {
                "face_count": face_count,
                "solidity_ratio": round(solidity, 6),
                "closed_shells": closed_shells,
                "chosen": chosen,
                "rationale": rationale,
            }},
        )
