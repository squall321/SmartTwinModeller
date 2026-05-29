"""disc_with_dome 단위 테스트."""
from __future__ import annotations

import pytest

from phone_designer.skills.create.disc_with_dome import DiscWithDome


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _bbox(body):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.Add_s(body.wrapped, bb, True)
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return (xmin, ymin, zmin, xmax, ymax, zmax)


def test_flat_disc_no_dome_no_fillet():
    """rise=0 + corner_r=0 → 순수 원기둥."""
    disc = DiscWithDome()
    r = disc.apply(None, {
        "diameter_mm": 40,
        "height_mm": 10,
        "dome_rise_mm": 0,
        "corner_r_mm": 0,
    })
    # 원기둥 부피 = π * r² * h = π * 20² * 10 = 12566.4
    expected = 3.14159 * 400 * 10
    assert abs(_volume(r.body) - expected) / expected < 0.05


def test_dome_increases_volume_above_flat():
    """rise > 0 → 평평한 disc 보다 부피 큼."""
    disc = DiscWithDome()
    flat = disc.apply(None, {
        "diameter_mm": 40, "height_mm": 10,
        "dome_rise_mm": 0, "corner_r_mm": 0,
    })
    domed = disc.apply(None, {
        "diameter_mm": 40, "height_mm": 10,
        "dome_rise_mm": 1.5, "corner_r_mm": 0,
    })
    assert _volume(domed.body) > _volume(flat.body)


def test_dome_bbox_height():
    """rise=1.5 → bbox z 최대 ≈ 10 + 1.5 = 11.5."""
    disc = DiscWithDome()
    r = disc.apply(None, {
        "diameter_mm": 40, "height_mm": 10,
        "dome_rise_mm": 1.5, "corner_r_mm": 0,
    })
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(r.body)
    assert abs(zmax - 11.5) < 0.2, f"top z={zmax}, expected ~11.5"
    assert abs(zmin) < 0.1


def test_corner_r_reduces_volume_slightly():
    disc = DiscWithDome()
    sharp = disc.apply(None, {
        "diameter_mm": 40, "height_mm": 10,
        "dome_rise_mm": 0, "corner_r_mm": 0,
    })
    rounded = disc.apply(None, {
        "diameter_mm": 40, "height_mm": 10,
        "dome_rise_mm": 0, "corner_r_mm": 2.0,
    })
    assert _volume(rounded.body) < _volume(sharp.body)


def test_disc_rejects_corner_r_too_large():
    disc = DiscWithDome()
    with pytest.raises(ValueError, match="too large"):
        disc.apply(None, {
            "diameter_mm": 10, "height_mm": 10,
            "dome_rise_mm": 0, "corner_r_mm": 6.0,   # 2*6 >= 10
        })


def test_disc_spec_metadata():
    spec = DiscWithDome.spec
    assert spec.name == "disc_with_dome"
    assert spec.level == "macro"
    assert spec.expansion is not None
    assert "cylinder" in spec.expansion
