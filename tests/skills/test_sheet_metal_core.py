"""Sheet-metal CORE skills: sheet_base, bend_edge, flange, hem, tab, slot.

Each skill is exercised with at least two test cases covering nominal use and
post-condition compliance. Volumes / bboxes are checked rather than topology
specifics, because OCCT Boolean fuse may yield different internal face counts
across versions.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.modify_sheet.sheet_base import SheetBase
from phone_designer.skills.modify_sheet.bend_edge import BendEdge
from phone_designer.skills.modify_sheet.flange import Flange
from phone_designer.skills.modify_sheet.hem import Hem
from phone_designer.skills.modify_sheet.tab_slot import Tab, Slot


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


# ---------------------------------------------------------------------------
# sheet_base
# ---------------------------------------------------------------------------

def test_sheet_base_volume_and_bbox():
    inst = SheetBase()
    result = inst.apply(None, {
        "length_mm": 40.0, "width_mm": 30.0, "thickness_mm": 1.0,
    })
    v = _volume(result.body)
    assert v == pytest.approx(40 * 30 * 1, rel=1e-3)
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(result.body)
    assert xmin == pytest.approx(-20.0, abs=1e-3)
    assert xmax == pytest.approx(20.0, abs=1e-3)
    assert ymin == pytest.approx(-15.0, abs=1e-3)
    assert ymax == pytest.approx(15.0, abs=1e-3)
    assert zmin == pytest.approx(0.0, abs=1e-3)
    assert zmax == pytest.approx(1.0, abs=1e-3)


def test_sheet_base_with_center_offset():
    inst = SheetBase()
    result = inst.apply(None, {
        "length_mm": 10.0, "width_mm": 10.0, "thickness_mm": 2.0,
        "center_x_mm": 5.0, "center_z_mm": 3.0,
    })
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(result.body)
    assert xmin == pytest.approx(0.0, abs=1e-3)
    assert xmax == pytest.approx(10.0, abs=1e-3)
    assert zmin == pytest.approx(3.0, abs=1e-3)
    assert zmax == pytest.approx(5.0, abs=1e-3)


def test_sheet_base_rejects_zero_thickness():
    inst = SheetBase()
    with pytest.raises(Exception):
        inst.apply(None, {
            "length_mm": 10.0, "width_mm": 10.0, "thickness_mm": 0.0,
        })


# ---------------------------------------------------------------------------
# bend_edge — bend right half of a sheet (centered) about a Y-axis edge at x=0
# ---------------------------------------------------------------------------

def _top_edge_selector_at_x(x_value: float):
    """SelectorRef matching the Y-direction edge on the top face whose mid X
    is approximately x_value. Use edges_by_position with a tight bbox at the
    top of the sheet around x_value."""
    from phone_designer.skills._selectors import EdgesByPositionSelector, BBoxModel

    return EdgesByPositionSelector(
        bbox=BBoxModel(
            min=(x_value - 0.01, -1e6, 0.99),
            max=(x_value + 0.01, +1e6, 1.01),
        ),
    )


def _top_edge_selector_axis_pos_z(t: float):
    """Selector matching Y-axis edges located on the top surface (z = t).

    edges_by_position with bbox covering only z ≈ t."""
    from phone_designer.skills._selectors import (
        EdgesByPositionSelector, BBoxModel, AxisAlignedEdgesSelector, AndSel,
    )
    return AndSel(
        left=AxisAlignedEdgesSelector(axis="Y"),
        right=EdgesByPositionSelector(
            bbox=BBoxModel(min=(-1e6, -1e6, t - 0.01),
                            max=(1e6, 1e6, t + 0.01)),
        ),
    )


def _build_sheet(length=40.0, width=30.0, thickness=1.0):
    inst = SheetBase()
    return inst.apply(None, {
        "length_mm": length, "width_mm": width, "thickness_mm": thickness,
    }).body


def test_bend_edge_90deg_changes_volume_and_bbox():
    sheet = _build_sheet(40, 30, 1.0)
    v_before = _volume(sheet)
    _, _, _, _, _, zmax_before = _bbox(sheet)

    # Bend at interior position x=0 (sheet centered at origin, so x=0 is the
    # midline). Bend +X half upward by 90 deg with r=2.
    bend = BendEdge()
    result = bend.apply(sheet, {
        "bend_axis": "Y",
        "bend_position_mm": 0.0,
        "angle_deg": 90.0,
        "radius_mm": 2.0,
        "bend_side": "+X",
    })
    v_after = _volume(result.body)
    _, _, _, _, _, zmax_after = _bbox(result.body)

    # Volume should increase by the arc (toroidal sector) added between halves.
    # Approximate arc volume: angle * width * t * (r + t/2) = (π/2)*30*1*2.5 ≈ 117.8
    expected_arc_volume = (math.pi / 2.0) * 30.0 * 1.0 * 2.5
    actual_added = v_after - v_before
    # Wide tolerance — Booleans may produce small interpenetrations.
    assert actual_added > 0.0, f"bend should add arc volume: delta={actual_added}"
    assert abs(actual_added - expected_arc_volume) / expected_arc_volume < 0.35, (
        f"bend arc volume off: expected ≈ {expected_arc_volume:.1f}, "
        f"got {actual_added:.1f}"
    )
    # bent +X half is now vertical → bbox z extends well above original.
    assert zmax_after > zmax_before + 10.0, (
        f"after 90-deg bend, top should rise: {zmax_before} → {zmax_after}"
    )


def test_bend_edge_invalid_bend_side_raises():
    sheet = _build_sheet(20, 20, 1.0)
    bend = BendEdge()
    # bend_axis="Y" but bend_side="+Y" — bend_side must be perpendicular.
    with pytest.raises(Exception):
        bend.apply(sheet, {
            "bend_axis": "Y",
            "bend_position_mm": 0.0,
            "angle_deg": 90.0,
            "radius_mm": 1.0,
            "bend_side": "+Y",
        })


def test_bend_edge_45deg_smaller_change_than_90deg():
    """Sanity: a 45-deg bend should add less volume than a 90-deg bend of
    the same radius/sheet."""
    sheet = _build_sheet(40, 30, 1.0)
    v0 = _volume(sheet)
    b45 = BendEdge().apply(sheet, {
        "bend_axis": "Y", "bend_position_mm": 0.0,
        "angle_deg": 45.0, "radius_mm": 2.0, "bend_side": "+X",
    })
    b90 = BendEdge().apply(sheet, {
        "bend_axis": "Y", "bend_position_mm": 0.0,
        "angle_deg": 90.0, "radius_mm": 2.0, "bend_side": "+X",
    })
    add_45 = _volume(b45.body) - v0
    add_90 = _volume(b90.body) - v0
    assert add_45 > 0
    assert add_90 > add_45


# ---------------------------------------------------------------------------
# flange
# ---------------------------------------------------------------------------

def test_flange_adds_volume_and_extends_bbox():
    from phone_designer.skills._selectors import (
        EdgesByPositionSelector, BBoxModel,
    )

    sheet = _build_sheet(40, 30, 1.0)
    v_before = _volume(sheet)
    _, _, _, xmax_before, _, zmax_before = _bbox(sheet)

    flange = Flange()
    # +X boundary edge at x=20, top face (z=1). Bend up 90 deg with r=2,
    # flange length 10 mm.
    result = flange.apply(sheet, {
        "edge_selector": EdgesByPositionSelector(
            bbox=BBoxModel(min=(19.9, -1e6, 0.99),
                            max=(20.1, 1e6, 1.01)),
        ).model_dump(),
        "angle_deg": 90.0,
        "radius_mm": 2.0,
        "bend_side": "+X",
        "flange_length_mm": 10.0,
    })
    v_after = _volume(result.body)
    assert v_after > v_before, f"flange should add material: {v_before} → {v_after}"

    # Approximate added volume: arc (toroidal sector for 90 deg, inner r=2,
    # thickness 1, width 30) + flat strip (10 x 30 x 1 = 300).
    # Arc volume ≈ angle * width * t * (r + t/2) = (π/2) * 30 * 1 * 2.5 ≈ 117.8.
    expected_arc = (math.pi / 2.0) * 30.0 * 1.0 * (2.0 + 0.5)
    expected_flat = 10.0 * 30.0 * 1.0
    expected_added = expected_arc + expected_flat
    actual_added = v_after - v_before
    # Allow generous tolerance — Boolean fusion may introduce small overlaps.
    assert abs(actual_added - expected_added) / expected_added < 0.25, (
        f"flange volume off: expected ≈ {expected_added:.1f}, "
        f"got {actual_added:.1f}"
    )


def test_flange_zero_length_rejected():
    from phone_designer.skills._selectors import (
        EdgesByPositionSelector, BBoxModel,
    )
    sheet = _build_sheet(20, 20, 1.0)
    flange = Flange()
    with pytest.raises(Exception):
        flange.apply(sheet, {
            "edge_selector": EdgesByPositionSelector(
                bbox=BBoxModel(min=(9.9, -1e6, 0.99),
                                max=(10.1, 1e6, 1.01)),
            ).model_dump(),
            "angle_deg": 90.0,
            "radius_mm": 1.0,
            "bend_side": "+X",
            "flange_length_mm": 0.0,
        })


# ---------------------------------------------------------------------------
# hem
# ---------------------------------------------------------------------------

def test_hem_adds_volume():
    from phone_designer.skills._selectors import (
        EdgesByPositionSelector, BBoxModel,
    )

    sheet = _build_sheet(40, 30, 1.0)
    v_before = _volume(sheet)

    hem = Hem()
    result = hem.apply(sheet, {
        "edge_selector": EdgesByPositionSelector(
            bbox=BBoxModel(min=(19.9, -1e6, 0.99),
                            max=(20.1, 1e6, 1.01)),
        ).model_dump(),
        "bend_side": "+X",
        "hem_length_mm": 8.0,
        "gap_mm": 0.0,
        "radius_mm": 0.5,
    })
    v_after = _volume(result.body)
    assert v_after > v_before, f"hem should add material: {v_before} → {v_after}"


def test_hem_with_explicit_gap_adds_volume():
    from phone_designer.skills._selectors import (
        EdgesByPositionSelector, BBoxModel,
    )

    sheet = _build_sheet(40, 30, 1.0)
    v_before = _volume(sheet)
    hem = Hem()
    result = hem.apply(sheet, {
        "edge_selector": EdgesByPositionSelector(
            bbox=BBoxModel(min=(19.9, -1e6, 0.99),
                            max=(20.1, 1e6, 1.01)),
        ).model_dump(),
        "bend_side": "+X",
        "hem_length_mm": 5.0,
        "gap_mm": 1.0,
    })
    v_after = _volume(result.body)
    assert v_after > v_before


# ---------------------------------------------------------------------------
# tab
# ---------------------------------------------------------------------------

def test_tab_adds_expected_volume():
    from phone_designer.skills._selectors import (
        EdgesByPositionSelector, BBoxModel,
    )

    sheet = _build_sheet(40, 30, 1.0)
    v_before = _volume(sheet)

    tab = Tab()
    result = tab.apply(sheet, {
        "edge_selector": EdgesByPositionSelector(
            bbox=BBoxModel(min=(19.9, -1e6, 0.99),
                            max=(20.1, 1e6, 1.01)),
        ).model_dump(),
        "tab_side": "+X",
        "tab_length_mm": 5.0,
        "tab_width_mm": 4.0,
    })
    v_after = _volume(result.body)
    expected = 5.0 * 4.0 * 1.0
    assert abs((v_after - v_before) - expected) / expected < 0.05


def test_tab_at_custom_position():
    from phone_designer.skills._selectors import (
        EdgesByPositionSelector, BBoxModel,
    )

    sheet = _build_sheet(40, 30, 1.0)
    tab = Tab()
    result = tab.apply(sheet, {
        "edge_selector": EdgesByPositionSelector(
            bbox=BBoxModel(min=(19.9, -1e6, 0.99),
                            max=(20.1, 1e6, 1.01)),
        ).model_dump(),
        "tab_side": "+X",
        "tab_length_mm": 3.0,
        "tab_width_mm": 2.0,
        "tab_position_along_edge_mm": 10.0,
    })
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(result.body)
    # Tab extends to x = 23.
    assert xmax == pytest.approx(23.0, abs=1e-2)
    # Tab is centered at y=10, width 2 → spans y=9..11. Sheet still spans
    # ymin=-15..15, so bbox y stays sheet-bounded.
    assert ymax == pytest.approx(15.0, abs=1e-2)


# ---------------------------------------------------------------------------
# slot
# ---------------------------------------------------------------------------

def test_slot_removes_volume_through_cut():
    from phone_designer.skills._selectors import (
        EdgesByPositionSelector, BBoxModel,
    )

    sheet = _build_sheet(40, 30, 1.0)
    v_before = _volume(sheet)

    slot = Slot()
    result = slot.apply(sheet, {
        "edge_selector": EdgesByPositionSelector(
            bbox=BBoxModel(min=(19.9, -1e6, 0.99),
                            max=(20.1, 1e6, 1.01)),
        ).model_dump(),
        "slot_inset_side": "-X",
        "slot_length_mm": 5.0,
        "slot_width_mm": 4.0,
    })
    v_after = _volume(result.body)
    expected = 5.0 * 4.0 * 1.0
    actual = v_before - v_after
    assert actual > 0
    assert abs(actual - expected) / expected < 0.10


def test_slot_partial_depth():
    from phone_designer.skills._selectors import (
        EdgesByPositionSelector, BBoxModel,
    )

    sheet = _build_sheet(40, 30, 2.0)
    v_before = _volume(sheet)

    slot = Slot()
    result = slot.apply(sheet, {
        "edge_selector": EdgesByPositionSelector(
            bbox=BBoxModel(min=(19.9, -1e6, 1.99),
                            max=(20.1, 1e6, 2.01)),
        ).model_dump(),
        "slot_inset_side": "-X",
        "slot_length_mm": 3.0,
        "slot_width_mm": 2.0,
        "slot_thickness_mm": 1.0,
    })
    v_after = _volume(result.body)
    expected = 3.0 * 2.0 * 1.0
    actual = v_before - v_after
    assert actual > 0
    assert abs(actual - expected) / expected < 0.10


# ---------------------------------------------------------------------------
# Spec metadata sanity
# ---------------------------------------------------------------------------

def test_all_skills_registered():
    assert SheetBase.spec.name == "sheet_base"
    assert SheetBase.spec.category == "create"
    assert BendEdge.spec.name == "bend_edge"
    assert BendEdge.spec.category == "modify/sheet"
    assert Flange.spec.name == "flange"
    assert Flange.spec.category == "modify/sheet"
    assert Flange.spec.level == "macro"
    assert Hem.spec.name == "hem"
    assert Tab.spec.name == "tab"
    assert Slot.spec.name == "slot"
