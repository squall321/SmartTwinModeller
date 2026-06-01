"""Pack W1-B FEM/CAE bridge — tests for the five skills:

    fem_cae.export_mesh_for_fem
    fem_cae.boundary_condition_tag
    fem_cae.material_property_tag
    inspect.modal_frequency_estimate
    inspect.stress_concentration_predict
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.fem_cae.boundary_condition_tag import (
    BoundaryConditionTag,
    get_bcs,
)
from phone_designer.skills.fem_cae.export_mesh_for_fem import ExportMeshForFem
from phone_designer.skills.fem_cae.material_property_tag import (
    MaterialPropertyTag,
    get_material,
)
from phone_designer.skills.inspect.modal_frequency_estimate import (
    ModalFrequencyEstimate,
)
from phone_designer.skills.inspect.stress_concentration_predict import (
    StressConcentrationPredict,
)


def _box(L: float = 20.0, W: float = 20.0, H: float = 10.0):
    return Box().apply(None, {
        "length_mm": L, "width_mm": W, "height_mm": H,
    }).body


# ────────────────────────────────────────────────────────────────────────────
# export_mesh_for_fem


def test_export_mesh_for_fem_inp_writes_node_and_element_blocks(tmp_path):
    body = _box(20.0, 20.0, 10.0)
    out = tmp_path / "box.inp"
    r = ExportMeshForFem().apply(body, {
        "out_path": str(out),
        "deflection_mm": 0.5,
        "format": "inp",
    })
    assert out.exists()
    text = out.read_text(encoding="ascii")
    assert "*NODE" in text
    assert "*ELEMENT, TYPE=S3" in text
    stats = r.extras["mesh_stats"]
    assert stats["tris"] > 0
    assert stats["nodes"] > 0
    assert stats["format"] == "inp"
    assert stats["path"] == str(out)
    # body is unchanged.
    assert r.body is body


def test_export_mesh_for_fem_msh_writes_gmsh_blocks(tmp_path):
    body = _box(10.0, 10.0, 10.0)
    out = tmp_path / "box.msh"
    r = ExportMeshForFem().apply(body, {
        "out_path": str(out),
        "deflection_mm": 0.5,
        "format": "msh",
    })
    assert out.exists()
    text = out.read_text(encoding="ascii")
    assert "$MeshFormat" in text
    assert "$Nodes" in text
    assert "$Elements" in text
    assert r.extras["mesh_stats"]["tris"] > 0


def test_export_mesh_for_fem_stl_writes_file(tmp_path):
    body = _box(10.0, 10.0, 10.0)
    out = tmp_path / "box.stl"
    r = ExportMeshForFem().apply(body, {
        "out_path": str(out),
        "deflection_mm": 0.5,
        "format": "stl",
    })
    assert out.exists()
    assert os.path.getsize(out) > 0
    assert r.extras["mesh_stats"]["format"] == "stl"


def test_export_mesh_for_fem_deflection_affects_triangle_count(tmp_path):
    body = _box(20.0, 20.0, 10.0)
    coarse = tmp_path / "coarse.inp"
    fine = tmp_path / "fine.inp"
    rc = ExportMeshForFem().apply(body, {
        "out_path": str(coarse), "deflection_mm": 5.0, "format": "inp",
    })
    rf = ExportMeshForFem().apply(body, {
        "out_path": str(fine), "deflection_mm": 0.05, "format": "inp",
    })
    # Finer deflection ≥ coarser triangle count.
    assert rf.extras["mesh_stats"]["tris"] >= rc.extras["mesh_stats"]["tris"]


# ────────────────────────────────────────────────────────────────────────────
# boundary_condition_tag


def test_boundary_condition_tag_fixed_attaches_record():
    body = _box(20.0, 20.0, 10.0)
    r = BoundaryConditionTag().apply(body, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "bc_kind": "fixed",
        "value": None,
    })
    bcs = get_bcs(r.body)
    assert len(bcs) == 1
    assert bcs[0]["kind"] == "fixed"
    assert bcs[0]["value"] is None
    assert len(bcs[0]["face_centers"]) >= 1
    # body unchanged geometrically.
    assert r.body is body


def test_boundary_condition_tag_force_requires_vector():
    body = _box(10.0, 10.0, 10.0)
    with pytest.raises(ValueError):
        BoundaryConditionTag().apply(body, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "bc_kind": "force",
            "value": 100.0,   # scalar — invalid for "force".
        })


def test_boundary_condition_tag_pressure_requires_scalar():
    body = _box(10.0, 10.0, 10.0)
    with pytest.raises(ValueError):
        BoundaryConditionTag().apply(body, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "bc_kind": "pressure",
            "value": None,
        })


def test_boundary_condition_tag_force_stores_vector():
    body = _box(10.0, 10.0, 10.0)
    r = BoundaryConditionTag().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "bc_kind": "force",
        "value": (10.0, 0.0, -5.0),
    })
    bcs = get_bcs(r.body)
    assert len(bcs) == 1
    assert bcs[0]["kind"] == "force"
    assert bcs[0]["value"] == [10.0, 0.0, -5.0]


def test_boundary_condition_tag_appends_multiple():
    body = _box(20.0, 20.0, 10.0)
    BoundaryConditionTag().apply(body, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "bc_kind": "fixed",
    })
    BoundaryConditionTag().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "bc_kind": "pressure",
        "value": 2.5,
    })
    bcs = get_bcs(body)
    assert len(bcs) == 2
    kinds = {b["kind"] for b in bcs}
    assert kinds == {"fixed", "pressure"}


# ────────────────────────────────────────────────────────────────────────────
# material_property_tag


def test_material_property_tag_body_scope_for_full_match():
    body = _box(20.0, 20.0, 10.0)
    # face_named "top" matches one face → scope must be "faces".
    r = MaterialPropertyTag().apply(body, {
        "face_or_body_selector": {"kind": "face_named", "name": "top"},
        "youngs_modulus_mpa": 70000.0,
        "poisson": 0.33,
        "density_kg_m3": 2700.0,
        "yield_mpa": 270.0,
    })
    mat = get_material(r.body)
    assert mat is not None
    assert mat["youngs_modulus_mpa"] == 70000.0
    assert mat["density_kg_m3"] == 2700.0
    assert mat["yield_mpa"] == 270.0
    assert mat["scope"] == "faces"
    assert len(mat["face_centers"]) == 1


def test_material_property_tag_defaults_applied():
    body = _box(10.0, 10.0, 10.0)
    r = MaterialPropertyTag().apply(body, {
        "face_or_body_selector": {"kind": "face_named", "name": "bottom"},
        "youngs_modulus_mpa": 200000.0,
    })
    mat = get_material(r.body)
    assert mat["poisson"] == 0.3
    assert mat["density_kg_m3"] == 7850.0
    assert mat["yield_mpa"] == 200.0


def test_material_property_tag_negative_modulus_rejected():
    body = _box(10.0, 10.0, 10.0)
    with pytest.raises(Exception):
        MaterialPropertyTag().apply(body, {
            "face_or_body_selector": {"kind": "face_named", "name": "bottom"},
            "youngs_modulus_mpa": -100.0,
        })


# ────────────────────────────────────────────────────────────────────────────
# modal_frequency_estimate


def test_modal_frequency_estimate_steel_plate_returns_positive_hz():
    body = _box(100.0, 100.0, 2.0)
    r = ModalFrequencyEstimate().apply(body, {
        "material": {
            "youngs_modulus_mpa": 200000.0,
            "poisson": 0.3,
            "density_kg_m3": 7850.0,
        },
        "body_volume_mm3": 100.0 * 100.0 * 2.0,
        "bbox": [[0.0, 0.0, 0.0], [100.0, 100.0, 2.0]],
    })
    f1 = r.extras["f1_hz_estimate"]
    assert f1 > 0.0
    # Steel 100x100x2 mm plate — first mode is in the few-hundred-Hz range.
    assert 100.0 < f1 < 5000.0
    inputs = r.extras["modal_inputs"]
    assert inputs["a_mm"] == 100.0
    assert inputs["b_mm"] == 100.0
    assert inputs["h_mm"] == pytest.approx(2.0, rel=1e-6)


def test_modal_frequency_estimate_thinner_plate_lower_f1():
    """A thinner plate has lower first frequency at fixed a, b (D scales as h³,
    mass scales as h, so f ∝ h). Confirm trend."""
    bbox_thin = [[0.0, 0.0, 0.0], [100.0, 100.0, 1.0]]
    bbox_thick = [[0.0, 0.0, 0.0], [100.0, 100.0, 4.0]]
    material = {
        "youngs_modulus_mpa": 200000.0,
        "poisson": 0.3,
        "density_kg_m3": 7850.0,
    }
    body = _box(100.0, 100.0, 1.0)
    thin = ModalFrequencyEstimate().apply(body, {
        "material": material,
        "body_volume_mm3": 100.0 * 100.0 * 1.0,
        "bbox": bbox_thin,
    }).extras["f1_hz_estimate"]
    thick = ModalFrequencyEstimate().apply(body, {
        "material": material,
        "body_volume_mm3": 100.0 * 100.0 * 4.0,
        "bbox": bbox_thick,
    }).extras["f1_hz_estimate"]
    assert thick > thin


def test_modal_frequency_estimate_degenerate_bbox_raises():
    body = _box(10.0, 10.0, 10.0)
    with pytest.raises(ValueError):
        ModalFrequencyEstimate().apply(body, {
            "material": {"youngs_modulus_mpa": 200000.0,
                          "poisson": 0.3,
                          "density_kg_m3": 7850.0},
            "body_volume_mm3": 1000.0,
            "bbox": [[0.0, 0.0, 0.0], [10.0, 10.0, 0.0]],
        })


# ────────────────────────────────────────────────────────────────────────────
# stress_concentration_predict


def test_stress_concentration_predict_returns_kt_and_local_stress():
    body = _box(20.0, 20.0, 10.0)
    r = StressConcentrationPredict().apply(body, {
        "min_internal_radius_mm": 0.5,
        "applied_stress_mpa": 100.0,
        "notch_depth_mm": 2.0,
    })
    summary = r.extras["stress_summary"]
    # Kt = 1 + 2 sqrt(2/0.5) = 1 + 2*2 = 5 → max ≥ 5.
    assert summary["max_Kt"] >= 5.0 - 1e-6
    assert summary["max_local_stress_mpa"] >= 500.0 - 1e-3
    assert summary["applied_stress_mpa"] == 100.0
    assert summary["min_internal_radius_mm"] == 0.5
    assert summary["hotspot_count"] >= 1
    # body unchanged.
    assert r.body is body


def test_stress_concentration_predict_larger_radius_reduces_kt():
    body = _box(20.0, 20.0, 10.0)
    sharp = StressConcentrationPredict().apply(body, {
        "min_internal_radius_mm": 0.1,
        "applied_stress_mpa": 50.0,
        "notch_depth_mm": 1.0,
    }).extras["stress_summary"]["max_Kt"]
    soft = StressConcentrationPredict().apply(body, {
        "min_internal_radius_mm": 5.0,
        "applied_stress_mpa": 50.0,
        "notch_depth_mm": 1.0,
    }).extras["stress_summary"]["max_Kt"]
    assert sharp > soft


def test_stress_concentration_predict_zero_radius_rejected():
    body = _box(10.0, 10.0, 10.0)
    with pytest.raises(Exception):
        StressConcentrationPredict().apply(body, {
            "min_internal_radius_mm": 0.0,
            "applied_stress_mpa": 50.0,
        })


def test_stress_concentration_predict_zero_applied_stress_returns_zero_local():
    body = _box(10.0, 10.0, 10.0)
    r = StressConcentrationPredict().apply(body, {
        "min_internal_radius_mm": 1.0,
        "applied_stress_mpa": 0.0,
        "notch_depth_mm": 1.0,
    })
    summary = r.extras["stress_summary"]
    assert summary["max_local_stress_mpa"] == 0.0
    assert summary["max_Kt"] > 1.0


# ────────────────────────────────────────────────────────────────────────────
# Integration — material + BC + mesh export round-trip.


def test_full_fem_pipeline_smoke(tmp_path):
    """Material + BCs + mesh export — three steps in succession should all
    succeed and the body should accumulate every annotation."""
    body = _box(40.0, 30.0, 5.0)

    MaterialPropertyTag().apply(body, {
        "face_or_body_selector": {"kind": "face_named", "name": "top"},
        "youngs_modulus_mpa": 70000.0,
        "poisson": 0.33,
        "density_kg_m3": 2700.0,
        "yield_mpa": 270.0,
    })
    BoundaryConditionTag().apply(body, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "bc_kind": "fixed",
    })
    BoundaryConditionTag().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "bc_kind": "pressure",
        "value": 1.5,
    })
    out = tmp_path / "panel.inp"
    r = ExportMeshForFem().apply(body, {
        "out_path": str(out),
        "deflection_mm": 0.5,
        "format": "inp",
    })

    mat = get_material(body)
    assert mat is not None
    assert mat["density_kg_m3"] == 2700.0
    bcs = get_bcs(body)
    assert len(bcs) == 2
    assert {b["kind"] for b in bcs} == {"fixed", "pressure"}
    assert out.exists()
    assert r.extras["mesh_stats"]["tris"] > 0
