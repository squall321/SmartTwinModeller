"""GD&T extended — ISO 5459 datum target + tolerance modifiers (Wave2-A).

Skills under test:
  * datum_target_assign       — body._pd_datums tag
  * basic_dimension_attach    — body._pd_basic_dims
  * projected_tolerance_zone  — body._pd_pgtolerance
  * mmc_lmc_modifier          — body._pd_mc_modifiers
  * runout_check              — extras["runout"]
  * feature_control_frame_compose — body._pd_fcf

All read-only — body must be returned unchanged
(post_condition body_present).
"""
from __future__ import annotations

import math

from phone_designer.skills.create.box import Box
from phone_designer.skills.create.cylinder import Cylinder
from phone_designer.skills.inspect.basic_dimension_attach import (
    BasicDimensionAttach,
)
from phone_designer.skills.inspect.datum_target_assign import (
    DatumTargetAssign,
)
from phone_designer.skills.inspect.feature_control_frame_compose import (
    FeatureControlFrameCompose,
)
from phone_designer.skills.inspect.mmc_lmc_modifier import MmcLmcModifier
from phone_designer.skills.inspect.projected_tolerance_zone import (
    ProjectedToleranceZone,
)
from phone_designer.skills.inspect.runout_check import RunoutCheck
from phone_designer.skills.modify_pocket.hole import Hole


# ──────────────────────────────────────────────────────────────────────────────
# datum_target_assign

def test_datum_target_assign_box_top_face():
    box = Box().apply(None, {
        "length_mm": 20, "width_mm": 20, "height_mm": 10,
    }).body
    r = DatumTargetAssign().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "datum_label": "A",
        "precedence": 1,
        "target_kind": "area",
    })
    dt = r.extras["datum_target"]
    assert dt["label"] == "A"
    assert dt["precedence"] == 1
    assert dt["kind"] == "area"
    assert dt["face_count"] >= 1
    # box top is planar +Z (z = height = 10 since align Z MIN, height = 10)
    assert abs(dt["center"][2] - 10.0) < 1e-3
    assert abs(abs(dt["normal"][2]) - 1.0) < 1e-3
    # stored on body
    assert "A" in getattr(r.body, "_pd_datums", {})
    assert r.extras["datums_total"] == 1
    assert r.body is box


def test_datum_target_assign_multiple_labels_accumulate():
    box = Box().apply(None, {
        "length_mm": 20, "width_mm": 20, "height_mm": 10,
    }).body
    body = DatumTargetAssign().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "datum_label": "A",
        "precedence": 1,
    }).body
    r2 = DatumTargetAssign().apply(body, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "datum_label": "B",
        "precedence": 2,
        "target_kind": "area",
    })
    assert r2.extras["datums_total"] == 2
    datums = getattr(r2.body, "_pd_datums", {})
    assert "A" in datums and "B" in datums
    assert datums["B"]["precedence"] == 2


def test_datum_target_assign_missing_match_raises():
    box = Box().apply(None, {
        "length_mm": 10, "width_mm": 10, "height_mm": 10,
    }).body
    try:
        DatumTargetAssign().apply(box, {
            "face_selector": {"kind": "faces_by_area", "min": 1e9, "max": 1e10},
            "datum_label": "C",
        })
    except ValueError as ex:
        assert "0 faces" in str(ex)
    else:
        raise AssertionError("expected ValueError for unmatched selector")


# ──────────────────────────────────────────────────────────────────────────────
# basic_dimension_attach

def test_basic_dim_distance_measured():
    """Distance between top and bottom face centers of a 20×20×10 box = 10."""
    box = Box().apply(None, {
        "length_mm": 20, "width_mm": 20, "height_mm": 10,
    }).body
    r = BasicDimensionAttach().apply(box, {
        "entity_a_selector": {
            "kind": "face_center",
            "selector": {"kind": "face_named", "name": "top"},
        },
        "entity_b_selector": {
            "kind": "face_center",
            "selector": {"kind": "face_named", "name": "bottom"},
        },
        "dimension_kind": "distance",
        "nominal_value": None,
        "basic": True,
    })
    dim = r.extras["dimension"]
    assert dim["kind"] == "distance"
    assert abs(dim["value"] - 10.0) < 1e-3
    assert abs(dim["measured"] - 10.0) < 1e-3
    assert dim["basic"] is True
    # stored on body
    assert len(getattr(r.body, "_pd_basic_dims", [])) == 1
    assert r.body is box


def test_basic_dim_distance_with_nominal_override():
    box = Box().apply(None, {
        "length_mm": 30, "width_mm": 30, "height_mm": 15,
    }).body
    r = BasicDimensionAttach().apply(box, {
        "entity_a_selector": {
            "kind": "face_center",
            "selector": {"kind": "face_named", "name": "top"},
        },
        "entity_b_selector": {
            "kind": "face_center",
            "selector": {"kind": "face_named", "name": "bottom"},
        },
        "dimension_kind": "distance",
        "nominal_value": 15.00,
        "basic": True,
    })
    dim = r.extras["dimension"]
    # nominal 명시 → value=15, measured=15 (same anyway)
    assert abs(dim["value"] - 15.0) < 1e-3
    assert abs(dim["measured"] - 15.0) < 1e-3


