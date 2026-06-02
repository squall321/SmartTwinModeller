"""Pack W1-B (CAE bridge deeper) — tests for the six new skills:

    fem_cae.mesh_refinement_zones
    fem_cae.thermal_bc_tag
    fem_cae.contact_pair
    fem_cae.modal_analysis_setup
    fem_cae.load_case_compose
    fem_cae.export_abaqus_inp_v2
"""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.fem_cae.boundary_condition_tag import (
    BoundaryConditionTag,
    get_bcs,
)
from phone_designer.skills.fem_cae.contact_pair import ContactPair, get_contacts
from phone_designer.skills.fem_cae.export_abaqus_inp_v2 import ExportAbaqusInpV2
from phone_designer.skills.fem_cae.load_case_compose import (
    LoadCaseCompose,
    get_load_cases,
)
from phone_designer.skills.fem_cae.material_property_tag import MaterialPropertyTag
from phone_designer.skills.fem_cae.mesh_refinement_zones import (
    MeshRefinementZones,
    get_mesh_refinement,
)
from phone_designer.skills.fem_cae.modal_analysis_setup import (
    ModalAnalysisSetup,
    get_modal,
)
from phone_designer.skills.fem_cae.thermal_bc_tag import (
    ThermalBcTag,
    get_thermal_bcs,
)


def _box(L: float = 20.0, W: float = 20.0, H: float = 10.0):
    return Box().apply(None, {
        "length_mm": L, "width_mm": W, "height_mm": H,
    }).body


# ────────────────────────────────────────────────────────────────────────────
# mesh_refinement_zones


def test_mesh_refinement_zones_attaches_record():
    body = _box(20.0, 20.0, 10.0)
    r = MeshRefinementZones().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "refinement_factor": 4.0,
    })
    zones = get_mesh_refinement(r.body)
    assert len(zones) == 1
    assert zones[0]["factor"] == 4.0
    assert len(zones[0]["face_centers"]) >= 1
    # Body unchanged.
    assert r.body is body


def test_mesh_refinement_zones_appends_multiple():
    body = _box(20.0, 20.0, 10.0)
    MeshRefinementZones().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "refinement_factor": 2.0,
    })
    MeshRefinementZones().apply(body, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "refinement_factor": 8.0,
    })
    zones = get_mesh_refinement(body)
    assert len(zones) == 2
    factors = {z["factor"] for z in zones}
    assert factors == {2.0, 8.0}


def test_mesh_refinement_zones_factor_below_one_rejected():
    body = _box(10.0, 10.0, 10.0)
    with pytest.raises(Exception):
        MeshRefinementZones().apply(body, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "refinement_factor": 0.5,
        })


# ────────────────────────────────────────────────────────────────────────────
# thermal_bc_tag


def test_thermal_bc_tag_fixed_temp():
    body = _box(20.0, 20.0, 10.0)
    r = ThermalBcTag().apply(body, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "bc_kind": "fixed_temp",
        "value": 25.0,
    })
    bcs = get_thermal_bcs(r.body)
    assert len(bcs) == 1
    assert bcs[0]["kind"] == "fixed_temp"
    assert bcs[0]["value"] == 25.0


def test_thermal_bc_tag_convection_requires_h_and_ambient():
    body = _box(20.0, 20.0, 10.0)
    with pytest.raises(ValueError):
        ThermalBcTag().apply(body, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "bc_kind": "convection",
            "value": 0.0,
            # missing h_coefficient and ambient_t_c
        })


def test_thermal_bc_tag_convection_accepts_full_spec():
    body = _box(20.0, 20.0, 10.0)
    r = ThermalBcTag().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "bc_kind": "convection",
        "value": 0.0,
        "h_coefficient": 15.0,
        "ambient_t_c": 22.0,
    })
    bcs = get_thermal_bcs(r.body)
    assert bcs[0]["h_coefficient"] == 15.0
    assert bcs[0]["ambient_t_c"] == 22.0


