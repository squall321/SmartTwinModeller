"""delete_face_defeature — atomic. Remove selected faces and heal the solid.

The #1 direct-edit / RE primitive (SolidWorks Delete Face→Heal, NX/Fusion Delete
Face, Creo Remove): delete a fillet / boss / hole / logo face group and let the
kernel EXTEND the neighbouring faces to re-close a watertight solid. Essential for
simplifying imported/reverse-engineered geometry before FEA meshing or before
re-featuring.

Uses OCCT 7.8 ``BRepAlgoAPI_Defeaturing`` — AddFacesToRemove(faces) + Build() +
IsDone. Distinct from the existing repair ops, which only clean SUB-TOLERANCE
slivers: ``remove_micro_features`` explicitly disclaims topological defeaturing;
``simplify_to_canonical`` only merges co-domain faces (ShapeUpgrade_UnifySameDomain);
``shape_heal`` fixes bad geometry, it does not delete features.

Faces are chosen with the standard selector (e.g. faces_by_area to drop small blend
faces, faces_by_normal, tagged, largest_n). A HONEST guard: the result volume must
stay within a sane band of the input — defeaturing REMOVES a feature so a small
volume change is expected, but a wildly different volume means the heal collapsed
the body, which is a structured refusal rather than a silent garbage solid.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult

_EPS_VOL = 1e-6


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return abs(float(g.Mass()))


@skill(
    name="delete_face_defeature",
    category="repair",
    level="atomic",
    summary="Delete the faces matched by a selector and HEAL the solid "
            "(BRepAlgoAPI_Defeaturing) — extend the neighbours to re-close "
            "watertight. The #1 direct-edit/RE primitive: strip a fillet/boss/hole/"
            "logo off imported geometry. Unlike remove_micro_features (sliver "
            "cleanup only) this does true topological defeaturing. Refuses a heal "
            "that collapses the body (fm.defeature_volume_collapse).",
    selector_kinds=["faces"],
    history_rules={"body_faces": HistoryRule.MODIFIED_INHERIT,
                   "removed_faces": HistoryRule.CONSUMED},
    produces_features=[],
    preserves=["outer_envelope_outer"],
    manufacturing={},
    failure_modes=["fm.no_faces_selected", "fm.defeature_failed",
                   "fm.defeature_volume_collapse"],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class DeleteFaceDefeature(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef = Field(
            description="Selects the faces to delete (e.g. faces_by_area with a max "
                        "to drop small blend/fillet faces, faces_by_normal, tagged, "
                        "largest_n). Every matched face is removed and the wound "
                        "healed by extending its neighbours.")
        max_volume_change_frac: float = Field(
            default=0.5, gt=0.0, le=1.0,
            description="Reject the heal if |Δvolume|/input_volume exceeds this "
                        "(a collapsed heal). Default 0.5 (a defeature that changes "
                        "volume by >50% almost certainly went wrong).")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
        from OCP.TopTools import TopTools_ListOfShape

        from phone_designer.skills._resolvers import resolve_faces

        if body is None:
            raise ValueError("fm.no_faces_selected: delete_face_defeature needs a body.")
        shape = body.wrapped if hasattr(body, "wrapped") else body
        vol_before = _volume(shape)

        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise ValueError(
                "fm.no_faces_selected: the selector matched no faces to delete.")

        try:
            df = BRepAlgoAPI_Defeaturing()
            df.SetShape(shape)
            to_remove = TopTools_ListOfShape()
            for f in faces:
                to_remove.Append(f)
            df.AddFacesToRemove(to_remove)
            df.Build()
            if not df.IsDone():
                raise RuntimeError("Defeaturing IsDone=False (heal did not succeed)")
            result = df.Shape()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"fm.defeature_failed: {type(exc).__name__}: {exc} — the selected "
                "faces may not form a removable feature (try a tighter selection).")

        vol_after = _volume(result)
        if result is None or result.IsNull() or vol_after <= _EPS_VOL:
            raise ValueError(
                f"fm.defeature_volume_collapse: healed volume {vol_after:.6g}mm³ ≈ 0.")
        if vol_before > _EPS_VOL:
            frac = abs(vol_after - vol_before) / vol_before
            if frac > args.max_volume_change_frac:
                raise ValueError(
                    f"fm.defeature_volume_collapse: volume changed by {frac:.1%} "
                    f"({vol_before:.1f}→{vol_after:.1f}mm³) > "
                    f"{args.max_volume_change_frac:.0%} — the heal likely collapsed "
                    "the body. Select a smaller / cleaner face group.")

        return SkillResult(
            body=Part(result),
            history=EntityHistoryMap(rules={
                "body_faces": HistoryRule.MODIFIED_INHERIT,
                "removed_faces": HistoryRule.CONSUMED}),
            extras={"defeature": {
                "n_faces_removed": len(faces),
                "volume_before_mm3": round(vol_before, 3),
                "volume_after_mm3": round(vol_after, 3),
                "volume_delta_mm3": round(vol_after - vol_before, 3),
            }},
        )
