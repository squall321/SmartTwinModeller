"""bearing_bore — extended ISO 15 catalog (62xx completed, 63xx added).

The gearbox exercise refused '6204' live: the shipped catalog stopped the
62xx series at 6203 and had no 63xx at all. These tests pin:

  1. '6204' now builds (60x60x30 box, seat on a wall) and the seat bore
     diameter is EXACTLY 47 mm — measured on the resulting cylindrical face.
  2. '6306' works end-to-end.
  3. An unknown spec ('9999') still gets the honest refusal that lists the
     known designations.
  4. Existing '608' / '6202' behaviour (dims + build) is unchanged.

Plus a catalog-schema check: every entry carries exactly the three public
ISO 15 dimension keys (bore_id_mm / outer_d_mm / width_mm) as positive
numbers, and outer_d_mm > bore_id_mm.
"""
from __future__ import annotations

import math
import pathlib

import pytest
import yaml

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.bearing_bore import BearingBore

CATALOG_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "catalogs" / "standards" / "bearings_metric.yaml"
)

# ISO 15 public dimensions (bore_id_mm, outer_d_mm, width_mm).
NEW_ENTRIES = {
    "6203": (17, 40, 12),
    "6204": (20, 47, 14),
    "6205": (25, 52, 15),
    "6206": (30, 62, 16),
    "6207": (35, 72, 17),
    "6208": (40, 80, 18),
    "6209": (45, 85, 19),
    "6210": (50, 90, 20),
    "6300": (10, 35, 11),
    "6301": (12, 37, 12),
    "6302": (15, 42, 13),
    "6303": (17, 47, 14),
    "6304": (20, 52, 15),
    "6305": (25, 62, 17),
    "6306": (30, 72, 19),
}

# Pre-existing entries that must NOT change (regression guard).
LEGACY_ENTRIES = {
    "608": (8, 22, 7),
    "625": (5, 16, 5),
    "6000": (10, 26, 8),
    "6202": (15, 35, 11),
}


