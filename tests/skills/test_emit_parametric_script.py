"""emit_parametric_script — editable parametric build123d recovery (2026-06-19).

Proves the recovered script is (1) GENERATED with named editable parameters and an
ordered feature history, (2) EXECUTABLE and reconstructs the part (geometry_
deviation Hausdorff — the anti-fake ground truth, never match_ratio), and (3)
genuinely PARAMETRIC (scaling housing_length regenerates a wider part).
"""
from __future__ import annotations

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.hole import Hole
from phone_designer.skills.reverse_engineer.emit_parametric_script import (
    EmitParametricScript,
    build_parametric_script,
)


# ──────────────────────────────────────────────────────────────────────────────
# Pure generator (no OCCT) — fast.


def test_build_script_emits_named_params_and_ops():
    bbox = [-25.0, -20.0, 0.0, 25.0, 20.0, 10.0]
    steps = [
        {"id": "s_base", "skill": "box",
         "args": {"length_mm": 50.0, "width_mm": 40.0, "height_mm": 10.0}},
        {"id": "s_pocket_0", "skill": "extrude_pocket",
         "args": {"sketch": {"kind": "rectangle", "length_mm": 5.0, "width_mm": 5.0,
                             "center_x_mm": -15.0, "center_y_mm": -12.0},
                  "depth_mm": 6.0}},
        {"id": "s_hole_0", "skill": "hole",
         "args": {"position": [10.0, 8.0, 10.0], "diameter_mm": 3.0, "depth_mm": 6.0,
                  "direction": "-Z"}},
        {"id": "s_blend", "skill": "edge_blend", "args": {"radius_mm": 1.0}},
    ]
    kd = [
        {"name": "housing_length", "value_mm": 50.0, "role": "envelope"},
        {"name": "primary_bore_diameter", "value_mm": 3.0, "role": "primary_bore"},
    ]
    gen = build_parametric_script(steps, kd, bbox)

    # named base envelope params are always present
    names = {p["name"] for p in gen["parameters"]}
    assert {"housing_length", "housing_width", "housing_height"} <= names
    # the script is valid-looking build123d
    src = gen["script"]
    assert "from build123d import" in src
    assert "housing_length = 50.0" in src
    assert "Box(housing_length, housing_width, housing_height)" in src
    # pocket + hole emitted, the unsupported edge_blend recorded as skipped (honest)
    assert gen["coverage"]["emitted"].get("extrude_pocket") == 1
    assert gen["coverage"]["emitted"].get("hole") == 1
    assert gen["coverage"]["skipped"].get("edge_blend") == 1
    assert gen["coverage"]["fully_covered"] is False
    # the script compiles
    compile(src, "<test>", "exec")


def test_primary_bore_diameter_is_a_named_handle():
    bbox = [-25.0, -20.0, 0.0, 25.0, 20.0, 10.0]
    steps = [
        {"id": "s_base", "skill": "box",
         "args": {"length_mm": 50.0, "width_mm": 40.0, "height_mm": 10.0}},
        {"id": "s_hole_0", "skill": "hole",
         "args": {"position": [0.0, 0.0, 10.0], "diameter_mm": 3.0, "depth_mm": 6.0}},
    ]
    kd = [{"name": "primary_bore_diameter", "value_mm": 3.0, "role": "primary_bore"}]
    src = build_parametric_script(steps, kd, bbox)["script"]
    # the Ø3 hole references the named param, not a bare literal
    assert "Cylinder(primary_bore_diameter/2, 6.0)" in src


# ──────────────────────────────────────────────────────────────────────────────
# End-to-end: generate -> execute -> Hausdorff -> edit-check.


def _box_with_holes():
    b = Box().apply(None, {"length_mm": 50.0, "width_mm": 40.0, "height_mm": 10.0}).body
    for (x, y) in [(-15.0, -12.0), (15.0, -12.0), (0.0, 12.0)]:
        b = Hole().apply(b, {"position": (x, y, 10.0), "diameter_mm": 5.0,
                             "depth_mm": 6.0, "direction": "-Z"}).body
    return b


def test_generated_script_reconstructs_the_part():
    res = EmitParametricScript().apply(_box_with_holes(), {"verify": True})
    ps = res.extras["parametric_script"]
    assert ps["ok"] is True
    assert ps["n_parameters"] >= 3
    # the executed script reconstructs the part — Hausdorff is the box-mode
    # reconstruction fidelity (a few mm for small-hole rect proxies), NOT inflated.
    assert ps["hausdorff_mm"] is not None
    assert ps["hausdorff_mm"] < 5.0, ps["hausdorff_mm"]


