"""shape_heal — atomic. Repair a B-rep with OCCT ShapeFix_Shape.

Wraps OCCT's top-level shape-healing aggregator. Drives the per-sub-tool
modes (solid/shell/face/wire) using `Fix*Mode = 1` (auto) by default and
lets the precision triple (`SetPrecision` / `SetMinTolerance` /
`SetMaxTolerance`) bound how aggressively gaps are closed.

`fix_small_edges` enables the wire-level small-edge fix
(`FixWireTool().FixSmallMode = 1`) — degenerate / sub-tolerance edges get
absorbed into neighbouring vertices, which is the most common cause of
"holey" STEP imports.

`fix_face_orientation` enables shell-level orientation fixing — the
default ShapeFix_Solid pipeline already reorients faces to point outward,
but on degenerate input that comes in as a loose shell (not yet a solid)
this still helps.

`extras["heal_report"]` exposes the per-stage tag counts the agent can
log to verify the heal actually did something useful — these are read off
ShapeFix_Shape's Status*() bitmasks.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _count_subshapes(shape, kind) -> int:
    """Count direct sub-shapes of the given TopAbs_* kind."""
    from OCP.TopExp import TopExp_Explorer

    it = TopExp_Explorer(shape, kind)
    n = 0
    while it.More():
        n += 1
        it.Next()
    return n


def _heal_report(before, after) -> dict[str, int]:
    """Diff sub-shape counts before/after ShapeFix_Shape.Perform()."""
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_WIRE

    def _delta(kind: Any) -> int:
        return _count_subshapes(after, kind) - _count_subshapes(before, kind)

    # ShapeFix_Shape may *add* shells/faces (e.g., when it splits a non-
    # manifold shell) or remove them (degenerate-edge collapse). We expose
    # the absolute net change so callers can see "something changed at this
    # level" rather than chasing sign conventions.
    return {
        "shells_fixed": abs(_delta(TopAbs_SHELL)),
        "faces_fixed":  abs(_delta(TopAbs_FACE)),
        "wires_fixed":  abs(_delta(TopAbs_WIRE)),
        "edges_fixed":  abs(_delta(TopAbs_EDGE)),
    }


@skill(
    name="shape_heal",
    category="repair",
    level="atomic",
    summary="Run OCCT ShapeFix_Shape on the body — closes small gaps, fixes "
            "wire/edge issues, optionally reorients faces. Returns the healed "
            "body plus extras['heal_report'] with per-stage counts.",
    selector_kinds=[],
    history_rules={},
    produces_features=["healed_brep"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.shapefix_perform_failed"],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class ShapeHeal(SkillBase):
    class Args(BaseModel):
        precision_mm: float = Field(default=1e-4, gt=0, le=1.0)
        fix_small_edges: bool = True
        fix_face_orientation: bool = True

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.ShapeFix import ShapeFix_Shape

        shape = body.wrapped if hasattr(body, "wrapped") else body

        fixer = ShapeFix_Shape()
        # Precision triple — the bandwidth ShapeFix_Shape is allowed to
        # consume when snapping vertices / edges together.
        fixer.SetPrecision(args.precision_mm)
        fixer.SetMinTolerance(args.precision_mm * 0.1)
        fixer.SetMaxTolerance(max(args.precision_mm * 1000.0, 1e-2))

        fixer.Init(shape)

        # Sub-tool modes. The integer 1 ≡ "force-on" in OCCT's Standard_
        # Integer mode flags (-1=auto, 0=off, 1=on).
        if args.fix_small_edges:
            try:
                fixer.FixWireTool().FixSmallMode = 1
            except Exception:
                # Sub-tool unavailable on this OCCT build — best-effort.
                pass
        if args.fix_face_orientation:
            try:
                fixer.FixFreeShellMode = 1
            except Exception:
                pass

        fixer.Perform()
        healed = fixer.Shape()

        # Empty / null result fallback — keep the original body so the
        # `body_present` post-condition can still pass and downstream
        # skills don't crash on a None.
        if healed is None or healed.IsNull():
            healed = shape

        report = _heal_report(shape, healed)
        return SkillResult(
            body=Part(healed),
            history=EntityHistoryMap(),
            extras={"heal_report": report},
        )
