"""_render_headless depth cue — coplanar-normal surfaces at different depths
must be visibly distinguishable (task pins).

The bug: flat Lambert gave the gearbox housing's top flange and its cavity
floor (both +Z normals) the IDENTICAL color — an open cavity was
indistinguishable from a solid top in the PNG. In iso it was even worse: EVERY
axis-aligned face ties at |n·(1,1,1)|/√3, so the whole part rendered one flat
color.

The fix under test (two deterministic, pure-numpy terms):
  * per-face recession — d = n·(centroid − center) is constant across a planar
    face (flat plate stays uniform) yet separates parallel/recessed faces;
  * gentle per-pixel z-buffer fog (nearer = brighter).

Pins:
  1. gearbox_housing.step iso: flange-ring pixel vs a cavity-interior pixel
     differ by >=8 gray levels on some channel (before: identical). HONEST
     geometry note: the cavity-floor CENTRE is self-occluded in iso — the ray
     from (0,0,12) toward the camera exits through the +Y wall face, so its
     projected pixel shows the OUTER wall (asserted below). The floor-vs-flange
     coplanar-normal pair is therefore asserted in the TOP view (where the
     floor is actually visible), and the iso pin is carried by the deepest
     VISIBLE cavity surface (the far inner x-wall).
  2. determinism: two renders -> byte-identical PNGs.
  3. (consumer pins run via their own files: tests/test_mcp_server_session.py
     preview test + tests/skills/test_render_preview.py.)
  4. a solid box top face stays visually uniform: max in-face variation
     < 6 gray levels (iso AND top views).
"""
from __future__ import annotations

import os

os.environ.setdefault("PHONE_DESIGNER_UI_HEADLESS", "1")

import json
from pathlib import Path

import numpy as np
import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect._render_headless import (
    project_to_pixel,
    render_view,
    render_views_to_pngs,
)

HOUSING_STEP = Path(__file__).resolve().parents[2] / ".pd_workspace" / \
    "gearbox_housing.step"

# world points on the housing (140x90x85 envelope, open-top cavity 112x56x73,
# floor at z=12, flange band y in [28,45] — clear of the (±50,±36.5) holes):
FLANGE_RING_PT = (0.0, 36.5, 85.0)      # top flange ring, +Z normal
CAVITY_FLOOR_CENTRE = (0.0, 0.0, 12.0)  # cavity floor centre, +Z normal
# deepest cavity surface VISIBLE in iso: far inner x-wall (x=-56, n=(+1,0,0));
# visible from the (1,1,1) camera for z0 >= 57 at y=0 (mouth-clearance ray).
INNER_WALL_PT = (-56.0, 0.0, 60.0)
# the point on the OUTER +Y wall that lies on the SAME iso camera ray as the
# cavity-floor centre: (0,0,12) + 45*(1,1,1) = (45,45,57) — proves occlusion.
OUTER_WALL_SAME_RAY_PT = (45.0, 45.0, 57.0)


def _sample(arr, info, world_pt):
    col, row = project_to_pixel(world_pt, info)
    size = info["projection"]["size"]
    assert 0 <= col < size and 0 <= row < size, \
        f"{world_pt} projects off-image at ({col},{row})"
    return tuple(int(v) for v in arr[row, col])


def _max_channel_diff(rgb_a, rgb_b) -> int:
    return max(abs(a - b) for a, b in zip(rgb_a, rgb_b))


@pytest.fixture(scope="module")
def housing():
    if not HOUSING_STEP.exists():
        pytest.skip(f"context part missing: {HOUSING_STEP}")
    from phone_designer.skills.create.import_step import ImportStep
    return ImportStep().apply(None, {"path": str(HOUSING_STEP)}).body


@pytest.fixture(scope="module")
def solid_box():
    """The housing's solid-top counterpart: same 140x90x85 envelope, no cavity
    (Box is XY-centered with Z from 0 — top face is the full 140x90 at z=85)."""
    return Box().apply(None, {
        "length_mm": 140.0, "width_mm": 90.0, "height_mm": 85.0}).body


# ── pin 1: housing — flange vs cavity now visibly different ──────────────────

def test_iso_flange_vs_cavity_interior_ge_8_levels(housing):
    arr, info = render_view(housing, "iso")
    flange = _sample(arr, info, FLANGE_RING_PT)
    cavity = _sample(arr, info, INNER_WALL_PT)
    diff = _max_channel_diff(flange, cavity)
    assert diff >= 8, (
        f"iso depth cue too weak: flange RGB={flange} vs cavity-interior "
        f"RGB={cavity} differ by only {diff} levels (need >=8)")
    # the cavity interior must read DARKER (recessed) than the flange, on
    # every channel — a recessed-pit cue, not an arbitrary tint shift.
    assert all(c < f for c, f in zip(cavity, flange)), \
        f"cavity {cavity} not darker than flange {flange}"


