"""topology_health — BREP validity gate.

Fixtures cover the four regression-prone corners:

    1. Healthy build123d box — everything clean.
    2. Open shell (box minus one face) — 4 boundary edges forming ONE
       closed free-boundary loop; OCCT verdict on a bare open shell
       recorded honestly (it is a VALID shell — openness only invalidates
       a solid).
    3. Sliver: a 100×100×0.0005 mm box — the 4 vertical 0.0005 mm edges
       sit below the default 1e-3 mm edge threshold; the 0.05 mm² side
       faces sit ABOVE the default 1e-4 mm² face threshold and only show
       up when the face threshold is raised.
    4. Cylinder — the seam edge has 1 distinct parent face but must NOT be
       counted as a free edge (the regression-prone case).

Plus: bowtie face (invalid + self-intersecting), opt-in CheckerSI on a
healthy box, and the face-count guard.

Direct module imports — passes without manifest registration.
"""
from __future__ import annotations

from phone_designer.skills.create.box import Box
from phone_designer.skills.create.cylinder import Cylinder
from phone_designer.skills.inspect.topology_health import TopologyHealth


# ──────────────────────────────────────────────────────────────────────────────
# Fixture builders


def _open_shell_five_faces():
    """Shell made of 5 of a 10×10×10 box's 6 faces — one open square hole."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS, TopoDS_Shell

    box = BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape()
    faces = []
    it = TopExp_Explorer(box, TopAbs_FACE)
    while it.More():
        faces.append(TopoDS.Face_s(it.Current()))
        it.Next()

    builder = BRep_Builder()
    shell = TopoDS_Shell()
    builder.MakeShell(shell)
    for f in faces[1:]:  # drop the first face → open shell
        builder.Add(shell, f)
    return shell


def _bowtie_face():
    """Face on a self-intersecting (bowtie) wire — invalid by construction."""
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.gp import gp_Pnt

    pts = [
        gp_Pnt(0.0, 0.0, 0.0),
        gp_Pnt(10.0, 10.0, 0.0),
        gp_Pnt(10.0, 0.0, 0.0),
        gp_Pnt(0.0, 10.0, 0.0),
    ]
    mw = BRepBuilderAPI_MakeWire()
    for i in range(4):
        mw.Add(BRepBuilderAPI_MakeEdge(pts[i], pts[(i + 1) % 4]).Edge())
    return BRepBuilderAPI_MakeFace(mw.Wire(), True).Face()


# ──────────────────────────────────────────────────────────────────────────────
# 1. Healthy box


def test_healthy_box_is_clean():
    body = Box().apply(None, {
        "length_mm": 20.0, "width_mm": 20.0, "height_mm": 10.0,
    }).body
    r = TopologyHealth().apply(body, {})
    th = r.extras["topology_health"]
    assert th["is_valid"] is True
    assert th["statuses"] == {}
    assert th["free_edge_count"] == 0
    assert th["open_wire_count"] == 0
    assert th["closed_wire_count"] == 0
    assert th["non_manifold_edge_count"] == 0
    assert th["sliver_faces"] == []
    assert th["sliver_edges"] == []
    assert th["self_intersecting"] is None  # check off by default
    assert th["self_intersection_pair_count"] is None
    assert th["guarded"] is False
    assert th["face_count"] == 6
    # read-only
    assert r.body is body


# ──────────────────────────────────────────────────────────────────────────────
# 2. Open shell


def test_open_shell_free_edges_and_boundary_loop():
    shell = _open_shell_five_faces()
    th = TopologyHealth().apply(shell, {}).extras["topology_health"]
    # The missing face leaves exactly 4 single-parent boundary edges.
    assert th["free_edge_count"] == 4
    # Those 4 edges chain into ONE CLOSED loop (the missing face's outline),
    # so FreeBounds reports 1 closed wire and 0 open wires.
    assert th["closed_wire_count"] == 1
    assert th["open_wire_count"] == 0
    assert th["non_manifold_edge_count"] == 0
    # OCCT verdict recorded 2026-06-10 (OCCT 7.8 / OCP): a bare open SHELL
    # is a VALID shell — openness only invalidates a SOLID. The free-edge
    # census above is what flags the hole.
    assert th["is_valid"] is True


# ──────────────────────────────────────────────────────────────────────────────
# 3. Sliver thresholds


def _thin_box():
    return Box().apply(None, {
        "length_mm": 100.0, "width_mm": 100.0, "height_mm": 0.0005,
    }).body


def test_sliver_edges_detected_at_default_threshold():
    """The 4 vertical edges measure 0.0005 mm < default 1e-3 mm → slivers.

    Threshold behavior documented: the 4 side FACES measure
    100 × 0.0005 = 0.05 mm², which is ABOVE the default 1e-4 mm² face
    threshold — so default args report sliver EDGES but no sliver faces.
    """
    th = TopologyHealth().apply(_thin_box(), {}).extras["topology_health"]
    assert len(th["sliver_edges"]) == 4
    for entry in th["sliver_edges"]:
        assert entry["length_mm"] < 1e-3
    assert th["sliver_faces"] == []


def test_sliver_faces_detected_with_raised_threshold():
    """Raising sliver_face_area_mm2 above 0.05 mm² catches the 4 side faces."""
    th = TopologyHealth().apply(
        _thin_box(), {"sliver_face_area_mm2": 0.1},
    ).extras["topology_health"]
    assert len(th["sliver_faces"]) == 4
    for entry in th["sliver_faces"]:
        assert entry["area_mm2"] < 0.1


# ──────────────────────────────────────────────────────────────────────────────
# 4. Cylinder seam — the regression-prone case


def test_cylinder_seam_edge_not_counted_as_free():
    """A cylinder's seam edge has ONE distinct parent face (listed twice in
    the non-unique ancestor map). It must be excluded from the free-edge
    count — a naive 'exactly 1 parent face = boundary' rule would report 1.
    """
    body = Cylinder().apply(None, {"radius_mm": 5.0, "height_mm": 10.0}).body
    th = TopologyHealth().apply(body, {}).extras["topology_health"]
    assert th["free_edge_count"] == 0
    assert th["non_manifold_edge_count"] == 0
    assert th["open_wire_count"] == 0
    assert th["closed_wire_count"] == 0
    assert th["is_valid"] is True
    assert th["sliver_edges"] == []


# ──────────────────────────────────────────────────────────────────────────────
# 5. Self-intersection (opt-in)


def test_self_intersection_opt_in_clean_box():
    body = Box().apply(None, {
        "length_mm": 10.0, "width_mm": 10.0, "height_mm": 10.0,
    }).body
    th = TopologyHealth().apply(
        body, {"check_self_intersection": True},
    ).extras["topology_health"]
    assert th["self_intersecting"] is False
    assert th["self_intersection_pair_count"] == 0


def test_bowtie_face_invalid_and_self_intersecting():
    face = _bowtie_face()
    th = TopologyHealth().apply(
        face, {"check_self_intersection": True},
    ).extras["topology_health"]
    assert th["is_valid"] is False
    statuses = th["statuses"]
    if statuses.get("invalid") is True:
        # honest fallback path — enumeration unavailable in this binding
        assert statuses.get("detail") == "enumeration unavailable"
    else:
        # recorded 2026-06-10 (OCCT 7.8 / OCP): the bowtie wire reports
        # SelfIntersectingWire (+ UnorientableShape on the wire itself)
        assert "BRepCheck_SelfIntersectingWire" in statuses
        assert statuses["BRepCheck_SelfIntersectingWire"] >= 1
    # CheckerSI finds the crossing edge pair
    assert th["self_intersecting"] is True
    assert th["self_intersection_pair_count"] >= 1


# ──────────────────────────────────────────────────────────────────────────────
# 6. Face-count guard


def test_face_count_guard_returns_partial_report():
    body = Box().apply(None, {
        "length_mm": 10.0, "width_mm": 10.0, "height_mm": 10.0,
    }).body
    r = TopologyHealth().apply(body, {"max_face_count": 3})
    th = r.extras["topology_health"]
    assert th["guarded"] is True
    assert th["face_count"] == 6
    assert th["limit"] == 3
    assert th["reason"] == "too_big"
    assert th["is_valid"] is None
    assert th["free_edge_count"] is None
    assert th["sliver_faces"] is None
    # guard is a partial report, not a failure — body untouched
    assert r.body is body


def test_face_count_guard_disabled_with_none():
    body = Box().apply(None, {
        "length_mm": 10.0, "width_mm": 10.0, "height_mm": 10.0,
    }).body
    th = TopologyHealth().apply(
        body, {"max_face_count": None},
    ).extras["topology_health"]
    assert th["guarded"] is False
    assert th["is_valid"] is True


# ──────────────────────────────────────────────────────────────────────────────
# 7. Spec metadata


def test_spec_registered_read_only():
    spec = TopologyHealth.spec
    assert spec.name == "topology_health"
    assert spec.category == "inspect"
    assert spec.level == "atomic"
    kinds = {pc.kind for pc in spec.post_conditions}
    assert "body_present" in kinds
