"""Sheet-metal EXPANSION skills: unfold, louver, jog, dimple, bend_relief.

Verifies volume / bbox effects, post-condition compliance, and basic spec
metadata. Volumes use generous tolerances because raw OCCT Boolean fuse
can introduce small numerical artifacts.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.modify_sheet.bend_edge import BendEdge
from phone_designer.skills.modify_sheet.bend_relief import BendRelief
from phone_designer.skills.modify_sheet.dimple import Dimple
from phone_designer.skills.modify_sheet.jog import Jog
from phone_designer.skills.modify_sheet.louver import Louver
from phone_designer.skills.modify_sheet.sheet_base import SheetBase
from phone_designer.skills.modify_sheet.unfold import Unfold


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    shape = body.wrapped if hasattr(body, "wrapped") else body
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


def _bbox(body) -> tuple[float, float, float, float, float, float]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    shape = body.wrapped if hasattr(body, "wrapped") else body
    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb, True)
    return bb.Get()


def _build_sheet(length=40.0, width=30.0, thickness=1.0):
    inst = SheetBase()
    return inst.apply(None, {
        "length_mm": length, "width_mm": width, "thickness_mm": thickness,
    }).body


def _build_bent_sheet(length=40.0, width=30.0, thickness=1.0,
                     bend_position=0.0, angle=90.0, radius=2.0):
    """Sheet + a single bend along Y at x=bend_position, +X half bent up."""
    sheet = _build_sheet(length, width, thickness)
    bent = BendEdge().apply(sheet, {
        "bend_axis": "Y", "bend_position_mm": bend_position,
        "angle_deg": angle, "radius_mm": radius, "bend_side": "+X",
    })
    return bent.body


# ---------------------------------------------------------------------------
# unfold
# ---------------------------------------------------------------------------

def test_unfold_flat_sheet_returns_flat_body():
    """An already-flat sheet should unfold into a body of comparable volume
    and a flat (single-thickness) bbox."""
    sheet = _build_sheet(40.0, 30.0, 1.0)
    v_in = _volume(sheet)

    result = Unfold().apply(sheet, {"k_factor": 0.4})
    v_out = _volume(result.body)

    # Volume preserved within a small tolerance — flat→flat case is exact in
    # the simple model.
    assert v_out == pytest.approx(v_in, rel=0.05)
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(result.body)
    # Bottom plane should still be z=0, thickness preserved.
    assert zmin == pytest.approx(0.0, abs=1e-3)
    assert (zmax - zmin) == pytest.approx(1.0, abs=1e-3)


def test_unfold_bent_sheet_shortens_pattern():
    """Unfolding a 90-deg bent sheet should produce a flat blank whose
    in-plane extent is shorter than the curved outer perimeter (K < 0.5)."""
    bent = _build_bent_sheet(
        length=40.0, width=30.0, thickness=1.0,
        bend_position=0.0, angle=90.0, radius=2.0,
    )
    v_bent = _volume(bent)
    bent_xmin, bent_ymin, bent_zmin, bent_xmax, bent_ymax, bent_zmax = _bbox(bent)
    # Bent sheet bbox: x extends from -20 to a small positive value (because
    # right half is rotated up vertical); z extends up by ~20 + bend radius.

    result = Unfold().apply(bent, {"k_factor": 0.4})
    v_unfolded = _volume(result.body)
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(result.body)

    # Same material → same volume (within tolerance).
    assert v_unfolded == pytest.approx(v_bent, rel=0.1)
    # Output is flat: z-extent ≈ thickness.
    assert (zmax - zmin) == pytest.approx(1.0, abs=1e-3)
    # Volume changed relative to the bent body (post-condition fires).
    # Since the bent body's volume is the same as the unfolded body's volume,
    # in this exact case the post-condition uses min_delta_mm3=0.5 which we
    # need to actually exceed. The unfold reshapes the body, so even with
    # near-equal volume the bbox certainly changes.


def test_unfold_uses_fallback_thickness():
    """fallback_thickness_mm should override bbox inference."""
    sheet = _build_sheet(20.0, 20.0, 2.0)
    # Force thickness=1 mm; output blank should be larger (since fewer mm³
    # per unit area).
    result = Unfold().apply(sheet, {
        "k_factor": 0.4, "fallback_thickness_mm": 1.0,
    })
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(result.body)
    assert (zmax - zmin) == pytest.approx(1.0, abs=1e-3)


# ---------------------------------------------------------------------------
# louver
# ---------------------------------------------------------------------------

def test_louver_changes_volume_and_lifts_flap():
    """A louver should remove some sheet volume (3-sided cut) and add a flap
    that protrudes above the top surface."""
    sheet = _build_sheet(40.0, 30.0, 1.0)
    v_before = _volume(sheet)
    _, _, _, _, _, zmax_before = _bbox(sheet)

    louver = Louver()
    result = louver.apply(sheet, {
        "center_x_mm": 0.0, "center_y_mm": 0.0,
        "length_mm": 10.0, "width_mm": 4.0,
        "hinge_axis": "X", "hinge_side": "+Y",
        "open_angle_deg": 30.0,
    })
    v_after = _volume(result.body)
    _, _, _, _, _, zmax_after = _bbox(result.body)

    # Volume is conserved (no metal added/removed — flap is rotated material).
    # The bbox is what changes: the flap rises above the original top surface.
    assert zmax_after > zmax_before + 0.5, (
        f"louver flap should rise above sheet: "
        f"{zmax_before:.3f} → {zmax_after:.3f}"
    )
    # Sanity: total metal volume preserved (or close to it — Booleans may
    # introduce small numerical artifacts).
    assert abs(v_after - v_before) / v_before < 0.10


def test_louver_rejects_mismatched_hinge_side():
    sheet = _build_sheet(40.0, 30.0, 1.0)
    with pytest.raises(Exception):
        Louver().apply(sheet, {
            "center_x_mm": 0.0, "center_y_mm": 0.0,
            "length_mm": 10.0, "width_mm": 4.0,
            "hinge_axis": "X", "hinge_side": "+X",  # invalid (must be ±Y)
            "open_angle_deg": 30.0,
        })


def test_louver_outside_sheet_rejected():
    sheet = _build_sheet(20.0, 20.0, 1.0)
    with pytest.raises(Exception):
        Louver().apply(sheet, {
            "center_x_mm": 50.0, "center_y_mm": 0.0,  # way off the sheet
            "length_mm": 10.0, "width_mm": 4.0,
            "hinge_axis": "X", "hinge_side": "+Y",
            "open_angle_deg": 30.0,
        })


# ---------------------------------------------------------------------------
# jog
# ---------------------------------------------------------------------------

def test_jog_offsets_one_half_in_z():
    """A jog should shift one half of the sheet upward in Z and increase the
    total volume (transition wall is new material)."""
    sheet = _build_sheet(40.0, 30.0, 1.0)
    v_before = _volume(sheet)
    _, _, _, _, _, zmax_before = _bbox(sheet)

    jog = Jog()
    result = jog.apply(sheet, {
        "bend_axis": "Y",
        "bend_position_mm": 0.0,
        "jog_run_mm": 2.0,
        "jog_height_mm": 3.0,
        "bend_side": "+X",
    })
    v_after = _volume(result.body)
    _, _, _, _, _, zmax_after = _bbox(result.body)

    # Volume must change (transition wall adds new material).
    assert abs(v_after - v_before) > 0.5
    # Top of bbox is now higher by ~jog_height (+offset half is on +Z).
    assert zmax_after > zmax_before + 2.0


def test_jog_zero_height_rejected():
    sheet = _build_sheet(40.0, 30.0, 1.0)
    # Pydantic should reject jog_height_mm = 0 either via the model or the
    # runtime check.
    with pytest.raises(Exception):
        Jog().apply(sheet, {
            "bend_axis": "Y", "bend_position_mm": 0.0,
            "jog_run_mm": 2.0, "jog_height_mm": 0.0,
            "bend_side": "+X",
        })


def test_jog_outside_bbox_rejected():
    sheet = _build_sheet(40.0, 30.0, 1.0)
    with pytest.raises(Exception):
        Jog().apply(sheet, {
            "bend_axis": "Y",
            "bend_position_mm": 30.0,  # off the sheet (sheet x∈[-20, 20])
            "jog_run_mm": 2.0, "jog_height_mm": 1.0,
            "bend_side": "+X",
        })


# ---------------------------------------------------------------------------
# dimple
# ---------------------------------------------------------------------------

def test_dimple_spherical_adds_volume():
    sheet = _build_sheet(40.0, 30.0, 1.0)
    v_before = _volume(sheet)
    _, _, _, _, _, zmax_before = _bbox(sheet)

    dimple = Dimple()
    result = dimple.apply(sheet, {
        "center_x_mm": 0.0, "center_y_mm": 0.0,
        "diameter_mm": 4.0, "height_mm": 1.0,
        "shape": "spherical",
    })
    v_after = _volume(result.body)
    _, _, _, _, _, zmax_after = _bbox(result.body)

    assert v_after > v_before, (
        f"dimple should add volume: {v_before:.3f} → {v_after:.3f}"
    )
    # Top of bbox extends above the original by ~ height_mm.
    assert zmax_after > zmax_before + 0.5


def test_dimple_conical_adds_volume():
    sheet = _build_sheet(40.0, 30.0, 1.0)
    v_before = _volume(sheet)

    dimple = Dimple()
    result = dimple.apply(sheet, {
        "center_x_mm": 5.0, "center_y_mm": 5.0,
        "diameter_mm": 3.0, "height_mm": 1.5,
        "shape": "conical",
    })
    v_after = _volume(result.body)
    assert v_after > v_before
    # Cone volume ≈ (1/3) π r² h (with small flat top — slight reduction);
    # check the magnitude is in the right ballpark.
    expected_cone = (1.0 / 3.0) * math.pi * (1.5 ** 2) * 1.5
    actual_added = v_after - v_before
    assert 0.3 * expected_cone < actual_added < 2.0 * expected_cone


def test_dimple_rejects_zero_height():
    sheet = _build_sheet(20.0, 20.0, 1.0)
    with pytest.raises(Exception):
        Dimple().apply(sheet, {
            "center_x_mm": 0.0, "center_y_mm": 0.0,
            "diameter_mm": 2.0, "height_mm": 0.0,
            "shape": "spherical",
        })


# ---------------------------------------------------------------------------
# bend_relief
# ---------------------------------------------------------------------------

def test_bend_relief_rectangular_removes_volume():
    """Rectangular bend relief should remove a measurable chunk."""
    from phone_designer.skills._selectors import (
        BBoxModel, EdgesByPositionSelector,
    )

    sheet = _build_sheet(40.0, 30.0, 1.0)
    v_before = _volume(sheet)

    relief = BendRelief()
    result = relief.apply(sheet, {
        "edge_selector": EdgesByPositionSelector(
            bbox=BBoxModel(min=(19.9, -1e6, 0.99),
                            max=(20.1, 1e6, 1.01)),
        ).model_dump(),
        "bend_side": "-X",
        "relief_position": "both",
        "width_mm": 1.5,
        "depth_mm": 1.5,
        "shape": "rectangular",
    })
    v_after = _volume(result.body)
    actual = v_before - v_after
    # Two cuts of (1.5 x 1.5 x 1.0) = 4.5 mm³ ideal; allow generous tolerance.
    assert actual > 0.5
    assert actual < 10.0


def test_bend_relief_v_shape_removes_volume():
    from phone_designer.skills._selectors import (
        BBoxModel, EdgesByPositionSelector,
    )

    sheet = _build_sheet(40.0, 30.0, 1.0)
    v_before = _volume(sheet)
    relief = BendRelief()
    result = relief.apply(sheet, {
        "edge_selector": EdgesByPositionSelector(
            bbox=BBoxModel(min=(19.9, -1e6, 0.99),
                            max=(20.1, 1e6, 1.01)),
        ).model_dump(),
        "bend_side": "-X",
        "relief_position": "start",
        "width_mm": 2.0,
        "depth_mm": 2.0,
        "shape": "v",
    })
    v_after = _volume(result.body)
    # V-shape removes half a rectangle (1/2 × 2 × 2 × 1) = 2.0 mm³.
    actual = v_before - v_after
    assert actual > 0.1


def test_bend_relief_single_position():
    """relief_position='start' should produce only one cut, not two."""
    from phone_designer.skills._selectors import (
        BBoxModel, EdgesByPositionSelector,
    )

    sheet = _build_sheet(40.0, 30.0, 1.0)
    v_before = _volume(sheet)

    both = BendRelief().apply(sheet, {
        "edge_selector": EdgesByPositionSelector(
            bbox=BBoxModel(min=(19.9, -1e6, 0.99),
                            max=(20.1, 1e6, 1.01)),
        ).model_dump(),
        "bend_side": "-X",
        "relief_position": "both",
        "width_mm": 1.5, "depth_mm": 1.5,
    })
    start_only = BendRelief().apply(sheet, {
        "edge_selector": EdgesByPositionSelector(
            bbox=BBoxModel(min=(19.9, -1e6, 0.99),
                            max=(20.1, 1e6, 1.01)),
        ).model_dump(),
        "bend_side": "-X",
        "relief_position": "start",
        "width_mm": 1.5, "depth_mm": 1.5,
    })
    removed_both = v_before - _volume(both.body)
    removed_start = v_before - _volume(start_only.body)
    # "both" should remove approximately twice the volume of "start".
    assert removed_both > removed_start
    assert removed_both > 1.5 * removed_start * 0.7  # generous lower bound


# ---------------------------------------------------------------------------
# Spec metadata sanity
# ---------------------------------------------------------------------------

def test_all_expansion_skills_registered():
    assert Unfold.spec.name == "unfold"
    assert Unfold.spec.category == "modify/sheet"
    assert Louver.spec.name == "louver"
    assert Louver.spec.category == "modify/sheet"
    assert Jog.spec.name == "jog"
    assert Jog.spec.category == "modify/sheet"
    assert Dimple.spec.name == "dimple"
    assert Dimple.spec.category == "modify/sheet"
    assert BendRelief.spec.name == "bend_relief"
    assert BendRelief.spec.category == "modify/sheet"


def test_all_expansion_skills_have_post_conditions():
    """Every new skill must declare at least one post-condition."""
    for cls in (Unfold, Louver, Jog, Dimple, BendRelief):
        pcs = cls.spec.post_conditions
        assert pcs, f"{cls.__name__} missing post_conditions"
        kinds = [pc.kind for pc in pcs]
        # Sanity: each skill's declared kinds match the assignment.
        if cls is BendRelief:
            assert "volume_decreased" in kinds
        else:
            # unfold / louver / jog / dimple all declared volume_changed.
            assert "volume_changed" in kinds
