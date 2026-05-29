"""
Synthetic Galaxy-Watch-style assembly fixture generator.

Produces:
  fixtures/simple_watch.step                 -- XDE assembly w/ named parts
  fixtures/simple_watch_housing_only.step    -- housing only (외피 reproduction 입력)
  fixtures/simple_watch_components/<name>.step -- each part separately

All STEP files are AP242. Part names are preserved via XDE labels so
src/phone_designer/reference/step_reader.py can extract them.

Dimensions: simplified Galaxy Watch (~44mm OD, ~11mm thick).
Features intentionally include: outer corner fillet, top edge fillet,
display step-down pocket, crown shaft hole, lug pin holes.
This exercises the §5 STEP topology analyzer and §5.6 reverse-engineer planner.

Usage:
    python fixtures/make_simple_watch.py
        [--out-dir fixtures]
        [--housing-only]

Requires build123d + cadquery-ocp (i.e. OCP). Run after `setup.ps1`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from build123d import (
    Align,
    Axis,
    Box,
    chamfer,
    Cylinder,
    fillet,
    Location,
    Part,
    Sphere,
)

# Align enum 사용 (Rev 6 점검 수정: 0.5 같은 invalid 값 제거)
_BOT = (Align.CENTER, Align.CENTER, Align.MIN)     # XY center, Z=0 부터 위로
_CTR = (Align.CENTER, Align.CENTER, Align.CENTER)  # 전부 center 정렬

# OCP for XDE assembly STEP write
from OCP.TDocStd import TDocStd_Document
from OCP.TCollection import TCollection_ExtendedString
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs
from OCP.IFSelect import IFSelect_RetDone
from OCP.TDataStd import TDataStd_Name
from OCP.Interface import Interface_Static


# Galaxy-Watch-ish dimensions (mm)
HOUSING_OD = 44.0
HOUSING_INNER_OD = 41.0   # 내부 cavity 직경
HOUSING_HEIGHT = 11.0
HOUSING_BOTTOM_THK = 1.5
HOUSING_DOME_RISE = 1.5    # top dome 의 sag
HOUSING_OUTER_FILLET = 2.0 # 옆+위 모서리 fillet
HOUSING_BOTTOM_CHAMFER = 0.5

DISPLAY_OD = 33.0
DISPLAY_THK = 2.0
DISPLAY_POCKET_DEPTH = 0.5   # 외피 top 에서 step-down

BATTERY_OD = 30.0
BATTERY_THK = 4.0

CROWN_OD = 4.0
CROWN_LEN = 4.5            # 측면 돌출 길이
CROWN_SHAFT_HOLE_D = 2.5
CROWN_ANGLE_DEG = 90.0     # 우측 (3시 방향)

LUG_LENGTH = 12.0
LUG_WIDTH = 8.0
LUG_THK = 3.0
LUG_OFFSET_FROM_HOUSING = HOUSING_OD / 2 - 1.0   # 약간 housing 안쪽으로
LUG_PIN_HOLE_D = 1.5

# ---------------------------------------------------------------------------

def make_housing() -> Part:
    """원형 disc + 상부 dome + 내부 cavity + 디스플레이 step-down + 크라운 hole + 측면 lug pad."""
    od = HOUSING_OD
    od_in = HOUSING_INNER_OD
    h = HOUSING_HEIGHT
    bot = HOUSING_BOTTOM_THK
    dome = HOUSING_DOME_RISE

    # 1. 외피 base cylinder
    outer = Cylinder(radius=od / 2, height=h, align=_BOT)  # bottom at z=0

    # 2. 상부 dome — sphere section 으로 살짝 부풀림
    #    구의 반경을 매우 크게 잡고 top 만 사용
    R = (od / 2) ** 2 / (2 * dome) + dome / 2
    dome_sphere = Sphere(radius=R)
    dome_sphere.position = (0, 0, h - R + dome)
    # outer 의 top 만 dome 으로 잘라내기: 교차 → dome 부피만큼 추가
    top_keep = outer & dome_sphere  # 교차 부분이 새 top
    # Simpler: keep outer as-is, then union dome cap
    # Actually simplest: cut outer top flat, add dome.
    cut_box = Box(od, od, dome, align=_BOT)
    cut_box.position = (0, 0, h - dome)
    outer = outer - cut_box
    # dome cap = sphere section above z = h-dome
    cap_solid = dome_sphere & Box(od * 1.5, od * 1.5, R, align=_BOT).moved(
        Location((0, 0, h - dome))
    )
    outer = outer + cap_solid

    # 3. 내부 cavity (배터리/PCB 공간) — bottom 두께만 남김
    cavity = Cylinder(radius=od_in / 2, height=h - bot, align=_BOT)
    cavity.position = (0, 0, bot)
    outer = outer - cavity

    # 4. 디스플레이 step-down pocket (top 면에 얕은 원형 포켓)
    display_pocket = Cylinder(
        radius=DISPLAY_OD / 2 + 0.2,
        height=DISPLAY_POCKET_DEPTH + 1.0,  # 여유, 위로 잘려나감
        align=_BOT,
    )
    display_pocket.position = (0, 0, h - DISPLAY_POCKET_DEPTH)
    outer = outer - display_pocket

    # 5. 외곽 fillet — 측면 + top edge
    #    cylindrical edge (위 원형) 에 fillet
    try:
        top_circle_edges = [
            e for e in outer.edges()
            if abs(e.center().Z - h) < 1.5 and e.length > 5
        ]
        if top_circle_edges:
            outer = fillet(top_circle_edges, HOUSING_OUTER_FILLET)
    except Exception as exc:  # build123d API 변동 대응
        print(f"  [warn] top fillet 적용 실패: {exc}", file=sys.stderr)

    # 6. 하부 chamfer
    try:
        bottom_edges = [
            e for e in outer.edges()
            if abs(e.center().Z) < 0.1 and e.length > 5
        ]
        if bottom_edges:
            outer = chamfer(bottom_edges, HOUSING_BOTTOM_CHAMFER)
    except Exception as exc:
        print(f"  [warn] bottom chamfer 적용 실패: {exc}", file=sys.stderr)

    # 7. 크라운 샤프트 hole (우측 측면)
    crown_hole = Cylinder(
        radius=CROWN_SHAFT_HOLE_D / 2,
        height=od / 2 + 1.0,
        align=_BOT,
    )
    # X 축 방향으로 회전 후 우측에 배치
    crown_hole = crown_hole.rotate(Axis.Y, 90)  # 축이 +X 방향
    crown_hole.position = (od / 4, 0, h / 2 + 1.0)  # 살짝 위쪽
    outer = outer - crown_hole

    # 8. 측면 lug pad (12시·6시 방향, 두 군데) — housing 외부에 stub 으로 붙임
    for sign in (+1, -1):
        lug_stub = Box(
            length=LUG_WIDTH,
            width=LUG_THK,
            height=LUG_THK,
            align=_CTR,
        )
        lug_stub.position = (0, sign * (od / 2 + LUG_THK / 2 - 0.5), h / 2)
        outer = outer + lug_stub

        # lug pin hole — Y 축 방향
        lug_pin = Cylinder(
            radius=LUG_PIN_HOLE_D / 2,
            height=LUG_THK + 1.0,
            align=_BOT,
        )
        lug_pin = lug_pin.rotate(Axis.X, 90)
        lug_pin.position = (0, sign * (od / 2 + LUG_THK / 2 - 0.5), h / 2)
        outer = outer - lug_pin

    outer.label = "housing"
    return outer


def make_display() -> Part:
    """원형 OLED placeholder. 외피 top 의 step-down 자리에 들어감.
    Display top = housing top, display bottom = housing top - DISPLAY_THK.
    """
    d = Cylinder(radius=DISPLAY_OD / 2, height=DISPLAY_THK, align=_BOT)
    d.position = (0, 0, HOUSING_HEIGHT - DISPLAY_THK)
    d.label = "display"
    return d


def make_battery() -> Part:
    """원형 배터리 placeholder. 외피 내부 cavity 의 바닥에 위치."""
    b = Cylinder(radius=BATTERY_OD / 2, height=BATTERY_THK, align=_BOT)
    b.position = (0, 0, HOUSING_BOTTOM_THK + 0.3)  # 0.3mm 간격
    b.label = "battery"
    return b


def make_crown() -> Part:
    """우측 측면 돌출 크라운."""
    c = Cylinder(radius=CROWN_OD / 2, height=CROWN_LEN, align=_BOT)
    c = c.rotate(Axis.Y, 90)
    c.position = (HOUSING_OD / 2 + CROWN_LEN / 2 - 1.0, 0, HOUSING_HEIGHT / 2 + 1.0)
    c.label = "crown"
    return c


def make_lug_pair() -> Part:
    """양 측면 (12시 / 6시) 스트랩 마운트. 두 개를 한 compound 로."""
    pieces = []
    for sign in (+1, -1):
        lug = Box(LUG_WIDTH, LUG_THK, LUG_LENGTH, align=_CTR)
        lug.position = (
            0,
            sign * (HOUSING_OD / 2 + LUG_THK + 0.5),
            HOUSING_HEIGHT / 2,
        )
        pieces.append(lug)
    # 두 lug 합집합
    combined = pieces[0] + pieces[1]
    combined.label = "lug_pair"
    return combined


# ---------------------------------------------------------------------------
# XDE assembly STEP writer


def write_xde_step(parts: list[tuple[str, Part]], output_path: Path) -> None:
    """build123d Part 들을 XDE label 보존된 STEP 으로 저장."""
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("doc"))
    app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    for name, part in parts:
        shape = part.wrapped  # OCP TopoDS_Shape
        label = shape_tool.AddShape(shape, False)  # False: not assembly compound
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))

    # AP242 — XDE assembly + 네이밍 지원
    Interface_Static.SetCVal_s("write.step.schema", "AP242DIS")
    Interface_Static.SetIVal_s("write.step.assembly", 1)

    writer = STEPCAFControl_Writer()
    writer.Transfer(doc, STEPControl_AsIs)
    status = writer.Write(str(output_path))

    if status != IFSelect_RetDone:
        raise RuntimeError(f"STEP 쓰기 실패: status={status}")


def write_single_step(part: Part, output_path: Path, name: str = None) -> None:
    """단일 Part 만 XDE 로 (네이밍 보존)."""
    write_xde_step([(name or part.label or "part", part)], output_path)


# ---------------------------------------------------------------------------
# Main


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="fixtures", type=Path)
    ap.add_argument("--housing-only", action="store_true",
                    help="외피만 출력 (Phase 2 reproduction 입력용)")
    args = ap.parse_args()

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    comp_dir = out_dir / "simple_watch_components"
    comp_dir.mkdir(exist_ok=True)

    print(">>> Generating parts...")
    housing = make_housing()
    print(f"    housing:  {len(housing.faces())} faces, vol={housing.volume:.1f} mm³")

    if args.housing_only:
        out_path = out_dir / "simple_watch_housing_only.step"
        write_single_step(housing, out_path, name="housing")
        print(f"    -> {out_path}")
        return

    display = make_display()
    battery = make_battery()
    crown = make_crown()
    lugs = make_lug_pair()
    print(f"    display:  {len(display.faces())} faces")
    print(f"    battery:  {len(battery.faces())} faces")
    print(f"    crown:    {len(crown.faces())} faces")
    print(f"    lug_pair: {len(lugs.faces())} faces")

    parts = [
        ("housing", housing),
        ("display", display),
        ("battery", battery),
        ("crown", crown),
        ("lug_pair", lugs),
    ]

    # 1. 전체 어셈블리
    assembly_path = out_dir / "simple_watch.step"
    write_xde_step(parts, assembly_path)
    print(f">>> Assembly:        {assembly_path}")

    # 2. 외피만 (Phase 2 reproduction 의 reference)
    housing_only_path = out_dir / "simple_watch_housing_only.step"
    write_single_step(housing, housing_only_path, name="housing")
    print(f">>> Housing-only:    {housing_only_path}")

    # 3. 부품별 분리 (Phase 6 부품 배치 입력)
    for name, part in parts:
        p = comp_dir / f"{name}.step"
        write_single_step(part, p, name=name)
        print(f">>> Component:       {p}")

    print("\nDone. Run sanity check:")
    print("  python -m phone_designer test --scenario phase0_fixture_make")


if __name__ == "__main__":
    main()
