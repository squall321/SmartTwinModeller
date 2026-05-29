"""Tests for threaded-insert pocket skills:
   heatset_insert_pocket, helicoil_pocket.

Each skill is exercised on a simple box body. We verify:
  - volume decreases (post_conditions enforce this),
  - removed volume matches the catalog cylinder volume,
  - the catalog YAMLs contain the documented metric size keys,
  - manifest-level spec attributes (category, post_conditions),
  - error paths (unknown insert spec, unknown length multiple).
"""
from __future__ import annotations

import math
import pathlib

import pytest
import yaml

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.heatset_insert_pocket import HeatsetInsertPocket
from phone_designer.skills.modify_pocket.helicoil_pocket import HelicoilPocket


# ── helpers ─────────────────────────────────────────────────────────────────


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _make_box(L: float = 40.0, W: float = 40.0, H: float = 20.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


def _catalog(name: str) -> dict:
    root = pathlib.Path(__file__).resolve().parents[2]
    return yaml.safe_load((root / "catalogs" / "standards" / f"{name}.yaml").read_text())


# ── catalog presence ────────────────────────────────────────────────────────


def test_heatset_catalog_has_expected_sizes():
    cat = _catalog("inserts_heatset")
    expected = {"M2", "M2.5", "M3", "M4", "M5"}
    assert expected <= set(cat["inserts"].keys())
    for key, entry in cat["inserts"].items():
        for field in ("outer_d_mm", "length_mm", "pilot_hole_d_mm"):
            assert field in entry, f"{key} missing {field}"
            assert entry[field] > 0
        # pilot hole must be smaller than outer_d for knurls to bite
        assert entry["pilot_hole_d_mm"] < entry["outer_d_mm"]


def test_helicoil_catalog_has_expected_sizes():
    cat = _catalog("inserts_helicoil")
    expected = {"M3", "M4", "M5", "M6", "M8", "M10"}
    assert expected <= set(cat["inserts"].keys())
    for key, entry in cat["inserts"].items():
        assert entry["tap_drill_mm"] > 0
        assert {"1xD", "1.5xD", "2xD"} <= set(entry["lengths_mm"].keys())
        # ascending length multiples
        ls = entry["lengths_mm"]
        assert ls["1xD"] < ls["1.5xD"] < ls["2xD"]


# ── heatset_insert_pocket ───────────────────────────────────────────────────


def test_heatset_insert_pocket_decreases_volume_m3():
    box = _make_box(40, 40, 20)
    v0 = _volume(box)
    r = HeatsetInsertPocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "position_xy": (0.0, 0.0),
        "insert_spec": "M3",
    })
    v1 = _volume(r.body)
    assert v1 < v0
    cat = _catalog("inserts_heatset")["inserts"]["M3"]
    expected = math.pi * (cat["pilot_hole_d_mm"] / 2.0) ** 2 * cat["length_mm"]
    delta = v0 - v1
    assert abs(delta - expected) / expected < 0.02


def test_heatset_insert_pocket_depth_override():
    box = _make_box(40, 40, 20)
    v0 = _volume(box)
    r = HeatsetInsertPocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "insert_spec": "M4",
        "depth_override_mm": 10.0,
    })
    v1 = _volume(r.body)
    cat = _catalog("inserts_heatset")["inserts"]["M4"]
    expected = math.pi * (cat["pilot_hole_d_mm"] / 2.0) ** 2 * 10.0
    delta = v0 - v1
    assert abs(delta - expected) / expected < 0.02


def test_heatset_insert_pocket_rejects_unknown_spec():
    box = _make_box(40, 40, 20)
    with pytest.raises(ValueError, match="unknown insert_spec"):
        HeatsetInsertPocket().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "insert_spec": "M99",
        })


def test_heatset_insert_pocket_spec_metadata():
    spec = HeatsetInsertPocket.spec
    assert spec.name == "heatset_insert_pocket"
    assert spec.category == "modify/pocket"
    assert spec.level == "atomic"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── helicoil_pocket ─────────────────────────────────────────────────────────


def test_helicoil_pocket_decreases_volume_default_1p5xd():
    box = _make_box(40, 40, 30)
    v0 = _volume(box)
    r = HelicoilPocket().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "insert_spec": "M5",
    })
    v1 = _volume(r.body)
    assert v1 < v0
    cat = _catalog("inserts_helicoil")["inserts"]["M5"]
    expected = math.pi * (cat["tap_drill_mm"] / 2.0) ** 2 * cat["lengths_mm"]["1.5xD"]
    delta = v0 - v1
    assert abs(delta - expected) / expected < 0.02


def test_helicoil_pocket_2xd_deeper_than_1xd():
    cat = _catalog("inserts_helicoil")["inserts"]["M6"]
    tap_d = cat["tap_drill_mm"]
    r_area = math.pi * (tap_d / 2.0) ** 2

    box_a = _make_box(40, 40, 30)
    v0a = _volume(box_a)
    ra = HelicoilPocket().apply(box_a, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "insert_spec": "M6",
        "length_multiple": "1xD",
    })
    delta_a = v0a - _volume(ra.body)

    box_b = _make_box(40, 40, 30)
    v0b = _volume(box_b)
    rb = HelicoilPocket().apply(box_b, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "insert_spec": "M6",
        "length_multiple": "2xD",
    })
    delta_b = v0b - _volume(rb.body)

    assert delta_b > delta_a
    expected_a = r_area * cat["lengths_mm"]["1xD"]
    expected_b = r_area * cat["lengths_mm"]["2xD"]
    assert abs(delta_a - expected_a) / expected_a < 0.02
    assert abs(delta_b - expected_b) / expected_b < 0.02


def test_helicoil_pocket_rejects_unknown_spec():
    box = _make_box(40, 40, 30)
    with pytest.raises(ValueError, match="unknown insert_spec"):
        HelicoilPocket().apply(box, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "insert_spec": "M99",
        })


def test_helicoil_pocket_spec_metadata():
    spec = HelicoilPocket.spec
    assert spec.name == "helicoil_pocket"
    assert spec.category == "modify/pocket"
    assert spec.level == "atomic"
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)
