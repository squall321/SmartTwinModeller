"""Tests for mesh I/O (stl_import, stl_export) and lattice skills
(voronoi_lattice, gyroid_lattice)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.create.gyroid_lattice import GyroidLattice
from phone_designer.skills.create.voronoi_lattice import VoronoiLattice
from phone_designer.skills.inspect.inspect_geometry import InspectGeometry
from phone_designer.skills.io.stl_export import StlExport
from phone_designer.skills.io.stl_import import StlImport


# -------------------------- helpers -----------------------------------------

def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    shape = body.wrapped if hasattr(body, "wrapped") else body
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


def _bbox(body):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    shape = body.wrapped if hasattr(body, "wrapped") else body
    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return (xmin, ymin, zmin), (xmax, ymax, zmax)


# -------------------------- STL roundtrip -----------------------------------

def test_stl_export_writes_file_and_returns_body_unchanged(tmp_path):
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    out = tmp_path / "box.stl"

    r = StlExport().apply(box, {"path": str(out),
                                 "linear_deflection_mm": 0.1,
                                 "angular_deflection_deg": 5.0})

    assert out.exists(), "STL file should be written"
    assert out.stat().st_size > 0
    assert r.extras["written_path"] == str(out)
    assert r.extras["triangle_count"] > 0
    # Body unchanged.
    assert abs(_volume(r.body) - 1000.0) < 1.0


def test_stl_import_loads_file_and_produces_body(tmp_path):
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 20}).body
    out = tmp_path / "box.stl"
    StlExport().apply(box, {"path": str(out)})

    r = StlImport().apply(None, {"path": str(out)})
    assert r.body is not None
    # The mesh import yields per-triangle faces — at least the 12 triangles
    # of a cube tessellation.
    rep = InspectGeometry().apply(r.body, {}).extras["inspection_report"]
    assert rep["face_count"] >= 12


def test_stl_import_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        StlImport().apply(None, {"path": "/nonexistent/does/not/exist.stl"})


def test_stl_roundtrip_volume_within_2_percent(tmp_path):
    """Export a box, re-import, and verify bbox-derived volume is within 2%."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 20, "height_mm": 10}).body
    out = tmp_path / "roundtrip.stl"
    StlExport().apply(box, {"path": str(out), "linear_deflection_mm": 0.05})

    r = StlImport().apply(None, {"path": str(out)})
    # Imported STL is an open shell (not a solid) — VolumeProperties returns 0
    # or a signed estimate. Use bbox volume as the comparable measure.
    (mn1, mx1) = _bbox(box)
    (mn2, mx2) = _bbox(r.body)
    v1 = (mx1[0] - mn1[0]) * (mx1[1] - mn1[1]) * (mx1[2] - mn1[2])
    v2 = (mx2[0] - mn2[0]) * (mx2[1] - mn2[1]) * (mx2[2] - mn2[2])
    assert v1 > 0 and v2 > 0
    rel_err = abs(v1 - v2) / v1
    assert rel_err <= 0.02, f"roundtrip bbox volume diff {rel_err:.3%} > 2%"


# -------------------------- Voronoi lattice ---------------------------------

def test_voronoi_lattice_produces_positive_volume():
    r = VoronoiLattice().apply(None, {
        "bbox_min": (0.0, 0.0, 0.0),
        "bbox_max": (20.0, 20.0, 20.0),
        "n_seeds": 12,
        "wall_thickness_mm": 1.0,
        "seed": 7,
    })
    assert r.body is not None
    vol = _volume(r.body)
    assert vol > 0.0, f"voronoi lattice volume must be positive, got {vol}"
    assert r.extras["strut_count"] > 0


