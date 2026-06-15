"""recover_freeform_solid + place_freeform_solid + planner base tests
(FREEFORM RE-SOLIDIFY, 2026-06-15).

The proven OCCT 7.8 recipe sews a body's COMPLETE boundary face set into a
watertight freeform SOLID that reproduces the TRUE outer geometry near-
losslessly (hausdorff ~0, volume == original). These tests pin:

  1. Ventilator RE-SOLIDIFIES: volume == original (±0.5%), hausdorff < 0.5 mm,
     is_valid, body returned unchanged (read-only).
  2. A genuinely-open synthetic shell (one box face removed) → solidified=False
     honest open-shell report, NO fake solid.
  3. The planner base A/B keeps the freeform-shell base on Ventilator (its
     hausdorff strictly beats the box) and REVERTS to box on a prismatic part
     whose box base is already its tight outline.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT = Path(__file__).resolve().parents[2]
_FIXTURES = _PROJECT / "fixtures"
if str(_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_FIXTURES))

_VENT = _PROJECT / "corpus" / "oem" / "pythonocc__Ventilator.step"


def _shape_volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, p)
    return abs(float(p.Mass()))


# ──────────────────────────────────────────────────────────────────────────────
# 1 — Ventilator re-solidifies near-losslessly.


@pytest.mark.slow
def test_ventilator_resolidifies_near_lossless():
    from phone_designer.skills.create.import_step import ImportStep
    from phone_designer.skills.inspect.recover_freeform_solid import (
        RecoverFreeformSolid,
    )

    if not _VENT.is_file():
        pytest.skip("Ventilator corpus file missing")
    body = ImportStep().apply(None, {"path": str(_VENT)}).body
    res = RecoverFreeformSolid().apply(body, {})
    rep = res.extras["freeform_solid"]

    # The watertight VALID solid forms from the COMPLETE boundary.
    assert rep["solidified"] is True
    assert rep["is_valid"] is True
    assert rep["n_shells"] >= 1
    assert rep["n_faces_sewn"] > 0

    # Volume reproduces the original to within 0.5 %.
    orig_v = _shape_volume(body.wrapped if hasattr(body, "wrapped") else body)
    assert rep["volume_mm3"] == pytest.approx(orig_v, rel=0.005)
    assert rep["volume_ratio"] == pytest.approx(1.0, abs=0.005)

    # Hausdorff vs the original is essentially zero (faces are the originals).
    assert rep["hausdorff_vs_original_mm"] is not None
    assert rep["hausdorff_vs_original_mm"] < 0.5

    # The recovered solid is attached for the planner, and the body is
    # returned UNCHANGED (read-only contract).
    from build123d import Part
    assert isinstance(rep["part"], Part)
    assert res.body is body


# ──────────────────────────────────────────────────────────────────────────────
# 2 — genuinely-open shell → honest solidified=False, no fake solid.


def _open_box_shell():
    """A box boundary with ONE face removed — a genuinely OPEN shell that can
    never close into a watertight solid (a compound of 5 of 6 box faces)."""
    from build123d import Box, Part
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    from phone_designer.skills._resolvers import _all_faces

    box = Box(10, 10, 10)
    faces = _all_faces(box.wrapped)
    builder = BRep_Builder()
    comp = TopoDS_Compound()
    builder.MakeCompound(comp)
    for f in faces[:-1]:  # drop the last face → open boundary
        builder.Add(comp, f)
    return Part(comp)


def test_open_shell_is_honest_no_fake_solid():
    from phone_designer.skills.inspect.recover_freeform_solid import (
        RecoverFreeformSolid,
    )

    open_body = _open_box_shell()
    res = RecoverFreeformSolid().apply(open_body, {})
    rep = res.extras["freeform_solid"]

    # Honest open-boundary outcome — NO fake solid is fabricated.
    assert rep["solidified"] is False
    assert rep["is_valid"] is False
    assert rep["part"] is None
    assert rep["volume_mm3"] is None
    assert rep["reason"] == "open_shell_no_solid"
    # An honest open-shell surface deviation IS produced (the open shell
    # reproduces the 5 faces it does carry exactly).
    assert rep["open_shell_deviation_vs_body"] is not None
    assert rep["fidelity_basis"] == "open_shell_surface_only"


def test_place_freeform_solid_round_trips_ventilator():
    """The create skill re-solidifies a STEP boundary into a base solid whose
    volume matches the original (round-trips through the executor contract)."""
    from phone_designer.skills.create.place_freeform_solid import (
        PlaceFreeformSolid,
    )

    if not _VENT.is_file():
        pytest.skip("Ventilator corpus file missing")
    res = PlaceFreeformSolid().apply(None, {"path": str(_VENT)})
    assert res.body is not None
    placed = res.body.wrapped if hasattr(res.body, "wrapped") else res.body
    from phone_designer.skills.create.import_step import ImportStep
    orig = ImportStep().apply(None, {"path": str(_VENT)}).body
    orig_v = _shape_volume(orig.wrapped if hasattr(orig, "wrapped") else orig)
    assert _shape_volume(placed) == pytest.approx(orig_v, rel=0.005)


def test_place_freeform_solid_open_boundary_raises(tmp_path):
    """A STEP of a genuinely-open boundary must FAIL the place step — never
    fabricate a solid."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    from phone_designer.skills.create.place_freeform_solid import (
        PlaceFreeformSolid,
    )

    open_body = _open_box_shell()
    step = tmp_path / "open_shell.step"
    w = STEPControl_Writer()
    w.Transfer(open_body.wrapped, STEPControl_AsIs)
    assert w.Write(str(step)) == IFSelect_RetDone
    with pytest.raises(Exception):
        PlaceFreeformSolid().apply(None, {"path": str(step)})


