"""validate_rebuilt_body — atomic, read-only.

Post-variation geometric validation (verification plan P3). After a
(possibly scaled) feature catalog is rebuilt by the plan executor, PROVE
the planned cut features were actually realized — without this, a rebuild
that silently lost 100% of its holes (zero-delta SKIP on every cut)
reports PASS.

Checks (opt-in via ``checks``):
    "features" — census of the EXECUTED plan's cut steps. For each
        world-placed cut step (``hole`` / ``extrude_pocket_world``) probe
        3 points along the cut axis (0.25 / 0.5 / 0.75 of the depth,
        clamped to the body bbox) with ``BRepClass3d_SolidClassifier``:
        TopAbs_IN means MATERIAL where the void should be (cut lost),
        TopAbs_OUT means the void was realized. Majority vote across the
        3 probes tolerates partial cuts. The census source is the PLAN
        (not the catalog) so features the planner deliberately filtered
        (phantom fasteners, sub-threshold cuts) raise no false alarms.
        Box-mode plans carry coordinates already shifted into the box
        frame — the rebuilt body IS in box frame, so plan args are used
        verbatim. face_selector + position_xy style steps (preserve_brep
        mode: counterbore_hole / countersink_hole / tap_drill_hole /
        clearance_hole / extrude_pocket) have no world anchor and are
        skipped (counted in ``skipped_steps``).
    "brep" — BRepCheck_Analyzer(shape).IsValid(); optionally
        BOPAlgo_CheckerSI when ``check_self_intersection`` is set
        (expensive — off by default).
    "bbox" — per-axis extent vs ``expected_bbox_mm`` within 0.5%.

``min_wall_mm`` (independent of ``checks``) runs inspect_wall_thickness_v2
and gates global_min against the given floor.

extras schema:
    {"rebuilt_validation": {
        "valid": bool,                     # all enabled checks ok
        "brep_valid": bool | None,
        "self_intersection_free": bool | None,
        "features_expected": int,
        "features_realized": int,
        "features_lost": [{"step_id", "skill", "probe", "reason"}, ...],
        "bbox_axis_ratios": [rx, ry, rz] | None,
        "skipped_steps": int,
        "min_wall_mm_measured": float | None,
     }}

body 는 변경하지 않는다 (post ``body_present``).
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


# ── plan-step census ─────────────────────────────────────────────────────────
#
# Cut steps with WORLD coordinates — probe-able directly. These are the
# steps plan_from_feature_catalog emits in box mode (the primary consumer
# of this skill): generic ``hole`` (position + direction + depth) and
# ``extrude_pocket_world`` (world_origin + axis_dir + depth).
_WORLD_CUT_SKILLS = {"hole", "extrude_pocket_world"}

# Cut steps anchored by face_selector + position_xy / sketch — emitted in
# preserve_brep mode. Their entry plane is resolved at execution time from
# the body's faces, so there is no world anchor to probe; skip the census
# for these and surface the count so the caller knows coverage is partial.
_FACE_ANCHORED_CUT_SKILLS = {
    "counterbore_hole",
    "countersink_hole",
    "tap_drill_hole",
    "clearance_hole",
    "extrude_pocket",
}

_DIR_STR_TO_VEC: dict[str, tuple[float, float, float]] = {
    "+X": (1.0, 0.0, 0.0), "-X": (-1.0, 0.0, 0.0),
    "+Y": (0.0, 1.0, 0.0), "-Y": (0.0, -1.0, 0.0),
    "+Z": (0.0, 0.0, 1.0), "-Z": (0.0, 0.0, -1.0),
}

# Probe fractions along the (bbox-clamped) cut axis. Three points +
# majority vote tolerate partial cuts (e.g. a hole realized at 60% depth
# still reads OUT at 0.25/0.5 and only IN at 0.75 → realized).
_PROBE_FRACTIONS = (0.25, 0.5, 0.75)

# Shrink the body bbox by this margin when clamping the probe segment so
# probes never sit exactly ON the outer skin (classifier ON state is
# inconclusive).
_BBOX_CLAMP_EPS_MM = 1e-3


def _parse_world_cut(
    skill_name: str, args: dict,
) -> tuple[tuple[float, float, float], tuple[float, float, float], float] | None:
    """Extract (entry_origin, unit_axis_into_void, depth_mm) from a
    world-placed cut step's args. Returns None when the args are malformed
    (caller counts the step as skipped rather than crashing the census).
    """
    try:
        if skill_name == "hole":
            pos = args.get("position")
            ox, oy, oz = float(pos[0]), float(pos[1]), float(pos[2])
            # hole skill: depth_mm=None means "through" (internally 200).
            depth = float(args.get("depth_mm") or 200.0)
            dvec = _DIR_STR_TO_VEC.get(str(args.get("direction") or "-Z"))
            if dvec is None or depth <= 0.0:
                return None
            return ((ox, oy, oz), dvec, depth)
        if skill_name == "extrude_pocket_world":
            org = args.get("world_origin")
            ox, oy, oz = float(org[0]), float(org[1]), float(org[2])
            ad = args.get("axis_dir")
            ax, ay, az = float(ad[0]), float(ad[1]), float(ad[2])
            mag = math.sqrt(ax * ax + ay * ay + az * az)
            depth = float(args.get("depth_mm") or 0.0)
            if mag < 1e-9 or depth <= 0.0:
                return None
            # direction="into" → prism extends in -axis_dir; "out" → +axis_dir
            # (see extrude_pocket_world._build_world_prism).
            sign = -1.0 if str(args.get("direction") or "into") == "into" else 1.0
            return (
                (ox, oy, oz),
                (sign * ax / mag, sign * ay / mag, sign * az / mag),
                depth,
            )
    except Exception:
        return None
    return None


def _clamp_segment_to_bbox(
    origin: tuple[float, float, float],
    axis: tuple[float, float, float],
    depth: float,
    bbox: tuple[float, float, float, float, float, float],
) -> tuple[float, float] | None:
    """Intersect the parametric probe segment ``origin + t*axis, t∈[0,depth]``
    with the body bbox shrunk by ``_BBOX_CLAMP_EPS_MM`` (slab method).

    Through-cuts are emitted with depth larger than the body, so probes at
    raw 0.25/0.5/0.75·depth could land OUTSIDE the body entirely and read
    TopAbs_OUT even when the cut was never made. Clamping keeps every
    probe inside the body envelope, where "OUT" really means "void".

    Returns the clamped (t_lo, t_hi), or None when the axis misses the
    bbox altogether (tool placed off the body — that cut never happened).
    """
    lo, hi = 0.0, float(depth)
    for i in range(3):
        bmin = float(bbox[i]) + _BBOX_CLAMP_EPS_MM
        bmax = float(bbox[i + 3]) - _BBOX_CLAMP_EPS_MM
        if bmin > bmax:  # degenerate (paper-thin) axis — keep midplane
            bmin = bmax = (float(bbox[i]) + float(bbox[i + 3])) * 0.5
        d = axis[i]
        if abs(d) < 1e-12:
            if origin[i] < bmin or origin[i] > bmax:
                return None
            continue
        ta = (bmin - origin[i]) / d
        tb = (bmax - origin[i]) / d
        if ta > tb:
            ta, tb = tb, ta
        lo = max(lo, ta)
        hi = min(hi, tb)
        if lo >= hi:
            return None
    return (lo, hi)


def _classify_point(shape, pnt: tuple[float, float, float]) -> str:
    """'in' | 'out' | 'on' | 'unknown' for a world point vs the solid."""
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON, TopAbs_OUT

    try:
        clsf = BRepClass3d_SolidClassifier(
            shape, gp_Pnt(pnt[0], pnt[1], pnt[2]), 1e-6,
        )
        state = clsf.State()
    except Exception:
        return "unknown"
    if state == TopAbs_IN:
        return "in"
    if state == TopAbs_OUT:
        return "out"
    if state == TopAbs_ON:
        return "on"
    return "unknown"


def _shape_bbox(shape) -> tuple[float, float, float, float, float, float] | None:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    try:
        bb = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, bb)
        if bb.IsVoid():
            return None
        return bb.Get()
    except Exception:
        return None


def _self_intersection_free(shape) -> bool | None:
    """True if BOPAlgo_CheckerSI finds no self-interference; None on failure."""
    from OCP.BOPAlgo import BOPAlgo_CheckerSI
    from OCP.TopTools import TopTools_ListOfShape

    try:
        checker = BOPAlgo_CheckerSI()
        args_list = TopTools_ListOfShape()
        args_list.Append(shape)
        checker.SetArguments(args_list)
        checker.Perform()
        return not checker.HasErrors()
    except Exception:
        return None


@skill(
    name="validate_rebuilt_body",
    category="inspect",
    level="atomic",
    summary="Post-variation rebuild validation: probe each planned cut's "
            "expected void with BRepClass3d_SolidClassifier (material there "
            "= cut lost), BRepCheck BREP health, bbox extents vs expected "
            "within 0.5%, optional min-wall gate. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["rebuilt_validation_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.no_body"],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class ValidateRebuiltBody(SkillBase):
    class Args(BaseModel):
        plan: dict | None = Field(
            default=None,
            description="The EXECUTED plan dict (steps with skill names + "
                        "args). Census source for the 'features' check.",
        )
        checks: list[str] = Field(
            default_factory=lambda: ["features", "brep", "bbox"],
        )
        expected_bbox_mm: list[float] | None = Field(
            default=None,
            description="[xmin, ymin, zmin, xmax, ymax, zmax] the rebuilt "
                        "body is expected to fill (box frame).",
        )
        min_wall_mm: float | None = Field(default=None, gt=0.0)
        check_self_intersection: bool = Field(
            default=False,
            description="Run BOPAlgo_CheckerSI as part of the 'brep' check "
                        "(expensive).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("validate_rebuilt_body: body is None")
        if args.expected_bbox_mm is not None and len(args.expected_bbox_mm) != 6:
            raise ValueError(
                "validate_rebuilt_body: expected_bbox_mm must have 6 entries "
                f"[xmin, ymin, zmin, xmax, ymax, zmax], got "
                f"{len(args.expected_bbox_mm)}"
            )

        shape = _occt_shape(body)
        checks = set(args.checks)
        body_bbox = _shape_bbox(shape)

        # ── 1. feature census ────────────────────────────────────────────
        features_expected = 0
        features_realized = 0
        features_lost: list[dict[str, Any]] = []
        skipped_steps = 0
        features_ok = True
        if "features" in checks and args.plan is not None:
            steps = args.plan.get("steps") or []
            for step in steps:
                if not isinstance(step, dict):
                    continue
                skill_name = step.get("skill")
                step_args = step.get("args") or {}
                if skill_name in _FACE_ANCHORED_CUT_SKILLS:
                    # preserve_brep style — no world anchor to probe.
                    skipped_steps += 1
                    continue
                if skill_name not in _WORLD_CUT_SKILLS:
                    continue
                parsed = _parse_world_cut(skill_name, step_args)
                if parsed is None:
                    skipped_steps += 1
                    continue
                origin, axis, depth = parsed
                features_expected += 1

                seg = None
                if body_bbox is not None:
                    seg = _clamp_segment_to_bbox(origin, axis, depth, body_bbox)
                if seg is None and body_bbox is not None:
                    features_lost.append({
                        "step_id": step.get("id"),
                        "skill": skill_name,
                        "probe": [round(c, 4) for c in origin],
                        "reason": "probe axis misses the body bbox — the "
                                  "cut never intersected the body",
                    })
                    continue
                t_lo, t_hi = seg if seg is not None else (0.0, depth)

                votes = {"in": 0, "out": 0, "on": 0, "unknown": 0}
                mid_probe = origin
                for frac in _PROBE_FRACTIONS:
                    t = t_lo + frac * (t_hi - t_lo)
                    p = (
                        origin[0] + axis[0] * t,
                        origin[1] + axis[1] * t,
                        origin[2] + axis[2] * t,
                    )
                    if frac == 0.5:
                        mid_probe = p
                    votes[_classify_point(shape, p)] += 1

                if votes["out"] > votes["in"]:
                    features_realized += 1
                else:
                    features_lost.append({
                        "step_id": step.get("id"),
                        "skill": skill_name,
                        "probe": [round(c, 4) for c in mid_probe],
                        "reason": f"material at {votes['in']}/"
                                  f"{len(_PROBE_FRACTIONS)} probe(s) inside "
                                  f"the expected void (votes: in={votes['in']}"
                                  f" out={votes['out']} on={votes['on']})",
                    })
            features_ok = not features_lost

        # ── 2. BREP health ───────────────────────────────────────────────
        brep_valid: bool | None = None
        si_free: bool | None = None
        brep_ok = True
        if "brep" in checks:
            from OCP.BRepCheck import BRepCheck_Analyzer

            try:
                brep_valid = bool(BRepCheck_Analyzer(shape).IsValid())
            except Exception:
                brep_valid = False
            brep_ok = brep_valid is True
            if args.check_self_intersection:
                si_free = _self_intersection_free(shape)
                # None = checker itself failed — inconclusive, don't fail.
                if si_free is False:
                    brep_ok = False

        # ── 3. bbox extents vs expected ──────────────────────────────────
        bbox_axis_ratios: list[float] | None = None
        bbox_ok = True
        if "bbox" in checks and args.expected_bbox_mm is not None:
            if body_bbox is None:
                bbox_ok = False
            else:
                exp = [float(c) for c in args.expected_bbox_mm]
                bbox_axis_ratios = []
                for i in range(3):
                    exp_ext = exp[i + 3] - exp[i]
                    got_ext = float(body_bbox[i + 3]) - float(body_bbox[i])
                    if exp_ext <= 0.0:
                        bbox_axis_ratios.append(float("inf"))
                        bbox_ok = False
                        continue
                    ratio = got_ext / exp_ext
                    bbox_axis_ratios.append(round(ratio, 6))
                    if abs(ratio - 1.0) > 0.005:
                        bbox_ok = False

        # ── 4. optional min-wall gate ────────────────────────────────────
        min_wall_measured: float | None = None
        wall_ok = True
        if args.min_wall_mm is not None:
            from phone_designer.skills.inspect.inspect_wall_thickness_v2 import (
                InspectWallThicknessV2,
            )

            report = InspectWallThicknessV2().apply(body, {}).extras[
                "wall_thickness"
            ]
            min_wall_measured = float(report["global_min"])
            wall_ok = min_wall_measured >= float(args.min_wall_mm)

        valid = bool(features_ok and brep_ok and bbox_ok and wall_ok)
        extras = {
            "rebuilt_validation": {
                "valid": valid,
                "brep_valid": brep_valid,
                "self_intersection_free": si_free,
                "features_expected": features_expected,
                "features_realized": features_realized,
                "features_lost": features_lost,
                "bbox_axis_ratios": bbox_axis_ratios,
                "skipped_steps": skipped_steps,
                "min_wall_mm_measured": min_wall_measured,
            }
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
