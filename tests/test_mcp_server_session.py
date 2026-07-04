"""MCP Phase-1 session tools — the stateful / self-correcting / hang-proof loop.

Pins the roadmap Phase-1 contract end-to-end through the WIRED server (not just
the mcp_support modules): generate → modify (volume decreases) → undo (volume
restored EXACTLY) → measure → preview (honest headless skip) → preflight →
machine-actionable failure enrichment (raw error never masked) → pillar tools →
one-call RFQ package.

Runs with PHONE_DESIGNER_SKILL_TIMEOUT_S=0 (inline lane) so CI stays fast — the
worker/timeout lane is pinned by tests/test_guarded_exec.py.
"""
from __future__ import annotations

import json
import math
import os

import pytest

os.environ.setdefault("PHONE_DESIGNER_UI_HEADLESS", "1")
os.environ["PHONE_DESIGNER_SKILL_TIMEOUT_S"] = "0"  # inline lane for CI speed

# the `mcp` package (FastMCP) is not installed in every CI lane — skip the whole
# module cleanly instead of interrupting collection (same guard as
# test_mcp_server.py).
M = pytest.importorskip(
    "phone_designer.mcp_server",
    reason="mcp (FastMCP) not installed in this environment")


@pytest.fixture(scope="module")
def box_id():
    g = M.cad_generate([{"op": "box", "args": {
        "length_mm": 40, "width_mm": 30, "height_mm": 10}}], name="sess_box")
    assert g["ok"] and g["body_id"]
    return g["body_id"]


# ── session flow ─────────────────────────────────────────────────────────────

def test_modify_decreases_volume_and_links_parent(box_id):
    m = M.cad_modify(box_id, [{"op": "hole", "args": {
        "position": [0, 0, 10], "diameter_mm": 6, "depth_mm": 10,
        "direction": "-Z"}}])
    assert m["ok"] and m["body_id"]
    assert m["parent_body_id"] == box_id
    # 12000 − π·3²·10 = 11717.256
    assert m["volume_mm3"] == pytest.approx(12000 - math.pi * 9 * 10, rel=1e-4)


def test_undo_restores_exact_parent_volume(box_id):
    m = M.cad_modify(box_id, [{"op": "hole", "args": {
        "position": [0, 0, 10], "diameter_mm": 8, "depth_mm": 10,
        "direction": "-Z"}}])
    u = M.cad_undo(m["body_id"])
    assert u["ok"] and u["body_id"] == box_id
    assert u["volume_mm3"] == pytest.approx(12000.0, abs=1e-6)
    assert isinstance(u["lineage"], list) and u["lineage"][0]["parent_id"] is None


def test_undo_at_root_is_honest_refusal(box_id):
    u = M.cad_undo(box_id)
    assert u["ok"] is False and "fm.at_root" in u["error"]


def test_unknown_body_id_structured_error():
    r = M.cad_measure(body_id="body_nope", what="mass")
    assert r["ok"] is False and "unknown_body_id" in r["error"]


def test_measure_mass_and_obb(box_id):
    mass = M.cad_measure(body_id=box_id, what="mass")
    assert mass["ok"] and mass["volume_mm3"] == pytest.approx(12000, rel=1e-6)
    obb = M.cad_measure(body_id=box_id, what="obb")
    assert sorted(obb["obb"]["size_mm"]) == pytest.approx([10, 30, 40], abs=1e-3)


def test_preview_headless_now_renders_real_pngs(box_id):
    # UPDATED (viewer work): cad_preview used to return skipped_no_gl under
    # headless (GPU/GL path). It now defaults to the GL-free numpy renderer, so
    # headless yields REAL PNGs — the old skip-marker assertion is intentionally
    # replaced.
    import os
    pv = M.cad_preview(body_id=box_id)
    assert pv["ok"] is True and pv["skipped"] is False
    assert pv.get("renderer") == "headless_raster"
    iso = (pv.get("images") or {}).get("iso")
    assert iso and os.path.exists(iso) and os.path.getsize(iso) > 2000
    json.dumps(pv, allow_nan=False)  # strict-JSON-safe payload


def test_import_roundtrip(box_id):
    exp = M.cad_export(body_id=box_id, formats=["step"], name="sess_reimp")
    imp = M.cad_import(exp["files"]["step"])
    assert imp["ok"] and imp["body_id"]
    assert imp["volume_mm3"] == pytest.approx(12000, rel=1e-4)


# ── self-correction loop ─────────────────────────────────────────────────────

def test_preflight_separates_tool_ok_from_spec_verdict():
    pf = M.cad_preflight([{"op": "nonexistent_op", "args": {}},
                          {"op": "box", "args": {"length_mm": 5, "width_mm": 5,
                                                 "height_mm": 5}}])
    assert pf["ok"] is True          # the tool ran
    assert pf["spec_ok"] is False    # the spec verdict
    assert pf["steps"][0]["known"] is False
    assert pf["steps"][1]["known"] is True


def test_failure_enrichment_adds_hints_but_never_masks_error():
    bad = M.cad_generate([
        {"op": "box", "args": {"length_mm": 10, "width_mm": 10, "height_mm": 10}},
        {"op": "fillet_edges_by_predicate", "args": {
            "selector": {"kind": "faces_by_area", "min": 1e9}, "radius_mm": 3}}],
        name="sess_bad")
    failed = [s for s in bad["steps"] if s["status"] != "pass"]
    assert failed, "the impossible selector step must fail"
    st = failed[0]
    assert st.get("error")                     # raw error preserved
    assert st.get("likely_cause")              # machine-actionable hint added
    assert st.get("suggested_fix")


# ── pillar + RFQ wiring ──────────────────────────────────────────────────────

def test_quote_package_one_call(box_id):
    q = M.cad_quote_package(body_id=box_id, lot_sizes=[1, 100])
    assert q["ok"] and q["zip_path"] and os.path.exists(q["zip_path"])
    man = q["manifest"]
    assert man and "costs" in man
    json.dumps(q, allow_nan=False)


def test_compare_wiring(box_id):
    g2 = M.cad_generate([{"op": "box", "args": {
        "length_mm": 40, "width_mm": 30, "height_mm": 10}}], name="sess_box2")
    c = M.cad_compare(body_a=box_id, body_b=g2["body_id"])
    assert c["ok"] is True
    assert c.get("summary", {}).get("classification")
