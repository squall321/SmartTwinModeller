"""initial_bbox_mm / estimate_cost mesh-history determinism — the ±$0.02 cost drift.

ROOT CAUSE (diagnosed 2026-07-10 on .pd_workspace/gearbox_housing.step):
ExtractFeatureCatalog's PACK-B bbox snapshot called ``BRepBndLib.AddOptimal_s``
with the DEFAULT ``useTriangulation=True``. OCCT then reads any triangulation
already stored on the shape and enlarges the box by the stored per-face
deflection — so ``initial_bbox_mm`` was a function of WHICHEVER mesher ran
first in the process, not of the geometry:

  * body never meshed            → exact 140×90×90, stock 1134.000 cm³ → $84.7008
  * after dfm_verdict (fine mesh)→ +0.003 mm/side,  stock 1134.208 cm³ → $84.7224
  * after the catalog's OWN 0.5mm canonical tessellation (any SECOND
    catalog run on the same body) → stock 1137.332 cm³ → $85.0478

(the first two are the exact live drift values; all measured at lot 1000,
cnc_3axis aluminum, pre-fix hole counts). The fix passes
``useTriangulation=False`` so the snapshot always uses the precise geometry
evaluators — one bbox per shape, independent of process history. On an
unmeshed body the result is bit-identical to the old call, so fresh single-run
catalogs (and every committed corpus baseline) are unchanged.

This battery pins:
  1. bbox immunity to stale triangulation (self-contained cylinder body);
  2. estimate_cost invariance to prior meshing / prior catalog runs;
  3. gearbox pin — extract 5× in FRESH subprocesses → repr-identical bboxes;
  4. gearbox pin — estimate_cost 3× fresh subprocesses → cent-identical costs;
  5. gearbox cross-context — cost direct == cost after dfm_verdict (the
     exact $84.7008-vs-$84.7224 live-drift reproduction).

Gearbox tests skip when the (git-ignored) fixture is absent.
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GEARBOX = REPO / ".pd_workspace" / "gearbox_housing.step"
SRC = REPO / "src"

_COST_ARGS = {"process": "cnc_3axis", "material": "aluminum", "lot_size": 1000}


# ── self-contained curved body (curved faces make mesh-inflation visible) ────
def _cylinder_body():
    from phone_designer.skills.create.cylinder import Cylinder
    return Cylinder().apply(None, {"radius_mm": 18.0, "height_mm": 40.0}).body


def _mesh_in_place(body, deflection_mm: float) -> None:
    """Simulate a prior skill (dfm/export/preview) tessellating the shape."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh

    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        _occt_shape,
    )
    BRepMesh_IncrementalMesh(
        _occt_shape(body), deflection_mm, False, math.radians(20.0), True,
    ).Perform()


def _initial_bbox(body):
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )
    cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
    return cat["initial_bbox_mm"]


def _cost(body):
    from phone_designer.skills.inspect.estimate_cost import EstimateCost
    return EstimateCost().apply(body, dict(_COST_ARGS)).extras["cost_estimate"]


# ── 1. bbox is immune to stale triangulation ─────────────────────────────────
def test_initial_bbox_immune_to_stale_triangulation():
    body = _cylinder_body()
    bb_fresh = _initial_bbox(body)          # never meshed → exact geometry
    # the first catalog run left its 0.5 mm canonical tessellation ON the shape
    bb_after_own_mesh = _initial_bbox(body)
    _mesh_in_place(body, 0.05)              # a FINER mesh replaces the 0.5 mm one
    bb_after_fine_mesh = _initial_bbox(body)
    assert repr(bb_fresh) == repr(bb_after_own_mesh) == repr(bb_after_fine_mesh)
    # and it is the EXACT bbox of an r18 × h40 cylinder at the origin — not a
    # deflection-inflated mesh box (pre-fix the meshed reads were ~0.05 mm wide)
    exact = [-18.0, -18.0, 0.0, 18.0, 18.0, 40.0]
    for got, want in zip(bb_fresh, exact):
        assert abs(got - want) < 1e-6, (bb_fresh, exact)


# ── 2. estimate_cost is invariant to the body's meshing history ─────────────
def test_estimate_cost_immune_to_prior_meshing():
    fresh = _cost(_cylinder_body())
    meshed_body = _cylinder_body()
    _mesh_in_place(meshed_body, 0.5)        # e.g. an stl_export / preview ran first
    meshed = _cost(meshed_body)
    assert fresh["unit_cost_usd"] == meshed["unit_cost_usd"]
    assert fresh["drivers"]["stock_cm3"] == meshed["drivers"]["stock_cm3"]


