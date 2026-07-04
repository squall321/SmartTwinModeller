"""render_preview — atomic, read-only.

Turn the working body into shaded PNG previews so an LLM client (or a Claude
Code session) driving the MCP server can SEE what it modelled — from
``cad_list_skills`` / ``generate_from_spec`` / ``cad_get_skill_schema`` too, not
only the standalone ``cad_preview`` tool.

This skill is a THIN, first-class wrapper over the already-built-and-verified
GL-free raster renderer in ``_render_headless.render_views_to_pngs`` (an OCCT
``BRepMesh`` triangulation rasterized with a numpy z-buffer + Pillow — ZERO GL,
deterministic, CI-safe). It ADDS the skill contract on top:

  * schema-validated args (out_dir / views / size / stem) via pydantic;
  * structured refusals — a None body → fm.no_body, an unknown view name →
    fm.unknown_view (listing the valid set) — validated BEFORE any render, so a
    typo never silently produces a partial/empty result;
  * an honest ``extras['render']`` summary the report/quality pipeline can read.

HONEST labels: a per-view failure (empty mesh, write error) is recorded as that
view = None with the reason in ``note`` — NEVER a fake/black PNG. ``n_rendered``
counts only the views that actually produced a file.

result.extras schema:
    {"render": {
        "images": {view: png_path | None},   # None = honest per-view failure
        "renderer": "headless_raster",
        "n_rendered": int,                    # views that produced a PNG file
        "size": int,                          # square edge in px
        "note": str,                          # GL-free note + any per-view reasons
    }}

Read-only — the body is returned unchanged.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.inspect._render_headless import (
    _VIEWS,
    render_views_to_pngs,
)

# The renderer's supported named cameras — kept in sync by importing _VIEWS so
# fm.unknown_view can never drift from what render_views_to_pngs accepts.
VALID_VIEWS: tuple[str, ...] = tuple(sorted(_VIEWS))


@skill(
    name="render_preview",
    category="inspect",
    level="atomic",
    summary="RENDER shaded PNG previews of the working body from named views "
            "(iso/front/back/right/left/top/bottom) via a GL-free numpy "
            "z-buffer rasterizer — no GPU/GL/display, deterministic. Writes "
            "<out_dir>/<stem>_<view>.png per view. A per-view failure is an "
            "honest None (never a black PNG). Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["render_preview"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.no_body", "fm.unknown_view"],
    cost_hint=0.3,
    result_grade="measured",
    post_conditions=[PostCondition(kind="body_present")],
)
class RenderPreview(SkillBase):
    class Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

        out_dir: str = Field(
            min_length=1,
            description="Directory for the per-view PNGs (created if missing).")
        views: list[str] = Field(
            default_factory=lambda: ["iso", "front", "top"],
            description="Named cameras to render. Valid: "
                        f"{list(VALID_VIEWS)}.")
        size: int = Field(
            default=640, ge=128, le=2048,
            description="Square image edge in pixels.")
        stem: str = Field(
            default="preview",
            min_length=1,
            description="File-name stem — writes <stem>_<view>.png.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        # ── structured refusals (BEFORE any render) ──────────────────────────
        if body is None:
            raise ValueError("fm.no_body: render_preview needs a body.")

        unknown = [v for v in args.views if v not in _VIEWS]
        if unknown:
            raise ValueError(
                f"fm.unknown_view: {unknown} not in valid views "
                f"{list(VALID_VIEWS)}.")

        # ── delegate to the verified GL-free raster renderer ─────────────────
        pv = render_views_to_pngs(
            body, args.out_dir, views=tuple(args.views),
            size=args.size, stem=args.stem)

        images = pv.get("images") or {}
        n_rendered = sum(1 for p in images.values() if p)

        render = {
            "images": images,
            "renderer": pv.get("renderer", "headless_raster"),
            "n_rendered": int(n_rendered),
            "size": int(args.size),
            "note": pv.get("note", ""),
        }
        # read-only — return the SAME body unchanged.
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"render": render},
        )
