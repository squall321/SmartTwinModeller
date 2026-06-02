"""Phone Designer desktop UI (PySide6 + pyvistaqt).

[[lat.md/ui]] 의 본격 구현. Phase 4 의 핵심 panel 들은 inspector_main 로 시작.

진입점:
    python -m phone_designer ui                          # 빈 inspector
    python -m phone_designer inspect --plan <plan.yaml>  # plan 즉시 실행 후 표시
    python -m phone_designer inspect --step <out.step>   # STEP 단일 표시
"""
from phone_designer.ui.inspector_main import (
    InspectorMainWindow,
    run_inspector,
    run_inspector_with_panels,
)

__all__ = [
    "InspectorMainWindow",
    "run_inspector",
    "run_inspector_with_panels",
]