def test_estimate_cost_same_after_prior_catalog_run():
    # the $85.0478 mechanism: extract_feature_catalog leaves its canonical
    # tessellation on the shape, then a LATER estimate_cost re-extracts and
    # (pre-fix) read the mesh-inflated bbox.
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )
    baseline = _cost(_cylinder_body())
    body = _cylinder_body()
    ExtractFeatureCatalog().apply(body, {})     # leaves the 0.5 mm mesh behind
    again = _cost(body)
    assert baseline["unit_cost_usd"] == again["unit_cost_usd"]
    assert baseline["drivers"]["stock_cm3"] == again["drivers"]["stock_cm3"]


# ── gearbox pins (fresh OS subprocesses — meshing caches cannot hide drift) ──
_SUB_COMMON = (
    "import sys; sys.path.insert(0, {src!r})\n"
    "from phone_designer.skills.create.import_step import ImportStep\n"
    "body = ImportStep().apply(None, {{'path': {step!r}}}).body\n"
)

_SUB_BBOX = _SUB_COMMON + (
    "from phone_designer.skills.reverse_engineer.extract_feature_catalog "
    "import ExtractFeatureCatalog\n"
    "cat = ExtractFeatureCatalog().apply(body, {{}}).extras['feature_catalog']\n"
    "print('MARKER=' + repr(tuple(cat['initial_bbox_mm'])))\n"
)

_SUB_COST = _SUB_COMMON + (
    "from phone_designer.skills.inspect.estimate_cost import EstimateCost\n"
    "ce = EstimateCost().apply(body, {cost!r}).extras['cost_estimate']\n"
    "print('MARKER=' + repr((ce['unit_cost_usd'], "
    "format(ce['unit_cost_usd'], '.2f'), ce['drivers']['stock_cm3'])))\n"
)


def _run_fresh(script: str) -> str:
    env = dict(os.environ)
    env["PHONE_DESIGNER_UI_HEADLESS"] = "1"
    out = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=300, env=env, check=True,
    ).stdout
    lines = [ln for ln in out.splitlines() if ln.startswith("MARKER=")]
    assert lines, f"no MARKER in subprocess output:\n{out[-2000:]}"
    return lines[-1][len("MARKER="):]


@pytest.mark.skipif(not GEARBOX.exists(), reason="gearbox fixture absent")
def test_gearbox_bbox_identical_across_5_fresh_subprocesses():
    script = _SUB_BBOX.format(src=str(SRC), step=str(GEARBOX))
    reprs = [_run_fresh(script) for _ in range(5)]
    assert len(set(reprs)) == 1, reprs


@pytest.mark.skipif(not GEARBOX.exists(), reason="gearbox fixture absent")
def test_gearbox_unit_cost_identical_across_3_fresh_subprocesses():
    script = _SUB_COST.format(src=str(SRC), step=str(GEARBOX), cost=_COST_ARGS)
    reprs = [_run_fresh(script) for _ in range(3)]
    assert len(set(reprs)) == 1, reprs


@pytest.mark.skipif(not GEARBOX.exists(), reason="gearbox fixture absent")
def test_gearbox_cost_direct_equals_cost_after_dfm_verdict():
    # THE live drift: estimate_cost direct ($84.7008 pre-fix) vs after
    # dfm_verdict had tessellated the body ($84.7224 pre-fix). Two separate
    # imports of the STEP give two independent shapes, so the mesh state of
    # one cannot leak into the other — only the fix makes them agree.
    from phone_designer.skills.create.import_step import ImportStep
    from phone_designer.skills.inspect.dfm_verdict import DfmVerdict

    direct = _cost(ImportStep().apply(None, {"path": str(GEARBOX)}).body)

    body2 = ImportStep().apply(None, {"path": str(GEARBOX)}).body
    DfmVerdict().apply(body2, {
        "processes": ["cnc_milling", "injection_molding", "sheet_metal"],
        "pull_direction": [0.0, 0.0, 1.0],
    })
    after_dfm = _cost(body2)

    assert direct["unit_cost_usd"] == after_dfm["unit_cost_usd"], (
        direct["unit_cost_usd"], after_dfm["unit_cost_usd"])
    assert direct["drivers"]["stock_cm3"] == after_dfm["drivers"]["stock_cm3"]
