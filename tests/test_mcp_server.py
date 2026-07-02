"""MCP server — the LLM-facing front door to model + analyse parts.

Pins the tool surface and the file-path state model: generate a part from a spec
→ STEP written to the workspace → cost on that STEP; plus discovery (list_skills /
get_skill_schema) so an LLM can self-serve specs, and structured error isolation.
"""
from __future__ import annotations

import os

import pytest

mcp_server = pytest.importorskip("phone_designer.mcp_server",
                                 reason="mcp SDK not installed")


def test_server_and_tools_registered():
    import asyncio
    assert mcp_server.mcp.name == "phone-designer-cad"
    tools = asyncio.new_event_loop().run_until_complete(mcp_server.mcp.list_tools())
    names = {t.name for t in tools}
    assert {"cad_list_skills", "cad_get_skill_schema", "cad_generate",
            "cad_analyze", "cad_estimate_cost", "cad_recommend_process",
            "cad_export"} <= names


def _bracket_spec():
    spec = [{"op": "box", "args": {"length_mm": 60, "width_mm": 40,
                                   "height_mm": 5}}]
    spec += [{"op": "hole", "args": {"position": [x, y, 5], "diameter_mm": 5,
                                     "depth_mm": 5, "direction": "-Z"}}
             for (x, y) in [(-25, -15), (25, -15), (-25, 15), (25, 15)]]
    return spec


def test_generate_part_writes_step_and_flows_into_cost():
    r = mcp_server.cad_generate(_bracket_spec(), name="t_bracket",
                                 formats=["step"])
    assert r["ok"] and r["is_solid"]
    assert r["volume_mm3"] < 12000.0          # the 4 holes removed material
    step = r["files"]["step"]
    assert os.path.exists(step) and os.path.getsize(step) > 0
    # the generated STEP flows into the analysis tools
    c = mcp_server.cad_estimate_cost(step, process="cnc_3axis", material="aluminum")
    assert c["ok"] and c["unit_cost_usd"] > 0 and c["grade"] == "estimate"


def test_discovery_lets_an_llm_self_serve_specs():
    ls = mcp_server.cad_list_skills(query="hole", limit=10)
    assert ls["ok"] and ls["n"] > 0
    gs = mcp_server.cad_get_skill_schema("box")
    assert gs["ok"]
    props = (gs["args_schema"] or {}).get("properties", {})
    assert {"length_mm", "width_mm", "height_mm"} <= set(props)


def test_bad_spec_is_isolated_not_crashed():
    r = mcp_server.cad_generate([{"op": "nope_not_a_skill", "args": {}}])
    assert r["ok"] is False
    assert any("unknown skill" in e for e in r["spec_errors"])


def test_get_skill_schema_unknown_is_structured_error():
    r = mcp_server.cad_get_skill_schema("definitely_not_a_skill")
    assert r["ok"] is False and "unknown skill" in r["error"]


def test_body_id_session_cache_and_export():
    # cad_generate mints a body_id; later tools take it (no path re-passing) and
    # cad_export re-exports the cached body without regenerating
    g = mcp_server.cad_generate(
        [{"op": "box", "args": {"length_mm": 50, "width_mm": 30,
                                "height_mm": 8}}], name="cube", formats=["step"])
    bid = g["body_id"]
    assert bid and g["resource_uris"]
    c = mcp_server.cad_estimate_cost(body_id=bid, process="cnc_3axis")
    assert c["ok"] and c["unit_cost_usd"] > 0
    e = mcp_server.cad_export(body_id=bid, formats=["stl"])
    assert e["ok"] and "stl" in e["files"] and e["resource_uris"]


def test_resolve_guards_one_of_body_id_or_path():
    # exactly-one-of contract + unknown body_id are structured errors, not crashes
    assert mcp_server.cad_estimate_cost()["ok"] is False                 # neither
    assert mcp_server.cad_estimate_cost(part_path="x.step",
                                        body_id="body_1")["ok"] is False  # both
    r = mcp_server.cad_estimate_cost(body_id="body_does_not_exist")
    # BodyStore raises the structured fm.unknown_body_id token (was the older
    # free-text "unknown body_id" message before Phase-1 sessionization).
    assert r["ok"] is False and "unknown_body_id" in r["error"]


# ── base-shape primitives are fully covered by the MCP surface ────────────────
# A client builds a primitive the same way it builds anything: cad_list_skills /
# cad_get_skill_schema to discover the op + its args, then cad_generate. These
# pin that EVERY core base shape round-trips through that flow to a valid solid +
# a resolvable body_id (so "MCP covers base shapes" is guaranteed, not incidental).

# op -> a known-good arg set (the args each create skill actually declares).
_BASE_SHAPES = {
    "box": {"length_mm": 40, "width_mm": 30, "height_mm": 12},
    "cylinder": {"radius_mm": 15, "height_mm": 30},
    "cone": {"radius_lower_mm": 20, "radius_upper_mm": 5, "height_mm": 30},
    "sphere": {"radius_mm": 20},
    "torus": {"major_radius_mm": 20, "minor_radius_mm": 5},
    "wedge": {"dx": 40, "dy": 20, "dz": 15, "ltx_mm": 10},
    "prism_n_sided": {"n_sides": 6, "circumscribed_radius_mm": 15, "height_mm": 20},
}


