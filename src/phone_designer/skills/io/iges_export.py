"""iges_export — atomic inspect. body → IGES file (.igs/.iges).

Writes the current body out as IGES using OCCT's `IGESControl_Writer`:

    writer = IGESControl_Writer()
    writer.AddShape(shape)
    writer.Write(path)

Used when the downstream consumer (a legacy CAM, CATIA V4 station, or older
analysis tool) cannot read STEP. Body is returned unchanged — this skill is
read-only as far as the model is concerned; only the filesystem is touched.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="iges_export",
    category="inspect",
    level="atomic",
    summary="Write the current body to an IGES (.igs/.iges) file via "
            "IGESControl_Writer. Read-only: body is returned unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["iges_artifact"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.iges_write_failed"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class IgesExport(SkillBase):
    class Args(BaseModel):
        path: str = Field(min_length=1,
                          description="출력 IGES 경로 (.igs 또는 .iges)")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.IGESControl import IGESControl_Writer

        if body is None:
            raise ValueError("iges_export: body is None")
        shape = body.wrapped if hasattr(body, "wrapped") else body
        if shape is None:
            raise ValueError("iges_export: body.wrapped is None")

        out_path = Path(args.path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        writer = IGESControl_Writer()
        added = bool(writer.AddShape(shape))
        if not added:
            raise RuntimeError(
                f"iges_export: AddShape rejected the body — shape may be "
                f"null or unsupported (path={out_path})"
            )
        ok = bool(writer.Write(str(out_path)))
        if not ok or not out_path.exists():
            raise RuntimeError(f"iges_export: Write failed → {out_path}")

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "written_path": str(out_path),
                "file_size_bytes": int(out_path.stat().st_size),
            },
        )
