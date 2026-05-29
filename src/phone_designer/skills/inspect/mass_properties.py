"""mass_properties — atomic, read-only.

Compute volume, centroid, inertia tensor, and (optionally) mass of a body
via OCCT ``GProp_GProps`` + ``BRepGProp.VolumeProperties_s``.

OCCT 의 single-density volume property 는 density=1 (그래서 ``Mass()`` 가 곧
부피와 같다). 본 skill 은 부피 (mm³) 를 직접 가져오고, 사용자 입력 density
(g/cm³) 를 곱해서 질량 (g) 도 함께 계산한다.

inertia matrix 는 OCCT 의 1-indexed ``Matrix.Value(i, j)`` 를 0-indexed 3x3
list-of-list 로 변환. OCCT 가 반환하는 텐서는 origin 기준 (= ``CentreOfMass``
좌표계가 아님). 본 단위는 ``mm^5`` (mass=volume_mm3 이므로).

extras schema:
    {
      "volume_mm3": float,
      "centroid": [x, y, z],
      "mass_g": float,                 # density_g_per_cm3 * volume_cm3
      "density_g_per_cm3": float,
      "inertia_matrix": [[Ixx,Ixy,Ixz], [Iyx,Iyy,Iyz], [Izx,Izy,Izz]],
      "inertia_diag": {"Ixx": ..., "Iyy": ..., "Izz": ...,
                        "Ixy": ..., "Ixz": ..., "Iyz": ...}
    }
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


@skill(
    name="mass_properties",
    category="inspect",
    level="atomic",
    summary="Compute volume (mm³), centroid, inertia matrix, and mass (g) of "
            "a body via GProp_GProps. Density input is in g/cm³ "
            "(default 1.0 — mass = volume_cm3). Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["mass_properties"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class MassProperties(SkillBase):
    class Args(BaseModel):
        density_g_per_cm3: float = Field(default=1.0, ge=0.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps

        shape = _occt_shape(body)

        # Volume + centroid + inertia
        volume_mm3 = 0.0
        cx = cy = cz = 0.0
        mat_rows: list[list[float]] = [
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
        ]
        try:
            props = GProp_GProps()
            BRepGProp.VolumeProperties_s(shape, props)
            volume_mm3 = float(props.Mass())
            c = props.CentreOfMass()
            cx, cy, cz = c.X(), c.Y(), c.Z()
            mat = props.MatrixOfInertia()
            # OCCT gp_Mat is 1-indexed; convert to 0-indexed 3x3.
            for i in range(3):
                for j in range(3):
                    mat_rows[i][j] = float(mat.Value(i + 1, j + 1))
        except Exception:
            # On failure, leave zeros — body still returned unchanged.
            pass

        # Mass = density (g/cm³) * volume (cm³). 1 cm³ = 1000 mm³.
        volume_cm3 = volume_mm3 / 1000.0
        mass_g = float(args.density_g_per_cm3) * volume_cm3

        Ixx = mat_rows[0][0]
        Iyy = mat_rows[1][1]
        Izz = mat_rows[2][2]
        Ixy = mat_rows[0][1]
        Ixz = mat_rows[0][2]
        Iyz = mat_rows[1][2]

        extras = {
            "volume_mm3": round(volume_mm3, 6),
            "centroid": [round(cx, 6), round(cy, 6), round(cz, 6)],
            "mass_g": round(mass_g, 6),
            "density_g_per_cm3": float(args.density_g_per_cm3),
            "inertia_matrix": [
                [round(v, 6) for v in row] for row in mat_rows
            ],
            "inertia_diag": {
                "Ixx": round(Ixx, 6),
                "Iyy": round(Iyy, 6),
                "Izz": round(Izz, 6),
                "Ixy": round(Ixy, 6),
                "Ixz": round(Ixz, 6),
                "Iyz": round(Iyz, 6),
            },
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
