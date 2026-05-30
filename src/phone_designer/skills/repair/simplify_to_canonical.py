"""simplify_to_canonical — atomic. Merge co-planar/co-curve faces & edges.

Wraps `ShapeUpgrade_UnifySameDomain` — OCCT's algorithm for collapsing
adjacent faces that share the same underlying geometric domain (e.g.,
two planar faces meeting along a seam that was an artefact of a boolean
operation) into a single canonical face, and similarly for edges that
share an underlying curve.

Use after a chain of boolean cuts/unions to recover a clean B-rep
without dangling seams. Also useful before measurement skills that
benefit from a minimal face/edge count.

Args:
    tolerance_mm: linear tolerance for "same domain" classification.
        Passed verbatim to SetLinearTolerance(). Angular tolerance is
        kept at a small fixed default (1e-4 rad ≈ 0.006°).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _count_faces(shape) -> int:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    it = TopExp_Explorer(shape, TopAbs_FACE)
    n = 0
    while it.More():
        n += 1
        it.Next()
    return n


def _count_edges(shape) -> int:
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer

    it = TopExp_Explorer(shape, TopAbs_EDGE)
    n = 0
    while it.More():
        n += 1
        it.Next()
    return n


@skill(
    name="simplify_to_canonical",
    category="repair",
    level="atomic",
    summary="Merge co-domain adjacent faces/edges using ShapeUpgrade_UnifySameDomain "
            "(UnifyFaces=True, UnifyEdges=True, ConcatBSplines=True).",
    selector_kinds=[],
    history_rules={},
    produces_features=["canonical_brep"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.unify_same_domain_failed"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class SimplifyToCanonical(SkillBase):
    class Args(BaseModel):
        tolerance_mm: float = Field(default=1e-3, gt=0, le=1.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

        shape = body.wrapped if hasattr(body, "wrapped") else body

        faces_before = _count_faces(shape)
        edges_before = _count_edges(shape)

        # UnifyFaces=True, UnifyEdges=True, ConcatBSplines=True.
        unifier = ShapeUpgrade_UnifySameDomain(shape, True, True, True)
        unifier.SetLinearTolerance(args.tolerance_mm)
        unifier.SetAngularTolerance(1e-4)
        # Don't mutate the input shape in place — safer for plan replay.
        try:
            unifier.SetSafeInputMode(True)
        except Exception:
            # Older OCCT — option absent; fall through.
            pass

        try:
            unifier.Build()
            simplified = unifier.Shape()
        except Exception:
            simplified = shape

        if simplified is None or simplified.IsNull():
            simplified = shape

        report = {
            "faces_before": faces_before,
            "faces_after":  _count_faces(simplified),
            "edges_before": edges_before,
            "edges_after":  _count_edges(simplified),
        }
        return SkillResult(
            body=Part(simplified),
            history=EntityHistoryMap(),
            extras={"simplify_report": report},
        )
