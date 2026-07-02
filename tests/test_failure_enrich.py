"""mcp_support/_failure_enrich — machine-actionable failures + spec preflight.

Pins the self-correction-loop v1 contract:
  * preflight is VALIDATION ONLY (no geometry execution) and fast;
  * unknown op   -> known=False + likely_cause=unknown_op;
  * bad args     -> args_valid=False with the pydantic message surfaced;
  * 0-match selector on a plain box -> selector_match_count==0 + suggestions
    present (the suggester always returns at least its fallback suggestion);
  * enrichment ADDS fields and keeps the ORIGINAL error byte-identical;
  * enrichment never raises on garbage input;
  * every returned structure is strict-JSON-safe (allow_nan=False).
"""
from __future__ import annotations

import json
import os
import time

import pytest

os.environ.setdefault("PHONE_DESIGNER_UI_HEADLESS", "1")

from phone_designer.mcp_support._failure_enrich import enrich_failures, preflight


@pytest.fixture(scope="module")
def box_body():
    from build123d import Box
    return Box(60, 40, 8)


_ZERO_MATCH_SELECTOR = {"kind": "faces_by_area", "min": 1.0e9}   # nothing is 1e9 mm²

_POCKET_ARGS_ZERO_MATCH = {
    "face_selector": _ZERO_MATCH_SELECTOR,
    "sketch": {"kind": "circle", "diameter_mm": 5},
    "depth_mm": 2,
}


# ── preflight ────────────────────────────────────────────────────────────────


def test_preflight_unknown_op():
    r = preflight([{"op": "definitely_not_a_skill", "args": {}}])
    assert r["ok"] is False
    s = r["steps"][0]
    assert s["known"] is False
    assert s["likely_cause"] == "unknown_op"
    assert any("definitely_not_a_skill" in w for w in s["warnings"])
    json.dumps(r, allow_nan=False)


def test_preflight_bad_args_negative_radius():
    # hole.diameter_mm is Field(gt=0) — a negative radius/diameter must fail
    # schema validation WITHOUT executing anything.
    r = preflight([{"op": "hole",
                    "args": {"position": [0, 0, 4], "diameter_mm": -5}}])
    assert r["ok"] is False
    s = r["steps"][0]
    assert s["known"] is True
    assert s["args_valid"] is False
    assert s["likely_cause"] == "args_invalid"
    # the pydantic message is surfaced verbatim in arg_errors
    assert any("greater than 0" in e for e in s["arg_errors"])
    json.dumps(r, allow_nan=False)


def test_preflight_zero_match_selector_counts_and_suggests(box_body):
    spec = [{"op": "extrude_pocket", "args": _POCKET_ARGS_ZERO_MATCH}]
    r = preflight(spec, body=box_body)
    s = r["steps"][0]
    assert s["known"] is True and s["args_valid"] is True
    assert s["selector_match_count"] == 0
    # pinned TRUE behavior: suggest_selector_from_phrase always returns at
    # least its fallback suggestion, so suggestions are PRESENT (non-empty).
    assert s["selector_suggestions"], "suggester fallback must be present"
    assert all("selector" in d and "confidence" in d
               for d in s["selector_suggestions"])
    assert any("0 entities" in w for w in s["warnings"])
    # a 0-match is a WARNING (validation-only) — known+valid still means ok
    assert r["ok"] is True
    json.dumps(r, allow_nan=False)


def test_preflight_valid_spec_ok_with_positive_match(box_body):
    spec = [
        {"op": "box", "args": {"length_mm": 60, "width_mm": 40,
                               "height_mm": 8}},
        {"op": "extrude_pocket", "args": {
            "face_selector": {"kind": "face_named", "name": "top"},
            "sketch": {"kind": "rectangle", "length_mm": 20, "width_mm": 10},
            "depth_mm": 2}},
    ]
    r = preflight(spec, body=box_body)
    assert r["ok"] is True
    assert all(s["known"] and s["args_valid"] for s in r["steps"])
    assert r["steps"][1]["selector_match_count"] == 1
    assert r["steps"][1]["warnings"] == []
    json.dumps(r, allow_nan=False)


def test_preflight_is_fast_for_ten_steps(box_body):
    # warm-up pays the one-time skill-library import cost
    preflight([{"op": "box", "args": {"length_mm": 1, "width_mm": 1,
                                      "height_mm": 1}}], body=box_body)
    spec = [{"op": "box", "args": {"length_mm": 60, "width_mm": 40,
                                   "height_mm": 8}}]
    spec += [{"op": "extrude_pocket", "args": {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "circle", "diameter_mm": 3,
                   "center_x_mm": 5.0 * i},
        "depth_mm": 1}} for i in range(9)]
    t0 = time.perf_counter()
    r = preflight(spec, body=box_body)
    dt = time.perf_counter() - t0
    assert len(r["steps"]) == 10 and r["ok"] is True
    assert dt < 1.0, f"preflight took {dt:.3f}s for a 10-step spec"


