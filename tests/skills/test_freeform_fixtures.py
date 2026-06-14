"""PILLAR FREEFORM fixture tests (phase-3, 2026-06-14).

Two guarantees for the freeform corpus lane:

  1. Each committed builder (``fixtures/freeform/__init__.py``) produces a
     single valid solid whose volume matches the builder's closed-form
     analytic expectation within ±2 % — so the analytic ground truth recorded
     in ``corpus/baselines/box_freeform.json`` is trustworthy.

  2. The silhouette fixture's recovered profile base beats a plain bbox box
     base on hausdorff (the anti-fake proof) — measured exactly the way the
     planner's revert-guard measures it (``_profile_base_step_args`` +
     ``_score_reconstruction``).

The reconstruction proof is slow (full box-mode round-trip ×2). It is run on
the single canonical silhouette fixture only and marked ``slow`` so a fast unit
pass can deselect it.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_PROJECT = Path(__file__).resolve().parents[2]
_FIXTURES = _PROJECT / "fixtures"
if str(_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_FIXTURES))

import freeform  # noqa: E402  (fixtures/freeform/__init__.py)

#: relative volume tolerance — matches scripts/build_freeform_fixtures.py.
VOLUME_REL_TOL = 0.02


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    shape = body.wrapped if hasattr(body, "wrapped") else body
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


# ──────────────────────────────────────────────────────────────────────────────
# 1. Builders produce valid single solids with the expected analytic volumes.


@pytest.mark.parametrize("name", list(freeform.BUILDERS.keys()))
def test_builder_single_solid_with_analytic_volume(name: str):
    part, analytic_vol = freeform.BUILDERS[name]()

    solids = part.solids()
    assert len(solids) == 1, f"{name}: expected 1 solid, got {len(solids)}"

    measured = _volume(part)
    assert measured > 0.0, f"{name}: non-positive volume {measured}"
    rel = abs(measured - analytic_vol) / analytic_vol
    assert rel <= VOLUME_REL_TOL, (
        f"{name}: volume drift {rel * 100:.3f}% > {VOLUME_REL_TOL * 100:.0f}% "
        f"(measured={measured:.4f}, analytic={analytic_vol:.4f})"
    )


def test_every_mechanism_covered():
    """The four builders exercise four DISTINCT freeform mechanisms."""
    mechs = {freeform.MECHANISM[name] for name in freeform.BUILDERS}
    assert mechs == {"silhouette_extrude", "loft_section", "revolve", "sweep"}


def test_rounded_plate_analytic_matches_formula():
    """Sanity: the rounded-plate builder's analytic volume equals the
    rounded-rectangle prism formula (independent re-derivation)."""
    p = freeform.ROUNDED_PLATE
    _part, vol = freeform.build_rounded_plate()
    expected = (
        p["length_mm"] * p["width_mm"]
        - (4.0 - math.pi) * p["corner_r_mm"] ** 2
    ) * p["height_mm"]
    assert vol == pytest.approx(expected, rel=1e-9)


# ──────────────────────────────────────────────────────────────────────────────
# 2. Anti-fake proof: the silhouette profile base beats the box base on
#    hausdorff for the prismatic rounded-plate fixture.


def _import_body(name: str):
    from phone_designer.skills.create.import_step import ImportStep

    step = _PROJECT / "fixtures" / "freeform" / f"{name}.step"
    if not step.is_file():
        pytest.skip(
            f"{step} missing — run scripts/build_freeform_fixtures.py first"
        )
    return ImportStep().apply(None, {"path": str(step)}).body


@pytest.mark.slow
def test_silhouette_profile_base_beats_box_on_hausdorff():
    """The rounded-plate's recovered silhouette base reconstructs strictly
    tighter than a bbox box base — the headline anti-fake guarantee.

    Measured exactly as the planner's revert-guard does: build the box plan,
    score it, swap ``s_base`` to the recovered ``extrude_profile_world``, score
    that, and compare hausdorff in the same box frame.
    """
    import copy

    from phone_designer.skills.reverse_engineer import (
        plan_from_feature_catalog as P,
    )
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )

    body = _import_body("rounded_plate")
    cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
    bb = cat.get("initial_bbox_mm")
    assert isinstance(bb, (list, tuple)) and len(bb) >= 6
    bbox = tuple(float(c) for c in bb[:6])

    # The body must be recognised as prismatic for a profile base to exist.
    assert P._classify_base_topology(body, bbox) == "prismatic"
    prof_args = P._profile_base_step_args(body, bbox, 1.0)
    assert prof_args is not None, "silhouette profile base not recovered"

    box_plan = P._build_plan(
        cat, body=body, base_step_kind="box", base_profile_mode="off"
    )
    alt_plan = copy.deepcopy(box_plan)
    swapped = False
    for st in alt_plan.get("steps") or []:
        if str(st.get("id", "")).startswith("s_base"):
            st["skill"] = "extrude_profile_world"
            st["args"] = prof_args
            swapped = True
            break
    assert swapped, "no s_base step to swap"

    box_score = P._score_reconstruction(box_plan, body, bbox)
    alt_score = P._score_reconstruction(alt_plan, body, bbox)
    assert box_score is not None and alt_score is not None
    _box_match, box_haus = box_score
    _alt_match, alt_haus = alt_score

    # MATERIALLY better — the rounded corners the box over-covers are removed
    # by the recovered outline. Require a clear margin, not a hair.
    assert alt_haus < box_haus, (
        f"profile base hausdorff {alt_haus:.4f} not < box {box_haus:.4f}"
    )
    assert alt_haus < 0.5 * box_haus, (
        f"profile base only marginally better: {alt_haus:.4f} vs {box_haus:.4f}"
    )
