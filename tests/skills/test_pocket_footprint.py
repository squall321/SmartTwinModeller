"""Pocket footprint capture + footprint-true emission - plan items A10/P9
(COMPLEX-CAD pass-26, 2026-06-11).

Contracts under test:

  (a) classify_pockets emits the new SIBLING fields on an authored slab:
      footprint_kind (circular / rectangular / slot) with width/length
      within 2 % of the authored dims, pocket_entry_origin on the TOP
      entry plane, pocket_entry_depth_mm = authored depth.
  (b) axis_origin is UNTOUCHED - it still follows the documented
      conventions (floor centroid for planar-floor pockets; mean cylinder
      axis location in-plane for cylinder-walled pockets). pass-18 was
      reverted for mutating it; pass-26 must only ADD siblings.
  (c) extrude_pocket_world back-compat - calling without the new args
      produces the EXACT legacy rectangular prism volume; kind='rect',
      angle_deg=0.0 defaults are byte-equivalent.
  (d) the new footprint kinds cut analytically correct volumes
      (circular = pi r^2 d, slot = (L-W)*W*d + pi (W/2)^2 d) and
      angle_deg rotates the rect tool in-plane.
  (e) _pocket_step wiring is BOX MODE ONLY: with shift == identity the
      emitted step is byte-identical whether or not the pocket carries
      footprint fields (preserve_brep hard constraint); with a box-mode
      shift the footprint-true extrude_pocket_world step is emitted at the
      entry anchor.

NAMING: the pocket entry fields are pocket-prefixed (pocket_entry_origin,
pocket_entry_depth_mm) - NOT the holes' literal entry_origin - because
feature_fidelity_diff._xyz_of prefers the exact key 'entry_origin' for
spatial pairing and pocket entries must never shift the pairing identity
away from the immutable floor-centroid axis_origin (first pass-26
spot-check round regressed as1_pe_203 pb 1.0 -> 0.774 over exactly this).
"""
from __future__ import annotations

import math

import pytest


# -----------------------------------------------------------------------------
# Helpers


def _volume_mm3(shape_or_part) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    shape = (
        shape_or_part.wrapped
        if hasattr(shape_or_part, "wrapped")
        else shape_or_part
    )
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


def _bbox(shape) -> tuple[float, float, float, float, float, float]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, bb)
    return bb.Get()


# Authored slab: 80 x 40 x 10 (z -5..+5) with three pockets sunk 4 mm into
# the TOP face (entry plane z = +5, floors at z = +1):
#   circular  D8             centred at x = -25
#   rect      6 (Y) x 14 (X) centred at x = 0
#   slot      6 x 20, R3 ends, long axis X, centred at x = +25
_SLAB_L, _SLAB_W, _SLAB_H = 80.0, 40.0, 10.0
_DEPTH = 4.0
_TOP_Z = +5.0
_FLOOR_Z = +1.0
_CIRC_X, _RECT_X, _SLOT_X = -25.0, 0.0, 25.0


@pytest.fixture(scope="module")
def slab_with_pockets():
    from build123d import Box, Cylinder, Pos

    slab = Box(_SLAB_L, _SLAB_W, _SLAB_H)
    # circular D8, depth 4 (z 1..5)
    slab -= Pos(_CIRC_X, 0, 3) * Cylinder(4.0, _DEPTH)
    # rect 14 (X) x 6 (Y), depth 4
    slab -= Pos(_RECT_X, 0, 3) * Box(14.0, 6.0, _DEPTH)
    # slot 6 x 20 with R3 caps: 14 x 6 rect + D6 caps at x = +/-7
    slot = (
        Box(14.0, 6.0, _DEPTH)
        + Pos(-7.0, 0, 0) * Cylinder(3.0, _DEPTH)
        + Pos(+7.0, 0, 0) * Cylinder(3.0, _DEPTH)
    )
    slab -= Pos(_SLOT_X, 0, 3) * slot
    return slab


@pytest.fixture(scope="module")
def pockets(slab_with_pockets):
    from phone_designer.skills.inspect.classify_pockets import ClassifyPockets

    res = ClassifyPockets().apply(slab_with_pockets, {})
    pl = res.extras["pockets"]
    assert isinstance(pl, list), f"pocket detection skipped: {pl}"
    return pl


