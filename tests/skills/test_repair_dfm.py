"""repair_dfm — analyze->fix loop with hausdorff-guarded, revert-on-no-improve.

Pins the conservative contract: every kept fix must STRICTLY improve the DFM
signal within a bounded geometry change; a no-help fix is reverted verbatim, so
worst-case output == input. Covers the two auto-fix families (internal-corner
fillet via enforce_min_tool_radius, draft via draft_apply_auto), the suggest-only
issues (max_wall_sink), dry-run, and the cross-process / hausdorff / op-failed
revert paths.
"""
from __future__ import annotations

from build123d import (
    Axis,
    Box,
    BuildPart,
    BuildSketch,
    Mode,
    Rectangle,
    extrude,
)

from phone_designer.skills.repair.repair_dfm import RepairDfm


# ── fixtures ────────────────────────────────────────────────────────────────

def _sharp_pocket():
    """A block with a square pocket -> sharp (zero-radius) vertical internal
    corners. Invisible to dfm_verdict's edge-blend check, but enforce_min_tool_
    radius sees them -> the headline fillet auto-fix case."""
    with BuildPart() as bp:
        Box(40, 30, 12)
        with BuildSketch(bp.faces().sort_by(Axis.Z)[-1]):
            Rectangle(16, 10)
        extrude(amount=-8, mode=Mode.SUBTRACT)
    return bp.part


def _straight_wall_boss():
    """A boss with 0-degree-draft vertical walls on a base -> molding draft is
    marginal (below_min_draft>0, no undercut) -> the draft auto-fix case. The
    thick boss over the thin base also trips max_wall_sink (suggest-only)."""
    with BuildPart() as bp:
        Box(40, 40, 4)
        with BuildSketch(bp.faces().sort_by(Axis.Z)[-1]):
            Rectangle(20, 20)
        extrude(amount=14)
    return bp.part


def _clean_box():
    with BuildPart() as bp:
        Box(30, 20, 10)
    return bp.part


def _repair(body, **kw):
    return RepairDfm().apply(body, kw).extras["dfm_repair"]


# ── 1. fillet auto-fix (sharp internal corner) ──────────────────────────────

def test_sharp_pocket_fillet_kept():
    rep = _repair(_sharp_pocket(), processes=["cnc_milling"],
                  pull_direction=[0, 0, 1])
    assert rep["body_changed"] is True
    assert rep["grade"] == "repaired"
    applied = rep["fixes_applied"]
    assert len(applied) == 1
    fix = applied[0]
    assert fix["op"] == "enforce_min_tool_radius"
    assert fix["params"]["edge_count"] >= 4          # the pocket's internal corners
    # margin clears dfm's 1.2x marginal band -> applied radius > tool radius
    assert fix["params"]["min_radius_mm"] > 0.0
    # conservative: a fillet displaces geometry by ~R, well within its budget.
    assert fix["hausdorff_mm"] <= fix["hausdorff_budget_mm"]
    assert rep["total_hausdorff_mm"] <= rep["max_hausdorff_mm"]
    assert rep["result_grade"] == "estimate"


# ── 2. draft auto-fix (sub-min draft, no undercut) ──────────────────────────

def test_straight_wall_draft_kept_and_check_passes():
    rep = _repair(_straight_wall_boss(), processes=["injection_molding"],
                  pull_direction=[0, 0, 1], fix_categories=["draft"])
    assert rep["body_changed"] is True
    drafts = [f for f in rep["fixes_applied"] if f["op"] == "draft_apply_auto"]
    assert len(drafts) == 1
    # the draft check's below_min_draft counter actually drops to 0 (the angle
    # clears the inspector floor, not just the displayed process limit).
    before = next(c for c in rep["before"]["injection_molding"]["checks"]
                  if c["id"] == "draft")
    after = next(c for c in rep["after"]["injection_molding"]["checks"]
                 if c["id"] == "draft")
    assert before["measured"]["below_min_draft"] > 0
    assert after["measured"]["below_min_draft"] == 0
    assert drafts[0]["dfm_before"] == "marginal"
    assert drafts[0]["dfm_after"] == "pass"


def test_both_categories_apply_draft_then_fillet():
    """Draft (bulk form) is applied before fillet (finishing) so both succeed
    on one part — drafting clean walls first, then filleting the corners."""
    rep = _repair(_straight_wall_boss(), processes=["injection_molding"],
                  pull_direction=[0, 0, 1])
    ops = [f["op"] for f in rep["fixes_applied"]]
    assert ops[0] == "draft_apply_auto"
    assert "enforce_min_tool_radius" in ops
    assert rep["fixes_rejected"] == []


# ── 3. suggest-only issues are surfaced, never silently passed ───────────────

def test_max_wall_sink_is_suggest_only():
    rep = _repair(_straight_wall_boss(), processes=["injection_molding"],
                  pull_direction=[0, 0, 1])
    issues = {s["issue"] for s in rep["suggestions"]}
    assert "max_wall_sink" in issues
    for s in rep["suggestions"]:
        assert s["advice"] and s["reason_not_auto"]


# ── 4. conservative: no-help / clean input -> identity, worst==input ─────────

def test_clean_box_no_change_identity():
    body = _clean_box()
    res = RepairDfm().apply(body, {"processes": ["cnc_milling"]})
    rep = res.extras["dfm_repair"]
    assert rep["body_changed"] is False
    assert rep["grade"] in ("no_change", "suggest_only")
    assert rep["total_hausdorff_mm"] == 0.0
    # the returned body IS the input object (byte/geometry identity).
    assert res.body is body


def test_dry_run_changes_nothing():
    body = _sharp_pocket()
    res = RepairDfm().apply(body, {"processes": ["cnc_milling"], "apply": False})
    rep = res.extras["dfm_repair"]
    assert rep["apply"] is False
    assert rep["body_changed"] is False
    assert rep["total_hausdorff_mm"] == 0.0
    assert res.body is body


# ── 5. tight budget forces a revert (never worse) ───────────────────────────

def test_tight_hausdorff_budget_reverts():
    # an impossibly tight ceiling rejects every fix -> identity preserved.
    body = _sharp_pocket()
    res = RepairDfm().apply(body, {"processes": ["cnc_milling"],
                                   "max_hausdorff_mm": 0.001})
    rep = res.extras["dfm_repair"]
    assert rep["body_changed"] is False
    assert rep["grade"] in ("no_change", "suggest_only")
    assert any(r["reason"] == "hausdorff_over_budget"
               for r in rep["fixes_rejected"])
    assert res.body is body


# ── 6. result envelope / honesty invariants ─────────────────────────────────

def test_after_verdict_never_regresses():
    rep = _repair(_sharp_pocket(), processes=["cnc_milling", "injection_molding"])
    sev = {"skipped": 0, "pass": 1, "marginal": 2, "fail": 3}
    for proc, av in rep["after"].items():
        bv = rep["before"][proc]["verdict"]
        assert sev[av["verdict"]] <= sev[bv], (
            f"{proc} regressed {bv} -> {av['verdict']}")


def test_empty_fix_categories_is_suggest_only():
    rep = _repair(_straight_wall_boss(), processes=["injection_molding"],
                  fix_categories=[])
    assert rep["body_changed"] is False
    assert rep["fixes_applied"] == []
    # still analyses + surfaces suggestions
    assert rep["suggestions"]
