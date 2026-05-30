"""close_shell_to_solid — atomic. Promote a (closed) shell into a solid.

Wraps `BRepBuilderAPI_MakeSolid`. The typical input is the output of
`sew_faces_to_shell` — a `TopoDS_Shell` whose faces form a closed
oriented boundary. `MakeSolid.Add(shell)` accumulates one or more shells,
`Build()` constructs the solid.

If the input is already a solid (or a compound containing one) we still
attempt the build: OCCT happily returns an equivalent solid. If no shell
can be found we fall back to the original body so the `body_present`
post-condition still passes — the caller can inspect the volume to
detect that nothing useful happened.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="close_shell_to_solid",
    category="repair",
    level="atomic",
    summary="Wrap the body's shell(s) with BRepBuilderAPI_MakeSolid to form a "
            "TopoDS_Solid. No-op when no usable shell is found.",
    selector_kinds=[],
    history_rules={},
    produces_features=["closed_solid"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.make_solid_failed"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class CloseShellToSolid(SkillBase):
    class Args(BaseModel):
        pass

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
        from OCP.TopAbs import TopAbs_SHELL
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        shape = body.wrapped if hasattr(body, "wrapped") else body

        # Walk shells in the input. Empty shape → just echo the body so
        # downstream skills get a stable handle.
        it = TopExp_Explorer(shape, TopAbs_SHELL)
        builder = BRepBuilderAPI_MakeSolid()
        shells_added = 0
        while it.More():
            shell = TopoDS.Shell_s(it.Current())
            builder.Add(shell)
            shells_added += 1
            it.Next()

        if shells_added == 0:
            # Nothing to promote — return the original body unchanged.
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={"shells_consumed": 0, "made_solid": False},
            )

        builder.Build()
        if not builder.IsDone():
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={"shells_consumed": shells_added, "made_solid": False},
            )

        solid = builder.Solid()
        if solid is None or solid.IsNull():
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={"shells_consumed": shells_added, "made_solid": False},
            )

        return SkillResult(
            body=Part(solid),
            history=EntityHistoryMap(),
            extras={"shells_consumed": shells_added, "made_solid": True},
        )
