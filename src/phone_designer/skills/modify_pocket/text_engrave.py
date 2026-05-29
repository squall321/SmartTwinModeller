"""text_engrave — atomic.

지정된 (planar) face 위에 텍스트를 새겨 그 깊이만큼 body 에서 빼낸다 (Cut).
소비자 제품 하우징의 로고 / 라벨 각인 (예: 후면 'SAMSUNG').

OCCT 7.8 의 `StdPrs_BRepFont` + `StdPrs_BRepTextBuilder` 가 백엔드 — 시스템 폰트
(Arial 등) 의 Unicode glyph outline 을 TopoDS_Shape (compound of faces) 로 변환한다.
그 compound 를 face 의 inward normal 따라 prism 으로 extrude 하고 BRepAlgoAPI_Cut.
"""
from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


# Module-level: cache windows-font lookup once.
# Maps lower-case font name (without extension) → absolute path.
_FONT_DIR = r"C:\Windows\Fonts"
_FONT_FALLBACKS: dict[str, str] = {
    "arial":   os.path.join(_FONT_DIR, "arial.ttf"),
    "calibri": os.path.join(_FONT_DIR, "calibri.ttf"),
    "tahoma":  os.path.join(_FONT_DIR, "tahoma.ttf"),
    "verdana": os.path.join(_FONT_DIR, "verdana.ttf"),
    "times":   os.path.join(_FONT_DIR, "times.ttf"),
}
_FONT_BOLD: dict[str, str] = {
    "arial":   os.path.join(_FONT_DIR, "arialbd.ttf"),
    "calibri": os.path.join(_FONT_DIR, "calibrib.ttf"),
    "tahoma":  os.path.join(_FONT_DIR, "tahomabd.ttf"),
    "verdana": os.path.join(_FONT_DIR, "verdanab.ttf"),
    "times":   os.path.join(_FONT_DIR, "timesbd.ttf"),
}
_FONT_ITALIC: dict[str, str] = {
    "arial":   os.path.join(_FONT_DIR, "ariali.ttf"),
    "calibri": os.path.join(_FONT_DIR, "calibrii.ttf"),
    "verdana": os.path.join(_FONT_DIR, "verdanai.ttf"),
    "times":   os.path.join(_FONT_DIR, "timesi.ttf"),
}
_FONT_BOLD_ITALIC: dict[str, str] = {
    "arial":   os.path.join(_FONT_DIR, "arialbi.ttf"),
    "calibri": os.path.join(_FONT_DIR, "calibriz.ttf"),
    "verdana": os.path.join(_FONT_DIR, "verdanaz.ttf"),
    "times":   os.path.join(_FONT_DIR, "timesbi.ttf"),
}


def _resolve_font_path(name: str, bold: bool, italic: bool) -> str | None:
    """font_name + style → Windows TTF path. 못 찾으면 None."""
    key = name.strip().lower()
    table: dict[str, str]
    if bold and italic:
        table = _FONT_BOLD_ITALIC
    elif bold:
        table = _FONT_BOLD
    elif italic:
        table = _FONT_ITALIC
    else:
        table = _FONT_FALLBACKS

    path = table.get(key)
    if path and os.path.isfile(path):
        return path
    # 폴백: 동일 키의 regular variant
    if table is not _FONT_FALLBACKS:
        path = _FONT_FALLBACKS.get(key)
        if path and os.path.isfile(path):
            return path
    return None


