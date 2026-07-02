"""drawing_sheet — third-angle engineering drawing sheet (track 2-1 macro).

Verification pins (plans/NEXT_ROADMAP.md §2-1 + task card):
  * on a generated part the sheet HTML is > 10 KB, self-contained, carries the
    baked-in 'DRAFT FOR REVIEW' label and ALL FOUR views (front/top/right/iso);
  * dimension-table row count == auto_dimension key_dimensions row count
    (parity pin);
  * a layered VISIBLE/HIDDEN DXF is written per view (front pin: 4/10 —
    the probe geometry);
  * anchored callouts (D-refs) + hole table (classify_holes standard match);
  * optional section view (cross_section reuse);
  * structured refusals: fm.no_body / fm.all_views_failed /
    fm.sheet_write_failed — all reachable.
"""
from __future__ import annotations

import json
import re

import pytest
from build123d import Align, Box, Cylinder, Part

from phone_designer.skills.inspect.auto_dimension import AutoDimension
from phone_designer.skills.inspect.drawing_sheet import DRAFT_LABEL, DrawingSheet

_C = (Align.CENTER, Align.CENTER, Align.CENTER)


@pytest.fixture(scope="module")
def body():
    """Generated part: 80×60×30 housing block with a ⌀16 through-hole (the
    same analytic probe geometry pinned in test_hlr_view.py)."""
    return Part() + Box(80, 60, 30, align=_C) - Cylinder(8, 40, align=_C)


@pytest.fixture(scope="module")
def sheet(body, tmp_path_factory):
    """Run the macro ONCE for the whole module (HLR ×4 + dims + holes)."""
    out_dir = tmp_path_factory.mktemp("sheet")
    extras = DrawingSheet().apply(body, {
        "out_dir": str(out_dir),
        "part_name": "probe-housing",
        "include_section": True,
        "section_origin": [0.0, 0.0, 0.0],
        "section_normal": [1.0, 0.0, 0.0],
        "generated_at": "2026-07-02 00:00 UTC",  # deterministic artifact
    }).extras
    return extras


def _table_rows(html: str, table_id: str) -> int:
    body_html = html.split(f'id="{table_id}"')[1].split("</table>")[0]
    tbody = body_html.split("<tbody>")[1]
    return len(re.findall(r"<tr>", tbody))


# ── the sheet artifact ───────────────────────────────────────────────────────

def test_html_written_over_10kb_with_draft_label(sheet):
    ds = sheet["drawing_sheet"]
    html = sheet["drawing_sheet_html"]
    assert ds["html_bytes"] > 10_000
    with open(ds["written"]["html"], encoding="utf-8") as fh:
        assert fh.read() == html  # file IS the artifact
    # the DRAFT label is baked into the ARTIFACT (banner + title block +
    # watermark + footer), not just the return dict.
    assert html.count(DRAFT_LABEL) >= 3
    assert ds["labels"] == [DRAFT_LABEL]
    assert ds["grade"] == "draft"
    # self-contained: no external asset references.
    assert "http://" not in html.replace("http://www.w3.org", "")
    assert "src=" not in html and "href=" not in html


def test_all_four_views_rendered_via_hlr(sheet):
    ds = sheet["drawing_sheet"]
    html = sheet["drawing_sheet_html"]
    assert [v["name"] for v in ds["views"]] == ["front", "top", "right", "iso"]
    for v in ds["views"]:
        assert v["status"] == "ok"
        assert v["mode"] == "hlr" and v["label"] == "hlr"
        assert f'data-view="{v["name"]}"' in html
    front = ds["views"][0]
    assert (front["n_visible"], front["n_hidden"], front["n_outline"]) \
        == (4, 9, 1)  # the probe pin, on the sheet


def test_dimension_table_rows_match_auto_dimension(sheet, body):
    ds = sheet["drawing_sheet"]
    rows = AutoDimension().apply(body, {}).extras["key_dimensions"]
    assert ds["dimension_row_count"] == len(rows) > 0
    assert _table_rows(sheet["drawing_sheet_html"], "dimension-table") \
        == len(rows)


def test_anchored_callout_and_hole_table(sheet):
    html = sheet["drawing_sheet_html"]
    ds = sheet["drawing_sheet"]
    # ⌀16 hole → one hole row + a D0 anchored callout in the FRONT view SVG
    # (v1: anchor marker only, NO leader line — roadmap REJECT #6).
    assert ds["hole_row_count"] == 1
    assert ">H0<" in html.replace("</td>", "<").replace("<td>", ">")
    assert 'class="callout"' in html and ">D0</text>" in html
    assert "no automatic\nleader placement" in html.lower() \
        or "no automatic leader placement" in html.lower() \
        or "no leader lines" in html.lower()


def test_section_view_included(sheet):
    ds = sheet["drawing_sheet"]
    assert ds["section"]["included"] is True
    assert ds["section"]["n_polylines"] > 0
    assert 'data-view="section"' in sheet["drawing_sheet_html"]


def test_layered_dxf_written_per_view(sheet):
    ezdxf = pytest.importorskip("ezdxf")
    ds = sheet["drawing_sheet"]
    assert set(ds["written"]["dxf"]) == {"front", "top", "right", "iso"}
    doc = ezdxf.readfile(ds["written"]["dxf"]["front"])
    assert {"VISIBLE", "HIDDEN"} <= {layer.dxf.name for layer in doc.layers}
    by_layer: dict[str, int] = {}
    for e in doc.modelspace():
        by_layer[e.dxf.layer] = by_layer.get(e.dxf.layer, 0) + 1
    assert by_layer == {"VISIBLE": 4, "HIDDEN": 10}  # 9 hidden + 1 outline


def test_extras_strict_json_safe_and_scale(sheet):
    json.dumps(sheet["drawing_sheet"])
    # 80-wide part in ~167 mm cells → common standard ortho scale 1:1.
    assert sheet["drawing_sheet"]["scale_note"] == "1:1"


# ── guards / structured refusals ─────────────────────────────────────────────

def test_none_body_refused(tmp_path):
    with pytest.raises(ValueError, match="fm.no_body"):
        DrawingSheet().apply(None, {"out_dir": str(tmp_path)})


def test_all_views_failed_on_empty_compound(tmp_path):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    comp = TopoDS_Compound()
    BRep_Builder().MakeCompound(comp)
    with pytest.raises(ValueError, match="fm.all_views_failed"):
        DrawingSheet().apply(comp, {"out_dir": str(tmp_path)})


def test_sheet_write_failed_on_file_as_out_dir(body, tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("occupied")
    with pytest.raises(ValueError, match="fm.sheet_write_failed"):
        DrawingSheet().apply(body, {
            "out_dir": str(blocker), "write_dxf": False})


def test_face_budget_fallback_view_is_labelled_on_sheet(body, tmp_path):
    """Budget-exceeded views still render — brute silhouette, honestly
    labelled non_cut_ready INSIDE the SVG, and the sheet survives."""
    ex = DrawingSheet().apply(body, {
        "out_dir": str(tmp_path), "max_face_count": 1,
        "write_dxf": False}).extras
    ds = ex["drawing_sheet"]
    assert all(v["status"] == "ok" for v in ds["views"])
    assert all(v["label"] == "non_cut_ready" for v in ds["views"])
    assert "NON-CUT-READY" in ex["drawing_sheet_html"]
