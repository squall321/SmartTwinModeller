"""Executed-rebuild regression for the pass-23 whitelist bug (plan P1).

Pipeline under test (the docs/parametric_variation_demo.md path):

    import_step(occt__linkrods.step)
        → extract_feature_catalog
        → plan_from_scaled_catalog(scale_factor=s, base_step_kind="box")
        → Plan.model_validate + PlanExecutor.run()   (box mode: NO initial_body)

On HEAD-before-the-P1-fix the 1.5× variant came out as a bare slab: holes
were emitted at the UNSCALED entry_origin / entry_depth_mm, missed the
scaled placeholder box, zero-delta SKIPPED, and the volume ratio drifted to
~3.887× instead of 1.5³ = 3.375×. The hole-rediscovery assertion below is
the one that fails on that HEAD (a slab has no holes to re-detect).
"""
from __future__ import annotations

import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_LINKRODS = _REPO_ROOT / "corpus" / "oem" / "complex" / "occt__linkrods.step"

pytestmark = [
    pytest.mark.requires_oem,
    # The conftest requires_oem gate keys off PHONE_DESIGNER_OEM_REF (the
    # galaxy-watch reference), not the corpus tree — guard the actual input
    # file too so a partial checkout skips instead of erroring.
    pytest.mark.skipif(not _LINKRODS.exists(), reason=f"corpus STEP not found: {_LINKRODS}"),
    pytest.mark.slow,
]


def _volume_mm3(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    shape = body.wrapped if hasattr(body, "wrapped") else body
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


def _register_all_skills() -> None:
    """Manifest import + the box-mode pocket skill the audit found missing
    from the manifest (V1 fixes the manifest itself; importing the module
    directly keeps this test about the WHITELIST regardless of whether V1
    has landed in this checkout)."""
    from phone_designer.skills import export_manifest  # noqa: F401
    try:
        import phone_designer.skills.modify_pocket.extrude_pocket_world  # noqa: F401
    except Exception:
        pass


@pytest.fixture(scope="module")
def linkrods():
    """(original_body, feature_catalog) — imported + extracted once."""
    _register_all_skills()
    from phone_designer.skills.create.import_step import ImportStep
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )

    body = ImportStep().apply(None, {"path": str(_LINKRODS)}).body
    assert body is not None
    catalog = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
    assert not catalog.get("skipped"), f"catalog skipped: {catalog}"
    assert len(catalog.get("holes") or []) >= 2, (
        "linkrods should expose >=2 holes — detector regression upstream?"
    )
    return body, catalog


def _rebuild(body, catalog: dict, scale: float):
    """plan_from_scaled_catalog → PlanExecutor. Returns the rebuilt body."""
    from phone_designer.plan.executor import PlanExecutor
    from phone_designer.plan.model import Plan
    from phone_designer.skills.reverse_engineer.plan_from_scaled_catalog import (
        PlanFromScaledCatalog,
    )

    res = PlanFromScaledCatalog().apply(body, {
        "catalog": catalog,
        "scale_factor": scale,
        "base_step_kind": "box",
    })
    plan_dict = res.extras.get("generated_plan") or {}
    assert plan_dict.get("steps"), f"no steps in generated_plan (scale={scale})"
    plan = Plan.model_validate(plan_dict)
    # Box mode constructs its own s_base placeholder — NO initial_body.
    exec_result = PlanExecutor(plan).run()
    assert exec_result.final_body is not None, (
        f"executor produced no final body (scale={scale}, "
        f"outcome={exec_result.outcome})"
    )
    return exec_result.final_body


@pytest.fixture(scope="module")
def rebuilt_pair(linkrods):
    """(body@1.0, body@1.5) box-mode rebuilds from the SAME catalog."""
    body, catalog = linkrods
    return _rebuild(body, catalog, 1.0), _rebuild(body, catalog, 1.5)


def test_scaled_rebuild_volume_ratio_is_cubic(rebuilt_pair):
    """1.5× uniform scale ⇒ volume ratio 1.5³ = 3.375 within ±3 %. The
    pre-fix bare slab measured ~3.887× (holes lost = material kept)."""
    base_body, scaled_body = rebuilt_pair
    v0 = _volume_mm3(base_body)
    v1 = _volume_mm3(scaled_body)
    assert v0 > 0.0
    ratio = v1 / v0
    expected = 1.5 ** 3
    assert abs(ratio - expected) / expected <= 0.03, (
        f"volume ratio {ratio:.4f} deviates from {expected} by "
        f"{abs(ratio - expected) / expected:.1%} (>3%) — scaled features "
        "lost or double-emitted"
    )


def test_scaled_rebuild_holes_rediscovered_at_scaled_diameter(rebuilt_pair, linkrods):
    """classify_holes on the 1.5× rebuild must find >=2 holes whose minimum
    diameter is ~1.5× some original-catalog hole's (±10 %). This is THE
    assertion that fails on HEAD-before-the-fix: the rebuilt body was a
    featureless slab, so 0 holes re-detect."""
    from phone_designer.skills.inspect.classify_holes import ClassifyHoles

    _body, catalog = linkrods
    _base_body, scaled_body = rebuilt_pair

    orig_min_ds = [
        float(min(h["diameters_mm"]))
        for h in catalog.get("holes") or []
        if h.get("diameters_mm")
    ]
    assert orig_min_ds, "original catalog has no hole diameters"

    redetected = ClassifyHoles().apply(
        scaled_body, {"match_standards": False},
    ).extras.get("holes") or []

    matched = 0
    for h in redetected:
        diams = h.get("diameters_mm") or []
        if not diams:
            continue
        d = float(min(diams))
        if any(abs(d - 1.5 * od) <= 0.10 * (1.5 * od) for od in orig_min_ds):
            matched += 1
    assert matched >= 2, (
        f"only {matched} re-detected hole(s) match a 1.5x-scaled original "
        f"diameter (orig minima: {sorted(orig_min_ds)}, redetected minima: "
        f"{sorted(float(min(h['diameters_mm'])) for h in redetected if h.get('diameters_mm'))})"
        " — scaled variant lost its holes (pass-23 whitelist bug)"
    )
