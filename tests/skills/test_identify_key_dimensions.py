"""Contract tests for identify_key_dimensions (Pillar VARIANTS, 2026-06-14).

identify_key_dimensions ranks the driving 'main dimensions' a UI would expose
from a feature_catalog and emits, for each, a vary_feature_catalog-RESOLVABLE
dotted_key. The two load-bearing guarantees this module pins:

  1. Ranking obeys the role contract: envelope > primary bore > pitch > wall >
     pocket (within a role, the larger dimension first).
  2. EVERY emitted dotted_key navigates through
     vary_feature_catalog._navigate_to_parent against the same catalog — so a
     UI / apply_variant_drivers can hand any key straight back to
     vary_feature_catalog and it lands.

Two layers:
  * Synthetic catalog with KNOWN main dims (always runs, CI-safe).
  * Real corpus verification on occt__linkrods / occt__screw / as1-oc-214 /
    kicad C_0603 — skipped when the OEM corpus is not checked out.
"""
from __future__ import annotations

import pathlib

import pytest

from phone_designer.skills.reverse_engineer.identify_key_dimensions import (
    IdentifyKeyDimensions,
    identify_key_dimensions,
)
from phone_designer.skills.reverse_engineer.vary_feature_catalog import (
    _navigate_to_parent,
    _split_dotted_key,
    vary_catalog,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────
def _key_resolves(catalog: dict, dotted_key: str) -> bool:
    return _navigate_to_parent(catalog, _split_dotted_key(dotted_key)) is not None


def _value_at(catalog: dict, dotted_key: str):
    nav = _navigate_to_parent(catalog, _split_dotted_key(dotted_key))
    assert nav is not None, f"dotted_key did not resolve: {dotted_key}"
    parent, last = nav
    return parent[last]


def _by_role(kd: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in kd:
        out.setdefault(e["role"], e)  # first (best-ranked) per role
    return out


# ──────────────────────────────────────────────────────────────────────────
# Synthetic catalog — known main dimensions
# ──────────────────────────────────────────────────────────────────────────
def _synthetic_catalog() -> dict:
    """A catalog with one of every driving-dimension role and KNOWN values.

    Envelope 40 x 20 x 10 mm; a confident-standard-matched M5 primary bore
    (5.0 mm) plus a smaller relief hole; a circular bolt-pattern (radius 12),
    a base thickness 3 mm, and a dominant pocket (top_d 15)."""
    return {
        "initial_bbox_mm": [0.0, 0.0, 0.0, 40.0, 20.0, 10.0],
        "base_thickness_mm": 3.0,
        "holes": [
            {  # relief / secondary drilling — larger but NOT standard-matched
                "id": 0,
                "axis_origin": [10.0, 10.0, 0.0],
                "axis_dir": [0.0, 0.0, 1.0],
                "diameters_mm": [6.2],
                "depth_mm": 4.0,
            },
            {  # the real fastener bore — M5 clearance, standard-matched
                "id": 1,
                "axis_origin": [30.0, 10.0, 0.0],
                "axis_dir": [0.0, 0.0, 1.0],
                "diameters_mm": [5.0],
                "depth_mm": 8.0,
            },
        ],
        "standard_matches": [
            {"hole_id": 0, "diameter_mm": 6.2, "best_match": None},
            {
                "hole_id": 1,
                "diameter_mm": 5.0,
                "best_match": {
                    "thread_spec": "M5",
                    "fit": "close",
                    "catalog_diameter_mm": 5.0,
                    "deviation_mm": 0.0,
                    "confidence": 1.0,
                },
            },
        ],
        "pockets": [
            {"id": 0, "axis_origin": [20.0, 10.0, 10.0],
             "top_d_mm": 15.0, "depth_mm": 2.0},
            {"id": 1, "axis_origin": [5.0, 5.0, 10.0],
             "top_d_mm": 4.0, "depth_mm": 1.0},
        ],
        "patterns": [
            {
                "pattern_kind": "circular",
                "feature_kind": "hole",
                "center": [20.0, 10.0, 0.0],
                "radius_mm": 12.0,
                "count": 6,
                "angular_pitch_deg": 60.0,
            },
            {
                "pattern_kind": "linear",
                "feature_kind": "hole",
                "spacing_mm": 5.0,
                "count": 3,
                "positions": [[0, 0, 0], [5, 0, 0], [10, 0, 0]],
                "direction": [1.0, 0.0, 0.0],
            },
        ],
        "bosses": [], "ribs": [], "lugs": [], "symmetries": [],
        "sweep_features": [], "loft_features": [], "revolve_features": [],
        "edge_blends": [],
    }


def test_ranking_role_priority_envelope_first():
    """Envelope > primary bore > pitch > wall > pocket."""
    cat = _synthetic_catalog()
    kd = identify_key_dimensions(cat)
    roles = [e["role"] for e in kd]

    # All three envelope dims come before any non-envelope dim.
    first_non_env = next(i for i, r in enumerate(roles) if r != "envelope")
    assert roles[:first_non_env] == ["envelope"] * 3
    # The non-envelope ordering respects the priority ladder.
    order = {"primary_bore": 0, "pitch": 1, "wall": 2, "pocket": 3}
    tail = [order[r] for r in roles[first_non_env:]]
    assert tail == sorted(tail), f"role ladder violated: {roles}"


def test_envelope_dims_named_and_largest_first():
    """The three envelope dims are length(40) > width(20) > height(10)."""
    cat = _synthetic_catalog()
    kd = identify_key_dimensions(cat)
    env = [e for e in kd if e["role"] == "envelope"]
    assert [e["value_mm"] for e in env] == [40.0, 20.0, 10.0]
    assert [e["name"] for e in env] == [
        "housing_length", "housing_width", "housing_height",
    ]


def test_primary_bore_is_the_standard_matched_hole_not_the_larger_relief():
    """The driving bore is the standard-matched M5 (5.0), not the 6.2 relief."""
    cat = _synthetic_catalog()
    kd = identify_key_dimensions(cat)
    bore = _by_role(kd)["primary_bore"]
    assert bore["value_mm"] == 5.0
    assert bore["dotted_key"] == "holes.1.diameters_mm.0"
    assert bore["standard_match"] is not None
    assert bore["standard_match"]["thread_spec"] == "M5"


def test_pitch_prefers_largest_pattern_radius():
    cat = _synthetic_catalog()
    kd = identify_key_dimensions(cat)
    pitch = _by_role(kd)["pitch"]
    assert pitch["value_mm"] == 12.0
    assert pitch["dotted_key"] == "patterns.0.radius_mm"


def test_wall_and_pocket_emitted():
    cat = _synthetic_catalog()
    kd = identify_key_dimensions(cat)
    by = _by_role(kd)
    assert by["wall"]["value_mm"] == 3.0
    assert by["wall"]["dotted_key"] == "base_thickness_mm"
    assert by["pocket"]["value_mm"] == 15.0  # dominant (largest top_d) pocket
    assert by["pocket"]["dotted_key"] == "pockets.0.top_d_mm"


def test_every_dotted_key_resolves_through_navigate_to_parent():
    """THE contract: every emitted dotted_key navigates the same catalog."""
    cat = _synthetic_catalog()
    kd = identify_key_dimensions(cat)
    assert kd, "no key dimensions emitted"
    for e in kd:
        assert _key_resolves(cat, e["dotted_key"]), (
            f"dotted_key does not resolve: {e['dotted_key']} (role {e['role']})"
        )


def test_dotted_keys_round_trip_through_vary_feature_catalog():
    """Handing each key to vary_feature_catalog as a per_feature_scale actually
    changes the targeted value (the key is not just navigable but LIVE)."""
    cat = _synthetic_catalog()
    kd = identify_key_dimensions(cat)
    for e in kd:
        before = _value_at(cat, e["dotted_key"])
        varied = vary_catalog(cat, per_feature_scale={e["dotted_key"]: 2.0})
        after = _value_at(varied, e["dotted_key"])
        # scalar fields double; bbox-component (envelope) also doubles.
        if isinstance(before, (int, float)):
            assert after == pytest.approx(2.0 * before), e["dotted_key"]


def test_ranks_are_contiguous_and_zero_based():
    cat = _synthetic_catalog()
    kd = identify_key_dimensions(cat)
    assert [e["rank"] for e in kd] == list(range(len(kd)))


def test_only_primary_bore_carries_standard_match_field():
    cat = _synthetic_catalog()
    kd = identify_key_dimensions(cat)
    for e in kd:
        if e["role"] == "primary_bore":
            assert "standard_match" in e
        else:
            assert "standard_match" not in e


def test_skipped_catalog_yields_empty_menu():
    assert identify_key_dimensions({"skipped": True, "reason": "too_big"}) == []
    assert identify_key_dimensions({}) == []


def test_missing_bbox_still_emits_other_roles():
    """No initial_bbox_mm ⇒ no envelope dims, but bore/wall/pocket survive."""
    cat = _synthetic_catalog()
    cat.pop("initial_bbox_mm")
    kd = identify_key_dimensions(cat)
    roles = {e["role"] for e in kd}
    assert "envelope" not in roles
    assert {"primary_bore", "wall", "pocket"} <= roles
    for e in kd:
        assert _key_resolves(cat, e["dotted_key"])


def test_skill_wrapper_emits_key_dimensions_extra():
    """The @skill wrapper returns extras['key_dimensions'] and leaves the body
    unchanged (post body_present)."""
    cat = _synthetic_catalog()
    sentinel = object()
    res = IdentifyKeyDimensions().apply(sentinel, {"catalog": cat})
    assert res.body is sentinel
    kd = res.extras["key_dimensions"]
    assert kd and kd[0]["role"] == "envelope"


# ──────────────────────────────────────────────────────────────────────────
# Real corpus verification (skipped when OEM corpus absent)
# ──────────────────────────────────────────────────────────────────────────
_CORPUS_FILES = {
    "linkrods": _REPO_ROOT / "corpus" / "oem" / "occt__linkrods.step",
    "screw": _REPO_ROOT / "corpus" / "oem" / "complex" / "occt__screw.step",
    "as1-214": _REPO_ROOT / "corpus" / "oem" / "pythonocc__as1-oc-214.step",
    "C_0603": _REPO_ROOT / "corpus" / "oem" / "kicad__C_0603_1608Metric.step",
}


def _real_catalog(path: pathlib.Path) -> dict:
    from phone_designer.skills.create.import_step import ImportStep
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )
    body = ImportStep().apply(None, {"path": str(path)}).body
    return ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]