def test_thermal_bc_tag_radiation_emissivity_range_enforced():
    body = _box(10.0, 10.0, 10.0)
    with pytest.raises(ValueError):
        ThermalBcTag().apply(body, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "bc_kind": "radiation",
            "value": 1.7,   # out of [0,1]
            "ambient_t_c": 25.0,
        })


# ────────────────────────────────────────────────────────────────────────────
# contact_pair


def test_contact_pair_bonded_pair_recorded():
    body = _box(20.0, 20.0, 10.0)
    r = ContactPair().apply(body, {
        "face_selector_a": {"kind": "face_named", "name": "top"},
        "face_selector_b": {"kind": "face_named", "name": "bottom"},
        "contact_kind": "bonded",
        "friction_coef": 0.0,
    })
    contacts = get_contacts(r.body)
    assert len(contacts) == 1
    assert contacts[0]["kind"] == "bonded"
    assert len(contacts[0]["a_face_centers"]) >= 1
    assert len(contacts[0]["b_face_centers"]) >= 1


def test_contact_pair_frictional_records_coef():
    body = _box(20.0, 20.0, 10.0)
    r = ContactPair().apply(body, {
        "face_selector_a": {"kind": "face_named", "name": "top"},
        "face_selector_b": {"kind": "face_named", "name": "bottom"},
        "contact_kind": "frictional",
        "friction_coef": 0.45,
    })
    contacts = get_contacts(r.body)
    assert contacts[0]["friction_coef"] == 0.45


def test_contact_pair_frictional_requires_positive_mu():
    body = _box(20.0, 20.0, 10.0)
    with pytest.raises(ValueError):
        ContactPair().apply(body, {
            "face_selector_a": {"kind": "face_named", "name": "top"},
            "face_selector_b": {"kind": "face_named", "name": "bottom"},
            "contact_kind": "frictional",
            "friction_coef": 0.0,
        })


# ────────────────────────────────────────────────────────────────────────────
# modal_analysis_setup


def test_modal_analysis_setup_defaults():
    body = _box(20.0, 20.0, 10.0)
    r = ModalAnalysisSetup().apply(body, {})
    modal = get_modal(r.body)
    assert modal is not None
    assert modal["count"] == 10
    assert modal["range"] == (0.0, 1000.0)


def test_modal_analysis_setup_custom_range():
    body = _box(20.0, 20.0, 10.0)
    r = ModalAnalysisSetup().apply(body, {
        "mode_count": 25,
        "frequency_range_hz": (10.0, 5000.0),
    })
    modal = get_modal(r.body)
    assert modal["count"] == 25
    assert modal["range"] == (10.0, 5000.0)


def test_modal_analysis_setup_invalid_range_rejected():
    body = _box(20.0, 20.0, 10.0)
    with pytest.raises(Exception):
        ModalAnalysisSetup().apply(body, {
            "mode_count": 5,
            "frequency_range_hz": (1000.0, 100.0),  # hi < lo
        })


# ────────────────────────────────────────────────────────────────────────────
# load_case_compose


def test_load_case_compose_static_case():
    body = _box(20.0, 20.0, 10.0)
    r = LoadCaseCompose().apply(body, {
        "case_name": "GRAVITY",
        "bcs": ["fixed_base"],
        "loads": [{"kind": "gravity", "g_mm_s2": 9810.0}],
        "analysis_type": "static",
    })
    cases = get_load_cases(r.body)
    assert len(cases) == 1
    assert cases[0]["name"] == "GRAVITY"
    assert cases[0]["analysis_type"] == "static"
    assert cases[0]["bcs"] == ["fixed_base"]


def test_load_case_compose_modal_allows_empty_bcs_loads():
    body = _box(20.0, 20.0, 10.0)
    r = LoadCaseCompose().apply(body, {
        "case_name": "FREE_MODAL",
        "analysis_type": "modal",
    })
    cases = get_load_cases(r.body)
    assert cases[0]["analysis_type"] == "modal"


