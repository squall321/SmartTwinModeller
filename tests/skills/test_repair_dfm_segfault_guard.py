"""MakeFillet segfault guard — the osculating-tangent-junction crash class.

ROOT CAUSE (found on gearbox_v2_lower.step, drain M10 cross-hole): OCCT's
BRepFilletAPI_MakeFillet HARD-CRASHES the process (0xC0000005
ACCESS_VIOLATION — not a catchable exception) when the concave edge being
filleted terminates at a vertex where a CLOSED edge (a cross-hole opening
circle) osculates it: tangent-parallel contact with the circle's seam at the
tangency vertex leaves a zero-angle cusp on the shared face, and ChFi3d's
corner processing null-derefs. The crash also POISONS every edge
tangent-connected to the cusp (the fillet spine propagates over G1
junctions), e.g. collinear corner-line stubs left by a prior fillet round.

The guard (_fillet_crash_guard in enforce_min_tool_radius) pre-validates each
sub-threshold edge and SKIPS the crash-prone ones with a reported reason
(extras['edges_skipped_unsafe']); repair_dfm surfaces the reason through its
fix / reject records. Worst-case output == input is preserved.

Every test here runs the previously-fatal geometry IN-PROCESS — before the
guard, this whole pytest process died with ACCESS_VIOLATION; process survival
IS the regression signal.
"""
from __future__ import annotations

from build123d import (
    Axis,
    Box,
    BuildPart,
    BuildSketch,
    Cylinder,
    Locations,
    Mode,
    Rectangle,
    RectangleRounded,
    extrude,
)

from phone_designer.skills.inspect.enforce_min_tool_radius import (
    EnforceMinToolRadius,
    _fillet_crash_guard,
)
from phone_designer.skills.repair.repair_dfm import RepairDfm


# ── fixtures ────────────────────────────────────────────────────────────────

def _osculating_hole_part():
    """Minimal repro of the gearbox_v2 split-housing crash: an L-step whose
    concave wall/floor corner line is touched TANGENTIALLY by the opening
    circle of a cross-hole (hole axis parallel to the corner's wall, centre
    exactly one radius above the floor — the drain-hole configuration).
    Filleting either half of the corner line segfaults OCCT without the
    guard."""
    with BuildPart() as bp:
        Box(60, 40, 24)                       # x -30..30, y -20..20, z -12..12
        with Locations((-15, 0, 21)):
            Box(30, 40, 18)                   # block x -30..0, z 12..30
        # Cross-hole along X through the block: r=4, axis at z=16 -> its
        # opening circle on the wall x=0 touches the corner line z=12 at
        # exactly (0, 0, 12).
        with Locations((-15, 0, 16)):
            Cylinder(4, 40, rotation=(0, 90, 0), mode=Mode.SUBTRACT)
    return bp.part


def _square_pocket_part():
    """Sharp square pocket — orthogonal junctions only, all fillable."""
    with BuildPart() as bp:
        Box(40, 30, 12)
        with BuildSketch(bp.faces().sort_by(Axis.Z)[-1]):
            Rectangle(16, 10)
        extrude(amount=-8, mode=Mode.SUBTRACT)
    return bp.part


def _rounded_pocket_part():
    """Pocket with rounded vertical corners — its bottom ring is a G1
    tangent CHAIN (line->arc->line...) with NO osculation. This is the
    legitimate configuration MakeFillet propagates over happily; the guard
    must NOT flag it (capability preservation)."""
    with BuildPart() as bp:
        Box(40, 30, 12)
        with BuildSketch(bp.faces().sort_by(Axis.Z)[-1]):
            RectangleRounded(16, 10, 2)
        extrude(amount=-8, mode=Mode.SUBTRACT)
    return bp.part


def _v2e_map(shape):
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_VERTEX
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
    m = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_VERTEX, TopAbs_EDGE, m)
    return m


