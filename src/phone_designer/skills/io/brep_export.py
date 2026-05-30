"""brep_export — atomic inspect. body → native OCCT .brep file.

Writes the current body using `BRepTools.Write_s`. The .brep format is
OCCT's own ASCII serialization — it is *lossless* (preserves full B-rep
topology, tolerances, and optionally cached triangulation). We use it as the
canonical interchange format between SmartTwinModeller runs, for
checkpoints, regression fixtures, and as the round-trip target in CI
("save → reload → compare" must be bit-stable for geometry).

Body is returned unchanged — only the filesystem is touched.
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
    name="brep_export",
    category="inspect",
    level="atomic",
    summary="Write the current body to an OCCT-native .brep file via "
            "BRepTools.Write_s. Lossless — round-trip preserves full B-rep "
            "topology. Body is returned unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["brep_artifact"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.brep_write_failed"],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class BrepExport(SkillBase):
    class Args(BaseModel):
        path: str = Field(min_length=1,
                          description="출력 BREP 경로 (.brep)")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepTools import BRepTools

        if body is None:
            raise ValueError("brep_export: body is None")
        shape = body.wrapped if hasattr(body, "wrapped") else body
        if shape is None:
            raise ValueError("brep_export: body.wrapped is None")

        out_path = Path(args.path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            ok = BRepTools.Write_s(shape, str(out_path))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"brep_export: BRepTools.Write_s raised {exc!r} → {out_path}"
            ) from exc

        if not ok or not out_path.exists():
            raise RuntimeError(f"brep_export: write failed → {out_path}")

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "written_path": str(out_path),
                "file_size_bytes": int(out_path.stat().st_size),
            },
        )
