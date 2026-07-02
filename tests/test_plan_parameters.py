"""Plan schema v2 — named parameters + $expr resolution (track 2-2).

Pins:
  * $expr arithmetic exact (housing_length/2 - wall == 23.0) via the same
    simpleeval whitelist as manufacturing/string_eval.py
  * undefined param name / cycle / bad node shape / eval failure →
    structured fm.expr_error (raw cause preserved, never masked)
  * derived parameters resolve; an override PINS a derived param
  * v1 YAML round-trip byte-stable: NO `parameters` key materializes,
    schema_version stays 1, save∘load∘save byte-identical
  * a plan that actually uses parameters auto-marks schema_version 2 and
    round-trips losslessly
  * generate_from_spec: parameters kwarg resolves $expr; the param-less
    manifest stays key-for-key identical to pre-v2 output
"""
from __future__ import annotations

import pytest

from phone_designer.plan.model import ParameterDef, Plan, Step
from phone_designer.plan.params import (
    ExprError,
    resolve_args,
    resolve_parameter_table,
    resolve_plan,
)
from phone_designer.plan.yaml_io import load_plan, save_plan


# ---------------------------------------------------------------- expr core


def test_expr_arithmetic_correct():
    table = resolve_parameter_table(
        {"housing_length": {"value": 50.0}, "wall": 2.0})
    out = resolve_args({"x": {"$expr": "housing_length/2 - wall"}}, table)
    assert out["x"] == pytest.approx(23.0)
    # whitelisted functions from the EXISTING simpleeval wrapper policy
    out2 = resolve_args(
        [{"$expr": "max(wall, 3)"}, {"$expr": "abs(-wall)"},
         {"$expr": "round(wall * 1.24, 1)"}], table)
    assert out2[0] == pytest.approx(3.0)
    assert out2[1] == pytest.approx(2.0)
    assert out2[2] == pytest.approx(2.5)
    # nested containers are walked; plain scalars pass through untouched
    nested = resolve_args(
        {"origin": [0.0, {"$expr": "wall*2"}, "keep_me"], "flag": True}, table)
    assert nested == {"origin": [0.0, 4.0, "keep_me"], "flag": True}


def test_undefined_param_name_is_fm_expr_error():
    with pytest.raises(ValueError, match=r"fm\.expr_error.*undefined"):
        resolve_args({"x": {"$expr": "wall + not_defined"}}, {"wall": 2.0})
    # the missing name AND the defined menu are both in the message (honest)
    with pytest.raises(ValueError, match=r"not_defined.*wall"):
        resolve_args({"x": {"$expr": "not_defined"}}, {"wall": 2.0})


def test_cycle_detection_is_fm_expr_error():
    with pytest.raises(ValueError, match=r"fm\.expr_error.*cycle"):
        resolve_parameter_table(
            {"a": {"value": "b + 1"}, "b": {"value": "a + 1"}})
    # self-cycle
    with pytest.raises(ValueError, match=r"fm\.expr_error.*cycle"):
        resolve_parameter_table({"a": {"value": "a * 2"}})


def test_derived_parameter_and_override_pinning():
    params = {"L": 40.0, "half": {"value": "L/2"}}
    assert resolve_parameter_table(params) == {"L": 40.0, "half": 20.0}
    # overriding the BASE re-derives the derived param
    assert resolve_parameter_table(params, {"L": 50.0})["half"] == 25.0
    # overriding the DERIVED param pins it to a literal
    t = resolve_parameter_table(params, {"half": 7.0})
    assert t == {"L": 40.0, "half": 7.0}


def test_override_of_unknown_name_refused():
    with pytest.raises(ValueError,
                       match=r"fm\.expr_error.*override for undefined"):
        resolve_parameter_table({"wall": 2.0}, {"bogus": 1.0})


def test_expr_node_shape_is_strict():
    table = {"L": 40.0}
    # sibling keys are refused, not silently dropped
    with pytest.raises(ValueError, match=r"fm\.expr_error.*sibling"):
        resolve_args({"x": {"$expr": "L", "unit": "mm"}}, table)
    # non-string expression refused
    with pytest.raises(ValueError, match=r"fm\.expr_error.*non-empty string"):
        resolve_args({"x": {"$expr": 5}}, table)


def test_eval_failure_raw_cause_never_masked():
    # ZeroDivisionError must appear verbatim (house rule: raw errors never
    # masked — unlike string_eval.safe_eval's None fallback)
    with pytest.raises(ValueError,
                       match=r"fm\.expr_error.*ZeroDivisionError"):
        resolve_args({"x": {"$expr": "1/0"}}, {})
    # strict-JSON-safe: inf never leaves the resolver
    with pytest.raises(ValueError, match=r"fm\.expr_error.*non-finite"):
        resolve_args({"x": {"$expr": "1e400"}}, {})
    assert isinstance(ExprError("x"), ValueError)


# ------------------------------------------------------- schema v2 + yaml io


def _v1_plan() -> Plan:
    return Plan(plan_name="p", steps=[
        Step(id="s1", skill="box",
             args={"length_mm": 10.0, "width_mm": 5.0, "height_mm": 2.0})])


