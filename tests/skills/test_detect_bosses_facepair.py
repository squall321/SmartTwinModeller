"""detect_bosses / detect_ribs face-pair adjacency fast-path — PILLAR-PERF.

phase-2 (2026-06-14). ``detect_bosses._classify_edges`` (consumed by the
detect_ribs / detect_standoffs / detect_lugs family for its edge → owner-face
adjacency map) historically built that map with a hand-rolled
O(F · E_per_face · E) ``IsSame`` triple loop. We ported the single-pass
``TopExp.MapShapesAndUniqueAncestors`` primitive from
``classify_pockets._shared_face_pairs`` (helper ``_edge_owners_facepair``).

This is a PURE SPEED optimization — the detector OUTPUT must be byte-identical
because the boss/rib catalogs feed preserve_brep + box reconstruction baselines.
``PD_DISABLE_FACEPAIR=1`` forces the legacy path; these tests assert the fast
and legacy paths are deep-equal, plus the fast ``edge_owners`` construction is
not slower than the legacy loop.

NOTE on the comparison target: ``DetectBosses.apply`` returns a SkillResult
whose ``extras`` carries a framework-injected ``metrics.duration_ms`` wall-clock
field that is non-deterministic by nature. We compare only the GEOMETRY payload
(``bosses`` / ``boss_count`` resp. ``ribs`` / ``rib_count``).
"""
from __future__ import annotations

import json
import os
import pathlib
import time
from contextlib import contextmanager

import pytest

