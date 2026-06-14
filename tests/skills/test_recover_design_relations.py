"""Contract tests for recover_design_relations (Pillar VARIANTS, 2026-06-14).

recover_design_relations lifts a lightweight relations graph from a
feature_catalog so a named-driver edit (apply_variant_drivers) can propagate
coherently. The load-bearing guarantees pinned here:

  1. Each relation kind (mirror / counterbore / pattern_pitch / concentric) is
     recovered from a synthetic catalog with KNOWN couplings.
  2. EVERY member dotted_key navigates through
     vary_feature_catalog._navigate_to_parent against the same catalog (so the
     downstream variation pipeline can land any member key).
  3. Conservatism: noise (equal-diameter "counterbore", a single hole, a sub-
     threshold mirror score) produces NO relation.

Two layers: synthetic (always runs, CI-safe) + real corpus (occt__linkrods,
skipped when the OEM corpus is absent).
"""
from __future__ import annotations

import pathlib

import pytest

from phone_designer.skills.reverse_engineer.recover_design_relations import (
    RecoverDesignRelations,
    recover_design_relations,
)
from phone_designer.skills.reverse_engineer.vary_feature_catalog import (
    _navigate_to_parent,
    _split_dotted_key,
    vary_catalog,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────
def _key_resolves(catalog: dict, dotted_key: str) -> bool:
    return _navigate_to_parent(catalog, _split_dotted_key(dotted_key)) is not None


def _by_kind(rels: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rels:
        out.setdefault(r["kind"], []).append(r)
    return out


def _synthetic_catalog() -> dict:
    """Known couplings:

    * counterbore — hole 0 has diameters [5.0, 9.0] (cb/through ratio 1.8).
    * mirror      — holes 1 & 2 are a Ø4 pair reflected across the YZ plane at
                    x=20 (symmetry_score 0.9).
    * concentric  — holes 3 & 4 share the same +Z axis line at (5, 5).
    * pattern_pitch — one linear pattern (spacing 5) and one circular (radius 12).
    """
    return {
        "initial_bbox_mm": [0.0, 0.0, 0.0, 40.0, 20.0, 10.0],
        "base_thickness_mm": 3.0,
        "holes": [
            {  # 0 — counterbore seat (Ø9) over a Ø5 through bore
                "id": 0, "type": "counterbore",
                "axis_origin": [10.0, 10.0, 0.0], "axis_dir": [0.0, 0.0, 1.0],
                "diameters_mm": [5.0, 9.0], "depth_mm": 8.0,
            },
            {  # 1 — mirror partner of 2 (Ø4) on the left
                "id": 1, "type": "simple",
                "axis_origin": [12.0, 6.0, 0.0], "axis_dir": [0.0, 0.0, 1.0],
                "diameters_mm": [4.0], "depth_mm": 5.0,
            },
            {  # 2 — mirror partner of 1 (Ø4) reflected across x=20
                "id": 2, "type": "simple",
                "axis_origin": [28.0, 6.0, 0.0], "axis_dir": [0.0, 0.0, 1.0],
                "diameters_mm": [4.0], "depth_mm": 5.0,
            },
            {  # 3 — co-axial with 4 (same +Z line at 5,5)
                "id": 3, "type": "simple",
                "axis_origin": [5.0, 5.0, 0.0], "axis_dir": [0.0, 0.0, 1.0],
                "diameters_mm": [3.0], "depth_mm": 2.0,
            },
            {  # 4 — co-axial with 3
                "id": 4, "type": "simple",
                "axis_origin": [5.0, 5.0, 4.0], "axis_dir": [0.0, 0.0, 1.0],
                "diameters_mm": [3.0], "depth_mm": 2.0,
            },
        ],
        "pockets": [],
        "patterns": [
            {
                "pattern_kind": "linear", "feature_kind": "hole",
                "direction": [1.0, 0.0, 0.0], "spacing_mm": 5.0, "count": 3,
                "positions": [[0, 0, 0], [5, 0, 0], [10, 0, 0]],
            },
            {
                "pattern_kind": "circular", "feature_kind": "hole",
                "center": [20.0, 10.0, 0.0], "axis": [0.0, 0.0, 1.0],
                "radius_mm": 12.0, "count": 4,
                "positions": [
                    [32.0, 10.0, 0.0], [20.0, 22.0, 0.0],
                    [8.0, 10.0, 0.0], [20.0, -2.0, 0.0],
                ],
            },
        ],
        "symmetries": [
            {"plane_origin": [20.0, 10.0, 0.0], "plane_normal": [1.0, 0.0, 0.0],
             "symmetry_score": 0.9},
        ],
        "bosses": [], "ribs": [], "lugs": [],
    }


# ──────────────────────────────────────────────────────────────────────────
# per-kind recovery
# ──────────────────────────────────────────────────────────────────────────
def test_counterbore_relation_ratio():
    cat = _synthetic_catalog()
    rels = recover_design_relations(cat)
    cbs = _by_kind(rels).get("counterbore") or []
    assert len(cbs) == 1, rels
    cb = cbs[0]
    assert cb["rule"] == "ratio"
    assert cb["value"] == pytest.approx(9.0 / 5.0)  # 1.8
    through_key, cb_key = cb["members"]
    # through bore = smallest diameter, cb seat = largest.
    assert through_key == "holes.0.diameters_mm.0"
    assert cb_key == "holes.0.diameters_mm.1"
    assert cb["confidence"] >= 0.7


def test_mirror_relation_pairs_reflected_holes():
    cat = _synthetic_catalog()
    rels = recover_design_relations(cat)
    mirrors = _by_kind(rels).get("mirror") or []
    assert len(mirrors) == 1, rels
    m = mirrors[0]
    assert m["rule"] == "reflect"
    assert m["plane_label"] == "YZ"
    assert set(m["members"]) == {"holes.1.diameters_mm.0", "holes.2.diameters_mm.0"}
    assert m["confidence"] == pytest.approx(0.9)


def test_concentric_relation_groups_coaxial_holes():
    cat = _synthetic_catalog()
    rels = recover_design_relations(cat)
    conc = _by_kind(rels).get("concentric") or []
    assert len(conc) == 1, rels
    c = conc[0]
    assert c["rule"] == "equal"
    assert set(c["members"]) == {"holes.3.axis_origin", "holes.4.axis_origin"}


def test_pattern_pitch_relations_linear_and_circular():
    cat = _synthetic_catalog()
    rels = recover_design_relations(cat)
    pp = _by_kind(rels).get("pattern_pitch") or []
    assert len(pp) == 2, rels
    handles = {tuple(r["members"][:1]): r for r in pp}
    # linear handle = spacing_mm, circular handle = radius_mm (no pitch_radius)
    assert ("patterns.0.spacing_mm",) in handles
    assert ("patterns.1.radius_mm",) in handles
    lin = handles[("patterns.0.spacing_mm",)]
    assert lin["value"] == pytest.approx(5.0)
    assert lin["rule"] == "equal"


def test_every_member_dotted_key_resolves():
    """THE contract: every member key navigates the same catalog."""
    cat = _synthetic_catalog()
    rels = recover_design_relations(cat)
    assert rels
    for r in rels:
        for m in r["members"]:
            assert _key_resolves(cat, m), f"{r['kind']} member did not resolve: {m}"


def test_relation_members_round_trip_through_vary_feature_catalog():
    """Each scalar member key is LIVE — vary_feature_catalog actually changes
    the targeted value."""
    cat = _synthetic_catalog()
    rels = recover_design_relations(cat)
    for r in rels:
        for m in r["members"]:
            nav = _navigate_to_parent(cat, _split_dotted_key(m))
            assert nav is not None
            parent, last = nav
            before = parent[last]
            if not isinstance(before, (int, float)):
                continue  # positions are vectors — covered elsewhere
            varied = vary_catalog(cat, per_feature_scale={m: 2.0})
            nav2 = _navigate_to_parent(varied, _split_dotted_key(m))
            assert nav2[0][nav2[1]] == pytest.approx(2.0 * before), m


# ──────────────────────────────────────────────────────────────────────────
# conservatism — noise must NOT produce relations
# ──────────────────────────────────────────────────────────────────────────
def test_equal_diameters_are_not_a_counterbore():
    cat = {
        "holes": [{"id": 0, "type": "simple", "diameters_mm": [6.0, 6.0],
                   "axis_origin": [0, 0, 0], "axis_dir": [0, 0, 1]}],
    }
    rels = recover_design_relations(cat)
    assert not _by_kind(rels).get("counterbore")


def test_marginal_ratio_below_floor_is_not_a_counterbore():
    """A 1.02× diameter pair is measurement noise, not a seat."""
    cat = {
        "holes": [{"id": 0, "type": "simple", "diameters_mm": [5.0, 5.1],
                   "axis_origin": [0, 0, 0], "axis_dir": [0, 0, 1]}],
    }
    rels = recover_design_relations(cat)
    assert not _by_kind(rels).get("counterbore")


def test_low_symmetry_score_yields_no_mirror():
    cat = _synthetic_catalog()
    cat["symmetries"][0]["symmetry_score"] = 0.5  # below _MIRROR_MIN_CONF
    rels = recover_design_relations(cat)
    assert not _by_kind(rels).get("mirror")


def test_single_hole_has_no_concentric_relation():
    cat = {
        "holes": [{"id": 0, "type": "simple", "diameters_mm": [3.0],
                   "axis_origin": [1, 1, 0], "axis_dir": [0, 0, 1]}],
    }
    rels = recover_design_relations(cat)
    assert not _by_kind(rels).get("concentric")


def test_parallel_but_laterally_offset_axes_are_not_concentric():
    cat = {
        "holes": [
            {"id": 0, "type": "simple", "diameters_mm": [3.0],
             "axis_origin": [0, 0, 0], "axis_dir": [0, 0, 1]},
            {"id": 1, "type": "simple", "diameters_mm": [3.0],
             "axis_origin": [10, 0, 0], "axis_dir": [0, 0, 1]},  # 10 mm offset
        ],
    }
    rels = recover_design_relations(cat)
    assert not _by_kind(rels).get("concentric")


def test_skipped_catalog_yields_no_relations():
    assert recover_design_relations({"skipped": True, "reason": "too_big"}) == []
    assert recover_design_relations({}) == []


def test_input_catalog_is_not_mutated():
    cat = _synthetic_catalog()
    import copy
    snapshot = copy.deepcopy(cat)
    recover_design_relations(cat)
    assert cat == snapshot


def test_skill_wrapper_emits_design_relations_extra():
    cat = _synthetic_catalog()
    sentinel = object()
    res = RecoverDesignRelations().apply(sentinel, {"catalog": cat})
    assert res.body is sentinel
    rels = res.extras["design_relations"]
    assert {r["kind"] for r in rels} >= {
        "mirror", "counterbore", "pattern_pitch", "concentric",
    }


# ──────────────────────────────────────────────────────────────────────────
# Real corpus verification (skipped when OEM corpus absent)
# ──────────────────────────────────────────────────────────────────────────
_LINKRODS = _REPO_ROOT / "corpus" / "oem" / "complex" / "occt__linkrods.step"


def _real_catalog(path: pathlib.Path) -> dict:
    from phone_designer.skills.create.import_step import ImportStep
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )
    body = ImportStep().apply(None, {"path": str(path)}).body
    return ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]


@pytest.mark.requires_oem
@pytest.mark.slow
@pytest.mark.skipif(not _LINKRODS.exists(), reason=f"corpus STEP not found: {_LINKRODS}")
def test_real_linkrods_relations_resolve():
    """On occt__linkrods every emitted relation's member keys resolve through
    the variation pipeline (no stale index leaks into a real catalog)."""
    cat = _real_catalog(_LINKRODS)
    if cat.get("skipped"):
        pytest.skip(f"linkrods catalog skipped: {cat.get('reason')}")
    rels = recover_design_relations(cat)
    for r in rels:
        assert r["confidence"] >= 0.7
        for m in r["members"]:
            assert _key_resolves(cat, m), (
                f"{r['kind']} member did not resolve on real catalog: {m}"
            )
