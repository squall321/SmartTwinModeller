"""PILLAR FREEFORM (phase-2, 2026-06-14): real loft cross-section recovery.

Covers:
  * a circle→rounded-rect loft fixture whose TWO real cross-sections are
    recovered by ``_recover_loft_profile`` and consumed by ``_loft_boss_step``,
    dropping the round-trip geometry_deviation hausdorff vs the circle-only
    estimate (the headline win);
  * the BYTE-IDENTITY guarantee: when section recovery returns nothing the loft
    feature carries NO profile keys and ``_loft_boss_step`` emits the historic
    circle sketch EXACTLY (the corpus-baseline safety contract);
  * the ``_classify_base_topology`` dispatcher labelling a lathe-style body
    ``revolved``.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
    _occt_shape,
    _recover_loft_profile,
)
from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
    _classify_base_topology,
    _hausdorff_inproc,
    _loft_boss_step,
)


# ── fixture helpers ─────────────────────────────────────────────────────────


def _circle_wire(r: float, z: float):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    ax = gp_Ax2(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1))
    e = BRepBuilderAPI_MakeEdge(gp_Circ(ax, r)).Edge()
    return BRepBuilderAPI_MakeWire(e).Wire()


def _circle_to_rounded_rect_loft(
    lower_r: float = 10.0,
    rect_l: float = 24.0,
    rect_w: float = 16.0,
    corner_r: float = 4.0,
    height: float = 20.0,
):
    """Standalone solid lofted circle (z=0) → rounded-rect (z=height)."""
    from build123d import Part, RectangleRounded
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.TopoDS import TopoDS

    rr = RectangleRounded(rect_l, rect_w, corner_r)
    wire = rr.faces()[0].outer_wire()
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(0, 0, height))
    upper_w = TopoDS.Wire_s(
        BRepBuilderAPI_Transform(wire.wrapped, t, True).Shape()
    )
    loft = BRepOffsetAPI_ThruSections(True, False, 1e-6)
    loft.AddWire(_circle_wire(lower_r, 0.0))
    loft.AddWire(upper_w)
    loft.Build()
    return Part(loft.Shape())


def _run_loft_step(feat: dict, base_l=40.0, base_w=40.0, base_h=5.0):
    """Execute box base + a single _loft_boss_step(feat); return the body."""
    from phone_designer.plan.executor import PlanExecutor
    from phone_designer.plan.model import Plan

    step = _loft_boss_step(0, feat)
    plan = Plan(
        plan_name="loft_section_recovery_test",
        steps=[
            {
                "id": "s_base",
                "skill": "box",
                "args": {"length_mm": base_l, "width_mm": base_w, "height_mm": base_h},
            },
            step,
        ],
    )
    return PlanExecutor(plan).run(initial_body=None).final_body


# ── recovery + sketch selection ─────────────────────────────────────────────


def test_recover_loft_profile_returns_real_sections():
    """The circle→rounded-rect loft's two real cross-sections are recovered."""
    body = _circle_to_rounded_rect_loft()
    shape = _occt_shape(body)

    lower = _recover_loft_profile(shape, 0.0, 0.0, 0.0, 20.0)
    upper = _recover_loft_profile(shape, 0.0, 0.0, 20.0, 24.0)

    assert lower is not None, "lower (circle, D=20) section must be recovered"
    assert upper is not None, "upper (rounded-rect, ~24) section must be recovered"
    # Executable sketch dicts consumable by _sketch_to_solid.
    assert lower["kind"] in ("polygon", "composite", "bspline_closed")
    assert upper["kind"] in ("polygon", "composite", "bspline_closed")


def test_loft_boss_step_prefers_recovered_profile():
    """When the feature carries profile keys, the step emits them (not circle)."""
    body = _circle_to_rounded_rect_loft()
    shape = _occt_shape(body)
    lower = _recover_loft_profile(shape, 0.0, 0.0, 0.0, 20.0)
    upper = _recover_loft_profile(shape, 0.0, 0.0, 20.0, 24.0)
    assert lower is not None and upper is not None

    feat = {
        "id": 0, "anchor_z": 5.0, "height_mm": 20.0,
        "lower_diameter_mm": 20.0, "upper_diameter_mm": 24.0,
        "center_xy": [0.0, 0.0], "kind": "boss",
        "lower_profile": lower, "upper_profile": upper,
    }
    step = _loft_boss_step(0, feat)
    args = step["args"]
    assert args["lower_sketch"]["kind"] != "circle"
    assert args["upper_sketch"]["kind"] != "circle"
    # Recovered loop re-centred at the loft centre.
    assert args["lower_sketch"]["center_x_mm"] == 0.0
    assert args["upper_sketch"]["center_y_mm"] == 0.0


