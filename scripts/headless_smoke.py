"""Headless smoke test — PROVE the MCP modelling surface works without a UI/GL
stack (so a Linux container build can fail fast if something is wrong).

Generates a part from scratch, exports STEP+STL, runs a cost estimate, and asserts
NO UI stack (vtk / PySide6 / pyvista) was imported. Exits non-zero on any failure.

Run:  python scripts/headless_smoke.py
"""
from __future__ import annotations

import sys


def main() -> int:
    import phone_designer.mcp_server as M

    g = M.cad_generate([
        {"op": "box", "args": {"length_mm": 40, "width_mm": 30, "height_mm": 10}},
        {"op": "hole", "args": {"position": [0, 0, 10], "diameter_mm": 6,
                                "depth_mm": 10, "direction": "-Z"}},
    ], name="smoke", formats=["step", "stl"])
    assert g.get("ok") and g.get("is_solid"), f"generate failed: {g}"
    step = g["files"]["step"]
    assert step.endswith(".step"), g

    c = M.cad_estimate_cost(body_id=g["body_id"], process="cnc_3axis",
                            material="aluminum")
    assert c.get("ok") and c.get("unit_cost_usd", 0) > 0, f"cost failed: {c}"

    ui = [m for m in ("vtk", "PySide6", "pyvista", "pyvistaqt", "PyQt5", "PyQt6")
          if m in sys.modules]
    assert not ui, f"UI stack unexpectedly loaded headless: {ui}"

    print(f"HEADLESS SMOKE OK — STEP={step}  vol={g['volume_mm3']}mm3  "
          f"cost=${c['unit_cost_usd']}/unit  ui_loaded={ui or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
