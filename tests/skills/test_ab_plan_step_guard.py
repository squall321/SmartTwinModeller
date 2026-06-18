"""'auto' A/B revert-guard plan-size cutoff — _AB_MAX_PLAN_STEPS (2026-06-18).

The multi-candidate freeform/parametric A/B in ``accept_freeform_base`` executes
up to FOUR full reconstruction regens (re-running EVERY feature cut) just to
score candidate bases. On a very complex plan that blows the time budget —
``pythonocc__11752`` (148 box-plan steps) went from a 178 s box reconstruction
to a >1600 s TIMEOUT, *losing the result entirely*. Such parts are also exactly
the ones a single parametric/freeform base can never represent (sparse
multi-body), so the A/B reverts to box anyway.

``accept_freeform_base`` now short-circuits to ``None`` (keep box) when the box
plan exceeds ``_AB_MAX_PLAN_STEPS``. These tests pin that cutoff WITHOUT a
148-feature corpus body: they spy on the first expensive call
(``_classify_base_topology``) and assert it is reached for a small plan but
NEVER reached for an oversized one — proving the guard short-circuits before any
candidate work.
"""
from __future__ import annotations

from build123d import Box

from phone_designer.skills.reverse_engineer import plan_from_feature_catalog as P


def _box_body_and_bbox():
    body = Box(20, 16, 8).wrapped
    bbox = P._body_bbox(body)
    assert bbox is not None
    return body, bbox


def _plan_with_n_steps(n: int) -> dict:
    return {
        "plan_name": "guard_test",
        "steps": [{"id": f"s{i}", "skill": "noop"} for i in range(n)],
    }


def test_oversized_plan_skips_ab_before_any_candidate_work(monkeypatch):
    body, bbox = _box_body_and_bbox()
    seen = {"topo": False}
    orig = P._classify_base_topology
    monkeypatch.setattr(
        P, "_classify_base_topology",
        lambda *a, **k: (seen.__setitem__("topo", True), orig(*a, **k))[1],
    )
    big_plan = _plan_with_n_steps(P._AB_MAX_PLAN_STEPS + 1)

    result = P.accept_freeform_base(big_plan, body, bbox)

    assert result is None, "guard must keep the box plan (return None) on an oversized plan"
    assert seen["topo"] is False, (
        "guard must short-circuit BEFORE _classify_base_topology — no candidate "
        "A/B work may run on an oversized plan"
    )


def test_small_plan_passes_the_guard_into_candidate_logic(monkeypatch):
    body, bbox = _box_body_and_bbox()
    seen = {"topo": False}
    orig = P._classify_base_topology
    monkeypatch.setattr(
        P, "_classify_base_topology",
        lambda *a, **k: (seen.__setitem__("topo", True), orig(*a, **k))[1],
    )
    small_plan = _plan_with_n_steps(2)

    # We don't care about the return (geometry-dependent) — only that a small
    # plan is NOT short-circuited by the guard, i.e. it reaches the real logic.
    P.accept_freeform_base(small_plan, body, bbox)

    assert seen["topo"] is True, "a small plan must NOT trip the plan-size guard"


def test_threshold_margin_separates_winners_from_complex_assembly():
    # The freeform WINNERS in box/complex are 2..~37 box-plan steps (screw 2,
    # linkrods ~22, Ventilator ~23); the complex sparse assembly 11752 is ~148.
    # The cutoff must sit clearly above the winners and below 11752.
    assert 40 <= P._AB_MAX_PLAN_STEPS < 148
