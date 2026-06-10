"""base_step_kind_chooser — synthetic-body tests for the RE base-step heuristic.

Actual decision logic (read from the source):

    1. closed_shells >= assembly_shell_min (default 3)        → "assembly"
    2. face_count <= face_count_box_max (default 50)
       AND solidity >= solidity_box_min (default 0.80)         → "box"
    3. otherwise                                               → "preserve_brep"

A solid cylinder is the canonical near-the-gate body: solidity is exactly
pi/4 ~ 0.785, just UNDER the default 0.80 — so it picks preserve_brep by
default and flips to box when the gate is loosened.
"""
from __future__ import annotations

import pytest
from build123d import Box as B3dBox, Cylinder, Part, Pos
from OCP.BRep import BRep_Builder
from OCP.TopoDS import TopoDS_Compound

from phone_designer.skills.inspect.base_step_kind_chooser import BaseStepKindChooser


# ──────────────────────────────────────────────────────────────────────────────
# Helpers


def _hint(body, args=None):
    res = BaseStepKindChooser().apply(body, args or {})
    return res.extras["base_step_kind_hint"]


def _compound_of(*parts):
    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    for p in parts:
        builder.Add(comp, p.wrapped)
    return Part(comp)


# ──────────────────────────────────────────────────────────────────────────────
# "box" — small, solid


def test_solid_box_chooses_box():
    h = _hint(B3dBox(20, 20, 10))
    assert h["chosen"] == "box"
    assert h["face_count"] == 6
    assert h["closed_shells"] == 1
    assert h["solidity_ratio"] == pytest.approx(1.0, abs=1e-3)
    assert "solidity" in h["rationale"]


# ──────────────────────────────────────────────────────────────────────────────
# "preserve_brep" — solidity gate


def test_solid_cylinder_is_just_under_the_solidity_gate():
    # pi/4 ~ 0.785 < 0.80 → cylinder-dominant bodies DON'T get the box
    # placeholder; they keep their real BREP.
    h = _hint(Cylinder(10, 30))
    assert h["chosen"] == "preserve_brep"
    assert h["solidity_ratio"] == pytest.approx(0.7854, abs=2e-3)
    assert h["face_count"] == 3  # barrel + 2 caps


def test_cylinder_flips_to_box_when_gate_is_loosened():
    h = _hint(Cylinder(10, 30), {"solidity_box_min": 0.7})
    assert h["chosen"] == "box"


def test_thin_walled_frame_chooses_preserve_brep():
    # 20^3 box with a 16x16 through-pocket → solidity 2880/8000 = 0.36.
    frame = B3dBox(20, 20, 20) - B3dBox(16, 16, 30)
    h = _hint(frame)
    assert h["chosen"] == "preserve_brep"
    assert h["solidity_ratio"] == pytest.approx(0.36, abs=1e-3)


# ──────────────────────────────────────────────────────────────────────────────
# "preserve_brep" — face-count gate


def test_face_count_gate_overrides_perfect_solidity():
    # A plain box (6 faces, solidity 1.0) forced over the face budget.
    h = _hint(B3dBox(20, 20, 10), {"face_count_box_max": 5})
    assert h["chosen"] == "preserve_brep"
    assert "face_count" in h["rationale"]


# ──────────────────────────────────────────────────────────────────────────────
# "assembly" — closed-shell multiplicity


def test_three_disjoint_solids_route_to_assembly():
    body = _compound_of(
        B3dBox(10, 10, 10),
        Pos(30, 0, 0) * B3dBox(10, 10, 10),
        Pos(60, 0, 0) * B3dBox(10, 10, 10),
    )
    h = _hint(body)
    assert h["closed_shells"] == 3
    assert h["chosen"] == "assembly"
    assert "assembly_reverse_engineer" in h["rationale"]


def test_two_shells_stay_below_the_default_assembly_gate():
    body = _compound_of(
        B3dBox(10, 10, 10),
        Pos(30, 0, 0) * B3dBox(10, 10, 10),
    )
    h = _hint(body)
    assert h["closed_shells"] == 2
    # 2 < 3 → falls through to the box/preserve gates. Combined bbox is
    # 40x10x10 = 4000 with 2000 mm^3 of material → solidity 0.5 < 0.80.
    assert h["chosen"] == "preserve_brep"


def test_assembly_gate_is_overridable():
    body = _compound_of(
        B3dBox(10, 10, 10),
        Pos(30, 0, 0) * B3dBox(10, 10, 10),
    )
    h = _hint(body, {"assembly_shell_min": 2})
    assert h["chosen"] == "assembly"
