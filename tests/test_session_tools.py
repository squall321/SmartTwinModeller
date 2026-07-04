"""Track 1-1 — mcp_support/_session_tools session primitives.

Covers the four body-object primitives the MCP session layer wires up:

    import_body   — STEP roundtrip metadata, oversize guard, format/file refusals
    modify_body   — hole spec shrinks volume, fail-fast unknown op, bad specs,
                    per-step failure isolation with raw error strings
    measure_body  — mass/obb/dimensions match the direct inspect-skill calls
    preview_body  — headless honest-skip contract (never fake images)

All returned metadata must be STRICT-JSON-safe: json.dumps(..., allow_nan=False).
Run with PHONE_DESIGNER_UI_HEADLESS=1 (the preview test also pins it itself).
"""
from __future__ import annotations

import json
import pathlib

import pytest

from phone_designer.mcp_support._session_tools import (
    import_body,
    measure_body,
    modify_body,
    preview_body,
)
from phone_designer.skills.create.box import Box

BOX_L, BOX_W, BOX_H = 40.0, 30.0, 10.0
BOX_VOLUME = BOX_L * BOX_W * BOX_H  # 12000 mm³


@pytest.fixture()
def box_body():
    """40×30×10 box body (z ∈ [0, 10], centered in XY)."""
    return Box().apply(None, {
        "length_mm": BOX_L, "width_mm": BOX_W, "height_mm": BOX_H,
    }).body


def _write_step(body, path: pathlib.Path) -> None:
    """Write a STEP file via OCCT directly (same as test_plan_reconstruction)."""
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    shape = body.wrapped if hasattr(body, "wrapped") else body
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    writer.Write(str(path))
    assert path.exists()


def _assert_json_safe(d: dict) -> None:
    json.dumps(d, allow_nan=False)  # raises on nan/inf or non-JSON types


# ──────────────────────────────────────────────────────────────────────────────
# import_body


def test_import_body_box_roundtrip(box_body, tmp_path):
    step = tmp_path / "box.step"
    _write_step(box_body, step)

    meta = import_body(str(step))
    assert meta["body"] is not None
    assert meta["step_path"] == str(step)
    assert meta["n_faces"] == 6
    assert meta["is_solid"] is True
    assert meta["volume_mm3"] == pytest.approx(BOX_VOLUME, rel=1e-4)
    assert meta["bbox_mm"] == pytest.approx([BOX_L, BOX_W, BOX_H], abs=1e-3)
    # STRICT-JSON-safe once the body object is stripped.
    _assert_json_safe({k: v for k, v in meta.items() if k != "body"})


def test_import_body_oversize_guard_fires(box_body, tmp_path):
    step = tmp_path / "box.step"
    _write_step(box_body, step)

    with pytest.raises(ValueError, match="fm.too_many_faces") as ei:
        import_body(str(step), max_faces=1)  # a 6-face box trips a limit of 1
    # the refusal must point at the assembly-scale front doors
    assert "assembly_reverse_engineer" in str(ei.value)
    assert "cad_analyze" in str(ei.value)


def test_import_body_refuses_non_step_format(tmp_path):
    iges = tmp_path / "part.iges"
    iges.write_text("not really iges")  # exists, but wrong format
    with pytest.raises(ValueError, match="fm.unsupported_format"):
        import_body(str(iges))


def test_import_body_missing_file(tmp_path):
    with pytest.raises(ValueError, match="fm.file_not_found"):
        import_body(str(tmp_path / "nope.step"))


def test_import_body_parse_failure(tmp_path):
    bad = tmp_path / "garbage.step"
    bad.write_text("this is not a STEP file")
    with pytest.raises(ValueError, match="fm.step_parse_failed"):
        import_body(str(bad))


# ──────────────────────────────────────────────────────────────────────────────
# modify_body


