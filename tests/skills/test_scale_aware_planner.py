"""Scale-aware planner — plan item P5 (COMPLEX-CAD pass-25, 2026-06-10).

``PlanFromFeatureCatalog.Args.dimension_scale`` multiplies every absolute-mm
guard/clamp/default in the planner (200 mm depth caps, the 20 mm mounting-pad
cap, the ``_MIN_EMITTED_CUT_MM3`` floor × scale³, …) so a uniformly scaled
catalog gets proportionally scaled clamps instead of the original-part ones.

Three contracts under test:

  (a) scale-invariant clamping — a 250 mm-deep hole clamps to 200 at s=1;
      the SAME hole varied ×2 (→ 500 mm) with dimension_scale=2 clamps to
      min(500, 400) = 400; a small hole that dies at the absolute
      ``_MIN_EMITTED_CUT_MM3`` floor survives the ×0.25³-scaled floor.
  (b) s=1.0 bit-identity — plan dicts for the linkrods catalog with and
      without the dimension_scale arg are deep-equal (corpus safety: the
      preserve_brep self-match hard constraint relies on this).
  (c) step-count / step-kind invariance across s ∈ {0.25, 1, 4} for the
      linkrods catalog (vary FIRST via vary_catalog_ex, then plan with the
      matching dimension_scale) — every guard now scales homogeneously, so
      the emitted plan structure must not depend on the uniform scale.
"""
from __future__ import annotations

import copy
import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_LINKRODS = _REPO_ROOT / "corpus" / "oem" / "complex" / "occt__linkrods.step"

_needs_linkrods = pytest.mark.skipif(
    not _LINKRODS.exists(),
    reason=f"corpus STEP not found: {_LINKRODS}",
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures


def _plan(body, catalog: dict, **extra_args) -> dict:
    from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
        PlanFromFeatureCatalog,
    )

    args = {"catalog": catalog, "base_step_kind": "box", **extra_args}
    return PlanFromFeatureCatalog().apply(body, args).extras["generated_plan"]


def _hole_steps(plan: dict) -> list[dict]:
    return [s for s in plan.get("steps") or [] if s.get("skill") == "hole"]


def _synthetic_catalog(hole_depth_mm: float, hole_d_mm: float) -> dict:
    """One top-drilled hole on a 300×80×40 body (bbox deliberately offset
    from the origin so box mode uses a non-identity feat_shift, i.e. the
    real scaled-rebuild code path)."""
    return {
        "initial_bbox_mm": [0.0, 0.0, 0.0, 300.0, 80.0, 40.0],
        "holes": [{
            "id": "h0",
            "type": "simple",
            "diameters_mm": [hole_d_mm],
            "depth_mm": hole_depth_mm,
            "axis_origin": [150.0, 40.0, 40.0],
            "axis_dir": [0.0, 0.0, -1.0],
        }],
        "pockets": [],
        "bosses": [],
        "ribs": [],
        "lugs": [],
        "patterns": [],
        "symmetries": [],
        "standard_matches": [],
    }


@pytest.fixture(scope="module")
def slab_body():
    from phone_designer.skills.create.box import Box

    return Box().apply(None, {
        "length_mm": 50.0, "width_mm": 50.0, "height_mm": 10.0,
    }).body


@pytest.fixture(scope="module")
def linkrods():
    """(body, feature_catalog) for the corpus linkrods part."""
    from phone_designer.skills.create.import_step import ImportStep
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )

    body = ImportStep().apply(None, {"path": str(_LINKRODS)}).body
    assert body is not None
    catalog = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
    assert not catalog.get("skipped"), f"catalog skipped: {catalog}"
    return body, catalog


def _vary(catalog: dict, scale: float) -> dict:
    from phone_designer.skills.reverse_engineer.vary_feature_catalog import (
        vary_catalog_ex,
    )

    varied, _warnings = vary_catalog_ex(catalog, scale_factor=scale)
    return varied


# ──────────────────────────────────────────────────────────────────────────────
# (a) scale-invariant clamping


