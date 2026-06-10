"""classify_edge_blends — fillet / chamfer detection (plan item A3).

Fixtures are tiny build123d bodies with KNOWN blends:

    1. 20×30×10 box, R2.0 fillet on ONE vertical edge → exactly one
       fillet entry, radius 2.0 ± 2 %, convexity 'round'.
    2. Same box, 1 × 45° chamfer on one vertical edge → exactly one
       chamfer entry, angle ≈ 45°, width ≈ √2.
    3. Plain box → no blends at all.
    4. Cylinder body → the curved side wall is NOT a blend (the
       regression-prone case: a full cylinder face must never
       false-positive as a fillet).
    5. Cylinder with a fillet on its TOP circle → exactly one torus
       fillet (minor radius), and the rod's own side wall (radius 5)
       still absent — exercises the arc-extent gate while tangent
       blends touch the wall.

A3-FIX (2026-06-11) round-trip stability gates:

    6. Box fillet SPLIT in two by a mid-height notch cut → ONE merged
       entry (fragment_count 2, same radius); per-face entries return
       with merge_coaxial_fragments=False.
    7. Thin plate (h=0.1) with R0.2 corner roundover — tangency seam
       (0.1) shorter than the radius → stub gate drops it (castellation
       / spotface pattern, Crystal_SMD); ratio 0 disables the gate.

Plus catalog wiring: extract_feature_catalog populates 'edge_blends'
by DEFAULT (A3-FIX default-on) and skips when include_edge_blends=False.
"""
from __future__ import annotations

from build123d import (
    Axis,
    Box as B3dBox,
    Cylinder as B3dCylinder,
    Pos,
    chamfer,
    fillet,
)

from phone_designer.skills.inspect.classify_edge_blends import ClassifyEdgeBlends


# ──────────────────────────────────────────────────────────────────────────────
# Fixture builders


def _box():
    return B3dBox(20.0, 30.0, 10.0)


def _box_one_fillet(radius=2.0):
    part = _box()
    edge = part.edges().filter_by(Axis.Z)[0]
    return fillet(edge, radius=radius)


def _box_one_chamfer(length=1.0):
    part = _box()
    edge = part.edges().filter_by(Axis.Z)[0]
    return chamfer(edge, length=length)


def _cylinder():
    return B3dCylinder(radius=5.0, height=20.0)


def _cylinder_top_fillet(radius=1.5):
    part = _cylinder()
    top_circle = part.edges().group_by(Axis.Z)[-1]
    return fillet(top_circle, radius=radius)


# A3-FIX (2026-06-11) fixtures — round-trip stability gates.


def _box_split_fillet(radius=2.0):
    """Box with one vertical R2 fillet, then a mid-height corner notch
    cut that SPLITS the fillet cylinder into two coaxial fragments —
    the boolean-split pattern the regen executor produces."""
    part = _box_one_fillet(radius=radius)
    blend = _blends(part)[0]
    cx, cy, _cz = blend["centroid"]
    notch = Pos(cx, cy, 0.0) * B3dBox(3.0, 3.0, 2.0)
    return part - notch


def _thin_plate_stub_fillet():
    """0.1 mm tall plate with an R0.2 corner roundover: the tangency
    seam (0.1) is shorter than the radius — the Crystal_SMD
    castellation / spotface stub pattern."""
    part = B3dBox(20.0, 30.0, 0.1)
    edge = part.edges().filter_by(Axis.Z)[0]
    return fillet(edge, radius=0.2)


def _blends(body, **args):
    r = ClassifyEdgeBlends().apply(body, args)
    out = r.extras["edge_blends"]
    assert isinstance(out, list)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Fillet