def test_iso_cavity_floor_centre_is_self_occluded(housing):
    """HONEST geometry pin: in iso the cavity-floor centre cannot be seen at
    all — the ray from (0,0,12) toward the (1,1,1) camera crosses the +Y wall
    (cavity mouth clearance needs y0 <= -45, but the cavity only reaches -28).
    Both world points below lie on that ONE camera ray, so they land on the
    SAME pixel, and the pixel shows the (nearer) outer wall."""
    arr, info = render_view(housing, "iso")
    px_floor = project_to_pixel(CAVITY_FLOOR_CENTRE, info)
    px_wall = project_to_pixel(OUTER_WALL_SAME_RAY_PT, info)
    assert px_floor == px_wall, \
        f"same-ray points project apart: {px_floor} vs {px_wall}"
    # and that shared pixel is NOT the dark cavity color — it is the bright
    # outer-wall shade (close to the flange, both at recession offset ~45).
    shared = _sample(arr, info, CAVITY_FLOOR_CENTRE)
    flange = _sample(arr, info, FLANGE_RING_PT)
    assert _max_channel_diff(shared, flange) < 8, (
        f"floor-centre pixel {shared} should show the outer wall "
        f"(≈flange {flange}) — the floor itself is occluded in iso")


def test_top_flange_vs_cavity_floor_centre_ge_8_levels(housing):
    """The EXACT bug pair — both +Z normals, floor actually visible from the
    top view. Before the depth cue these two pixels were IDENTICAL."""
    arr, info = render_view(housing, "top")
    flange = _sample(arr, info, FLANGE_RING_PT)
    floor = _sample(arr, info, CAVITY_FLOOR_CENTRE)
    diff = _max_channel_diff(flange, floor)
    assert diff >= 8, (
        f"top-view depth cue too weak: flange RGB={flange} vs cavity-floor "
        f"RGB={floor} differ by only {diff} levels (need >=8)")
    assert all(c < f for c, f in zip(floor, flange)), \
        f"floor {floor} not darker than flange {flange}"


# ── pin 2: determinism — two renders byte-identical ─────────────────────────

def test_two_renders_byte_identical_png(housing, tmp_path):
    d1, d2 = tmp_path / "r1", tmp_path / "r2"
    r1 = render_views_to_pngs(housing, str(d1), views=("iso",))
    r2 = render_views_to_pngs(housing, str(d2), views=("iso",))
    p1, p2 = r1["images"]["iso"], r2["images"]["iso"]
    assert p1 and p2
    b1, b2 = Path(p1).read_bytes(), Path(p2).read_bytes()
    assert len(b1) > 2_048, f"iso PNG only {len(b1)} bytes"
    assert b1 == b2, "two renders of the same body are not byte-identical"


# ── pin 4: a solid (no-cavity) box top face stays visually uniform ───────────

@pytest.mark.parametrize("view", ["iso", "top"])
def test_solid_box_top_face_uniform_lt_6_levels(solid_box, view):
    """A flat plate must still LOOK flat: sample an interior grid of the box's
    top face (inset 10mm from the edges) and require < 6 gray levels of
    variation per channel. This is the hard case for a per-pixel-only depth
    cue — in iso the 140x90 top face spans ~73% of the scene depth range."""
    arr, info = render_view(solid_box, view)
    samples = [
        _sample(arr, info, (float(x), float(y), 85.0))
        for x in np.linspace(-60.0, 60.0, 9)
        for y in np.linspace(-35.0, 35.0, 8)
    ]
    chans = np.asarray(samples, dtype=int)
    spread = chans.max(axis=0) - chans.min(axis=0)
    assert int(spread.max()) < 6, (
        f"{view}: solid top face not uniform — per-channel spread "
        f"{spread.tolist()} (need < 6); min={chans.min(axis=0).tolist()} "
        f"max={chans.max(axis=0).tolist()}")


def test_solid_box_top_face_brighter_than_housing_floor(solid_box, housing):
    """The point of the exercise: solid top vs open cavity must no longer be
    confusable in the top view — same +Z normal, very different recession."""
    arr_b, info_b = render_view(solid_box, "top")
    arr_h, info_h = render_view(housing, "top")
    box_top = _sample(arr_b, info_b, (0.0, 0.0, 85.0))
    floor = _sample(arr_h, info_h, CAVITY_FLOOR_CENTRE)
    assert _max_channel_diff(box_top, floor) >= 8, (
        f"solid top {box_top} vs open-cavity floor {floor} still confusable")


# ── plumbing: projection metadata stays strict-JSON-safe ─────────────────────

def test_projection_info_json_safe(solid_box):
    _, info = render_view(solid_box, "iso")
    dumped = json.dumps(info, allow_nan=False)  # strict: no NaN/inf, no numpy
    assert "projection" in dumped
    pr = info["projection"]
    assert pr["size"] == 640
    assert {"center", "right", "up", "fwd", "cu", "cv", "scale"} <= set(pr)
