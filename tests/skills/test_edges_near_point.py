"""edges_near_point selector + resolver — the V1 single-edge pick keystone.

The viewer's edge-pick works off the FACE raycast hit point (the GLB carries no
edge geometry), so a click NEAR an edge on the surface must resolve to exactly
that one OCCT edge. These tests pin:

  * registration (Literal / _KIND_TO_CLASS / _STABILITY_RANK / SelectorRef),
  * the resolver: n edges whose curve-MIDPOINT is nearest `point` within tol_mm,
  * the keystone edit — fillet_edges_by_predicate with an edges_near_point
    selector rounds ONLY the one clicked edge (volume drop << filleting all
    Z-edges),
  * honest empties (far point + tight tol → 0 edges),
  * n=2 → the two nearest edges,
  * a point ON the top face NEAR an edge picks that edge (the viewer path).

Box is XY-centered, Z from 0 to height (Align.CENTER,CENTER,MIN). For a
20x20x10 box: X,Y in [-10,10], Z in [0,10]. The 4 top edges (z=10) have
midpoints at (0,+/-10,10) and (+/-10,0,10).
"""
from __future__ import annotations

from typing import get_args

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_curvature.fillet_predicate import FilletEdgesByPredicate
from phone_designer.skills._resolvers import resolve_edges, _edge_midpoint
from phone_designer.skills._selectors import (
    EdgesNearPointSelector,
    SelectorKind,
    SelectorRef,
    _KIND_TO_CLASS,
    _STABILITY_RANK,
    selector_from_dict,
)


def _shape_of(part):
    return part.wrapped if hasattr(part, "wrapped") else part


def _box(l=20, w=20, h=10):
    return Box().apply(None, {"length_mm": l, "width_mm": w, "height_mm": h}).body


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


# ---------------------------------------------------------------- registration

def test_edges_near_point_registered_in_master_literal():
    """The Literal drift fix — the new kind (and the previously-missing siblings)
    are visible to any get_args(SelectorKind) schema/preflight/planner."""
    kinds = set(get_args(SelectorKind))
    assert "edges_near_point" in kinds
    # regression on the drift the backlog flagged — these were already missing:
    assert "faces_near_point" in kinds
    assert "edges_convex_only" in kinds
    assert "edges_concave_only" in kinds


def test_edges_near_point_in_kind_to_class_and_stability():
    assert _KIND_TO_CLASS["edges_near_point"] is EdgesNearPointSelector
    # positional live-pick — ranked with edges_by_position (rank 1).
    assert _STABILITY_RANK["edges_near_point"] == 1
    assert EdgesNearPointSelector(point=(0, 0, 0)).stability_rank() == 1


def test_edges_near_point_from_dict_round_trips():
    sel = selector_from_dict(
        {"kind": "edges_near_point", "point": [0, 10, 10], "tol_mm": 2.0, "n": 1}
    )
    assert isinstance(sel, EdgesNearPointSelector)
    assert sel.point == (0.0, 10.0, 10.0)
    assert sel.tol_mm == 2.0 and sel.n == 1
    # part of the discriminated union used by fillet's SelectorRef arg
    assert EdgesNearPointSelector in get_args(SelectorRef)


# ------------------------------------------------------------------- resolver

def test_resolves_exactly_one_top_edge():
    box = _box()
    # a point right at the midpoint of the +Y top edge (0, 10, 10)
    edges = resolve_edges(
        _shape_of(box),
        EdgesNearPointSelector(point=(0.0, 10.0, 10.0), tol_mm=2.0, n=1),
    )
    assert len(edges) == 1
    mx, my, mz = _edge_midpoint(edges[0])
    assert abs(mx - 0.0) < 1e-6
    assert abs(my - 10.0) < 1e-6
    assert abs(mz - 10.0) < 1e-6


