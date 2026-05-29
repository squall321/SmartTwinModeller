"""Tests for the modify_finish expansion: deburring, sanding_pass, surface_finish_tag."""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.create.sphere import Sphere
from phone_designer.skills.modify_finish.deburring import Deburring
from phone_designer.skills.modify_finish.sanding_pass import SandingPass
from phone_designer.skills.modify_finish.surface_finish_tag import (
    SurfaceFinishTag,
    get_finish_tags,
    set_finish_tags,
)


def _count_faces(body) -> int:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    n = 0
    it = TopExp_Explorer(body.wrapped if hasattr(body, "wrapped") else body, TopAbs_FACE)
    while it.More():
        n += 1
        it.Next()
    return n


# ---------------- deburring ----------------

def test_deburring_on_box_adds_faces():
    """Box 의 12 sharp edge 들이 deburr 의 작은 R 로 모두 깎여 face 가 늘어야 함."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10})
    initial_faces = _count_faces(box.body)
    assert initial_faces == 6

    result = Deburring().apply(box.body, {"max_radius_mm": 0.2})
    final_faces = _count_faces(result.body)
    assert final_faces > initial_faces, (
        f"deburring on a box should fillet sharp edges and add faces "
        f"(initial={initial_faces}, final={final_faces})"
    )


def test_deburring_on_sphere_is_noop():
    """Sphere 는 sharp edge 가 없으니 allow_no_change 가 정상 통과해야 함."""
    sph = Sphere().apply(None, {"radius_mm": 10})
    before = _count_faces(sph.body)
    # should NOT raise (post_condition has allow_no_change=True)
    result = Deburring().apply(sph.body, {"max_radius_mm": 0.2})
    after = _count_faces(result.body)
    assert after == before, "sphere has no sharp edges — should be a no-op"


def test_deburring_high_threshold_skips_all():
    """min_deviation_deg=170° 이상이면 거의 모든 edge 가 skip → no-op (no raise)."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10})
    before = _count_faces(box.body)
    result = Deburring().apply(
        box.body, {"max_radius_mm": 0.1, "min_deviation_deg": 170.0}
    )
    after = _count_faces(result.body)
    assert after == before, "no edge with 170° deviation → no-op expected"


def test_deburring_spec_metadata():
    spec = Deburring.spec
    assert spec.name == "deburring"
    assert spec.category == "modify/finish"
    assert spec.level == "atomic"
    assert spec.post_conditions, "deburring must declare post_conditions"
    assert spec.post_conditions[0].kind == "face_count_changed"
    assert spec.post_conditions[0].allow_no_change is True


# ---------------- sanding_pass ----------------

def test_sanding_pass_on_box_adds_faces():
    """Box 의 모든 외부 (convex) edge 12개에 미세 R 적용 → face 수 증가."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 20, "height_mm": 10})
    before = _count_faces(box.body)

    result = SandingPass().apply(box.body, {"radius_mm": 0.1})
    after = _count_faces(result.body)
    assert after > before, f"sanding should round all convex edges (before={before}, after={after})"


def test_sanding_pass_on_sphere_is_noop():
    """Sphere 표면은 edge 가 1 개 (seam) 거나 0 개 — convex predicate fail → no-op."""
    sph = Sphere().apply(None, {"radius_mm": 10})
    before = _count_faces(sph.body)
    result = SandingPass().apply(sph.body, {"radius_mm": 0.1})
    after = _count_faces(result.body)
    # Sphere 는 convex edge 가 없거나 (seam 만) → no-op 또는 동일.
    assert after == before, "sphere — no convex non-tangent edge to sand"


def test_sanding_pass_spec_metadata():
    spec = SandingPass.spec
    assert spec.name == "sanding_pass"
    assert spec.category == "modify/finish"
    assert spec.level == "atomic"
    assert spec.post_conditions
    assert spec.post_conditions[0].kind == "face_count_changed"
    assert spec.post_conditions[0].allow_no_change is True


# ---------------- surface_finish_tag ----------------

def test_surface_finish_tag_does_not_change_geometry():
    """metadata-only: face 수/volume 모두 그대로."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10})
    before_faces = _count_faces(box.body)

    result = SurfaceFinishTag().apply(
        box.body,
        {
            "face_selector": {"kind": "faces_by_normal", "direction": (0, 0, 1)},
            "finish_type": "polish",
            "target_ra_um": 0.2,
        },
    )
    after_faces = _count_faces(result.body)
    assert after_faces == before_faces, "surface_finish_tag must not change topology"

    tags = get_finish_tags(result.body)
    assert "polish" in tags, f"polish tag missing: {tags}"
    assert len(tags["polish"]) >= 1
    assert tags["polish"][0]["target_ra_um"] == 0.2


def test_surface_finish_tag_appends_to_existing():
    """두 번 호출하면 동일 finish_type 의 record 가 누적되어야 함."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10})
    s1 = SurfaceFinishTag().apply(
        box.body,
        {
            "face_selector": {"kind": "faces_by_normal", "direction": (0, 0, 1)},
            "finish_type": "matte",
            "target_ra_um": 0.8,
        },
    )
    s2 = SurfaceFinishTag().apply(
        s1.body,
        {
            "face_selector": {"kind": "faces_by_normal", "direction": (0, 0, -1)},
            "finish_type": "matte",
            "target_ra_um": 1.0,
        },
    )
    tags = get_finish_tags(s2.body)
    assert "matte" in tags
    assert len(tags["matte"]) >= 2, f"two calls should accumulate: {tags['matte']}"


def test_surface_finish_tag_raises_on_no_match():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10})
    with pytest.raises(RuntimeError, match="0 faces"):
        SurfaceFinishTag().apply(
            box.body,
            {
                # No face has this exotic normal.
                "face_selector": {
                    "kind": "faces_by_normal",
                    "direction": (0.577, 0.577, 0.577),
                    "tol_deg": 1.0,
                },
                "finish_type": "texture_a",
                "target_ra_um": 3.2,
            },
        )


def test_surface_finish_tag_extras_carry_metadata():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10})
    result = SurfaceFinishTag().apply(
        box.body,
        {
            "face_selector": {"kind": "faces_by_normal", "direction": (1, 0, 0)},
            "finish_type": "texture_b",
            "target_ra_um": 6.3,
        },
    )
    assert result.extras["finish_type"] == "texture_b"
    assert result.extras["target_ra_um"] == 6.3
    assert result.extras["face_count"] >= 1


def test_get_set_finish_tags_roundtrip():
    """helper API contract: set → get returns the same payload."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10})
    payload = {"polish": [{"center": (0, 0, 5), "area_mm2": 100.0, "target_ra_um": 0.1}]}
    set_finish_tags(box.body, payload)
    assert get_finish_tags(box.body) == payload


def test_surface_finish_tag_spec_metadata():
    spec = SurfaceFinishTag.spec
    assert spec.name == "surface_finish_tag"
    assert spec.category == "modify/finish"
    assert spec.level == "atomic"
    assert spec.post_conditions
    assert spec.post_conditions[0].kind == "body_present"