def test_single_fillet_detected_with_radius():
    body = _box_one_fillet(radius=2.0)
    blends = _blends(body)
    fillets = [b for b in blends if b["kind"] == "fillet"]
    assert len(fillets) == 1, f"expected exactly 1 fillet, got {blends}"
    f = fillets[0]
    assert abs(f["radius_mm"] - 2.0) / 2.0 <= 0.02, f"radius drift: {f}"
    # quarter-cylinder spans the box height
    assert f["edge_length_mm"] > 5.0
    assert len(f["centroid"]) == 3
    assert isinstance(f["face_index"], int)
    # outer-edge rounding is convex → 'round' (omitted only when degenerate)
    assert f.get("convexity", "round") == "round"
    # no phantom chamfers on the filleted box
    assert not [b for b in blends if b["kind"] == "chamfer"], blends


# ──────────────────────────────────────────────────────────────────────────────
# Chamfer


def test_single_chamfer_detected_with_width_and_angle():
    body = _box_one_chamfer(length=1.0)
    blends = _blends(body)
    chamfers = [b for b in blends if b["kind"] == "chamfer"]
    assert len(chamfers) == 1, f"expected exactly 1 chamfer, got {blends}"
    c = chamfers[0]
    # 1×45° symmetric chamfer strip width = sqrt(2) ≈ 1.4142
    assert abs(c["width_mm"] - 2.0 ** 0.5) / (2.0 ** 0.5) <= 0.05, c
    assert abs(c["angle_deg"] - 45.0) <= 3.0, c
    assert isinstance(c["face_index"], int)
    # no phantom fillets
    assert not [b for b in blends if b["kind"] == "fillet"], blends


# ──────────────────────────────────────────────────────────────────────────────
# Negative cases — conservatism is the contract


def test_plain_box_has_no_blends():
    assert _blends(_box()) == []


def test_cylinder_side_wall_is_not_a_blend():
    """A full cylindrical face (rod / hole wall) must NEVER read as a
    fillet — this is the regression-prone false positive."""
    assert _blends(_cylinder()) == []


def test_cylinder_with_top_fillet_reports_only_the_torus():
    body = _cylinder_top_fillet(radius=1.5)
    blends = _blends(body)
    fillets = [b for b in blends if b["kind"] == "fillet"]
    assert len(fillets) == 1, f"expected exactly 1 fillet, got {blends}"
    f = fillets[0]
    assert abs(f["radius_mm"] - 1.5) / 1.5 <= 0.02, f
    # the rod's own R5 side wall must not appear even though it is now
    # tangent to the blend.
    assert all(abs(b.get("radius_mm", 0.0) - 5.0) > 0.5 for b in blends), blends


def test_min_radius_floor_filters_fillet():
    body = _box_one_fillet(radius=2.0)
    blends = _blends(body, min_radius_mm=3.0)
    assert [b for b in blends if b["kind"] == "fillet"] == []


# ──────────────────────────────────────────────────────────────────────────────
# A3-FIX (2026-06-11) — round-trip stability gates


def test_split_fillet_merges_into_one_entry():
    """Two boolean-split fragments of ONE fillet cylinder must yield a
    single merged entry — fragment counts are topology-dependent and
    broke the preserve_brep round trip (Ventilator 31 vs 54)."""
    body = _box_split_fillet(radius=2.0)
    fillets = [b for b in _blends(body) if b["kind"] == "fillet"]
    assert len(fillets) == 1, f"expected 1 merged fillet, got {fillets}"
    f = fillets[0]
    assert abs(f["radius_mm"] - 2.0) / 2.0 <= 0.02, f
    assert f["fragment_count"] == 2, f
    # merged seam length = sum of both fragments (10 - 2 notch = 8)
    assert 7.0 <= f["edge_length_mm"] <= 9.0, f


def test_split_fillet_per_face_with_merge_disabled():
    """merge_coaxial_fragments=False restores the pre-fix per-face
    entries (escape hatch / debugging)."""
    body = _box_split_fillet(radius=2.0)
    blends = _blends(body, merge_coaxial_fragments=False)
    fillets = [b for b in blends if b["kind"] == "fillet"]
    assert len(fillets) == 2, f"expected 2 per-face fillets, got {fillets}"
    assert all(f["fragment_count"] == 1 for f in fillets), fillets


