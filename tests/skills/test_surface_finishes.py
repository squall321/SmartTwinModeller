"""Tests for the Pack W1-C surface finishes:
   apply_anodize, apply_paint, apply_plating, apply_texture_region,
   emi_gasket_groove + their catalogs.

Each finish-tag skill must:
    - succeed on a simple box with a planar selector,
    - tag the body with catalog-sourced metadata,
    - NOT change topology when grow_geometry=False (anodize/paint/plating
      default; texture is always metadata-only),
    - raise on an unknown spec key,
    - declare body_present post_condition.

emi_gasket_groove must strictly decrease the volume (volume_decreased
post_condition) and use the catalog width/depth verbatim when no override
is provided.
"""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_finish.apply_anodize import (
    ApplyAnodize,
    get_anodize_tags,
)
from phone_designer.skills.modify_finish.apply_paint import (
    ApplyPaint,
    get_paint_tags,
)
from phone_designer.skills.modify_finish.apply_plating import (
    ApplyPlating,
    get_plating_tags,
)
from phone_designer.skills.modify_finish.apply_texture_region import (
    ApplyTextureRegion,
    get_texture_tags,
)
from phone_designer.skills.modify_pocket.emi_gasket_groove import EmiGasketGroove


# ── helpers ─────────────────────────────────────────────────────────────────


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _count_faces(body) -> int:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    n = 0
    it = TopExp_Explorer(
        body.wrapped if hasattr(body, "wrapped") else body, TopAbs_FACE
    )
    while it.More():
        n += 1
        it.Next()
    return n


