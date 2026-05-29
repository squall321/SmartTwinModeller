"""TopologyAnalyzer — STEP shape 의 face/edge → FeatureCatalog.

[[lat.md/reference#topology-분석-→-featurecatalog]] 의 본격 구현.

Phase 3 v1 의 검출 대상:
  - Fillet         : Toroidal/Cylindrical face + 양쪽 인접 face 가 planar
  - Chamfer        : Planar face 가 두 평면 사이의 좁은 띠 (각도 < 30°)
  - Hole           : 원기둥 (cylindrical face) — bore. axis + diameter
  - Pocket         : 평면 base + 옆 face (planar/cylindrical) 가 음의 depth
  - Plateau        : 평면 base + 옆 face 가 양의 depth (camera bump 등)

Phase 3 v2 (P1 backlog):
  - polynomial pocket (BSpline 곡면)
  - swept feature
  - feature 의 'belong to' 관계 (예: 한 boss 가 여러 face)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GCPnts import GCPnts_AbscissaPoint
from OCP.GeomAbs import (
    GeomAbs_Plane,
    GeomAbs_Cylinder,
    GeomAbs_Cone,
    GeomAbs_Sphere,
    GeomAbs_Torus,
    GeomAbs_BSplineSurface,
    GeomAbs_BezierSurface,
)
from OCP.GProp import GProp_GProps

# 우리 framework 의 unique face/edge enumerator
from phone_designer.skills._resolvers import _all_faces, _all_edges


# ──────────────────────────────────────────────────────────────────────────────
# Feature dataclasses

SURFACE_TYPE_NAMES = {
    GeomAbs_Plane: "plane",
    GeomAbs_Cylinder: "cylinder",
    GeomAbs_Cone: "cone",
    GeomAbs_Sphere: "sphere",
    GeomAbs_Torus: "torus",
    GeomAbs_BSplineSurface: "bspline",
    GeomAbs_BezierSurface: "bezier",
}


@dataclass
class FaceInfo:
    """1 face 의 측정값."""
    surface_type: str           # "plane" | "cylinder" | "torus" | ...
    area: float
    center: tuple[float, float, float]
    normal_at_center: Optional[tuple[float, float, float]] = None  # planar 만
    cylinder_axis: Optional[tuple[float, float, float]] = None     # cylinder/cone 만
    cylinder_radius: Optional[float] = None
    torus_minor_radius: Optional[float] = None
    torus_major_radius: Optional[float] = None


@dataclass
class FilletFeature:
    """Toroidal/cylindrical fillet face."""
    face_index: int
    radius_mm: float                # toroidal minor R or cylinder R
    center: tuple[float, float, float]
    surface_type: str               # "torus" or "cylinder"


@dataclass
class ChamferFeature:
    """좁은 띠 planar face — 평면 두 개 사이를 잇는 chamfer 추정."""
    face_index: int
    width_mm: float
    center: tuple[float, float, float]
    angle_deg: float                # 인접 평면과의 각도


@dataclass
class HoleFeature:
    """원기둥 face — bore / through-hole / blind hole 후보."""
    face_index: int
    diameter_mm: float
    axis: tuple[float, float, float]
    axis_origin: tuple[float, float, float]
    depth_mm: Optional[float] = None    # cylinder face 의 z extent (계산 가능 시)


@dataclass
class PocketFeature:
    """평면 base + 옆 face — 음의 depth (housing 안쪽으로 파임)."""
    base_face_index: int
    side_face_indices: list[int]
    depth_mm: float
    base_center: tuple[float, float, float]


@dataclass
class PlateauFeature:
    """평면 base + 옆 face — 양의 height (housing 밖으로 돌출)."""
    base_face_index: int
    side_face_indices: list[int]
    height_mm: float
    base_center: tuple[float, float, float]


@dataclass
class FeatureCatalog:
    """1 shape 분석 결과."""
    n_faces: int = 0
    n_edges: int = 0
    surface_type_histogram: dict[str, int] = field(default_factory=dict)
    fillets: list[FilletFeature] = field(default_factory=list)
    chamfers: list[ChamferFeature] = field(default_factory=list)
    holes: list[HoleFeature] = field(default_factory=list)
    pockets: list[PocketFeature] = field(default_factory=list)
    plateaus: list[PlateauFeature] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"FeatureCatalog(n_faces={self.n_faces}, n_edges={self.n_edges}, "
            f"surfaces={dict(self.surface_type_histogram)}, "
            f"fillets={len(self.fillets)}, chamfers={len(self.chamfers)}, "
            f"holes={len(self.holes)}, pockets={len(self.pockets)}, "
            f"plateaus={len(self.plateaus)})"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Analyzer


class TopologyAnalyzer:
    """STEP shape 분석 → FeatureCatalog.

    사용:
        analyzer = TopologyAnalyzer()
        catalog = analyzer.analyze(shape)
        print(catalog.summary())
    """

    def __init__(self, *,
                 cylinder_min_radius_mm: float = 0.5,
                 cylinder_max_radius_mm: float = 50.0,
                 chamfer_max_width_mm: float = 5.0):
        self.cyl_rmin = cylinder_min_radius_mm
        self.cyl_rmax = cylinder_max_radius_mm
        self.chamfer_max_width = chamfer_max_width_mm

    def analyze(self, shape) -> FeatureCatalog:
        catalog = FeatureCatalog()
        faces = _all_faces(shape)
        edges = _all_edges(shape)
        catalog.n_faces = len(faces)
        catalog.n_edges = len(edges)

        # 1. face 별 surface type + 메타 측정
        face_infos: list[FaceInfo] = []
        for face in faces:
            info = self._inspect_face(face)
            face_infos.append(info)
            catalog.surface_type_histogram[info.surface_type] = (
                catalog.surface_type_histogram.get(info.surface_type, 0) + 1
            )

        # 2. Fillet 검출 (toroidal / cylindrical 작은 R)
        for i, info in enumerate(face_infos):
            if info.surface_type == "torus":
                catalog.fillets.append(FilletFeature(
                    face_index=i,
                    radius_mm=info.torus_minor_radius or 0.0,
                    center=info.center,
                    surface_type="torus",
                ))
            elif info.surface_type == "cylinder":
                r = info.cylinder_radius or 0.0
                # 작은 R + 짧은 face → fillet 후보, 큰 R → hole 후보
                if self.cyl_rmin <= r <= 5.0 and info.area < 100.0:
                    catalog.fillets.append(FilletFeature(
                        face_index=i,
                        radius_mm=r,
                        center=info.center,
                        surface_type="cylinder",
                    ))

        # 3. Hole 검출 (cylindrical face, 적정 반경)
        for i, info in enumerate(face_infos):
            if (info.surface_type == "cylinder"
                    and info.cylinder_radius is not None
                    and self.cyl_rmin <= info.cylinder_radius <= self.cyl_rmax
                    and info.area >= 5.0):  # fillet 보다 큰 면적
                catalog.holes.append(HoleFeature(
                    face_index=i,
                    diameter_mm=info.cylinder_radius * 2,
                    axis=info.cylinder_axis or (0, 0, 1),
                    axis_origin=info.center,
                    depth_mm=None,  # TODO: u/v param 으로 z extent 계산
                ))

        # 4. Chamfer 검출 (좁은 planar 띠) — Phase 3 v1 stub
        #    인접 face 의 angle 비교가 필요한데 그건 Phase 3 v2 에서.
        #    여기서는 area / longest_edge 비율로 휴리스틱.
        for i, info in enumerate(face_infos):
            if info.surface_type == "plane" and info.area < self.chamfer_max_width ** 2 * 4:
                # area 가 작은 평면 — chamfer 후보. width = sqrt(area) approx
                approx_width = (info.area) ** 0.5
                if approx_width <= self.chamfer_max_width:
                    # 인접 평면과 각도 추정 — Phase 3 v2
                    catalog.chamfers.append(ChamferFeature(
                        face_index=i,
                        width_mm=approx_width,
                        center=info.center,
                        angle_deg=45.0,  # 기본 가정
                    ))

        # 5. Pocket / Plateau — Phase 3 v2 (face cluster + base-side 관계)
        # TODO: connected face cluster + base plane 검출

        return catalog

    def _inspect_face(self, face) -> FaceInfo:
        surf = BRepAdaptor_Surface(face)
        stype_id = surf.GetType()
        stype = SURFACE_TYPE_NAMES.get(stype_id, "other")

        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        area = props.Mass()
        c = props.CentreOfMass()
        center = (round(c.X(), 3), round(c.Y(), 3), round(c.Z(), 3))

        info = FaceInfo(surface_type=stype, area=round(area, 3), center=center)

        if stype_id == GeomAbs_Plane:
            pl = surf.Plane()
            n = pl.Axis().Direction()
            info.normal_at_center = (round(n.X(), 3), round(n.Y(), 3), round(n.Z(), 3))
        elif stype_id == GeomAbs_Cylinder:
            cyl = surf.Cylinder()
            axis = cyl.Axis().Direction()
            info.cylinder_axis = (round(axis.X(), 3), round(axis.Y(), 3), round(axis.Z(), 3))
            info.cylinder_radius = round(cyl.Radius(), 3)
        elif stype_id == GeomAbs_Torus:
            tor = surf.Torus()
            info.torus_minor_radius = round(tor.MinorRadius(), 3)
            info.torus_major_radius = round(tor.MajorRadius(), 3)
            axis = tor.Axis().Direction()
            info.cylinder_axis = (round(axis.X(), 3), round(axis.Y(), 3), round(axis.Z(), 3))

        return info
