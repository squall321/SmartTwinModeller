"""Component 모델 — Pydantic.

[[lat.md/components#모델]] 의 구현.

3가지 source:
  OEM_CAD       — Phase 3 step_reader 가 OEM CAD 어셈블리에서 자동 추출
  CATALOG       — 사람이 작성한 일반화 부품 yaml
  USER_DEFINED  — UI 에서 사용자가 만든 customized 부품
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


class ComponentSource(str, Enum):
    OEM_CAD      = "OEM_CAD"
    CATALOG      = "CATALOG"
    USER_DEFINED = "USER_DEFINED"


class BoundingBox(BaseModel):
    """Component 의 점유 공간 (component-local frame, mm)."""

    length: float = Field(gt=0, description="X 축 (큰 변)")
    width: float = Field(gt=0, description="Y 축")
    thickness: float = Field(gt=0, description="Z 축")
    # 원형 부품 (display, battery)의 경우 length=width=diameter
    is_circular: bool = False

    @property
    def diameter(self) -> float:
        """원형일 때 외경 = max(L, W)."""
        return max(self.length, self.width)


class Pose(BaseModel):
    """Component 의 housing-local 위치 + 회전 (XYZ + Euler deg)."""

    x_mm: float = 0.0
    y_mm: float = 0.0
    z_mm: float = 0.0
    rx_deg: float = 0.0
    ry_deg: float = 0.0
    rz_deg: float = 0.0


class ClearanceSpec(BaseModel):
    """주변 최소 간극."""

    side_mm: float = Field(default=0.3, ge=0)
    back_mm: float = Field(default=0.5, ge=0)
    top_mm: float = Field(default=0.3, ge=0)
    thermal_zone_mm: float = Field(default=0.0, ge=0,
                                    description="발열 영역 (>0 이면 방열 공간 확보)")


class ScrewBossMount(BaseModel):
    kind: Literal["screw_boss"] = "screw_boss"
    screw_diameter_mm: float = Field(gt=0)
    n_screws: int = Field(ge=1, le=20)


class AdhesivePerimeterMount(BaseModel):
    kind: Literal["adhesive_perimeter"] = "adhesive_perimeter"
    width_mm: float = Field(gt=0, description="접착 영역 둘레 폭")
    roughness_ra_um: float | None = None
    flatness_mm: float | None = None


class SnapFitMount(BaseModel):
    kind: Literal["snap_fit"] = "snap_fit"
    n_hooks: int = Field(ge=2, le=20)


class PressFitMount(BaseModel):
    kind: Literal["press_fit"] = "press_fit"
    interference_mm: float = Field(default=0.05, ge=0, le=0.5)


MountInterface = Annotated[
    Union[ScrewBossMount, AdhesivePerimeterMount, SnapFitMount, PressFitMount],
    Field(discriminator="kind"),
]


class Port(BaseModel):
    """Component 의 외부 노출 포트 (USB-C insert, 카메라 lens window, 마이크 grille)."""

    name: str
    pose_local: Pose = Field(default_factory=Pose,
                              description="component-local 좌표")
    requires_housing_window: bool = False
    requires_housing_cutout: bool = False
    window_shape: dict[str, Any] | None = Field(
        default=None,
        description="housing 외부에 만들 window 모양. "
                    "예: {'kind': 'circle', 'diameter': 34.0} or "
                    "{'kind': 'rounded_rect', 'length': 138, 'width': 66, 'corner_r': 8}",
    )


class Component(BaseModel):
    """1 부품 — bbox + pose + mount + clearance + ports."""

    name: str
    category: str = "unknown"
    bbox: BoundingBox
    pose: Pose = Field(default_factory=Pose,
                        description="housing-local 좌표 (사용자가 배치 시 갱신)")
    source: ComponentSource = ComponentSource.CATALOG
    mount_interface: MountInterface | None = None
    clearance: ClearanceSpec = Field(default_factory=ClearanceSpec)
    ports: list[Port] = Field(default_factory=list)
    process_constraints: dict[str, Any] = Field(default_factory=dict)
    raw_step_path: str | None = Field(
        default=None,
        description="OEM_CAD 인 경우 원본 STEP 파일 (확장 시 import_step 으로 인서트)",
    )
    description: str | None = None

    def world_bbox_center(self) -> tuple[float, float, float]:
        """현재 pose 기준 bbox 중심 (단순 — rotation 미적용 v0)."""
        return (self.pose.x_mm, self.pose.y_mm, self.pose.z_mm)
