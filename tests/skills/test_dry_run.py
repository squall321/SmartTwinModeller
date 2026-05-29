"""dry_run — verify that a dry-run of extrude_pocket reports the predicted
volume_decreased change without actually pocketing the body.
"""
from __future__ import annotations

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.dry_run_skill import DryRun


def _box():
    return Box().apply(None, {
        "length_mm": 40, "width_mm": 40, "height_mm": 10,
    }).body


def _volume_mm3(body):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    shape = body.wrapped if hasattr(body, "wrapped") else body
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


# ──────────────────────────────────────────────────────────────────────────────
# core scenario — dry-run extrude_pocket on a box.


def test_dry_run_extrude_pocket_predicts_volume_decreased_and_does_not_pocket():
    box = _box()
    pre_volume = _volume_mm3(box)

    target_args = {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {
            "kind": "rect",
            "width_mm": 10.0,
            "height_mm": 10.0,
        },
        "depth_mm": 3.0,
    }

    r = DryRun().apply(box, {
        "target_skill_name": "extrude_pocket",
        "target_args": target_args,
    })

    dr = r.extras["dry_run"]

    # (c) predicted_volume_change derived from extrude_pocket's declared
    # post_conditions=[volume_decreased]
    assert dr["target_skill_name"] == "extrude_pocket"
    assert dr["predicted_volume_change"] == "decreased"
    assert dr["error"] is None

    # (b) metadata from @skill — extrude_pocket promises a volume_decreased
    # post-condition and declares history_rules for target_face/pocket_walls/
    # pocket_bottom.
    pc_kinds = {p["kind"] for p in dr["post_conditions"]}
    assert "volume_decreased" in pc_kinds
    assert "target_face" in dr["history_rules"]
    assert "pocket_walls" in dr["history_rules"]
    assert "faces" in dr["selector_kinds"]

    # (a) selector resolution — face_selector face_named=top matches 1 face.
    sel_entries = dr["matched_selectors"]
    assert len(sel_entries) == 1
    e = sel_entries[0]
    assert e["arg_path"] == "face_selector"
    assert e["kind"] == "faces"
    assert e["matched_count"] == 1
    assert e["error"] is None

    # body unchanged — the original box is echoed back, same volume.
    assert r.body is box
    assert abs(_volume_mm3(r.body) - pre_volume) < 1e-6
    # Sanity: pre_volume == 40 * 40 * 10 = 16000 mm³
    assert abs(pre_volume - 16000.0) < 1e-3


# ──────────────────────────────────────────────────────────────────────────────
# unknown skill name → error in extras, body still unchanged.


def test_dry_run_unknown_skill_reports_error_without_raising():
    box = _box()
    r = DryRun().apply(box, {
        "target_skill_name": "no_such_skill_xyz",
        "target_args": {},
    })
    dr = r.extras["dry_run"]
    assert dr["error"] is not None
    assert "no_such_skill_xyz" in dr["error"]
    # body echoed
    assert r.body is box


# ──────────────────────────────────────────────────────────────────────────────
# zero-match selector → matched_count=0, no exception.


def test_dry_run_zero_match_selector_reports_zero_count():
    box = _box()
    # bbox 가 box 밖 → edges_by_position 매칭 0
    target_args = {
        "edge_selector": {
            "kind": "edges_by_position",
            "bbox": {"min": (1000.0, 1000.0, 1000.0),
                     "max": (1100.0, 1100.0, 1100.0)},
        },
        "radius_mm": 1.0,
    }
    r = DryRun().apply(box, {
        "target_skill_name": "extrude_pocket",  # any registered skill
        "target_args": target_args,
    })
    dr = r.extras["dry_run"]
    # edge_selector (kind=edges_by_position) → edges
    sel_entries = [s for s in dr["matched_selectors"]
                   if s["arg_path"] == "edge_selector"]
    assert len(sel_entries) == 1
    assert sel_entries[0]["matched_count"] == 0
    assert sel_entries[0]["kind"] == "edges"
    assert sel_entries[0]["error"] is None
    # body unchanged
    assert r.body is box
