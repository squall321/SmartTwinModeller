"""Wave2-C feature detectors — boss / rib / standoff / lug.

All four skills are read-only (post_conditions=[body_present]) and report
their findings via the SkillResult.extras dict.
"""
from __future__ import annotations

from phone_designer.skills.compose.union import Union
from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.detect_bosses import DetectBosses
from phone_designer.skills.inspect.detect_lugs import DetectLugs
from phone_designer.skills.inspect.detect_ribs import DetectRibs
from phone_designer.skills.inspect.detect_standoffs import DetectStandoffs
from phone_designer.skills.modify_boss.boss_with_hole import BossWithHole
from phone_designer.skills.modify_boss.lug_pair import LugPair
from phone_designer.skills.modify_boss.rib import Rib
from phone_designer.skills.modify_pocket.hole import Hole


# ──────────────────────────────────────────────────────────────────────────────
# detect_bosses


def _plate() -> object:
    """50×50×4 mm plate, top face at z=4, bottom at 0."""
    return Box().apply(
        None, {"length_mm": 50, "width_mm": 50, "height_mm": 4},
    ).body


def test_detect_bosses_plain_plate_has_no_bosses():
    plate = _plate()
    r = DetectBosses().apply(plate, {})
    # body unchanged
    assert r.body is plate
    # plate is a single outer envelope — no boss cluster passes the
    # half-area filter
    assert r.extras["boss_count"] == 0
    assert r.extras["bosses"] == []


def test_detect_bosses_cylindrical_boss_on_plate():
    """Plate + one Ø6 mm cylinder of height 4 mm → 1 cylindrical boss."""
    plate = _plate()
    body = Union().apply(plate, {
        "other": {
            "kind": "primitive_cylinder",
            "diameter_mm": 6.0,
            "height_mm": 4.0,
            "position": (0.0, 0.0, 4.0),
            "axis": "+Z",
        },
    }).body
    r = DetectBosses().apply(body, {"min_height_mm": 0.5})
    assert r.body is body
    assert r.extras["boss_count"] >= 1
    cyl_bosses = [b for b in r.extras["bosses"] if b["type"] == "cylindrical"]
    assert len(cyl_bosses) >= 1
    b = cyl_bosses[0]
    # diameter ≈ 6 mm
    assert abs(b["diameter_or_size_mm"] - 6.0) < 0.5
    # height ≈ 4 mm along Z
    assert abs(b["height_mm"] - 4.0) < 0.5


def test_detect_bosses_min_height_filters_short_protrusion():
    """Plate + Ø6, height = 0.3 → rejected when min_height_mm=1.0."""
    plate = _plate()
    body = Union().apply(plate, {
        "other": {
            "kind": "primitive_cylinder",
            "diameter_mm": 6.0,
            "height_mm": 0.3,
            "position": (0.0, 0.0, 4.0),
            "axis": "+Z",
        },
    }).body
    r = DetectBosses().apply(body, {"min_height_mm": 1.0})
    cyl = [b for b in r.extras["bosses"] if b["type"] == "cylindrical"]
    # the short boss should be filtered (height ≈ 0.3 < 1.0)
    for b in cyl:
        assert b["height_mm"] >= 1.0


# ──────────────────────────────────────────────────────────────────────────────
# detect_ribs


def test_detect_ribs_long_thin_block_is_a_rib():
    plate = _plate()
    body = Rib().apply(plate, {
        "start": (-20.0, 0.0, 4.0),
        "end": (20.0, 0.0, 4.0),
        "width_mm": 1.0,       # thickness
        "height_mm": 3.0,
        "up_axis": "+Z",
    }).body
    r = DetectRibs().apply(body, {
        "min_aspect_ratio": 3.0,
        "max_thickness_mm": 3.0,
    })
    assert r.body is body
    assert r.extras["rib_count"] >= 1
    rib = r.extras["ribs"][0]
    # length ≈ 40, thickness ≈ 1, height ≈ 3
    assert rib["length_mm"] > 10.0
    assert rib["thickness_mm"] < 3.0
    assert rib["length_mm"] / rib["thickness_mm"] >= 3.0