# ──────────────────────────────────────────────────────────────────────────────
# 3 — planner base A/B: keep on Ventilator, revert on prismatic.


def test_accept_freeform_shell_keeps_strictly_better(monkeypatch):
    """``_accept_freeform_shell_base`` keeps the re-solidified base when its
    hausdorff strictly beats BOTH the box and silhouette baselines — and the
    accepted plan is the FULL reconstruction (base step ONLY) labelled
    honestly."""
    from phone_designer.skills.reverse_engineer import (
        plan_from_feature_catalog as P,
    )

    box_plan = {
        "plan_name": "p",
        "steps": [
            {"id": "s_base", "skill": "box", "args": {}},
            {"id": "s_pocket_0", "skill": "extrude_pocket_world", "args": {}},
        ],
    }
    ff_step = {"id": "s_base", "skill": "place_freeform_solid", "args": {"path": "x"}}
    # box 20.0, freeform-shell 0.05 (strictly tighter).
    scores = {"box": (0.4, 20.0), "place_freeform_solid": (0.4, 0.05)}

    def fake_score(plan_dict, body, bbox):
        return scores[plan_dict["steps"][0]["skill"]]

    monkeypatch.setattr(P, "_score_reconstruction", fake_score)
    kept = P._accept_freeform_shell_base(
        box_plan, ff_step, body=object(), bbox=(0,) * 6,
        extra_baseline_haus=0.5,  # silhouette baseline — freeform must beat it too
    )
    assert kept is not None
    # FULL reconstruction = base step ONLY (no re-cut features → no double-count).
    assert len(kept["steps"]) == 1
    assert kept["steps"][0]["skill"] == "place_freeform_solid"
    assert kept["base_mechanism"] == "freeform_shell"
    assert "re-solidified boundary" in kept["base_label"]


def test_accept_freeform_shell_reverts_when_not_better(monkeypatch):
    """Revert-safety: when the freeform-shell hausdorff does NOT beat the box
    (or loses to the silhouette baseline), keep the box plan."""
    from phone_designer.skills.reverse_engineer import (
        plan_from_feature_catalog as P,
    )

    box_plan = {"plan_name": "p", "steps": [{"id": "s_base", "skill": "box", "args": {}}]}
    ff_step = {"id": "s_base", "skill": "place_freeform_solid", "args": {}}

    # Case A — freeform-shell WORSE than box → revert.
    monkeypatch.setattr(
        P, "_score_reconstruction",
        lambda pd, b, bb: {"box": (0.4, 1.0), "place_freeform_solid": (0.4, 2.0)}[
            pd["steps"][0]["skill"]
        ],
    )
    assert P._accept_freeform_shell_base(
        box_plan, ff_step, object(), (0,) * 6, extra_baseline_haus=None,
    ) is None

    # Case B — better than box but LOSES to the silhouette baseline → revert.
    monkeypatch.setattr(
        P, "_score_reconstruction",
        lambda pd, b, bb: {"box": (0.4, 5.0), "place_freeform_solid": (0.4, 1.0)}[
            pd["steps"][0]["skill"]
        ],
    )
    assert P._accept_freeform_shell_base(
        box_plan, ff_step, object(), (0,) * 6, extra_baseline_haus=0.5,
    ) is None