def _build_text_shape(
    text: str,
    font_name: str,
    font_size_mm: float,
    bold: bool,
    italic: bool,
):
    """Unicode 문자열 → TopoDS_Shape (compound of glyph faces).

    먼저 Font_FontMgr 의 이름 lookup 을 시도하고 (StdPrs_BRepFont.Init(name, aspect, size)),
    실패 시 Windows system font 의 직접 경로를 사용한다.
    """
    from OCP.Font import (
        Font_FontAspect_Bold,
        Font_FontAspect_BoldItalic,
        Font_FontAspect_Italic,
        Font_FontAspect_Regular,
    )
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt
    from OCP.NCollection import NCollection_Utf8String
    from OCP.StdPrs import StdPrs_BRepFont, StdPrs_BRepTextBuilder

    if bold and italic:
        aspect = Font_FontAspect_BoldItalic
    elif bold:
        aspect = Font_FontAspect_Bold
    elif italic:
        aspect = Font_FontAspect_Italic
    else:
        aspect = Font_FontAspect_Regular

    font = StdPrs_BRepFont()
    # Note: do NOT call SetCompositeCurveMode(True). It produces glyph wires with
    # an orientation classification that makes BRepAlgoAPI_Fuse/Cut interpret the
    # prism's interior as exterior (boolean ops then misbehave even though
    # GProp's volume_mass is positive). Default mode produces a face with
    # multiple wire edges and the correct interior classification — matches
    # build123d.Compound.make_text and is boolean-safe.

    initialized = False
    # 1) Font_FontMgr 이름 기반 lookup
    try:
        if font.Init(NCollection_Utf8String(font_name), aspect, float(font_size_mm)):
            initialized = True
    except Exception:
        initialized = False

    # 2) Windows path 직접 lookup
    if not initialized:
        path = _resolve_font_path(font_name, bold, italic)
        if path is None:
            raise RuntimeError(
                f"text_engrave: 폰트 '{font_name}' 를 찾을 수 없음 "
                f"(bold={bold}, italic={italic}); FontMgr lookup + Windows 직접 경로 둘 다 실패"
            )
        if not font.Init(NCollection_Utf8String(path), float(font_size_mm), 0):
            raise RuntimeError(
                f"text_engrave: StdPrs_BRepFont.Init 실패 — path='{path}'"
            )

    builder = StdPrs_BRepTextBuilder()
    pen = gp_Ax3(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0), gp_Dir(1.0, 0.0, 0.0))
    shape = builder.Perform(font, NCollection_Utf8String(text), pen)
    if shape.IsNull():
        raise RuntimeError(f"text_engrave: text shape 가 null — text='{text}'")
    return shape


