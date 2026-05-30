"""shape repair family — smoke tests.

The repair skills wrap OCCT healing/sewing/upgrade APIs that already have
their own internal correctness — we mostly want to confirm:
    1. the skill loads, validates args, and runs without crashing,
    2. the body survives (body_present post-condition),
    3. extras dict is populated with the documented report keys.

Each test builds a small body (degenerate or vanilla box) and runs the
skill — we don't assert that, e.g., shape_heal removed N edges, since
the input is already clean. The tests do verify the contract surface so
downstream agents can rely on the report dict shape.
"""
from __future__ import annotations

from phone_designer.skills.create.box import Box
from phone_designer.skills.repair.close_shell_to_solid import CloseShellToSolid
from phone_designer.skills.repair.remove_micro_features import RemoveMicroFeatures
from phone_designer.skills.repair.sew_faces_to_shell import SewFacesToShell
from phone_designer.skills.repair.shape_heal import ShapeHeal
from phone_designer.skills.repair.simplify_to_canonical import SimplifyToCanonical


def _box(side: float = 10.0):
    return Box().apply(
        None, {"length_mm": side, "width_mm": side, "height_mm": side},
    ).body


# ----------------------------- shape_heal --------------------------------

def test_shape_heal_runs_on_clean_box():
    body = _box()
    r = ShapeHeal().apply(body, {})
    assert r.body is not None
    rep = r.extras["heal_report"]
    # All four keys present, integer counts, non-negative.
    for k in ("shells_fixed", "faces_fixed", "wires_fixed", "edges_fixed"):
        assert k in rep
        assert isinstance(rep[k], int)
        assert rep[k] >= 0


def test_shape_heal_respects_args():
    body = _box(5.0)
    r = ShapeHeal().apply(body, {
        "precision_mm": 1e-3,
        "fix_small_edges": False,
        "fix_face_orientation": False,
    })
    assert r.body is not None


def test_shape_heal_spec_metadata():
    spec = ShapeHeal.spec
    assert spec.name == "shape_heal"
    assert spec.category == "repair"
    assert spec.level == "atomic"


# ------------------------- sew_faces_to_shell ----------------------------

def test_sew_faces_to_shell_runs_on_box():
    body = _box()
    r = SewFacesToShell().apply(body, {})
    assert r.body is not None
    # Box has 6 faces — they should all have been fed to the sewer.
    assert r.extras["faces_input"] == 6


def test_sew_faces_to_shell_non_manifold_arg():
    body = _box()
    r = SewFacesToShell().apply(body, {"tolerance_mm": 0.01, "non_manifold": True})
    assert r.body is not None


# ------------------------- close_shell_to_solid --------------------------

def test_close_shell_to_solid_promotes_box_shell():
    body = _box()
    r = CloseShellToSolid().apply(body, {})
    assert r.body is not None
    # Box has 1 shell → should produce a solid.
    assert r.extras["shells_consumed"] == 1
    assert r.extras["made_solid"] is True


def test_close_shell_to_solid_no_shell_is_no_op():
    """When the input has no shell we still get a usable body back."""
    # Sewing first then closing is the typical chain; here we just
    # confirm the no-shell fallback path doesn't crash.
    from build123d import Part
    from OCP.TopoDS import TopoDS_Compound, TopoDS_Builder

    comp = TopoDS_Compound()
    TopoDS_Builder().MakeCompound(comp)
    r = CloseShellToSolid().apply(Part(comp), {})
    # No shells were available → result body is whatever we passed in.
    assert r.body is not None
    assert r.extras["shells_consumed"] == 0


# ------------------------- simplify_to_canonical -------------------------

def test_simplify_to_canonical_runs_on_box():
    body = _box()
    r = SimplifyToCanonical().apply(body, {})
    assert r.body is not None
    rep = r.extras["simplify_report"]
    assert rep["faces_before"] == 6
    # A clean box's faces are already canonical — should stay 6 (or fewer
    # if OCCT decides any are co-planar, though that shouldn't happen for
    # a 6-orthogonal-face box).
    assert rep["faces_after"] >= 1
    assert rep["edges_after"] >= 1


def test_simplify_to_canonical_tolerance_arg():
    body = _box()
    r = SimplifyToCanonical().apply(body, {"tolerance_mm": 1e-4})
    assert r.body is not None


# ------------------------- remove_micro_features -------------------------

def test_remove_micro_features_runs_on_box():
    body = _box()
    r = RemoveMicroFeatures().apply(body, {"min_size_mm": 0.01})
    assert r.body is not None
    rep = r.extras["removal_report"]
    # Documented report keys present.
    for k in ("faces_before", "faces_after", "edges_before", "edges_after",
              "wires_touched", "min_size_mm", "approximation"):
        assert k in rep


def test_remove_micro_features_default_args():
    body = _box()
    r = RemoveMicroFeatures().apply(body, {})
    assert r.body is not None
    assert r.extras["removal_report"]["min_size_mm"] == 0.05


# ------------------------- pipeline ---------------------------------------

def test_repair_pipeline_chains():
    """Heal → sew → close — the canonical recovery chain — produces a body."""
    body = _box()
    healed = ShapeHeal().apply(body, {}).body
    sewn = SewFacesToShell().apply(healed, {}).body
    solid = CloseShellToSolid().apply(sewn, {}).body
    assert solid is not None
