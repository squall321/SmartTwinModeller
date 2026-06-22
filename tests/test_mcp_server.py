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
            "cad_analyze", "cad_estimate_cost", "cad_recommend_process"} <= names


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
