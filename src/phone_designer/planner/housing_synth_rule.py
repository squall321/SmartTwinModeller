"""HousingSynthRule v0 — rule-based housing 합성.

[[lat.md/components#합성-v0]] 의 v0. ribbing 은 단순 휴리스틱 (PF-7 의 v2 spike 후 본격).

알고리즘:
  1. ComponentArrangement → InnerVolume bbox
  2. 외피 base: 원형이면 disc_with_dome, 사각이면 rounded_slab
  3. 각 component 의 port:
       requires_housing_window: extrude_through 또는 extrude_pocket
       requires_housing_cutout: extrude_through 측면
  4. mount_interface 별:
       adhesive_perimeter → mounting_pad
       screw_boss        → boss_with_hole array
       snap_fit          → snap_hook
       press_fit         → (handled by component placement, no housing change)
  5. (선택) ribbing — PF-7 결과 의존 (v0 는 skip)
  6. final_fillet_all_sharp_edges
"""
from __future__ import annotations

from dataclasses import dataclass

from phone_designer.components.arrangement import ComponentArrangement
from phone_designer.components.model import Component, Port
from phone_designer.plan.model import Plan, Step


@dataclass
class SynthConfig:
    outer_skin_mm: float = 4.0      # 실제 워치 bezel ≈ 4-5mm
    corner_r_mm: float = 2.0        # 외피 옆 모서리 fillet
    dome_rise_mm: float = 0.0       # v0 는 flat (face_named=top 호환)
    enable_ribbing: bool = False    # PF-7 결과 후 v2
    enable_internal_cavity: bool = True
    cavity_wall_mm: float = 1.0     # cavity 둘레 wall 두께
    cavity_floor_mm: float = 1.0    # cavity bottom 두께
    top_edge_r_mm: float = 0.5      # disc/slab 의 top rim fillet (sharp → round)
    final_fillet_r_mm: float = 0.3
    plan_name: str = "auto_housing_synth"


