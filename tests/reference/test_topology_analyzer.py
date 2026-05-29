"""TopologyAnalyzer 검증."""
from __future__ import annotations

from pathlib import Path

import pytest

from phone_designer.reference.step_reader import classify_parts, read_xde_step
from phone_designer.reference.topology_analyzer import TopologyAnalyzer
from phone_designer.skills.create.box import Box
from phone_designer.skills.create.disc_with_dome import DiscWithDome
from phone_designer.skills.modify_pocket.hole import Hole


FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "simple_watch.step"


def _shape(part):
    return part.wrapped if hasattr(part, "wrapped") else part


def test_box_only_planar_faces():
    """순수 박스 → 6 planar face, fillet/hole/torus 없음."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    catalog = TopologyAnalyzer().analyze(_shape(box))
    assert catalog.n_faces == 6
    assert catalog.surface_type_histogram.get("plane", 0) == 6
    assert len(catalog.fillets) == 0
    assert len(catalog.holes) == 0


def test_box_with_hole_detects_cylindrical():
    """Box → hole → cylindrical face 1개 + hole 1개."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    drilled = Hole().apply(
        box,
        {"position": (0.0, 0.0, 10.0), "diameter_mm": 4.0, "depth_mm": 5.0, "direction": "-Z"},
    ).body
    catalog = TopologyAnalyzer().analyze(_shape(drilled))
    assert catalog.surface_type_histogram.get("cylinder", 0) >= 1
    assert len(catalog.holes) >= 1
    detected = catalog.holes[0]
    assert abs(detected.diameter_mm - 4.0) < 0.1


def test_disc_with_bottom_fillet_detects_fillet():
    """원판 + bottom corner fillet → toroidal face 1개."""
    disc = DiscWithDome().apply(None, {
        "diameter_mm": 40, "height_mm": 10,
        "dome_rise_mm": 0, "corner_r_mm": 2.0,
    }).body
    catalog = TopologyAnalyzer().analyze(_shape(disc))
    # 원판의 옆면 cylinder + bottom 의 toroidal fillet + top/bottom plane
    assert catalog.surface_type_histogram.get("torus", 0) >= 1
    # 작은 R fillet 검출
    torus_fillets = [f for f in catalog.fillets if f.surface_type == "torus"]
    assert len(torus_fillets) >= 1
    # 검출된 R 가 ~2.0
    rs = [f.radius_mm for f in torus_fillets]
    assert any(abs(r - 2.0) < 0.5 for r in rs)


def test_fixture_housing_summary():
    """fixture 의 housing 부품 분석 — face count + 카테고리 분포."""
    if not FIXTURE.exists():
        pytest.skip("fixture STEP 없음")
    parts = read_xde_step(FIXTURE, load_shapes=True)
    cat_map = classify_parts(parts)
    housing_parts = cat_map.get("housing", [])
    assert housing_parts, "housing 부품 없음"

    catalog = TopologyAnalyzer().analyze(housing_parts[0].shape)
    print(catalog.summary())
    # housing 은 외피 base + 내부 cavity + display pocket + crown hole + lug hole 등
    # → 다양한 surface type 분포
    assert catalog.n_faces >= 6
    # crown / lug hole → cylinder 검출
    assert catalog.surface_type_histogram.get("cylinder", 0) >= 1
    assert len(catalog.holes) >= 1
