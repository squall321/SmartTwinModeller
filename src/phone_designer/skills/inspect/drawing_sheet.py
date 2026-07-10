"""drawing_sheet — macro, read-only. Third-angle engineering drawing sheet.

ONE call → a self-contained SVG-in-HTML drawing sheet (same self-contained
HTML convention as emit_quality_report: inline CSS only, no external assets)
plus a layered DXF (VISIBLE/HIDDEN) per projection view:

  * third-angle FRONT / TOP / RIGHT + ISO views via ``hlr_view`` (exact HLR,
    per-view face budget + honest brute-silhouette fallback labelled
    ``non_cut_ready``; one bad view NEVER kills the sheet — it is recorded
    with its raw error and the others still render);
  * optional SECTION view (reuses ``cross_section``, in-plane projected —
    per-edge polylines are chained end-to-end and CLOSED loops are filled
    with 45° hatch lines clipped by the even-odd rule, so inner loops
    (holes) stay clear; open profiles stay outline-only, unhatched);
  * title block (part name / date / scale / projection) with the fixed
    ``DRAFT FOR REVIEW`` label baked into the artifact (SVG watermark + title
    block + HTML banner);
  * dimensions as a TABLE from ``auto_dimension`` rows — row count is exactly
    ``len(key_dimensions)`` — with anchored callouts (D-refs at the hole
    centres on the FRONT view). v1 scope: anchored callouts + table only,
    NO automatic leader placement (per roadmap REJECT #6);
  * hole table from ``classify_holes`` (match_standards=True → standard
    thread spec per hole where one matches).

Writes ``{out_dir}/{part_name}_drawing.html`` and
``{out_dir}/{part_name}_{view}.dxf``. Read-only — the body is unchanged; only
the filesystem is touched.

Honest limits (also stated inside the artifact): this is a DRAFT for review,
not a released drawing — no leader lines, no GD&T frames on the sheet,
section hatch covers CLOSED loops only (open section profiles stay
unhatched outline), ISO view is not-to-scale, and fallback views carry
``non_cut_ready``.
"""
from __future__ import annotations

import datetime as _dt
import math
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.inspect.hlr_view import (
    DEFAULT_HLR_MAX_FACES,
    HlrView,
    sheet_basis,
)

DRAFT_LABEL = "DRAFT FOR REVIEW"

