"""generate_from_spec — generate a solid FROM SCRATCH from a declarative spec.

Pins that the generative front door builds a real solid by composing the
registered skill library (create + feature skills), validates the result,
isolates per-step failures, and that the generated body flows into the analysis
suite. This is the 'zero-base instructed structure' path.
"""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box  # noqa: F401  (ensures import)
from phone_designer.skills.create.generate_from_spec import GenerateFromSpec


def _gen(spec, **kw):
    return GenerateFromSpec().apply(None, {"spec": spec, **kw}).extras["generated"]


def test_generate_box_from_spec():
    r = _gen([{"op": "box", "args": {"length_mm": 40, "width_mm": 30,
                                     "height_mm": 10}}])
    assert r["ok"] and r["is_solid"] and r["n_ok"] == 1
    assert r["volume_mm3"] == pytest.approx(12000.0, abs=1.0)
    assert r["bbox_mm"] == [40.0, 30.0, 10.0]
    assert r["grade"] == "constructed"


def test_generate_bracket_features_apply():
    # box base + 4 corner holes — the whole multi-step build runs from scratch
    spec = [{"op": "box", "args": {"length_mm": 60, "width_mm": 40,
                                   "height_mm": 5}}]
    spec += [{"op": "hole", "args": {"position": [x, y, 5], "diameter_mm": 5,
                                     "depth_mm": 5, "direction": "-Z"}}
             for (x, y) in [(-25, -15), (25, -15), (-25, 15), (25, 15)]]
    r = _gen(spec)
    assert r["ok"] and r["n_ok"] == 5 and r["n_steps"] == 5
    assert r["volume_mm3"] < 12000.0   # the 4 holes removed material
    assert all(s["status"] == "pass" for s in r["steps"])


def test_unknown_skill_is_isolated():
    r = _gen([{"op": "box", "args": {"length_mm": 20, "width_mm": 20,
                                     "height_mm": 4}},
              {"op": "does_not_exist", "args": {}}])
    assert r["ok"] is False
    assert any("unknown skill" in e for e in r["spec_errors"])
    assert r["is_solid"]   # the valid box step still built a solid


def test_bad_args_step_isolated():
    # a valid skill with invalid args fails THAT step, not the whole build
    r = _gen([{"op": "box", "args": {"length_mm": 20, "width_mm": 20,
                                     "height_mm": 4}},
              {"op": "hole", "args": {"position": [0, 0, 4]}}])  # no diameter_mm
    assert r["ok"] is False
    statuses = {s["op"]: s["status"] for s in r["steps"]}
    assert statuses.get("box") == "pass"
    assert statuses.get("hole") in ("fail", "skipped")


def test_malformed_args_is_isolated_not_crashed():
    # a non-object 'args' (e.g. a string) must be a spec_error, not a ValueError
    r = _gen([{"op": "box", "args": "not_a_dict"}])
    assert r["ok"] is False
    assert any("'args' must be an object" in e for e in r["spec_errors"])


def test_generated_body_flows_into_analysis():
    body = GenerateFromSpec().apply(None, {"spec": [
        {"op": "box", "args": {"length_mm": 60, "width_mm": 40,
                               "height_mm": 12}}]}).body
    from phone_designer.skills.inspect.estimate_cost import EstimateCost
    ce = EstimateCost().apply(body, {"process": "cnc_3axis",
                                     "material": "aluminum"}).extras["cost_estimate"]
    assert ce["unit_cost_usd"] > 0 and ce["reliability"] == "ok"