def test_preflight_non_list_is_a_structured_refusal():
    with pytest.raises(ValueError, match=r"fm\.args_invalid"):
        preflight({"op": "box"})   # type: ignore[arg-type]


# ── enrich_failures ──────────────────────────────────────────────────────────


def test_enrich_real_generate_report_keeps_error_byte_identical():
    from phone_designer.skills.create.generate_from_spec import GenerateFromSpec
    spec = [
        {"op": "box", "args": {"length_mm": 60, "width_mm": 40,
                               "height_mm": 8}},
        {"op": "extrude_pocket", "args": _POCKET_ARGS_ZERO_MATCH},
    ]
    res = GenerateFromSpec().apply(None, {"spec": spec})
    steps = res.extras["generated"]["steps"]
    failed = [s for s in steps if s["status"] != "pass" and s.get("error")]
    assert failed, "the 0-match pocket step must fail in the real executor"
    original_error = failed[0]["error"]

    enriched = enrich_failures(steps)
    e_failed = [s for s in enriched
                if s.get("status") != "pass" and s.get("error")]
    # the ORIGINAL error string is byte-identical — never replaced/masked
    assert e_failed[0]["error"] == original_error
    assert e_failed[0]["likely_cause"] == "selector_zero_match"
    assert isinstance(e_failed[0]["suggested_fix"], str) and e_failed[0]["suggested_fix"]
    assert "suggest_selector_from_phrase" in e_failed[0]["related_skills"]
    # pass entries are untouched (no enrichment keys added)
    for orig, enr in zip(steps, enriched):
        if orig["status"] == "pass":
            assert enr == orig
    json.dumps(enriched, allow_nan=False)


def test_enrich_classification_buckets():
    entries = [
        {"op": "a", "status": "fail",
         "error": "fm.pocket_exits_body: depth 99 exceeds local thickness 3."},
        {"op": "b", "status": "fail", "error": "unknown skill 'boxx'"},
        {"op": "c", "status": "fail",
         "error": "BRepAlgoAPI_Cut: boolean operation failed"},
        {"op": "d", "status": "fail",
         "error": "1 validation error for Args\nradius_mm\n"
                  "  Input should be greater than 0"},
    ]
    out = enrich_failures(entries)
    # fm token is extracted WITHOUT the trailing sentence period
    assert out[0]["likely_cause"] == "fm_refusal:fm.pocket_exits_body"
    assert out[1]["likely_cause"] == "unknown_op"
    assert out[2]["likely_cause"] == "occt_failure"
    assert out[3]["likely_cause"] == "args_invalid"
    for enr, orig in zip(out, entries):
        assert enr["error"] == orig["error"]          # byte-identical, always
        assert isinstance(enr["suggested_fix"], str)
        assert isinstance(enr["related_skills"], list) and enr["related_skills"]
    json.dumps(out, allow_nan=False)


def test_enrich_selector_match_count_with_body(box_body):
    zero = {"op": "extrude_pocket", "status": "fail",
            "error": "face_selector matched 0 faces: "
                     "{'kind': 'faces_by_area', 'min': 1000000000.0}",
            "args": _POCKET_ARGS_ZERO_MATCH}
    hit = {"op": "extrude_pocket", "status": "fail",
           "error": "extrude_pocket cut failed",
           "args": {"face_selector": {"kind": "face_named", "name": "top"},
                    "sketch": {"kind": "circle", "diameter_mm": 5},
                    "depth_mm": 2}}
    out = enrich_failures([zero, hit], body=box_body)
    assert out[0]["selector_match_count"] == 0
    assert out[0]["selector_suggestions"]              # 0-match -> suggestions
    assert out[1]["selector_match_count"] == 1
    assert "selector_suggestions" not in out[1]        # only on 0-match
    # without a body, no counts are attempted
    out_nobody = enrich_failures([zero])
    assert "selector_match_count" not in out_nobody[0]
    json.dumps(out, allow_nan=False)


def test_enrich_never_raises_on_garbage(box_body):
    garbage = [
        None,
        42,
        "boom",
        [],
        {},
        {"status": "fail"},                                  # no error
        {"status": "fail", "error": None},                   # falsy error
        {"status": "fail", "error": 123},                    # non-str error
        {"status": "fail", "error": "x", "args": "not-a-dict"},
        {"status": "fail", "error": "y",
         "args": {"face_selector": {"kind": "no_such_kind"}}},
        {"status": "pass", "error": "leftover"},             # pass -> untouched
    ]
    out = enrich_failures(garbage, body=box_body)
    assert len(out) == len(garbage)
    # non-str error is classified best-effort but preserved untouched
    assert out[7]["error"] == 123 and "likely_cause" in out[7]
    # entries with no/falsy error stay classification-free
    assert "likely_cause" not in out[5] and "likely_cause" not in out[6]
    # pass entries stay untouched even when they carry an error string
    assert out[10] == garbage[10]
    # non-list input degrades to [] instead of raising
    assert enrich_failures("garbage") == []                  # type: ignore[arg-type]
    assert enrich_failures(None) == []                       # type: ignore[arg-type]