def test_named_parameter_is_genuinely_editable():
    res = EmitParametricScript().apply(
        _box_with_holes(), {"edit_check_scale": 1.2})
    ec = res.extras["parametric_script"]["edit_check"]
    # scaling housing_length by 1.2 regenerates a 1.2x wider part
    assert ec["is_parametric"] is True
    assert abs(ec["x_ratio"] - 1.2) < 0.02


def test_missing_features_degrade_honestly():
    # a featureless plain box: still emits a valid editable base, no crash.
    body = Box().apply(None, {"length_mm": 20.0, "width_mm": 16.0, "height_mm": 8.0}).body
    res = EmitParametricScript().apply(body, {"verify": True})
    ps = res.extras["parametric_script"]
    assert ps["ok"] is True
    assert "Box(housing_length, housing_width, housing_height)" in ps["script"]
    # a plain box reconstructs near-exactly
    assert ps["hausdorff_mm"] is not None and ps["hausdorff_mm"] < 0.5


def test_linear_pattern_collapses_to_one_editable_loop():
    """4 holes in a row -> ONE linear-pattern loop driven by a NAMED pitch param
    (true design intent), reconstructing exactly; changing the pitch genuinely
    re-spaces the holes."""
    b = Box().apply(None, {"length_mm": 60.0, "width_mm": 24.0, "height_mm": 8.0}).body
    for x in (-18.0, -6.0, 6.0, 18.0):
        b = Hole().apply(b, {"position": (x, 0.0, 8.0), "diameter_mm": 3.0,
                             "depth_mm": 5.0, "direction": "-Z"}).body
    res = EmitParametricScript().apply(b, {"verify": True})
    ps = res.extras["parametric_script"]
    # the 4 holes are ONE pattern loop, not 4 independent cuts
    assert ps["coverage"]["emitted"].get("linear_pattern") == 1
    src = ps["script"]
    assert "for _i in range(4):" in src and "pattern_0_pitch_mm" in src
    assert ps["hausdorff_mm"] is not None and ps["hausdorff_mm"] < 0.5

    # the pitch genuinely drives the geometry: at pitch 12 there is a hole at
    # x=18; shrink the pitch to 8 and x=18 becomes solid material.
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_State

    def _inside(part, x, y, z):
        clf = BRepClass3d_SolidClassifier(part.wrapped)
        clf.Perform(gp_Pnt(x, y, z), 1e-6)
        return clf.State() == TopAbs_State.TopAbs_IN

    ns12: dict = {}
    ns8: dict = {}
    exec(compile(src, "<p12>", "exec"), ns12, ns12)  # noqa: S102
    exec(compile(src.replace("pattern_0_pitch_mm = 12.0", "pattern_0_pitch_mm = 8.0"),
                 "<p8>", "exec"), ns8, ns8)  # noqa: S102
    assert _inside(ns12["part"], 18.0, 0.0, 5.5) is False   # void (hole at x=18)
    assert _inside(ns8["part"], 18.0, 0.0, 5.5) is True      # solid (no hole there)


def test_circular_pattern_emits_polar_loop_with_named_radius():
    """6 holes on a bolt circle -> ONE circular-pattern polar loop with a NAMED
    radius param, reconstructing exactly (the ring is stored as center+radius+
    angular_pitch, not explicit positions)."""
    import math as _m
    b = Box().apply(None, {"length_mm": 50.0, "width_mm": 50.0, "height_mm": 8.0}).body
    for k in range(6):
        a = _m.radians(k * 60.0)
        b = Hole().apply(b, {"position": (15.0 * _m.cos(a), 15.0 * _m.sin(a), 8.0),
                             "diameter_mm": 3.0, "depth_mm": 5.0, "direction": "-Z"}).body
    ps = EmitParametricScript().apply(b, {"verify": True}).extras["parametric_script"]
    assert ps["coverage"]["emitted"].get("circular_pattern") == 1
    src = ps["script"]
    assert "pattern_0_radius_mm" in src and "_math.cos" in src
    assert ps["hausdorff_mm"] is not None and ps["hausdorff_mm"] < 0.5