def _require(name: str) -> pathlib.Path:
    p = _CORPUS_FILES[name]
    if not p.exists():
        pytest.skip(f"OEM corpus file not present: {p}")
    return p


def _assert_all_keys_resolve_and_match(cat: dict, kd: list[dict]) -> None:
    assert kd, "no key dimensions emitted for real catalog"
    for e in kd:
        assert _key_resolves(cat, e["dotted_key"]), (
            f"dotted_key does not resolve on real catalog: {e['dotted_key']}"
        )


def _envelope_matches_bbox(cat: dict, kd: list[dict]) -> None:
    bb = cat["initial_bbox_mm"]
    extents = {
        "housing_length": abs(bb[3] - bb[0]),
        "housing_width": abs(bb[4] - bb[1]),
        "housing_height": abs(bb[5] - bb[2]),
    }
    for e in kd:
        if e["role"] == "envelope":
            assert e["value_mm"] == pytest.approx(extents[e["name"]], abs=1e-3)


@pytest.mark.parametrize("name", ["linkrods", "screw"])
def test_real_top_ranked_are_envelope_plus_primary_bore(name):
    """occt__linkrods / occt__screw: top items = 3 envelope dims then the
    primary bore; every dotted_key resolves; envelope values match the bbox."""
    cat = _real_catalog(_require(name))
    if cat.get("skipped"):
        pytest.skip(f"{name} catalog skipped: {cat.get('reason')}")
    kd = identify_key_dimensions(cat)
    _assert_all_keys_resolve_and_match(cat, kd)
    _envelope_matches_bbox(cat, kd)

    roles = [e["role"] for e in kd]
    assert roles[:3] == ["envelope"] * 3, f"{name}: top 3 not envelope: {roles}"
    assert "primary_bore" in roles, f"{name}: no primary bore detected"
    # The primary bore is the first non-envelope item.
    assert roles[3] == "primary_bore", f"{name}: bore not rank 3: {roles}"
    bore = _by_role(kd)["primary_bore"]
    assert _value_at(cat, bore["dotted_key"]) == pytest.approx(
        bore["value_mm"], abs=1e-3,
    )