def _corner_line_edges(shape, x=0.0, z=12.0):
    """The concave wall/floor corner edges of the osculating fixture."""
    from phone_designer.skills._resolvers import _all_edges
    from phone_designer.skills.modify_finish.cleanability_radius_enforce import (
        _is_concave_edge,
    )
    from phone_designer.skills.modify_finish.sanding_pass import _edge_midpoint
    out = []
    for e in _all_edges(shape):
        try:
            if not _is_concave_edge(shape, e):
                continue
            p = _edge_midpoint(e)
        except Exception:
            continue
        if abs(p.X() - x) < 1e-6 and abs(p.Z() - z) < 1e-6:
            out.append(e)
    return out


# ── 1. the guard predicate itself ────────────────────────────────────────────

def test_guard_flags_osculating_corner_edges():
    part = _osculating_hole_part()
    shape = part.wrapped
    targets = _corner_line_edges(shape)
    assert len(targets) == 2  # corner line split at the tangency vertex
    v2e = _v2e_map(shape)
    for e in targets:
        reason = _fillet_crash_guard(e, v2e)
        assert reason is not None
        assert "osculating tangent junction" in reason
        assert "ACCESS_VIOLATION" in reason


def test_guard_clean_on_square_pocket():
    """Orthogonal pocket corners must NOT be flagged (no capability loss)."""
    part = _square_pocket_part()
    shape = part.wrapped
    from phone_designer.skills._resolvers import _all_edges
    v2e = _v2e_map(shape)
    assert all(_fillet_crash_guard(e, v2e) is None for e in _all_edges(shape))


def test_guard_clean_on_tangent_chain_pocket():
    """A G1 line->arc chain WITHOUT osculation is legitimate fillet input —
    the guard must stay narrow and not flag it."""
    part = _rounded_pocket_part()
    shape = part.wrapped
    from phone_designer.skills._resolvers import _all_edges
    v2e = _v2e_map(shape)
    assert all(_fillet_crash_guard(e, v2e) is None for e in _all_edges(shape))


# ── 2. the crashing skill completes and reports honestly ─────────────────────

def test_enforce_min_tool_radius_survives_and_reports_skips():
    """Before the guard this call ACCESS_VIOLATED the process."""
    part = _osculating_hole_part()
    res = EnforceMinToolRadius().apply(
        part, {"min_radius_mm": 1.875, "auto_fix": True})
    rep = res.extras
    # both corner-line halves are violations AND guard-skipped, none filleted
    assert len(rep["violations"]) == 2
    assert rep["edges_filleted"] == 0
    skipped = rep["edges_skipped_unsafe"]
    assert len(skipped) == 2
    for s in skipped:
        assert "reason" in s and "osculating" in s["reason"]
        assert isinstance(s["edge_idx"], int)
        assert len(s["midpoint"]) == 3
    # worst-case == input: nothing filleted -> body unchanged
    assert res.body is part


def test_tangent_chain_pocket_still_auto_fixes():
    """Capability preservation: the guard must not block the legitimate
    tangent-chain pocket — its sharp bottom/corner edges still get filleted."""
    part = _rounded_pocket_part()
    res = EnforceMinToolRadius().apply(
        part, {"min_radius_mm": 1.0, "auto_fix": True})
    assert res.extras["edges_skipped_unsafe"] == []
    assert res.extras["edges_filleted"] > 0


# ── 3. repair_dfm end-to-end on the fatal geometry ───────────────────────────

def test_repair_dfm_survives_osculating_fixture():
    """The macro completes (no process crash), keeps worst-case==input, and
    surfaces the crash-guard skip as the honest reject reason."""
    part = _osculating_hole_part()
    res = RepairDfm().apply(part, {"processes": ["cnc_milling"],
                                   "pull_direction": [0.0, 0.0, 1.0]})
    rep = res.extras["dfm_repair"]
    # the only radius violations are the two crash-prone edges -> no fix kept
    assert rep["body_changed"] is False
    assert res.body is part  # worst-case == input, verbatim
    fillet_rejects = [f for f in rep["fixes_rejected"]
                      if f["op"] == "enforce_min_tool_radius"]
    assert fillet_rejects, "the skipped fillet must be surfaced, not silent"
    assert any("crash-prone" in f.get("detail", "") for f in fillet_rejects)
