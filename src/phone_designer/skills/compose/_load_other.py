"""subtract / union 의 'other' body 로드 헬퍼.

other 의 출처 3가지:
  step_path: STEP 파일
  fixture_part: 합성 fixture 의 부품 (assembly 의 1 부품)
  primitive: 인라인 box/cylinder/sphere
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class StepPathOther(BaseModel):
    """STEP 파일 1개를 other body 로."""
    kind: Literal["step_path"] = "step_path"
    path: str = Field(min_length=1)


class FixturePartOther(BaseModel):
    """XDE assembly 의 특정 부품 1개."""
    kind: Literal["fixture_part"] = "fixture_part"
    assembly_path: str = Field(min_length=1)
    part_name: str


class PrimitiveBoxOther(BaseModel):
    """인라인 Box other (가장 흔한 케이스 — 측면 cutout 의 도구 등)."""
    kind: Literal["primitive_box"] = "primitive_box"
    length_mm: float = Field(gt=0)
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)


class PrimitiveCylinderOther(BaseModel):
    """인라인 Cylinder other."""
    kind: Literal["primitive_cylinder"] = "primitive_cylinder"
    diameter_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    axis: Literal["+X", "-X", "+Y", "-Y", "+Z", "-Z"] = "+Z"


OtherBodySpec = Annotated[
    Union[
        StepPathOther,
        FixturePartOther,
        PrimitiveBoxOther,
        PrimitiveCylinderOther,
    ],
    Field(discriminator="kind"),
]


def load_other_shape(spec: OtherBodySpec):
    """OtherBodySpec → OCCT TopoDS_Shape."""
    from build123d import Align, Box, Cylinder, Axis, Location

    if spec.kind == "step_path":
        from OCP.STEPControl import STEPControl_Reader
        from OCP.IFSelect import IFSelect_RetDone
        p = Path(spec.path)
        if not p.exists():
            raise FileNotFoundError(f"STEP not found: {p}")
        reader = STEPControl_Reader()
        status = reader.ReadFile(str(p))
        if status != IFSelect_RetDone:
            raise RuntimeError(f"STEP parse failed: {p}")
        reader.TransferRoots()
        return reader.OneShape()

    if spec.kind == "fixture_part":
        from phone_designer.reference.step_reader import read_xde_step
        parts = read_xde_step(spec.assembly_path, load_shapes=True)
        for p in parts:
            if p.name == spec.part_name:
                return p.shape
        raise ValueError(
            f"부품 '{spec.part_name}' 없음. 가용: {[p.name for p in parts]}"
        )

    if spec.kind == "primitive_box":
        b = Box(spec.length_mm, spec.width_mm, spec.height_mm,
                align=(Align.CENTER, Align.CENTER, Align.CENTER))
        b.position = spec.position
        return b.wrapped

    if spec.kind == "primitive_cylinder":
        c = Cylinder(radius=spec.diameter_mm / 2, height=spec.height_mm,
                      align=(Align.CENTER, Align.CENTER, Align.MIN))
        if spec.axis in ("+X", "-X"):
            c = c.rotate(Axis.Y, 90 if spec.axis == "+X" else -90)
        elif spec.axis in ("+Y", "-Y"):
            c = c.rotate(Axis.X, -90 if spec.axis == "+Y" else 90)
        elif spec.axis == "-Z":
            c = c.rotate(Axis.X, 180)
        c.position = spec.position
        return c.wrapped

    raise NotImplementedError(f"OtherBodySpec.kind={spec.kind!r}")