def _text_bbox_xy(shape) -> tuple[float, float, float, float]:
    """text shape 의 XY bbox (xmin, ymin, xmax, ymax). Z 는 ≈ 0."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    mn = bb.CornerMin()
    mx = bb.CornerMax()
    return (mn.X(), mn.Y(), mx.X(), mx.Y())


def build_text_solid(
    *,
    text: str,
    font_name: str,
    font_size_mm: float,
    bold: bool,
    italic: bool,
    center_x_mm: float,
    center_y_mm: float,
    rotation_deg: float,
    face_origin: tuple[float, float, float],
    face_normal: tuple[float, float, float],
    height_mm: float,
    direction: str,  # "into" (engrave) | "out" (emboss)
):
    """Text 의 BRep solid prism 생성 — engrave/emboss 공용 helper.

    Steps:
      1. Local XY 위에서 text shape 생성.
      2. text bbox center 기준으로 (center_x, center_y) 만큼 평행이동 + 원점 정렬.
      3. rotation_deg 로 Z축 회전.
      4. face 의 local frame (origin, normal) 으로 transform — Z 축 = face normal.
      5. prism along ±normal by height_mm.

    Args:
        direction: "into" → -normal 방향 prism (cut), "out" → +normal 방향 (fuse).
    """
    import math

    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Ax1, gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

    text_shape = _build_text_shape(text, font_name, font_size_mm, bold, italic)

    # 1) bbox 기준 원점 정렬 + (center_x, center_y) 오프셋
    xmin, ymin, xmax, ymax = _text_bbox_xy(text_shape)
    bbx = (xmin + xmax) / 2.0
    bby = (ymin + ymax) / 2.0

    trsf_center = gp_Trsf()
    trsf_center.SetTranslation(gp_Vec(-bbx + center_x_mm, -bby + center_y_mm, 0.0))
    centered = BRepBuilderAPI_Transform(text_shape, trsf_center, True).Shape()

    # 2) Z 축 rotation
    if abs(rotation_deg) > 1e-9:
        rot = gp_Trsf()
        rot.SetRotation(
            gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0)),
            math.radians(rotation_deg),
        )
        centered = BRepBuilderAPI_Transform(centered, rot, True).Shape()

    # 3) local XY → face local frame. local frame 의 Z 는 face normal.
    nx, ny, nz = face_normal
    nlen = math.sqrt(nx * nx + ny * ny + nz * nz)
    if nlen < 1e-9:
        raise RuntimeError("text_engrave: face normal 이 0 — 곡면 face?")
    nx, ny, nz = nx / nlen, ny / nlen, nz / nlen

    target_ax = gp_Ax3(
        gp_Pnt(*face_origin),
        gp_Dir(nx, ny, nz),
        # X reference: world X 와 normal 이 평행하면 world Y 로 폴백 (gp 가 알아서 처리하지만
        # 명시적으로 안전한 X 선택).
        gp_Dir(1.0, 0.0, 0.0) if abs(nx) < 0.9 else gp_Dir(0.0, 1.0, 0.0),
    )
    source_ax = gp_Ax3(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0), gp_Dir(1.0, 0.0, 0.0))
    place = gp_Trsf()
    place.SetTransformation(target_ax, source_ax)
    placed = BRepBuilderAPI_Transform(centered, place, True).Shape()

    # 4) prism construction.
    #   into (engrave): extrude along -n by height_mm  → spans [face - h, face]
    #   out  (emboss):  extrude along +n by height_mm, with the source face
    #                   shifted -n by EMBED_MM so the resulting prism overlaps
    #                   the body. Without overlap the boolean Fuse with a
    #                   coplanar-touching tool can yield an under-specified
    #                   result.
    EMBED_MM = 0.05
    if direction == "into":
        vec = gp_Vec(-nx, -ny, -nz)
        vec.Multiply(float(height_mm))
        prism_source = placed
    elif direction == "out":
        embed_trsf = gp_Trsf()
        embed_trsf.SetTranslation(
            gp_Vec(-nx * EMBED_MM, -ny * EMBED_MM, -nz * EMBED_MM)
        )
        prism_source = BRepBuilderAPI_Transform(placed, embed_trsf, True).Shape()
        vec = gp_Vec(nx, ny, nz)
        vec.Multiply(float(height_mm) + EMBED_MM)
    else:
        raise ValueError(f"build_text_solid: direction 은 'into'|'out' (got {direction!r})")

    prism = BRepPrimAPI_MakePrism(prism_source, vec).Shape()
    if prism.IsNull():
        raise RuntimeError("text_engrave: prism 생성 실패")
    return prism


@skill(
    name="text_engrave",
    category="modify/pocket",
    level="atomic",
    summary="Engrave Unicode text into a planar face by extruding glyph outlines along the "
            "inward face normal and subtracting from the body. Standard logo/label engraving "
            "(e.g., 'SAMSUNG' on the back housing).",
    selector_kinds=["faces"],
    history_rules={
        "target_face":     HistoryRule.MODIFIED_INHERIT,
        "engrave_walls":   HistoryRule.GENERATED_NEW,
        "engrave_bottom":  HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["text_engrave"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.3,
                              "extras": {"min_stroke_width_mm": 0.3, "max_depth_mm": 1.0}},
        "die_cast_al":       {"min_wall_mm": 0.5, "min_draft_deg": 1.0,
                              "extras": {"min_stroke_width_mm": 0.4}},
        "injection_mold_pa": {"min_wall_mm": 0.4, "min_draft_deg": 0.5,
                              "extras": {"min_stroke_width_mm": 0.4}},
        "laser_engrave":     {"extras": {"surface_only": True, "max_depth_mm": 0.1}},
    },
    failure_modes=["fm.text_outside_face", "fm.engrave_too_deep"],
    cost_hint=0.18,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class TextEngrave(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        text: str = Field(min_length=1)
        font_name: str = "Arial"
        font_size_mm: float = Field(default=5.0, gt=0, le=200)
        depth_mm: float = Field(default=0.3, gt=0, le=20)
        center_x_mm: float = 0.0
        center_y_mm: float = 0.0
        rotation_deg: float = 0.0
        bold: bool = False
        italic: bool = False

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        from phone_designer.skills._resolvers import (
            _face_center,
            _face_normal_at_center,
            resolve_faces,
        )

        # 빈 text 는 boolean 이전에 차단 (pydantic min_length=1 도 같이 막지만
        # 명시적 ValueError 가 사용자에게 더 친절하다).
        if not args.text or not args.text.strip():
            raise ValueError("text_engrave: text 가 비어 있음")

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"text_engrave: face_selector matched 0 faces — "
                f"{args.face_selector.model_dump()}"
            )
        target_face = faces[0]

        face_origin = _face_center(target_face)
        face_normal = _face_normal_at_center(target_face)
        if face_normal == (0.0, 0.0, 0.0):
            raise RuntimeError(
                "text_engrave: target face 가 planar 가 아님 — v0 은 planar face 만 지원"
            )

        tool = build_text_solid(
            text=args.text,
            font_name=args.font_name,
            font_size_mm=args.font_size_mm,
            bold=args.bold,
            italic=args.italic,
            center_x_mm=args.center_x_mm,
            center_y_mm=args.center_y_mm,
            rotation_deg=args.rotation_deg,
            face_origin=face_origin,
            face_normal=face_normal,
            height_mm=args.depth_mm,
            direction="into",
        )

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("text_engrave: BRepAlgoAPI_Cut 실패")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":    HistoryRule.MODIFIED_INHERIT,
                "engrave_walls":  HistoryRule.GENERATED_NEW,
                "engrave_bottom": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
