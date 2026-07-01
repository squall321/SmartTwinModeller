"""Tests for trim_surface — trim/split a FACE or a SOLID by a cutting plane.

Analytic anchors:
  * A 20×20 planar face centred at the origin has area 400 mm². Trimming it by a
    plane through its centre (X=0, +X normal) keeps exactly HALF → ~200 mm².
  * A 20×20×20 box has volume 8000 mm³. Trimming by the Z=10 mid-plane keeps
    half → ~4000 mm³.
"""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_curvature.trim_surface import TrimSurface
from phone_designer.skills.modify_pocket._sketch import RectangleSketch
from phone_designer.skills.modify_pocket._sketch_to_solid import _build_planar_face


def _area(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, g)
    return abs(float(g.Mass()))


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return abs(float(g.Mass()))


def _unwrap(body):
    return body.wrapped if hasattr(body, "wrapped") else body


def _make_face_20x20():
    """A 20×20 planar face centred at the origin (area = 400 mm²)."""
    from build123d import Part
    face = _build_planar_face(RectangleSketch(length_mm=20.0, width_mm=20.0,
                                              center_x_mm=0.0, center_y_mm=0.0))
    return Part(face)


# ── FACE input: keeps ~half the area ────────────────────────────────────────


def test_trim_planar_face_keeps_half_area():
    face = _make_face_20x20()
    assert _area(_unwrap(face)) == pytest.approx(400.0, rel=1e-3)

    r = TrimSurface().apply(face, {
        "plane_origin_mm": (0.0, 0.0, 0.0),
        "plane_normal": (1.0, 0.0, 0.0),   # split at X=0
        "keep": "positive",                # keep +X half
    })
    kept = _area(_unwrap(r.body))
    assert kept == pytest.approx(200.0, rel=1e-3), (
        f"trim of 400mm² face should keep ~200mm², got {kept}")

    ex = r.extras["trim_surface"]
    assert ex["input_kind"] == "face"
    assert ex["area_mm2"] == pytest.approx(200.0, rel=1e-3)
    assert ex["input_area_mm2"] == pytest.approx(400.0, rel=1e-3)
    assert ex["kept_fraction"] == pytest.approx(0.5, abs=0.01)


def test_trim_face_negative_side_also_half():
    """Keeping the negative side gives the complementary ~200 mm²."""
    face = _make_face_20x20()
    r = TrimSurface().apply(face, {
        "plane_origin_mm": (0.0, 0.0, 0.0),
        "plane_normal": (1.0, 0.0, 0.0),
        "keep": "negative",
    })
    assert _area(_unwrap(r.body)) == pytest.approx(200.0, rel=1e-3)


def test_trim_face_returns_face_body():
    """A face input must return a FACE (surface body), not a solid."""
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    face = _make_face_20x20()
    r = TrimSurface().apply(face, {
        "plane_normal": (1.0, 0.0, 0.0), "keep": "positive"})
    assert not TopExp_Explorer(_unwrap(r.body), TopAbs_SOLID).More()
    assert _volume(_unwrap(r.body)) == pytest.approx(0.0, abs=1e-6)


# ── SOLID input: keeps ~half the volume ─────────────────────────────────────


def test_trim_solid_keeps_half_volume():
    box = Box().apply(None, {"length_mm": 20.0, "width_mm": 20.0,
                             "height_mm": 20.0}).body
    v0 = _volume(_unwrap(box))
    assert v0 == pytest.approx(8000.0, rel=1e-3)

    # Box created at origin spans z in [0, 20]; trim at Z=10 mid-plane.
    r = TrimSurface().apply(box, {
        "plane_origin_mm": (0.0, 0.0, 10.0),
        "plane_normal": (0.0, 0.0, 1.0),
        "keep": "positive",
    })
    kept = _volume(_unwrap(r.body))
    assert kept == pytest.approx(4000.0, rel=1e-2), (
        f"trim of 8000mm³ box at mid-plane should keep ~4000mm³, got {kept}")

    ex = r.extras["trim_surface"]
    assert ex["input_kind"] == "solid"
    assert ex["kept_fraction"] == pytest.approx(0.5, abs=0.02)


# ── guards ──────────────────────────────────────────────────────────────────


def test_trim_zero_normal_rejected():
    face = _make_face_20x20()
    with pytest.raises(Exception, match="zero_plane_normal"):
        TrimSurface().apply(face, {"plane_normal": (0.0, 0.0, 0.0)})


def test_trim_no_input_rejected():
    with pytest.raises(Exception, match="trim_no_input"):
        TrimSurface().apply(None, {"plane_normal": (1.0, 0.0, 0.0)})


def test_trim_plane_misses_body_refuses_empty_side():
    """A plane far off the body leaves everything on one side; keeping the empty
    side must refuse (fm.trim_no_piece), never return a silent empty body.

    Body spans X∈[-10, 10]; plane at X=100 (+X normal) means the whole body is on
    the NEGATIVE side, so keep='positive' finds nothing."""
    face = _make_face_20x20()
    with pytest.raises(Exception, match="trim_no_piece"):
        TrimSurface().apply(face, {
            "plane_origin_mm": (100.0, 0.0, 0.0),
            "plane_normal": (1.0, 0.0, 0.0),
            "keep": "positive",
        })


def test_trim_post_condition_kind():
    spec = TrimSurface.spec
    assert any(pc.kind == "body_present" for pc in spec.post_conditions)


def test_trim_surface_registered():
    from phone_designer.skills._registry import registry
    names = {s.name for s in registry.all()}
    assert "trim_surface" in names
