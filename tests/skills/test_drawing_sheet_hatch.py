"""drawing_sheet SECTION HATCH — 45° even-odd fill of CLOSED section loops.

Verification pins (section-hatch track):
  * gearbox_housing.step sheet: the section view carries a ``<g class="hatch">``
    group with > 50 line segments; EVERY emitted segment midpoint passes an
    independent even-odd inside test against the outer section loop (asserted,
    not eyeballed); NO midpoint lies inside a hole loop (bearing bores);
  * a body with NO closed section loops (tangent-plane cylinder section → one
    open polyline) → no hatch group, no crash;
  * the DRAFT FOR REVIEW label count stays exactly 5 per sheet;
  * the HTML stays self-contained (no external refs).

The even-odd checker below is a TEST-LOCAL implementation (ray casting on the
parsed SVG coordinates) — independent of the production 45°-family clipper.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest
from build123d import Align, Box, Cylinder, Part

from phone_designer.skills.inspect.drawing_sheet import DRAFT_LABEL, DrawingSheet

_C = (Align.CENTER, Align.CENTER, Align.CENTER)
_GEARBOX_STEP = Path("d:/SmartTwinModeller/.pd_workspace/gearbox_housing.step")


# ── test-local SVG parsing + even-odd geometry (independent of production) ──

def _section_chunk(html: str) -> str:
    """The section <g> content up to its label (hatch group + outlines)."""
    assert 'data-view="section"' in html
    return html.split('<g data-view="section">')[1].split("SECTION A-A")[0]


def _hatch_segments(html: str) -> list[tuple[float, float, float, float]]:
    chunk = _section_chunk(html)
    m = re.search(r'<g class="hatch">(.*?)</g>', chunk, re.S)
    if not m:
        return []
    return [tuple(float(v) for v in g)
            for g in re.findall(r'<line x1="([-\d.]+)" y1="([-\d.]+)" '
                                r'x2="([-\d.]+)" y2="([-\d.]+)"/>', m.group(1))]


def _section_polylines(html: str) -> list[list[tuple[float, float]]]:
    chunk = _section_chunk(html)
    out = []
    for pts in re.findall(r'<polyline class="pl-sec" points="([^"]+)"/>', chunk):
        out.append([tuple(float(v) for v in p.split(","))
                    for p in pts.split()])
    return out


def _chain(polys: list[list[tuple[float, float]]],
           tol: float = 5e-3) -> list[list[tuple[float, float]]]:
    """Test-local stitcher: per-edge polylines → CLOSED loops only."""
    t2 = tol * tol

    def d2(a, b):
        return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2

    rest = [list(p) for p in polys if len(p) >= 2]
    closed = []
    while rest:
        c = rest.pop(0)
        grew = True
        while grew and not (len(c) >= 4 and d2(c[0], c[-1]) <= t2):
            grew = False
            for i, q in enumerate(rest):
                if d2(c[-1], q[0]) <= t2:
                    c = c + q[1:]
                elif d2(c[-1], q[-1]) <= t2:
                    c = c + q[-2::-1]
                elif d2(c[0], q[-1]) <= t2:
                    c = q[:-1] + c
                elif d2(c[0], q[0]) <= t2:
                    c = q[::-1][:-1] + c
                else:
                    continue
                rest.pop(i)
                grew = True
                break
        if len(c) >= 4 and d2(c[0], c[-1]) <= t2:
            closed.append(c)
    return closed


def _inside(pt: tuple[float, float], loop: list[tuple[float, float]]) -> bool:
    """Even-odd ray cast (+x ray) — the pin's independent inside test."""
    x, y = pt
    inside = False
    for (x1, y1), (x2, y2) in zip(loop, loop[1:]):
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) / (y2 - y1) * (x2 - x1)
            if xi > x:
                inside = not inside
    return inside


def _bbox_area(loop: list[tuple[float, float]]) -> float:
    xs = [p[0] for p in loop]
    ys = [p[1] for p in loop]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


# ── PIN 1: gearbox housing — hatched section, holes clear ────────────────────

@pytest.fixture(scope="module")
def gearbox_sheet(tmp_path_factory):
    """Section plane y=40 (normal +Y): inside the bearing wall — the section
    is the wall face (outer loop) pierced by the two bearing-bore circles."""
    if not _GEARBOX_STEP.exists():
        pytest.skip(f"gearbox exercise artifact missing: {_GEARBOX_STEP}")
    from phone_designer.skills.create.import_step import ImportStep
    body = ImportStep().apply(None, {"path": str(_GEARBOX_STEP)}).body
    out_dir = tmp_path_factory.mktemp("gearbox_sheet")
    return DrawingSheet().apply(body, {
        "out_dir": str(out_dir),
        "part_name": "gearbox-housing",
        "include_section": True,
        "section_origin": [0.0, 40.0, 0.0],
        "section_normal": [0.0, 1.0, 0.0],
        "write_dxf": False,
        "generated_at": "2026-07-10 00:00 UTC",
    }).extras