def test_load_case_compose_static_empty_rejected():
    body = _box(20.0, 20.0, 10.0)
    with pytest.raises(ValueError):
        LoadCaseCompose().apply(body, {
            "case_name": "EMPTY",
            "analysis_type": "static",
        })


def test_load_case_compose_duplicate_name_rejected():
    body = _box(20.0, 20.0, 10.0)
    LoadCaseCompose().apply(body, {
        "case_name": "STEP1",
        "bcs": ["x"],
        "analysis_type": "static",
    })
    with pytest.raises(ValueError):
        LoadCaseCompose().apply(body, {
            "case_name": "STEP1",
            "bcs": ["y"],
            "analysis_type": "static",
        })


# ────────────────────────────────────────────────────────────────────────────
# export_abaqus_inp_v2


def test_export_abaqus_inp_v2_writes_mesh_and_material(tmp_path):
    body = _box(40.0, 30.0, 5.0)
    MaterialPropertyTag().apply(body, {
        "face_or_body_selector": {"kind": "face_named", "name": "top"},
        "youngs_modulus_mpa": 70000.0,
        "poisson": 0.33,
        "density_kg_m3": 2700.0,
        "yield_mpa": 270.0,
    })
    out = tmp_path / "panel.inp"
    r = ExportAbaqusInpV2().apply(body, {
        "path": str(out),
        "mesh_size_mm": 2.0,
    })
    assert out.exists()
    text = out.read_text(encoding="ascii")
    assert "*NODE" in text
    assert "*ELEMENT, TYPE=S3" in text
    assert "*MATERIAL, NAME=MAT_BODY" in text
    assert "*ELASTIC" in text
    assert "*DENSITY" in text
    stats = r.extras["mesh_stats"]
    assert stats["tris"] > 0
    assert stats["format"] == "inp_v2"


def test_export_abaqus_inp_v2_emits_bc_cards(tmp_path):
    body = _box(40.0, 30.0, 5.0)
    BoundaryConditionTag().apply(body, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "bc_kind": "fixed",
    })
    BoundaryConditionTag().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "bc_kind": "pressure",
        "value": 1.5,
    })
    out = tmp_path / "with_bcs.inp"
    r = ExportAbaqusInpV2().apply(body, {
        "path": str(out),
        "include_bcs": True,
    })
    text = out.read_text(encoding="ascii")
    assert "*BOUNDARY" in text
    assert "NSET_BC_" in text
    assert r.extras["mesh_stats"]["bc_cards"] >= 2


def test_export_abaqus_inp_v2_emits_contact_cards(tmp_path):
    body = _box(40.0, 30.0, 10.0)
    ContactPair().apply(body, {
        "face_selector_a": {"kind": "face_named", "name": "top"},
        "face_selector_b": {"kind": "face_named", "name": "bottom"},
        "contact_kind": "bonded",
    })
    out = tmp_path / "with_contact.inp"
    r = ExportAbaqusInpV2().apply(body, {
        "path": str(out),
        "include_contacts": True,
    })
    text = out.read_text(encoding="ascii")
    assert "*SURFACE" in text
    assert "CSURF_A_1" in text
    assert "CSURF_B_1" in text
    assert r.extras["mesh_stats"]["contact_cards"] >= 1


def test_export_abaqus_inp_v2_emits_load_case_steps(tmp_path):
    body = _box(40.0, 30.0, 5.0)
    BoundaryConditionTag().apply(body, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "bc_kind": "fixed",
    })
    LoadCaseCompose().apply(body, {
        "case_name": "STATIC_LOAD",
        "bcs": ["bottom_fixed"],
        "loads": [{"kind": "gravity", "g": 9810.0}],
        "analysis_type": "static",
    })
    LoadCaseCompose().apply(body, {
        "case_name": "MODAL",
        "analysis_type": "modal",
    })
    ModalAnalysisSetup().apply(body, {
        "mode_count": 6,
        "frequency_range_hz": (0.0, 2000.0),
    })
    out = tmp_path / "multi_step.inp"
    r = ExportAbaqusInpV2().apply(body, {
        "path": str(out),
        "include_bcs": True,
        "include_loads": True,
    })
    text = out.read_text(encoding="ascii")
    assert "*STEP, NAME=STATIC_LOAD" in text
    assert "*STEP, NAME=MODAL" in text
    assert "*STATIC" in text
    assert "*FREQUENCY" in text
    assert r.extras["mesh_stats"]["load_case_steps"] == 2


