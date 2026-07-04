"""render_preview — first-class registered wrapper over the GL-free renderer.

Pins (task card):
  * on a GENERATED box the skill writes iso/front/top PNGs, each > 2 KB, with
    the honest extras['render'] summary (renderer/n_rendered/size/note);
  * an unknown view name -> fm.unknown_view (listing the valid set), BEFORE any
    render;
  * a None body -> fm.no_body;
  * registered as inspect / atomic and reachable through build_manifest (so
    cad_list_skills / cad_get_skill_schema / generate_from_spec all see it);
  * reachable via generate_from_spec as a FINAL step after a box — the PNGs are
    written and the step passes;
  * read-only — the body object is returned UNCHANGED.

Deterministic + GL-free: the renderer is a numpy z-buffer, so these run anywhere
(headless CI). PHONE_DESIGNER_UI_HEADLESS=1 asserts we exercise the no-GL path.
"""
from __future__ import annotations

import os

os.environ.setdefault("PHONE_DESIGNER_UI_HEADLESS", "1")

import json
from pathlib import Path

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.create.generate_from_spec import GenerateFromSpec
from phone_designer.skills.export_manifest import build_manifest
from phone_designer.skills.inspect.render_preview import VALID_VIEWS, RenderPreview

_MIN_PNG_BYTES = 2_048


@pytest.fixture(scope="module")
def box_body():
    """A GENERATED box (via the create skill) — 40×30×20 mm solid."""
    return Box().apply(
        None, {"length_mm": 40.0, "width_mm": 30.0, "height_mm": 20.0}).body


# ── the render artifact ──────────────────────────────────────────────────────

def test_writes_iso_front_top_pngs_over_2kb(box_body, tmp_path):
    ex = RenderPreview().apply(box_body, {"out_dir": str(tmp_path)}).extras
    render = ex["render"]
    assert set(render["images"]) == {"iso", "front", "top"}
    assert render["renderer"] == "headless_raster"
    assert render["n_rendered"] == 3
    assert render["size"] == 640
    for view in ("iso", "front", "top"):
        p = render["images"][view]
        assert p is not None, f"{view} produced no PNG"
        pf = Path(p)
        assert pf.name == f"preview_{view}.png"
        assert pf.exists() and pf.stat().st_size > _MIN_PNG_BYTES, \
            f"{view} PNG only {pf.stat().st_size} bytes"


def test_extras_strict_json_safe(box_body, tmp_path):
    ex = RenderPreview().apply(box_body, {"out_dir": str(tmp_path)}).extras
    json.dumps(ex["render"])  # strict-JSON-safe (no non-serializable objects)


def test_read_only_body_unchanged(box_body, tmp_path):
    result = RenderPreview().apply(box_body, {"out_dir": str(tmp_path)})
    # same object identity back — a read-only inspect skill mutates nothing.
    assert result.body is box_body


def test_custom_size_and_stem(box_body, tmp_path):
    ex = RenderPreview().apply(box_body, {
        "out_dir": str(tmp_path), "views": ["right"], "size": 256,
        "stem": "shot"}).extras
    render = ex["render"]
    assert render["size"] == 256 and render["n_rendered"] == 1
    p = Path(render["images"]["right"])
    # a 256px view is legitimately smaller than the 640px 2KB floor — just
    # assert an honest non-empty PNG with the custom stem.
    assert p.name == "shot_right.png" and p.stat().st_size > 0


# ── structured refusals ──────────────────────────────────────────────────────

def test_unknown_view_refused(box_body, tmp_path):
    with pytest.raises(ValueError, match="fm.unknown_view") as ei:
        RenderPreview().apply(box_body, {
            "out_dir": str(tmp_path), "views": ["iso", "sideways"]})
    # the refusal lists the valid set so the client can self-correct.
    msg = str(ei.value)
    assert "sideways" in msg
    for v in VALID_VIEWS:
        assert v in msg
    # nothing rendered — no partial PNG left behind for the bad request.
    assert not list(tmp_path.glob("*.png"))


def test_none_body_refused(tmp_path):
    with pytest.raises(ValueError, match="fm.no_body"):
        RenderPreview().apply(None, {"out_dir": str(tmp_path)})


# ── registration + reachability ──────────────────────────────────────────────

def test_registered_inspect_atomic_in_manifest():
    skills = {s["name"]: s for s in build_manifest()["skills"]}
    assert "render_preview" in skills, "render_preview not in build_manifest"
    s = skills["render_preview"]
    assert s["category"] == "inspect"
    assert s["level"] == "atomic"
    assert s["result_grade"] == "measured"
    assert "fm.no_body" in s["failure_modes"]
    assert "fm.unknown_view" in s["failure_modes"]
    # the args schema carries the four documented args (reachable via
    # cad_get_skill_schema, which returns exactly this schema).
    props = s["args_schema"]["properties"]
    assert {"out_dir", "views", "size", "stem"} <= set(props)


def test_reachable_via_generate_from_spec_final_step(tmp_path):
    """render_preview runs as the FINAL step of a generate_from_spec build,
    after a box — the PNGs are written and the step passes (read-only, so the
    solid still validates)."""
    ex = GenerateFromSpec().apply(None, {"spec": [
        {"op": "box", "args": {
            "length_mm": 40.0, "width_mm": 30.0, "height_mm": 20.0}},
        {"op": "render_preview", "args": {
            "out_dir": str(tmp_path), "views": ["iso", "front"]}},
    ]}).extras["generated"]
    assert ex["is_solid"] is True and ex["ok"] is True
    steps = {st["op"]: st["status"] for st in ex["steps"]}
    assert steps["render_preview"] == "pass"
    pngs = sorted(p.name for p in tmp_path.glob("*.png"))
    assert pngs == ["preview_front.png", "preview_iso.png"]
    for p in tmp_path.glob("*.png"):
        assert p.stat().st_size > _MIN_PNG_BYTES