def test_gearbox_hatch_group_over_50_segments(gearbox_sheet):
    html = gearbox_sheet["drawing_sheet_html"]
    sec = gearbox_sheet["drawing_sheet"]["section"]
    assert '<g class="hatch">' in html
    segs = _hatch_segments(html)
    assert len(segs) > 50
    assert sec["n_hatch_segments"] == len(segs)  # meta == artifact
    # y=40 cuts the bearing wall: outer face + 2 bore circles, all closed.
    assert sec["n_closed_loops"] == 3
    assert sec["n_open_profiles"] == 0
    assert "45° hatch on closed profiles" in html  # in-sheet honest note


def test_gearbox_hatch_inside_outer_loop_and_holes_clear(gearbox_sheet):
    """THE pin: every emitted segment midpoint passes the even-odd inside
    test on the OUTER loop, and no midpoint sits inside a hole loop."""
    html = gearbox_sheet["drawing_sheet_html"]
    loops = _chain(_section_polylines(html))
    assert len(loops) == 3  # outer wall face + two bearing bores
    loops.sort(key=_bbox_area, reverse=True)
    outer, holes = loops[0], loops[1:]
    # the "holes" really are the bore circles: closed and near-circular.
    for h in holes:
        xs = [p[0] for p in h]
        ys = [p[1] for p in h]
        assert abs((max(xs) - min(xs)) - (max(ys) - min(ys))) < 0.5

    mids = [((x1 + x2) / 2.0, (y1 + y2) / 2.0)
            for x1, y1, x2, y2 in _hatch_segments(html)]
    assert mids
    assert all(_inside(m, outer) for m in mids)          # all inside outer
    for hole in holes:                                   # holes unhatched
        assert not any(_inside(m, hole) for m in mids)


def test_gearbox_sheet_self_contained_and_label_count_5(gearbox_sheet):
    html = gearbox_sheet["drawing_sheet_html"]
    assert html.count(DRAFT_LABEL) == 5  # title/banner/titleblock/wm/footer
    assert "http://" not in html.replace("http://www.w3.org", "")
    assert "src=" not in html and "href=" not in html
    json.dumps(gearbox_sheet["drawing_sheet"])  # strict-JSON safe


# ── always-on synthetic twin of pin 1 (no workspace artifact needed) ─────────

@pytest.fixture(scope="module")
def plate_sheet(tmp_path_factory):
    """60×40×10 plate with a ⌀16 through-hole, sectioned normal to the hole
    axis → one closed rectangle + one closed circle (the hole)."""
    body = Part() + Box(60, 40, 10, align=_C) - Cylinder(8, 20, align=_C)
    out_dir = tmp_path_factory.mktemp("plate_sheet")
    return DrawingSheet().apply(body, {
        "out_dir": str(out_dir),
        "part_name": "plate-hole",
        "include_section": True,
        "section_origin": [0.0, 0.0, 0.0],
        "section_normal": [0.0, 0.0, 1.0],
        "write_dxf": False,
        "generated_at": "2026-07-10 00:00 UTC",
    }).extras


def test_plate_hatch_inside_rect_hole_clear(plate_sheet):
    html = plate_sheet["drawing_sheet_html"]
    sec = plate_sheet["drawing_sheet"]["section"]
    assert sec["n_closed_loops"] == 2
    segs = _hatch_segments(html)
    assert len(segs) == sec["n_hatch_segments"] > 0
    loops = _chain(_section_polylines(html))
    assert len(loops) == 2
    loops.sort(key=_bbox_area, reverse=True)
    outer, hole = loops
    mids = [((x1 + x2) / 2.0, (y1 + y2) / 2.0) for x1, y1, x2, y2 in segs]
    assert all(_inside(m, outer) for m in mids)
    assert not any(_inside(m, hole) for m in mids)
    # hatch really is 45° on the sheet (SVG y is flipped → slope magnitude 1).
    for x1, y1, x2, y2 in segs:
        assert math.isclose(abs(y2 - y1), abs(x2 - x1),
                            rel_tol=0.0, abs_tol=2e-2)
    assert html.count(DRAFT_LABEL) == 5


# ── PIN 2: no closed section loops → no hatch group, no crash ────────────────

def test_open_profile_section_gets_no_hatch(tmp_path):
    """Plane tangent to a cylinder → the section is ONE OPEN line: it must
    stay outline (unhatched), the sheet must still render."""
    body = Cylinder(8, 30, align=_C)
    ex = DrawingSheet().apply(body, {
        "out_dir": str(tmp_path),
        "part_name": "tangent-cyl",
        "include_section": True,
        "section_origin": [8.0, 0.0, 0.0],
        "section_normal": [1.0, 0.0, 0.0],
        "write_dxf": False,
        "generated_at": "2026-07-10 00:00 UTC",
    }).extras
    html = ex["drawing_sheet_html"]
    sec = ex["drawing_sheet"]["section"]
    assert sec["included"] is True
    assert sec["n_closed_loops"] == 0
    assert sec["n_hatch_segments"] == 0
    assert sec["hatch_spacing_mm"] is None
    assert 'class="hatch"' not in html
    if sec["n_polylines"] > 0:  # outline still drawn, honestly labelled
        assert "no closed profile to hatch" in html
    assert html.count(DRAFT_LABEL) == 5