def test_depth_clamp_scales_with_dimension_scale(slab_body):
    """250 mm hole → clamped to 200 at s=1; the 2×-varied catalog (500 mm
    hole) with dimension_scale=2 → min(500, 2×200) = 400."""
    cat = _synthetic_catalog(hole_depth_mm=250.0, hole_d_mm=10.0)

    holes_s1 = _hole_steps(_plan(slab_body, cat))
    assert len(holes_s1) == 1, holes_s1
    assert holes_s1[0]["args"]["depth_mm"] == pytest.approx(200.0)

    varied2 = _vary(cat, 2.0)
    assert varied2["holes"][0]["depth_mm"] == pytest.approx(500.0)

    holes_s2 = _hole_steps(_plan(slab_body, varied2, dimension_scale=2.0))
    assert len(holes_s2) == 1, holes_s2
    assert holes_s2[0]["args"]["depth_mm"] == pytest.approx(400.0)

    # Contrast: planning the SAME 2× catalog without dimension_scale keeps
    # the absolute 200 mm cap — proving the cap (not the data) moved above.
    holes_s2_unaware = _hole_steps(_plan(slab_body, varied2))
    assert len(holes_s2_unaware) == 1
    assert holes_s2_unaware[0]["args"]["depth_mm"] == pytest.approx(200.0)


def test_min_emitted_cut_floor_scales_cubically(slab_body):
    """A Ø0.4×0.5 hole (~0.063 mm³) is emitted at s=1. Varied ×0.25 it
    becomes Ø0.1×0.125 (~0.00098 mm³): DEAD under the absolute 0.02 mm³
    floor, alive under the scaled 0.02×0.25³ ≈ 0.0003 mm³ floor."""
    cat = _synthetic_catalog(hole_depth_mm=0.5, hole_d_mm=0.4)
    assert len(_hole_steps(_plan(slab_body, cat))) == 1

    varied = _vary(cat, 0.25)
    assert varied["holes"][0]["diameters_mm"][0] == pytest.approx(0.1)
    assert varied["holes"][0]["depth_mm"] == pytest.approx(0.125)

    # Scale-unaware planning drops the (legitimate) tiny hole entirely.
    assert len(_hole_steps(_plan(slab_body, varied))) == 0

    # Scale-aware planning keeps it.
    holes = _hole_steps(_plan(slab_body, varied, dimension_scale=0.25))
    assert len(holes) == 1, holes
    assert holes[0]["args"]["diameter_mm"] == pytest.approx(0.1)
    assert holes[0]["args"]["depth_mm"] == pytest.approx(0.125)


# ──────────────────────────────────────────────────────────────────────────────
# (b) s=1.0 bit-identity on a real corpus catalog


@pytest.mark.slow
@_needs_linkrods
@pytest.mark.parametrize("mode", ["box", "preserve_brep"])
def test_dimension_scale_one_is_bit_identical_linkrods(linkrods, mode):
    body, catalog = linkrods
    plan_default = _plan(body, copy.deepcopy(catalog), base_step_kind=mode)
    plan_explicit = _plan(
        body, copy.deepcopy(catalog), base_step_kind=mode, dimension_scale=1.0,
    )
    assert plan_default == plan_explicit


# ──────────────────────────────────────────────────────────────────────────────
# (c) step-count / step-kind invariance across scales


@pytest.mark.slow
@_needs_linkrods
def test_step_kinds_invariant_across_scales_linkrods(linkrods):
    body, catalog = linkrods
    kinds_by_scale: dict[float, list[str]] = {}
    for s in (0.25, 1.0, 4.0):
        varied = _vary(copy.deepcopy(catalog), s)
        plan = _plan(body, varied, dimension_scale=s)
        kinds_by_scale[s] = [st["skill"] for st in plan["steps"]]

    ref = kinds_by_scale[1.0]
    assert ref, "scale-1 plan emitted no steps"
    for s, kinds in kinds_by_scale.items():
        assert kinds == ref, (
            f"step kinds at scale {s} diverge from scale 1:\n"
            f"  s={s}: {kinds}\n  s=1: {ref}"
        )
