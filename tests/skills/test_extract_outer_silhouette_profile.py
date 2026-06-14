"""extract_outer_silhouette_profile + extrude_profile_world (PILLAR FREEFORM,
phase-1, 2026-06-14).

extract_outer_silhouette_profile detects a body's dominant prismatic axis
(constant cross-section over 25/50/75% slices) and recovers the swept OUTER
silhouette as an executable sketch + height. extrude_profile_world rebuilds that
profile into a base solid for box-mode reconstruction.

byte_identical_proof: N/A — both are NEW skills. The planner's profile base is
gated behind PlanFromFeatureCatalog base_profile_mode="auto" (default "off"), so
the committed default plan path is unchanged (the box base is emitted exactly as
before). The invariants proven here are:
  * a rounded slab is detected prismatic and its recovered profile, extruded,
    reproduces the true volume within 2% (prism volume == height · area);
  * the revert-guard primitive keeps the box when the profile is NOT strictly
    better (a plain box: profile solid == box solid, hausdorff tie -> box).
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.extrude_profile_world import (
    ExtrudeProfileWorld,
    build_profile_solid,
)
from phone_designer.skills.inspect.extract_outer_silhouette_profile import (
    ExtractOuterSilhouetteProfile,
    detect_outer_silhouette_profile,
)


# ──────────────────────────────────────────────────────────────────────────────
# fixtures / helpers


def _solid_volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


def _box(length, width, height):
    from build123d import Align
    from build123d import Box as B123dBox

    return B123dBox(length, width, height, align=(Align.CENTER, Align.CENTER, Align.MIN))


def _rounded_slab(length, width, height, corner_r):
    """L×W×H slab with the 4 vertical edges filleted to corner_r — a prismatic
    but NON-rectangular plate (the box base would clip its rounded corners)."""
    from phone_designer.skills.create.rounded_slab import RoundedSlab

    return RoundedSlab().apply(None, {
        "length_mm": length, "width_mm": width,
        "height_mm": height, "corner_r_mm": corner_r,
    }).body


def _bbox(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    return bb.Get()


def _hausdorff(a, b, deflection=0.2):
    import numpy as np

    from phone_designer.skills.inspect import geometry_deviation as gd

    gd._tessellate(a, deflection)
    gd._tessellate(b, deflection)
    av, at = gd._triangle_soup(a)
    bv, bt = gd._triangle_soup(b)
    d1, _ = gd._directed_deviation(av, gd._TriGrid(bt), bv, 20000)
    d2, _ = gd._directed_deviation(bv, gd._TriGrid(at), av, 20000)
    return float(np.concatenate([d1, d2]).max())


# ──────────────────────────────────────────────────────────────────────────────
# (1) detection — a rounded slab is prismatic; its outline area beats the bbox.


def test_rounded_slab_detected_prismatic():
    """A 60×40×10 slab with r=8 corners is prismatic along its long axis."""
    body = _rounded_slab(60.0, 40.0, 10.0, 8.0)
    prof = detect_outer_silhouette_profile(body.wrapped)

    assert prof["prismatic"] is True, prof["reason"]
    # The slab is thin (H=10) so the sweep axis is the SHORT one (Z, the 10mm
    # extrude) only if H were dominant — here X (60) is the longest extent but
    # the constant cross-section is the L×W rounded rectangle swept along Z.
    # Either way the recovered profile area must be the rounded outline, which
    # is strictly LESS than the bbox face it sweeps through (corners removed).
    assert prof["sketch"] is not None
    assert prof["axis"] in ("X", "Y", "Z")
    assert prof["area_mm2"] > 0.0


def test_rounded_slab_profile_area_below_bbox_rectangle():
    """The recovered cross-section drops the filleted corners → area < L·W."""
    L, W, H, r = 60.0, 40.0, 10.0, 8.0
    body = _rounded_slab(L, W, H, r)
    prof = detect_outer_silhouette_profile(body.wrapped)
    assert prof["prismatic"] is True, prof["reason"]

    # The four r=8 corners remove 4·(r² − π r²/4) = 4 r²(1 − π/4) of area from
    # the bounding rectangle of the swept face. The recovered area must be
    # meaningfully below the full rectangle (a box base would over-fill).
    corner_loss = 4.0 * r * r * (1.0 - math.pi / 4.0)
    # Identify which face is the constant cross-section by the axis.
    rect = {"X": W * H, "Y": L * H, "Z": L * W}[prof["axis"]]
    assert prof["area_mm2"] < rect - 0.5 * corner_loss, (prof["axis"], prof["area_mm2"], rect)


# ──────────────────────────────────────────────────────────────────────────────
# (2) extrude round-trip — recovered profile reproduces the true volume <2%.


def test_rounded_slab_profile_volume_within_2pct():
    """Recover the slab's profile, extrude it the full sweep length, and the
    prism volume matches the original slab volume within 2%."""
    L, W, H, r = 60.0, 40.0, 10.0, 8.0
    body = _rounded_slab(L, W, H, r)
    true_vol = _solid_volume(body.wrapped)

    prof = detect_outer_silhouette_profile(body.wrapped)
    assert prof["prismatic"] is True, prof["reason"]

    solid = build_profile_solid(
        prof["sketch"], prof["height_mm"], prof["axis"],
        tuple(prof["in_plane_axes"]["u"]),
        tuple(prof["section_origin_world"]),
        box_shift=(0.0, 0.0, 0.0),
    )
    prism_vol = _solid_volume(solid)
    assert math.isclose(prism_vol, true_vol, rel_tol=0.02), (prism_vol, true_vol)


def test_extrude_profile_world_skill_apply():
    """The registered create skill builds a non-null solid from recovered args."""
    body = _rounded_slab(60.0, 40.0, 10.0, 8.0)
    prof = detect_outer_silhouette_profile(body.wrapped)
    assert prof["prismatic"] is True, prof["reason"]

    out = ExtrudeProfileWorld().apply(None, {
        "sketch": prof["sketch"],
        "height_mm": prof["height_mm"],
        "axis": prof["axis"],
        "u_axis": prof["in_plane_axes"]["u"],
        "section_origin_world": prof["section_origin_world"],
    })
    assert out.body is not None
    assert _solid_volume(out.body.wrapped) > 0.0


def test_inspect_skill_apply_emits_base_profile():
    """The @skill apply() surfaces extras['base_profile'] and leaves body alone."""
    body = _rounded_slab(60.0, 40.0, 10.0, 8.0)
    res = ExtractOuterSilhouetteProfile().apply(body, {})
    prof = res.extras["base_profile"]
    assert prof["prismatic"] is True, prof["reason"]
    assert res.body is body  # read-only


# ──────────────────────────────────────────────────────────────────────────────
# (3) revert-guard — a plain box's profile is NOT strictly better → keep box.


def test_revert_guard_keeps_box_for_plain_box():
    """For a plain rectangular box the recovered profile reproduces the box
    EXACTLY (hausdorff ~0, a tie), so the strict-better revert-guard keeps the
    box base — the feature cannot regress a body the box already fits."""
    L, W, H = 50.0, 30.0, 20.0
    body = _box(L, W, H)
    bb = _bbox(body.wrapped)
    cx = (bb[0] + bb[3]) / 2.0
    cy = (bb[1] + bb[4]) / 2.0
    zmin = bb[2]
    shift = (-cx, -cy, -zmin)

    prof = detect_outer_silhouette_profile(body.wrapped)
    assert prof["prismatic"] is True, prof["reason"]

    prof_solid = build_profile_solid(
        prof["sketch"], prof["height_mm"], prof["axis"],
        tuple(prof["in_plane_axes"]["u"]),
        tuple(prof["section_origin_world"]),
        box_shift=shift,
    )
    box_solid = _box(L, W, H).wrapped
    from phone_designer.skills.inspect import geometry_deviation as gd
    orig_box = gd._translate(body.wrapped, *shift)

    h_prof = _hausdorff(prof_solid, orig_box)
    h_box = _hausdorff(box_solid, orig_box)

    # The profile is at best a tie with the box (both reproduce the box). The
    # strict-better rule (h_prof < h_box) must therefore be FALSE → revert.
    assert not (h_prof < h_box), (h_prof, h_box)


def test_near_cubic_body_not_prismatic():
    """A near-cubic body has no meaningful sweep axis (aspect gate) → not
    prismatic → the planner keeps the box base."""
    body = _box(20.0, 20.0, 19.0)
    prof = detect_outer_silhouette_profile(body.wrapped)
    assert prof["prismatic"] is False
    assert "low_aspect" in (prof["reason"] or "")
