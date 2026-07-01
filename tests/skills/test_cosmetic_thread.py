"""cosmetic_thread — lightweight (unmodeled) thread annotation.

Covers:
    (a) M6 on a plain body -> extras major=6.0, pitch=1.0, minor≈4.917,
        class default 6H, and the body is returned geometrically UNCHANGED
        (same object, same volume, same face count);
    (b) the durable ``_pd_thread`` attribute mirrors extras;
    (c) explicit-pitch designation 'M6x1.0' and separator/case variants parse
        to the same M6 catalog row; a fine-pitch override 'M8x1.0' beats the
        catalog coarse 1.25;
    (d) minor-diameter is the ISO 68-1 internal D1 = major - 1.08253*pitch for
        several sizes (M3, M8);
    (e) depth_mm + thread_class flow through;
    (f) a face_selector that hits the cylindrical side counts tapped_faces>=1;
    (g) an unknown designation raises.
"""
from __future__ import annotations

import pytest

from phone_designer.skills.inspect.cosmetic_thread import CosmeticThread


def _cylinder(radius_mm: float = 5.0, height_mm: float = 20.0):
    """A plain solid cylinder on the +Z axis (a boss the thread annotates)."""
    from build123d import Align, Cylinder

    return Cylinder(
        radius=radius_mm,
        height=height_mm,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    shape = body.wrapped if hasattr(body, "wrapped") else body
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return abs(float(props.Mass()))


def _face_count(body) -> int:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopTools import TopTools_MapOfShape

    shape = body.wrapped if hasattr(body, "wrapped") else body
    seen = TopTools_MapOfShape()
    it = TopExp_Explorer(shape, TopAbs_FACE)
    n = 0
    while it.More():
        f = it.Current()
        if not seen.Contains(f):
            seen.Add(f)
            n += 1
        it.Next()
    return n


# ──────────────────────────────────────────────────────────────────────────────
# (a) core: M6 spec values + body unchanged


def test_m6_extras_major_pitch_minor():
    body = _cylinder()
    v0, fc0 = _volume(body), _face_count(body)

    r = CosmeticThread().apply(body, {"designation": "M6"})
    ct = r.extras["cosmetic_thread"]

    assert ct["major_dia_mm"] == 6.0
    assert ct["pitch_mm"] == 1.0
    assert ct["minor_dia_mm"] == pytest.approx(4.917, abs=0.001)
    assert ct["class"] == "6H"
    assert ct["depth_mm"] is None
    assert ct["designation"] == "M6x1"

    # read-only — same object, same geometry
    assert r.body is body
    assert _volume(r.body) == pytest.approx(v0, rel=0, abs=1e-9)
    assert _face_count(r.body) == fc0


# (b) durable _pd_thread attribute


def test_pd_thread_attribute_mirrors_extras():
    body = _cylinder()
    r = CosmeticThread().apply(body, {"designation": "M6"})
    assert hasattr(r.body, "_pd_thread")
    meta = r.body._pd_thread
    assert meta["major_dia_mm"] == 6.0
    assert meta["pitch_mm"] == 1.0
    assert meta == r.extras["cosmetic_thread"]


# (c) designation parsing


@pytest.mark.parametrize("desig", ["M6", "M6x1.0", "M6X1", "m6x1", " M6 "])
def test_designation_variants_resolve_to_m6(desig):
    r = CosmeticThread().apply(_cylinder(), {"designation": desig})
    ct = r.extras["cosmetic_thread"]
    assert ct["major_dia_mm"] == 6.0
    assert ct["pitch_mm"] == 1.0
    assert ct["minor_dia_mm"] == pytest.approx(4.917, abs=0.001)


def test_explicit_fine_pitch_overrides_catalog_coarse():
    # M8 catalog coarse pitch is 1.25; an explicit x1.0 must win.
    r = CosmeticThread().apply(_cylinder(), {"designation": "M8x1.0"})
    ct = r.extras["cosmetic_thread"]
    assert ct["major_dia_mm"] == 8.0
    assert ct["pitch_mm"] == 1.0
    assert ct["minor_dia_mm"] == pytest.approx(8.0 - 1.08253175 * 1.0, abs=1e-3)


# (d) ISO internal minor diameter for other sizes


@pytest.mark.parametrize(
    "desig,major,pitch",
    [("M3", 3.0, 0.5), ("M8", 8.0, 1.25)],
)
def test_iso_minor_diameter(desig, major, pitch):
    ct = CosmeticThread().apply(
        _cylinder(), {"designation": desig}
    ).extras["cosmetic_thread"]
    assert ct["major_dia_mm"] == major
    assert ct["pitch_mm"] == pitch
    assert ct["minor_dia_mm"] == pytest.approx(major - 1.08253175 * pitch, abs=1e-3)


# (e) depth + class flow through


def test_depth_and_class_flow_through():
    ct = CosmeticThread().apply(
        _cylinder(), {"designation": "M6", "thread_class": "6g", "depth_mm": 12.5}
    ).extras["cosmetic_thread"]
    assert ct["depth_mm"] == 12.5
    assert ct["class"] == "6g"


# (f) face_selector tags the cylindrical side


def test_face_selector_counts_cylindrical_faces():
    body = _cylinder(radius_mm=5.0, height_mm=20.0)
    # The lateral surface of a Cylinder is the single cylindrical face; select
    # faces by a radial normal is awkward, so use faces_by_area (the side is
    # the largest face here: 2*pi*5*20 ≈ 628 vs each cap pi*25 ≈ 78.5).
    selector = {"kind": "largest_n", "inner": {"kind": "faces_by_area"}, "n": 1}
    ct = CosmeticThread().apply(
        body, {"designation": "M6", "face_selector": selector}
    ).extras["cosmetic_thread"]
    assert ct["tapped_faces"] >= 1


def test_no_selector_tapped_faces_zero():
    ct = CosmeticThread().apply(
        _cylinder(), {"designation": "M6"}
    ).extras["cosmetic_thread"]
    assert ct["tapped_faces"] == 0


# (g) unknown designation raises


def test_unknown_designation_raises():
    with pytest.raises(ValueError):
        CosmeticThread().apply(_cylinder(), {"designation": "M7"})


def test_unparseable_designation_raises():
    with pytest.raises(ValueError):
        CosmeticThread().apply(_cylinder(), {"designation": "banana"})