def test_loft_profile_beats_circle_hausdorff():
    """The real-section loft regen is geometrically closer to the truth than
    the circle-only estimate (THE anti-fake geometry_deviation gate)."""
    truth = _circle_to_rounded_rect_loft()
    shape = _occt_shape(truth)
    lower = _recover_loft_profile(shape, 0.0, 0.0, 0.0, 20.0)
    upper = _recover_loft_profile(shape, 0.0, 0.0, 20.0, 24.0)
    assert lower is not None and upper is not None

    feat_circle = {
        "id": 0, "anchor_z": 5.0, "height_mm": 20.0,
        "lower_diameter_mm": 20.0, "upper_diameter_mm": 24.0,
        "center_xy": [0.0, 0.0], "kind": "boss",
    }
    feat_prof = dict(feat_circle, lower_profile=lower, upper_profile=upper)

    body_circle = _run_loft_step(feat_circle)
    body_prof = _run_loft_step(feat_prof)

    # Truth solid placed on the same z=5 base for an apples-to-apples compare.
    from build123d import Part
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec

    base = Box().apply(None, {
        "length_mm": 40.0, "width_mm": 40.0, "height_mm": 5.0,
    }).body
    tt = gp_Trsf()
    tt.SetTranslation(gp_Vec(0, 0, 5))
    truth_up = BRepBuilderAPI_Transform(shape, tt, True).Shape()
    fuse = BRepAlgoAPI_Fuse(base.wrapped, truth_up)
    fuse.Build()
    truth_on_base = Part(fuse.Shape())

    h_circle = _hausdorff_inproc(body_circle.wrapped, truth_on_base.wrapped)
    h_prof = _hausdorff_inproc(body_prof.wrapped, truth_on_base.wrapped)
    assert h_circle is not None and h_prof is not None
    assert h_prof < h_circle, (
        f"recovered profile must reduce hausdorff: "
        f"circle={h_circle:.4f} profile={h_prof:.4f}"
    )
    # Real win, not noise: the profile should be dramatically tighter.
    assert h_prof < 0.5 * h_circle


# ── byte-identity reference ─────────────────────────────────────────────────


def test_loft_step_byte_identical_when_recovery_absent():
    """A loft feature with NO profile keys → _loft_boss_step emits the historic
    circle sketch EXACTLY. This is the corpus byte-identity contract: when
    section recovery returns nothing the catalog carries no profile keys and the
    plan is bit-for-bit what it was before the feature existed."""
    feat = {
        "id": 0, "anchor_z": 5.0, "height_mm": 20.0,
        "lower_diameter_mm": 20.0, "upper_diameter_mm": 24.0,
        "center_xy": [1.5, -2.5], "kind": "boss",
    }
    step = _loft_boss_step(0, feat)
    args = step["args"]
    assert args["lower_sketch"] == {
        "kind": "circle", "diameter_mm": 20.0,
        "center_x_mm": 1.5, "center_y_mm": -2.5,
    }
    assert args["upper_sketch"] == {
        "kind": "circle", "diameter_mm": 24.0,
        "center_x_mm": 1.5, "center_y_mm": -2.5,
    }


def test_recover_loft_profile_rejects_oversized_slab_loop():
    """The span-vs-diameter gate rejects a recovered loop far larger than the
    loft's circle-diameter estimate (the body's whole-slab outline). The
    feature then keeps its circle fallback → catalog byte-identity preserved."""
    # A plain 40×40×10 slab: a section at z=5 returns the 40 mm slab outline.
    body = Box().apply(None, {
        "length_mm": 40.0, "width_mm": 40.0, "height_mm": 10.0,
    }).body
    shape = _occt_shape(body)
    # Pretend the loft estimate diameter is a tiny 6 mm — the 40 mm slab span is
    # WAY outside the ±50 % band, so recovery must be rejected (returns None).
    rec = _recover_loft_profile(shape, 0.0, 0.0, 5.0, 6.0)
    assert rec is None


# ── base-topology dispatcher ────────────────────────────────────────────────


def test_classify_base_topology_revolved_cylinder():
    """A pure cylinder (single dominant rotational axis) classifies revolved."""
    from phone_designer.skills.create.cylinder import Cylinder

    body = Cylinder().apply(None, {"radius_mm": 8.0, "height_mm": 25.0}).body
    assert _classify_base_topology(body, None) == "revolved"


def test_classify_base_topology_box_for_generic_cube():
    """A near-cubic box has no dominant rotational axis nor a tight prismatic
    sweep → classifies box (default; box base kept)."""
    body = Box().apply(None, {
        "length_mm": 20.0, "width_mm": 20.0, "height_mm": 20.0,
    }).body
    assert _classify_base_topology(body, None) == "box"