def _pocket_near_x(pockets: list, x: float) -> dict:
    def _px(p):
        src = (
            p.get("pocket_entry_origin")
            or p.get("axis_origin")
            or [1e9, 1e9, 1e9]
        )
        return float(src[0])

    best = min(pockets, key=lambda p: abs(_px(p) - x))
    assert abs(_px(best) - x) < 2.0, (
        f"no pocket near x={x}: positions={[_px(p) for p in pockets]}"
    )
    return best


def _rel_err(measured: float, authored: float) -> float:
    return abs(float(measured) - authored) / authored


# -----------------------------------------------------------------------------
# (a) footprint classification on the authored slab


def test_three_pockets_detected(pockets):
    assert len(pockets) == 3, (
        f"expected 3 authored pockets, got {len(pockets)}: "
        f"{[(p.get('footprint_kind'), p.get('top_d_mm')) for p in pockets]}"
    )


def test_circular_footprint(pockets):
    p = _pocket_near_x(pockets, _CIRC_X)
    assert p["footprint_kind"] == "circular"
    assert _rel_err(p["footprint_width_mm"], 8.0) <= 0.02
    assert _rel_err(p["footprint_length_mm"], 8.0) <= 0.02


def test_rect_footprint(pockets):
    p = _pocket_near_x(pockets, _RECT_X)
    assert p["footprint_kind"] == "rectangular"
    assert _rel_err(p["footprint_width_mm"], 6.0) <= 0.02
    assert _rel_err(p["footprint_length_mm"], 14.0) <= 0.02
    # long axis is world X and the canonical pocket axis frame's u == X.
    assert abs(p["footprint_angle_deg"]) < 1.0 or (
        abs(abs(p["footprint_angle_deg"]) - 180.0) < 1.0
    )


def test_slot_footprint(pockets):
    p = _pocket_near_x(pockets, _SLOT_X)
    assert p["footprint_kind"] == "slot"
    assert _rel_err(p["footprint_width_mm"], 6.0) <= 0.02
    assert _rel_err(p["footprint_length_mm"], 20.0) <= 0.02
    assert abs(p["footprint_angle_deg"]) < 1.0 or (
        abs(abs(p["footprint_angle_deg"]) - 180.0) < 1.0
    )


def test_entry_anchor_on_top_plane(pockets):
    for x in (_CIRC_X, _RECT_X, _SLOT_X):
        p = _pocket_near_x(pockets, x)
        eo = p["pocket_entry_origin"]
        assert eo is not None, f"pocket near x={x} has no pocket_entry_origin"
        assert abs(eo[0] - x) < 0.1
        assert abs(eo[1] - 0.0) < 0.1
        assert abs(eo[2] - _TOP_Z) < 0.1, (
            f"pocket_entry_origin z={eo[2]} != top plane {_TOP_Z}"
        )
        assert abs(p["pocket_entry_depth_mm"] - _DEPTH) < 0.05


def test_no_unprefixed_entry_keys_on_pockets(pockets):
    """The diff-coupling guard: pockets must NEVER emit the holes' literal
    entry_origin/entry_depth_mm keys (feature_fidelity_diff._xyz_of would
    pair on them - the pass-26 pb/box regression)."""
    for p in pockets:
        assert "entry_origin" not in p
        assert "entry_depth_mm" not in p


# -----------------------------------------------------------------------------
# (b) axis_origin IMMUTABILITY - documented conventions still hold


def test_axis_origin_is_floor_centroid_for_rect(pockets):
    """Planar-floor pockets: axis_origin == floor centroid (the convention
    preserve_brep self-match round-trips - pass-18 revert lesson)."""
    p = _pocket_near_x(pockets, _RECT_X)
    ao = p["axis_origin"]
    assert abs(ao[0] - _RECT_X) < 1e-3
    assert abs(ao[1] - 0.0) < 1e-3
    assert abs(ao[2] - _FLOOR_Z) < 1e-3, (
        f"axis_origin z={ao[2]} moved off the floor centroid ({_FLOOR_Z}) - "
        "pass-26 must only ADD sibling fields"
    )