def _catalog() -> dict:
    return yaml.safe_load(CATALOG_PATH.read_text())["bearings"]


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _cylindrical_face_radii(body) -> list[float]:
    """Radii of every cylindrical face on the body (exact, from the surface)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    radii = []
    it = TopExp_Explorer(body.wrapped, TopAbs_FACE)
    while it.More():
        face = TopoDS.Face_s(it.Current())
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() == GeomAbs_Cylinder:
            radii.append(surf.Cylinder().Radius())
        it.Next()
    return radii


def _box(length: float, width: float, height: float):
    return Box().apply(
        None, {"length_mm": length, "width_mm": width, "height_mm": height},
    ).body


# ───────────────────────────── catalog schema ────────────────────────────────


def test_catalog_new_entries_have_exact_iso15_dims():
    catalog = _catalog()
    for spec, (bore, outer, width) in NEW_ENTRIES.items():
        assert spec in catalog, f"catalog missing {spec}"
        entry = catalog[spec]
        assert entry["bore_id_mm"] == bore, f"{spec} bore_id_mm"
        assert entry["outer_d_mm"] == outer, f"{spec} outer_d_mm"
        assert entry["width_mm"] == width, f"{spec} width_mm"


def test_catalog_legacy_entries_unchanged():
    catalog = _catalog()
    for spec, (bore, outer, width) in LEGACY_ENTRIES.items():
        entry = catalog[spec]
        assert entry["bore_id_mm"] == bore, f"{spec} bore_id_mm changed"
        assert entry["outer_d_mm"] == outer, f"{spec} outer_d_mm changed"
        assert entry["width_mm"] == width, f"{spec} width_mm changed"


def test_catalog_schema_uniform_across_all_entries():
    catalog = _catalog()
    for spec, entry in catalog.items():
        assert set(entry.keys()) == {"bore_id_mm", "outer_d_mm", "width_mm"}, (
            f"{spec}: schema drift — keys {sorted(entry.keys())}"
        )
        for key, value in entry.items():
            assert isinstance(value, (int, float)) and value > 0, (
                f"{spec}.{key} must be a positive number, got {value!r}"
            )
        assert entry["outer_d_mm"] > entry["bore_id_mm"], (
            f"{spec}: outer_d_mm must exceed bore_id_mm"
        )


# ───────────────────────── PIN 1: 6204 builds, Ø == 47 ───────────────────────


def test_bearing_bore_6204_builds_and_seat_diameter_is_exactly_47():
    # 60x60x30 housing block, seat sunk into the top wall (z=30, axis -Z).
    body = _box(60, 60, 30)
    v_before = _volume(body)

    r = BearingBore().apply(
        body,
        {
            "axis_origin": (0.0, 0.0, 30.0),
            "axis_direction": (0.0, 0.0, -1.0),
            "bearing_spec": "6204",
            "with_shoulder": False,
        },
    )
    v_after = _volume(r.body)
    assert v_after < v_before, "6204 bore must remove material"

    # 6204: outer Ø 47, width 14 → removed volume π·23.5²·14
    expected = math.pi * 23.5 ** 2 * 14.0
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.001, (
        f"6204 seat volume: expected {expected:.1f}, got {actual:.1f}"
    )

    # The seat bore must be EXACTLY Ø 47 — measured on the cylindrical face.
    radii = _cylindrical_face_radii(r.body)
    assert radii, "cut body must expose a cylindrical seat face"
    assert any(abs(rad - 23.5) < 1e-9 for rad in radii), (
        f"expected a cylindrical face of radius 23.5 (Ø47), got radii {radii}"
    )


def test_bearing_bore_6204_with_shoulder_removes_more():
    body = _box(60, 60, 30)
    plain = BearingBore().apply(
        body,
        {
            "axis_origin": (0.0, 0.0, 30.0),
            "axis_direction": (0.0, 0.0, -1.0),
            "bearing_spec": "6204",
            "with_shoulder": False,
        },
    )
    shouldered = BearingBore().apply(
        body,
        {
            "axis_origin": (0.0, 0.0, 30.0),
            "axis_direction": (0.0, 0.0, -1.0),
            "bearing_spec": "6204",
            "with_shoulder": True,
            "shoulder_height_mm": 3.0,
        },
    )
    assert _volume(shouldered.body) < _volume(plain.body)


# ───────────────────────────── PIN 2: 6306 works ─────────────────────────────


def test_bearing_bore_6306_builds_with_catalog_geometry():
    # 6306: outer Ø 72, width 19 → block must be wide enough for the seat.
    body = _box(100, 100, 40)
    v_before = _volume(body)

    r = BearingBore().apply(
        body,
        {
            "axis_origin": (0.0, 0.0, 40.0),
            "axis_direction": (0.0, 0.0, -1.0),
            "bearing_spec": "6306",
            "with_shoulder": False,
        },
    )
    v_after = _volume(r.body)
    expected = math.pi * 36.0 ** 2 * 19.0
    actual = v_before - v_after
    assert abs(actual - expected) / expected < 0.001, (
        f"6306 seat volume: expected {expected:.1f}, got {actual:.1f}"
    )
    radii = _cylindrical_face_radii(r.body)
    assert any(abs(rad - 36.0) < 1e-9 for rad in radii), (
        f"expected a cylindrical face of radius 36 (Ø72), got radii {radii}"
    )


# ─────────────────────── PIN 3: unknown spec honest refusal ──────────────────


def test_bearing_bore_unknown_spec_9999_refused_listing_known_specs():
    body = _box(60, 60, 30)
    with pytest.raises(ValueError) as exc_info:
        BearingBore().apply(
            body,
            {
                "axis_origin": (0.0, 0.0, 30.0),
                "axis_direction": (0.0, 0.0, -1.0),
                "bearing_spec": "9999",
            },
        )
    msg = str(exc_info.value)
    assert "unknown bearing_spec" in msg and "9999" in msg
    # The refusal must list the known designations (honest, actionable).
    for known in ("608", "6202", "6204", "6306"):
        assert known in msg, f"refusal must list known spec {known}: {msg}"


# ──────────────────── PIN 4: existing 608/6202 unchanged ─────────────────────


def test_bearing_bore_608_behaviour_unchanged():
    body = _box(40, 40, 20)
    v_before = _volume(body)
    r = BearingBore().apply(
        body,
        {
            "axis_origin": (0.0, 0.0, 20.0),
            "axis_direction": (0.0, 0.0, -1.0),
            "bearing_spec": "608",
        },
    )
    expected = math.pi * 11.0 ** 2 * 7.0
    actual = v_before - _volume(r.body)
    assert abs(actual - expected) / expected < 0.001
    radii = _cylindrical_face_radii(r.body)
    assert any(abs(rad - 11.0) < 1e-9 for rad in radii)


def test_bearing_bore_6202_behaviour_unchanged():
    body = _box(60, 60, 30)
    v_before = _volume(body)
    r = BearingBore().apply(
        body,
        {
            "axis_origin": (0.0, 0.0, 30.0),
            "axis_direction": (0.0, 0.0, -1.0),
            "bearing_spec": "6202",
        },
    )
    expected = math.pi * 17.5 ** 2 * 11.0
    actual = v_before - _volume(r.body)
    assert abs(actual - expected) / expected < 0.001
    radii = _cylindrical_face_radii(r.body)
    assert any(abs(rad - 17.5) < 1e-9 for rad in radii)
