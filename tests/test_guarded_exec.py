"""test_guarded_exec — hang-proof warm-worker plan execution (Track 1-3b).

Covers the four contract verifications:
  (a) inline mode (timeout_s=0): box spec -> STEP written, volume in generated
  (b) worker mode: same spec twice through the warm worker; the SECOND call is
      faster (warm registry) and both ok
  (c) timeout: __test_sleep__ 30 s with timeout_s=2 -> fm.timeout in ~seconds,
      and a follow-up call works (respawn proven)
  (d) crash: __test_exit__ -> fm.worker_crash, next call recovers
plus the structured refusals (fm.bad_args / env-guarded test ops / inline
__test_exit__ refusal) and the spawn-failure inline fallback.

Worker tests are marked slow — each cold worker spawn pays the ~30-40 s
skill-registry warm import once.
"""
from __future__ import annotations

import os

# Both BEFORE any phone_designer import / worker spawn: the worker inherits
# the parent env at Popen time, so the test-ops gate must already be open.
os.environ.setdefault("PHONE_DESIGNER_UI_HEADLESS", "1")
os.environ["PHONE_DESIGNER_ALLOW_TEST_OPS"] = "1"

import json
import time

import pytest

from phone_designer.mcp_support import _guarded_exec as gx

BOX_SPEC = [{"op": "box",
             "args": {"length_mm": 10.0, "width_mm": 20.0, "height_mm": 5.0}}]
BOX_VOLUME = 10.0 * 20.0 * 5.0


@pytest.fixture(scope="module", autouse=True)
def _kill_worker_after_module():
    yield
    gx.shutdown_worker()


def _ensure_warm(tmp_path) -> None:
    """Guarantee a live warm worker so timed assertions exclude spawn cost."""
    r = gx.run_guarded_plan(BOX_SPEC, out_dir=str(tmp_path), name="warmup",
                            timeout_s=300)
    assert r["ok"] is True, r


# ── (a) inline mode ───────────────────────────────────────────────────────────


def test_inline_box_builds_step(tmp_path):
    res = gx.run_guarded_plan(BOX_SPEC, out_dir=str(tmp_path),
                              name="box_inline", timeout_s=0)
    assert res["ok"] is True
    assert res["step_path"] and os.path.exists(res["step_path"])
    g = res["generated"]
    assert g["mode"] == "generate"
    assert g["ok"] is True and g["is_solid"] is True
    assert abs(g["volume_mm3"] - BOX_VOLUME) < 1.0
    assert g["n_steps"] == 1 and g["n_ok"] == 1
    # STRICT-JSON safety of the whole return
    json.dumps(res, allow_nan=False)


def test_inline_modify_from_step(tmp_path):
    seed = gx.run_guarded_plan(BOX_SPEC, out_dir=str(tmp_path), name="seed",
                               timeout_s=0)
    assert seed["ok"] and seed["step_path"]
    res = gx.run_guarded_plan(
        [{"op": "scale_body", "args": {"factor": 2.0}}],
        initial_step_path=seed["step_path"],
        out_dir=str(tmp_path), name="scaled", timeout_s=0)
    assert res["ok"] is True, res
    g = res["generated"]
    assert g["mode"] == "modify"
    assert g["is_solid"] is True
    # uniform x2 -> volume x8 (small STEP round-trip tolerance)
    assert abs(g["volume_mm3"] - BOX_VOLUME * 8.0) < 8.0
    assert res["step_path"] and os.path.exists(res["step_path"])
    json.dumps(res, allow_nan=False)


def test_inline_unknown_op_isolated(tmp_path):
    res = gx.run_guarded_plan([{"op": "definitely_not_a_skill", "args": {}}],
                              out_dir=str(tmp_path), name="bad_op",
                              timeout_s=0)
    # pipeline COMPLETED (ok True) but honestly graded a failed build
    assert res["ok"] is True
    g = res["generated"]
    assert g["ok"] is False
    assert any("unknown skill" in e for e in g["spec_errors"])
    assert res["step_path"] is None
    json.dumps(res, allow_nan=False)


def test_inline_missing_initial_step_is_exec_error(tmp_path):
    res = gx.run_guarded_plan(
        [], initial_step_path=str(tmp_path / "nope.step"),
        out_dir=str(tmp_path), name="missing", timeout_s=0)
    assert res["ok"] is False
    assert res["error"].startswith("fm.exec_error:")


def test_bad_args_refused():
    with pytest.raises(ValueError, match="fm.bad_args"):
        gx.run_guarded_plan("not a list", out_dir="x")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="fm.bad_args"):
        gx.run_guarded_plan(BOX_SPEC, out_dir="")
    with pytest.raises(ValueError, match="fm.bad_args"):
        gx.run_guarded_plan(BOX_SPEC, out_dir="x", timeout_s=-1)
    with pytest.raises(ValueError, match="fm.bad_args"):
        gx.run_guarded_plan(
            [{"op": "box", "args": {"length_mm": float("nan")}}],
            out_dir="x", timeout_s=0)


