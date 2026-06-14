"""PILLAR FREEFORM fixture builders (phase-3, 2026-06-14).

Deterministic build123d builders for the freeform corpus lane. Each builder
returns a ``(build123d.Part, analytic_volume_mm3)`` pair so callers (the
fixture script + tests) can assert the solid is valid AND that its volume
matches the closed-form expectation within a tight tolerance.

The four builders exercise one freeform-reconstruction mechanism each:

  * ``build_rounded_plate``   — rounded-corner prismatic plate. Constant
    cross-section swept along Z → ``detect_outer_silhouette_profile`` reports
    ``prismatic`` and the planner's silhouette-extrude base fires. This is the
    fixture whose profile-base hausdorff should MATERIALLY beat a bbox box.
  * ``build_loft_boss``       — circle → rounded-rectangle ruled loft. Both
    sections share the same in-plane area so the analytic estimate is the
    simple ``area × height`` (ruled loft of equal-area parallel sections is
    within a fraction of a percent of that). Loft-section recovery target.
  * ``build_revolve_frustum`` — a trapezoid meridian revolved 360° about Z
    (a truncated cone). Solid-of-revolution → revolve recovery target. Volume
    is the exact frustum formula.
  * ``build_swept_channel``   — a rectangular profile swept along an L-shaped
    centreline (two straight legs + a quarter-circle fillet). The arc's inner
    and outer area imbalance cancels for a centroid-centred profile, so the
    volume is exactly ``profile_area × centreline_length``. Sweep target.

All dimensions are fixed constants (NEVER per-file / data-driven) so the STEP
outputs are byte-deterministic across machines.
"""
from __future__ import annotations

import math

from build123d import (
    Align,
    Axis,
    BuildLine,
    BuildPart,
    BuildSketch,
    Circle,
    JernArc,
    Line,
    Part,
    Plane,
    Polyline,
    Rectangle,
    RectangleRounded,
    extrude,
    loft,
    make_face,
    revolve,
    sweep,
)

# ──────────────────────────────────────────────────────────────────────────────
# (a) Rounded prismatic plate — silhouette-extrude target.

ROUNDED_PLATE = dict(length_mm=50.0, width_mm=30.0, corner_r_mm=6.0, height_mm=8.0)


def build_rounded_plate() -> tuple[Part, float]:
    """Rectangle 50×30 with 6 mm corner radius, extruded 8 mm along +Z.

    Constant cross-section ⇒ prismatic ⇒ the silhouette detector recovers the
    rounded outline. A bbox box would over-cover the four rounded corners by
    ``(4-π)·r²`` per layer — the silhouette base removes exactly that error,
    which is the anti-fake hausdorff win the test asserts.
    """
    p = ROUNDED_PLATE
    with BuildPart() as bp:
        with BuildSketch():
            RectangleRounded(p["length_mm"], p["width_mm"], p["corner_r_mm"])
        extrude(amount=p["height_mm"])
    area = (
        p["length_mm"] * p["width_mm"]
        - (4.0 - math.pi) * p["corner_r_mm"] ** 2
    )
    vol = area * p["height_mm"]
    return bp.part, vol


# ──────────────────────────────────────────────────────────────────────────────
# (b) Circle → rounded-rectangle loft boss — loft-section recovery target.

_LOFT_BOTTOM_R = 10.0
_LOFT_CORNER_R = 3.0
_LOFT_HEIGHT = 12.0


def build_loft_boss() -> tuple[Part, float]:
    """Ruled loft from a Ø20 circle (bottom) to a rounded square (top), both
    of EQUAL in-plane area, over a 12 mm height.

    Equal-area parallel sections make the analytic estimate the plain
    ``area × height``; the ruled (linear) loft sits within a fraction of a
    percent of that, comfortably inside the ±2 % fixture tolerance.
    """
    area = math.pi * _LOFT_BOTTOM_R ** 2
    # rounded square side solving  side² − (4−π)·rc² = area.
    side = math.sqrt(area + (4.0 - math.pi) * _LOFT_CORNER_R ** 2)
    with BuildPart() as bp:
        with BuildSketch(Plane.XY):
            Circle(_LOFT_BOTTOM_R)
        with BuildSketch(Plane.XY.offset(_LOFT_HEIGHT)):
            RectangleRounded(side, side, _LOFT_CORNER_R)
        loft(ruled=True)
    vol = area * _LOFT_HEIGHT
    return bp.part, vol


