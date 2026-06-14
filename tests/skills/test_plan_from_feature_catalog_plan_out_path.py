"""phase-0 (2026-06-13): PlanFromFeatureCatalog.Args.plan_out_path.

Covers the injectable plan-output-path contract added so a future
process-pool worker can give each worker its own plan file (no parallelism
is added here — only the path is made injectable):

  (a) plan_out_path=None writes the historic default file with content
      identical to what the skill wrote before the arg existed, and does
      NOT surface extras['plan_path'].
  (b) plan_out_path=<tmp> writes the plan THERE and extras['plan_path']
      points at exactly that file.
  (c) two calls with different paths do not clobber each other (each file
      holds its own catalog's plan).

byte_identical_proof (separate, OCCT-backed): the plan dict emitted for the
real ``linkrods`` and ``as1-oc-214`` catalogs with the arg defaulted to
None deep-equals the plan dict emitted WITHOUT touching the arg at all.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
    PlanFromFeatureCatalog,
    _build_plan,
)


# ──────────────────────────────────────────────────────────────────────────────
# Synthetic catalogs — body-free plan generation. The ``box`` base step only
# needs ``initial_bbox_mm``; no OCCT body is required, so these tests are fast
# and deterministic. Two distinct catalogs so the clobber test is meaningful.


def _catalog_a() -> dict:
    return {
        "initial_bbox_mm": [-20.0, -10.0, 0.0, 20.0, 10.0, 8.0],
        "holes": [
            {
                "type": "simple",
                "diameters_mm": [4.0],
                "depth_mm": 5.0,
                "axis_origin": [3.0, 2.0, 8.0],
                "axis_dir": [0.0, 0.0, -1.0],
            }
        ],
        "pockets": [],
        "bosses": [],
        "ribs": [],
        "lugs": [],
        "patterns": [],
    }


def _catalog_b() -> dict:
    return {
        "initial_bbox_mm": [-30.0, -15.0, 0.0, 30.0, 15.0, 12.0],
        "holes": [
            {
                "type": "simple",
                "diameters_mm": [6.0],
                "depth_mm": 9.0,
                "axis_origin": [-5.0, -4.0, 12.0],
                "axis_dir": [0.0, 0.0, -1.0],
            }
        ],
        "pockets": [
            {
                "top_d_mm": 8.0,
                "depth_mm": 3.0,
                "axis_origin": [10.0, 5.0, 12.0],
                "axis_dir": [0.0, 0.0, -1.0],
            }
        ],
        "bosses": [],
        "ribs": [],
        "lugs": [],
        "patterns": [],
    }


def _default_plan_path() -> pathlib.Path:
    # Same resolution the skill uses: 4 parents up from the skill module to
    # the repo root, then plans/reconstructed_plan.yaml.
    import phone_designer.skills.reverse_engineer.plan_from_feature_catalog as m

    root = pathlib.Path(m.__file__).resolve().parents[4]
    return root / "plans" / "reconstructed_plan.yaml"


# ──────────────────────────────────────────────────────────────────────────────
# (a) plan_out_path=None → historic default file, identical content, no
#     extras['plan_path'].


def test_default_path_unchanged_and_no_plan_path_extra() -> None:
    cat = _catalog_a()
    default_path = _default_plan_path()

    # Build the YAML text the way the legacy write path does — this is the
    # exact byte string the skill must still produce when plan_out_path=None.
    plan_direct = _build_plan(cat, body=None, base_step_kind="box")
    expected_text = yaml.safe_dump(
        plan_direct, sort_keys=False, allow_unicode=True
    )

    res = PlanFromFeatureCatalog().apply(
        None, {"catalog": cat, "base_step_kind": "box"}
    )

    # Default-path callers must NOT see a plan_path key (byte-identical
    # extras for existing consumers).
    assert "plan_path" not in res.extras
    assert "generated_plan" in res.extras
    assert res.extras["generated_plan"] == plan_direct

    # The historic default file holds exactly the legacy content.
    assert default_path.exists()
    assert default_path.read_text(encoding="utf-8") == expected_text


# ──────────────────────────────────────────────────────────────────────────────
# (b) plan_out_path=<tmp> → writes there, surfaces the path.


def test_explicit_path_writes_there_and_surfaces_path(tmp_path) -> None:
    cat = _catalog_b()
    out = tmp_path / "nested" / "worker_7_plan.yaml"  # parent dir created

    res = PlanFromFeatureCatalog().apply(
        None,
        {"catalog": cat, "base_step_kind": "box", "plan_out_path": str(out)},
    )

    assert res.extras.get("plan_path") == str(out)
    assert out.exists()

    on_disk = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert on_disk == res.extras["generated_plan"]


# ──────────────────────────────────────────────────────────────────────────────
# (c) two calls, two paths, no clobber.


def test_two_distinct_paths_do_not_clobber(tmp_path) -> None:
    cat_a = _catalog_a()
    cat_b = _catalog_b()
    out_a = tmp_path / "a.yaml"
    out_b = tmp_path / "b.yaml"

    res_a = PlanFromFeatureCatalog().apply(
        None,
        {"catalog": cat_a, "base_step_kind": "box", "plan_out_path": str(out_a)},
    )
    res_b = PlanFromFeatureCatalog().apply(
        None,
        {"catalog": cat_b, "base_step_kind": "box", "plan_out_path": str(out_b)},
    )

    plan_a = yaml.safe_load(out_a.read_text(encoding="utf-8"))
    plan_b = yaml.safe_load(out_b.read_text(encoding="utf-8"))

    # Each file still holds its OWN catalog's plan after both writes.
    assert plan_a == res_a.extras["generated_plan"]
    assert plan_b == res_b.extras["generated_plan"]
    # The two plans differ (distinct catalogs) — proves no clobber.
    assert plan_a != plan_b
    assert res_a.extras["plan_path"] == str(out_a)
    assert res_b.extras["plan_path"] == str(out_b)


# ──────────────────────────────────────────────────────────────────────────────
# byte_identical_proof — real linkrods + as1-oc-214 catalogs, plan dict with
# plan_out_path=None deep-equals the plan dict emitted without the arg.

_CORPUS = pathlib.Path(__file__).resolve().parents[2] / "corpus" / "oem"
_LINKRODS = _CORPUS / "occt__linkrods.step"
_AS1 = _CORPUS / "pythonocc__as1-oc-214.step"


def _catalog_from_step(step_path: pathlib.Path) -> dict | None:
    from phone_designer.skills.create.import_step import ImportStep
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )

    body = ImportStep().apply(None, {"path": str(step_path)}).body
    cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
    if cat.get("skipped"):
        return None
    return cat


@pytest.mark.parametrize(
    "step_path",
    [_LINKRODS, _AS1],
    ids=["linkrods", "as1-oc-214"],
)
def test_plan_dict_identical_with_and_without_none_arg(step_path) -> None:
    if not step_path.exists():
        pytest.skip(f"corpus file missing: {step_path}")
    cat = _catalog_from_step(step_path)
    if cat is None:
        pytest.skip(f"catalog skipped for {step_path.name}")

    # WITHOUT the arg (legacy call shape — arg never mentioned).
    plan_without = PlanFromFeatureCatalog().apply(
        None, {"catalog": cat, "base_step_kind": "box"}
    ).extras["generated_plan"]

    # WITH the arg explicitly defaulted to None.
    plan_with_none = PlanFromFeatureCatalog().apply(
        None,
        {"catalog": cat, "base_step_kind": "box", "plan_out_path": None},
    ).extras["generated_plan"]

    assert plan_with_none == plan_without