@pytest.mark.parametrize("op,args", list(_BASE_SHAPES.items()))
def test_base_shape_generates_via_mcp(op, args):
    # 1) discovery: the op + its args are self-describable (a client reads this
    #    to compose the spec — every arg we pass is a declared property).
    gs = mcp_server.cad_get_skill_schema(op)
    assert gs["ok"], f"{op} not discoverable"
    props = set((gs["args_schema"] or {}).get("properties", {}))
    assert set(args) <= props, f"{op}: {set(args) - props} not in schema"

    # 2) generation: cad_generate builds the primitive to a valid solid + body_id.
    g = mcp_server.cad_generate([{"op": op, "args": args}], name=f"prim_{op}")
    assert g["ok"] and g["is_solid"], f"{op} did not build: {g.get('steps')}"
    assert g["volume_mm3"] > 0
    bid = g["body_id"]
    assert bid and g["resource_uris"]
    assert os.path.exists(g["files"]["step"])

    # 3) the primitive flows into the analysis surface by body_id.
    c = mcp_server.cad_estimate_cost(body_id=bid, process="cnc_3axis")
    assert c["ok"] and c["unit_cost_usd"] > 0


def test_create_category_lists_the_base_shapes():
    # a client enumerates the buildable base shapes via the create category.
    ls = mcp_server.cad_list_skills(category="create", limit=60)
    assert ls["ok"] and ls["n"] >= len(_BASE_SHAPES)
    names = {s["name"] for s in ls["skills"]}
    assert set(_BASE_SHAPES) <= names, f"missing from create listing: {set(_BASE_SHAPES) - names}"


# ── DFM repair + orchestration tools (A1 exposed; analyze→repair→quote chain) ──

def _sharp_pocket_step(tmp_path):
    """A block with a square pocket (sharp internal corners) → STEP, the DFM
    issue cad_repair_dfm fixes (filleting the concave edges to the tool radius)."""
    from build123d import (
        Axis, Box, BuildPart, BuildSketch, Mode, Rectangle, extrude,
    )
    with BuildPart() as bp:
        Box(40, 30, 12)
        with BuildSketch(bp.faces().sort_by(Axis.Z)[-1]):
            Rectangle(16, 10)
        extrude(amount=-8, mode=Mode.SUBTRACT)
    path = str(tmp_path / "sharp_pocket.step")
    assert mcp_server._write_step(bp.part, path)
    return path


def test_repair_and_workflow_tools_registered():
    import asyncio
    tools = asyncio.new_event_loop().run_until_complete(mcp_server.mcp.list_tools())
    names = {t.name for t in tools}
    assert {"cad_repair_dfm", "cad_dfm_workflow"} <= names


def test_cad_repair_dfm_fixes_and_mints_a_new_body(tmp_path):
    path = _sharp_pocket_step(tmp_path)
    r = mcp_server.cad_repair_dfm(part_path=path, processes=["cnc_milling"])
    assert r["ok"] and r["body_changed"] is True and r["grade"] == "repaired"
    # a NEW body_id is minted for the repaired part + its STEP is written.
    rid = r["repaired_body_id"]
    assert rid and "step" in r["files"] and os.path.exists(r["files"]["step"])
    assert r["resource_uris"]
    assert r["fixes_applied"] and r["fixes_applied"][0]["op"] == "enforce_min_tool_radius"
    assert r["total_hausdorff_mm"] >= 0.0
    # before/after verdicts are present per process.
    assert "cnc_milling" in r["before"] and "cnc_milling" in r["after"]
    # the repaired body_id resolves for a follow-up analysis call (session cache).
    c = mcp_server.cad_estimate_cost(body_id=rid, process="cnc_3axis")
    assert c["ok"] and c["unit_cost_usd"] > 0


def test_cad_repair_dfm_clean_part_no_change_no_new_body():
    g = mcp_server.cad_generate(
        [{"op": "box", "args": {"length_mm": 30, "width_mm": 20,
                                "height_mm": 10}}], name="clean")
    r = mcp_server.cad_repair_dfm(body_id=g["body_id"], processes=["cnc_milling"])
    assert r["ok"] and r["body_changed"] is False
    assert r["repaired_body_id"] is None
    assert r["grade"] in ("no_change", "suggest_only")
    assert r["total_hausdorff_mm"] == 0.0


def test_cad_dfm_workflow_chains_repair_and_quote(tmp_path):
    path = _sharp_pocket_step(tmp_path)
    w = mcp_server.cad_dfm_workflow(part_path=path, processes=["cnc_milling"],
                                    material="aluminum", lot_size=1000)
    assert w["ok"] and w["grade"] == "estimate"
    # the part was repaired and the QUOTE is on the repaired geometry.
    assert w["repair"]["body_changed"] is True
    assert w["repaired_body_id"] and w["priced_body"] == "repaired"
    assert w["quote"]["recommendation"] is not None
    assert w["quote"]["overall_flag"] in {
        "ok", "marginal", "advisory_alternative_exists", "no_viable_process",
        "input_unreliable"}


def test_cad_dfm_workflow_repair_false_prices_the_input(tmp_path):
    path = _sharp_pocket_step(tmp_path)
    w = mcp_server.cad_dfm_workflow(part_path=path, repair=False)
    assert w["ok"] and w["repair"] is None
    assert w["priced_body"] == "input" and w["repaired_body_id"] is None
    assert w["quote"]["recommendation"] is not None
