"""helix_sweep — from-scratch "sweep a closed 2D section along a HELIX" create skill.

Pins the analytic-helix sweep (threads/springs/augers/coiled tubing) alongside
sketch_sweep (line+arc spine). The spine is an analytic helix
(coil_radius, pitch, turns) about +Z; the section is any closed SketchSpec, built
in local XY and auto-oriented to the helix START tangent, then swept to a SOLID
with MakePipeShell→MakeSolid.

Volume is checked against Pappus' theorem: for a circle section of area A swept
along a helix of length L = turns·sqrt((2π·R)² + pitch²), V ≈ A·L. (Pappus is
exact only for a planar closed curve moving perpendicular to a path that does not
self-overlap; a helix has mild torsion so a 6% tolerance is honest.)

Also pins the honest guards the OCCT builder does NOT provide: a section radial
extent about its LOCAL ORIGIN ≥ coil_radius folds through the axis (structured
refusal, offset-aware — a bbox half-SIZE guard was bypassed by center_y_mm
offsets), an off-centre section falsifies the coil_radius/Pappus extras
(fm.helix_section_offcentre), pitch < the oriented section's axial extent makes
adjacent coils interpenetrate and double-counts the overlap volume
(fm.helix_pitch_overlap, only when turns ≥ 1), and is_solid is gated on
volume>0. The wire-cast fix is implicitly pinned — a bare TopoDS_Shape fed to
MakePipeShell/BRepAdaptor_CompCurve HANGS, so a passing (non-timeout) build
proves the TopoDS.Wire_s cast on BOTH the helix wire and the section wire.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.helix_sweep import HelixSweep, _helix_length


def _hx(**kw):
    return HelixSweep().apply(None, kw).extras["sketch_solid"]


# ── builds — Pappus volume ───────────────────────────────────────────────────

def test_circle_helix_sweep_matches_pappus():
    # circle d=4 (A = π·2² = 4π ≈ 12.566) swept along a helix R=10, pitch=6,
    # turns=3. L = 3·sqrt((2π·10)² + 6²) = 3·sqrt(3947.84 + 36) ≈ 188.554.
    # Pappus V ≈ A·L ≈ 12.566·188.554 ≈ 2369.4 mm³ (within 6% for a helix).
    r = _hx(section={"kind": "circle", "diameter_mm": 4},
            coil_radius_mm=10, pitch_mm=6, turns=3)
    area = math.pi * 2.0 ** 2
    length = _helix_length(10, 6, 3)
    pappus = area * length
    assert r["is_solid"] is True and r["kind"] == "helix_sweep"
    assert r["volume_mm3"] > 0
    assert r["helix_length_mm"] == pytest.approx(length, rel=1e-3)
    assert r["volume_mm3"] == pytest.approx(pappus, rel=0.06)


def test_helix_length_formula():
    # one turn, zero-in-Z would be a circle circumference; with pitch it grows.
    assert _helix_length(10, 0, 1) == pytest.approx(2 * math.pi * 10, rel=1e-9)
    assert _helix_length(10, 6, 2) == pytest.approx(
        2 * math.sqrt((2 * math.pi * 10) ** 2 + 36), rel=1e-9)


def test_helix_sweep_bbox_spans_diameter_plus_section():
    # the coil's XY footprint spans 2·(coil_radius) + section width; its Z height
    # spans (turns·pitch) + section height. R=10,d=4 → XY ≈ 24, Z(2 turns,p=8)≈20.
    r = _hx(section={"kind": "circle", "diameter_mm": 4},
            coil_radius_mm=10, pitch_mm=8, turns=2)
    bx, by, bz = r["bbox_mm"]
    assert bx == pytest.approx(24.0, abs=0.5)
    assert by == pytest.approx(24.0, abs=0.5)
    # 2 turns advance 16 in Z, plus the ~4mm section thickness (tilted a little).
    assert bz == pytest.approx(20.0, abs=1.5)
    assert r["turns"] == 2 and r["pitch_mm"] == 8


def test_rectangle_section_helix_builds():
    # a non-circular (rectangular ribbon) section — an auger/scroll flight — must
    # also build to a positive-volume solid. Section local X maps near-axially,
    # so the flight is 2mm thick axially (length) × 6mm wide radially (width);
    # the old 6×2 case had a 6mm axial extent > pitch 5 and self-intersected
    # (BOPAlgo SelfInterMode=True) — it is now the fm.helix_pitch_overlap
    # refusal pinned below.
    r = _hx(section={"kind": "rectangle", "length_mm": 2, "width_mm": 6},
            coil_radius_mm=12, pitch_mm=5, turns=2.5)
    assert r["is_solid"] and r["volume_mm3"] > 0
    assert r["section_kind"] == "rectangle"


def test_origin_translation_shifts_solid():
    r = _hx(section={"kind": "circle", "diameter_mm": 4},
            coil_radius_mm=10, pitch_mm=6, turns=1,
            origin_mm=(100.0, 0.0, 0.0))
    assert r["is_solid"] and r["origin_mm"] == [100.0, 0.0, 0.0]
    # bbox extents are translation-invariant; volume unchanged vs the un-shifted.
    base = _hx(section={"kind": "circle", "diameter_mm": 4},
               coil_radius_mm=10, pitch_mm=6, turns=1)
    assert r["volume_mm3"] == pytest.approx(base["volume_mm3"], rel=1e-6)


# ── honest refusals ──────────────────────────────────────────────────────────

def test_section_wider_than_coil_refused():
    # section d=24 (radial half-extent 12) on a coil of radius 10: the inner wall
    # folds through the axis → conservative up-front structured refusal.
    with pytest.raises(ValueError, match="helix_section_exceeds_coil"):
        _hx(section={"kind": "circle", "diameter_mm": 24},
            coil_radius_mm=10, pitch_mm=6, turns=2)


def test_offset_section_spanning_axis_refused():
    # d=4 circle offset cy=-10 on a coil of radius 10: bbox half-SIZE is only 2
    # (< 10, the old guard passed and swept 11mm³ of self-intersecting scrap),
    # but the section really spans y ∈ [-12,-8] about its local origin — the
    # wall folds through the +Z axis. The guard must measure extent about the
    # LOCAL ORIGIN (raw bbox coords), not the size.
    with pytest.raises(ValueError, match="helix_section_exceeds_coil"):
        _hx(section={"kind": "circle", "diameter_mm": 4, "center_y_mm": -10},
            coil_radius_mm=10, pitch_mm=6, turns=2)


def test_mildly_offcentre_section_refused_as_offcentre():
    # cy=+3 passes the radial-extent guard (max extent 5 < 10) but sweeps at a
    # real centreline radius of 13 while extras would claim coil_radius_mm=10 /
    # Pappus at R=10 (probe: vol 2058.6 vs claimed 1579.7). coil_radius_mm is
    # documented as the distance to the section CENTRE → honest refusal instead
    # of false metadata.
    with pytest.raises(ValueError, match="helix_section_offcentre"):
        _hx(section={"kind": "circle", "diameter_mm": 4, "center_y_mm": 3},
            coil_radius_mm=10, pitch_mm=6, turns=2)


def test_pitch_smaller_than_section_axial_extent_refused():
    # d=6 circle with pitch 2: each turn advances only 2mm but the coil tube is
    # ~6mm tall axially → adjacent coils interpenetrate; VolumeProperties
    # double-counts the overlap (probe: reported 5330mm³ exceeds the coil's own
    # enclosing annulus capacity 4523mm³; BOPAlgo self-intersection=True).
    with pytest.raises(ValueError, match="helix_pitch_overlap"):
        _hx(section={"kind": "circle", "diameter_mm": 6},
            coil_radius_mm=10, pitch_mm=2, turns=3)


def test_ribbon_axial_extent_exceeding_pitch_refused():
    # the OLD test_rectangle_section_helix_builds case: 6mm of the ribbon maps
    # axially (local X) but pitch is 5 → coils interpenetrate (probe-verified
    # self-intersecting). The measured-extent guard must refuse it while the
    # swapped 2(axial)×6(radial) flight above still builds.
    with pytest.raises(ValueError, match="helix_pitch_overlap"):
        _hx(section={"kind": "rectangle", "length_mm": 6, "width_mm": 2},
            coil_radius_mm=12, pitch_mm=5, turns=2.5)


def test_subturn_tight_pitch_still_builds():
    # turns=0.5 with pitch 2 and a d=6 tube: there is NO adjacent coil, so the
    # pitch guard must not fire — probe-verified valid solid (BOPAlgo
    # self-intersection=False, vol 888.7).
    r = _hx(section={"kind": "circle", "diameter_mm": 6},
            coil_radius_mm=10, pitch_mm=2, turns=0.5)
    assert r["is_solid"] and r["volume_mm3"] == pytest.approx(888.7, rel=0.02)


# ── discoverability ──────────────────────────────────────────────────────────

def test_helix_sweep_is_discoverable_in_the_manifest():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    assert "helix_sweep" in names
    hx = next(s for s in m["skills"] if s["name"] == "helix_sweep")
    assert hx["category"] == "create" and hx["level"] == "atomic"
