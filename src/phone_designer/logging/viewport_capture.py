"""PyVista offscreen viewport snapshot — 회사 컴 자동 캡처용.

매 시나리오 step 후 4-view (iso/top/side/front) PNG 저장.

[[lat.md/dev-test.md#viewport-snapshot-자동-캡처]] 참조.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# PyVista import 는 함수 안에서 — 본 모듈 import 만으로 VTK 초기화 안 되도록
ViewName = Literal["iso", "top", "side", "front", "back", "bottom", "exploded"]


@dataclass
class CaptureSpec:
    out_dir: Path
    label: str            # 파일 prefix (e.g., "step_001_box")
    views: list[ViewName]
    window_size: tuple[int, int] = (1920, 1080)


# PyVista camera_position 유효값: xy, xz, yz, yx, zx, zy, iso.
# 의미: xy = looking down +Z (= top view), yx = looking up -Z (= bottom),
#       xz = looking from +Y (= front), zx = looking from -Y (= back),
#       yz = looking from +X (= side+), zy = looking from -X (= side-).
_CAMERA_POSITIONS = {
    "iso":      {"position": "iso"},
    "top":      {"position": "xy"},
    "bottom":   {"position": "yx"},
    "front":    {"position": "xz"},
    "back":     {"position": "zx"},
    "side":     {"position": "yz"},
    "side_neg": {"position": "zy"},
    "exploded": {"position": "iso"},   # exploded view 는 별도 처리 필요, v0 는 iso
}


def capture_polydata(polydata, spec: CaptureSpec) -> list[Path]:
    """4-view PNG 캡처. PolyData 가 미리 만들어져 있어야 함."""
    import pyvista as pv

    spec.out_dir.mkdir(parents=True, exist_ok=True)
    out_paths: list[Path] = []

    for view in spec.views:
        plotter = pv.Plotter(off_screen=True, window_size=list(spec.window_size))
        plotter.add_mesh(polydata, color="lightgray", show_edges=True, edge_color="black")

        cam = _CAMERA_POSITIONS.get(view)
        if cam:
            try:
                plotter.camera_position = cam["position"]
            except Exception:
                # invalid view → iso fallback
                plotter.camera_position = "iso"
        plotter.reset_camera()

        out = spec.out_dir / f"{spec.label}_{view}.png"
        plotter.show(screenshot=str(out), auto_close=True)
        out_paths.append(out)

    return out_paths


def capture_step_file(step_path: Path, spec: CaptureSpec) -> list[Path]:
    """STEP 파일 → PolyData → PNG 캡처. build123d 가 OCCT shape tessellate."""
    import pyvista as pv
    from build123d import import_step

    parts = import_step(str(step_path))
    if not parts:
        raise ValueError(f"STEP 로드 결과 비어있음: {step_path}")
    # parts 가 Compound 인 경우 face 들 합치기
    if hasattr(parts, "wrapped"):
        shape = parts.wrapped
    else:
        shape = parts[0].wrapped if isinstance(parts, list) else parts

    # OCCT shape → triangulation → PolyData 변환
    polydata = _shape_to_polydata(shape)
    return capture_polydata(polydata, spec)


def _shape_to_polydata(shape) -> "pv.PolyData":
    """OCCT TopoDS_Shape → pyvista.PolyData (tessellate)."""
    import numpy as np
    import pyvista as pv
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopLoc import TopLoc_Location
    from OCP.BRep import BRep_Tool
    from OCP.TopoDS import TopoDS

    BRepMesh_IncrementalMesh(shape, 0.1, False, 0.5, True).Perform()

    vertices: list[tuple[float, float, float]] = []
    triangles: list[int] = []
    offset = 0

    it = TopExp_Explorer(shape, TopAbs_FACE)
    while it.More():
        # TopExp_Explorer 는 TopoDS_Shape 반환 — Face 캐스트 필요.
        face = TopoDS.Face_s(it.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None:
            it.Next()
            continue
        trsf = loc.Transformation()
        for i in range(1, tri.NbNodes() + 1):
            p = tri.Node(i).Transformed(trsf)
            vertices.append((p.X(), p.Y(), p.Z()))
        for i in range(1, tri.NbTriangles() + 1):
            t = tri.Triangle(i)
            n1, n2, n3 = t.Get()
            triangles.extend([3, offset + n1 - 1, offset + n2 - 1, offset + n3 - 1])
        offset += tri.NbNodes()
        it.Next()

    if not vertices:
        raise ValueError("Tessellation 결과 vertex 가 비어있음")

    return pv.PolyData(np.array(vertices), np.array(triangles))
