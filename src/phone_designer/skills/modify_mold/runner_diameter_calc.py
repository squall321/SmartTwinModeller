"""runner_diameter_calc — atomic, **calculation only** (no geometry change).

Recommend a hot/cold-runner diameter for an injection-molded part using the
classical empirical formula (after DuPont / Rosato):

    D_runner_mm = 0.2654 * sqrt(mass_g) * (L_mm) ** 0.148

where ``mass_g`` = part_volume_mm3 * density_g_per_cm3 / 1000 and
``L_mm`` = flow_length_mm. The material plugs in via density so heavier or
denser polymers get larger runners.

This skill makes no geometric change to the body — it just reports the
recommendation. The body is returned unchanged (post_condition body_present).

extras:
    runner_recommendation = {
        "diameter_mm":      float,
        "L_over_D":         float,
        "mass_g":           float,
        "flow_length_mm":   float,
        "material":         str,
        "density_g_per_cm3": float,
    }
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


# Typical polymer densities (g/cm3). Source: matweb generic grades.
_MATERIAL_DENSITY_G_PER_CM3 = {
    "abs":  1.05,
    "pa":   1.14,
    "pa6":  1.14,
    "pa66": 1.15,
    "pc":   1.20,
    "pp":   0.91,
    "pe":   0.95,
    "ps":   1.05,
    "pmma": 1.18,
    "pom":  1.41,
    "pet":  1.38,
    "pbt":  1.31,
}


def _density_for(material: str) -> float:
    key = material.strip().lower()
    return _MATERIAL_DENSITY_G_PER_CM3.get(key, 1.05)   # ABS default


@skill(
    name="runner_diameter_calc",
    category="modify/mold",
    level="atomic",
    summary="Recommend an injection-molding runner diameter via the empirical "
            "D = 0.2654 * sqrt(mass_g) * L_mm^0.148 rule. Pure calculation — "
            "body is unchanged. Result in extras['runner_recommendation'].",
    selector_kinds=[],
    history_rules={},
    produces_features=["runner_recommendation"],
    preserves=["body_topology"],
    manufacturing={
        "injection_mold_pa": {"extras": {"runner_aware": True}},
    },
    failure_modes=["fm.invalid_volume", "fm.invalid_flow_length"],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class RunnerDiameterCalc(SkillBase):
    class Args(BaseModel):
        part_volume_mm3: float = Field(..., gt=0.0)
        flow_length_mm:  float = Field(..., gt=0.0)
        material: str = Field(default="abs")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        density = _density_for(args.material)
        # mass_g = volume_mm3 * (g/cm3) / 1000 (mm3 → cm3)
        mass_g = args.part_volume_mm3 * density / 1000.0
        if mass_g <= 0.0:
            raise RuntimeError(
                "runner_diameter_calc: computed mass is non-positive"
            )
        L = args.flow_length_mm
        diameter_mm = 0.2654 * math.sqrt(mass_g) * (L ** 0.148)
        l_over_d = L / diameter_mm if diameter_mm > 0.0 else 0.0

        rec = {
            "diameter_mm":       round(diameter_mm, 4),
            "L_over_D":          round(l_over_d, 4),
            "mass_g":            round(mass_g, 4),
            "flow_length_mm":    round(L, 4),
            "material":          args.material,
            "density_g_per_cm3": density,
        }

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"runner_recommendation": rec},
        )
