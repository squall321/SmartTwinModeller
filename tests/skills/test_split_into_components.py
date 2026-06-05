"""split_into_components + per-component feature catalog — tests.

We don't ship the iPhone teardown mesh in the repo, so the "65 shell"
verification is performed by synthesising a Compound that contains 65
disjoint box-shells. This exercises exactly the same code paths the
iPhone pipeline triggers — TopExp_Explorer(TopAbs_SHELL) enumeration,
BRepCheck_Shell closure check, and per-component detector dispatch.
"""
from __future__ import annotations

from build123d import Part

from phone_designer.skills.create.box import Box
from phone_designer.skills.repair.split_into_components import SplitIntoComponents
from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
    ExtractFeatureCatalog,
)


def _box_part(side: float = 5.0):
    return Box().apply(
        None, {"length_mm": side, "width_mm": side, "height_mm": side},
    ).body


def _translate(shape, dx: float, dy: float = 0.0, dz: float = 0.0):
    """Return a translated copy of ``shape`` (TopoDS_Shape)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec

    t = gp_Trsf()
    t.SetTranslation(gp_Vec(dx, dy, dz))
    tr = BRepBuilderAPI_Transform(shape, t, True)
    tr.Build()
    return tr.Shape()


def _compound_of_n_boxes(n: int):
    """Build a TopoDS_Compound containing ``n`` disjoint box shells.

    Each box is offset along X so their bboxes don't overlap; the result
    is a single Compound that holds ``n`` separate TopoDS_Shell sub-
    shapes (one per box). This is the same topology shape we'd get from
    ``mesh_to_brep`` on a teardown mesh with ``n`` disjoint regions.
    """
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)

    base = Box().apply(
        None, {"length_mm": 5.0, "width_mm": 5.0, "height_mm": 5.0},
    ).body
    for i in range(n):
        # Translate base box along X so bboxes don't overlap.
        shifted = _translate(base.wrapped, dx=float(i * 20))
        builder.Add(comp, shifted)
    return Part(comp)


# ────────────────────────────────────────────────────────────────────────
# split_into_components — basic
# ────────────────────────────────────────────────────────────────────────

def test_split_into_components_on_single_box():
    """A clean Box has exactly one TopoDS_Shell."""
    body = _box_part()
    r = SplitIntoComponents().apply(body, {})
    assert r.body is body  # unchanged
    comps = r.extras["components"]
    assert len(comps) == 1
    assert r.extras["component_count"] == 1
    assert r.extras["closed_component_count"] == 1

    c0 = comps[0]
    assert c0["index"] == 0
    assert c0["shell_count"] == 1
    assert c0["closed"] is True
    assert c0["volume_mm3"] > 0.0
    assert c0["bbox"] is not None and len(c0["bbox"]) == 6
    # body_ref is a usable Part.
    assert c0["body_ref"] is not None
    assert hasattr(c0["body_ref"], "wrapped")


def test_split_into_components_drops_tiny_artefacts():
    """A 0.05 mm cube has volume ~1.25e-4 mm³ — under the default 0.1 floor."""
    tiny = _box_part(side=0.05)
    r = SplitIntoComponents().apply(tiny, {})
    # Default min_volume_mm3=0.1 drops the artefact.
    assert r.extras["component_count"] == 0
    assert r.extras["skipped_small"] == 1
    # Setting the floor to 0 keeps it.
    r2 = SplitIntoComponents().apply(tiny, {"min_volume_mm3": 0.0})
    assert r2.extras["component_count"] == 1


def test_split_into_components_spec_metadata():
    spec = SplitIntoComponents.spec
    assert spec.name == "split_into_components"
    assert spec.category == "repair"
    assert spec.level == "atomic"


# ────────────────────────────────────────────────────────────────────────
# 65-shell synthetic — the iPhone-mirror test
# ────────────────────────────────────────────────────────────────────────

def test_split_into_components_handles_65_shells():
    """The headline scenario — the iPhone teardown mesh produced 65 shells.

    We synthesise the same multi-shell topology and assert the skill
    returns exactly 65 components, each with shell_count == 1.
    """
    compound = _compound_of_n_boxes(65)
    r = SplitIntoComponents().apply(compound, {})
    comps = r.extras["components"]
    assert r.extras["component_count"] == 65
    assert len(comps) == 65
    # Every component is a single, closed shell.
    for i, c in enumerate(comps):
        assert c["index"] == i
        assert c["shell_count"] == 1
        assert c["closed"] is True
        assert c["volume_mm3"] > 0.0


# ────────────────────────────────────────────────────────────────────────
# extract_feature_catalog(per_component=True)
# ────────────────────────────────────────────────────────────────────────

def test_extract_feature_catalog_per_component_on_65_shells():
    """per_component=True runs the detector pipeline on every shell."""
    compound = _compound_of_n_boxes(65)
    r = ExtractFeatureCatalog().apply(
        compound, {"per_component": True, "max_face_count": None},
    )
    extras = r.extras
    # Whole-body catalog still present.
    assert "feature_catalog" in extras
    # Per-component catalogs aggregated.
    assert "per_component_catalogs" in extras
    pcc = extras["per_component_catalogs"]
    assert extras["component_count"] == 65
    assert len(pcc) == 65
    # Each entry references its component index and carries a catalog dict.
    for i, entry in enumerate(pcc):
        assert entry["component_idx"] == i
        cat = entry["catalog"]
        assert isinstance(cat, dict)
        # Either the full catalog (with detector keys) or a too_big skip.
        assert ("holes" in cat) or (cat.get("skipped") is True)


def test_extract_feature_catalog_per_component_off_by_default():
    """Backwards-compat: per_component defaults to False — no new keys."""
    body = _box_part()
    r = ExtractFeatureCatalog().apply(body, {"max_face_count": None})
    assert "feature_catalog" in r.extras
    assert "per_component_catalogs" not in r.extras
