"""sketch_sweep + sketch_loft — from-scratch sweep/loft create skills.

Pins the two remaining classic-CAD-from-scratch capabilities alongside
sketch_extrude/sketch_revolve:
  * sketch_sweep — sweep a closed 2D section along a line+arc PATH (a pipe/handle).
  * sketch_loft  — loft a solid through ≥2 closed sections at heights (a frustum).

Volumes are exact analytic values (Pappus / prismatoid). Also pins the traps the
OCCT builders do NOT guard — a KINKED path silently drops swept volume, a section
wider than the bend radius self-intersects, a CLOSED/DISCONNECTED spine is refused;
is_solid is gated on volume>0. The wire-cast fix is implicitly pinned: a bare
TopoDS_Shape fed to ThruSections/MakePipeShell HANGS, so a passing (non-timeout)
build proves the TopoDS.Wire_s cast is in place.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from phone_designer.skills.create.sketch_loft import SketchLoft
from phone_designer.skills.create.sketch_sweep import SketchSweep


def _sw(**kw):
    return SketchSweep().apply(None, kw).extras["sketch_solid"]


def _lo(**kw):
    return SketchLoft().apply(None, kw).extras["sketch_solid"]


_CIRC6 = {"kind": "circle", "diameter_mm": 6}
_RECT = {"kind": "rectangle", "length_mm": 6, "width_mm": 4}
# a tangent-continuous bent pipe: line → arc(r8, ccw) → line. The tangents match at
# each joint (line is horizontal → arc entry horizontal; arc exit vertical → line
# vertical), so it is G1-continuous and MakePipeShell keeps the full swept volume.
_BENT = {"kind": "path", "plane": "XY", "segments": [
    {"kind": "line", "start": [0, 0], "end": [20, 0]},
    {"kind": "arc", "start": [20, 0], "end": [28, 8], "radius": 8, "ccw": True},
    {"kind": "line", "start": [28, 8], "end": [28, 28]}]}


# ── sweep — builds ─────────────────────────────────────────────────────────────

def test_straight_sweep_equals_extrude():
    # rect 6×4 swept along a single straight line len 20 == prism 6·4·20.
    r = _sw(section=_RECT, path={"kind": "path", "plane": "XY", "segments": [
        {"kind": "line", "start": [0, 0], "end": [20, 0]}]})
    assert r["volume_mm3"] == pytest.approx(480.0, rel=1e-4)
    assert r["bbox_mm"] == pytest.approx([20.0, 4.0, 6.0])
    assert r["is_solid"] is True and r["kind"] == "sweep"


def test_bent_pipe_sweep_matches_pappus():
    # circle d=6 (A=π·9) swept along a tangent-continuous line+arc+line path.
    # Pappus: V = A · path_len = π·9 · 52.566 = 1486.279.
    r = _sw(section=_CIRC6, path=_BENT)
    assert r["path_len_mm"] == pytest.approx(52.566, rel=1e-3)
    assert r["volume_mm3"] == pytest.approx(1486.279, rel=1e-3)
    assert r["is_solid"] is True


def test_bent_pipe_sweep_cw_arc_matches_pappus():
    # the MIRROR of _BENT: line → arc(r8, ccw=False) → line, bending DOWN. It is
    # exactly as G1-continuous as the ccw twin (line exit (1,0) == cw-arc entry;
    # cw-arc exit (0,-1) == line direction). Pins the _edge_arc_radius fix: the
    # old +Z-circle/Sense=ccw build returned the 270° COMPLEMENT arc with a
    # backwards parameterization, so this path was falsely refused as a 90° kink.
    r = _sw(section=_CIRC6, path={"kind": "path", "plane": "XY", "segments": [
        {"kind": "line", "start": [0, 0], "end": [20, 0]},
        {"kind": "arc", "start": [20, 0], "end": [28, -8], "radius": 8, "ccw": False},
        {"kind": "line", "start": [28, -8], "end": [28, -28]}]})
    assert r["path_len_mm"] == pytest.approx(52.566, rel=1e-3)
    assert r["volume_mm3"] == pytest.approx(1486.279, rel=1e-3)
    assert r["is_solid"] is True


def test_sweep_cw_arc_cusp_refused():
    # a TRUE 170° cusp into a cw arc (entry tangent (-1,0) vs line dir ~(0.985,
    # 0.174)). The old complement-arc build let this cusp through SILENTLY
    # (spine swept the wrong 190° arc, vol 1077.99) — must be a kink refusal.
    import math as _m
    line_start = [-12 * _m.cos(_m.radians(10)), -12 * _m.sin(_m.radians(10))]
    arc_end = [8 * _m.cos(_m.radians(100)), 8 + 8 * _m.sin(_m.radians(100))]
    with pytest.raises(ValueError, match=r"sweep_tangent_discontinuity.*170\.0"):
        _sw(section=_CIRC6, path={"kind": "path", "plane": "XY", "segments": [
            {"kind": "line", "start": line_start, "end": [0, 0]},
            {"kind": "arc", "start": [0, 0], "end": arc_end,
             "radius": 8, "ccw": False}]})


def test_sweep_into_xz_plane_rotates_path():
    # a path declared in the XZ plane sweeps a solid whose bbox spans X and Z
    # (Y is the section's thin axis) — proves _rotate_to_plane orients the spine.
    r = _sw(section={"kind": "circle", "diameter_mm": 4},
            path={"kind": "path", "plane": "XZ", "segments": [
                {"kind": "line", "start": [0, 0], "end": [15, 0]}]})
    assert r["volume_mm3"] == pytest.approx(188.496, rel=1e-3)  # π·4·15
    assert r["bbox_mm"] == pytest.approx([15.0, 4.0, 4.0])
    assert r["plane"] == "XZ"


# ── sweep — honest refusals ──────────────────────────────────────────────────

def test_sweep_kink_refused():
    # a 90° line→line corner: MakePipeShell would silently drop the swept volume,
    # so a >30° tangent turn is a structured refusal, not a lying volume.
    with pytest.raises(ValueError, match="sweep_tangent_discontinuity"):
        _sw(section=_CIRC6, path={"kind": "path", "plane": "XY", "segments": [
            {"kind": "line", "start": [0, 0], "end": [20, 0]},
            {"kind": "line", "start": [20, 0], "end": [20, 20]}]})


def test_sweep_self_intersection_refused():
    # section d=12 (radial extent 6) on an arc of radius 3: the inner wall folds
    # through the arc centre → conservative up-front refusal.
    with pytest.raises(ValueError, match="sweep_self_intersection"):
        _sw(section={"kind": "circle", "diameter_mm": 12},
            path={"kind": "path", "plane": "XY", "segments": [
                {"kind": "line", "start": [0, 0], "end": [10, 0]},
                {"kind": "arc", "start": [10, 0], "end": [13, 3], "radius": 3, "ccw": True},
                {"kind": "line", "start": [13, 3], "end": [13, 20]}]})


def test_sweep_closed_path_refused():
    # a sweep spine must be OPEN; a closed loop is a validation-time refusal.
    with pytest.raises(ValidationError, match="sweep_path_closed"):
        _sw(section=_CIRC6, path={"kind": "path", "plane": "XY", "segments": [
            {"kind": "line", "start": [0, 0], "end": [10, 0]},
            {"kind": "line", "start": [10, 0], "end": [0, 0]}]})


def test_sweep_disconnected_path_refused():
    with pytest.raises(ValidationError, match="sweep_path_disconnected"):
        _sw(section=_CIRC6, path={"kind": "path", "plane": "XY", "segments": [
            {"kind": "line", "start": [0, 0], "end": [10, 0]},
            {"kind": "line", "start": [15, 5], "end": [20, 5]}]})


# ── loft — builds ─────────────────────────────────────────────────────────────

def test_ruled_frustum_matches_prismatoid():
    # 10×10 → 4×4 over height 10, ruled (linear). Prismatoid:
    # h/6·(A_bot + 4·A_mid + A_top) = 10/6·(100 + 4·49 + 16) = 520.0.
    r = _lo(sections=[{"kind": "rectangle", "length_mm": 10, "width_mm": 10},
                      {"kind": "rectangle", "length_mm": 4, "width_mm": 4}],
            heights_mm=[0, 10], ruled=True)
    assert r["volume_mm3"] == pytest.approx(520.0, rel=1e-4)
    assert r["bbox_mm"] == pytest.approx([10.0, 10.0, 10.0])
    assert r["is_solid"] is True and r["kind"] == "loft"


def test_smooth_square_to_circle_loft_builds():
    # the classic mismatched-vertex-count blend: a smooth (non-ruled) loft
    # tolerates a square → circle transition. Just assert a positive-volume solid.
    r = _lo(sections=[{"kind": "rectangle", "length_mm": 10, "width_mm": 10},
                      {"kind": "circle", "diameter_mm": 4}],
            heights_mm=[0, 10], ruled=False)
    assert r["is_solid"] and r["volume_mm3"] > 0
    assert r["section_kinds"] == ["rectangle", "circle"]


def test_three_section_loft_stacks():
    r = _lo(sections=[{"kind": "rectangle", "length_mm": 8, "width_mm": 8},
                      {"kind": "rectangle", "length_mm": 4, "width_mm": 4},
                      {"kind": "rectangle", "length_mm": 6, "width_mm": 6}],
            heights_mm=[0, 5, 10], ruled=True)
    assert r["is_solid"] and r["n_sections"] == 3
    assert r["bbox_mm"][2] == pytest.approx(10.0)


# ── loft — honest refusals ───────────────────────────────────────────────────

def test_loft_height_count_mismatch_refused():
    with pytest.raises(ValueError, match="loft_height_count_mismatch"):
        _lo(sections=[{"kind": "rectangle", "length_mm": 10, "width_mm": 10},
                      {"kind": "rectangle", "length_mm": 4, "width_mm": 4}],
            heights_mm=[0, 5, 10])


def test_loft_non_monotonic_heights_refused():
    with pytest.raises(ValueError, match="loft_heights_not_monotonic"):
        _lo(sections=[{"kind": "rectangle", "length_mm": 10, "width_mm": 10},
                      {"kind": "rectangle", "length_mm": 4, "width_mm": 4}],
            heights_mm=[10, 5])


# ── from-scratch via generate_from_spec (MCP) + discoverability ──────────────

def test_both_build_from_scratch_via_generate_from_spec():
    from phone_designer.skills.create.generate_from_spec import GenerateFromSpec
    gs = GenerateFromSpec().apply(None, {"spec": [
        {"op": "sketch_sweep", "args": {
            "section": _RECT,
            "path": {"kind": "path", "plane": "XY", "segments": [
                {"kind": "line", "start": [0, 0], "end": [20, 0]}]}}}]}).extras["generated"]
    assert gs["ok"] and gs["is_solid"] and gs["volume_mm3"] == pytest.approx(480.0, rel=1e-4)
    gl = GenerateFromSpec().apply(None, {"spec": [
        {"op": "sketch_loft", "args": {
            "sections": [{"kind": "rectangle", "length_mm": 10, "width_mm": 10},
                         {"kind": "rectangle", "length_mm": 4, "width_mm": 4}],
            "heights_mm": [0, 10], "ruled": True}}]}).extras["generated"]
    assert gl["ok"] and gl["is_solid"] and gl["volume_mm3"] == pytest.approx(520.0, rel=1e-4)


def test_sweep_and_loft_are_discoverable_in_the_manifest():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    assert {"sketch_sweep", "sketch_loft"} <= names
    sw = next(s for s in m["skills"] if s["name"] == "sketch_sweep")
    assert sw["category"] == "create" and sw["level"] == "atomic"
    defs = (sw["args_schema"] or {}).get("$defs", {})
    # the SweepPath model + its arc segment must be published so a client can
    # learn the line+arc path shape from the schema alone.
    assert "SweepPath" in defs and "ArcSegment" in defs


# ── orientation='fixed' — TRUE fixed trihedron (SetMode(gp_Ax2) overload) ─────
# Pins the fix for: SetMode(bool) made 'fixed' behave as Corrected-Frenet, so
# fixed and frenet were byte-identical on a bent path. A gentle 30°-turn path:
# line (0,0)→(20,0) then ccw arc r=20 → end (30, 2.679492), G1 at the joint.

_BENT30 = {"kind": "path", "plane": "XY", "segments": [
    {"kind": "line", "start": [0, 0], "end": [20, 0]},
    {"kind": "arc", "start": [20, 0], "end": [30.0, 2.679492],
     "radius": 20, "ccw": True}]}


def test_fixed_orientation_differs_from_frenet_on_bent_path():
    # frenet follows the tangent → Pappus V = A·L = 9π·(20 + 20·π/6) = 861.575.
    # fixed keeps all sections PARALLEL → sheared prism (Cavalieri):
    # V = A · (net advance along the fixed start-tangent normal) = 9π·30 = 848.230.
    fre = _sw(section=_CIRC6, path=_BENT30, orientation="frenet")
    fix = _sw(section=_CIRC6, path=_BENT30, orientation="fixed")
    assert fre["volume_mm3"] == pytest.approx(861.575, rel=1e-3)
    assert fix["volume_mm3"] == pytest.approx(848.230, rel=1e-3)
    assert fix["volume_mm3"] != pytest.approx(fre["volume_mm3"], rel=1e-3)
    assert fix["orientation"] == "fixed" and fre["orientation"] == "frenet"


def test_fixed_orientation_straight_path_matches_prism():
    # trivial regression: on a straight spine the fixed trihedron equals the
    # frenet result — rect 6×4 · len 20 = 480 for both.
    path = {"kind": "path", "plane": "XY", "segments": [
        {"kind": "line", "start": [0, 0], "end": [20, 0]}]}
    for orientation in ("frenet", "fixed"):
        r = _sw(section=_RECT, path=path, orientation=orientation)
        assert r["volume_mm3"] == pytest.approx(480.0, rel=1e-4)
        assert r["is_solid"] is True


def test_fixed_orientation_90deg_turn_refused():
    # the docstring bent pipe turns 90° total: its last leg's tangent is
    # PERPENDICULAR to the fixed section normal, so the parallel sections
    # travel within their own plane — a zero-thickness wall OCCT would still
    # "build". Structured refusal instead of a degenerate flap.
    with pytest.raises(ValueError, match="sweep_degenerate"):
        _sw(section=_CIRC6, path=_BENT, orientation="fixed")


# ── self-intersection guard — extent about the LOCAL ORIGIN, not bbox size ───
# Pins the fix for: the guard used the bbox half-SIZE, ignoring center_x/y_mm,
# so an off-centre section rode off the spine, swept inside-out through the
# r=8 bend and returned signed-volume garbage (A·(40−π)) as is_solid=True.

def test_sweep_offcenter_section_on_tight_bend_refused():
    # circle d=6 at local y=−10: bbox half-size is only 3mm, but the section
    # reaches |y|=13mm from the spine ≥ the 8mm bend radius — the inner wall
    # folds through the arc centre.
    with pytest.raises(ValueError, match="sweep_self_intersection"):
        _sw(section={"kind": "circle", "diameter_mm": 6, "center_y_mm": -10},
            path=_BENT)


def test_sweep_offcenter_outer_side_refused_conservative():
    # +10 offset happens to be geometrically valid (outer side, r_eff=18), but
    # the planar guard has no per-arc bend-centre side info — documented
    # conservative refusal, same as the guard's helical-spring philosophy.
    with pytest.raises(ValueError, match="sweep_self_intersection"):
        _sw(section={"kind": "circle", "diameter_mm": 6, "center_y_mm": 10},
            path=_BENT)


def test_sweep_small_offcenter_section_builds_with_honest_pappus_volume():
    # d=4 at y=+2: extent about the local origin = 4mm < 8mm bend radius →
    # builds, and the volume is Pappus at the OFFSET centroid radius r_eff=10:
    # V = 4π·(40 + 10·π/2) = 700.047 — proves the origin-extent guard is not
    # over-conservative for in-range offsets.
    r = _sw(section={"kind": "circle", "diameter_mm": 4, "center_y_mm": 2},
            path=_BENT)
    assert r["volume_mm3"] == pytest.approx(700.047, rel=1e-3)
    assert r["is_solid"] is True
