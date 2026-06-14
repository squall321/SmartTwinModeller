"""Component geometric-signature helper (phase-0, 2026-06-14).

A small, standalone helper (NOT a @skill) that distills an OCCT shape — or a
single component split out of a compound (see ``assembly/_compound.py``) — into
a hashable, tolerance-banded *signature*. Two instances of the *same* component
(e.g. identical bolts placed at different positions / orientations) must hash
EQUAL, while genuinely different parts (different volume / extents / topology)
must differ.

The signature is built from four rigid-motion-invariant quantities:

  * rounded volume        (BRepGProp.VolumeProperties — invariant under
                           translation/rotation)
  * sorted bbox extents   (the three side-lengths of the axis-aligned bounding
                           box, sorted ascending so that a rotation that swaps
                           axes does not change the key)
  * face count            (topology — invariant under rigid motion)
  * edge count            (topology — invariant under rigid motion)

Stability under the audit-v6 0.001 mm OCCT jitter
-------------------------------------------------
Raw volume/extents wobble at the sub-micron level between otherwise-identical
shapes (OCCT meshing / transform round-off). We defend against this two ways:

  * volume is rounded to ``VOL_SIG_FIGS`` significant figures (a *relative*
    band — robust whether the part is 1 mm³ or 1e6 mm³), and
  * each bbox extent is snapped to a ``BBOX_BAND`` mm grid (default 0.01 mm,
    i.e. 10 µm — an order of magnitude above the 1 µm jitter).

Because the bands are wide relative to the jitter but narrow relative to a
genuine design delta, a 0.001 mm difference lands in the same band (EQUAL)
while a 2x-volume part lands in a different band (DIFFERENT).

The returned signature is a plain ``ComponentSignature`` namedtuple — fully
hashable, ``==``-comparable, JSON-ish, and deterministic across calls.

Imported nowhere yet — this is groundwork for the future compare-prefilter,
which will use :func:`are_comparable` to cheaply reject pairs that cannot be
the same component before running an expensive geometric diff.
"""
from __future__ import annotations

import math
from typing import NamedTuple

# ── tolerance bands ────────────────────────────────────────────────────────
# Relative band on volume: 3 significant figures. 1.0000 mm³ and 1.0009 mm³
# (a 0.001 mm-class wobble) both round to 1.00; a 2x part (1.00 vs 2.00) does
# not. Significant-figure rounding keeps the band proportional to part size.
VOL_SIG_FIGS = 3

# Absolute band on each bbox extent, in millimetres. 0.01 mm = 10 µm, an order
# of magnitude above the audit-v6 0.001 mm jitter, so identical components snap
# to the same grid cell while real geometry deltas separate.
BBOX_BAND = 0.01

# Default tolerance handed to callers as ``tol`` — same meaning as BBOX_BAND
# (the bbox grid). Volume uses the relative sig-fig band independently.
DEFAULT_TOL = BBOX_BAND


class ComponentSignature(NamedTuple):
    """Hashable, tolerance-banded geometric key for a single component.

    Field order is the comparison order. All fields are banded ints/floats so
    that two structurally-identical instances compare EQUAL despite sub-micron
    OCCT jitter, and ``hash()`` is stable across process-identical inputs.

    Attributes:
        volume: volume rounded to ``VOL_SIG_FIGS`` significant figures.
        extents: the three axis-aligned bbox side-lengths, each snapped to the
            ``band`` grid, then sorted ascending (rotation-invariant).
        face_count: number of unique faces.
        edge_count: number of unique edges.
        band: the bbox grid size used (carried so two signatures are only
            compared when produced with the same band).
    """

    volume: float
    extents: tuple[float, float, float]
    face_count: int
    edge_count: int
    band: float


def _occt_shape(shape_or_body):
    """Accept a raw OCCT ``TopoDS_Shape`` or a build123d body (``.wrapped``)."""
    if shape_or_body is None:
        raise ValueError("geometric_signature: shape_or_body is None")
    return shape_or_body.wrapped if hasattr(shape_or_body, "wrapped") else shape_or_body


