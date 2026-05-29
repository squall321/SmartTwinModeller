"""Component 간 OBB 충돌 검사 (trimesh.collision 사용).

Phase 6 v1: bbox (회전 미적용) + clearance 의 AABB-based broad-phase + trimesh
OBB intersection narrow-phase. 정밀 충돌은 v2.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from phone_designer.components.model import Component


@dataclass
class CollisionReport:
    has_any: bool = False
    pairs: list[tuple[str, str]] = field(default_factory=list)
    clearance_violations: list[tuple[str, str, float]] = field(default_factory=list)


def _aabb(c: Component, expand_clearance: bool = False) -> tuple[float, float, float, float, float, float]:
    """component-local bbox + pose → world AABB (회전 미적용)."""
    cx, cy, cz = c.pose.x_mm, c.pose.y_mm, c.pose.z_mm
    hl = c.bbox.length / 2
    hw = c.bbox.width / 2
    ht = c.bbox.thickness / 2
    if expand_clearance:
        hl += c.clearance.side_mm
        hw += c.clearance.side_mm
        ht += c.clearance.top_mm
    return (cx - hl, cy - hw, cz - ht, cx + hl, cy + hw, cz + ht)


def _aabb_intersects(a, b) -> bool:
    return (a[0] <= b[3] and a[3] >= b[0]
            and a[1] <= b[4] and a[4] >= b[1]
            and a[2] <= b[5] and a[5] >= b[2])


def _aabb_clearance_gap(a, b) -> float:
    """두 AABB 사이 최소 거리 (음수 = 겹침). v1 의 단순 추정."""
    dx = max(0, max(a[0], b[0]) - min(a[3], b[3]))
    dy = max(0, max(a[1], b[1]) - min(a[4], b[4]))
    dz = max(0, max(a[2], b[2]) - min(a[5], b[5]))
    return (dx * dx + dy * dy + dz * dz) ** 0.5 if (dx or dy or dz) else 0.0


def has_collision(a: Component, b: Component) -> bool:
    """단순 AABB 기반 충돌 검사 (회전 미적용)."""
    return _aabb_intersects(_aabb(a), _aabb(b))


def collision_report(components: list[Component]) -> CollisionReport:
    """모든 pair 의 충돌 + clearance 위반 검사."""
    report = CollisionReport()
    n = len(components)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = components[i], components[j]
            aabb_a = _aabb(a)
            aabb_b = _aabb(b)
            if _aabb_intersects(aabb_a, aabb_b):
                report.has_any = True
                report.pairs.append((a.name, b.name))
                continue
            # clearance 위반 — bbox 는 안 겹치지만 clearance 영역 침범
            aabb_ac = _aabb(a, expand_clearance=True)
            aabb_bc = _aabb(b, expand_clearance=True)
            if _aabb_intersects(aabb_ac, aabb_bc):
                gap = _aabb_clearance_gap(aabb_a, aabb_b)
                required = max(a.clearance.side_mm, b.clearance.side_mm)
                if gap < required:
                    report.clearance_violations.append((a.name, b.name, round(gap, 3)))
    return report
