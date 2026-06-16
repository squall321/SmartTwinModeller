"""classify_generating_op + parametric generating-op base tests (PILLAR
FREEFORM, 2026-06-16).

Three guarantees for the parametric-from-catalog reconstruction lane:

  1. classify_generating_op recovers a freeform body's DOMINANT generating
     operation and its EDITABLE inputs: the revolved frustum fixture yields a
     ``revolve`` op with a recovered half-plane meridian; the corpus screw
     (a real lathe-style part) also yields ``revolve``.

  2. The recovered parametric base reconstructs competitively on hausdorff —
     measured exactly the way the planner's revert-guard measures it — and the
     planner's ``accept_freeform_base`` actually labels it
     ``base_mechanism='parametric_revolve'``.

  3. EDITABILITY: scaling the recovered revolve meridian (profile_scale=1.2)
     rebuilds a body whose volume scales by 1.2**3 and whose bbox scales 1.2× in
     every dimension — a parametric change a re-solidified shell CANNOT make.

  4. GATE SAFETY: base_profile_mode='off' (the default) is byte-identical — the
     parametric candidate is opt-in via 'auto' only, so the box corpus baseline
     is untouched.

The reconstruction proofs run a full box-mode round-trip and are marked
``slow`` so a fast unit pass can deselect them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT = Path(__file__).resolve().parents[2]
_FIXTURES = _PROJECT / "fixtures"
if str(_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_FIXTURES))


def _import_body(rel: str):
    from phone_designer.skills.create.import_step import ImportStep

    step = _PROJECT / rel
    if not step.is_file():
        pytest.skip(f"{step} missing")
    return ImportStep().apply(None, {"path": str(step)}).body


def _vol_bbox(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    sh = shape.wrapped if hasattr(shape, "wrapped") else shape
    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(sh, p)
    bb = Bnd_Box()
    BRepBndLib.Add_s(sh, bb)
    g = bb.Get()
    return float(p.Mass()), (g[3] - g[0], g[4] - g[1], g[5] - g[2])


# ──────────────────────────────────────────────────────────────────────────────
# 1. Classifier: revolve recovery on the frustum fixture + the corpus screw.


def test_revolve_frustum_classifies_as_revolve_with_meridian():
    from phone_designer.skills.inspect.classify_generating_op import (
        ClassifyGeneratingOp,
    )

    body = _import_body("fixtures/freeform/revolve_frustum.step")
    op = ClassifyGeneratingOp().apply(body, {}).extras["generating_op"]

    assert op["kind"] == "revolve", op["reason"]
    assert op["confidence"] >= 0.5
    assert op["axis"] is not None
    # Canonical axis points +Z (dominant component positive).
    assert op["axis"]["dir"][2] > 0.9
    prof = op["profile_sketch"]
    assert prof is not None and prof["kind"] == "polygon"
    verts = prof["vertices"]
    # The frustum meridian: R1=15 at base (y=0) → R2=8 at top (y=20). The
    # recovered half-plane meridian must stay on the +X (radial) side (x >= 0,
    # never crossing the axis) and span the radii / height of the frustum.
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    assert min(xs) >= -1e-3, "meridian crosses the revolution axis"
    assert max(xs) == pytest.approx(15.0, abs=0.5)   # bottom radius
    assert max(ys) == pytest.approx(20.0, abs=0.5)   # frustum height


def test_corpus_screw_classifies_as_revolve():
    from phone_designer.skills.inspect.classify_generating_op import (
        ClassifyGeneratingOp,
    )

    body = _import_body("corpus/oem/occt__screw.step")
    op = ClassifyGeneratingOp().apply(body, {}).extras["generating_op"]
    assert op["kind"] == "revolve", op["reason"]
    assert op["profile_sketch"] is not None


def test_generating_op_extras_shape_is_complete():
    """The extras dict always carries the full documented schema (so consumers
    can rely on the keys regardless of the recovered kind)."""
    from phone_designer.skills.inspect.classify_generating_op import (
        ClassifyGeneratingOp,
    )

    body = _import_body("fixtures/freeform/revolve_frustum.step")
    op = ClassifyGeneratingOp().apply(body, {}).extras["generating_op"]
    for key in (
        "kind", "confidence", "axis", "profile_sketch",
        "section_sketches", "path", "reason", "diagnostics",
    ):
        assert key in op
    assert op["kind"] in ("revolve", "loft", "sweep", "none")


# ──────────────────────────────────────────────────────────────────────────────
# 2. EDITABILITY — the headline proof.


def test_revolve_meridian_scale_changes_body_parametrically():
    """Scaling the recovered revolve meridian by 1.2 rebuilds a body whose
    volume scales by 1.2**3 and whose bbox scales 1.2× in every dimension.

    This is the whole point: the recovered op is EDITABLE. A re-solidified shell
    is frozen and cannot do this.
    """
    from phone_designer.skills.inspect.classify_generating_op import (
        ClassifyGeneratingOp,
    )
    from phone_designer.skills.create.place_generating_op_solid import (
        build_generating_op_solid,
    )

    body = _import_body("fixtures/freeform/revolve_frustum.step")
    op = ClassifyGeneratingOp().apply(body, {}).extras["generating_op"]
    assert op["kind"] == "revolve"
    axis = op["axis"]

    def build(scale):
        return build_generating_op_solid(
            "revolve",
            profile_sketch=op["profile_sketch"],
            axis_origin=tuple(axis["origin"]),
            axis_dir=tuple(axis["dir"]),
            profile_scale=scale,
        )

    v1, bb1 = _vol_bbox(build(1.0))
    v2, bb2 = _vol_bbox(build(1.2))

    # identity rebuild reproduces the original volume + bbox.
    ov, obb = _vol_bbox(body)
    assert v1 == pytest.approx(ov, rel=0.02)
    for a, b in zip(bb1, obb):
        assert a == pytest.approx(b, rel=0.02)

    # parametric edit: volume ×1.2**3, every bbox dim ×1.2.
    assert v2 == pytest.approx(v1 * (1.2 ** 3), rel=0.02), (
        f"volume did not scale parametrically: {v2:.1f} vs {v1 * 1.728:.1f}"
    )
    for a, b in zip(bb2, bb1):
        assert a == pytest.approx(b * 1.2, rel=0.02)


# ──────────────────────────────────────────────────────────────────────────────
# 3. Parametric base A/B — accepted on the clean revolve, labelled honestly.


@pytest.mark.slow
def test_parametric_revolve_base_accepted_on_frustum():
    """accept_freeform_base keeps the parametric revolve base for the frustum
    (its hausdorff is far tighter than the box base) and labels it
    base_mechanism='parametric_revolve'."""
    from phone_designer.skills.reverse_engineer import (
        plan_from_feature_catalog as P,
    )
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )

    body = _import_body("fixtures/freeform/revolve_frustum.step")
    cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
    bb = cat.get("initial_bbox_mm")
    bbox = tuple(float(c) for c in bb[:6])
    box_plan = P._build_plan(
        cat, body=body, base_step_kind="box", base_profile_mode="off"
    )
    base_l, base_w, base_h = (
        bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]
    )
    verdict = P.accept_freeform_base(
        box_plan, body, bbox, base_l=base_l, base_w=base_w, base_h=base_h
    )
    assert verdict is not None
    assert verdict["base_mechanism"] == "parametric_revolve"
    # The single base step is the editable parametric op.
    assert len(verdict["steps"]) == 1
    assert verdict["steps"][0]["skill"] == "place_generating_op_solid"
    assert verdict["steps"][0]["args"]["kind"] == "revolve"
    # And it really is tighter than the box base it replaced.
    assert verdict["base_haus_mm"] < verdict["box_haus_mm"]


@pytest.mark.slow
def test_parametric_revolve_base_accepted_on_corpus_screw():
    """The real lathe-style corpus screw also recovers a parametric revolve base
    that beats the box base on hausdorff."""
    from phone_designer.skills.reverse_engineer import (
        plan_from_feature_catalog as P,
    )
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )

    body = _import_body("corpus/oem/occt__screw.step")
    cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
    bb = cat.get("initial_bbox_mm")
    bbox = tuple(float(c) for c in bb[:6])
    box_plan = P._build_plan(
        cat, body=body, base_step_kind="box", base_profile_mode="off"
    )
    base_l, base_w, base_h = (
        bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]
    )
    verdict = P.accept_freeform_base(
        box_plan, body, bbox, base_l=base_l, base_w=base_w, base_h=base_h
    )
    assert verdict is not None
    assert verdict["base_mechanism"] == "parametric_revolve"
    assert verdict["base_haus_mm"] < verdict["box_haus_mm"]


# ──────────────────────────────────────────────────────────────────────────────
# 4. GATE SAFETY — base_profile_mode='off' is byte-identical (no parametric
#    candidate is even attempted), so the box corpus baseline is untouched.


def test_off_mode_does_not_emit_parametric_base():
    """The DEFAULT base_profile_mode='off' NEVER engages the parametric
    generating-op candidate (accept_freeform_base is only called in 'auto'
    mode). This is the corpus-safety invariant: the parametric lane is strictly
    opt-in, so the off-mode plan never carries the parametric base step / label.

    (The off-mode base step itself is whatever _pick_base_shape sizes — e.g. a
    'cone' primitive for this frustum — which is the pre-existing behaviour and
    is left untouched.)"""
    from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
        PlanFromFeatureCatalog,
    )

    body = _import_body("fixtures/freeform/revolve_frustum.step")
    res = PlanFromFeatureCatalog().apply(body, {"base_profile_mode": "off"})
    plan = res.extras["generated_plan"]
    # No parametric mechanism label and no parametric base skill in off mode.
    assert plan.get("base_mechanism") != "parametric_revolve"
    skills = [s["skill"] for s in plan["steps"]]
    assert "place_generating_op_solid" not in skills
