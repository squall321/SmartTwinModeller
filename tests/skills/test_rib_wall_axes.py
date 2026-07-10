"""rib up_axis generalisation (±X/±Y wall ribs) + legacy ±Z regression pins.

Legacy regression values (4590.0 / 4528.284271247462) were captured on HEAD
(commit 82b740a, pre-change rib.py) and BREP byte-identity of the ±Z branch
was verified out-of-band (BRepTools.Write_s pre/post byte-compare: identical).
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_boss.rib import Rib


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _bbox(body) -> tuple[float, float, float, float, float, float]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    b = Bnd_Box()
    BRepBndLib.Add_s(body.wrapped, b, False)
    mn, mx = b.CornerMin(), b.CornerMax()
    return mn.X(), mn.Y(), mn.Z(), mx.X(), mx.Y(), mx.Z()


def _solid_count(body) -> int:
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    it, n = TopExp_Explorer(body.wrapped, TopAbs_SOLID), 0
    while it.More():
        n += 1
        it.Next()
    return n


def _box_60_40_30():
    # XY centered, Z from 0: x ∈ [-30, 30], y ∈ [-20, 20], z ∈ [0, 30]
    return Box().apply(None, {"length_mm": 60, "width_mm": 40, "height_mm": 30}).body


# ── wall ribs (new ±X/±Y branch) ────────────────────────────────────────────


def test_wall_rib_minus_y_vertical():
    """Vertical rib on the y=-20 wall: grows outward to y=-24.

    Rib prism is 20 (path along Z) × 6 (width along X) × 4 (height along -Y),
    entirely outside the host box → the fuse adds exactly 6·4·20 = 480 mm³
    (no draft geometry in v1, flat end caps — zero delta expected).
    """
    box = _box_60_40_30()
    v0 = _volume(box)
    r = Rib().apply(box, {
        "start": (0.0, -20.0, 5.0),
        "end": (0.0, -20.0, 25.0),
        "width_mm": 6.0,
        "height_mm": 4.0,
        "up_axis": "-Y",
    })
    delta = _volume(r.body) - v0
    assert abs(delta - 480.0) < 1e-6 * 480.0
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    assert abs(ymin - (-24.0)) < 1e-3          # rib protrudes to y = -24
    assert abs(ymax - 20.0) < 1e-3             # opposite wall untouched
    assert abs(xmin - (-30.0)) < 1e-3 and abs(xmax - 30.0) < 1e-3
    # is_solid house rule: SOLID count AND volume
    assert _solid_count(r.body) == 1
    assert _volume(r.body) > 1e-6


def test_wall_rib_plus_x_variant():
    """+X rib on the x=+30 wall, path along Y — protrudes to x = 34."""
    box = _box_60_40_30()
    v0 = _volume(box)
    r = Rib().apply(box, {
        "start": (30.0, -10.0, 15.0),
        "end": (30.0, 10.0, 15.0),
        "width_mm": 6.0,
        "height_mm": 4.0,
        "up_axis": "+X",
    })
    delta = _volume(r.body) - v0
    assert abs(delta - 480.0) < 1e-6 * 480.0
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    assert abs(xmax - 34.0) < 1e-3
    assert abs(zmin - 0.0) < 1e-3 and abs(zmax - 30.0) < 1e-3   # width stays in-wall
    assert _solid_count(r.body) == 1


def test_wall_rib_minus_x_diagonal_path():
    """-X wall rib with a diagonal (Y+Z) path — full 3D path is honoured."""
    box = _box_60_40_30()
    v0 = _volume(box)
    r = Rib().apply(box, {
        "start": (-30.0, -10.0, 5.0),
        "end": (-30.0, 10.0, 25.0),
        "width_mm": 2.0,
        "height_mm": 3.0,
        "up_axis": "-X",
    })
    expected = math.sqrt(20.0 ** 2 + 20.0 ** 2) * 2.0 * 3.0
    delta = _volume(r.body) - v0
    assert abs(delta - expected) / expected < 1e-6
    xmin = _bbox(r.body)[0]
    assert abs(xmin - (-33.0)) < 1e-3          # grows outward from x=-30 wall


# ── fm.axis_parallel refusal ────────────────────────────────────────────────


def test_wall_rib_parallel_axis_refused():
    """Path along +Y with up_axis +Y — degenerate frame → structured refusal."""
    box = _box_60_40_30()
    with pytest.raises(ValueError, match=r"fm\.axis_parallel"):
        Rib().apply(box, {
            "start": (0.0, -5.0, 5.0),
            "end": (0.0, 5.0, 5.0),
            "up_axis": "+Y",
        })


def test_wall_rib_antiparallel_axis_refused():
    """Anti-parallel counts too: path along +X with up_axis -X."""
    box = _box_60_40_30()
    with pytest.raises(ValueError, match=r"fm\.axis_parallel"):
        Rib().apply(box, {
            "start": (-5.0, 0.0, 5.0),
            "end": (5.0, 0.0, 5.0),
            "up_axis": "-X",
        })


def test_wall_rib_zero_length_refused():
    """start == end on the wall branch keeps the legacy zero-length message."""
    box = _box_60_40_30()
    with pytest.raises(ValueError, match="zero length"):
        Rib().apply(box, {
            "start": (0.0, -20.0, 5.0),
            "end": (0.0, -20.0, 5.0),
            "up_axis": "-Y",
        })


def test_manifest_declares_axis_parallel():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    rib = next(s for s in m["skills"] if s["name"] == "rib")
    assert "fm.axis_parallel" in rib["failure_modes"]
    enum = rib["args_schema"]["properties"]["up_axis"]["enum"]
    assert set(enum) == {"+X", "-X", "+Y", "-Y", "+Z", "-Z"}


# ── legacy ±Z regression pins (values captured on HEAD, pre-change) ─────────


def test_legacy_plus_z_rib_volume_unchanged():
    """Same call as test_rib_adds_volume_along_path: HEAD volume was 4590.0."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 5}).body
    r = Rib().apply(box, {
        "start": (-10.0, 0.0, 5.0),
        "end": (10.0, 0.0, 5.0),
        "width_mm": 1.5,
        "height_mm": 3.0,
        "up_axis": "+Z",
    })
    assert abs(_volume(r.body) - 4590.0) < 1e-9 * 4590.0


def test_legacy_diagonal_default_up_volume_unchanged():
    """Default up_axis stays +Z: HEAD volume was 4528.284271247462."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 5}).body
    r = Rib().apply(box, {
        "start": (-5.0, -5.0, 5.0),
        "end": (5.0, 5.0, 5.0),
        "width_mm": 1.0,
        "height_mm": 2.0,
    })
    assert abs(_volume(r.body) - 4528.284271247462) < 1e-8


def test_legacy_minus_z_rib_volume_unchanged():
    """-Z branch (rib below the bottom face): HEAD volume was 4590.0."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 5}).body
    r = Rib().apply(box, {
        "start": (-10.0, 0.0, 0.0),
        "end": (10.0, 0.0, 0.0),
        "width_mm": 1.5,
        "height_mm": 3.0,
        "up_axis": "-Z",
    })
    assert abs(_volume(r.body) - 4590.0) < 1e-9 * 4590.0