def test_export_abaqus_inp_v2_modal_only_emits_default_step(tmp_path):
    body = _box(40.0, 30.0, 5.0)
    ModalAnalysisSetup().apply(body, {
        "mode_count": 8,
        "frequency_range_hz": (0.0, 1500.0),
    })
    out = tmp_path / "modal_only.inp"
    r = ExportAbaqusInpV2().apply(body, {
        "path": str(out),
        "include_loads": True,
    })
    text = out.read_text(encoding="ascii")
    assert "*FREQUENCY" in text
    assert "8, 0.000000, 1500.000000" in text
    assert r.extras["mesh_stats"]["load_case_steps"] >= 1


def test_export_abaqus_inp_v2_thermal_bcs_emit_cards(tmp_path):
    body = _box(20.0, 20.0, 10.0)
    ThermalBcTag().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "bc_kind": "fixed_temp",
        "value": 100.0,
    })
    ThermalBcTag().apply(body, {
        "face_selector": {"kind": "face_named", "name": "bottom"},
        "bc_kind": "convection",
        "value": 0.0,
        "h_coefficient": 25.0,
        "ambient_t_c": 22.0,
    })
    out = tmp_path / "thermal.inp"
    r = ExportAbaqusInpV2().apply(body, {
        "path": str(out),
        "include_bcs": True,
    })
    text = out.read_text(encoding="ascii")
    assert "NSET_TBC_" in text
    assert "*FILM" in text
    assert r.extras["mesh_stats"]["bc_cards"] >= 2


def test_export_abaqus_inp_v2_includes_refinement_comment(tmp_path):
    body = _box(20.0, 20.0, 10.0)
    MeshRefinementZones().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "refinement_factor": 4.0,
    })
    out = tmp_path / "with_refine.inp"
    ExportAbaqusInpV2().apply(body, {
        "path": str(out),
    })
    text = out.read_text(encoding="ascii")
    assert "** mesh refinement zones:" in text
    assert "factor=4.0" in text


def test_export_abaqus_inp_v2_full_pipeline(tmp_path):
    """End-to-end — material + mechanical BC + thermal BC + contact +
    load case + modal setup all emitted in one deck."""
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
    ThermalBcTag().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "bc_kind": "heat_flux",
        "value": 500.0,
    })
    ContactPair().apply(body, {
        "face_selector_a": {"kind": "face_named", "name": "top"},
        "face_selector_b": {"kind": "face_named", "name": "bottom"},
        "contact_kind": "frictional",
        "friction_coef": 0.3,
    })
    MeshRefinementZones().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "refinement_factor": 2.0,
    })
    LoadCaseCompose().apply(body, {
        "case_name": "FULL_LOAD",
        "bcs": ["base"],
        "loads": [{"kind": "pressure", "value_mpa": 1.0}],
        "analysis_type": "static",
    })
    out = tmp_path / "full.inp"
    r = ExportAbaqusInpV2().apply(body, {
        "path": str(out),
        "include_bcs": True,
        "include_loads": True,
        "include_contacts": True,
        "mesh_size_mm": 2.0,
    })
    assert out.exists()
    stats = r.extras["mesh_stats"]
    assert stats["tris"] > 0
    assert stats["bc_cards"] >= 2
    assert stats["contact_cards"] >= 1
    assert stats["load_case_steps"] >= 1
    # Body still has all tags.
    assert get_bcs(body)
    assert get_thermal_bcs(body)
    assert get_contacts(body)
    assert get_mesh_refinement(body)
    assert get_load_cases(body)