def test_modify_body_hole_decreases_volume(box_body):
    out = modify_body(box_body, [{
        "op": "hole",
        "args": {"position": (0.0, 0.0, BOX_H), "diameter_mm": 6.0,
                 "depth_mm": 5.0, "direction": "-Z"},
    }])
    report = out["report"]
    assert out["body"] is not None
    assert report["n_steps"] == 1
    assert report["n_ok"] == 1
    assert report["ok"] is True
    assert report["steps"][0]["op"] == "hole"
    assert report["steps"][0]["status"] == "pass"
    assert report["steps"][0]["error"] is None
    assert report["is_solid"] is True
    # a Ø6×5 hole removes ~141.37 mm³
    assert report["volume_mm3"] < BOX_VOLUME
    assert report["volume_mm3"] == pytest.approx(
        BOX_VOLUME - 3.141592653589793 * 9.0 * 5.0, rel=1e-3)
    assert report["bbox_mm"] == pytest.approx([BOX_L, BOX_W, BOX_H], abs=1e-3)
    _assert_json_safe(report)


def test_modify_body_unknown_op_fails_fast(box_body):
    with pytest.raises(ValueError, match="fm.unknown_op") as ei:
        modify_body(box_body, [
            {"op": "definitely_not_a_registered_skill", "args": {}},
        ])
    assert "definitely_not_a_registered_skill" in str(ei.value)


def test_modify_body_bad_spec_refusals(box_body):
    with pytest.raises(ValueError, match="fm.bad_spec"):
        modify_body(box_body, [])                       # empty spec
    with pytest.raises(ValueError, match="fm.bad_spec"):
        modify_body(box_body, ["hole"])                 # step not an object
    with pytest.raises(ValueError, match="fm.bad_spec"):
        modify_body(box_body, [{"args": {}}])           # missing op
    with pytest.raises(ValueError, match="fm.bad_spec"):
        modify_body(box_body, [{"op": "hole", "args": "d=6"}])  # args not dict
    with pytest.raises(ValueError, match="fm.empty_body"):
        modify_body(None, [{"op": "hole", "args": {}}])  # no body


def test_modify_body_failing_step_isolated_with_raw_error(box_body):
    # diameter_mm=-5 passes the registry pre-check (op exists) but fails the
    # skill's own Args validation INSIDE the executor — the failure must be
    # recorded per-step with the RAW error string, not raised and not masked.
    out = modify_body(box_body, [{
        "op": "hole",
        "args": {"position": (0.0, 0.0, BOX_H), "diameter_mm": -5.0,
                 "depth_mm": 5.0, "direction": "-Z"},
    }])
    report = out["report"]
    assert report["n_ok"] == 0
    assert report["ok"] is False
    assert report["steps"][0]["status"] != "pass"
    assert report["steps"][0]["error"]  # raw message present, never masked
    # body falls back to the input — volume unchanged
    assert report["volume_mm3"] == pytest.approx(BOX_VOLUME, rel=1e-4)
    _assert_json_safe(report)


# ──────────────────────────────────────────────────────────────────────────────
# measure_body


def test_measure_mass_matches_direct_skill(box_body):
    from phone_designer.skills.inspect.mass_properties import MassProperties

    got = measure_body(box_body, "mass")
    direct = MassProperties().apply(box_body, {}).extras
    assert got["what"] == "mass"
    assert got["volume_mm3"] == direct["volume_mm3"]
    assert got["centroid"] == list(direct["centroid"])
    assert got["mass_g"] == direct["mass_g"]
    _assert_json_safe(got)


def test_measure_obb_matches_direct_skill(box_body):
    from phone_designer.skills.inspect.oriented_bounding_box import (
        OrientedBoundingBox,
    )

    got = measure_body(box_body, "obb")
    direct = OrientedBoundingBox().apply(box_body, {}).extras
    assert got["what"] == "obb"
    assert got["obb"]["size_mm"] == list(direct["obb"]["size_mm"])
    assert got["obb"]["volume_mm3"] == direct["obb"]["volume_mm3"]
    assert got["aabb"]["size_mm"] == list(direct["aabb"]["size_mm"])
    # the box's OBB extents are the box dims (any axis order)
    assert sorted(got["obb"]["size_mm"]) == pytest.approx(
        sorted([BOX_L, BOX_W, BOX_H]), abs=1e-2)
    _assert_json_safe(got)


def test_measure_dimensions_defaults(box_body):
    got = measure_body(box_body, "dimensions")
    assert got["what"] == "dimensions"
    dims = {d["name"]: d["value"] for d in got["key_dimensions"]}
    assert dims["overall_length_mm"] == pytest.approx(BOX_L, abs=1e-3)
    assert dims["overall_width_mm"] == pytest.approx(BOX_W, abs=1e-3)
    assert dims["overall_height_mm"] == pytest.approx(BOX_H, abs=1e-3)
    _assert_json_safe(got)


