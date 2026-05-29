"""Mold-tooling skills — parting_surface, core_cavity_split, draft_apply_auto,
ejector_pin_clearance."""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_mold.core_cavity_split import CoreCavitySplit
from phone_designer.skills.modify_mold.draft_apply_auto import DraftApplyAuto
from phone_designer.skills.modify_mold.ejector_pin_clearance import (
    EjectorPinClearance,
)
from phone_designer.skills.modify_mold.parting_surface import PartingSurface


def _volume(shape_or_part) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    shape = shape_or_part.wrapped if hasattr(shape_or_part, "wrapped") else shape_or_part
    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, p)
    return p.Mass()


def _bbox(shape) -> tuple[float, float, float, float, float, float]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    return bb.Get()


def _make_box(length=40.0, width=30.0, height=20.0):
    return Box().apply(
        None,
        {"length_mm": length, "width_mm": width, "height_mm": height},
    ).body


# ---------------------------------------------------------------- parting_surface


def test_parting_surface_on_box_returns_face_shape():
    body = _make_box()
    r = PartingSurface().apply(
        body,
        {"parting_z_mm": 10.0, "extension_mm": 5.0, "pull_direction": "+Z"},
    )
    # Body unchanged.
    assert _volume(r.body) == pytest.approx(_volume(body), rel=1e-6)
    # extras populated.
    assert "parting_surface_shape" in r.extras
    ps = r.extras["parting_surface_shape"]
    assert ps is not None
    # Parting surface lies in the z=10 plane.
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(ps)
    assert abs(zmin - 10.0) < 1e-3
    assert abs(zmax - 10.0) < 1e-3
    # Surface extends beyond the body in XY.
    assert xmin <= -20.0 - 5.0 + 0.5  # box half-width 20 + extension 5
    assert xmax >= 20.0 + 5.0 - 0.5


def test_parting_surface_extension_grows_outer_rect():
    body = _make_box()
    r_small = PartingSurface().apply(
        body, {"parting_z_mm": 10.0, "extension_mm": 1.0},
    )
    r_large = PartingSurface().apply(
        body, {"parting_z_mm": 10.0, "extension_mm": 30.0},
    )
    xmin_s, _, _, xmax_s, _, _ = _bbox(r_small.extras["parting_surface_shape"])
    xmin_l, _, _, xmax_l, _, _ = _bbox(r_large.extras["parting_surface_shape"])
    assert xmax_l - xmin_l > xmax_s - xmin_s + 20.0


def test_parting_surface_spec_metadata():
    spec = PartingSurface.spec
    assert spec.name == "parting_surface"
    assert spec.category == "modify/mold"
    assert "parting_surface" in spec.produces_features


# -------------------------------------------------------------- core_cavity_split


def test_core_cavity_split_produces_two_valid_solids():
    body = _make_box(40.0, 30.0, 20.0)
    full_vol = _volume(body)  # 40*30*20 = 24000
    r = CoreCavitySplit().apply(
        body,
        {"parting_z_mm": 10.0, "extension_mm": 5.0, "pull_direction": "+Z"},
    )
    core = r.extras["core_solid"]
    cavity = r.extras["cavity_solid"]
    core_v = _volume(core)
    cavity_v = _volume(cavity)
    # Each half ≈ half the box.
    assert core_v == pytest.approx(full_vol / 2.0, rel=1e-3)
    assert cavity_v == pytest.approx(full_vol / 2.0, rel=1e-3)
    # Total ≈ full body.
    assert core_v + cavity_v == pytest.approx(full_vol, rel=1e-3)


def test_core_cavity_split_pull_direction_swaps_halves():
    body = _make_box(40.0, 30.0, 20.0)
    r_pos = CoreCavitySplit().apply(
        body, {"parting_z_mm": 5.0, "pull_direction": "+Z"},
    )
    r_neg = CoreCavitySplit().apply(
        body, {"parting_z_mm": 5.0, "pull_direction": "-Z"},
    )
    # +Z: core is the upper part (z=5..20, vol=15·30·40=18000),
    #      cavity is the lower part (z=0..5, vol= 5·30·40= 6000).
    # -Z: swapped.
    core_pos = _volume(r_pos.extras["core_solid"])
    cav_pos = _volume(r_pos.extras["cavity_solid"])
    core_neg = _volume(r_neg.extras["core_solid"])
    cav_neg = _volume(r_neg.extras["cavity_solid"])
    assert core_pos == pytest.approx(cav_neg, rel=1e-3)
    assert cav_pos == pytest.approx(core_neg, rel=1e-3)
    assert core_pos > cav_pos  # 18000 > 6000


