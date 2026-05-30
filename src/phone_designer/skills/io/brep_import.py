"""brep_import — atomic create. Native OCCT .brep file → body.

The .brep format is OCCT's own ASCII serialization of `TopoDS_Shape`. It is
*lossless* — it preserves the full B-rep topology, tolerances, and (when
written that way) the cached triangulation. We use it as the canonical
internal interchange format between SmartTwinModeller runs / processes.

Read path:

    shape = TopoDS_Shape()
    builder = BRep_Builder()
    ok = BRepTools.Read_s(shape, str(path), builder)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="brep_import",
    category="create",
    level="atomic",
    summary="Load an OCCT-native .brep file as the body via BRepTools.Read_s "
            "+ BRep_Builder. Lossless interchange format — preserves full "
            "B-rep topology and tolerances.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["imported_brep"],
    preserves=[],
    manufacturing={},
    failure_modes=["fm.file_not_found", "fm.brep_parse_failed"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class BrepImport(SkillBase):
    class Args(BaseModel):
        path: str = Field(min_length=1,
                          description="BREP 파일 경로 (.brep), 절대 또는 cwd-relative")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRep import BRep_Builder
        from OCP.BRepTools import BRepTools
        from OCP.TopoDS import TopoDS_Shape

        p = Path(args.path)
        if not p.exists():
            raise FileNotFoundError(f"BREP not found: {p}")

        shape = TopoDS_Shape()
        builder = BRep_Builder()
        try:
            ok = BRepTools.Read_s(shape, str(p), builder)
        except Exception as exc:  # noqa: BLE001 — surface OCCT failure
            raise RuntimeError(f"brep_import: BRepTools.Read_s raised {exc!r} on {p}") from exc

        if not ok or shape.IsNull():
            raise RuntimeError(f"brep_import: failed to parse {p}")

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(
            body=Part(shape),
            history=history,
            extras={"source_path": str(p)},
        )