def test_basic_dim_diameter_of_cylinder():
    cyl = Cylinder().apply(None, {
        "radius_mm": 5.0, "height_mm": 10.0,
    }).body
    r = BasicDimensionAttach().apply(cyl, {
        "entity_a_selector": {
            "kind": "face_center",
            "selector": {
                "kind": "largest_n", "n": 1, "sort_by": "measure",
                "inner": {"kind": "faces_by_area", "min": 50.0, "max": 1e6},
            },
        },
        "entity_b_selector": {
            "kind": "face_center",
            "selector": {
                "kind": "largest_n", "n": 1, "sort_by": "measure",
                "inner": {"kind": "faces_by_area", "min": 50.0, "max": 1e6},
            },
        },
        "dimension_kind": "diameter",
    })
    dim = r.extras["dimension"]
    assert abs(dim["value"] - 10.0) < 1e-2   # diameter = 2 * r = 10
    assert dim["kind"] == "diameter"


# ──────────────────────────────────────────────────────────────────────────────
# projected_tolerance_zone

def test_projected_tolerance_zone_tags_hole():
    box = Box().apply(None, {
        "length_mm": 40, "width_mm": 40, "height_mm": 10,
    }).body
    drilled = Hole().apply(box, {
        "position": (0.0, 0.0, 10.0),
        "diameter_mm": 4.0,
        "depth_mm": 5.0,
        "direction": "-Z",
    }).body
    r = ProjectedToleranceZone().apply(drilled, {
        # r=2, depth=5 → lateral area ≈ 62.8 mm²
        "hole_face_selector": {
            "kind": "faces_by_area", "min": 50.0, "max": 80.0,
        },
        "projection_length_mm": 8.0,
        "tolerance_mm": 0.05,
    })
    pt = r.extras["projected_tolerance"]
    assert abs(pt["hole_radius_mm"] - 2.0) < 1e-3
    assert pt["projection_length_mm"] == 8.0
    assert pt["tolerance_mm"] == 0.05
    assert len(pt["axis_direction"]) == 3
    assert len(pt["projected_axis_tip"]) == 3
    # stored
    assert len(getattr(r.body, "_pd_pgtolerance", [])) == 1
    assert r.body is drilled


def test_projected_tolerance_zone_non_cylinder_raises():
    box = Box().apply(None, {
        "length_mm": 20, "width_mm": 20, "height_mm": 10,
    }).body
    try:
        ProjectedToleranceZone().apply(box, {
            "hole_face_selector": {"kind": "face_named", "name": "top"},
            "projection_length_mm": 5.0,
            "tolerance_mm": 0.1,
        })
    except ValueError as ex:
        assert "cylindrical" in str(ex)
    else:
        raise AssertionError("expected ValueError for non-cylindrical face")


# ──────────────────────────────────────────────────────────────────────────────
# mmc_lmc_modifier

def test_mmc_modifier_on_hole():
    box = Box().apply(None, {
        "length_mm": 40, "width_mm": 40, "height_mm": 10,
    }).body
    drilled = Hole().apply(box, {
        "position": (0.0, 0.0, 10.0),
        "diameter_mm": 4.0,
        "depth_mm": 5.0,
        "direction": "-Z",
    }).body
    r = MmcLmcModifier().apply(drilled, {
        "feature_selector": {
            "kind": "faces_by_area", "min": 50.0, "max": 80.0,
        },
        "modifier": "MMC",
    })
    mc = r.extras["material_condition"]
    assert mc["modifier"] == "MMC"
    assert mc["feature_kind"] == "cylinder"
    assert abs(mc["measured_size_mm"] - 4.0) < 1e-3
    # OCCT marks the inner hole surface as REVERSED → is_internal=True
    assert mc["is_internal"] is True
    # stored
    assert len(getattr(r.body, "_pd_mc_modifiers", [])) == 1
    assert r.body is drilled


def test_lmc_modifier_on_planar_feature():
    box = Box().apply(None, {
        "length_mm": 20, "width_mm": 20, "height_mm": 10,
    }).body
    r = MmcLmcModifier().apply(box, {
        "feature_selector": {"kind": "face_named", "name": "top"},
        "modifier": "LMC",
    })
    mc = r.extras["material_condition"]
    assert mc["modifier"] == "LMC"
    assert mc["feature_kind"] == "plane"
    assert mc["measured_size_mm"] == 0.0


def test_rfs_modifier_stored():
    box = Box().apply(None, {
        "length_mm": 10, "width_mm": 10, "height_mm": 10,
    }).body
    r = MmcLmcModifier().apply(box, {
        "feature_selector": {"kind": "face_named", "name": "top"},
        "modifier": "RFS",
    })
    assert r.extras["material_condition"]["modifier"] == "RFS"


