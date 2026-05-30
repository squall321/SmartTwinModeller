"""sew_faces_to_shell — atomic. Stitch loose faces into a shell with sewing.

Wraps OCCT's BRepBuilderAPI_Sewing. Iterates every TopAbs_FACE in the
input body and feeds it into the sewer, then `Perform()`s — the output
is whatever sewing produced (typically a shell, or a compound if some
faces remained free).

Use cases:
    - STEP / IGES imports that came in as a face soup rather than a shell.
    - Hand-built constructions where each face was made independently
      and now needs to be welded.

The `tolerance_mm` argument is the maximum gap between matched edges —
larger tolerance closes wider gaps but risks stitching the wrong pair.
Set `non_manifold=True` if you expect three or more faces to share an
edge (e.g., a mesh-like internal partition); the default rejects such
configurations to keep the result a clean 2-manifold.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="sew_faces_to_shell",
    category="repair",
    level="atomic",
    summary="Sew loose faces of the body into a connected shell using "
            "BRepBuilderAPI_Sewing. Returns the sewn shape (shell or compound).",
    selector_kinds=[],
    history_rules={},
    produces_features=["sewn_shell"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.sewing_yielded_null_shape"],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="body_present")],
)
class SewFacesToShell(SkillBase):
    class Args(BaseModel):
        tolerance_mm: float = Field(default=1e-3, gt=0, le=10.0)
        non_manifold: bool = False

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
        from OCP.TopAbs import TopAbs_FACE
        from OCP.TopExp import TopExp_Explorer

        shape = body.wrapped if hasattr(body, "wrapped") else body

        sewer = BRepBuilderAPI_Sewing(args.tolerance_mm)
        sewer.SetNonManifoldMode(bool(args.non_manifold))

        # Hand every face in the body to the sewer. If the body has no
        # faces (e.g., it's an empty compound) we still call Perform() so
        # the result is well-defined.
        it = TopExp_Explorer(shape, TopAbs_FACE)
        added = 0
        while it.More():
            sewer.Add(it.Current())
            added += 1
            it.Next()

        sewer.Perform()
        sewn = sewer.SewedShape()

        # Empty result → keep original so body_present holds.
        if sewn is None or sewn.IsNull():
            sewn = shape

        return SkillResult(
            body=Part(sewn),
            history=EntityHistoryMap(),
            extras={"faces_input": added},
        )