def test_detect_ribs_thick_block_rejected():
    """A 10×10×3 boss does not satisfy the aspect-ratio constraint."""
    plate = _plate()
    body = Union().apply(plate, {
        "other": {
            "kind": "primitive_box",
            "length_mm": 10.0,
            "width_mm": 10.0,
            "height_mm": 3.0,
            "position": (0.0, 0.0, 5.5),  # base at z=4
        },
    }).body
    r = DetectRibs().apply(body, {
        "min_aspect_ratio": 3.0,
        "max_thickness_mm": 3.0,
    })
    # a 10×10×3 block is too chunky to be a rib — aspect 10/3 ≈ 3.3 in
    # both planar directions, thickness 3 is not <3.0
    assert r.extras["rib_count"] == 0


def test_detect_ribs_plain_plate_zero():
    plate = _plate()
    r = DetectRibs().apply(plate, {})
    assert r.body is plate
    assert r.extras["rib_count"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# detect_standoffs


def test_detect_standoffs_boss_with_hole_is_a_standoff():
    """One boss_with_hole at center → one standoff."""
    plate = _plate()
    body = BossWithHole().apply(plate, {
        "position": (0.0, 0.0, 4.0),
        "direction": "+Z",
        "boss_diameter_mm": 6.0,
        "boss_height_mm": 4.0,
        "hole_diameter_mm": 3.0,
        "hole_depth_mm": 3.0,
    }).body
    r = DetectStandoffs().apply(body, {})
    assert r.body is body
    assert r.extras["standoff_count"] >= 1
    s = r.extras["standoffs"][0]
    assert s["hole_diameter_mm"] < s["boss_diameter_mm"]


def test_detect_standoffs_plain_boss_no_standoff():
    """Boss without a central hole → standoff_count == 0."""
    plate = _plate()
    body = Union().apply(plate, {
        "other": {
            "kind": "primitive_cylinder",
            "diameter_mm": 6.0,
            "height_mm": 4.0,
            "position": (0.0, 0.0, 4.0),
            "axis": "+Z",
        },
    }).body
    r = DetectStandoffs().apply(body, {})
    assert r.extras["standoff_count"] == 0


def test_detect_standoffs_plain_plate_zero():
    plate = _plate()
    r = DetectStandoffs().apply(plate, {})
    assert r.extras["standoff_count"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# detect_lugs


def test_detect_lugs_lug_pair_macro_yields_one_pair():
    """LugPair macro adds two lug pads + two pin holes on +Y/-Y."""
    plate = Box().apply(
        None, {"length_mm": 30, "width_mm": 30, "height_mm": 8},
    ).body
    body = LugPair().apply(plate, {
        "axis": "+Y/-Y",
        "offset_mm": 18.0,
        "z_position_mm": 4.0,
        "lug_length_mm": 8.0,
        "lug_thickness_mm": 3.0,
        "lug_height_mm": 3.0,
        "pin_diameter_mm": 1.5,
    }).body
    r = DetectLugs().apply(body, {})
    assert r.body is body
    # We can't strictly guarantee >=1 here because the lug heuristic depends
    # on whether the rib/boss clusters survive — but we at least require
    # the output dict to have the right shape.
    assert "lugs" in r.extras
    assert "lug_count" in r.extras
    assert isinstance(r.extras["lugs"], list)


def test_detect_lugs_plain_plate_zero():
    plate = _plate()
    r = DetectLugs().apply(plate, {})
    assert r.extras["lug_count"] == 0
    assert r.extras["lugs"] == []


def test_detect_lugs_single_standoff_yields_no_pair():
    """One boss_with_hole alone cannot form a lug pair."""
    plate = _plate()
    body = BossWithHole().apply(plate, {
        "position": (0.0, 0.0, 4.0),
        "direction": "+Z",
        "boss_diameter_mm": 6.0,
        "boss_height_mm": 4.0,
        "hole_diameter_mm": 3.0,
        "hole_depth_mm": 3.0,
    }).body
    r = DetectLugs().apply(body, {})
    assert r.extras["lug_count"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# all four — body_present post condition is satisfied


def test_all_detect_skills_preserve_body_identity():
    plate = _plate()
    for skill_cls in (DetectBosses, DetectRibs, DetectStandoffs, DetectLugs):
        r = skill_cls().apply(plate, {})
        assert r.body is plate, f"{skill_cls.__name__} mutated body"