def test_measure_unknown_what_refused(box_body):
    with pytest.raises(ValueError, match="fm.unknown_measure"):
        measure_body(box_body, "surface_area")
    with pytest.raises(ValueError, match="fm.empty_body"):
        measure_body(None, "mass")


# ──────────────────────────────────────────────────────────────────────────────
# preview_body
#
# NOTE: preview_body used to return an honest SKIP marker under
# PHONE_DESIGNER_UI_HEADLESS (skipped=True, 'skipped_no_gl' in the note, all
# images None). That behaviour is INTENTIONALLY REPLACED: preview_body now
# wires the GL-free headless raster renderer (inspect/_render_headless), which
# rasterizes real shaded PNGs with a numpy z-buffer + Pillow and needs no GL.
# So the tests below assert the REAL-RENDER contract even under
# PHONE_DESIGNER_UI_HEADLESS=1 (skipped=False, images are real >2 KB PNGs).


def test_preview_headless_renders_real_pngs(box_body, tmp_path, monkeypatch):
    # Was test_preview_headless_returns_honest_skip. Under headless we now get
    # REAL renders, not a 'skipped_no_gl' marker — the old skip is replaced.
    monkeypatch.setenv("PHONE_DESIGNER_UI_HEADLESS", "1")
    out_dir = tmp_path / "previews"

    r = preview_body(box_body, str(out_dir))
    assert r["skipped"] is False                       # no longer a GL skip
    assert r["renderer"] == "headless_raster"          # renderer marker present
    assert "skipped_no_gl" not in r["note"]
    assert set(r["images"]) == {"iso", "front", "top"}
    # every requested view is a real shaded PNG on disk, > 2 KB (not black/fake)
    for view, png in r["images"].items():
        assert png is not None, f"{view} failed to render"
        p = pathlib.Path(png)
        assert p.exists() and p.suffix == ".png"
        assert p.stat().st_size > 2048, f"{view} png too small ({p.stat().st_size} B)"
    # the iso view specifically is a real file > 2 KB
    assert pathlib.Path(r["images"]["iso"]).stat().st_size > 2048
    _assert_json_safe(r)


def test_preview_none_body_honest_note_no_fake_png(tmp_path):
    # A None body has nothing to render: every image None with an honest note,
    # and NO png written. skipped stays False (this is not a GL skip).
    out_dir = tmp_path / "previews"
    r = preview_body(None, str(out_dir))
    assert r["skipped"] is False
    assert all(v is None for v in r["images"].values())
    assert "empty" in r["note"]                        # honest reason
    # never a fake/black placeholder PNG when there is nothing to render
    assert not (out_dir.exists() and list(out_dir.glob("*.png")))
    _assert_json_safe(r)


def test_preview_empty_geometry_body_honest_none(tmp_path):
    # A valid shape with NO faces (empty compound) → images None with a
    # per-view reason in the note, and no fake PNG — the honest-failure path.
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    comp = TopoDS_Compound()
    BRep_Builder().MakeCompound(comp)
    out_dir = tmp_path / "previews"

    r = preview_body(comp, str(out_dir), views=("iso", "front"))
    assert set(r["images"]) == {"iso", "front"}
    assert all(v is None for v in r["images"].values())
    assert "empty" in r["note"]                        # honest per-view reason
    assert not (out_dir.exists() and list(out_dir.glob("*.png")))
    _assert_json_safe(r)


def test_preview_side_alias_and_view_keys_preserved(box_body, tmp_path):
    # Legacy 'side' alias maps to the renderer's 'right' view but the returned
    # dict keeps the caller-requested key 'side'. All requested views render.
    out_dir = tmp_path / "previews"
    r = preview_body(box_body, str(out_dir), views=("iso", "side", "left"))
    assert set(r["images"]) == {"iso", "side", "left"}
    assert r["skipped"] is False
    for view, png in r["images"].items():
        assert png is not None, f"{view} failed to render"
        assert pathlib.Path(png).stat().st_size > 2048
    _assert_json_safe(r)
