"""extract_quality_report — quality aggregator + result_grade metadata (A9).

Fixtures: a synthetic housing (30x20x10 box, R2 fillet on one vertical edge,
through hole) exercises every default section end-to-end:

    1. Default run → all 8 sections present, each with a grade in
       {'measured', 'estimate'}, error=None, data populated.
    2. sections=['topology'] → only that section (plus _meta).
    3. Registry walk → the 4 heuristic skills carry result_grade='estimate',
       a 5-skill sample of the rest defaults to 'measured', and
       to_manifest_dict() surfaces the field.

Direct module imports — passes without manifest registration. Whole module
runs in well under 60 s (the housing has ~12 faces).
"""
from __future__ import annotations

from build123d import Axis, Box as B3dBox, Cylinder as B3dCylinder, Pos, fillet

from phone_designer.skills.inspect.extract_quality_report import (
    DEFAULT_SECTIONS,
    ExtractQualityReport,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixture builder


def _housing():
    """30x20x10 box + R2.0 fillet on one vertical edge + 4 mm through hole."""
    part = B3dBox(30.0, 20.0, 10.0)
    edge = part.edges().filter_by(Axis.Z)[0]
    part = fillet(edge, radius=2.0)
    hole = Pos(5.0, 0.0, 0.0) * B3dCylinder(radius=2.0, height=12.0)
    return part - hole


def _report(body, **args):
    r = ExtractQualityReport().apply(body, args)
    qr = r.extras["quality_report"]
    assert isinstance(qr, dict)
    return qr


# ──────────────────────────────────────────────────────────────────────────────
# 1. Default run — every section, graded, error-free


def test_default_sections_all_present_and_clean():
    qr = _report(_housing())

    section_names = [k for k in qr if not k.startswith("_")]
    assert set(section_names) == set(DEFAULT_SECTIONS), section_names

    for name in DEFAULT_SECTIONS:
        sec = qr[name]
        assert sec["error"] is None, f"{name}: {sec['error']}"
        assert sec["grade"] in {"measured", "estimate"}, f"{name}: {sec}"
        assert sec["duration_s"] >= 0.0
        assert sec["data"] is not None, f"{name}: empty data"
        # tiny housing — nothing may hit the face-count guard
        assert "guarded" not in sec, f"{name} unexpectedly guarded: {sec}"


def test_default_sections_payload_spot_checks():
    qr = _report(_housing())

    # topology — healthy solid
    assert qr["topology"]["data"]["is_valid"] is True

    # wall_thickness — between hole wall and outer face: > 0, < 30 mm
    wt = qr["wall_thickness"]["data"]
    assert 0.0 < wt["global_min"] <= 30.0, wt["global_min"]

    # edge_blends — exactly the one R2.0 fillet, no phantom chamfers
    blends = qr["edge_blends"]["data"]
    fillets = [b for b in blends if b["kind"] == "fillet"]
    assert len(fillets) == 1, blends
    assert abs(fillets[0]["radius_mm"] - 2.0) / 2.0 <= 0.05, fillets

    # mass_properties — volume below the parent box, above half of it
    vol = qr["mass_properties"]["data"]["volume_mm3"]
    assert 3000.0 < vol < 6000.0, vol

    # principal_axes — three axes on a generic solid
    assert len(qr["principal_axes"]["data"]["principal_axes"]) == 3

    # mesh_quality — tessellation produced triangles
    assert qr["mesh_quality"]["data"]["triangle_count"] > 0

    # gdt_summary — untagged body: zero PMI everywhere
    gs = qr["gdt_summary"]["data"]
    assert gs == {
        "dimensions": 0, "datums": [], "textures": 0, "welds": 0, "fcfs": 0,
    }, gs

    # draft — pull +Z: the hole wall is vertical (0 deg draft) → no crash,
    # counts dict present
    assert "counts" in qr["draft"]["data"]

    # _meta bookkeeping
    meta = qr["_meta"]
    assert meta["face_count"] > 0
    assert meta["unknown_sections"] == []


# ──────────────────────────────────────────────────────────────────────────────
# 2. Section filtering


def test_sections_filter_returns_only_requested():
    qr = _report(_housing(), sections=["topology"])
    section_names = [k for k in qr if not k.startswith("_")]
    assert section_names == ["topology"], section_names
    assert qr["topology"]["error"] is None
    assert qr["_meta"]["sections_requested"] == ["topology"]


def test_unknown_section_ignored_and_reported():
    qr = _report(_housing(), sections=["mass_properties", "bogus_section"])
    section_names = [k for k in qr if not k.startswith("_")]
    assert section_names == ["mass_properties"], section_names
    assert qr["_meta"]["unknown_sections"] == ["bogus_section"]


# ──────────────────────────────────────────────────────────────────────────────
# 3. Face-count guard — over-budget sections record guarded=True, never hang


def test_face_count_guard_marks_heavy_sections_guarded():
    qr = _report(_housing(), max_face_count=2)  # housing has ~12 faces

    # aggregator-level guard (no internal guard in these skills)
    for name in ("wall_thickness", "mesh_quality"):
        sec = qr[name]
        assert sec.get("guarded") is True, f"{name}: {sec}"
        assert sec["data"]["reason"] == "too_big"
        assert sec["error"] is None

    # pass-through guard (inner skill's own sentinel)
    for name in ("topology", "draft", "edge_blends"):
        sec = qr[name]
        assert sec.get("guarded") is True, f"{name}: {sec}"
        assert sec["error"] is None

    # cheap sections still computed
    assert qr["mass_properties"]["data"]["volume_mm3"] > 0
    assert qr["gdt_summary"]["error"] is None


# ──────────────────────────────────────────────────────────────────────────────
# 4. result_grade metadata (A9) — registry walk


def test_estimate_grade_skills_tagged():
    # Import registers the specs (direct module imports, no manifest needed).
    from phone_designer.skills.inspect import find_features  # noqa: F401
    from phone_designer.skills.inspect import modal_frequency_estimate  # noqa: F401
    from phone_designer.skills.inspect import stress_concentration_predict  # noqa: F401
    from phone_designer.skills.inspect import tolerance_stack  # noqa: F401
    from phone_designer.skills._registry import registry

    for name in (
        "modal_frequency_estimate",
        "stress_concentration_predict",
        "tolerance_stack",
        "find_features",
    ):
        spec = registry.get(name)
        assert spec.result_grade == "estimate", f"{name}: {spec.result_grade}"
        assert spec.to_manifest_dict()["result_grade"] == "estimate", name


def test_sampled_other_skills_default_measured():
    # 5-skill sample across categories — all must default to 'measured'.
    from phone_designer.skills.inspect import classify_edge_blends  # noqa: F401
    from phone_designer.skills.inspect import gdt_flatness  # noqa: F401
    from phone_designer.skills.inspect import mass_properties  # noqa: F401
    from phone_designer.skills.inspect import mesh_quality  # noqa: F401
    from phone_designer.skills.inspect import topology_health  # noqa: F401
    from phone_designer.skills._registry import registry

    for name in (
        "classify_edge_blends",
        "gdt_flatness",
        "mass_properties",
        "mesh_quality",
        "topology_health",
    ):
        spec = registry.get(name)
        assert spec.result_grade == "measured", f"{name}: {spec.result_grade}"
        assert spec.to_manifest_dict()["result_grade"] == "measured", name


def test_quality_report_sections_carry_measured_grade():
    # All 8 default inner skills are measured-grade today; the envelope must
    # echo spec.result_grade verbatim.
    qr = _report(_housing())
    for name in DEFAULT_SECTIONS:
        assert qr[name]["grade"] == "measured", (name, qr[name]["grade"])