from phone_designer.skills.inspect.detect_bosses import (
    DetectBosses,
    _classify_edges,
    _edge_owners,
    _edge_owners_facepair,
    _edge_owners_legacy,
)
from phone_designer.skills.inspect.detect_ribs import DetectRibs

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Boss/rib-bearing corpus bodies (real imported CAD). Mix of small (sub-
# crossover) and dense (1018-face 11752 — past the crossover, where the
# single-pass map wins) so the byte-identity + timing tests exercise BOTH
# dispatcher branches.
_CORPUS = {
    "as1-oc-214": _REPO_ROOT / "corpus" / "oem" / "complex" / "pythonocc__as1-oc-214.stp",
    "Ventilator": _REPO_ROOT / "corpus" / "oem" / "complex" / "pythonocc__Ventilator.stp",
    "bracket": _REPO_ROOT / "corpus" / "oem" / "industrial" / "freecad__2020_corner_bracket.step",
    "linkrods": _REPO_ROOT / "corpus" / "oem" / "complex" / "occt__linkrods.step",
    "11752": _REPO_ROOT / "corpus" / "oem" / "complex" / "pythonocc__11752.stp",
}


@contextmanager
def _legacy_facepair():
    """Force the legacy O(F·E²) IsSame adjacency loop for the reference run."""
    prev = os.environ.get("PD_DISABLE_FACEPAIR")
    os.environ["PD_DISABLE_FACEPAIR"] = "1"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("PD_DISABLE_FACEPAIR", None)
        else:
            os.environ["PD_DISABLE_FACEPAIR"] = prev


@contextmanager
def _fast_facepair():
    """Force the fast single-pass ancestor map (default)."""
    prev = os.environ.get("PD_DISABLE_FACEPAIR")
    os.environ.pop("PD_DISABLE_FACEPAIR", None)
    try:
        yield
    finally:
        if prev is not None:
            os.environ["PD_DISABLE_FACEPAIR"] = prev


def _load(path: pathlib.Path):
    from phone_designer.skills.assembly._compound import load_step_shape
    return load_step_shape(str(path))


def _boss_payload(shape) -> str:
    e = DetectBosses().apply(shape, {}).extras
    return json.dumps({"bosses": e["bosses"], "boss_count": e["boss_count"]},
                      sort_keys=True)


def _rib_payload(shape) -> str:
    e = DetectRibs().apply(shape, {}).extras
    return json.dumps({"ribs": e["ribs"], "rib_count": e["rib_count"]},
                      sort_keys=True)


def _params():
    return [
        pytest.param(
            name, path,
            marks=pytest.mark.skipif(not path.exists(),
                                     reason=f"corpus file missing: {path}"),
        )
        for name, path in _CORPUS.items()
    ]


# ── byte-identity: edge_owners helper vs verbatim legacy loop ──────────────


@pytest.mark.parametrize("name, path", _params())
def test_edge_owners_facepair_matches_legacy_loop(name, path):
    from phone_designer.skills._resolvers import _all_edges, _all_faces

    shape = _load(path)
    faces = _all_faces(shape)
    edges = _all_edges(shape)

    fast = _edge_owners_facepair(shape, edges, faces)
    slow = _edge_owners_legacy(shape, edges, faces)
    assert fast == slow, f"{name}: edge_owners adjacency differs fast vs legacy"


@pytest.mark.parametrize("name, path", _params())
def test_edge_owners_dispatcher_matches_legacy(name, path):
    """The adaptive dispatcher (fast OR legacy depending on size) must return
    the same map as the verbatim legacy loop, regardless of which branch it
    picks for this body."""
    from phone_designer.skills._resolvers import _all_edges, _all_faces

    shape = _load(path)
    faces = _all_faces(shape)
    edges = _all_edges(shape)

    assert _edge_owners(shape, edges, faces) == _edge_owners_legacy(
        shape, edges, faces
    ), f"{name}: adaptive _edge_owners differs from legacy"


# ── byte-identity: _classify_edges full output on/off the fast path ────────


@pytest.mark.parametrize("name, path", _params())
def test_classify_edges_deep_equal_on_off_fastpath(name, path):
    shape = _load(path)

    with _fast_facepair():
        labels_f, owners_f, _, _ = _classify_edges(shape)
    with _legacy_facepair():
        labels_s, owners_s, _, _ = _classify_edges(shape)

    assert owners_f == owners_s, f"{name}: edge_owners differ on/off fast path"
    assert labels_f == labels_s, f"{name}: edge labels differ on/off fast path"


# ── byte-identity: detect_bosses / detect_ribs geometry payload ────────────


@pytest.mark.parametrize("name, path", _params())
def test_detect_bosses_deep_equal_on_off_fastpath(name, path):
    shape = _load(path)

    with _fast_facepair():
        boss_f = _boss_payload(shape)
        rib_f = _rib_payload(shape)
    with _legacy_facepair():
        boss_s = _boss_payload(shape)
        rib_s = _rib_payload(shape)

    assert boss_f == boss_s, f"{name}: detect_bosses output differs on/off fast path"
    assert rib_f == rib_s, f"{name}: detect_ribs output differs on/off fast path"


# ── timing: adaptive dispatcher never regresses; fast path wins at scale ───


def _best_of(fn, shape, edges, faces, reps=3) -> float:
    best = float("inf")
    for _ in range(reps):
        t0 = time.perf_counter()
        fn(shape, edges, faces)
        best = min(best, time.perf_counter() - t0)
    return best


@pytest.mark.parametrize("name, path", _params())
def test_adaptive_dispatcher_not_slower_than_legacy(name, path):
    """The size-gated ``_edge_owners`` must never be materially slower than the
    legacy loop on ANY body: below the crossover it dispatches TO the legacy
    loop, above it the single-pass map wins. Catches a regression where the
    dispatcher accidentally forces the slow-on-small fast path everywhere."""
    from phone_designer.skills._resolvers import _all_edges, _all_faces

    shape = _load(path)
    faces = _all_faces(shape)
    edges = _all_edges(shape)

    # warm OCCT caches so neither path pays a first-touch penalty.
    _edge_owners(shape, edges, faces)
    _edge_owners_legacy(shape, edges, faces)

    t_adaptive = _best_of(_edge_owners, shape, edges, faces)
    t_legacy = _best_of(_edge_owners_legacy, shape, edges, faces)

    # Allow modest slack: on a sub-crossover body the dispatcher runs the legacy
    # loop plus a len()/env check, so it should be within a few ms; on a large
    # body the single-pass map is strictly faster. 25% + 5ms absorbs scheduler
    # jitter on the tiny corpus bodies without masking a real blow-up.
    assert t_adaptive <= t_legacy * 1.25 + 0.005, (
        f"{name}: adaptive {t_adaptive*1e3:.2f}ms vs legacy {t_legacy*1e3:.2f}ms"
    )


@pytest.mark.slow
def test_facepair_faster_on_largest_corpus_body():
    """At scale the single-pass ancestor map MUST beat the O(F·E²) legacy loop
    — that is the whole point of the optimization. Runs on the largest present
    corpus body; skips if only small (<600-face) bodies are available, since
    below the crossover the fast path is legitimately not the faster one."""
    from phone_designer.skills._resolvers import _all_edges, _all_faces

    present = [p for p in _CORPUS.values() if p.exists()]
    if not present:
        pytest.skip("no corpus bodies available")

    # pick the body with the most faces.
    best_shape = None
    best_n = -1
    best_edges = best_faces = None
    for path in present:
        shape = _load(path)
        faces = _all_faces(shape)
        if len(faces) > best_n:
            best_n = len(faces)
            best_shape = shape
            best_faces = faces
            best_edges = _all_edges(shape)

    if best_n < 600:
        pytest.skip(
            f"largest corpus body has {best_n} faces (< 600 crossover); "
            "fast path not expected to win below the crossover"
        )

    # warm caches.
    _edge_owners_facepair(best_shape, best_edges, best_faces)
    _edge_owners_legacy(best_shape, best_edges, best_faces)

    t_fast = _best_of(_edge_owners_facepair, best_shape, best_edges, best_faces, reps=1)
    t_slow = _best_of(_edge_owners_legacy, best_shape, best_edges, best_faces, reps=1)
    assert t_fast < t_slow, (
        f"facepair {t_fast*1e3:.1f}ms not faster than legacy "
        f"{t_slow*1e3:.1f}ms on {best_n}-face body"
    )