def test_axis_origin_inplane_for_cylinder_walled(pockets):
    """Cylinder-walled pockets: axis_origin in-plane = mean cylinder axis
    location (unchanged convention; axial position is OCCT-parametric)."""
    p = _pocket_near_x(pockets, _CIRC_X)
    assert abs(p["axis_origin"][0] - _CIRC_X) < 1e-3
    assert abs(p["axis_origin"][1] - 0.0) < 1e-3
    p = _pocket_near_x(pockets, _SLOT_X)
    assert abs(p["axis_origin"][0] - _SLOT_X) < 1e-3
    assert abs(p["axis_origin"][1] - 0.0) < 1e-3


# -----------------------------------------------------------------------------
# (c) extrude_pocket_world back-compat


def test_world_prism_legacy_call_volume_exact():
    from phone_designer.skills.modify_pocket.extrude_pocket_world import (
        _build_world_prism,
    )

    legacy = _build_world_prism((0, 0, 0), (0, 0, 1), 14.0, 6.0, 4.0)
    assert abs(_volume_mm3(legacy) - 14.0 * 6.0 * 4.0) < 1e-6

    defaulted = _build_world_prism(
        (0, 0, 0), (0, 0, 1), 14.0, 6.0, 4.0,
        direction="into", kind="rect", angle_deg=0.0,
    )
    assert _volume_mm3(defaulted) == _volume_mm3(legacy)
    assert _bbox(defaulted) == _bbox(legacy)


def test_skill_apply_without_new_args_old_volume(slab_with_pockets):
    """No new args in the Args dict => the cut removes exactly the legacy
    rectangular prism volume (old plan YAMLs re-execute unchanged)."""
    from phone_designer.skills.modify_pocket.extrude_pocket_world import (
        ExtrudePocketWorld,
    )

    v0 = _volume_mm3(slab_with_pockets)
    res = ExtrudePocketWorld().apply(slab_with_pockets, {
        "world_origin": [-25.0, 10.0, 5.0],
        "axis_dir": [0.0, 0.0, 1.0],
        "length_mm": 5.0,
        "width_mm": 4.0,
        "depth_mm": 2.0,
        "direction": "into",
    })
    removed = v0 - _volume_mm3(res.body)
    assert abs(removed - 5.0 * 4.0 * 2.0) < 1e-6


# -----------------------------------------------------------------------------
# (d) new footprint kinds - analytic tool volumes + angle behaviour


def test_circular_tool_volume():
    from phone_designer.skills.modify_pocket.extrude_pocket_world import (
        _build_world_prism,
    )

    tool = _build_world_prism(
        (0, 0, 0), (0, 0, 1), 8.0, 8.0, 4.0, kind="circular",
    )
    assert abs(_volume_mm3(tool) - math.pi * 16.0 * 4.0) < 1e-4


def test_slot_tool_volume():
    from phone_designer.skills.modify_pocket.extrude_pocket_world import (
        _build_world_prism,
    )

    tool = _build_world_prism(
        (0, 0, 0), (0, 0, 1), 20.0, 6.0, 4.0, kind="slot",
    )
    expected = (20.0 - 6.0) * 6.0 * 4.0 + math.pi * 9.0 * 4.0
    assert abs(_volume_mm3(tool) - expected) < 1e-3


def test_rect_angle_rotates_inplane():
    from phone_designer.skills.modify_pocket.extrude_pocket_world import (
        _build_world_prism,
    )

    straight = _build_world_prism((0, 0, 0), (0, 0, 1), 14.0, 6.0, 4.0)
    rotated = _build_world_prism(
        (0, 0, 0), (0, 0, 1), 14.0, 6.0, 4.0, kind="rect", angle_deg=90.0,
    )
    assert abs(_volume_mm3(rotated) - _volume_mm3(straight)) < 1e-6
    bs = _bbox(straight)
    br = _bbox(rotated)
    # 90 deg about Z: X/Y extents swap.
    assert abs((bs[3] - bs[0]) - (br[4] - br[1])) < 1e-6
    assert abs((bs[4] - bs[1]) - (br[3] - br[0])) < 1e-6


# -----------------------------------------------------------------------------
# (e) planner wiring - BOX MODE ONLY


