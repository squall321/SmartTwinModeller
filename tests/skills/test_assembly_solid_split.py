"""SOLID-aware split (pillar-perf phase-4, 2026-06-15).

A STEP assembly arrives as one ``TopoDS_Compound`` whose leaves are SOLIDs —
one per physical body. The historic ``split_into_components`` enumerates
*shells*, but a solid with an internal cavity owns TWO shells, so the shell
split over-counts and slices one physical body into two bogus components. The
new ``split_solids=True`` mode explodes the compound into its constituent
SOLIDS instead.

These tests pin:
  1. SPLIT COUNT — a synthetic 5-solid compound yields exactly 5 components in
     SOLID mode, with split_mode='solid'.
  2. CAVITY OVER-COUNT — a solid-with-void (2 shells, 1 solid) yields ONE
     component in SOLID mode but TWO in the historic shell mode, proving the
     fix removes the over-count.
  3. FALLBACK — a body with no solids (a bare open shell) falls back to the
     shell split so the mode never returns an empty catalog by accident.
  4. WIRED THROUGH — assembly_reverse_engineer with split_solids=True reports
     split_mode='solid' and one component per solid.
"""
from __future__ import annotations

from build123d import Box as B3dBox, Part, Pos
from OCP.BRep import BRep_Builder
from OCP.TopoDS import TopoDS_Compound

from phone_designer.skills.assembly._compound import (
    count_solids,
    iter_solid_components,
)
from phone_designer.skills.repair.split_into_components import SplitIntoComponents


def _compound(shapes):
    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    for s in shapes:
        builder.Add(comp, s.wrapped)
    return Part(comp)


def _five_solid_compound():
    """5 disjoint solid boxes in one compound (each a single closed solid)."""
    return _compound([Pos(40 * i, 0, 0) * B3dBox(20, 20, 10) for i in range(5)])


def _solid_with_void():
    """A 30-cube with a 10-cube hollow centre cut out — ONE solid, TWO shells
    (outer wall + inner void wall). The shell split over-counts this to 2."""
    outer = B3dBox(30, 30, 30)
    inner = B3dBox(10, 10, 10)  # centred, fully inside ⇒ a closed internal void
    return outer - inner


def test_iter_solid_components_counts_five():
    body = _five_solid_compound()
    solids = list(iter_solid_components(body.wrapped))
    assert len(solids) == 5
    assert count_solids(body.wrapped) == 5


def test_split_solids_yields_one_component_per_solid():
    body = _five_solid_compound()
    extras = SplitIntoComponents().apply(
        body, {"split_solids": True, "min_volume_mm3": 100.0}
    ).extras
    assert extras["split_mode"] == "solid"
    assert extras["component_count"] == 5
    for c in extras["components"]:
        assert c["volume_mm3"] > 100.0
        assert c["closed"] is True


def test_cavity_solid_overcounts_in_shell_mode_but_not_solid_mode():
    body = _solid_with_void()
    # 1 solid, 2 shells (outer + void).
    assert count_solids(body.wrapped) == 1

    solid_mode = SplitIntoComponents().apply(
        body, {"split_solids": True, "min_volume_mm3": 100.0}
    ).extras
    shell_mode = SplitIntoComponents().apply(
        body, {"split_solids": False, "min_volume_mm3": 100.0}
    ).extras

    assert solid_mode["split_mode"] == "solid"
    assert solid_mode["component_count"] == 1, "one physical body ⇒ one component"

    assert shell_mode["split_mode"] == "shell"
    # The historic shell split sees the inner void wall as a second "shell".
    assert shell_mode["component_count"] >= 2, (
        "shell split over-counts a solid-with-cavity — the bug the SOLID "
        "split fixes"
    )


def test_split_solids_falls_back_to_shell_when_no_solids():
    """A bare open shell (no solids) must fall back to the shell split so the
    SOLID mode never silently returns an empty catalog."""
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    box = B3dBox(20, 20, 10)
    ex = TopExp_Explorer(box.wrapped, TopAbs_SHELL)
    shell = TopoDS.Shell_s(ex.Current())
    bare = Part(shell)  # a shell, not a solid

    assert count_solids(bare.wrapped) == 0
    extras = SplitIntoComponents().apply(
        bare, {"split_solids": True, "min_volume_mm3": 100.0}
    ).extras
    # No solids ⇒ fell back to the shell path and still found the shell.
    assert extras["split_mode"] == "shell"
    assert extras["component_count"] == 1


def test_assembly_re_uses_solid_split_by_default():
    """assembly_reverse_engineer splits by SOLID by default and reports it."""
    from phone_designer.skills.reverse_engineer.assembly_reverse_engineer import (
        AssemblyReverseEngineer,
    )

    body = _five_solid_compound()
    e = AssemblyReverseEngineer().apply(
        body, {"top_n": 10, "max_workers": 1}
    ).extras
    assert e["split_mode"] == "solid"
    # 5 identical solids ⇒ one signature class, RE runs once, 5 components.
    assert e["components_total"] == 5
    assert e["components_processed"] == 5