def test_test_ops_env_guarded(tmp_path, monkeypatch):
    monkeypatch.delenv("PHONE_DESIGNER_ALLOW_TEST_OPS", raising=False)
    res = gx.run_guarded_plan(
        [{"op": "__test_sleep__", "args": {"seconds": 0}}],
        out_dir=str(tmp_path), name="guarded", timeout_s=0)
    assert res["ok"] is False
    assert "PHONE_DESIGNER_ALLOW_TEST_OPS" in res["error"]


def test_inline_test_exit_refused(tmp_path):
    # __test_exit__ inline would kill THIS process — must be a refusal.
    res = gx.run_guarded_plan([{"op": "__test_exit__", "args": {}}],
                              out_dir=str(tmp_path), name="noexit",
                              timeout_s=0)
    assert res["ok"] is False
    assert res["error"].startswith("fm.exec_error:")
    assert "__test_exit__" in res["error"]


# ── (b) worker mode + warmth ──────────────────────────────────────────────────


@pytest.mark.slow
def test_worker_box_twice_second_call_warm(tmp_path):
    gx.shutdown_worker()  # force a cold spawn so t1 includes the warm-up
    t0 = time.perf_counter()
    r1 = gx.run_guarded_plan(BOX_SPEC, out_dir=str(tmp_path), name="w1",
                             timeout_s=300)
    t1 = time.perf_counter() - t0
    t0 = time.perf_counter()
    r2 = gx.run_guarded_plan(BOX_SPEC, out_dir=str(tmp_path), name="w2",
                             timeout_s=300)
    t2 = time.perf_counter() - t0
    assert r1["ok"] is True and r2["ok"] is True
    assert os.path.exists(r1["step_path"]) and os.path.exists(r2["step_path"])
    assert abs(r2["generated"]["volume_mm3"] - BOX_VOLUME) < 1.0
    # warmth: call 1 paid the ~30-40 s registry import, call 2 must not
    assert t2 < t1, f"second call not warm: t1={t1:.1f}s t2={t2:.1f}s"
    json.dumps(r2, allow_nan=False)


# ── (c) timeout -> kill -> respawn ────────────────────────────────────────────


@pytest.mark.slow
def test_worker_timeout_then_respawn(tmp_path):
    _ensure_warm(tmp_path)
    t0 = time.perf_counter()
    res = gx.run_guarded_plan(
        [{"op": "__test_sleep__", "args": {"seconds": 30}}],
        out_dir=str(tmp_path), name="sleepy", timeout_s=2)
    elapsed = time.perf_counter() - t0
    assert res["ok"] is False
    assert res["error"].startswith("fm.timeout:")
    assert "worker respawned" in res["error"]
    assert elapsed < 8.0, f"timeout not enforced promptly ({elapsed:.1f}s)"
    # follow-up call proves the respawn (pays one warm-up again)
    follow = gx.run_guarded_plan(BOX_SPEC, out_dir=str(tmp_path),
                                 name="after_timeout", timeout_s=300)
    assert follow["ok"] is True, follow
    assert os.path.exists(follow["step_path"])


# ── (d) crash -> fm.worker_crash -> recover ──────────────────────────────────


@pytest.mark.slow
def test_worker_crash_then_recover(tmp_path):
    _ensure_warm(tmp_path)
    res = gx.run_guarded_plan([{"op": "__test_exit__", "args": {"code": 7}}],
                              out_dir=str(tmp_path), name="crash",
                              timeout_s=60)
    assert res["ok"] is False
    assert res["error"].startswith("fm.worker_crash:")
    follow = gx.run_guarded_plan(BOX_SPEC, out_dir=str(tmp_path),
                                 name="after_crash", timeout_s=300)
    assert follow["ok"] is True, follow
    assert os.path.exists(follow["step_path"])


# ── spawn failure -> inline fallback ─────────────────────────────────────────


def test_spawn_failure_falls_back_inline(tmp_path, monkeypatch):
    gx.shutdown_worker()

    def _boom():
        raise RuntimeError("no interpreter for you")

    monkeypatch.setattr(gx, "_spawn_worker", _boom)
    res = gx.run_guarded_plan(BOX_SPEC, out_dir=str(tmp_path),
                              name="fallback", timeout_s=30)
    assert res["ok"] is True, res
    assert os.path.exists(res["step_path"])
    assert any("fm.worker_spawn_failed" in w for w in res.get("warnings", []))
    json.dumps(res, allow_nan=False)