_FOOTPRINT_POCKET = {
    "id": 0,
    "type": "blind",
    "axis_origin": [0.0, 0.0, 1.0],
    "axis_dir": [0.0, 0.0, -1.0],
    "top_d_mm": 14.0,
    "bottom_d_mm": 14.0,
    "depth_mm": 4.0,
    "face_indices": [1, 2, 3, 4, 5],
    "footprint_kind": "rectangular",
    "footprint_width_mm": 6.0,
    "footprint_length_mm": 14.0,
    "footprint_angle_deg": 0.0,
    "pocket_entry_origin": [0.0, 0.0, 5.0],
    "pocket_entry_depth_mm": 4.0,
}

_BBOX = (-40.0, -20.0, -5.0, 40.0, 20.0, 5.0)


def test_preserve_brep_emission_ignores_footprint_fields():
    """shift == identity (preserve_brep / import_step): the step must be
    byte-identical whether or not the sibling fields are present."""
    from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
        _pocket_step,
    )

    legacy_pocket = {
        k: v for k, v in _FOOTPRINT_POCKET.items()
        if not k.startswith(("footprint_", "pocket_entry_"))
    }
    with_fp = _pocket_step(0, dict(_FOOTPRINT_POCKET), bbox=_BBOX,
                           shift=(0.0, 0.0, 0.0))
    without_fp = _pocket_step(0, legacy_pocket, bbox=_BBOX,
                              shift=(0.0, 0.0, 0.0))
    assert with_fp == without_fp


def test_box_mode_emits_footprint_true_step():
    from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
        _pocket_step,
    )

    shift = (0.0, 0.0, 5.0)  # world -> box-local (zmin = -5)
    step = _pocket_step(0, dict(_FOOTPRINT_POCKET), bbox=_BBOX, shift=shift)
    assert step["skill"] == "extrude_pocket_world"
    args = step["args"]
    assert args["kind"] == "rect"
    assert args["length_mm"] == 14.0
    assert args["width_mm"] == 6.0
    assert args["depth_mm"] == 4.0
    assert args["direction"] == "into"
    # anchored at the ENTRY plane (shifted into box-local coords)
    assert args["world_origin"] == [0.0, 0.0, 10.0]
    # axis_dir points OUT of the body (floor -> entry)
    assert args["axis_dir"] == [0.0, 0.0, 1.0]


def test_box_mode_circular_footprint_uses_circular_kind():
    from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
        _pocket_step,
    )

    pocket = dict(_FOOTPRINT_POCKET)
    pocket.update({
        "footprint_kind": "circular",
        "footprint_width_mm": 8.0,
        "footprint_length_mm": 8.0,
        "top_d_mm": 8.0,
    })
    step = _pocket_step(0, pocket, bbox=_BBOX, shift=(0.0, 0.0, 5.0))
    assert step["skill"] == "extrude_pocket_world"
    assert step["args"]["kind"] == "circular"
    assert step["args"]["width_mm"] == 8.0


def test_box_mode_freeform_falls_back_to_legacy_proxy():
    from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
        _pocket_step,
    )

    pocket = dict(_FOOTPRINT_POCKET)
    pocket["footprint_kind"] = "freeform"
    pocket["footprint_width_mm"] = None
    pocket["footprint_length_mm"] = None
    step = _pocket_step(0, pocket, bbox=_BBOX, shift=(0.0, 0.0, 5.0))
    assert step["skill"] == "extrude_pocket_world"
    # legacy proxy: square top_d x top_d at the axis_origin, no kind arg.
    assert "kind" not in step["args"]
    assert step["args"]["world_origin"] == [0.0, 0.0, 6.0]


# -----------------------------------------------------------------------------
# end-to-end: classify -> plan (box) -> footprint-true steps for all three


def test_authored_slab_box_plan_uses_true_footprints(slab_with_pockets, pockets):
    from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
        _pocket_step, _world_to_box_shift,
    )

    bbox = (-40.0, -20.0, -5.0, 40.0, 20.0, 5.0)
    shift = _world_to_box_shift(bbox)
    kinds = {}
    for i, p in enumerate(pockets):
        step = _pocket_step(i, p, bbox=bbox, shift=shift)
        key = round(float((p.get("pocket_entry_origin") or p["axis_origin"])[0]))
        kinds[key] = (step["skill"], step["args"].get("kind"))
    assert kinds[round(_CIRC_X)] == ("extrude_pocket_world", "circular")
    assert kinds[round(_RECT_X)] == ("extrude_pocket_world", "rect")
    assert kinds[round(_SLOT_X)] == ("extrude_pocket_world", "slot")