def test_point_slightly_off_edge_still_picks_it():
    """The viewer path: a click ON the top face NEAR the +Y edge (pulled 1mm in
    toward the face centre and 1mm below the top) still resolves that edge."""
    box = _box()
    edges = resolve_edges(
        _shape_of(box),
        # near (0,10,10) but not exactly on it — within tol_mm
        EdgesNearPointSelector(point=(0.0, 9.0, 9.5), tol_mm=2.0, n=1),
    )
    assert len(edges) == 1
    _mx, my, mz = _edge_midpoint(edges[0])
    assert abs(my - 10.0) < 1e-6 and abs(mz - 10.0) < 1e-6


def test_far_point_tight_tol_is_empty():
    """Honest: a point far from every edge with a tight tol → no edges."""
    box = _box()
    edges = resolve_edges(
        _shape_of(box),
        EdgesNearPointSelector(point=(500.0, 500.0, 500.0), tol_mm=2.0, n=1),
    )
    assert edges == []


def test_n2_returns_the_two_nearest_edges():
    """n=2 returns the two globally-nearest edges by midpoint distance, nearest
    first. Computed independently from the full edge set so the assertion does
    not bake in a hand-guessed pair (the nearest edge at a box corner can be the
    vertical Z-edge, not another top edge)."""
    box = _box()
    shape = _shape_of(box)
    point = (10.0, 10.0, 10.0)

    edges = resolve_edges(
        shape, EdgesNearPointSelector(point=point, tol_mm=20.0, n=2),
    )
    assert len(edges) == 2

    # independent ground truth: rank ALL edges by midpoint distance
    from phone_designer.skills._resolvers import _all_edges
    ranked = sorted(
        _all_edges(shape),
        key=lambda e: sum((m - p) ** 2 for m, p in zip(_edge_midpoint(e), point)),
    )
    want = [tuple(round(c, 3) for c in _edge_midpoint(e)) for e in ranked[:2]]
    got = [tuple(round(c, 3) for c in _edge_midpoint(e)) for e in edges]
    assert got == want  # nearest-first, exactly the two closest


def test_n_clamped_to_at_least_one():
    box = _box()
    edges = resolve_edges(
        _shape_of(box),
        EdgesNearPointSelector(point=(0.0, 10.0, 10.0), tol_mm=2.0, n=0),
    )
    assert len(edges) == 1


# ------------------------------------------------- keystone edit (fillet ONE)

def test_fillet_one_clicked_edge_vs_all_top_edges():
    """THE KEYSTONE: edges_near_point picks ONE edge → fillet rounds ONLY it.

    Prove it's just one by comparing the volume drop against filleting ALL FOUR
    top edges (the same family — same geometry, same radius). One clicked edge
    removes ~1/4 the material, so the drop is strictly less than half.
    """
    r = 2.0
    v0 = _volume(_box())

    # fillet ONLY the clicked top edge (0,10,10)
    one = FilletEdgesByPredicate().apply(
        _box(),
        {
            "selector": {
                "kind": "edges_near_point",
                "point": [0.0, 10.0, 10.0],
                "tol_mm": 2.0,
                "n": 1,
            },
            "radius_mm": r,
        },
    )
    assert one.selector_freeze is not None
    assert one.selector_freeze.matched_count == 1, "must be exactly ONE edge"
    v_one = _volume(one.body)

    # fillet ALL FOUR top edges (z≈10) for comparison — same family, same radius
    allt = FilletEdgesByPredicate().apply(
        _box(),
        {
            "selector": {
                "kind": "edges_by_position",
                "bbox": {"min": [-100, -100, 9.5], "max": [100, 100, 10.5]},
            },
            "radius_mm": r,
        },
    )
    assert allt.selector_freeze.matched_count == 4
    v_all = _volume(allt.body)

    drop_one = v0 - v_one
    drop_all = v0 - v_all
    # both remove material
    assert drop_one > 0.0 and drop_all > 0.0
    # rounding one of four equivalent edges removes ~1/4 — strictly less than half
    assert drop_one < drop_all * 0.5, (
        f"one-edge drop {drop_one:.3f} should be << all-top drop {drop_all:.3f}"
    )