@pytest.mark.slow
def test_ventilator_planner_keeps_freeform_shell_base():
    """End-to-end: the production A/B (accept_freeform_base) keeps the
    freeform-shell base on Ventilator (hausdorff strictly beats box)."""
    from phone_designer.skills.create.import_step import ImportStep
    from phone_designer.skills.reverse_engineer import (
        plan_from_feature_catalog as P,
    )
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )

    if not _VENT.is_file():
        pytest.skip("Ventilator corpus file missing")
    body = ImportStep().apply(None, {"path": str(_VENT)}).body
    cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
    bb = cat["initial_bbox_mm"]
    bbox = tuple(float(c) for c in bb[:6])
    box_plan = P._build_plan(
        cat, body=body, base_step_kind="box", base_profile_mode="off"
    )
    base_l, base_w, base_h = bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]
    kept = P.accept_freeform_base(
        box_plan, body, bbox, base_l=base_l, base_w=base_w, base_h=base_h,
    )
    assert kept is not None, "Ventilator must keep a non-box base"
    assert kept.get("base_mechanism") == "freeform_shell"
    base_step = next(s for s in kept["steps"] if str(s["id"]).startswith("s_base"))
    assert base_step["skill"] == "place_freeform_solid"
    # FULL reconstruction — base step ONLY (boundary already carries features).
    assert len(kept["steps"]) == 1


@pytest.mark.slow
def test_non_solidifying_part_reverts_to_box():
    """Revert-safety: when the body's boundary does NOT re-solidify (genuinely
    open shell), the freeform-shell base is not even a candidate, and with no
    silhouette/revolved win either the A/B reverts to box (returns None).

    This is the deterministic revert case — a prismatic SOLID box would also
    re-solidify losslessly (it reproduces its own faces to 0.0 mm), so 'revert'
    for clean prismatic solids means 'the box base is already exact'; the
    honest hard-revert guarantee is keyed on the boundary not closing."""
    from phone_designer.skills.reverse_engineer import (
        plan_from_feature_catalog as P,
    )
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )

    open_body = _open_box_shell()
    # The open boundary must NOT re-solidify — so place_freeform_solid is never
    # a candidate (cheap gate returns False).
    assert P._freeform_shell_solidifies(open_body, 0.1) is False

    cat = ExtractFeatureCatalog().apply(open_body, {}).extras["feature_catalog"]
    bbox = P._body_bbox(open_body)
    box_plan = P._build_plan(
        cat, body=open_body, base_step_kind="box", base_profile_mode="off"
    )
    base_l, base_w, base_h = bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]
    kept = P.accept_freeform_base(
        box_plan, open_body, bbox, base_l=base_l, base_w=base_w, base_h=base_h,
    )
    # No solidifying boundary + no tighter silhouette/revolved → keep box.
    assert kept is None


def test_prismatic_box_freeform_shell_is_lossless(monkeypatch):
    """Honesty note (unit-level, no execution): the freeform-shell A/B keeps a
    candidate ONLY when STRICTLY tighter. A clean prismatic box's box base is
    already exact, so a candidate that merely TIES it must revert — proving the
    guard never replaces an already-exact base on a no-improvement."""
    from phone_designer.skills.reverse_engineer import (
        plan_from_feature_catalog as P,
    )

    box_plan = {"plan_name": "p", "steps": [{"id": "s_base", "skill": "box", "args": {}}]}
    ff_step = {"id": "s_base", "skill": "place_freeform_solid", "args": {}}
    # box exact (0.0) and freeform-shell also exact (0.0) → TIE → revert.
    monkeypatch.setattr(
        P, "_score_reconstruction",
        lambda pd, b, bb: {"box": (1.0, 0.0), "place_freeform_solid": (1.0, 0.0)}[
            pd["steps"][0]["skill"]
        ],
    )
    assert P._accept_freeform_shell_base(
        box_plan, ff_step, object(), (0,) * 6, extra_baseline_haus=None,
    ) is None
