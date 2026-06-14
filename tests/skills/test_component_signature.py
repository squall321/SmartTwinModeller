"""Tests for the component geometric-signature helper (phase-0, 2026-06-14).

Invariants under test (the helper is imported nowhere yet — these prove its
own contract for the future compare-prefilter):

  (a) the SAME box translated/rotated → EQUAL signature (rigid-motion invariant)
  (b) a box vs a box 0.001 mm bigger on one axis → EQUAL (within the band;
      audit-v6 OCCT jitter must not split identical components)
  (c) a box vs a clearly different box (2x volume) → DIFFERENT
  (d) determinism across 5 calls on the same shape
"""
from __future__ import annotations

from build123d import Box, Pos, Rot

from phone_designer.skills.reverse_engineer._component_signature import (
    are_comparable,
    geometric_signature,
)


def _box(dx: float, dy: float, dz: float):
    return Box(dx, dy, dz)


# (a) same box translated AND rotated → equal signature ──────────────────────
def test_same_box_translated_equal_signature():
    base = _box(20, 14, 8)
    moved = Pos(137, -42, 9) * _box(20, 14, 8)

    sig_base = geometric_signature(base)
    sig_moved = geometric_signature(moved)

    assert sig_base == sig_moved
    assert hash(sig_base) == hash(sig_moved)
    assert are_comparable(sig_base, sig_moved)


def test_same_box_rotated_equal_signature():
    # A 90° rotation about Z swaps the X/Y extents — sorted extents make the
    # signature rotation-invariant, so it must stay equal.
    base = _box(20, 14, 8)
    rotated = Rot(0, 0, 90) * _box(20, 14, 8)

    sig_base = geometric_signature(base)
    sig_rot = geometric_signature(rotated)

    assert sig_base == sig_rot
    assert are_comparable(sig_base, sig_rot)


def test_same_box_translated_and_rotated_equal_signature():
    base = _box(20, 14, 8)
    placed = Pos(11, 22, 33) * Rot(0, 0, 90) * _box(20, 14, 8)

    assert geometric_signature(base) == geometric_signature(placed)


# (b) 0.001 mm bigger on one axis → equal within band ────────────────────────
def test_micron_jitter_same_signature():
    base = _box(20, 14, 8)
    jittered = _box(20 + 0.001, 14, 8)

    sig_base = geometric_signature(base)
    sig_jit = geometric_signature(jittered)

    assert sig_base == sig_jit, (sig_base, sig_jit)
    assert are_comparable(sig_base, sig_jit)


# (c) clearly different box (2x volume) → different ──────────────────────────
def test_double_volume_different_signature():
    base = _box(20, 14, 8)
    bigger = _box(40, 14, 8)  # 2x volume, double one extent

    sig_base = geometric_signature(base)
    sig_big = geometric_signature(bigger)

    assert sig_base != sig_big
    assert not are_comparable(sig_base, sig_big)


def test_different_topology_different_signature():
    # Same overall bbox/volume class is not enough — a different face/edge
    # count must separate the signatures. A box (6 faces) vs the same box with
    # a hole through it (more faces/edges) differ.
    from build123d import Cylinder, Mode

    plain = _box(20, 20, 10)
    with_hole = _box(20, 20, 10) - Cylinder(3, 12, mode=Mode.SUBTRACT)

    sig_plain = geometric_signature(plain)
    sig_hole = geometric_signature(with_hole)

    assert sig_plain != sig_hole
    assert not are_comparable(sig_plain, sig_hole)


# (d) determinism across 5 calls ─────────────────────────────────────────────
def test_determinism_across_five_calls():
    box = _box(31.7, 12.3, 5.5)
    sigs = [geometric_signature(box) for _ in range(5)]

    first = sigs[0]
    for s in sigs[1:]:
        assert s == first
        assert hash(s) == hash(first)
    # exactly one distinct value
    assert len(set(sigs)) == 1


# accepts raw OCCT shape as well as a build123d body ─────────────────────────
def test_accepts_raw_occt_shape():
    body = _box(20, 14, 8)
    sig_body = geometric_signature(body)
    sig_raw = geometric_signature(body.wrapped)
    assert sig_body == sig_raw


# are_comparable rejects mismatched bands ────────────────────────────────────
def test_mismatched_band_not_comparable():
    box = _box(20, 14, 8)
    sig_default = geometric_signature(box)
    sig_coarse = geometric_signature(box, tol=0.1)
    # Different band → never comparable, even for the same physical box.
    assert not are_comparable(sig_default, sig_coarse)
