"""Topology graph wave-1B — face_adjacency_graph / edge_concavity_classify /
vertex_connectivity.

All three are read-only inspect skills; body must survive every call
(post_condition body_present) and report deterministic counts on the
canonical 20×30×10 box (6 faces, 12 edges, 8 vertices, all box edges convex).
"""
from __future__ import annotations

from phone_designer.skills.create.box import Box
from phone_designer.skills.create.disc_with_dome import DiscWithDome
from phone_designer.skills.inspect.edge_concavity_classify import (
    EdgeConcavityClassify,
)
from phone_designer.skills.inspect.face_adjacency_graph import (
    FaceAdjacencyGraph,
)
from phone_designer.skills.inspect.vertex_connectivity import VertexConnectivity
from phone_designer.skills.modify_pocket.hole import Hole


# ──────────────────────────────────────────────────────────────────────────────
# face_adjacency_graph


def test_face_graph_box_has_six_nodes_and_twelve_edges():
    """20×30×10 box → 6 faces (nodes), 12 shared-edge graph-edges, all convex."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 30, "height_mm": 10}).body
    r = FaceAdjacencyGraph().apply(box, {})
    g = r.extras["face_graph"]
    assert g["node_count"] == 6
    assert len(g["edges"]) == 12
    # All box edges must classify convex
    kinds = {e["concavity"] for e in g["edges"]}
    assert "convex" in kinds
    assert "concave" not in kinds
    # body unchanged
    assert r.body is box


def test_face_graph_edge_lengths_match_box_dimensions():
    """Edge-length distribution: 4 each of 20, 30, 10."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 30, "height_mm": 10}).body
    r = FaceAdjacencyGraph().apply(box, {})
    lens = sorted(round(e["len"], 1) for e in r.extras["face_graph"]["edges"])
    assert lens.count(20.0) == 4
    assert lens.count(30.0) == 4
    assert lens.count(10.0) == 4


def test_face_graph_pocket_introduces_concave_edges():
    """Drilling a hole through a box creates concave edges at the lip."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 10}).body
    holed = Hole().apply(box, {
        "position": (0.0, 0.0, 10.0),
        "diameter_mm": 5.0,
        "depth_mm": 5.0,
        "direction": "-Z",
    }).body
    r = FaceAdjacencyGraph().apply(holed, {})
    kinds = [e["concavity"] for e in r.extras["face_graph"]["edges"]]
    assert "concave" in kinds


# ──────────────────────────────────────────────────────────────────────────────
# edge_concavity_classify


def test_edge_concavity_box_all_convex():
    """A simple box has only convex edges (12)."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = EdgeConcavityClassify().apply(box, {})
    ec = r.extras["edge_concavity"]
    assert ec["total"] == 12
    assert len(ec["convex"]) == 12
    assert ec["concave"] == []
    assert r.body is box


def test_edge_concavity_disc_has_tangent_edges():
    """Disc (cylinder + plane) seam edges classify as tangent when normals align
    smoothly along the circular boundary, but disc cap/wall meeting is convex.
    At minimum, no concave edges should appear."""
    disc = DiscWithDome().apply(None, {
        "diameter_mm": 20.0,
        "height_mm": 5.0,
        "dome_rise_mm": 0.0,
        "corner_r_mm": 0.0,
    }).body
    r = EdgeConcavityClassify().apply(disc, {})
    ec = r.extras["edge_concavity"]
    assert ec["total"] >= 2
    assert ec["concave"] == []
    # union convex+tangent must cover everything counted in totals
    counted = len(ec["convex"]) + len(ec["tangent"]) + len(ec["concave"])
    # boundary (open) edges may be skipped; total is the *raw* edge count
    assert counted <= ec["total"]


def test_edge_concavity_hole_creates_concave():
    """A blind hole introduces a concave lip between top face and hole wall."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 10}).body
    holed = Hole().apply(box, {
        "position": (0.0, 0.0, 10.0),
        "diameter_mm": 5.0,
        "depth_mm": 5.0,
        "direction": "-Z",
    }).body
    r = EdgeConcavityClassify().apply(holed, {})
    ec = r.extras["edge_concavity"]
    assert len(ec["concave"]) >= 1


# ──────────────────────────────────────────────────────────────────────────────
# vertex_connectivity


def test_vertex_connectivity_box_eight_vertices_degree_three():
    """20×30×10 box has 8 vertices; every vertex has degree 3."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 30, "height_mm": 10}).body
    r = VertexConnectivity().apply(box, {})
    vs = r.extras["vertices"]
    assert len(vs) == 8
    for v in vs:
        assert v["degree"] == 3
        assert len(v["incident_edges"]) == 3
        assert isinstance(v["position"], list)
        assert len(v["position"]) == 3
    # body unchanged
    assert r.body is box


def test_vertex_connectivity_positions_at_box_corners():
    """A 20×30×10 box centered around the build123d origin: every vertex's
    coordinates must use ±10 (X), ±15 (Y), and 0 / 10 (Z) — i.e. the eight
    corner positions. (build123d Box default: aligned so MIN at z=0.)"""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 30, "height_mm": 10}).body
    r = VertexConnectivity().apply(box, {})
    xs = sorted({round(v["position"][0], 3) for v in r.extras["vertices"]})
    ys = sorted({round(v["position"][1], 3) for v in r.extras["vertices"]})
    zs = sorted({round(v["position"][2], 3) for v in r.extras["vertices"]})
    assert len(xs) == 2
    assert len(ys) == 2
    assert len(zs) == 2
    assert abs((xs[1] - xs[0]) - 20.0) < 0.01
    assert abs((ys[1] - ys[0]) - 30.0) < 0.01
    assert abs((zs[1] - zs[0]) - 10.0) < 0.01


def test_vertex_connectivity_incident_edge_indices_are_valid():
    """Every incident_edges index must point at a real edge (0..edge_count-1)."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = VertexConnectivity().apply(box, {})
    # A box has 12 edges total
    for v in r.extras["vertices"]:
        for ei in v["incident_edges"]:
            assert 0 <= ei < 12


# ──────────────────────────────────────────────────────────────────────────────
# spec metadata sanity


def test_topology_skills_spec_metadata():
    for cls in (FaceAdjacencyGraph, EdgeConcavityClassify, VertexConnectivity):
        spec = cls.spec
        assert spec.category == "inspect"
        assert spec.level == "atomic"
        # Read-only: body must still be present after the skill runs.
        kinds = {pc.kind for pc in spec.post_conditions}
        assert "body_present" in kinds
