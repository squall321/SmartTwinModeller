"""stl_import — atomic create. STL mesh file → body.

STL is a polygon-soup format (no topology). We use `StlAPI_Reader.Read` which
converts every triangle into an OCCT face; that yields a TopoDS_Shape (compound
of N faces) which is enough for downstream `inspect_geometry`, bbox queries,
and re-export. Note: the result is *not* a closed solid — boolean operations
against it will generally fail. Use `merge_tolerance_mm` as a hint; OCCT's
StlAPI ignores it but we keep the arg so callers / planners can document
intent and a future sewing-based implementation can honor it.
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
    name="stl_import",
    category="create",
    level="atomic",
    summary="Load an STL mesh file as the body. Result is a faceted shape "
            "(not a closed solid); good for inspection / visualization / "
            "re-export but unreliable for boolean ops.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["imported_mesh"],
    preserves=[],
    manufacturing={},
    failure_modes=["fm.file_not_found", "fm.stl_parse_failed"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class StlImport(SkillBase):
    class Args(BaseModel):
        path: str = Field(min_length=1, description="STL 파일 경로 (절대 또는 cwd-relative)")
        merge_tolerance_mm: float = Field(
            default=0.01, ge=0,
            description="Hint for downstream sewing (currently informational — "
                        "StlAPI_Reader emits independent faces).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.StlAPI import StlAPI_Reader
        from OCP.TopoDS import TopoDS_Shape

        p = Path(args.path)
        if not p.exists():
            raise FileNotFoundError(f"STL not found: {p}")

        reader = StlAPI_Reader()
        shape = TopoDS_Shape()
        ok = reader.Read(shape, str(p))
        if not ok or shape.IsNull():
            raise RuntimeError(f"STL parse failed: {p}")

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(
            body=Part(shape),
            history=history,
            extras={"source_path": str(p),
                    "merge_tolerance_mm": float(args.merge_tolerance_mm)},
        )