# Third-angle view set: (name, view_direction camera→body, sheet-up hint).
_VIEWS: list[tuple[str, tuple[float, float, float], tuple[float, float, float]]] = [
    ("front", (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    ("top", (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    ("right", (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    ("iso", (-0.5773502692, -0.5773502692, -0.5773502692), (0.0, 0.0, 1.0)),
]

# A3 landscape sheet, mm coordinates.
_SHEET_W, _SHEET_H = 420.0, 297.0
# 2×2 view grid cells: (x, y, w, h) — third angle: TOP above FRONT,
# RIGHT to the right of FRONT, ISO top-right corner.
_CELLS = {
    "top": (10.0, 10.0, 195.0, 118.0),
    "iso": (210.0, 10.0, 195.0, 118.0),
    "front": (10.0, 132.0, 195.0, 118.0),
    "right": (210.0, 132.0, 195.0, 118.0),
}
_SECTION_CELL = (275.0, 254.0, 130.0, 36.0)  # small inset next to title block
_CELL_PAD = 14.0  # keeps room for the view label under the geometry

# Standard drawing scales (drawing_mm per model_mm).
_SCALE_LADDER = [10.0, 5.0, 2.0, 1.0, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01]

_CSS = """
body { font-family: 'Segoe UI', Arial, sans-serif; background: #f2f2f2;
       margin: 0; padding: 16px; color: #111; }
.banner { background: #b00020; color: #fff; font-weight: 700;
          padding: 8px 14px; margin-bottom: 12px; border-radius: 4px; }
.sheet { background: #fff; box-shadow: 0 1px 6px rgba(0,0,0,.25);
         display: block; margin: 0 auto 16px auto; max-width: 1280px; }
svg .pl-vis { fill: none; stroke: #111; stroke-width: 0.35; }
svg .pl-hid { fill: none; stroke: #555; stroke-width: 0.22;
              stroke-dasharray: 2 1.2; }
svg .pl-out { fill: none; stroke: #111; stroke-width: 0.35; }
svg .pl-out-h { fill: none; stroke: #555; stroke-width: 0.22;
                stroke-dasharray: 2 1.2; }
svg .pl-sec { fill: none; stroke: #06437a; stroke-width: 0.3; }
svg .hatch line { stroke: #06437a; stroke-width: 0.1; }
svg .lbl { font-family: Arial, sans-serif; font-size: 4px; fill: #111;
           text-anchor: middle; }
svg .lbl-warn { font-family: Arial, sans-serif; font-size: 3px; fill: #b00020;
                text-anchor: middle; }
svg .tb { font-family: Arial, sans-serif; font-size: 3.2px; fill: #111; }
svg .tb-big { font-family: Arial, sans-serif; font-size: 6px; fill: #b00020;
              font-weight: bold; }
svg .callout { font-family: Arial, sans-serif; font-size: 2.6px;
               fill: #b00020; text-anchor: middle; }
svg .watermark { font-family: Arial, sans-serif; font-size: 22px;
                 fill: #b00020; opacity: 0.12; text-anchor: middle;
                 font-weight: bold; }
table { border-collapse: collapse; background: #fff; margin-bottom: 16px; }
th, td { border: 1px solid #999; padding: 3px 10px; font-size: 13px;
         text-align: left; }
th { background: #e8e8e8; }
h2 { font-size: 16px; margin: 12px 0 6px 0; }
.note { color: #555; font-size: 12px; }
footer { color: #555; font-size: 11px; margin-top: 12px; }
"""


def _json_safe(obj: Any) -> Any:
    """Strict-JSON safety: non-finite floats → None (house rule)."""
    if isinstance(obj, float):
        return round(obj, 6) if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _esc(text: Any) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _safe_stem(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "part"
    return stem


def _scale_note(s: float) -> str:
    if s >= 1.0:
        r = s if abs(s - round(s)) > 1e-9 else int(round(s))
        return f"{r}:1"
    inv = 1.0 / s
    r = inv if abs(inv - round(inv)) > 1e-9 else int(round(inv))
    return f"1:{r}"


def _pick_scale(views: list[dict[str, Any]]) -> tuple[float, str]:
    """Common standard scale for the ortho views (front/top/right)."""
    s_fit = math.inf
    for v in views:
        if v.get("status") != "ok" or v["name"] == "iso":
            continue
        u0, v0, u1, v1 = v["extras"]["extent_uv"]
        eu, ev = max(u1 - u0, 1e-6), max(v1 - v0, 1e-6)
        _, _, cw, ch = _CELLS[v["name"]]
        s_fit = min(s_fit,
                    (cw - 2 * _CELL_PAD) / eu,
                    (ch - 2 * _CELL_PAD) / ev)
    if not math.isfinite(s_fit):
        return 1.0, "1:1"
    for s in _SCALE_LADDER:
        if s <= s_fit:
            return s, _scale_note(s)
    return s_fit, f"{_scale_note(s_fit)} (non-standard, auto-fit)"


def _fit_scale(extent: list[float], cell: tuple[float, float, float, float]) -> float:
    u0, v0, u1, v1 = extent
    eu, ev = max(u1 - u0, 1e-6), max(v1 - v0, 1e-6)
    _, _, cw, ch = cell
    return min((cw - 2 * _CELL_PAD) / eu, (ch - 2 * _CELL_PAD) / ev)


def _view_to_svg(view: dict[str, Any], cell, scale: float,
                 callouts: list[dict[str, Any]] | None = None) -> str:
    """One view's polylines → an SVG <g> centred in its cell (y flipped)."""
    ex = view["extras"]
    cx, cy = cell[0] + cell[2] / 2.0, cell[1] + cell[3] / 2.0
    u0, v0, u1, v1 = ex["extent_uv"]
    mu, mv = (u0 + u1) / 2.0, (v0 + v1) / 2.0

    def _pts(poly) -> str:
        return " ".join(
            f"{round(cx + (p[0] - mu) * scale, 3)},"
            f"{round(cy - (p[1] - mv) * scale, 3)}"
            for p in poly)

    parts: list[str] = [f'<g data-view="{_esc(view["name"])}">']
    outline = ex.get("outline", {})
    for polys, cls in [
        (ex.get("hidden_polylines_2d", []), "pl-hid"),
        (outline.get("hidden_polylines_2d", []), "pl-out-h"),
        (ex.get("visible_polylines_2d", []), "pl-vis"),
        (outline.get("visible_polylines_2d", []), "pl-out"),
    ]:
        for poly in polys:
            if len(poly) >= 2:
                parts.append(f'<polyline class="{cls}" points="{_pts(poly)}"/>')

    for c in (callouts or []):
        ax = round(cx + (c["u"] - mu) * scale, 3)
        ay = round(cy - (c["v"] - mv) * scale, 3)
        parts.append(f'<circle cx="{ax}" cy="{ay}" r="2.0" fill="none" '
                     'stroke="#b00020" stroke-width="0.25"/>')
        parts.append(f'<text class="callout" x="{ax}" y="{ay + 0.9}">'
                     f'{_esc(c["ref"])}</text>')

    label = view["title"]
    parts.append(f'<text class="lbl" x="{cx}" '
                 f'y="{round(cell[1] + cell[3] - 3.0, 3)}">{_esc(label)}</text>')
    if view["extras"].get("label") == "non_cut_ready":
        parts.append(
            f'<text class="lbl-warn" x="{cx}" '
            f'y="{round(cell[1] + cell[3] - 8.0, 3)}">'
            "NON-CUT-READY (silhouette fallback — hidden lines not computed)"
            "</text>")
    parts.append("</g>")
    return "\n".join(parts)


# ── section hatching (45°, even-odd) ────────────────────────────────────────
_HATCH_SPACING_MM = 2.5    # perpendicular spacing in MODEL mm (~2-3 mm at 1:1)
_HATCH_MAX_LINES = 400     # huge parts: widen spacing instead of exploding SVG
_CHAIN_TOL = 1e-3          # endpoint stitch tolerance (points rounded to 1e-4)


def _d2(a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _chain_closed_loops(
    polys: list[list[tuple[float, float]]],
) -> tuple[list[list[tuple[float, float]]], list[list[tuple[float, float]]]]:
    """Stitch per-edge 2D polylines end-to-end → (closed_loops, open_profiles).

    ``cross_section`` returns ONE polyline per section edge (straight edges
    are open segments; circles may come back split at the cylinder seam), so
    loop closure is a property of the CHAINED profile, not of the raw edge.
    """
    t2 = _CHAIN_TOL * _CHAIN_TOL
    closed: list[list[tuple[float, float]]] = []
    rest: list[list[tuple[float, float]]] = []
    for p in polys:
        pts = [(float(q[0]), float(q[1])) for q in p]
        if len(pts) < 2:
            continue
        if len(pts) >= 4 and _d2(pts[0], pts[-1]) <= t2:
            closed.append(pts)
        else:
            rest.append(pts)
    open_profiles: list[list[tuple[float, float]]] = []
    while rest:
        chain = rest.pop(0)
        grew = True
        while grew and not (len(chain) >= 4 and _d2(chain[0], chain[-1]) <= t2):
            grew = False
            for i, q in enumerate(rest):
                if _d2(chain[-1], q[0]) <= t2:
                    chain = chain + q[1:]
                elif _d2(chain[-1], q[-1]) <= t2:
                    chain = chain + q[-2::-1]
                elif _d2(chain[0], q[-1]) <= t2:
                    chain = q[:-1] + chain
                elif _d2(chain[0], q[0]) <= t2:
                    chain = q[::-1][:-1] + chain
                else:
                    continue
                rest.pop(i)
                grew = True
                break
        if len(chain) >= 4 and _d2(chain[0], chain[-1]) <= t2:
            closed.append(chain)
        else:
            open_profiles.append(chain)
    return closed, open_profiles


def _hatch_segments_45(
    closed_loops: list[list[tuple[float, float]]],
    spacing: float = _HATCH_SPACING_MM,
) -> tuple[list[tuple[tuple[float, float], tuple[float, float]]], float | None]:
    """Clip the 45° line family against the closed-loop set (even-odd rule).

    Each hatch line ``v = u + c`` is intersected with EVERY closed-loop
    segment; crossings are sorted along the line and paired even-odd, so
    material (odd winding) is filled and inner loops (holes) stay clear.
    Pure python — no new deps. Returns (segments, effective_spacing_mm).
    """
    if not closed_loops:
        return [], None
    edges = [(a, b) for lp in closed_loops for a, b in zip(lp, lp[1:])
             if _d2(a, b) > 0.0]
    if not edges:
        return [], None
    us = [p[0] for lp in closed_loops for p in lp]
    vs = [p[1] for lp in closed_loops for p in lp]
    c_lo, c_hi = min(vs) - max(us), max(vs) - min(us)  # offsets of v = u + c
    if not (c_hi - c_lo > 0.0):
        return [], None
    step = spacing * math.sqrt(2.0)  # Δc for a perpendicular gap == spacing
    if (c_hi - c_lo) / step > _HATCH_MAX_LINES:
        step = (c_hi - c_lo) / _HATCH_MAX_LINES
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    k = 0
    c = c_lo + 0.5 * step
    while c < c_hi:
        ts: list[float] = []
        for a, b in edges:
            sa, sb = a[1] - a[0] - c, b[1] - b[0] - c
            if (sa > 0.0) == (sb > 0.0):  # s == 0 counts as the negative side
                continue
            f = sa / (sa - sb)
            ts.append(a[0] + f * (b[0] - a[0]))
        ts.sort()
        for i in range(0, len(ts) - 1, 2):  # even-odd pairing along the line
            if ts[i + 1] - ts[i] > 0.05:    # drop sub-0.05 mm slivers
                segs.append(((ts[i], ts[i] + c), (ts[i + 1], ts[i + 1] + c)))
        k += 1
        c = c_lo + (k + 0.5) * step
    return segs, round(step / math.sqrt(2.0), 6)


def _section_to_svg(section: dict[str, Any], cell) -> str:
    loops = section.get("polylines_2d", [])
    if not loops:
        return ""
    u0 = min(p[0] for lp in loops for p in lp)
    u1 = max(p[0] for lp in loops for p in lp)
    v0 = min(p[1] for lp in loops for p in lp)
    v1 = max(p[1] for lp in loops for p in lp)
    scale = min((cell[2] - 10.0) / max(u1 - u0, 1e-6),
                (cell[3] - 10.0) / max(v1 - v0, 1e-6))
    cx, cy = cell[0] + cell[2] / 2.0, cell[1] + cell[3] / 2.0
    mu, mv = (u0 + u1) / 2.0, (v0 + v1) / 2.0
    parts = ['<g data-view="section">']
    hatch = section.get("hatch_segments_2d") or []
    if hatch:  # under the outline strokes; even-odd keeps holes clear
        parts.append('<g class="hatch">')
        for a, b in hatch:
            parts.append(
                f'<line x1="{round(cx + (a[0] - mu) * scale, 3)}" '
                f'y1="{round(cy - (a[1] - mv) * scale, 3)}" '
                f'x2="{round(cx + (b[0] - mu) * scale, 3)}" '
                f'y2="{round(cy - (b[1] - mv) * scale, 3)}"/>')
        parts.append("</g>")
    for lp in loops:
        pts = " ".join(
            f"{round(cx + (p[0] - mu) * scale, 3)},"
            f"{round(cy - (p[1] - mv) * scale, 3)}" for p in lp)
        parts.append(f'<polyline class="pl-sec" points="{pts}"/>')
    hatch_note = ("45° hatch on closed profiles, holes clear" if hatch
                  else "outline only — no closed profile to hatch")
    parts.append(f'<text class="lbl" x="{cx}" y="{round(cell[1] + cell[3] - 1.2, 3)}"'
                 f' style="font-size:2.6px">SECTION A-A ({hatch_note};'
                 f' scale to fit)</text>')
    parts.append("</g>")
    return "\n".join(parts)


def _title_block_svg(part_name: str, generated_at: str, scale_note: str) -> str:
    x, y, w, h = 10.0, 254.0, 260.0, 36.0
    rows = [
        f"PART: {part_name}",
        f"DATE: {generated_at}",
        f"SCALE: {scale_note} | UNITS: mm | PROJECTION: THIRD ANGLE",
    ]
    parts = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" '
             'stroke="#111" stroke-width="0.4"/>']
    for i, row in enumerate(rows):
        parts.append(f'<text class="tb" x="{x + 3}" y="{y + 6 + i * 6}">'
                     f'{_esc(row)}</text>')
    parts.append(f'<text class="tb-big" x="{x + 3}" y="{y + h - 5}">'
                 f'{DRAFT_LABEL} — NOT A RELEASED DRAWING</text>')
    return "\n".join(parts)


@skill(
    name="drawing_sheet",
    category="inspect",
    level="macro",
    summary="ONE-call third-angle engineering drawing sheet: FRONT/TOP/RIGHT "
            "+ ISO hlr_view projections (per-view face budget with honest "
            "non_cut_ready silhouette fallback; one bad view never kills the "
            "sheet), optional cross-section (closed profiles carry 45° "
            "even-odd hatch — holes clear), title block, auto_dimension "
            "TABLE with anchored callouts (v1: NO automatic leader "
            "placement), classify_holes hole table — as a self-contained "
            "SVG-in-HTML sheet + a layered VISIBLE/HIDDEN DXF per view. The "
            "'DRAFT FOR REVIEW' label is baked into the artifact. Read-only "
            "— only the filesystem is touched.",
    selector_kinds=[],
    history_rules={},
    produces_features=["drawing_sheet"],
    expansion=["hlr_view", "cross_section", "auto_dimension", "classify_holes",
               "dxf_export"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.no_body", "fm.all_views_failed", "fm.sheet_write_failed"],
    cost_hint=3.0,
    result_grade="draft",
    post_conditions=[PostCondition(kind="body_present")],
)
class DrawingSheet(SkillBase):
    class Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

        out_dir: str = Field(
            min_length=1,
            description="Directory for the HTML sheet + per-view DXF files "
                        "(created if missing).")
        part_name: str = Field(
            default="part",
            description="Stamped into the title block and file names.")
        include_section: bool = Field(
            default=False,
            description="Add a SECTION A-A inset (cross_section reuse — "
                        "closed profiles filled with 45° even-odd hatch, "
                        "holes clear; open profiles stay outline).")
        section_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        section_normal: tuple[float, float, float] = (1.0, 0.0, 0.0)
        samples_per_edge: int = Field(default=20, ge=2, le=500)
        max_face_count: int = Field(
            default=DEFAULT_HLR_MAX_FACES, ge=1,
            description="Per-view exact-HLR face budget (hlr_view "
                        "passthrough).")
        write_dxf: bool = Field(
            default=True,
            description="Also write a layered VISIBLE/HIDDEN DXF per view.")
        generated_at: str | None = Field(
            default=None,
            description="Timestamp override for deterministic artifacts "
                        "(tests). None → now (UTC ISO).")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("fm.no_body: drawing_sheet needs a body.")

        generated_at = args.generated_at or _dt.datetime.now(
            _dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        stem = _safe_stem(args.part_name)

        # 1 — HLR views, per-view try/except: one bad view never kills the
        # sheet; its raw error is recorded and rendering continues.
        views: list[dict[str, Any]] = []
        for name, direction, up in _VIEWS:
            title = {"front": "FRONT", "top": "TOP", "right": "RIGHT SIDE",
                     "iso": "ISO (NTS)"}[name]
            rec: dict[str, Any] = {"name": name, "title": title,
                                   "view_direction": list(direction)}
            try:
                ex = HlrView().apply(body, {
                    "view_direction": list(direction),
                    "up_hint": list(up),
                    "samples_per_edge": args.samples_per_edge,
                    "max_face_count": args.max_face_count,
                }).extras
                rec.update(status="ok", extras=ex, mode=ex["mode"],
                           label=ex["label"], n_visible=ex["n_visible"],
                           n_hidden=ex["n_hidden"], n_outline=ex["n_outline"],
                           note=ex["note"])
            except Exception as exc:  # noqa: BLE001 — per-view guard
                rec.update(status="error", mode=None, label=None,
                           note=f"{type(exc).__name__}: {exc}")
            views.append(rec)

        ok_views = [v for v in views if v["status"] == "ok"]
        if not ok_views:
            notes = "; ".join(f"{v['name']}: {v['note']}" for v in views)
            raise ValueError(f"fm.all_views_failed: every projection failed "
                             f"— {notes}")

        # 2 — dimensions (auto_dimension rows AS-IS: table row count ==
        # len(key_dimensions)) + anchored callout refs for diameter rows.
        dim_rows: list[dict[str, Any]] = []
        dim_note = ""
        try:
            from phone_designer.skills.inspect.auto_dimension import AutoDimension
            dim_rows = AutoDimension().apply(body, {}).extras["key_dimensions"]
        except Exception as exc:  # noqa: BLE001
            dim_note = f"auto_dimension failed: {type(exc).__name__}: {exc}"

        front_basis = sheet_basis((0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        callouts: list[dict[str, Any]] = []
        d_idx = 0
        for row in dim_rows:
            if row.get("kind") == "diameter" and row.get("axis_origin"):
                ref = f"D{d_idx}"
                row["ref"] = ref
                o = row["axis_origin"]
                u_ax, v_ax = front_basis[0], front_basis[1]
                callouts.append({
                    "ref": ref,
                    "u": o[0] * u_ax[0] + o[1] * u_ax[1] + o[2] * u_ax[2],
                    "v": o[0] * v_ax[0] + o[1] * v_ax[1] + o[2] * v_ax[2],
                })
                d_idx += 1
            else:
                row["ref"] = "-"

        # 3 — hole table (classify_holes; standard_match already attached).
        hole_rows: list[dict[str, Any]] = []
        hole_note = ""
        try:
            from phone_designer.skills.inspect.classify_holes import ClassifyHoles
            holes = ClassifyHoles().apply(
                body, {"match_standards": True}).extras["holes"]
            for h in holes:
                std = h.get("standard_match")
                if isinstance(std, dict):
                    std_txt = (f"{std.get('thread_spec', '?')} "
                               f"({std.get('fit', '?')}, "
                               f"conf {std.get('confidence', '?')})")
                else:
                    std_txt = "-" if std is None else str(std)
                hole_rows.append({
                    "ref": f"H{h.get('id')}",
                    "type": h.get("type"),
                    "diameters_mm": h.get("diameters_mm"),
                    "depth_mm": h.get("depth_mm"),
                    "standard": std_txt,
                })
        except Exception as exc:  # noqa: BLE001
            hole_note = f"classify_holes failed: {type(exc).__name__}: {exc}"

        # 4 — optional section (cross_section reuse, in-plane projection).
        section_meta: dict[str, Any] = {"included": bool(args.include_section)}
        section_svg = ""
        if args.include_section:
            try:
                from phone_designer.skills.inspect.cross_section import CrossSection
                from phone_designer.skills.io.dxf_export import _inplane_frame
                sex = CrossSection().apply(body, {
                    "plane_origin": list(args.section_origin),
                    "plane_normal": list(args.section_normal),
                }).extras
                u_ax, v_ax = _inplane_frame(args.section_normal)
                o = args.section_origin
                loops_2d = []
                for poly in sex.get("polylines", []):
                    loops_2d.append([
                        (round((p[0] - o[0]) * u_ax[0] + (p[1] - o[1]) * u_ax[1]
                               + (p[2] - o[2]) * u_ax[2], 4),
                         round((p[0] - o[0]) * v_ax[0] + (p[1] - o[1]) * v_ax[1]
                               + (p[2] - o[2]) * v_ax[2], 4))
                        for p in poly])
                closed_loops, open_profiles = _chain_closed_loops(loops_2d)
                hatch_segs, hatch_spacing = _hatch_segments_45(closed_loops)
                section = {"polylines_2d": loops_2d,
                           "hatch_segments_2d": hatch_segs}
                section_meta.update(
                    origin=list(args.section_origin),
                    normal=list(args.section_normal),
                    n_polylines=len(loops_2d),
                    n_closed_loops=len(closed_loops),
                    n_open_profiles=len(open_profiles),
                    n_hatch_segments=len(hatch_segs),
                    hatch_spacing_mm=hatch_spacing)
                section_svg = _section_to_svg(section, _SECTION_CELL)
                if not loops_2d:
                    section_meta["note"] = ("section plane misses the body — "
                                            "no curves")
            except Exception as exc:  # noqa: BLE001
                section_meta.update(
                    note=f"cross_section failed: {type(exc).__name__}: {exc}")

        # 5 — per-view layered DXF (same writer as dxf_export source='hlr').
        out_dir = Path(args.out_dir)
        dxf_paths: dict[str, str] = {}
        if args.write_dxf:
            from phone_designer.skills.io.dxf_export import write_layered_dxf
            for v in ok_views:
                ex = v["extras"]
                visible = (list(ex["visible_polylines_2d"])
                           + list(ex["outline"]["visible_polylines_2d"]))
                hidden = (list(ex["hidden_polylines_2d"])
                          + list(ex["outline"]["hidden_polylines_2d"]))
                dxf_path = out_dir / f"{stem}_{v['name']}.dxf"
                try:
                    write_layered_dxf(
                        dxf_path,
                        {"VISIBLE": visible, "HIDDEN": hidden},
                        layer_linetypes={"HIDDEN": "HIDDEN"})
                    dxf_paths[v["name"]] = str(dxf_path)
                    v["dxf_path"] = str(dxf_path)
                except Exception as exc:  # noqa: BLE001 — honest per-view
                    v["dxf_path"] = None
                    v["note"] += f" | dxf write failed: {exc}"

        # 6 — SVG sheet + HTML page.
        scale, scale_note = _pick_scale(views)
        svg_parts = [
            f'<svg class="sheet" viewBox="0 0 {_SHEET_W:g} {_SHEET_H:g}" '
            'xmlns="http://www.w3.org/2000/svg" width="1260">',
            f'<rect x="2" y="2" width="{_SHEET_W - 4:g}" '
            f'height="{_SHEET_H - 4:g}" fill="#fff" stroke="#111" '
            'stroke-width="0.6"/>',
        ]
        for v in views:
            if v["status"] != "ok":
                cell = _CELLS[v["name"]]
                svg_parts.append(
                    f'<text class="lbl-warn" x="{cell[0] + cell[2] / 2}" '
                    f'y="{cell[1] + cell[3] / 2}">{_esc(v["title"])} — view '
                    f'failed: {_esc(v["note"])}</text>')
                continue
            cell = _CELLS[v["name"]]
            s = scale if v["name"] != "iso" else _fit_scale(
                v["extras"]["extent_uv"], cell)
            svg_parts.append(_view_to_svg(
                v, cell, s,
                callouts=callouts if v["name"] == "front" else None))
        if section_svg:
            svg_parts.append(section_svg)
        svg_parts.append(_title_block_svg(args.part_name, generated_at,
                                          scale_note))
        svg_parts.append(
            f'<text class="watermark" x="{_SHEET_W / 2:g}" '
            f'y="{_SHEET_H / 2:g}" '
            f'transform="rotate(-20 {_SHEET_W / 2:g} {_SHEET_H / 2:g})">'
            f'{DRAFT_LABEL}</text>')
        svg_parts.append("</svg>")
        svg = "\n".join(svg_parts)

        html = self._render_html(
            args.part_name, generated_at, svg, dim_rows, dim_note,
            hole_rows, hole_note, views, section_meta, scale_note)

        html_path = out_dir / f"{stem}_drawing.html"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            html_path.write_text(html, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"fm.sheet_write_failed: {type(exc).__name__}: {exc}")
        if not html_path.exists():
            raise ValueError(
                f"fm.sheet_write_failed: file not written → {html_path}")

        view_meta = [{k: v.get(k) for k in
                      ("name", "title", "status", "mode", "label", "n_visible",
                       "n_hidden", "n_outline", "dxf_path", "note")}
                     for v in views]
        extras = _json_safe({
            "drawing_sheet": {
                "part_name": args.part_name,
                "generated_at": generated_at,
                "projection": "third_angle",
                "scale_note": scale_note,
                "labels": [DRAFT_LABEL],
                "grade": "draft",
                "views": view_meta,
                "dimension_row_count": len(dim_rows),
                "hole_row_count": len(hole_rows),
                "section": section_meta,
                "written": {"html": str(html_path), "dxf": dxf_paths},
                "html_bytes": len(html.encode("utf-8")),
                "notes": [n for n in (dim_note, hole_note) if n] + [
                    "v1: anchored callouts + dimension table only — no "
                    "automatic leader placement; ISO view not to scale; "
                    "section (when included) fills CLOSED profiles with 45° "
                    "even-odd hatch (holes stay clear); open section "
                    "profiles stay outline-only, unhatched.",
                ],
            },
        })
        extras["drawing_sheet_html"] = html
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )

    # ────────────────────────────────────────────────────────────────────
    def _render_html(self, part_name, generated_at, svg, dim_rows, dim_note,
                     hole_rows, hole_note, views, section_meta,
                     scale_note) -> str:
        dim_body = "\n".join(
            "<tr>"
            f"<td>{_esc(r.get('ref', '-'))}</td>"
            f"<td>{_esc(r.get('name'))}</td>"
            f"<td>{_esc(r.get('value'))}</td>"
            f"<td>{_esc(r.get('unit'))}</td>"
            f"<td>{_esc(r.get('tolerance'))}</td>"
            f"<td>{_esc(r.get('kind'))}</td>"
            "</tr>"
            for r in dim_rows)
        if not dim_rows:
            note = dim_note or "no dimensions detected"
            dim_body = (f'<tr><td colspan="6" class="note">{_esc(note)}'
                        "</td></tr>")

        hole_body = "\n".join(
            "<tr>"
            f"<td>{_esc(r['ref'])}</td>"
            f"<td>{_esc(r['type'])}</td>"
            f"<td>{_esc(r['diameters_mm'])}</td>"
            f"<td>{_esc(r['depth_mm'])}</td>"
            f"<td>{_esc(r['standard'])}</td>"
            "</tr>"
            for r in hole_rows)
        if not hole_rows:
            note = hole_note or "no holes detected"
            hole_body = (f'<tr><td colspan="5" class="note">{_esc(note)}'
                         "</td></tr>")

        view_items = []
        for v in views:
            dxf = v.get("dxf_path")
            dxf_txt = f" | DXF: {dxf}" if dxf else ""
            view_items.append(
                f"<li><b>{_esc(v['title'])}</b> — status {_esc(v['status'])}"
                f", mode {_esc(v.get('mode'))}, label {_esc(v.get('label'))}"
                f" | visible {_esc(v.get('n_visible'))}"
                f" / hidden {_esc(v.get('n_hidden'))}"
                f" / outline {_esc(v.get('n_outline'))}"
                f"{_esc(dxf_txt)}<br/>"
                f"<span class='note'>{_esc(v.get('note'))}</span></li>")
        section_note = ""
        if section_meta.get("included"):
            section_note = (f"<p class='note'>Section A-A: "
                            f"{_esc(section_meta)}</p>")

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>{_esc(part_name)} — engineering drawing ({DRAFT_LABEL})</title>
<style>{_CSS}</style>
</head>
<body>
<div class="banner">{DRAFT_LABEL} — auto-generated third-angle drawing
(grade: draft). Anchored callouts + dimension table only; no automatic
leader placement. Verify before manufacturing.</div>
{svg}
<h2>Dimension table (auto_dimension)</h2>
<table id="dimension-table">
<thead><tr><th>REF</th><th>NAME</th><th>VALUE</th><th>UNIT</th>
<th>TOL</th><th>KIND</th></tr></thead>
<tbody>
{dim_body}
</tbody>
</table>
<h2>Hole table (classify_holes)</h2>
<table id="hole-table">
<thead><tr><th>REF</th><th>TYPE</th><th>DIAMETERS (mm)</th>
<th>DEPTH (mm)</th><th>STANDARD</th></tr></thead>
<tbody>
{hole_body}
</tbody>
</table>
<h2>Views</h2>
<ul>
{"".join(view_items)}
</ul>
{section_note}
<footer>Generated {_esc(generated_at)} by phone_designer drawing_sheet v1 —
third-angle projection, units mm, scale {_esc(scale_note)}.
{DRAFT_LABEL}: this artifact is a draft; D-refs are anchored at hole centres
on the FRONT view (no leader lines in v1).</footer>
</body>
</html>
"""
