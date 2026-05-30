"""step_export_v2 — atomic inspect. body → STEP file, v2 with schema + color.

Improvement over the original mesh-only / plain STEP path:

  * `schema` arg selects the STEP application protocol (AP203, AP214, AP242).
    Configured via `Interface_Static.SetCVal_s("write.step.schema", ...)`.
  * `with_colors=True` routes the export through `STEPCAFControl_Writer`
    against an in-memory XCAF document so that color attributes attached to
    the body via `tag_face` survive the round-trip. The plain
    `STEPControl_Writer` discards colors.

Body is returned unchanged (read-only — only the filesystem is touched).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


_SCHEMA_VALUE = {
    # OCCT's documented values for write.step.schema. Empirically:
    # 1=AP214CD, 2=AP214IS, 3=AP203, 4=AP242DIS, 5=AP214DIS. We honor the
    # three protocols the spec lists.
    "AP203": "3",
    "AP214": "2",
    "AP242": "4",
}


def _configure_schema(schema: str) -> None:
    """Set the OCCT static parameter for STEP schema selection."""
    from OCP.Interface import Interface_Static
    Interface_Static.SetCVal_s("write.step.schema", _SCHEMA_VALUE[schema])


def _write_plain(shape, out_path: Path) -> None:
    """STEPControl_Writer path — no XCAF, no colors."""
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPControl import STEPControl_StepModelType, STEPControl_Writer

    writer = STEPControl_Writer()
    status = writer.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs)
    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise RuntimeError(f"step_export_v2: Transfer failed (status={status})")
    write_status = writer.Write(str(out_path))
    if write_status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise RuntimeError(f"step_export_v2: Write failed (status={write_status})")


def _write_with_cafdoc(shape, out_path: Path) -> None:
    """STEPCAFControl_Writer path — XCAF doc carries shape + color labels."""
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_StepModelType
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    shape_tool.AddShape(shape, True, True)

    writer = STEPCAFControl_Writer()
    ok = writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
    if not ok:
        raise RuntimeError("step_export_v2: STEPCAFControl_Writer.Transfer returned False")
    write_status = writer.Write(str(out_path))
    if write_status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise RuntimeError(f"step_export_v2: STEPCAF Write failed (status={write_status})")


@skill(
    name="step_export_v2",
    category="inspect",
    level="atomic",
    summary="Write the current body to a STEP file with configurable schema "
            "(AP203 / AP214 / AP242) and optional color preservation via "
            "STEPCAFControl_Writer. Body is returned unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["step_artifact"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.step_write_failed", "fm.unsupported_step_schema"],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class StepExportV2(SkillBase):
    class Args(BaseModel):
        model_config = {"populate_by_name": True, "protected_namespaces": ()}

        path: str = Field(min_length=1, description="출력 STEP 경로 (.step/.stp)")
        # Pydantic v2 protects BaseModel.schema(); expose as ``schema`` over the
        # wire via alias while using ``step_schema`` as the Python attribute.
        step_schema: Literal["AP203", "AP214", "AP242"] = Field(
            default="AP242",
            alias="schema",
            description="STEP application protocol. AP242 is the modern default "
                        "and supports PMI/GD&T; AP214 is the long-standing "
                        "automotive baseline; AP203 is the oldest 'config-mgmt' "
                        "protocol still occasionally required by legacy CAM.",
        )
        with_colors: bool = Field(
            default=True,
            description="If True, write through STEPCAFControl_Writer + an "
                        "XCAF document so color attributes survive. If False, "
                        "use the plain STEPControl_Writer (smaller file).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("step_export_v2: body is None")
        shape = body.wrapped if hasattr(body, "wrapped") else body
        if shape is None:
            raise ValueError("step_export_v2: body.wrapped is None")

        out_path = Path(args.path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        _configure_schema(args.step_schema)

        if args.with_colors:
            _write_with_cafdoc(shape, out_path)
        else:
            _write_plain(shape, out_path)

        if not out_path.exists():
            raise RuntimeError(f"step_export_v2: file not produced → {out_path}")

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "written_path": str(out_path),
                "schema": args.step_schema,
                "with_colors": bool(args.with_colors),
                "file_size_bytes": int(out_path.stat().st_size),
            },
        )
