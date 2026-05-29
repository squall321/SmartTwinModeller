"""STEP XDE reader 검증."""
from __future__ import annotations

from pathlib import Path

import pytest

from phone_designer.reference.step_reader import classify_parts, read_xde_step


FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "simple_watch.step"


def test_read_xde_step_returns_5_parts():
    if not FIXTURE.exists():
        pytest.skip("fixture STEP 없음 — make_simple_watch.py 먼저 실행")
    parts = read_xde_step(FIXTURE)
    assert len(parts) == 5


def test_read_xde_step_names_preserved():
    if not FIXTURE.exists():
        pytest.skip("fixture STEP 없음")
    parts = read_xde_step(FIXTURE)
    names = {p.name for p in parts}
    expected = {"housing", "display", "battery", "crown", "lug_pair"}
    assert expected <= names, f"missing names: {expected - names}"


def test_read_xde_step_load_shapes():
    if not FIXTURE.exists():
        pytest.skip("fixture STEP 없음")
    parts = read_xde_step(FIXTURE, load_shapes=True)
    for p in parts:
        assert p.shape is not None, f"part {p.name}: shape 없음"


def test_read_xde_step_skip_shapes():
    if not FIXTURE.exists():
        pytest.skip("fixture STEP 없음")
    parts = read_xde_step(FIXTURE, load_shapes=False)
    for p in parts:
        assert p.shape is None


def test_classify_parts_maps_housing():
    if not FIXTURE.exists():
        pytest.skip("fixture STEP 없음")
    parts = read_xde_step(FIXTURE, load_shapes=False)
    cat = classify_parts(parts)
    assert "housing" in cat
    assert len(cat["housing"]) == 1


def test_classify_parts_maps_lug_pair_to_lug():
    if not FIXTURE.exists():
        pytest.skip("fixture STEP 없음")
    parts = read_xde_step(FIXTURE, load_shapes=False)
    cat = classify_parts(parts)
    assert "lug" in cat
    assert any(p.name == "lug_pair" for p in cat["lug"])


def test_classify_parts_no_unknown_for_fixture():
    """fixture 의 5 부품 모두 알려진 category 에 매칭."""
    if not FIXTURE.exists():
        pytest.skip("fixture STEP 없음")
    parts = read_xde_step(FIXTURE, load_shapes=False)
    cat = classify_parts(parts)
    assert "unknown" not in cat or len(cat["unknown"]) == 0


def test_file_not_found_raises():
    with pytest.raises(FileNotFoundError):
        read_xde_step(Path("nonexistent_file.step"))
