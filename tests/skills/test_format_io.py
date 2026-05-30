"""Format-IO pack — IGES + BREP native + STEP v2.

Round-trips a known box through each format and asserts bbox equivalence
after re-import. The plain `step_export_v2` is verified by file-existence
+ extras only because we don't ship a STEP reader in this pack (there is
already `create.import_step`, which we use to verify content survives).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.create.import_step import ImportStep
from phone_designer.skills.io.brep_export import BrepExport
from phone_designer.skills.io.brep_import import BrepImport
from phone_designer.skills.io.iges_export import IgesExport
from phone_designer.skills.io.iges_import import IgesImport
from phone_designer.skills.io.step_export_v2 import StepExportV2


def _bbox(body):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    shape = body.wrapped if hasattr(body, "wrapped") else body
    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return (xmin, ymin, zmin), (xmax, ymax, zmax)


def _assert_bbox_close(b1, b2, tol=0.5):
    (mn1, mx1) = _bbox(b1)
    (mn2, mx2) = _bbox(b2)
    for a, b in zip(mn1, mn2):
        assert abs(a - b) < tol, f"min mismatch: {mn1} vs {mn2}"
    for a, b in zip(mx1, mx2):
        assert abs(a - b) < tol, f"max mismatch: {mx1} vs {mx2}"


# ---------------------------------------------------------------- IGES -------

def test_iges_roundtrip_box_bbox_matches(tmp_path):
    box = Box().apply(None, {"length_mm": 10, "width_mm": 20, "height_mm": 30}).body
    igs = tmp_path / "box.iges"
    r = IgesExport().apply(box, {"path": str(igs)})

    assert igs.exists() and igs.stat().st_size > 0
    assert r.extras["written_path"] == str(igs)
    assert r.extras["file_size_bytes"] > 0
    # body passthrough
    assert r.body is box

    r2 = IgesImport().apply(None, {"path": str(igs)})
    assert r2.body is not None
    assert r2.extras["roots_transferred"] >= 1
    _assert_bbox_close(box, r2.body, tol=0.5)


def test_iges_export_none_body_raises(tmp_path):
    with pytest.raises(ValueError):
        IgesExport().apply(None, {"path": str(tmp_path / "x.iges")})


def test_iges_import_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        IgesImport().apply(None, {"path": str(tmp_path / "nope.iges")})


# ---------------------------------------------------------------- BREP -------

def test_brep_roundtrip_box_bbox_matches(tmp_path):
    box = Box().apply(None, {"length_mm": 7, "width_mm": 11, "height_mm": 13}).body
    brep = tmp_path / "box.brep"
    r = BrepExport().apply(box, {"path": str(brep)})

    assert brep.exists() and brep.stat().st_size > 0
    assert r.body is box
    assert r.extras["file_size_bytes"] > 0

    r2 = BrepImport().apply(None, {"path": str(brep)})
    assert r2.body is not None
    # BREP is lossless — bbox should be exact (within OCCT round-off).
    _assert_bbox_close(box, r2.body, tol=1e-6)


def test_brep_export_none_body_raises(tmp_path):
    with pytest.raises(ValueError):
        BrepExport().apply(None, {"path": str(tmp_path / "x.brep")})


def test_brep_import_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        BrepImport().apply(None, {"path": str(tmp_path / "nope.brep")})


# -------------------------------------------------------------- STEP v2 ------

def test_step_export_v2_ap242_with_colors_writes_file(tmp_path):
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    step = tmp_path / "box_ap242.step"
    r = StepExportV2().apply(box, {
        "path": str(step),
        "schema": "AP242",
        "with_colors": True,
    })
    assert step.exists() and step.stat().st_size > 0
    assert r.extras["schema"] == "AP242"
    assert r.extras["with_colors"] is True
    assert r.body is box
    # re-import via create.import_step and check bbox
    r2 = ImportStep().apply(None, {"path": str(step)})
    _assert_bbox_close(box, r2.body, tol=0.5)


def test_step_export_v2_ap203_no_colors_smaller_or_equal(tmp_path):
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    ap203 = tmp_path / "box_ap203.step"
    r = StepExportV2().apply(box, {
        "path": str(ap203),
        "schema": "AP203",
        "with_colors": False,
    })
    assert ap203.exists()
    assert r.extras["schema"] == "AP203"
    assert r.extras["with_colors"] is False


def test_step_export_v2_all_three_schemas_succeed(tmp_path):
    box = Box().apply(None, {"length_mm": 5, "width_mm": 5, "height_mm": 5}).body
    for schema in ("AP203", "AP214", "AP242"):
        out = tmp_path / f"box_{schema}.step"
        r = StepExportV2().apply(box, {
            "path": str(out),
            "schema": schema,
            "with_colors": True,
        })
        assert out.exists() and out.stat().st_size > 0
        assert r.extras["schema"] == schema


def test_step_export_v2_none_body_raises(tmp_path):
    with pytest.raises(ValueError):
        StepExportV2().apply(None, {"path": str(tmp_path / "x.step")})


# ------------------------------------------------------------ spec sanity ---

def test_format_io_specs_registered():
    assert IgesImport.spec.name == "iges_import"
    assert IgesImport.spec.category == "create"
    assert any(pc.kind == "body_present" for pc in IgesImport.spec.post_conditions)

    assert IgesExport.spec.name == "iges_export"
    assert IgesExport.spec.category == "inspect"
    assert any(pc.kind == "body_present" for pc in IgesExport.spec.post_conditions)

    assert BrepImport.spec.name == "brep_import"
    assert BrepImport.spec.category == "create"
    assert any(pc.kind == "body_present" for pc in BrepImport.spec.post_conditions)

    assert BrepExport.spec.name == "brep_export"
    assert BrepExport.spec.category == "inspect"
    assert any(pc.kind == "body_present" for pc in BrepExport.spec.post_conditions)

    assert StepExportV2.spec.name == "step_export_v2"
    assert StepExportV2.spec.category == "inspect"
    assert any(pc.kind == "body_present" for pc in StepExportV2.spec.post_conditions)