class HousingSynthRule:
    """ComponentArrangement → Plan (외피 + cutout + mount + 마감)."""

    def __init__(self, config: SynthConfig | None = None):
        self.config = config or SynthConfig()

    def synthesize(self, arrangement: ComponentArrangement) -> Plan:
        steps: list[Step] = []
        sid = 0

        def next_id() -> str:
            nonlocal sid
            sid += 1
            return f"s{sid}"

        # 1. 외피 base
        L, W, T = arrangement.estimate_housing_bbox(self.config.outer_skin_mm)
        if abs(L - W) < 0.05 * max(L, W):
            # 원형 — disc_with_dome
            steps.append(Step(
                id=next_id(), skill="disc_with_dome",
                args={
                    "diameter_mm": round(max(L, W), 2),
                    "height_mm": round(T, 2),
                    "dome_rise_mm": self.config.dome_rise_mm,
                    "corner_r_mm": self.config.corner_r_mm,
                },
                notes="외피 base (자동 합성)",
            ))
        else:
            steps.append(Step(
                id=next_id(), skill="rounded_slab",
                args={
                    "length_mm": round(L, 2),
                    "width_mm": round(W, 2),
                    "height_mm": round(T, 2),
                    "corner_r_mm": self.config.corner_r_mm,
                },
                notes="외피 base (자동 합성)",
            ))

        # 1.5 top rim fillet — disc_with_dome/rounded_slab 의 top 평면 face 와
        #     측면 사이의 sharp edge 를 round. final_fillet 이 끝에서 한 번 더 돌지만
        #     그 시점이면 이미 mounting_pad/pocket 들이 top 을 분할해서 원래의
        #     "외곽 top rim" edge 를 정확히 잡지 못한다. 그래서 base 직후에 박는다.
        if self.config.top_edge_r_mm > 0:
            steps.append(Step(
                id=next_id(), skill="fillet_edges_by_predicate",
                args={
                    "selector": {
                        "kind": "edges_on_face",
                        "face": {"kind": "face_named", "name": "top"},
                    },
                    "radius_mm": self.config.top_edge_r_mm,
                },
                notes="top rim fillet (외곽 sharp edge round)",
            ))

        # 2. internal cavity — bottom face 에 안쪽으로 pocket (부품 들어갈 공간)
        #   top-side pocket depth 를 미리 스캔해서 cavity 의 ceiling clearance 를 확보.
        max_top_pocket_depth = self._max_top_pocket_depth(arrangement)
        if self.config.enable_internal_cavity:
            steps.append(self._step_for_cavity(
                L, W, T,
                outer_skin_mm=self.config.outer_skin_mm,
                corner_r_mm=self.config.corner_r_mm,
                max_top_pocket_depth=max_top_pocket_depth,
                next_id=next_id,
            ))

        # 3. 각 component 의 port + mount 처리
        for comp in arrangement.components:
            steps.extend(self._steps_for_component(comp, next_id))

        # 4. final fillet 마감
        steps.append(Step(
            id=next_id(), skill="final_fillet_all_sharp_edges",
            args={"radius_mm": self.config.final_fillet_r_mm},
            notes="외피 sharp edge 일괄 마감",
        ))

        return Plan(
            schema_version=1,
            plan_name=self.config.plan_name,
            description=(
                f"Auto-synthesized from {len(arrangement.components)} components. "
                f"Outer bbox {L:.1f}×{W:.1f}×{T:.1f}mm."
            ),
            steps=steps,
        )

    def _step_for_cavity(
        self, L: float, W: float, T: float, *,
        outer_skin_mm: float, corner_r_mm: float,
        max_top_pocket_depth: float, next_id,
    ) -> Step:
        """bottom face 에 안쪽으로 pocket — 부품 들어갈 internal cavity.

        cavity sketch 는 bottom 평면 face 안에 들어가야. bottom planar face 의 size:
          - circular base D=disc_D 의 corner_r 적용 → 평면 부분 D = disc_D - 2*corner_r
          - rectangle base 도 비슷
        cavity 의 wall = outer_skin (외피 두께) + cavity_wall (보강) + corner_r (안전)

        depth 결정:
          extrude_pocket 은 bottom face 에 sketch 를 놓고 +Z 로 자른다 →
          cavity 는 z=0..depth 를 차지. 따라서:
            top_clearance = max(deepest top-side pocket, corner_r) + cavity_wall
                            (top 쪽 feature floor 와 cavity ceiling 사이 두께)
            depth = T - cavity_floor_mm - top_clearance
          NOTE: extrude_pocket 의 한계상 cavity 바닥은 bottom face 에 그대로 열린다.
          (워치류는 보통 back cover 가 별도 부품이므로 v0 에서는 open-bottom 으로 둔다.
          cavity_floor_mm 는 historical 이지만 top_clearance 로 의미가 옮겨졌다.)
        """
        is_circular = abs(L - W) < 0.05 * max(L, W)
        wall = outer_skin_mm + self.config.cavity_wall_mm + corner_r_mm
        floor = self.config.cavity_floor_mm
        # top 쪽 feature(pocket/window from top)와 cavity 사이 최소 두께.
        top_clearance = max(max_top_pocket_depth, corner_r_mm) + self.config.cavity_wall_mm
        depth = round(max(T - floor - top_clearance, 0.1), 3)
        if is_circular:
            sketch_d = max(max(L, W) - 2 * wall, 1.0)
            sketch = {"kind": "circle", "diameter_mm": round(sketch_d, 3)}
        else:
            sketch = {
                "kind": "rectangle",
                "length_mm": round(max(L - 2 * wall, 1.0), 3),
                "width_mm": round(max(W - 2 * wall, 1.0), 3),
            }
        return Step(
            id=next_id(), skill="extrude_pocket",
            args={
                "face_selector": {"kind": "face_named", "name": "bottom"},
                "sketch": sketch,
                "depth_mm": depth,
            },
            notes=(f"internal cavity (wall {wall:.1f}mm including skin+corner_r, "
                    f"floor {floor}mm, top_clearance {top_clearance:.2f}mm, "
                    f"depth {depth}mm)"),
        )

    def _max_top_pocket_depth(self, arrangement: ComponentArrangement) -> float:
        """top face 에 파내려가는 pocket 의 최대 깊이 (mm).

        `_step_for_window` 와 동일한 규칙으로 계산해서 cavity 가
        ceiling clearance 를 충분히 남기도록 한다. through-window 와
        mounting_pad (위로 추가됨) 는 housing 내부로 들어가지 않아 0.
        """
        max_d = 0.0
        for comp in arrangement.components:
            is_pocket_mount = (
                comp.mount_interface is not None
                and getattr(comp.mount_interface, "kind", "") == "adhesive_perimeter"
            )
            if not is_pocket_mount:
                continue
            for port in comp.ports:
                if not port.requires_housing_window:
                    continue
                ws = port.window_shape or {}
                if ws.get("kind") not in ("circle", "rounded_rect", "rectangle"):
                    continue
                # _step_for_window 의 pocket-depth 공식과 동일.
                d = comp.bbox.thickness + comp.clearance.top_mm + 0.5
                if d > max_d:
                    max_d = d
        return max_d

    def _steps_for_component(self, comp: Component, next_id) -> list[Step]:
        """1 component 의 mount + port 를 housing 변형 step 들로.

        순서가 중요:
          (1) mount_interface (pad/boss/snap) — top face plateau 추가
          (2) port (window/cutout) — 그 plateau/face 에 hole.
        반대 순서이면 mount pad 가 window 메움.
        """
        out: list[Step] = []

        # 1. mount_interface 먼저
        if comp.mount_interface is not None:
            out.extend(self._steps_for_mount(comp, next_id))

        # 2. ports — window/cutout
        for port in comp.ports:
            if port.requires_housing_window:
                step = self._step_for_window(comp, port, next_id())
                if step:
                    out.append(step)
            elif port.requires_housing_cutout:
                step = self._step_for_cutout(comp, port, next_id())
                if step:
                    out.append(step)

        return out

    def _step_for_window(self, comp: Component, port: Port, sid: str) -> Step | None:
        """requires_housing_window — pocket 또는 extrude_through.

        결정 규칙:
          - adhesive_perimeter mount 이고 component thickness 가 작으면 → pocket
            (디스플레이/sensor 처럼 housing 안에 들어가는 부품)
          - 그 외 → extrude_through (lens / grille / 직접 외부 노출)
        """
        ws = port.window_shape or {}
        kind = ws.get("kind", "circle")
        if kind == "circle":
            sketch = {"kind": "circle", "diameter_mm": ws.get("diameter", 10.0)}
        elif kind in ("rounded_rect", "rectangle"):
            sketch = {
                "kind": "rectangle",
                "length_mm": ws.get("length", 10.0),
                "width_mm": ws.get("width", 6.0),
            }
        else:
            return None

        is_pocket = (
            comp.mount_interface is not None
            and getattr(comp.mount_interface, "kind", "") == "adhesive_perimeter"
        )

        if is_pocket:
            depth = comp.bbox.thickness + comp.clearance.top_mm + 0.5
            return Step(
                id=sid, skill="extrude_pocket",
                args={
                    "face_selector": {"kind": "face_named", "name": "top"},
                    "sketch": sketch,
                    "depth_mm": round(depth, 3),
                },
                notes=f"{comp.name} 의 {port.name} pocket",
            )
        return Step(
            id=sid, skill="extrude_through",
            args={
                "face_selector": {"kind": "face_named", "name": "top"},
                "sketch": sketch,
            },
            notes=f"{comp.name} 의 {port.name} through window",
        )

    def _step_for_cutout(self, comp: Component, port: Port, sid: str) -> Step | None:
        """requires_housing_cutout — 측면 hole (예: crown shaft exit, USB-C)."""
        ws = port.window_shape or {}
        # 측면 cutout 의 axis 결정 — port pose_local 의 가장 큰 component
        pose_w = (
            comp.pose.x_mm + port.pose_local.x_mm,
            comp.pose.y_mm + port.pose_local.y_mm,
            comp.pose.z_mm + port.pose_local.z_mm,
        )
        # axis 결정: x 가 가장 크면 +X 또는 -X
        ax, ay, az = abs(pose_w[0]), abs(pose_w[1]), abs(pose_w[2])
        if ax > ay and ax > az:
            direction = "+X" if pose_w[0] > 0 else "-X"
        elif ay > ax and ay > az:
            direction = "+Y" if pose_w[1] > 0 else "-Y"
        else:
            direction = "-Z"

        if ws.get("kind") == "circle":
            return Step(
                id=sid, skill="hole",
                args={
                    "position": list(pose_w),
                    "diameter_mm": ws.get("diameter", 2.5),
                    "depth_mm": None,
                    "direction": direction,
                },
                notes=f"{comp.name} 의 {port.name} cutout",
            )
        return None

    def _steps_for_mount(self, comp: Component, next_id) -> list[Step]:
        """mount_interface kind 별 housing 변형."""
        mi = comp.mount_interface
        out: list[Step] = []
        if mi.kind == "adhesive_perimeter":
            # 외주 mounting_pad — component shape (circular vs rectangular) 따라 sketch.
            if comp.bbox.is_circular:
                sketch = {
                    "kind": "circle",
                    "diameter_mm": comp.bbox.diameter + 2 * mi.width_mm,
                }
            else:
                sketch = {
                    "kind": "rectangle",
                    "length_mm": comp.bbox.length + 2 * mi.width_mm,
                    "width_mm": comp.bbox.width + 2 * mi.width_mm,
                }
            out.append(Step(
                id=next_id(), skill="mounting_pad",
                args={
                    "face_selector": {"kind": "face_named", "name": "top"},
                    "sketch": sketch,
                    "height_mm": 0.5,
                    "required_roughness_ra_um": mi.roughness_ra_um,
                    "required_flatness_mm": mi.flatness_mm,
                },
                notes=f"{comp.name} 의 adhesive pad",
            ))
        elif mi.kind == "screw_boss":
            # N 개 boss 를 component bbox 의 corner 에 배치
            n = mi.n_screws
            d = mi.screw_diameter_mm
            hl = comp.bbox.length / 2 - 1.0
            hw = comp.bbox.width / 2 - 1.0
            corners = [
                (comp.pose.x_mm + hl, comp.pose.y_mm + hw, 0.0),
                (comp.pose.x_mm - hl, comp.pose.y_mm + hw, 0.0),
                (comp.pose.x_mm + hl, comp.pose.y_mm - hw, 0.0),
                (comp.pose.x_mm - hl, comp.pose.y_mm - hw, 0.0),
            ][:n]
            for pos in corners:
                out.append(Step(
                    id=next_id(), skill="boss_with_hole",
                    args={
                        "position": list(pos),
                        "direction": "+Z",
                        "boss_diameter_mm": d * 2.0,
                        "boss_height_mm": 3.0,
                        "hole_diameter_mm": d,
                        "hole_depth_mm": 3.5,
                    },
                    notes=f"{comp.name} 의 screw boss",
                ))
        elif mi.kind == "snap_fit":
            # 단순화 — N hook 을 측면 따라
            n = mi.n_hooks
            for i in range(min(n, 4)):
                angle_x = comp.pose.x_mm + (10 if i % 2 == 0 else -10)
                out.append(Step(
                    id=next_id(), skill="snap_hook",
                    args={
                        "base_position": [angle_x, comp.pose.y_mm, comp.pose.z_mm + 5],
                        "cantilever_axis": "-Z",
                    },
                    notes=f"{comp.name} 의 snap hook #{i+1}",
                ))
        # press_fit: housing 변형 없음

        return out
