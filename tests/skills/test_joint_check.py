"""joint_check — fastener-path integrity across bolted joints.

Covers:
    (a) THE REAL CASE: .pd_workspace/gearbox_asm.step — the cover AND the
        housing flange are both drilled clearance Ø5.5, so the 6 cover stacks
        must ALL come back verdict=all_clearance (the live v1 bug);
    (b) fixture: plate Ø5.5 over block Ø4.2 blind → ok_threaded (M5,
        clearance + TERMINAL tap-drill), member diameters/depths exact;
    (c) fm.no_holes refusal on a plain box (reachable failure mode);
    (d) all_tapped: both members at the M5 tap drill Ø4.2 — flagged;
    (e) convex cylinders (bosses / outer fillets) are NOT bores: two stacked
        solid cylinders share an axis but must refuse with fm.no_holes;
    (f) extras are strict-JSON-safe (json.dumps with allow_nan=False);
    (g) the skill is registered in the manifest (export_manifest import).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from phone_designer.skills.inspect.joint_check import JointCheck

GEARBOX_ASM = Path("d:/SmartTwinModeller/.pd_workspace/gearbox_asm.step")


def _compound(*parts):
    """OCCT compound of build123d parts — a multi-body 'assembly'."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    builder = BRep_Builder()
    comp = TopoDS_Compound()
    builder.MakeCompound(comp)
    for p in parts:
        builder.Add(comp, p.wrapped if hasattr(p, "wrapped") else p)
    return comp


def _plate(hole_d_mm: float, thick_mm: float = 5.0):
    """30x30 plate from z=0..thick with a through hole on the Z axis."""
    from build123d import Align, Box, Cylinder, Pos

    plate = Box(30, 30, thick_mm, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return plate - Pos(0, 0, -1) * Cylinder(
        radius=hole_d_mm / 2.0, height=thick_mm + 2.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN))


def _block(hole_d_mm: float, hole_depth_mm: float = 12.0):
    """30x30x20 block from z=-20..0 with a blind hole down from its top."""
    from build123d import Align, Box, Cylinder, Pos

    block = Pos(0, 0, -20) * Box(
        30, 30, 20, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return block - Pos(0, 0, -hole_depth_mm) * Cylinder(
        radius=hole_d_mm / 2.0, height=hole_depth_mm,
        align=(Align.CENTER, Align.CENTER, Align.MIN))


# ──────────────────────────────────────────────────────────────────────────────
# (a) THE REAL CASE — the gearbox v1 bug: 6 cover stacks, ALL all_clearance


@pytest.mark.skipif(not GEARBOX_ASM.exists(), reason="gearbox_asm.step artifact absent")
def test_gearbox_asm_six_stacks_all_clearance():
    r = JointCheck().apply(None, {"path": str(GEARBOX_ASM)})
    ex = r.extras

    assert ex["ok"] is True
    assert ex["summary"]["stack_count"] == 6
    assert ex["summary"]["all_clearance"] == 6
    assert ex["summary"]["ok_threaded"] == 0
    assert ex["summary"]["component_count"] == 2

    for s in ex["stacks"]:
        assert s["verdict"] == "all_clearance"
        assert s["nominal_guess"] == pytest.approx(5.0)
        assert s["designation"] == "M5"
        # Cover + housing flange — one member per component, both Ø5.5.
        assert {m["comp"] for m in s["members"]} == {0, 1}
        for m in s["members"]:
            assert m["d"] == pytest.approx(5.5, abs=0.01)
            assert m["class"] == "clearance"

    # The 6 bolt positions from the design: (±63,0) and (±50,±36.5).
    pts = sorted((round(s["axis_point"][0], 1), round(s["axis_point"][1], 1))
                 for s in ex["stacks"])
    assert pts == [(-63.0, 0.0), (-50.0, -36.5), (-50.0, 36.5),
                   (50.0, -36.5), (50.0, 36.5), (63.0, 0.0)]


# ──────────────────────────────────────────────────────────────────────────────
# (b) fixture: plate Ø5.5 over block Ø4.2 → ok_threaded


def test_plate_over_tapped_block_ok_threaded():
    comp = _compound(_plate(5.5), _block(4.2))
    r = JointCheck().apply(comp, {})
    ex = r.extras

    assert ex["ok"] is True
    assert ex["summary"]["stack_count"] == 1
    assert ex["summary"]["ok_threaded"] == 1

    (s,) = ex["stacks"]
    assert s["verdict"] == "ok_threaded"
    assert s["nominal_guess"] == pytest.approx(5.0)
    by_class = {m["class"]: m for m in s["members"]}
    assert by_class["clearance"]["d"] == pytest.approx(5.5, abs=0.01)
    assert by_class["clearance"]["depth"] == pytest.approx(5.0, abs=0.01)
    assert by_class["tap_drill"]["d"] == pytest.approx(4.2, abs=0.01)
    assert by_class["tap_drill"]["depth"] == pytest.approx(12.0, abs=0.01)
    # Members are ordered along the axis and the tap-drill bore is TERMINAL.
    assert s["members"][0]["class"] == "tap_drill"
    assert s["members"][-1]["class"] == "clearance"


# ──────────────────────────────────────────────────────────────────────────────
# (c) refusal: plain box → fm.no_holes


def test_plain_box_refuses_fm_no_holes():
    from build123d import Box

    with pytest.raises(ValueError, match=r"fm\.no_holes"):
        JointCheck().apply(Box(10, 10, 10), {})


# ──────────────────────────────────────────────────────────────────────────────
# (d) all_tapped: no clearance hole anywhere in the stack — unusual, flagged


def test_both_sides_tap_drill_flags_all_tapped():
    comp = _compound(_plate(4.2), _block(4.2))
    r = JointCheck().apply(comp, {})
    ex = r.extras

    assert ex["summary"]["stack_count"] == 1
    (s,) = ex["stacks"]
    assert s["verdict"] == "all_tapped"
    assert s["nominal_guess"] == pytest.approx(5.0)
    assert all(m["class"] == "tap_drill" for m in s["members"])


# ──────────────────────────────────────────────────────────────────────────────
# (e) convex cylinders are NOT bores — coaxial stacked bosses must not stack


def test_stacked_solid_cylinders_are_not_hole_stacks():
    from build123d import Align, Cylinder, Pos

    lower = Cylinder(radius=6, height=10,
                     align=(Align.CENTER, Align.CENTER, Align.MIN))
    upper = Pos(0, 0, 10) * Cylinder(
        radius=6, height=10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    with pytest.raises(ValueError, match=r"fm\.no_holes"):
        JointCheck().apply(_compound(lower, upper), {})


# ──────────────────────────────────────────────────────────────────────────────
# (f) strict-JSON-safe extras


def test_extras_strict_json_safe():
    comp = _compound(_plate(5.5), _block(4.2))
    r = JointCheck().apply(comp, {})
    payload = {k: v for k, v in r.extras.items() if k != "_step_metrics"}
    s = json.dumps(payload, allow_nan=False)  # raises on NaN/inf
    assert "ok_threaded" in s


# ──────────────────────────────────────────────────────────────────────────────
# (g) manifest registration — export_manifest import alone must register it


def test_registered_in_manifest():
    from phone_designer.skills.export_manifest import build_manifest

    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    assert "joint_check" in names
    spec = next(s for s in m["skills"] if s["name"] == "joint_check")
    assert spec["level"] == "atomic"
    assert "fm.no_holes" in spec["failure_modes"]