# ──────────────────────────────────────────────────────────────────────────────
# (c) Revolved frustum — revolve recovery target.

_REV_R1 = 15.0  # bottom radius
_REV_R2 = 8.0   # top radius
_REV_H = 20.0   # height


def build_revolve_frustum() -> tuple[Part, float]:
    """A trapezoid meridian (R1=15 at z=0 → R2=8 at z=20) revolved 360° about
    Z — a truncated cone.

    Volume is the exact frustum formula ``π·h/3·(R1²+R1·R2+R2²)``.
    """
    with BuildPart() as bp:
        with BuildSketch(Plane.XZ):
            with BuildLine():
                Polyline(
                    [
                        (0.0, 0.0),
                        (_REV_R1, 0.0),
                        (_REV_R2, _REV_H),
                        (0.0, _REV_H),
                    ],
                    close=True,
                )
            make_face()
        revolve(axis=Axis.Z)
    vol = (
        math.pi * _REV_H / 3.0
        * (_REV_R1 ** 2 + _REV_R1 * _REV_R2 + _REV_R2 ** 2)
    )
    return bp.part, vol


# ──────────────────────────────────────────────────────────────────────────────
# (d) L-shaped swept channel — sweep recovery target.

_SWEEP_W = 6.0       # profile width  (along the path's left-hand normal)
_SWEEP_T = 4.0       # profile thickness
_SWEEP_LEG = 20.0    # each straight leg length
_SWEEP_ARC_R = 8.0   # quarter-circle corner radius (path centreline)


def build_swept_channel() -> tuple[Part, float]:
    """A W×T rectangular profile swept along an L-shaped centreline: a 20 mm
    +X leg, a 90° quarter-circle turn of radius 8 mm, then a 20 mm +Y leg.

    For a profile centred on the centreline the inner/outer area imbalance of
    the arc cancels exactly, so ``volume = profile_area × centreline_length``.
    """
    with BuildPart() as bp:
        with BuildLine():
            Line((0.0, 0.0, 0.0), (_SWEEP_LEG, 0.0, 0.0))
            arc = JernArc(
                start=(_SWEEP_LEG, 0.0, 0.0),
                tangent=(1.0, 0.0, 0.0),
                radius=_SWEEP_ARC_R,
                arc_size=90.0,
            )
            Line(arc @ 1, (arc @ 1) + (0.0, _SWEEP_LEG, 0.0))
        with BuildSketch(Plane.YZ):
            Rectangle(_SWEEP_W, _SWEEP_T)
        sweep()
    centreline = _SWEEP_LEG + (math.pi / 2.0) * _SWEEP_ARC_R + _SWEEP_LEG
    vol = _SWEEP_W * _SWEEP_T * centreline
    return bp.part, vol


# ──────────────────────────────────────────────────────────────────────────────
# Registry — name → builder. Order is the canonical fixture order.

BUILDERS: dict[str, "callable[[], tuple[Part, float]]"] = {
    "rounded_plate": build_rounded_plate,
    "loft_boss": build_loft_boss,
    "revolve_frustum": build_revolve_frustum,
    "swept_channel": build_swept_channel,
}

#: mechanism each fixture exercises (documentation / baseline annotation).
MECHANISM: dict[str, str] = {
    "rounded_plate": "silhouette_extrude",
    "loft_boss": "loft_section",
    "revolve_frustum": "revolve",
    "swept_channel": "sweep",
}
