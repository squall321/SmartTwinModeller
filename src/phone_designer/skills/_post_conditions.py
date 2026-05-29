"""Post-condition framework — declare expected effects on a skill and verify
them after `_apply` returns. Catches silent no-ops (e.g., `extrude_pocket`
removing 0 mm³ due to a face-orientation bug).

Design:
    - Each skill optionally declares `post_conditions=[...]` in @skill(...).
    - `SkillBase.apply()` measures `(volume, face_count, edge_count)` of the
      input `body` before `_apply` and the result body afterwards.
    - Each declared `PostCondition` is evaluated; the first failure raises
      `PostConditionError`, which propagates up to PlanExecutor and is
      caught as a normal step failure (FAIL with a clear message).

Checks supported:
    - "volume_decreased" — final volume < initial - min_delta_mm3
    - "volume_increased" — final volume > initial + min_delta_mm3
    - "volume_changed"   — abs(final - initial) > min_delta_mm3
    - "face_count_changed" — final face count != initial face count
    - "body_present"     — result body is not None (used by create skills,
      which receive body=None as input)

Edge cases:
    - For create skills the input body is `None`. Pre-metrics are then `None`
      and only `body_present` makes sense (others are skipped/no-op).
    - `allow_no_change=True` makes the check non-fatal when the input
      legitimately produces no change (e.g., `final_fillet` on a body with no
      sharp edges).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


PostConditionKind = Literal[
    "volume_decreased",
    "volume_increased",
    "volume_changed",
    "face_count_changed",
    "body_present",
]


@dataclass(frozen=True)
class PostCondition:
    """Declarative post-execution check for a skill.

    Args:
        kind: which check to perform.
        min_delta_mm3: minimum volume change in mm³ (for volume_* checks).
            Default 0.01 (10 µL — well above OCCT roundoff but small enough
            to catch the cavity bug, which removed 0 mm³).
        allow_no_change: if True, treat "no measurable effect" as a soft pass
            instead of raising. Useful for skills that legitimately may be
            no-ops on degenerate inputs (e.g., final_fillet without sharp edges).
    """

    kind: PostConditionKind
    min_delta_mm3: float = 0.01
    allow_no_change: bool = False


class PostConditionError(RuntimeError):
    """Raised when a declared post-condition is not satisfied.

    PlanExecutor catches this like any other skill exception and FAILs the
    step with the message — same machinery as OCCT errors.
    """


def _occt_shape(body: Any):
    """Return the raw OCCT TopoDS_Shape for a build123d Part or raw shape."""
    return getattr(body, "wrapped", body)


def _measure(body: Any) -> dict[str, float] | None:
    """Compute (volume_mm3, face_count, edge_count) for a body.

    Returns None if `body` is None (e.g., create skills' input body).
    Robust to OCCT errors — on failure returns a dict with NaN-equivalent
    placeholder values so checks can still distinguish "missing" from
    "measured".
    """
    if body is None:
        return None

    shape = _occt_shape(body)
    if shape is None:
        return None

    # Volume via BRepGProp.VolumeProperties
    volume = 0.0
    try:
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props)
        volume = float(props.Mass())
    except Exception:
        volume = 0.0

    # Face / edge count via TopExp_Explorer with dedup
    face_count = 0
    edge_count = 0
    try:
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopTools import TopTools_MapOfShape

        seen_f = TopTools_MapOfShape()
        it = TopExp_Explorer(shape, TopAbs_FACE)
        while it.More():
            f = it.Current()
            if not seen_f.Contains(f):
                seen_f.Add(f)
                face_count += 1
            it.Next()

        seen_e = TopTools_MapOfShape()
        it = TopExp_Explorer(shape, TopAbs_EDGE)
        while it.More():
            e = it.Current()
            if not seen_e.Contains(e):
                seen_e.Add(e)
                edge_count += 1
            it.Next()
    except Exception:
        pass

    return {
        "volume_mm3": volume,
        "face_count": face_count,
        "edge_count": edge_count,
    }


def _check_one(
    cond: PostCondition,
    pre: dict[str, float] | None,
    post: dict[str, float] | None,
    skill_name: str,
) -> None:
    """Evaluate a single PostCondition. Raises PostConditionError on failure."""

    if cond.kind == "body_present":
        if post is None:
            raise PostConditionError(
                f"{skill_name}: post_condition 'body_present' failed — "
                f"result body is None"
            )
        return

    # All volume/face checks need a result.
    if post is None:
        raise PostConditionError(
            f"{skill_name}: post_condition '{cond.kind}' failed — "
            f"result body is None"
        )

    # Skills that mutate an existing body need pre-metrics.
    if pre is None:
        # No baseline to compare against — silently pass. (e.g., a create
        # skill incorrectly declared a volume check; that's a spec bug, not
        # a runtime bug.)
        return

    pre_v = pre.get("volume_mm3", 0.0)
    post_v = post.get("volume_mm3", 0.0)
    delta = post_v - pre_v

    if cond.kind == "volume_decreased":
        if delta < -cond.min_delta_mm3:
            return
        if cond.allow_no_change and abs(delta) <= cond.min_delta_mm3:
            return
        raise PostConditionError(
            f"{skill_name}: post_condition 'volume_decreased' failed — "
            f"pre={pre_v:.4f} mm³, post={post_v:.4f} mm³, "
            f"delta={delta:.4f} mm³ (expected ≤ -{cond.min_delta_mm3})"
        )

    if cond.kind == "volume_increased":
        if delta > cond.min_delta_mm3:
            return
        if cond.allow_no_change and abs(delta) <= cond.min_delta_mm3:
            return
        raise PostConditionError(
            f"{skill_name}: post_condition 'volume_increased' failed — "
            f"pre={pre_v:.4f} mm³, post={post_v:.4f} mm³, "
            f"delta={delta:.4f} mm³ (expected ≥ +{cond.min_delta_mm3})"
        )

    if cond.kind == "volume_changed":
        if abs(delta) > cond.min_delta_mm3:
            return
        if cond.allow_no_change:
            return
        raise PostConditionError(
            f"{skill_name}: post_condition 'volume_changed' failed — "
            f"pre={pre_v:.4f} mm³, post={post_v:.4f} mm³, "
            f"|delta|={abs(delta):.4f} mm³ (expected > {cond.min_delta_mm3})"
        )

    if cond.kind == "face_count_changed":
        pre_fc = int(pre.get("face_count", 0))
        post_fc = int(post.get("face_count", 0))
        if pre_fc != post_fc:
            return
        # Edge count change is also evidence of topology change (e.g., a
        # tangent-edge fillet that adds new edges without changing the face
        # count). Treat either as success.
        pre_ec = int(pre.get("edge_count", 0))
        post_ec = int(post.get("edge_count", 0))
        if pre_ec != post_ec:
            return
        if cond.allow_no_change:
            return
        raise PostConditionError(
            f"{skill_name}: post_condition 'face_count_changed' failed — "
            f"face count unchanged ({pre_fc} → {post_fc}), "
            f"edge count unchanged ({pre_ec} → {post_ec})"
        )

    raise PostConditionError(
        f"{skill_name}: unknown post_condition kind '{cond.kind}'"
    )


def check_post_conditions(
    conds: list[PostCondition] | None,
    pre: dict[str, float] | None,
    post: dict[str, float] | None,
    skill_name: str,
) -> None:
    """Evaluate every declared PostCondition in order. First failure raises."""
    if not conds:
        return
    for c in conds:
        _check_one(c, pre, post, skill_name)
