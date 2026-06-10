"""Shared draft-angle math for inspect skills.

Two conventions coexist in the codebase and are easy to mix up:

``unsigned_pull_angle_deg``
    Angle in [0, 90] between the face normal and the pull AXIS with the
    two hemispheres collapsed (``abs(dot)``). This is what
    ``color_by_property``'s ``draft_angle`` property reports:
    0 deg = face perpendicular to pull (top/bottom face),
    90 deg = dead-vertical wall.

``signed_draft_deg``
    Mold-maker's wall draft angle: ``90 deg - angle(normal, pull)``
    = ``asin(dot(n_hat, p_hat))``.
    Positive  -> wall opens toward the pull direction (moldable),
    0         -> dead-vertical wall (no draft),
    negative  -> undercut: the wall overhangs against the pull and
                 needs side-action tooling.

Both helpers normalise their inputs and return ``None`` when either
vector is degenerate (near-zero length) so callers can decide their own
fallback. ``inspect_draft_angles`` and ``color_by_property`` both build
on these.
"""
from __future__ import annotations

import math

_EPS = 1e-12


def unit(v: tuple[float, float, float]) -> tuple[float, float, float] | None:
    """Normalise ``v`` to unit length; ``None`` when near-zero."""
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if length < _EPS:
        return None
    return (v[0] / length, v[1] / length, v[2] / length)


def unsigned_pull_angle_deg(
    normal: tuple[float, float, float],
    pull: tuple[float, float, float] = (0.0, 0.0, 1.0),
) -> float | None:
    """Angle [0, 90] deg between ``normal`` and the pull AXIS (hemispheres
    collapsed via ``abs``). ``None`` if either vector is degenerate."""
    n = unit(normal)
    p = unit(pull)
    if n is None or p is None:
        return None
    cos_v = abs(n[0] * p[0] + n[1] * p[1] + n[2] * p[2])
    cos_v = max(-1.0, min(1.0, cos_v))
    return math.degrees(math.acos(cos_v))


def signed_draft_deg(
    normal: tuple[float, float, float],
    pull: tuple[float, float, float] = (0.0, 0.0, 1.0),
) -> float | None:
    """Signed wall draft angle in degrees vs the mold pull direction.

    ``asin(dot(n_hat, p_hat))`` — positive = drafted, 0 = vertical,
    negative = undercut. ``None`` if either vector is degenerate."""
    n = unit(normal)
    p = unit(pull)
    if n is None or p is None:
        return None
    dot = n[0] * p[0] + n[1] * p[1] + n[2] * p[2]
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(math.asin(dot))