def test_v1_yaml_roundtrip_byte_stable(tmp_path):
    plan = _v1_plan()
    # a param-less plan is STILL a v1 document — no version churn
    assert plan.schema_version == 1 and plan.parameters is None
    p1 = tmp_path / "a.yaml"
    save_plan(plan, p1)
    text1 = p1.read_text(encoding="utf-8")
    assert "parameters" not in text1          # THE pin: no key materializes
    assert "schema_version: 1" in text1
    loaded = load_plan(p1)                    # runs the v1→v2 migration path
    assert loaded.schema_version == 1 and loaded.parameters is None
    p2 = tmp_path / "b.yaml"
    save_plan(loaded, p2)
    assert p2.read_bytes() == p1.read_bytes()  # byte-identical round trip


def test_handwritten_v1_yaml_gains_no_keys(tmp_path):
    src = tmp_path / "hand.yaml"
    src.write_text(
        "schema_version: 1\nplan_name: hand\nsteps:\n"
        "- id: s1\n  skill: box\n"
        "  args: {length_mm: 4.0, width_mm: 4.0, height_mm: 4.0}\n",
        encoding="utf-8")
    plan = load_plan(src)
    assert plan.parameters is None and plan.schema_version == 1
    out = tmp_path / "resaved.yaml"
    save_plan(plan, out)
    text = out.read_text(encoding="utf-8")
    assert "parameters" not in text and "schema_version: 1" in text


def test_plan_with_parameters_is_v2_and_roundtrips(tmp_path):
    plan = Plan(
        plan_name="p2",
        parameters={"wall": ParameterDef(value=2.0, description="wall thk"),
                    "half_l": ParameterDef(value="wall * 10")},
        steps=[Step(id="s1", skill="box",
                    args={"length_mm": {"$expr": "half_l"},
                          "width_mm": 5.0, "height_mm": 2.0})])
    # using parameters HONESTLY marks the document v2
    assert plan.schema_version == 2
    p1 = tmp_path / "v2.yaml"
    save_plan(plan, p1)
    loaded = load_plan(p1)
    assert loaded.schema_version == 2
    assert loaded.parameters["wall"].value == 2.0
    assert loaded.parameters["wall"].unit == "mm"
    assert loaded.parameters["wall"].description == "wall thk"
    assert loaded.parameters["half_l"].value == "wall * 10"
    assert loaded.steps[0].args["length_mm"] == {"$expr": "half_l"}
    p2 = tmp_path / "v2b.yaml"
    save_plan(loaded, p2)
    assert p2.read_bytes() == p1.read_bytes()
    # resolve_plan: derived param + expr args → literals, table rewritten
    resolved, table = resolve_plan(loaded)
    assert table == {"wall": 2.0, "half_l": 20.0}
    assert resolved.steps[0].args["length_mm"] == pytest.approx(20.0)
    assert resolved.parameters["half_l"].value == 20.0
    # the ORIGINAL plan object was not mutated
    assert loaded.steps[0].args["length_mm"] == {"$expr": "half_l"}


def test_empty_parameters_normalizes_to_none():
    plan = Plan(plan_name="p", parameters={}, steps=_v1_plan().steps)
    assert plan.parameters is None and plan.schema_version == 1


def test_future_schema_version_still_refused(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("schema_version: 3\nplan_name: x\nsteps: []\n",
                   encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version=3"):
        load_plan(bad)


# ------------------------------------------------- generate_from_spec kwarg


def _gen(spec, **kw):
    from phone_designer.skills.create.generate_from_spec import GenerateFromSpec
    return (GenerateFromSpec()
            .apply(None, {"spec": spec, **kw}).extras["generated"])


def test_generate_from_spec_with_parameters():
    r = _gen(
        [{"op": "box", "args": {"length_mm": {"$expr": "L"},
                                "width_mm": {"$expr": "W"},
                                "height_mm": {"$expr": "L/4 - t"}}}],
        parameters={"L": 40, "W": {"value": 30.0, "unit": "mm"}, "t": 2})
    assert r["ok"] and r["is_solid"]
    assert r["volume_mm3"] == pytest.approx(40 * 30 * 8, abs=1.0)   # 9600
    assert r["bbox_mm"] == [40.0, 30.0, 8.0]
    assert r["parameters_resolved"] == {"L": 40.0, "W": 30.0, "t": 2.0}


def test_generate_from_spec_bad_expr_step_isolated():
    r = _gen(
        [{"op": "box", "args": {"length_mm": {"$expr": "Q"},
                                "width_mm": 5, "height_mm": 5}},
         {"op": "box", "args": {"length_mm": 20, "width_mm": 20,
                                "height_mm": 4}}],
        parameters={"L": 40})
    assert r["ok"] is False
    assert any("fm.expr_error" in e and "Q" in e for e in r["spec_errors"])
    assert r["n_ok"] == 1 and r["is_solid"]   # the literal box still built


def test_generate_from_spec_broken_table_raises_fm_expr_error():
    with pytest.raises(ValueError, match=r"fm\.expr_error.*cycle"):
        _gen([{"op": "box", "args": {"length_mm": 10, "width_mm": 10,
                                     "height_mm": 10}}],
             parameters={"a": {"value": "b"}, "b": {"value": "a"}})


def test_generate_from_spec_paramless_manifest_unchanged():
    # BYTE-STABLE default: without the kwarg the manifest is key-for-key the
    # pre-v2 shape — parameters_resolved must NOT appear.
    r = _gen([{"op": "box", "args": {"length_mm": 40, "width_mm": 30,
                                     "height_mm": 10}}])
    assert set(r.keys()) == {
        "n_steps", "n_ok", "ok", "is_solid", "volume_mm3", "bbox_mm",
        "steps", "spec_errors", "grade"}
    assert r["ok"] and r["volume_mm3"] == pytest.approx(12000.0, abs=1.0)
