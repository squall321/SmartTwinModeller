"""Schema-coupling contract for vary_feature_catalog's whitelists (plan P1).

Why this exists: COMPLEX-CAD pass-23 added ``entry_origin`` /
``entry_depth_mm`` to classify_holes WITHOUT extending the
vary_feature_catalog whitelists. plan_from_feature_catalog PREFERS those
fields for box-mode hole cuts, so every scaled variant emitted holes at the
UNSCALED position → zero-delta SKIP → bare slab — and nothing failed.

This module makes that class of omission impossible to repeat silently:

  1. Build a REAL catalog (fixtures/make_simple_watch.py housing — committed,
     CI-safe, no OEM corpus needed) via ExtractFeatureCatalog.
  2. Walk every nested dict key. Any key with a dimensional-LOOKING name
     (``(_mm$)|origin|center|position|bbox|centroid``) must be either in the
     scale whitelists or in the explicit DIMENSIONLESS_FIELDS exclusion set
     below. A future detector field with a dimensional name fails this test
     until someone consciously classifies it.
  3. A direct unit test pins the pass-23 fields' scaling behaviour (and that
     plane_normal — a direction — is never scaled).
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
    ExtractFeatureCatalog,
)
from phone_designer.skills.reverse_engineer.vary_feature_catalog import (
    _SCALAR_DIM_FIELDS,
    _VECTOR_DIM_FIELDS,
    vary_catalog,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Keys whose name LOOKS dimensional. Case-sensitive, substring match (except
# the _mm$ anchor) — deliberately greedy so new detector fields get caught.
_DIMENSIONAL_KEY_RE = re.compile(r"(_mm$)|origin|center|position|bbox|centroid")

# Explicit exclusion set: keys the walk finds that match the regex (or could)
# but must NOT be uniform-scaled. Every entry needs a reason.
DIMENSIONLESS_FIELDS: frozenset[str] = frozenset({
    # ── unit direction vectors — directions must NEVER scale ───────────────
    "axis_dir",
    "axis_direction",
    "plane_normal",
    "normal",
    "axis",
    "direction",
    # ── standard-catalog identity values (match_standard_hole best_match) ──
    # An M3's nominal clearance is 3.4 mm whatever the body scale; scaling
    # it would fabricate a diameter that exists in no catalog. The match
    # simply goes stale on scaled variants — re-matching is
    # normalize_varied_catalog's job (plan P6). deviation_mm is the residual
    # against that fixed nominal, equally non-scalable.
    "catalog_diameter_mm",
    "deviation_mm",
})


def _load_make_simple_watch():
    """Import fixtures/make_simple_watch.py by path (fixtures/ is not a
    package; tests run with PYTHONPATH=src only)."""
    path = _REPO_ROOT / "fixtures" / "make_simple_watch.py"
    spec = importlib.util.spec_from_file_location("make_simple_watch", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _walk_keys(node, found: set[str]) -> None:
    """Recursively collect every dict key (at any nesting depth)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str):
                found.add(k)
            _walk_keys(v, found)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _walk_keys(item, found)


@pytest.fixture(scope="module")
def real_catalog() -> dict:
    """Feature catalog of the synthetic watch housing (44 mm OD, crown +
    lug-pin holes, display pocket, mirror symmetry — exercises every
    detector family that feeds the catalog)."""
    housing = _load_make_simple_watch().make_housing()
    cat = ExtractFeatureCatalog().apply(housing, {}).extras["feature_catalog"]
    assert not cat.get("skipped"), f"catalog skipped: {cat}"
    return cat


def test_catalog_exercises_the_pass23_fields(real_catalog):
    """Guard the guard: the fixture must actually emit the fields whose
    omission caused the pass-23 bug, otherwise the contract walk below
    proves nothing."""
    keys: set[str] = set()
    _walk_keys(real_catalog, keys)
    assert len(real_catalog.get("holes") or []) >= 1, "fixture lost its holes"
    assert "entry_origin" in keys
    assert "entry_depth_mm" in keys
    assert "plane_origin" in keys  # detect_mirror_symmetry always emits planes


def test_every_dimensional_looking_key_is_classified(real_catalog):
    """THE contract: every nested key matching the dimensional regex must be
    whitelisted for scaling or explicitly excluded above. New detector
    fields fail here until consciously classified."""
    keys: set[str] = set()
    _walk_keys(real_catalog, keys)
    dimensional = {k for k in keys if _DIMENSIONAL_KEY_RE.search(k)}
    assert dimensional, "walk found no dimensional keys — fixture broken?"

    whitelist = _SCALAR_DIM_FIELDS | _VECTOR_DIM_FIELDS
    unclassified = sorted(dimensional - whitelist - DIMENSIONLESS_FIELDS)
    assert not unclassified, (
        "Catalog emits dimensional-looking field(s) unknown to "
        "vary_feature_catalog: "
        f"{unclassified}. Either add them to _SCALAR_DIM_FIELDS / "
        "_VECTOR_DIM_FIELDS (they scale with the body) or to "
        "DIMENSIONLESS_FIELDS in this test (with a reason). Silent omission "
        "is what produced the pass-23 bare-slab bug."
    )


def test_no_direction_vector_leaked_into_whitelist():
    """Directions must never scale — pin them out of the whitelists."""
    forbidden = {"axis_dir", "plane_normal", "axis_direction", "normal"}
    leaked = forbidden & (_SCALAR_DIM_FIELDS | _VECTOR_DIM_FIELDS)
    assert not leaked, f"direction vector(s) in scale whitelist: {sorted(leaked)}"


def test_scale_2x_scales_entry_and_plane_origin_but_not_normals():
    """Direct pin of the P1 repair on a synthetic catalog: entry_origin /
    entry_depth_mm / plane_origin scale; plane_normal stays unit."""
    cat = {
        "holes": [{
            "axis_origin": [1, 2, 3],
            "entry_origin": [1, 2, 3],
            "entry_depth_mm": 5,
            "depth_mm": 5,
            "diameters_mm": [2],
        }],
        "symmetries": [{
            "plane_origin": [1, 1, 1],
            "plane_normal": [0, 0, 1],
        }],
    }
    varied = vary_catalog(cat, scale_factor=2.0)

    hole = varied["holes"][0]
    assert hole["entry_origin"] == [2.0, 4.0, 6.0]
    assert hole["entry_depth_mm"] == 10.0
    assert hole["axis_origin"] == [2.0, 4.0, 6.0]
    assert hole["depth_mm"] == 10.0
    assert hole["diameters_mm"] == [4.0]

    sym = varied["symmetries"][0]
    assert sym["plane_origin"] == [2.0, 2.0, 2.0]
    assert sym["plane_normal"] == [0, 0, 1], "direction vector must NOT scale"

    # Input catalog untouched (deep copy contract).
    assert cat["holes"][0]["entry_origin"] == [1, 2, 3]
    assert cat["symmetries"][0]["plane_origin"] == [1, 1, 1]