# ──────────────────────────────────────────────────────────────────────────────
# runout_check

def test_runout_check_perfect_cylinder_low_runout():
    """Cylinder side rotated about its own axis → runout ≈ 0."""
    cyl = Cylinder().apply(None, {
        "radius_mm": 5.0, "height_mm": 20.0,
    }).body
    r = RunoutCheck().apply(cyl, {
        "rotating_axis": ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        "surface_selector": {
            "kind": "largest_n", "n": 1, "sort_by": "measure",
            "inner": {"kind": "faces_by_area", "min": 50.0, "max": 1e6},
        },
        "samples": 64,
        "slice_count": 8,
    })
    ro = r.extras["runout"]
    assert ro["sample_count"] >= 16
    # Perfectly round, perfectly straight → both ~ 0
    assert ro["total"] < 1e-3
    assert ro["circular"] < 1e-3
    assert ro["value_mm"] == ro["total"]
    assert r.body is cyl


def test_runout_check_offset_axis_gives_eccentric_runout():
    """Move the rotating axis 2 mm off the cylinder axis → big runout."""
    cyl = Cylinder().apply(None, {
        "radius_mm": 5.0, "height_mm": 20.0,
    }).body
    r = RunoutCheck().apply(cyl, {
        "rotating_axis": ((2.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        "surface_selector": {
            "kind": "largest_n", "n": 1, "sort_by": "measure",
            "inner": {"kind": "faces_by_area", "min": 50.0, "max": 1e6},
        },
        "samples": 64,
        "slice_count": 6,
    })
    ro = r.extras["runout"]
    # With a 2 mm offset rotating axis on a r=5 cyl, r_i sweeps from 3 to 7 →
    # total FIM ≈ 4 mm.
    assert ro["total"] > 3.0
    assert ro["circular"] > 3.0


# ──────────────────────────────────────────────────────────────────────────────
# feature_control_frame_compose

def test_fcf_position_with_mmc_on_datum_a():
    box = Box().apply(None, {
        "length_mm": 20, "width_mm": 20, "height_mm": 10,
    }).body
    # Pre-assign datum A so FCF can resolve.
    body = DatumTargetAssign().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "datum_label": "A",
        "precedence": 1,
    }).body
    r = FeatureControlFrameCompose().apply(body, {
        "geom_char": "position",
        "tolerance_value": 0.05,
        "modifiers": ["DIAMETER", "MMC"],
        "datums": ["A"],
    })
    fcf = r.extras["fcf"]
    assert fcf["geom_char"] == "position"
    assert fcf["geom_symbol"] == "⊕"
    assert fcf["tolerance_value"] == 0.05
    assert "Ⓜ" in fcf["modifier_symbols"]
    assert "⌀" in fcf["modifier_symbols"]
    # datum A was assigned → resolved=True
    assert fcf["datum_refs"][0]["label"] == "A"
    assert fcf["datum_refs"][0]["resolved"] is True
    # text contains the symbols
    assert "⊕" in fcf["text"]
    assert "⌀0.05" in fcf["text"]
    assert "Ⓜ" in fcf["text"]
    assert "| A" in fcf["text"]
    # stored on body
    assert len(getattr(r.body, "_pd_fcf", [])) == 1
    assert r.body is body


def test_fcf_flatness_no_datums_no_modifiers():
    box = Box().apply(None, {
        "length_mm": 10, "width_mm": 10, "height_mm": 5,
    }).body
    r = FeatureControlFrameCompose().apply(box, {
        "geom_char": "flatness",
        "tolerance_value": 0.02,
        "modifiers": [],
        "datums": [],
    })
    fcf = r.extras["fcf"]
    assert fcf["geom_symbol"] == "⏥"
    assert fcf["modifier_symbols"] == []
    assert fcf["datum_refs"] == []
    # Single-row text: "⏥ | 0.02"
    assert fcf["text"].startswith("⏥")
    assert "0.02" in fcf["text"]


def test_fcf_unresolved_datum_marked_unresolved():
    box = Box().apply(None, {
        "length_mm": 10, "width_mm": 10, "height_mm": 5,
    }).body
    r = FeatureControlFrameCompose().apply(box, {
        "geom_char": "perpendicularity",
        "tolerance_value": 0.1,
        "modifiers": [],
        "datums": ["A"],  # no datum A assigned
    })
    fcf = r.extras["fcf"]
    assert fcf["datum_refs"][0]["resolved"] is False


def test_fcf_unknown_modifier_raises():
    box = Box().apply(None, {
        "length_mm": 10, "width_mm": 10, "height_mm": 5,
    }).body
    try:
        FeatureControlFrameCompose().apply(box, {
            "geom_char": "position",
            "tolerance_value": 0.1,
            "modifiers": ["BOGUS"],
            "datums": [],
        })
    except ValueError as ex:
        assert "modifier" in str(ex).lower()
    else:
        raise AssertionError("expected ValueError for unknown modifier")
