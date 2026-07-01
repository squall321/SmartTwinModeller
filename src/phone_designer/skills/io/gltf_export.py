"""gltf_export — atomic, read-only io. body → glTF / GLB file.

glTF (``.gltf`` — JSON + a ``.bin`` sidecar) and its binary container GLB
(``.glb`` — one self-contained file) are the ubiquitous web / AR / design-review
3D formats (three.js, model-viewer, Windows 3D Viewer, USDZ pipelines, etc.).

OCCT 7.8 writes glTF natively through ``RWGltf_CafWriter``, which serializes an
**XCAF document** (so it can carry the shape hierarchy + colors), *not* a raw
``TopoDS_Shape``. So the flow mirrors ``step_export_v2``'s CAF path:

  1. Tessellate the shape in-place (``BRepMesh_IncrementalMesh``). glTF stores
     triangle meshes, not B-rep — the writer's own doc says *"Triangulation
     data should be precomputed within shapes!"* so we mesh BEFORE writing.
  2. Build a minimal XCAF doc and ``AddShape`` the tessellated shape.
  3. ``RWGltf_CafWriter(TCollection_AsciiString(path), is_binary)`` then
     ``.Perform(doc, file_info_map, progress_range)`` (the 3-arg overload —
     no label filter needed to export the whole doc).

``binary`` selects GLB (True) vs glTF+bin (False); by default it is inferred
from the extension (``.glb`` → binary, anything else → text glTF).

Body is returned unchanged (read-only — only the filesystem is touched).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional

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
    name="gltf_export",
    category="io",
    level="atomic",
    summary="Tessellate the body and write it to a glTF (.gltf + .bin) or GLB "
            "(.glb) file via OCCT's native RWGltf_CafWriter (XCAF-based). GLB "
            "vs glTF is inferred from the extension unless `binary` is set. "
            "Read-only — body is returned unchanged; only the filesystem is "
            "touched.",
    selector_kinds=[],
    history_rules={},
    produces_features=["gltf_artifact"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.gltf_write_failed", "fm.tessellation_failed"],
    cost_hint=0.12,
    post_conditions=[PostCondition(kind="body_present")],
)
class GltfExport(SkillBase):
    class Args(BaseModel):
        path: str = Field(
            min_length=1,
            description="출력 경로 (.glb = 바이너리 GLB, .gltf = JSON+.bin sidecar)",
        )
        binary: Optional[bool] = Field(
            default=None,
            description="True → GLB (binary), False → glTF (JSON + .bin). "
                        "None (default) infers from the path extension: "
                        ".glb → binary, otherwise text glTF.",
        )
        linear_deflection_mm: float = Field(
            default=0.1, gt=0,
            description="Max chord deviation in mm — smaller = finer mesh.",
        )
        angular_deflection_deg: float = Field(
            default=15.0, gt=0,
            description="Per-face angular deflection limit in degrees.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.Message import Message_ProgressRange
        from OCP.RWGltf import RWGltf_CafWriter
        from OCP.TColStd import TColStd_IndexedDataMapOfStringString
        from OCP.TCollection import (
            TCollection_AsciiString,
            TCollection_ExtendedString,
        )
        from OCP.TDocStd import TDocStd_Document
        from OCP.XCAFApp import XCAFApp_Application
        from OCP.XCAFDoc import XCAFDoc_DocumentTool

        if body is None:
            raise ValueError("gltf_export: body is None")

        shape = body.wrapped if hasattr(body, "wrapped") else body
        if shape is None:
            raise ValueError("gltf_export: body.wrapped is None")

        out_path = Path(args.path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Infer GLB vs glTF from the extension unless caller forced `binary`.
        is_binary = (
            bool(args.binary)
            if args.binary is not None
            else out_path.suffix.lower() == ".glb"
        )

        # 1. Tessellate in-place — glTF stores triangle meshes, and the writer
        #    requires triangulation to already exist on the shape.
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
            raise RuntimeError("gltf_export: BRepMesh_IncrementalMesh failed")

        # 2. Minimal XCAF doc carrying the tessellated shape.
        app = XCAFApp_Application.GetApplication_s()
        doc = TDocStd_Document(TCollection_ExtendedString("gltf-XCAF"))
        app.InitDocument(doc)
        shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
        shape_tool.AddShape(shape, False, True)

        # 3. Write. The 3-arg Perform overload exports the whole document
        #    (no label filter). file_info is an (optional) key/value metadata
        #    map embedded in the glTF "asset.extras".
        writer = RWGltf_CafWriter(TCollection_AsciiString(str(out_path)), is_binary)
        file_info = TColStd_IndexedDataMapOfStringString()
        ok = writer.Perform(doc, file_info, Message_ProgressRange())
        if not ok:
            raise RuntimeError("gltf_export: RWGltf_CafWriter.Perform returned False")
        if not out_path.exists():
            raise RuntimeError(f"gltf_export: file not produced → {out_path}")

        # glTF (text) mode also writes a `.bin` buffer sidecar next to the JSON.
        bin_sidecar = out_path.with_suffix(".bin")
        sidecar_path = (
            str(bin_sidecar) if (not is_binary and bin_sidecar.exists()) else None
        )

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "written_path": str(out_path),
                "binary": bool(is_binary),
                "format": "glb" if is_binary else "gltf",
                "bin_sidecar_path": sidecar_path,
                "triangle_count": int(_count_triangles(shape)),
                "linear_deflection_mm": float(args.linear_deflection_mm),
                "angular_deflection_deg": float(args.angular_deflection_deg),
                "file_size_bytes": int(out_path.stat().st_size),
            },
        )