def test_core_cavity_split_body_unchanged():
    body = _make_box()
    v_before = _volume(body)
    r = CoreCavitySplit().apply(body, {"parting_z_mm": 10.0})
    assert _volume(r.body) == pytest.approx(v_before, rel=1e-6)


# -------------------------------------------------------------- draft_apply_auto


def test_draft_apply_auto_tilts_side_faces_by_angle():
    from phone_designer.skills._resolvers import (
        _all_faces, _face_normal_at_center,
    )

    body = _make_box(40.0, 30.0, 20.0)
    r = DraftApplyAuto().apply(
        body,
        {
            "min_draft_deg": 5.0,
            "pull_direction": "+Z",
            "parting_z_mm": 20.0,
        },
    )
    drafted = r.body

    # Each of the 4 side walls should now have a non-zero Z normal component.
    side_tilts = []
    for f in _all_faces(drafted.wrapped):
        n = _face_normal_at_center(f)
        if n == (0.0, 0.0, 0.0):
            continue
        if abs(n[2]) < 0.9:    # side wall, not top/bottom
            tilt_deg = math.degrees(math.asin(abs(n[2])))
            side_tilts.append(tilt_deg)
    assert len(side_tilts) == 4, f"expected 4 side faces, got {len(side_tilts)}"
    for t in side_tilts:
        # Within 0.05° of the commanded angle.
        assert abs(t - 5.0) < 0.05, f"draft tilt {t}° != 5°"


def test_draft_apply_auto_changes_volume():
    body = _make_box(40.0, 30.0, 20.0)
    v_before = _volume(body)
    r = DraftApplyAuto().apply(
        body,
        {"min_draft_deg": 3.0, "pull_direction": "+Z", "parting_z_mm": 20.0},
    )
    v_after = _volume(r.body)
    assert abs(v_after - v_before) > 1.0, (
        f"draft should measurably change volume: {v_before} → {v_after}"
    )


def test_draft_apply_auto_spec_metadata():
    spec = DraftApplyAuto.spec
    assert spec.name == "draft_apply_auto"
    assert spec.category == "modify/mold"


# ----------------------------------------------------------- ejector_pin_clearance


def test_ejector_pin_clearance_drills_n_pins():
    body = _make_box(40.0, 30.0, 20.0)
    v_before = _volume(body)
    pins = [(0.0, 0.0), (10.0, 5.0), (-10.0, -5.0)]
    r = EjectorPinClearance().apply(
        body,
        {
            "pin_positions": pins,
            "pin_diameter_mm": 3.0,
            "pin_depth_mm": 5.0,
            "z_start_mm": 20.0,
        },
    )
    v_after = _volume(r.body)
    expected_per_pin = math.pi * (1.5 ** 2) * 5.0  # ≈ 35.34
    expected_loss = expected_per_pin * len(pins)
    actual_loss = v_before - v_after
    assert abs(actual_loss - expected_loss) / expected_loss < 0.05, (
        f"3 pins → expected loss ≈ {expected_loss:.2f}, got {actual_loss:.2f}"
    )


def test_ejector_pin_clearance_volume_decreases_per_post_condition():
    body = _make_box(40.0, 30.0, 20.0)
    v_before = _volume(body)
    r = EjectorPinClearance().apply(
        body,
        {
            "pin_positions": [(0.0, 0.0)],
            "pin_diameter_mm": 4.0,
            "pin_depth_mm": 8.0,
            "z_start_mm": 20.0,
        },
    )
    assert _volume(r.body) < v_before


def test_ejector_pin_clearance_rejects_empty_positions():
    body = _make_box()
    with pytest.raises(Exception):
        EjectorPinClearance().apply(
            body,
            {
                "pin_positions": [],
                "pin_diameter_mm": 3.0,
                "pin_depth_mm": 5.0,
                "z_start_mm": 20.0,
            },
        )


def test_ejector_pin_clearance_spec_metadata():
    spec = EjectorPinClearance.spec
    assert spec.name == "ejector_pin_clearance"
    assert "ejector_pin_holes" in spec.produces_features
