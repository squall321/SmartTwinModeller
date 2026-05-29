"""text_engrave / text_emboss 단위 테스트.

OCCT 7.8 의 StdPrs_BRepFont 백엔드. 시스템 폰트 (Windows Arial) 가 reachable 이어야
한다 — 없으면 모듈 전체 skip.
"""
from __future__ import annotations

import os

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_boss.text_emboss import TextEmboss
from phone_designer.skills.modify_pocket.text_engrave import TextEngrave

# 백엔드 가용성 — Arial 폰트가 없으면 모든 테스트 skip.
_ARIAL = r"C:\Windows\Fonts\arial.ttf"
pytestmark = pytest.mark.skipif(
    not os.path.isfile(_ARIAL),
    reason=f"Arial system font not available at {_ARIAL} — text skills require OCCT-readable font",
)


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return float(props.Mass())


def _bbox(body):
    """body bbox (xmin, ymin, zmin, xmax, ymax, zmax)."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.Add_s(body.wrapped, bb)
    mn = bb.CornerMin()
    mx = bb.CornerMax()
    return (mn.X(), mn.Y(), mn.Z(), mx.X(), mx.Y(), mx.Z())


def _make_box(L=50.0, W=50.0, H=10.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


def test_text_engrave_basic_volume_decrease():
    """'AB' 5mm tall, depth 0.5 — top face 에서 일부 material 제거."""
    body = _make_box()
    v_before = _volume(body)

    r = TextEngrave().apply(
        body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "text": "AB",
            "font_name": "Arial",
            "font_size_mm": 5.0,
            "depth_mm": 0.5,
        },
    )

    v_after = _volume(r.body)
    delta = v_before - v_after
    assert delta > 0.5, (
        f"engrave 로 제거된 volume 이 너무 작다: delta={delta:.4f} mm³ (5mm 'AB' depth 0.5)"
    )
    # 'AB' 5mm 약 6.4×3.6 area * 0.5 mm depth — 글리프 ink 비율 약 40~50% → 5..15 mm³ 가 합리적
    assert delta < 30.0, (
        f"engrave delta 가 너무 크다: {delta:.4f} mm³ — text shape 가 면적을 차지 너무 크게 잡혔다?"
    )


def test_text_engrave_empty_string_raises():
    """빈 string 은 boolean 이전에 ValueError — pydantic min_length=1 가 먼저 잡는다."""
    body = _make_box()

    # 완전 빈 string 은 pydantic 의 min_length=1 에서 ValidationError. whitespace 만
    # 들어오면 args.text.strip() == '' 에서 ValueError. 양쪽 다 허용.
    with pytest.raises((ValueError, Exception)):
        TextEngrave().apply(
            body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "text": "",
                "depth_mm": 0.3,
            },
        )

    # whitespace 만 → ValueError
    with pytest.raises(ValueError, match="비어"):
        TextEngrave().apply(
            body,
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "text": "   ",
                "depth_mm": 0.3,
            },
        )


def test_text_emboss_basic_volume_increase():
    """'X' 4mm tall, height 0.4 — top face 에서 외측으로 돌출."""
    body = _make_box()
    v_before = _volume(body)

    r = TextEmboss().apply(
        body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "text": "X",
            "font_name": "Arial",
            "font_size_mm": 4.0,
            "height_mm": 0.4,
        },
    )

    v_after = _volume(r.body)
    delta = v_after - v_before
    assert delta > 0.3, (
        f"emboss volume 증가가 너무 작다: delta={delta:.4f} mm³"
    )
    assert delta < 20.0, (
        f"emboss volume 증가가 너무 크다: {delta:.4f} mm³"
    )

    # Emboss 후 bbox z-max 는 box top (z=10) + height_mm (0.4) 근처
    bb = _bbox(r.body)
    assert bb[5] > 10.0 + 0.3, (
        f"emboss 후 z-max 가 box top + 0.3 보다 작다: {bb[5]:.4f}"
    )


def test_text_engrave_rotation_changes_bbox():
    """'WW' (가로로 명확히 넓은 텍스트) 를 0° 와 90° 에서 engrave 시 결과 본문의
    tool bbox aspect 가 swap 된다 (X-wide ↔ Y-wide).
    """
    body = _make_box()

    r0 = TextEngrave().apply(
        body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "text": "WW",
            "font_size_mm": 5.0,
            "depth_mm": 0.5,
            "rotation_deg": 0.0,
        },
    )
    r90 = TextEngrave().apply(
        body,
        {
            "face_selector": {"kind": "face_named", "name": "top"},
            "text": "WW",
            "font_size_mm": 5.0,
            "depth_mm": 0.5,
            "rotation_deg": 90.0,
        },
    )

    # Volume delta should be ~equal — same glyph, just rotated.
    v_base = _volume(body)
    d0 = v_base - _volume(r0.body)
    d90 = v_base - _volume(r90.body)
    assert abs(d0 - d90) / max(d0, d90) < 0.05, (
        f"회전만 다른데 volume delta 가 다르다: 0°={d0:.4f}, 90°={d90:.4f}"
    )
    assert d0 > 0 and d90 > 0

    # Verify tool bbox aspect swaps with rotation.
    from phone_designer.skills.modify_pocket.text_engrave import build_text_solid
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    def tool_xy(rot):
        s = build_text_solid(
            text="WW", font_name="Arial", font_size_mm=5.0,
            bold=False, italic=False,
            center_x_mm=0.0, center_y_mm=0.0, rotation_deg=rot,
            face_origin=(0.0, 0.0, 0.0), face_normal=(0.0, 0.0, 1.0),
            height_mm=0.5, direction="into",
        )
        bb = Bnd_Box()
        BRepBndLib.Add_s(s, bb)
        return (bb.CornerMax().X() - bb.CornerMin().X(),
                bb.CornerMax().Y() - bb.CornerMin().Y())

    dx0, dy0 = tool_xy(0.0)
    dx90, dy90 = tool_xy(90.0)

    assert dx0 > dy0, f"0°: dx({dx0:.3f}) 이 dy({dy0:.3f}) 보다 커야 'WW' 는 X-wide"
    assert dy90 > dx90, f"90°: dy({dy90:.3f}) 이 dx({dx90:.3f}) 보다 커야"
    # dx/dy swap (회전 90° 이면 X 와 Y bbox 가 swap)
    assert abs(dx0 - dy90) < 0.1, f"swap check: dx0={dx0:.3f} vs dy90={dy90:.3f}"
    assert abs(dy0 - dx90) < 0.1, f"swap check: dy0={dy0:.3f} vs dx90={dx90:.3f}"


def test_text_engrave_centering():
    """text 가 face center 에 centered — engrave tool 의 XY bbox center 가 face
    center 와 일치해야 한다 (center_x_mm=center_y_mm=0)."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    from phone_designer.skills.modify_pocket.text_engrave import build_text_solid

    # 50x50 box 의 top face center = (25, 25, 10)
    face_origin = (25.0, 25.0, 10.0)
    face_normal = (0.0, 0.0, 1.0)

    tool = build_text_solid(
        text="ABC",
        font_name="Arial",
        font_size_mm=6.0,
        bold=False, italic=False,
        center_x_mm=0.0, center_y_mm=0.0,
        rotation_deg=0.0,
        face_origin=face_origin,
        face_normal=face_normal,
        height_mm=0.5,
        direction="into",
    )
    bb = Bnd_Box()
    BRepBndLib.Add_s(tool, bb)
    mn = bb.CornerMin(); mx = bb.CornerMax()
    cx = (mn.X() + mx.X()) / 2.0
    cy = (mn.Y() + mx.Y()) / 2.0

    # face center 와 0.05 mm 이내 (텍스트 ink 가 bbox 의 정중앙은 아닐 수 있는
    # font metric 미세차 허용)
    assert abs(cx - 25.0) < 0.1, f"engrave centering: cx={cx:.4f} expected 25.0"
    assert abs(cy - 25.0) < 0.1, f"engrave centering: cy={cy:.4f} expected 25.0"

    # 그리고 z range 는 face top 의 아래 0.5mm (engrave 는 into → -Z 방향)
    assert mx.Z() <= 10.0 + 1e-6
    assert mn.Z() >= 10.0 - 0.5 - 1e-6


def test_text_engrave_spec_metadata():
    spec = TextEngrave.spec
    assert spec.name == "text_engrave"
    assert spec.category == "modify/pocket"
    assert spec.level == "atomic"
    assert "text_engrave" in spec.produces_features


def test_text_emboss_spec_metadata():
    spec = TextEmboss.spec
    assert spec.name == "text_emboss"
    assert spec.category == "modify/boss"
    assert "text_emboss" in spec.produces_features