def test_stub_fillet_dropped_by_length_to_radius_gate():
    """A roundover whose tangency seam is shorter than its own radius
    is a castellation / spotface wall (Crystal_SMD corners) — dropped
    by default; min_length_to_radius_ratio=0 disables the gate."""
    body = _thin_plate_stub_fillet()
    assert [b for b in _blends(body) if b["kind"] == "fillet"] == []
    kept = [
        b for b in _blends(body, min_length_to_radius_ratio=0.0)
        if b["kind"] == "fillet"
    ]
    assert len(kept) == 1, f"ratio=0 must disable the stub gate: {kept}"
    assert abs(kept[0]["radius_mm"] - 0.2) <= 0.01, kept


def test_single_fillet_carries_fragment_count_one():
    f = [b for b in _blends(_box_one_fillet()) if b["kind"] == "fillet"][0]
    assert f["fragment_count"] == 1, f


# ──────────────────────────────────────────────────────────────────────────────
# Spec / read-only contract


def test_spec_metadata_and_body_unchanged():
    spec = ClassifyEdgeBlends.spec
    assert spec.name == "classify_edge_blends"
    assert spec.category == "inspect"
    assert spec.level == "atomic"
    kinds = {pc.kind for pc in spec.post_conditions}
    assert "body_present" in kinds
    body = _box_one_fillet()
    r = ClassifyEdgeBlends().apply(body, {})
    assert r.body is body


def test_face_count_guard_returns_sentinel():
    body = _box_one_fillet()
    out = ClassifyEdgeBlends().apply(body, {"max_face_count": 3}).extras[
        "edge_blends"
    ]
    assert isinstance(out, dict) and out.get("skipped") is True
    assert out.get("reason") == "too_big"


# ──────────────────────────────────────────────────────────────────────────────
# Catalog wiring (extract_feature_catalog)


def test_catalog_carries_edge_blends_key():
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )
    body = _box_one_fillet(radius=2.0)
    # A3-FIX (2026-06-11): DEFAULT-ON — the detector is round-trip
    # stable now (same-surface aggregation + union arc gate + stub
    # gate; 6-file spot check green), so the catalog carries blends
    # without opting in. include_edge_blends=False still skips.
    cat = ExtractFeatureCatalog().apply(
        body, {"parallel": False},
    ).extras["feature_catalog"]
    assert "edge_blends" in cat
    fillets = [b for b in cat["edge_blends"] if b["kind"] == "fillet"]
    assert len(fillets) == 1
    assert abs(fillets[0]["radius_mm"] - 2.0) <= 0.05

    cat_off = ExtractFeatureCatalog().apply(
        body, {"parallel": False, "include_edge_blends": False},
    ).extras["feature_catalog"]
    assert cat_off.get("edge_blends") == []


# ──────────────────────────────────────────────────────────────────────────────
# Fidelity pairing


def test_fidelity_diff_pairs_edge_blends():
    from phone_designer.skills.reverse_engineer.feature_fidelity_diff import (
        FeatureFidelityDiff,
    )
    blend = {
        "id": 0, "kind": "fillet", "radius_mm": 2.0,
        "face_index": 4, "edge_length_mm": 10.0,
        "centroid": [10.0, 15.0, 5.0],
    }
    ca = {"edge_blends": [blend]}
    # identical catalogs self-match → ratio 1.0
    fid = FeatureFidelityDiff().apply(
        _box(), {"catalog_a": ca, "catalog_b": {"edge_blends": [dict(blend)]}},
    ).extras["feature_fidelity"]
    assert fid["by_kind"]["edge_blends"]["matched"] == 1
    assert fid["overall_match_ratio"] == 1.0

    # radius outside the 10 % dim gate → no pair, ratio drops
    far = dict(blend)
    far["radius_mm"] = 2.5
    fid2 = FeatureFidelityDiff().apply(
        _box(), {"catalog_a": ca, "catalog_b": {"edge_blends": [far]}},
    ).extras["feature_fidelity"]
    assert fid2["by_kind"]["edge_blends"]["matched"] == 0
    assert fid2["overall_match_ratio"] < 1.0
