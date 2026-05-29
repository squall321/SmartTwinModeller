"""stl_export — atomic, read-only inspect. body → STL file.

Tessellates the body in-place via `BRepMesh_IncrementalMesh`, writes binary STL
through `StlAPI_Writer`, and returns the body unchanged. The mesh quality is
controlled by `linear_deflection_mm` (chord deviation, smaller = finer) and
`angular_deflection_deg` (per-face angle limit).

Diagnostic info — `triangle_count`, `written_path` — is attached to
`result.extras`. The skill is filed under `inspect` because it never mutates
geometry; the only side effect is the filesystem write.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _count_triangles(shape: Any) -> int:
    """Total triangle count across every face's Poly_Triangulation."""
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    total = 0
    it = TopExp_Explorer(shape, TopAbs_FACE)
    while it.More():
        try:
            face = TopoDS.Face_s(it.Current())
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(face, loc)
            if tri is not None:
                total += int(tri.NbTriangles())
        except Exception:
            pass
        it.Next()
    return total


@skill(
    name="stl_export",
    category="inspect",
    level="atomic",
    summary="Tessellate the body and write it to an STL file. Read-only — "
            "body is returned unchanged; only the filesystem is touched.",
    selector_kinds=[],
    history_rules={},
    produces_features=["stl_artifact"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.stl_write_failed", "fm.tessellation_failed"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class StlExport(SkillBase):
    class Args(BaseModel):
        path: str = Field(min_length=1, description="출력 STL 경로")
        linear_deflection_mm: float = Field(
            default=0.1, gt=0,
            description="Max chord deviation in mm — smaller = finer mesh.",
        )
        angular_deflection_deg: float = Field(
            default=5.0, gt=0,
            description="Per-face angular deflection limit in degrees.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.StlAPI import StlAPI_Writer

        if body is None:
            raise ValueError("stl_export: body is None")

        shape = body.wrapped if hasattr(body, "wrapped") else body
        if shape is None:
            raise ValueError("stl_export: body.wrapped is None")

        ang_rad = math.radians(float(args.angular_deflection_deg))
        mesher = BRepMesh_IncrementalMesh(
            shape,
            float(args.linear_deflection_mm),
            False,
            ang_rad,
            True,  # parallel
        )
        mesher.Perform()
        if not mesher.IsDone():
            raise RuntimeError("stl_export: BRepMesh_IncrementalMesh failed")

        out_path = Path(args.path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        writer = StlAPI_Writer()
        ok = writer.Write(shape, str(out_path))
        if not ok or not out_path.exists():
            raise RuntimeError(f"stl_export: StlAPI_Writer.Write failed → {out_path}")

        tri_count = _count_triangles(shape)

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "written_path": str(out_path),
                "triangle_count": int(tri_count),
                "linear_deflection_mm": float(args.linear_deflection_mm),
                "angular_deflection_deg": float(args.angular_deflection_deg),
            },
        )
