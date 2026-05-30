"""iges_import — atomic create. IGES file (.igs/.iges) → body.

IGES (Initial Graphics Exchange Specification) is one of the older, but still
common, neutral CAD exchange formats. Many legacy automotive / aerospace
tools (CATIA V4, NX) emit IGES alongside STEP. We use OCCT's
`IGESControl_Reader`:

    reader.ReadFile(path)        # parse the .iges into the reader's model
    reader.TransferRoots()       # translate every root entity into TopoDS
    shape = reader.OneShape()    # collapse to a single shape (compound if N>1)

The result is wrapped as a build123d `Part`. As with `import_step`, the file
may contain surfaces (rather than solids); downstream callers may need
`sew_shell_to_solid` or similar to lift it to a closed solid.
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
    name="iges_import",
    category="create",
    level="atomic",
    summary="Load an IGES (.igs/.iges) file as the body via "
            "IGESControl_Reader. Used to ingest legacy CAD geometry "
            "(surfaces or solids) from CATIA V4 / NX / older tools.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["imported_iges"],
    preserves=[],
    manufacturing={},
    failure_modes=["fm.file_not_found", "fm.iges_parse_failed"],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class IgesImport(SkillBase):
    class Args(BaseModel):
        path: str = Field(min_length=1,
                          description="IGES 파일 경로 (.igs/.iges), 절대 또는 cwd-relative")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.IFSelect import IFSelect_ReturnStatus
        from OCP.IGESControl import IGESControl_Reader

        p = Path(args.path)
        if not p.exists():
            raise FileNotFoundError(f"IGES not found: {p}")

        reader = IGESControl_Reader()
        status = reader.ReadFile(str(p))
        if status != IFSelect_ReturnStatus.IFSelect_RetDone:
            raise RuntimeError(f"iges_import: parse failed for {p} (status={status})")

        n_roots = int(reader.TransferRoots())
        if n_roots <= 0:
            raise RuntimeError(
                f"iges_import: TransferRoots translated 0 entities — "
                f"file may be empty or contain only unsupported entity types: {p}"
            )
        shape = reader.OneShape()
        if shape is None or shape.IsNull():
            raise RuntimeError(f"iges_import: OneShape returned null for {p}")

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(
            body=Part(shape),
            history=history,
            extras={
                "source_path": str(p),
                "roots_transferred": int(n_roots),
            },
        )
