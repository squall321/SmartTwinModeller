"""FeatureCatalog → Plan 초안 자동 생성.

[[lat.md/reference#feature-→-plan-reverse-engineer]] 의 본격 구현.

Phase 3 v1: 외피 (disc/slab) + fillet + hole 까지 자동.
Phase 3 v2 (P1): pocket / plateau / chamfer 의 정확한 sketch + face_selector 매핑.
Phase 4 (P1): polynomial_pocket / variable_radius_fillet 자동 인식.
"""
from __future__ import annotations

from dataclasses import dataclass

from phone_designer.plan.model import Plan, Step
from phone_designer.reference.topology_analyzer import FeatureCatalog


@dataclass
class BBox:
    x_min: float
    y_min: float
    z_min: float
    x_max: float
    y_max: float
    z_max: float

    @property
    def length(self) -> float:
        return self.x_max - self.x_min

    @property
    def width(self) -> float:
        return self.y_max - self.y_min

    @property
    def height(self) -> float:
        return self.z_max - self.z_min

    @property
    def is_circular(self) -> bool:
        """XY 단면이 거의 정사각형이면 원형 disc 가정."""
        return abs(self.length - self.width) < 0.05 * max(self.length, self.width)

    @property
    def diameter(self) -> float:
        return max(self.length, self.width)


def _shape_bbox(shape) -> BBox:
    """Triangulation 의존 (Add_s with True) 대신 AddOptimal_s 로 정확한 OCCT bbox.

    Add_s 는 fillet 후 tessellation 외접 다각형으로 bbox 가 부풀려져 약 8% 오차.
    AddOptimal_s 는 정확한 geometric bbox (느림, 그러나 reverse engineer 의 핵심).
    """
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, bb)
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return BBox(xmin, ymin, zmin, xmax, ymax, zmax)


def feature_to_plan(
    catalog: FeatureCatalog,
    shape,
    *,
    plan_name: str = "auto_reverse_engineered",
) -> Plan:
    """FeatureCatalog + 원본 shape → Plan 초안.

    Strategy:
      1. shape bbox 로 base (disc_with_dome 또는 rounded_slab) 추정
      2. 가장 작은 fillet R 의 max → 외피 corner_r 추정
      3. hole 들 → hole step 들 (axis 방향 변환 포함)
      4. (Phase 3 v2) pocket / plateau / chamfer

    Args:
        catalog: TopologyAnalyzer 결과
        shape: 원본 TopoDS_Shape (bbox 계산용)
        plan_name: 출력 Plan 의 이름

    Returns:
        Plan
    """
    bbox = _shape_bbox(shape)
    steps: list[Step] = []
    sid = 0

    def next_id() -> str:
        nonlocal sid
        sid += 1
        return f"s{sid}"

    # 1. base
    if bbox.is_circular:
        # disc — corner_r 는 가장 작은 fillet 후보로
        corner_r = 0.0
        if catalog.fillets:
            small_fillets = [f.radius_mm for f in catalog.fillets if f.radius_mm < 5.0]
            if small_fillets:
                corner_r = round(min(small_fillets), 2)
        steps.append(Step(
            id=next_id(), skill="disc_with_dome",
            args={
                "diameter_mm": round(bbox.diameter, 2),
                "height_mm": round(bbox.height, 2),
                "dome_rise_mm": 0,   # Phase 3 v1: flat top
                "corner_r_mm": corner_r,
            },
        ))
    else:
        # rectangular slab
        corner_r = 0.5
        if catalog.fillets:
            small_fillets = [f.radius_mm for f in catalog.fillets
                             if f.radius_mm < min(bbox.length, bbox.width) / 4]
            if small_fillets:
                corner_r = round(min(small_fillets), 2)
        steps.append(Step(
            id=next_id(), skill="rounded_slab",
            args={
                "length_mm": round(bbox.length, 2),
                "width_mm": round(bbox.width, 2),
                "height_mm": round(bbox.height, 2),
                "corner_r_mm": corner_r,
            },
        ))

    # 2. holes (cylindrical face 의 합리 반경)
    for h in catalog.holes:
        # axis 방향 분류
        axis = h.axis
        ax_abs = (abs(axis[0]), abs(axis[1]), abs(axis[2]))
        if ax_abs[2] > 0.9:
            direction = "+Z" if axis[2] > 0 else "-Z"
        elif ax_abs[0] > 0.9:
            direction = "+X" if axis[0] > 0 else "-X"
        elif ax_abs[1] > 0.9:
            direction = "+Y" if axis[1] > 0 else "-Y"
        else:
            # 비축 정렬 — 일단 -Z 로 가정
            direction = "-Z"

        steps.append(Step(
            id=next_id(), skill="hole",
            args={
                "position": [round(c, 3) for c in h.axis_origin],
                "diameter_mm": round(h.diameter_mm, 3),
                "depth_mm": None,  # through 가정 — Phase 3 v2 에서 정확히
                "direction": direction,
            },
        ))

    # 3. fillet/chamfer 의 본격 reverse engineer 는 Phase 3 v2.
    #    현 단계는 base 의 corner_r 로만 흡수.

    return Plan(
        schema_version=1,
        plan_name=plan_name,
        description=(
            f"Auto reverse-engineered from FeatureCatalog "
            f"(faces={catalog.n_faces}, fillets={len(catalog.fillets)}, "
            f"holes={len(catalog.holes)})."
        ),
        steps=steps,
    )
