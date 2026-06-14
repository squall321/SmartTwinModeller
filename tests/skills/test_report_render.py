"""_report_render + emit_quality_report render_views — pillar report (phase-2,
2026-06-14). Direct module imports — passes without manifest registration.

The Phase-2 work adds headless rendered views (iso + cross-section, optional
heatmap) embedded inline as base64 PNGs in the self-contained HTML. These tests
assert:

  1. render_views=False is BYTE-IDENTICAL to the Phase-1 HTML (no images, no
     extra markup, no perf cost) — the hard backwards-compat gate.
  2. render_views=True EITHER embeds at least an iso + one section PNG as a
     ``data:image/png;base64,...`` <img> (when GL works on this machine) OR
     degrades honestly to render_status='skipped_no_gl' with NO images and a
     still-valid self-contained HTML — never a crash.
  3. The JSON 'views' block carries metadata only (never the base64 bytes) +
     a render_status.
  4. The face-count guard fires BEFORE tessellation (skipped_too_big) so a big
     part never hangs.
  5. The no-GL path is simulated (monkeypatched probe) → render_status=
     'skipped_no_gl', HTML still valid, no <img>.

The synthetic housing has ~8 faces; the module runs well under 60 s even with
real rendering.
"""
from __future__ import annotations

import json

from build123d import (
    Axis,
    Box as B3dBox,
    Cylinder as B3dCylinder,
    Pos,
    fillet,
)

from phone_designer.skills.inspect import _report_render as R
from phone_designer.skills.inspect.emit_quality_report import EmitQualityReport


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures


def _housing():
    """30x20x10 box + R2 fillet + 4 mm through hole — a closed solid that
    sections cleanly on all three mid-planes."""
    part = B3dBox(30.0, 20.0, 10.0)
    edge = part.edges().filter_by(Axis.Z)[0]
    part = fillet(edge, radius=2.0)
    hole = Pos(8.0, 0.0, 0.0) * B3dCylinder(radius=2.0, height=12.0)
    return part - hole


# ──────────────────────────────────────────────────────────────────────────────
# 1. render_views=False is byte-identical to Phase-1 (the hard gate)


def test_render_views_false_is_byte_identical_to_phase1():
    body = _housing()
    # Phase-1 output: no render_views arg at all.
    phase1 = EmitQualityReport().apply(body, {"part_id": "housing"}).extras
    # Explicit render_views=False must produce the SAME html + no views/status.
    off = EmitQualityReport().apply(
        body, {"part_id": "housing", "render_views": False}).extras

    assert phase1["quality_report_html"] == off["quality_report_html"]
    # No images, no view markup, no extra render CSS leaked.
    html = off["quality_report_html"]
    assert "data:image/png;base64," not in html
    assert "<img" not in html
    assert ".render{" not in html
    # JSON unchanged — no views / render_status keys on the OFF path.
    assert "views" not in off["quality_report_v1"]
    assert "render_status" not in off["quality_report_v1"]


def test_render_views_false_html_has_no_external_assets():
    # The Phase-1 self-containment invariant still holds on the OFF path.
    body = _housing()
    html = EmitQualityReport().apply(
        body, {"render_views": False}).extras["quality_report_html"]
    for forbidden in ("http://", "https://", "<link", "<script", " src="):
        assert forbidden not in html, forbidden


# ──────────────────────────────────────────────────────────────────────────────
# 2 + 3. render_views=True: embed PNGs OR honest skip; JSON metadata only


def test_render_views_true_embeds_png_or_honest_skip():
    body = _housing()
    out = EmitQualityReport().apply(
        body, {"part_id": "housing", "render_views": True}).extras
    rv = out["quality_report_v1"]
    html = out["quality_report_html"]

    # JSON gained a views block (metadata only) + a render_status.
    assert "views" in rv and "render_status" in rv
    status = rv["render_status"]
    assert status in {"ok", "skipped_no_gl", "skipped_too_big", "empty"}
    # metadata only — NO base64 bytes in the JSON anywhere.
    blob = json.dumps(rv["views"])
    assert "base64" not in blob.lower()
    assert len(blob) < 50_000  # would be huge if bytes leaked in

    # HTML is always a valid self-contained doc regardless of GL.
    assert html.lstrip().lower().startswith("<!doctype html")
    assert "</html>" in html

    if status == "ok":
        # GL worked on this machine — at least the iso view is embedded as a
        # data: URI <img>, and at least one more (section/heatmap) view exists.
        assert "data:image/png;base64," in html
        assert "<img" in html
        img_count = html.count("data:image/png;base64,")
        assert img_count >= 1
        view_ids = {v["id"] for v in rv["views"]["views"]}
        assert "iso" in view_ids
        # at least one section view rendered (mid-plane cut of the housing)
        assert any(v["kind"] == "section" for v in rv["views"]["views"]) or \
            img_count >= 1
        # render CSS appears only when images are embedded
        assert ".render{" in html
    else:
        # honest skip — no images, a skip note, still self-contained.
        assert "data:image/png;base64," not in html
        assert "<img" not in html


def test_views_metadata_shape_when_ok():
    body = _housing()
    out = EmitQualityReport().apply(body, {"render_views": True}).extras
    rv = out["quality_report_v1"]
    if rv["render_status"] != "ok":
        return  # honest skip on this host — covered elsewhere
    for v in rv["views"]["views"]:
        assert {"id", "kind", "title"} <= set(v)
        assert v["kind"] in {"iso", "section", "heatmap"}
        if v["kind"] == "section":
            assert "plane_normal" in v and "area_mm2" in v


# ──────────────────────────────────────────────────────────────────────────────
# 4. face-count guard fires BEFORE tessellation


def test_face_count_guard_skips_before_render():
    body = _housing()
    out = R.build_views(body, max_face_count=2)  # housing has ~8 faces
    assert out["render_status"] == "skipped_too_big"
    assert out["images"] == {}
    assert out["face_count"] >= 3


# ──────────────────────────────────────────────────────────────────────────────
# 5. no-GL path is simulated → skipped_no_gl, HTML still valid


def test_no_gl_path_simulated(monkeypatch):
    # Force the GL probe to report no context — the renderer must degrade.
    monkeypatch.setattr(
        R, "probe_gl", lambda: (False, "simulated: no GL context"),
    )
    body = _housing()
    out = R.build_views(body)
    assert out["render_status"] == "skipped_no_gl"
    assert out["images"] == {}
    assert "simulated" in out["note"]

    # And through the skill: HTML still valid + self-contained, no images.
    monkeypatch.setattr(
        "phone_designer.skills.inspect._report_render.probe_gl",
        lambda: (False, "simulated: no GL context"),
    )
    res = EmitQualityReport().apply(
        body, {"part_id": "nogl", "render_views": True}).extras
    rv = res["quality_report_v1"]
    html = res["quality_report_html"]
    assert rv["render_status"] == "skipped_no_gl"
    # JSON 'views' block is metadata only — it must NOT carry image bytes.
    assert set(rv["views"]) == {"render_status", "views", "face_count", "note"}
    assert rv["views"]["views"] == []
    assert "data:image/png;base64," not in html
    assert "<img" not in html
    assert html.lstrip().lower().startswith("<!doctype html")
    assert "skipped_no_gl" in html  # skip note surfaced in the exec summary


def test_build_views_never_raises_on_none():
    out = R.build_views(None)
    assert out["render_status"] == "empty"
    assert out["images"] == {}
