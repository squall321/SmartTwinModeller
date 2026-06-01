"""modal_frequency_estimate — atomic, read-only.

Quick first-natural-frequency estimate using a thin-plate approximation. For
a simply-supported rectangular plate with side lengths a × b and thickness h:

    f1 ≈ (π / 2) * sqrt(D / (ρ * h)) * (1/a² + 1/b²)

where ``D = E * h³ / (12 * (1 - ν²))`` is the flexural rigidity.

The skill maps the body's bounding box (``bbox`` argument, [[xmin,ymin,zmin],
[xmax,ymax,zmax]]) and computed volume (``body_volume_mm3``) to:

    a = longest in-plane edge
    b = next in-plane edge
    h = volume / (a * b)         # smeared thickness — robust for hollow bodies

Material is taken from the ``material`` arg (same schema as
``material_property_tag``), with sensible structural-steel defaults when
keys are missing.

Result is purely informative — a finite-element solve would refine it by
a factor of ~0.5–2× depending on boundary conditions. The estimate is
meant for design-time sanity checks (e.g., "does this housing resonate in
the audible band?").

extras["f1_hz_estimate"]: float
extras["modal_inputs"]: {a_mm, b_mm, h_mm, E_mpa, rho_kg_m3, poisson}
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="modal_frequency_estimate",
    category="inspect",
    level="atomic",
    summary="Estimate the first natural frequency (Hz) of a body using a "
            "thin-plate simply-supported approximation. Read-only — body "
            "unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["modal_estimate"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.invalid_material", "fm.degenerate_bbox"],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class ModalFrequencyEstimate(SkillBase):
    class Args(BaseModel):
        material: dict[str, float] = Field(
            description="Linear-elastic material dict — keys "
                        "youngs_modulus_mpa, poisson, density_kg_m3.",
        )
        body_volume_mm3: float = Field(gt=0.0)
        bbox: list[list[float]] = Field(
            description="[[xmin,ymin,zmin],[xmax,ymax,zmax]] in mm",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if len(args.bbox) != 2 or any(len(p) != 3 for p in args.bbox):
            raise ValueError(
                "modal_frequency_estimate: bbox must be [[xmin,ymin,zmin],"
                "[xmax,ymax,zmax]]",
            )
        mn = args.bbox[0]
        mx = args.bbox[1]
        sizes_mm = sorted(
            [mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]], reverse=True,
        )
        # Filter degenerate dimensions.
        if any(s <= 0.0 for s in sizes_mm):
            raise ValueError(
                f"modal_frequency_estimate: degenerate bbox sizes={sizes_mm}",
            )

        # Material defaults — structural steel-ish.
        E_mpa = float(args.material.get("youngs_modulus_mpa", 200000.0))
        nu = float(args.material.get("poisson", 0.3))
        rho_kg_m3 = float(args.material.get("density_kg_m3", 7850.0))
        if E_mpa <= 0.0 or rho_kg_m3 <= 0.0 or nu <= -1.0 or nu >= 0.5:
            raise ValueError(
                f"modal_frequency_estimate: invalid material E={E_mpa}, "
                f"rho={rho_kg_m3}, nu={nu}",
            )

        # a = longest in-plane edge, b = next, h = smeared thickness.
        a_mm = sizes_mm[0]
        b_mm = sizes_mm[1]
        h_mm = args.body_volume_mm3 / (a_mm * b_mm)

        # Convert to SI (m, Pa, kg).
        a_m = a_mm * 1e-3
        b_m = b_mm * 1e-3
        h_m = h_mm * 1e-3
        E_pa = E_mpa * 1e6

        if h_m <= 0.0:
            raise ValueError(
                "modal_frequency_estimate: derived plate thickness is 0",
            )

        # Flexural rigidity D = E h³ / (12 (1 - ν²)).
        D = E_pa * (h_m ** 3) / (12.0 * (1.0 - nu * nu))
        # First mode of simply-supported plate:
        #   f11 = (π/2) sqrt(D / (ρ h)) (1/a² + 1/b²)
        denom = rho_kg_m3 * h_m
        if denom <= 0.0:
            raise ValueError(
                "modal_frequency_estimate: ρ h must be > 0",
            )
        f1_hz = (math.pi / 2.0) * math.sqrt(D / denom) * (
            1.0 / (a_m * a_m) + 1.0 / (b_m * b_m)
        )

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "f1_hz_estimate": float(round(f1_hz, 4)),
                "modal_inputs": {
                    "a_mm": round(a_mm, 4),
                    "b_mm": round(b_mm, 4),
                    "h_mm": round(h_mm, 4),
                    "E_mpa": round(E_mpa, 4),
                    "rho_kg_m3": round(rho_kg_m3, 4),
                    "poisson": round(nu, 6),
                    "flexural_rigidity_n_m": round(D, 6),
                },
            },
        )