def test_voronoi_lattice_bbox_inside_request():
    """The strut network's bbox should fit inside (or close to) the requested bbox."""
    bbox_min = (0.0, 0.0, 0.0)
    bbox_max = (30.0, 30.0, 30.0)
    r = VoronoiLattice().apply(None, {
        "bbox_min": bbox_min,
        "bbox_max": bbox_max,
        "n_seeds": 15,
        "wall_thickness_mm": 0.8,
        "seed": 11,
    })
    (mn, mx) = _bbox(r.body)
    margin = 1.0  # tolerate cylinder caps poking ~radius past bbox
    assert mn[0] >= bbox_min[0] - margin
    assert mn[1] >= bbox_min[1] - margin
    assert mn[2] >= bbox_min[2] - margin
    assert mx[0] <= bbox_max[0] + margin
    assert mx[1] <= bbox_max[1] + margin
    assert mx[2] <= bbox_max[2] + margin


def test_voronoi_lattice_reproducible_with_same_seed():
    args = {
        "bbox_min": (0.0, 0.0, 0.0),
        "bbox_max": (15.0, 15.0, 15.0),
        "n_seeds": 10,
        "wall_thickness_mm": 0.8,
        "seed": 99,
    }
    a = VoronoiLattice().apply(None, args)
    b = VoronoiLattice().apply(None, args)
    assert a.extras["strut_count"] == b.extras["strut_count"]
    assert abs(_volume(a.body) - _volume(b.body)) < 1e-3


def test_voronoi_lattice_rejects_invalid_bbox():
    with pytest.raises(Exception):
        VoronoiLattice().apply(None, {
            "bbox_min": (10.0, 0.0, 0.0),
            "bbox_max": (0.0, 10.0, 10.0),  # x flipped
            "n_seeds": 8,
            "wall_thickness_mm": 1.0,
        })


# -------------------------- Gyroid lattice ----------------------------------

def test_gyroid_lattice_produces_positive_volume():
    r = GyroidLattice().apply(None, {
        "bbox_min": (0.0, 0.0, 0.0),
        "bbox_max": (10.0, 10.0, 10.0),
        "period_mm": 5.0,
        "wall_thickness_mm": 0.5,
        "resolution": 12,
    })
    assert r.body is not None
    vol = _volume(r.body)
    assert vol > 0.0, f"gyroid lattice volume must be positive, got {vol}"
    assert r.extras["bead_count"] > 0


def test_gyroid_lattice_bbox_close_to_request():
    bbox_min = (0.0, 0.0, 0.0)
    bbox_max = (20.0, 20.0, 20.0)
    r = GyroidLattice().apply(None, {
        "bbox_min": bbox_min,
        "bbox_max": bbox_max,
        "period_mm": 8.0,
        "wall_thickness_mm": 0.6,
        "resolution": 14,
    })
    (mn, mx) = _bbox(r.body)
    # Beads have wall_thickness/2 radius; allow that as margin.
    margin = 1.0
    assert mn[0] >= bbox_min[0] - margin
    assert mx[0] <= bbox_max[0] + margin
    assert mn[1] >= bbox_min[1] - margin
    assert mx[1] <= bbox_max[1] + margin
    assert mn[2] >= bbox_min[2] - margin
    assert mx[2] <= bbox_max[2] + margin


def test_gyroid_lattice_rejects_invalid_period():
    with pytest.raises(Exception):
        GyroidLattice().apply(None, {
            "bbox_min": (0.0, 0.0, 0.0),
            "bbox_max": (10.0, 10.0, 10.0),
            "period_mm": -1.0,
            "wall_thickness_mm": 0.5,
        })


# -------------------------- Spec sanity --------------------------------------

def test_skill_specs_registered():
    assert StlImport.spec.name == "stl_import"
    assert StlImport.spec.category == "create"
    assert StlExport.spec.name == "stl_export"
    assert StlExport.spec.category == "inspect"
    assert VoronoiLattice.spec.name == "voronoi_lattice"
    assert VoronoiLattice.spec.level == "macro"
    assert GyroidLattice.spec.name == "gyroid_lattice"
    assert GyroidLattice.spec.level == "macro"
