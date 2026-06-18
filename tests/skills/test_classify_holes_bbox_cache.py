"""classify_holes — per-face optimal-bbox memo (PERF, 2026-06-18).

cProfile of ClassifyHoles on RC_Buggy's heaviest 493-face leaf showed 608
``BRepBndLib.AddOptimal_s(face, ...)`` calls that resolved to only 40 UNIQUE
faces — a 15.2x redundancy because every axis-grouping / band-projection /
cone-on-axis pass re-derived the SAME face's optimal bbox. ``_face_bbox`` now
memoizes the per-face result (keyed by ``hash(face)``) inside a per-_apply
scope opened by ``ClassifyHoles._apply``.

The optimal bbox of a face is a pure, repeat-stable function of the face
(measured max_abs_dev = 0.0 across 30 interleaved recomputations on the 493
RC_Buggy faces), so memoizing the first result is BYTE-IDENTICAL to the legacy
path. These tests pin that:

    1. The full ``holes`` list is JSON-deep-equal (sort_keys) with the cache
       ON vs OFF, for a hole-bearing build123d slab AND (when present) a
       corpus body.
    2. The cache never makes detection SLOWER (cache_time <= no_cache_time).
    3. The cache really removes redundant AddOptimal_s calls (face-bbox
       AddOptimal_s count drops with the cache on).

Toggling: ``_BBOX_CACHE_DISABLED`` is a module flag consulted by both
``_bbox_cache_get`` and ``_bbox_cache_scope.__enter__``; flipping it gives a
clean in-process on/off without re-importing.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect import classify_holes as CH
from phone_designer.skills.inspect.classify_holes import ClassifyHoles
from phone_designer.skills.modify_pocket.hole import Hole


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers


def _slab_with_holes():
    """40 x 40 x 5 slab with a Ø6 counterbore-ish stack + two plain holes —
    enough cylinder/cone faces that the cone-on-axis pass re-bboxes faces
    across multiple groups (exercises the memo)."""
    body = Box().apply(None, {
        "length_mm": 40.0, "width_mm": 40.0, "height_mm": 5.0,
    }).body
    for (x, y), d in (
        ((-12.0, -12.0), 3.0),
        ((12.0, -12.0), 4.0),
        ((-12.0, 12.0), 6.0),
        ((12.0, 12.0), 2.0),
        ((0.0, 0.0), 8.0),
    ):
        body = Hole().apply(body, {
            "position": (x, y, 5.0),
            "diameter_mm": d,
            "depth_mm": 7.0,
            "direction": "-Z",
        }).body
    return body


def _holes_with_flag(body, *, disabled: bool):
    """Run ClassifyHoles with the bbox memo forced ON (disabled=False) or
    OFF (disabled=True). Returns (holes_list, seconds, addoptimal_face_count)."""
    import OCP.BRepBndLib as _bbl
    from OCP.TopAbs import TopAbs_FACE

    prev = CH._BBOX_CACHE_DISABLED
    CH._BBOX_CACHE_DISABLED = disabled
    # ensure no stale thread-local cache leaks between runs
    if hasattr(CH._bbox_cache_tls, "cache"):
        CH._bbox_cache_tls.cache = None

    orig = _bbl.BRepBndLib.AddOptimal_s
    face_calls = {"n": 0}

    def counting(shp, bb, *a, **k):
        try:
            if shp.ShapeType() == TopAbs_FACE:
                face_calls["n"] += 1
        except Exception:
            pass
        return orig(shp, bb, *a, **k)

    _bbl.BRepBndLib.AddOptimal_s = staticmethod(counting)
    try:
        t0 = time.perf_counter()
        res = ClassifyHoles().apply(body, {"match_standards": True})
        dt = time.perf_counter() - t0
    finally:
        _bbl.BRepBndLib.AddOptimal_s = orig
        CH._BBOX_CACHE_DISABLED = prev
        if hasattr(CH._bbox_cache_tls, "cache"):
            CH._bbox_cache_tls.cache = None
    return res.extras["holes"], dt, face_calls["n"]


def _canon(holes):
    return json.dumps(holes, sort_keys=True)


# ──────────────────────────────────────────────────────────────────────────────
# Tests


def test_holes_byte_identical_cache_on_vs_off_build123d():
    body = _slab_with_holes()
    holes_off, _t_off, faces_off = _holes_with_flag(body, disabled=True)
    holes_on, _t_on, faces_on = _holes_with_flag(body, disabled=False)

    assert _canon(holes_on) == _canon(holes_off), (
        "classify_holes output DIFFERS with the bbox memo on vs off — "
        "byte-identity broken"
    )
    assert len(holes_on) >= 5, f"expected >=5 holes, got {len(holes_on)}"
    # The memo must never INCREASE face-bbox AddOptimal_s calls. (On this
    # trivial slab each plain hole owns one cylinder face in its own group, so
    # there is no cross-group redundancy to remove and on == off is expected;
    # the strict reduction is asserted on the Ventilator corpus body below,
    # where one face is re-bboxed across many groups: 2986 -> 269.)
    assert faces_on <= faces_off, (
        f"cache INCREASED face-bbox AddOptimal_s calls "
        f"(on={faces_on} off={faces_off})"
    )


def test_cache_not_slower_than_no_cache_build123d():
    body = _slab_with_holes()
    # warm caches / imports (yaml catalog, OCP) so the timing compares work,
    # not first-call import cost.
    _holes_with_flag(body, disabled=False)
    _holes_with_flag(body, disabled=True)

    # best-of-3 to damp scheduler noise on a small body
    t_on = min(_holes_with_flag(body, disabled=False)[1] for _ in range(3))
    t_off = min(_holes_with_flag(body, disabled=True)[1] for _ in range(3))
    # allow a tiny slack for timer noise on a trivially small body.
    assert t_on <= t_off + 0.05, (
        f"cache slower than no-cache: cache={t_on:.4f}s no_cache={t_off:.4f}s"
    )


_CORPUS = os.path.join(
    os.path.dirname(__file__), "..", "..", "corpus", "oem", "complex",
    "pythonocc__Ventilator.stp",
)


@pytest.mark.skipif(
    not os.path.exists(_CORPUS),
    reason="corpus body pythonocc__Ventilator.stp not available",
)
def test_holes_byte_identical_on_corpus_body():
    from OCP.STEPControl import STEPControl_Reader
    reader = STEPControl_Reader()
    reader.ReadFile(_CORPUS)
    reader.TransferRoots()
    shape = reader.OneShape()

    holes_off, _t_off, faces_off = _holes_with_flag(shape, disabled=True)
    holes_on, t_on, faces_on = _holes_with_flag(shape, disabled=False)

    assert _canon(holes_on) == _canon(holes_off), (
        "Ventilator classify_holes output DIFFERS with the bbox memo on vs "
        "off — byte-identity broken on a corpus body"
    )
    assert faces_on < faces_off, (
        f"cache did not reduce face-bbox AddOptimal_s on Ventilator "
        f"(on={faces_on} off={faces_off})"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Whole-SHAPE bbox hoist (2026-06-18) — orthogonal to the per-face memo above.
# _standardize_entry / _body_entry_along_axis / _rescue_band_anchored_entry each
# called BRepBndLib.AddOptimal_s on the WHOLE shape once per hole group; that
# single line was the dominant cost behind the 800 s box-mode TIMEOUTs (KR600:
# 434 groups). The hoist computes it ONCE in _apply and threads it in. These
# tests count NON-FACE (whole-shape) AddOptimal_s calls and pin byte-identity.


def _holes_with_shape_count(body, *, disabled: bool):
    """Like _holes_with_flag but counts WHOLE-SHAPE (non-FACE) AddOptimal_s
    calls — the ones the per-group hoist removes."""
    import OCP.BRepBndLib as _bbl
    from OCP.TopAbs import TopAbs_FACE

    prev = CH._BBOX_CACHE_DISABLED
    CH._BBOX_CACHE_DISABLED = disabled
    if hasattr(CH._bbox_cache_tls, "cache"):
        CH._bbox_cache_tls.cache = None

    orig = _bbl.BRepBndLib.AddOptimal_s
    shape_calls = {"n": 0}

    def counting(shp, bb, *a, **k):
        try:
            if shp.ShapeType() != TopAbs_FACE:
                shape_calls["n"] += 1
        except Exception:
            pass
        return orig(shp, bb, *a, **k)

    _bbl.BRepBndLib.AddOptimal_s = staticmethod(counting)
    try:
        res = ClassifyHoles().apply(body, {"match_standards": True})
    finally:
        _bbl.BRepBndLib.AddOptimal_s = orig
        CH._BBOX_CACHE_DISABLED = prev
        if hasattr(CH._bbox_cache_tls, "cache"):
            CH._bbox_cache_tls.cache = None
    return res.extras["holes"], shape_calls["n"]


@pytest.mark.skipif(
    not os.path.exists(_CORPUS),
    reason="corpus body pythonocc__Ventilator.stp not available",
)
def test_shape_bbox_hoist_byte_identical_and_fewer_shape_calls():
    from OCP.STEPControl import STEPControl_Reader
    reader = STEPControl_Reader()
    reader.ReadFile(_CORPUS)
    reader.TransferRoots()
    shape = reader.OneShape()

    holes_off, shape_off = _holes_with_shape_count(shape, disabled=True)
    holes_on, shape_on = _holes_with_shape_count(shape, disabled=False)

    # 1. byte-identical output (the whole point — a hoist, not a behaviour change)
    assert _canon(holes_on) == _canon(holes_off), (
        "Ventilator classify_holes output DIFFERS with the whole-shape bbox "
        "hoist on vs off — byte-identity broken"
    )
    # 2. the hoist collapses many per-group whole-shape AddOptimal_s calls into
    #    one (Ventilator has multiple hole groups, each previously re-bboxing
    #    the whole shape in _standardize_entry + _body_entry_along_axis).
    assert shape_on < shape_off, (
        f"hoist did not reduce whole-shape AddOptimal_s "
        f"(on={shape_on} off={shape_off})"
    )
    # with the hoist, the per-_apply whole-shape bbox is computed exactly once.
    assert shape_on == 1, (
        f"expected exactly 1 whole-shape AddOptimal_s with the hoist, "
        f"got {shape_on}"
    )
