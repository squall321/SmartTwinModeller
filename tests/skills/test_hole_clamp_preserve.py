"""preserve/import-mode generic-hole emission — raw axis_origin + entry_depth_mm,
no 200mm clamp, no bbox-face override (2026-06-18).

In preserve_brep / import mode (shift == identity) the generic `hole` re-cut used
to take depth_mm (the FULL cylinder length), clamp it to a blind 200mm cut, and
move the entry to a bbox face — together carving a fresh floor 200mm inside solid
material (as1_pe_203 preserve hausdorff 200.0mm). The fix emits at the RAW
axis_origin + the body-relative entry_depth_mm with neither the clamp nor the
override. Box mode (shift != identity) keeps the load-bearing 200mm clamp.

These pin the emission logic directly (no corpus body needed).
"""
from __future__ import annotations

from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
    _hole_step,
)


def _long_hole():
    # axis_origin and entry_origin deliberately differ; depth_mm is the full
    # 5080mm cylinder, entry_depth_mm the 736mm body-clipped depth.
    return {
        "type": "simple",
        "axis_origin": [10.0, 20.0, 5.0],
        "axis_dir": [0.0, 0.0, -1.0],
        "entry_origin": [10.0, 20.0, 30.0],
        "entry_depth_mm": 736.0,
        "depth_mm": 5080.0,
        "diameters_mm": [4.0],
    }


_BBOX = (-100.0, -100.0, -100.0, 100.0, 100.0, 100.0)


def test_preserve_generic_hole_uses_entry_depth_and_raw_origin():
    step = _hole_step(0, _long_hole(), None, bbox=_BBOX, shift=(0.0, 0.0, 0.0))
    assert step["skill"] == "hole"
    # body-relative entry depth, NOT the 200mm clamp, NOT the 5080mm full length
    assert step["args"]["depth_mm"] == 736.0
    # raw axis_origin — NOT moved to a bbox face by the old override
    assert step["args"]["position"] == [10.0, 20.0, 5.0]


def test_box_generic_hole_still_clamps_at_200():
    # box mode (shift != identity): the 200mm clamp stays load-bearing, and the
    # entry-origin path (shift != identity) is used, shifted into box-local frame.
    step = _hole_step(0, _long_hole(), None, bbox=_BBOX, shift=(5.0, 5.0, 5.0))
    assert step["skill"] == "hole"
    assert step["args"]["depth_mm"] == 200.0  # clamp still fires in box mode
    # entry_origin (10,20,30) shifted by (5,5,5)
    assert step["args"]["position"] == [15.0, 25.0, 35.0]


def test_preserve_short_hole_unaffected_by_clamp_change():
    # a normal short hole (depth < 200) emits its entry_depth_mm in preserve mode
    # exactly as before — the change only matters for holes the clamp would bite.
    h = _long_hole()
    h["depth_mm"] = 12.0
    h["entry_depth_mm"] = 12.0
    step = _hole_step(0, h, None, bbox=_BBOX, shift=(0.0, 0.0, 0.0))
    assert step["args"]["depth_mm"] == 12.0