def test_real_as1_214_envelope_tops_and_resolves():
    """as1-oc-214: a large assembly part — envelope dims top the menu and
    every dotted_key resolves (it has no through-holes, so no bore is
    required)."""
    cat = _real_catalog(_require("as1-214"))
    if cat.get("skipped"):
        pytest.skip(f"as1-214 catalog skipped: {cat.get('reason')}")
    kd = identify_key_dimensions(cat)
    _assert_all_keys_resolve_and_match(cat, kd)
    _envelope_matches_bbox(cat, kd)
    assert [e["role"] for e in kd][:3] == ["envelope"] * 3


def test_real_kicad_C_0603_envelope_is_1p6_by_0p8():
    """kicad C_0603 chip: the envelope (1.6 x 0.8) ranks top."""
    cat = _real_catalog(_require("C_0603"))
    if cat.get("skipped"):
        pytest.skip(f"C_0603 catalog skipped: {cat.get('reason')}")
    kd = identify_key_dimensions(cat)
    _assert_all_keys_resolve_and_match(cat, kd)
    env = [e for e in kd if e["role"] == "envelope"]
    assert env[0]["rank"] == 0, "envelope must rank top on the chip"
    by_name = {e["name"]: e["value_mm"] for e in env}
    assert by_name["housing_length"] == pytest.approx(1.6, abs=1e-3)
    assert by_name["housing_width"] == pytest.approx(0.8, abs=1e-3)
