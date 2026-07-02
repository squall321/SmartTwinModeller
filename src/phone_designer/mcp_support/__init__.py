"""mcp_support — Phase-1 session/loop plumbing for the MCP server.

Modules (each self-contained; mcp_server.py wires them into FastMCP tools):
  _body_store     — BodyStore: LRU body cache w/ STEP-snapshot lineage + undo
  _guarded_exec   — warm-worker subprocess plan execution w/ hard timeout
  _session_tools  — import/modify/measure/preview primitives over a body
  _failure_enrich — machine-actionable failure enrichment + spec preflight
  _pillar_tools   — compare / variants / cheapest-variant thin wrappers
"""
