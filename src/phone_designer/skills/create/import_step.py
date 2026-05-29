"""import_step — atomic create. STEP 파일 → body.

OEM 부품 인서트, 합성 fixture 의 부품 1개 가져오기 등.
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
    name="import_step",
    category="create",
    level="atomic",
    summary="Load a STEP file as the body. Used to insert OEM parts or pre-built components.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["imported_solid"],
    preserves=[],
    manufacturing={},  # 가져온 파일이라 공정 추정 없음
    failure_modes=["fm.file_not_found", "fm.step_parse_failed"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class ImportStep(SkillBase):
    class Args(BaseModel):
        path: str = Field(min_length=1, description="STEP 파일 경로 (repo-relative 또는 절대)")
        scale: float = Field(default=1.0, gt=0,
                              description="단위 환산 (mm 가 아닌 경우, 예: 0.001=mm→m)")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.STEPControl import STEPControl_Reader
        from OCP.IFSelect import IFSelect_RetDone

        p = Path(args.path)
        if not p.exists():
            raise FileNotFoundError(f"STEP not found: {p}")

        reader = STEPControl_Reader()
        status = reader.ReadFile(str(p))
        if status != IFSelect_RetDone:
            raise RuntimeError(f"STEP parse failed: {p} (status={status})")
        reader.TransferRoots()
        shape = reader.OneShape()

        # 단위 환산 (필요 시)
        if args.scale != 1.0:
            from OCP.gp import gp_Trsf
            from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
            trsf = gp_Trsf()
            trsf.SetScaleFactor(args.scale)
            shape = BRepBuilderAPI_Transform(shape, trsf, True).Shape()

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(body=Part(shape), history=history)
