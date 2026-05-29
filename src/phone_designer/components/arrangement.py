"""ComponentArrangement — 배치된 Component 들의 collection.

사용자가 UI 또는 plan 에서 부품을 housing-local 좌표에 배치한 결과.
housing_synth_rule 의 입력.
"""
from __future__ import annotations

from typing import Iterable

from pydantic import BaseModel, Field

from phone_designer.components.model import Component, Pose


class ComponentArrangement(BaseModel):
    """배치된 component list + (선택) housing 경계 hint."""

    components: list[Component] = Field(default_factory=list)
    housing_envelope_hint: dict | None = Field(
        default=None,
        description="(선택) 외곽 envelope 가이드 — disc / slab + 기본 dimensions. "
                    "없으면 components 의 union + clearance 로 자동 추정.",
    )

    def add(self, component: Component, *, pose: Pose | None = None) -> None:
        if pose is not None:
            component = component.model_copy(update={"pose": pose})
        self.components.append(component)

    def by_category(self, category: str) -> list[Component]:
        return [c for c in self.components if c.category == category]

    # housing 외부에 부착되는 부품 카테고리 — bbox 추정에서 제외.
    # 사용자가 명시한 housing_envelope_hint 가 우선.
    _EXTERNAL_CATEGORIES = {"crown", "button", "lug"}

    def is_likely_circular(self) -> bool:
        """내부 부품 (외부 제외) 의 대다수가 is_circular 이고 size 분포가 대칭이면 원형 housing."""
        internal = [c for c in self.components
                    if c.category not in self._EXTERNAL_CATEGORIES]
        if not internal:
            return False
        n_circ = sum(1 for c in internal if c.bbox.is_circular)
        return n_circ >= len(internal) * 0.5    # 절반 이상이 원형이면

    def estimate_inner_volume_bbox(
        self, *, exclude_external: bool = True,
    ) -> tuple[float, float, float, float, float, float]:
        """모든 component bbox + clearance 의 합집합 → housing 내부 부피 bbox (mm).

        Args:
            exclude_external: True 면 crown/button/lug 등 housing 외부 부착 제외.
        """
        if not self.components:
            return (0, 0, 0, 0, 0, 0)
        xs_min, ys_min, zs_min = [], [], []
        xs_max, ys_max, zs_max = [], [], []
        for c in self.components:
            if exclude_external and c.category in self._EXTERNAL_CATEGORIES:
                continue
            cx, cy, cz = c.pose.x_mm, c.pose.y_mm, c.pose.z_mm
            hl = c.bbox.length / 2 + c.clearance.side_mm
            hw = c.bbox.width / 2 + c.clearance.side_mm
            ht = c.bbox.thickness / 2 + c.clearance.top_mm
            xs_min.append(cx - hl); xs_max.append(cx + hl)
            ys_min.append(cy - hw); ys_max.append(cy + hw)
            zs_min.append(cz - ht); zs_max.append(cz + ht)
        if not xs_min:
            return self.estimate_inner_volume_bbox(exclude_external=False)
        return (min(xs_min), min(ys_min), min(zs_min),
                max(xs_max), max(ys_max), max(zs_max))

    def estimate_housing_bbox(self, outer_skin_mm: float = 1.5) -> tuple[float, float, float]:
        """외피 외경 추정 — inner_volume + outer_skin. 원형 housing 이면 L=W 강제."""
        # housing_envelope_hint 우선
        if self.housing_envelope_hint:
            h = self.housing_envelope_hint
            if h.get("kind") == "disc":
                d = h.get("diameter", 0)
                t = h.get("height", 0)
                return (d, d, t)
            if h.get("kind") == "slab":
                return (h.get("length", 0), h.get("width", 0), h.get("height", 0))

        xmin, ymin, zmin, xmax, ymax, zmax = self.estimate_inner_volume_bbox()
        L = (xmax - xmin) + 2 * outer_skin_mm
        W = (ymax - ymin) + 2 * outer_skin_mm
        T = (zmax - zmin) + 2 * outer_skin_mm

        # 원형 housing 추정 — L 과 W 중 큰 값으로 통일
        if self.is_likely_circular():
            diameter = max(L, W)
            return (diameter, diameter, T)

        return (L, W, T)