def _make_box(L: float = 40.0, W: float = 30.0, H: float = 10.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


# ── catalog availability ───────────────────────────────────────────────────


def _load_catalog(name: str):
    import pathlib
    import yaml
    root = pathlib.Path(__file__).resolve().parents[2]
    return yaml.safe_load(
        (root / "catalogs" / "finishes" / f"{name}.yaml").read_text()
    )


def test_catalogs_present_and_keyed():
    """All five finish catalogs must load and contain the documented keys."""
    anodize = _load_catalog("anodize")["finishes"]
    for k in [
        "Anodize_Type_II", "Anodize_Type_III", "Anodize_Type_II_Clear",
        "Anodize_Color_Red", "Anodize_Color_Blue", "Anodize_Color_Gold",
    ]:
        assert k in anodize, f"missing anodize key {k}"
        e = anodize[k]
        for f in [
            "thickness_um", "hardness_hv", "color", "max_substrate_temp_c",
            "dimensional_growth_um_per_side",
        ]:
            assert f in e, f"anodize.{k} missing field {f}"

    paint = _load_catalog("paint")["finishes"]
    for k in [
        "Paint_Pantone_Cool_Gray_9",
        "Paint_Pantone_Black_6",
        "Paint_Pantone_Red_032_C",
    ]:
        assert k in paint
        for f in ["thickness_um", "durability_rating", "dimensional_growth_um"]:
            assert f in paint[k], f"paint.{k} missing {f}"
        # spec target: thickness ~50 μm.
        assert 30 <= paint[k]["thickness_um"] <= 100

    plating = _load_catalog("plating")["finishes"]
    for k in [
        "Nickel_Electroless_15um", "Zinc_Chromate_8um",
        "Chrome_25um", "Gold_Flash_0.5um",
    ]:
        assert k in plating
        for f in ["thickness_um", "hardness_hv", "corrosion_rating"]:
            assert f in plating[k], f"plating.{k} missing {f}"

    textures = _load_catalog("textures")["textures"]
    expected_ra = {
        "VDI3400_15": 0.4,
        "VDI3400_21": 1.4,
        "VDI3400_27": 5.0,
        "VDI3400_33": 12.0,
        "VDI3400_39": 27.0,
    }
    for k, ra in expected_ra.items():
        assert k in textures
        assert textures[k]["ra_um"] == pytest.approx(ra)
        for f in ["draft_delta_deg", "etch_depth_um"]:
            assert f in textures[k]
    for k in ["MoldTech_MT11005", "MoldTech_MT11020", "MoldTech_MT11030"]:
        assert k in textures

    emi = _load_catalog("emi_gaskets")["gaskets"]
    for k in [
        "Chomerics_CHO-FORM_5550_0.5mm",
        "Chomerics_CHO-FORM_5550_1.0mm",
        "Chomerics_CHO-FORM_5550_1.5mm",
        "Parker_E-Foam_4880",
    ]:
        assert k in emi
        e = emi[k]
        for f in [
            "cs_mm", "conductivity_db",
            "recommended_groove_w_mm", "recommended_groove_d_mm",
        ]:
            assert f in e, f"emi_gaskets.{k} missing {f}"
    # Sanity: groove w > cs (sidewall clearance).
    e1 = emi["Chomerics_CHO-FORM_5550_1.0mm"]
    assert e1["recommended_groove_w_mm"] >= e1["cs_mm"]


# ── apply_anodize ──────────────────────────────────────────────────────────


def test_apply_anodize_tags_metadata_without_growing():
    box = _make_box()
    v0 = _volume(box)
    nf0 = _count_faces(box)

    r = ApplyAnodize().apply(box, {
        "face_selector": {"kind": "faces_by_normal", "direction": (0, 0, 1)},
        "anodize_spec": "Anodize_Type_II",
    })
    assert _volume(r.body) == pytest.approx(v0, rel=1e-9)
    assert _count_faces(r.body) == nf0

    tags = get_anodize_tags(r.body)
    assert len(tags) == 1
    assert tags[0]["anodize_spec"] == "Anodize_Type_II"
    assert tags[0]["thickness_um"] == 25.0
    assert tags[0]["dimensional_growth_um_per_side"] == 12.5
    assert tags[0]["color"] == "matte_black"
    assert r.extras["face_count"] >= 1
    assert r.extras["grew_geometry"] is False


def test_apply_anodize_growth_increases_volume():
    """grow_geometry=True applies a tiny but measurable skin offset."""
    box = _make_box(40, 30, 10)
    v0 = _volume(box)
    r = ApplyAnodize().apply(box, {
        "face_selector": {"kind": "faces_by_normal", "direction": (0, 0, 1)},
        "anodize_spec": "Anodize_Type_III",   # 25 μm / side → noticeable.
        "grow_geometry": True,
    })
    v1 = _volume(r.body)
    assert v1 > v0, f"grown body should be larger ({v0} → {v1})"
    # Lower bound: outer envelope grows by 25 μm on every side. For a 40x30x10
    # box the surface area is 2*(40*30 + 40*10 + 30*10) = 2*(1200+400+300)=3800
    # → ΔV ≳ 3800 * 0.025 = 95 mm³. Allow plenty of slack for the offset
    # algorithm's corner handling.
    assert (v1 - v0) > 20.0


def test_apply_anodize_unknown_spec_raises():
    box = _make_box()
    with pytest.raises(ValueError, match="unknown anodize_spec"):
        ApplyAnodize().apply(box, {
            "face_selector": {"kind": "faces_by_normal", "direction": (0, 0, 1)},
            "anodize_spec": "Anodize_Unobtainium",
        })


def test_apply_anodize_spec_metadata():
    spec = ApplyAnodize.spec
    assert spec.name == "apply_anodize"
    assert spec.category == "modify/finish"
    assert spec.level == "atomic"
    assert spec.post_conditions[0].kind == "body_present"


# ── apply_paint ────────────────────────────────────────────────────────────


def test_apply_paint_tags_pantone_metadata():
    box = _make_box()
    v0 = _volume(box)
    r = ApplyPaint().apply(box, {
        "face_selector": {"kind": "faces_by_normal", "direction": (0, 0, 1)},
        "paint_spec": "Paint_Pantone_Cool_Gray_9",
    })
    assert _volume(r.body) == pytest.approx(v0, rel=1e-9)

    tags = get_paint_tags(r.body)
    assert len(tags) == 1
    rec = tags[0]
    assert rec["paint_spec"] == "Paint_Pantone_Cool_Gray_9"
    assert rec["pantone"].startswith("Pantone")
    assert rec["thickness_um"] == 50.0
    assert rec["durability_rating"] == 3


def test_apply_paint_unknown_spec_raises():
    box = _make_box()
    with pytest.raises(ValueError, match="unknown paint_spec"):
        ApplyPaint().apply(box, {
            "face_selector": {"kind": "faces_by_normal", "direction": (0, 0, 1)},
            "paint_spec": "Paint_None",
        })


def test_apply_paint_spec_metadata():
    spec = ApplyPaint.spec
    assert spec.name == "apply_paint"
    assert spec.category == "modify/finish"
    assert spec.post_conditions[0].kind == "body_present"


# ── apply_plating ──────────────────────────────────────────────────────────


def test_apply_plating_tags_record_double_sided_flag():
    box = _make_box()
    r = ApplyPlating().apply(box, {
        "face_selector": {"kind": "faces_by_normal", "direction": (0, 0, 1)},
        "plating_spec": "Nickel_Electroless_15um",
        "double_sided": True,
    })
    tags = get_plating_tags(r.body)
    assert tags[0]["plating_spec"] == "Nickel_Electroless_15um"
    assert tags[0]["thickness_um"] == 15.0
    assert tags[0]["double_sided"] is True
    assert tags[0]["hardness_hv"] == 550


def test_apply_plating_default_no_growth():
    """double_sided default + grow_geometry=False → volume unchanged."""
    box = _make_box()
    v0 = _volume(box)
    r = ApplyPlating().apply(box, {
        "face_selector": {"kind": "faces_by_normal", "direction": (0, 0, 1)},
        "plating_spec": "Chrome_25um",
    })
    assert _volume(r.body) == pytest.approx(v0, rel=1e-9)
    assert r.extras["grew_geometry"] is False


def test_apply_plating_unknown_spec_raises():
    box = _make_box()
    with pytest.raises(ValueError, match="unknown plating_spec"):
        ApplyPlating().apply(box, {
            "face_selector": {"kind": "faces_by_normal", "direction": (0, 0, 1)},
            "plating_spec": "Unobtainium_99um",
        })


def test_apply_plating_spec_metadata():
    spec = ApplyPlating.spec
    assert spec.name == "apply_plating"
    assert spec.category == "modify/finish"
    assert spec.post_conditions[0].kind == "body_present"


# ── apply_texture_region ───────────────────────────────────────────────────


def test_apply_texture_tags_draft_delta():
    box = _make_box()
    v0 = _volume(box)
    nf0 = _count_faces(box)

    r = ApplyTextureRegion().apply(box, {
        "face_selector": {"kind": "faces_by_normal", "direction": (1, 0, 0)},
        "texture_spec": "VDI3400_27",
    })
    # metadata only — topology and volume unchanged.
    assert _count_faces(r.body) == nf0
    assert _volume(r.body) == pytest.approx(v0, rel=1e-9)

    tags = get_texture_tags(r.body)
    assert len(tags) == 1
    rec = tags[0]
    assert rec["texture_spec"] == "VDI3400_27"
    assert rec["ra_um"] == pytest.approx(5.0)
    assert rec["draft_delta_deg"] == pytest.approx(1.5)
    assert r.extras["draft_delta_deg"] == pytest.approx(1.5)


def test_apply_texture_mold_tech():
    box = _make_box()
    r = ApplyTextureRegion().apply(box, {
        "face_selector": {"kind": "faces_by_normal", "direction": (0, 1, 0)},
        "texture_spec": "MoldTech_MT11020",
    })
    tags = get_texture_tags(r.body)
    assert tags[0]["grade"] == "MT-11020"
    assert tags[0]["draft_delta_deg"] == pytest.approx(2.0)


def test_apply_texture_unknown_spec_raises():
    box = _make_box()
    with pytest.raises(ValueError, match="unknown texture_spec"):
        ApplyTextureRegion().apply(box, {
            "face_selector": {"kind": "faces_by_normal", "direction": (0, 0, 1)},
            "texture_spec": "VDI3400_999",
        })


def test_apply_texture_spec_metadata():
    spec = ApplyTextureRegion.spec
    assert spec.name == "apply_texture_region"
    assert spec.category == "modify/finish"
    assert spec.post_conditions[0].kind == "body_present"


# ── emi_gasket_groove ──────────────────────────────────────────────────────


def _square_path(half: float, z_offset: float = 0.0):
    """Centerline of a square loop in face-local XY (origin = face centroid)."""
    return [(-half, -half), (half, -half), (half, half), (-half, half)]


def test_emi_gasket_groove_decreases_volume():
    box = _make_box(40, 30, 10)
    v0 = _volume(box)

    r = EmiGasketGroove().apply(box, {
        "face_selector": {"kind": "faces_by_normal", "direction": (0, 0, 1)},
        "path_points": _square_path(10.0),
        "gasket_spec": "Chomerics_CHO-FORM_5550_1.0mm",
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # Catalog: w=1.1mm, d=0.7mm. Perimeter = 4 * 20 = 80mm.
    # Approx removed volume ≈ 80 * 1.1 * 0.7 ≈ 61.6 mm³.
    expected = 80.0 * 1.1 * 0.7
    delta = v0 - v1
    assert delta > 0.5 * expected, f"expected ≈ {expected}, got {delta}"
    assert delta < 1.5 * expected


def test_emi_gasket_groove_uses_catalog_dims():
    """extras must report the exact catalog width/depth when no override."""
    box = _make_box(40, 30, 10)
    r = EmiGasketGroove().apply(box, {
        "face_selector": {"kind": "faces_by_normal", "direction": (0, 0, 1)},
        "path_points": _square_path(10.0),
        "gasket_spec": "Chomerics_CHO-FORM_5550_1.5mm",
    })
    assert r.extras["gasket_spec"] == "Chomerics_CHO-FORM_5550_1.5mm"
    assert r.extras["cs_mm"] == pytest.approx(1.5)
    assert r.extras["groove_w_mm"] == pytest.approx(1.65)
    assert r.extras["groove_d_mm"] == pytest.approx(1.05)


def test_emi_gasket_groove_override_dims():
    box = _make_box(40, 30, 10)
    r = EmiGasketGroove().apply(box, {
        "face_selector": {"kind": "faces_by_normal", "direction": (0, 0, 1)},
        "path_points": _square_path(10.0),
        "gasket_spec": "Chomerics_CHO-FORM_5550_0.5mm",
        "width_override_mm": 1.0,
        "depth_override_mm": 0.5,
    })
    assert r.extras["groove_w_mm"] == pytest.approx(1.0)
    assert r.extras["groove_d_mm"] == pytest.approx(0.5)


def test_emi_gasket_groove_unknown_spec_raises():
    box = _make_box()
    with pytest.raises(ValueError, match="unknown gasket_spec"):
        EmiGasketGroove().apply(box, {
            "face_selector": {"kind": "faces_by_normal", "direction": (0, 0, 1)},
            "path_points": _square_path(10.0),
            "gasket_spec": "Imaginary_Gasket",
        })


def test_emi_gasket_groove_spec_metadata():
    spec = EmiGasketGroove.spec
    assert spec.name == "emi_gasket_groove"
    assert spec.category == "modify/pocket"
    assert spec.post_conditions[0].kind == "volume_decreased"