def _round_sig(x: float, sig: int) -> float:
    """Round ``x`` to ``sig`` significant figures (0.0 stays 0.0)."""
    if x == 0.0 or not math.isfinite(x):
        return 0.0
    digits = sig - 1 - int(math.floor(math.log10(abs(x))))
    return round(x, digits)


def _snap(x: float, band: float) -> float:
    """Snap ``x`` to the nearest multiple of ``band`` (the tolerance grid)."""
    if band <= 0:
        return float(x)
    # +0.0 guards against a signed negative zero making two equal keys differ.
    return round(x / band) * band + 0.0


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return abs(props.Mass())


def _bbox_extents(shape) -> tuple[float, float, float]:
    """Axis-aligned bbox side-lengths (dx, dy, dz), order as-is (unsorted).

    Uses the plain (mesh-based) ``Add_s`` rather than ``AddOptimal_s``: it is
    deterministic, does not mutate the shape's optimal-bbox cache (see the
    PACK B drift note in extract_feature_catalog.py), and is comfortably below
    the band — we are bucketing extents to 0.01 mm anyway.
    """
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    if bb.IsVoid():
        return (0.0, 0.0, 0.0)
    xmin, ymin, zmin, xmax, ymax, zmax = (float(c) for c in bb.Get())
    return (xmax - xmin, ymax - ymin, zmax - zmin)


def _face_count(shape) -> int:
    from phone_designer.skills._resolvers import _all_faces

    return len(_all_faces(shape))


def _edge_count(shape) -> int:
    from phone_designer.skills._resolvers import _all_edges

    return len(_all_edges(shape))


def geometric_signature(shape_or_body, tol: float = DEFAULT_TOL) -> ComponentSignature:
    """Build a hashable, tolerance-banded geometric signature for a component.

    The signature is invariant under rigid motion (translation + rotation):
    identical components placed differently hash EQUAL, while genuinely
    different parts hash DIFFERENT. It is stable under the audit-v6 0.001 mm
    OCCT jitter because volume is banded to ``VOL_SIG_FIGS`` significant
    figures and every bbox extent is snapped to the ``tol`` grid.

    Args:
        shape_or_body: an OCCT ``TopoDS_Shape`` or a build123d body exposing
            ``.wrapped`` (e.g. a component yielded by ``iter_compound_components``).
        tol: bbox extent grid in millimetres (default ``DEFAULT_TOL`` = 0.01).
            Must match across signatures for them to be comparable.

    Returns:
        A :class:`ComponentSignature` (a hashable ``NamedTuple``).
    """
    shape = _occt_shape(shape_or_body)
    band = float(tol)

    volume = _round_sig(_volume(shape), VOL_SIG_FIGS)
    raw_extents = _bbox_extents(shape)
    extents = tuple(sorted(_snap(e, band) for e in raw_extents))

    return ComponentSignature(
        volume=volume,
        extents=extents,  # type: ignore[arg-type]
        face_count=_face_count(shape),
        edge_count=_edge_count(shape),
        band=band,
    )


def are_comparable(sig_a: ComponentSignature, sig_b: ComponentSignature) -> bool:
    """True iff two signatures fall in the same signature class (within band).

    Intended as a cheap compare-prefilter: if this returns False the two
    components cannot be the same part, so an expensive geometric diff can be
    skipped. Because every field is already tolerance-banded by
    :func:`geometric_signature`, "same class" is exact equality of the banded
    fields — *provided both were produced with the same band* (mismatched
    bands are never comparable).

    Args:
        sig_a: first signature.
        sig_b: second signature.

    Returns:
        bool — same banded signature class.
    """
    if sig_a.band != sig_b.band:
        return False
    return (
        sig_a.volume == sig_b.volume
        and sig_a.extents == sig_b.extents
        and sig_a.face_count == sig_b.face_count
        and sig_a.edge_count == sig_b.edge_count
    )
