"""MCP Phase-1 session tools — the stateful / self-correcting / hang-proof loop.

Pins the roadmap Phase-1 contract end-to-end through the WIRED server (not just
the mcp_support modules): generate → modify (volume decreases) → undo (volume
restored EXACTLY) → measure → preview (honest headless skip) → preflight →
machine-actionable failure enrichment (raw error never masked) → pillar tools →
one-call RFQ package.

Runs with PHONE_DESIGNER_SKILL_TIMEOUT_S=0 (inline lane) so CI stays fast — the
worker/timeout lane is pinned by tests/test_guarded_exec.py.
"""
from __future__ import annotations

import json
import math
import os

import pytest

os.environ.setdefault("PHONE_DESIGNER_UI_HEADLESS", "1")
os.environ["PHONE_DESIGNER_SKILL_TIMEOUT_S"] = "0"  # inline lane for CI speed

# the `mcp` package (FastMCP) is not installed in every CI lane — skip the whole
# module cleanly instead of interrupting collection (same guard as
# test_mcp_server.py).
M = pytest.importorskip(
    "phone_designer.mcp_server",
    reason="mcp (FastMCP) not installed in this environment")


@pytest.fixture(scope="module")
def box_id():
    g = M.cad_generate([{"op": "box", "args": {
        "length_mm": 40, "width_mm": 30, "height_mm": 10}}], name="sess_box")
    assert g["ok"] and g["body_id"]
    return g["body_id"]


@pytest.fixture(scope="module")
def box_2holes_id():
    """A 40×30×10 slab with two Ø6 blind holes — the cad_scene target part."""
    g = M.cad_generate([
        {"op": "box", "args": {"length_mm": 40, "width_mm": 30, "height_mm": 10}},
        {"op": "hole", "args": {"position": [-10, 0, 10], "diameter_mm": 6,
                                "depth_mm": 10, "direction": "-Z"}},
        {"op": "hole", "args": {"position": [10, 0, 10], "diameter_mm": 6,
                                "depth_mm": 10, "direction": "-Z"}},
    ], name="sess_box2h")
    assert g["ok"] and g["body_id"]
    return g["body_id"]


# ── session flow ─────────────────────────────────────────────────────────────

def test_modify_decreases_volume_and_links_parent(box_id):
    m = M.cad_modify(box_id, [{"op": "hole", "args": {
        "position": [0, 0, 10], "diameter_mm": 6, "depth_mm": 10,
        "direction": "-Z"}}])
    assert m["ok"] and m["body_id"]
    assert m["parent_body_id"] == box_id
    # 12000 − π·3²·10 = 11717.256
    assert m["volume_mm3"] == pytest.approx(12000 - math.pi * 9 * 10, rel=1e-4)


def test_undo_restores_exact_parent_volume(box_id):
    m = M.cad_modify(box_id, [{"op": "hole", "args": {
        "position": [0, 0, 10], "diameter_mm": 8, "depth_mm": 10,
        "direction": "-Z"}}])
    u = M.cad_undo(m["body_id"])
    assert u["ok"] and u["body_id"] == box_id
    assert u["volume_mm3"] == pytest.approx(12000.0, abs=1e-6)
    assert isinstance(u["lineage"], list) and u["lineage"][0]["parent_id"] is None


def test_undo_at_root_is_honest_refusal(box_id):
    u = M.cad_undo(box_id)
    assert u["ok"] is False and "fm.at_root" in u["error"]


def test_unknown_body_id_structured_error():
    r = M.cad_measure(body_id="body_nope", what="mass")
    assert r["ok"] is False and "unknown_body_id" in r["error"]


def test_measure_mass_and_obb(box_id):
    mass = M.cad_measure(body_id=box_id, what="mass")
    assert mass["ok"] and mass["volume_mm3"] == pytest.approx(12000, rel=1e-6)
    obb = M.cad_measure(body_id=box_id, what="obb")
    assert sorted(obb["obb"]["size_mm"]) == pytest.approx([10, 30, 40], abs=1e-3)


def test_preview_headless_now_renders_real_pngs(box_id):
    # UPDATED (viewer work): cad_preview used to return skipped_no_gl under
    # headless (GPU/GL path). It now defaults to the GL-free numpy renderer, so
    # headless yields REAL PNGs — the old skip-marker assertion is intentionally
    # replaced.
    import os
    pv = M.cad_preview(body_id=box_id)
    assert pv["ok"] is True and pv["skipped"] is False
    assert pv.get("renderer") == "headless_raster"
    iso = (pv.get("images") or {}).get("iso")
    assert iso and os.path.exists(iso) and os.path.getsize(iso) > 2000
    json.dumps(pv, allow_nan=False)  # strict-JSON-safe payload


def test_import_roundtrip(box_id):
    exp = M.cad_export(body_id=box_id, formats=["step"], name="sess_reimp")
    imp = M.cad_import(exp["files"]["step"])
    assert imp["ok"] and imp["body_id"]
    assert imp["volume_mm3"] == pytest.approx(12000, rel=1e-4)


# ── self-correction loop ─────────────────────────────────────────────────────

def test_preflight_separates_tool_ok_from_spec_verdict():
    pf = M.cad_preflight([{"op": "nonexistent_op", "args": {}},
                          {"op": "box", "args": {"length_mm": 5, "width_mm": 5,
                                                 "height_mm": 5}}])
    assert pf["ok"] is True          # the tool ran
    assert pf["spec_ok"] is False    # the spec verdict
    assert pf["steps"][0]["known"] is False
    assert pf["steps"][1]["known"] is True


def test_failure_enrichment_adds_hints_but_never_masks_error():
    bad = M.cad_generate([
        {"op": "box", "args": {"length_mm": 10, "width_mm": 10, "height_mm": 10}},
        {"op": "fillet_edges_by_predicate", "args": {
            "selector": {"kind": "faces_by_area", "min": 1e9}, "radius_mm": 3}}],
        name="sess_bad")
    failed = [s for s in bad["steps"] if s["status"] != "pass"]
    assert failed, "the impossible selector step must fail"
    st = failed[0]
    assert st.get("error")                     # raw error preserved
    assert st.get("likely_cause")              # machine-actionable hint added
    assert st.get("suggested_fix")


# ── pillar + RFQ wiring ──────────────────────────────────────────────────────

def test_quote_package_one_call(box_id):
    q = M.cad_quote_package(body_id=box_id, lot_sizes=[1, 100])
    assert q["ok"] and q["zip_path"] and os.path.exists(q["zip_path"])
    man = q["manifest"]
    assert man and "costs" in man
    json.dumps(q, allow_nan=False)


def test_compare_wiring(box_id):
    g2 = M.cad_generate([{"op": "box", "args": {
        "length_mm": 40, "width_mm": 30, "height_mm": 10}}], name="sess_box2")
    c = M.cad_compare(body_a=box_id, body_b=g2["body_id"])
    assert c["ok"] is True
    assert c.get("summary", {}).get("classification")


# ── F2: the full viewer→MCP loop (namespace bridge) is closed ────────────────

def test_get_selection_reports_current_body_id_and_hint():
    # F2: cad_get_selection surfaces current_body_id (the newest viewer stem) and
    # a modify_hint even before/with a stash, so Claude can bridge the stem↔id gap.
    g = M.cad_generate([{"op": "box", "args": {
        "length_mm": 30, "width_mm": 20, "height_mm": 10}}], name="f2_probe")
    assert g["ok"]
    gs = M.cad_get_selection()
    # current_body_id is the newest STEP stem in the workspace (== 'f2_probe').
    assert gs.get("current_body_id") == "f2_probe"


def test_full_viewer_to_modify_round_trip():
    # THE loop-completer (F1+F2): cad_generate(name=X) → export step → viewer
    # pick_face_by_index → cad_get_selection → cad_modify(mcp_body_id, {selector
    # from get_selection}) == ok. The selector is body-agnostic so it lands on
    # the clicked face of the client's OWN session body.
    from pathlib import Path

    from phone_designer import viewer_server as V
    from phone_designer.skills._resolvers import _all_faces, _face_center
    from phone_designer.skills.create.import_step import ImportStep

    g = M.cad_generate([{"op": "box", "args": {
        "length_mm": 40, "width_mm": 20, "height_mm": 10}}],
        name="f2_loop", formats=["step"])
    assert g["ok"] and g["body_id"]
    mcp_body_id = g["body_id"]
    ws = M._WORKSPACE
    assert (Path(ws) / "f2_loop.step").exists()          # viewer stem == 'f2_loop'

    # user picks the top face in the viewer (server uses faces[idx] directly).
    faces = _all_faces(ImportStep().apply(
        None, {"path": str(Path(ws) / "f2_loop.step")}).body.wrapped)
    top_idx = max(range(len(faces)), key=lambda i: _face_center(faces[i])[2])
    sel = V.pick_face_by_index(Path(ws), "f2_loop", top_idx)
    assert sel["ok"] and abs(sel["centroid"][2] - 10.0) < 1e-3

    # Claude reads the selection …
    gs = M.cad_get_selection()
    assert gs["ok"] and gs["current_body_id"] == "f2_loop"
    selector = gs["selector"]

    # … and drops the body-agnostic selector into cad_modify on its OWN body_id.
    m = M.cad_modify(mcp_body_id, [{
        "op": "fillet_edges_by_predicate",
        "args": {"selector": {"kind": "edges_on_face", "face": selector},
                 "radius_mm": 2.0}}])
    assert m["ok"] is True and m["body_id"]               # loop closed: ok=True
    assert m["parent_body_id"] == mcp_body_id
    # the fillet actually landed (top face's 4 edges rounded → volume dropped).
    assert m["volume_mm3"] < 8000.0
    json.dumps(m, allow_nan=False)                        # strict-JSON-safe


def test_workspace_pointer_published_at_import():
    # F3(a): the MCP server drops its live _WORKSPACE into the well-known pointer
    # file so the separate viewer process binds the SAME dir.
    import tempfile
    from pathlib import Path
    ptr = Path(tempfile.gettempdir()) / "pd_mcp_current.txt"
    assert ptr.exists()
    assert ptr.read_text(encoding="utf-8").strip() == str(M._WORKSPACE)


# ── V3: cad_scene / cad_measure(distance) / cad_section — LLM reasoning tools ─

def test_scene_reports_holes_with_face_indices(box_2holes_id):
    # cad_scene surfaces the feature catalog trimmed for an LLM: two Ø6 holes,
    # each with the face_indices that map 1:1 to GLB primitives / faceMeshes[].
    sc = M.cad_scene(body_id=box_2holes_id)
    assert sc["ok"] is True
    assert sc["counts"]["holes"] == 2
    assert sc["n_faces"] == 8            # 6 box faces + 2 hole side faces
    assert sc["bbox_mm"] == pytest.approx([40, 30, 10], abs=0.1)
    for h in sc["holes"]:
        assert h["face_indices"] and all(isinstance(i, int) for i in h["face_indices"])
        assert h["diameters_mm"] == pytest.approx([6.0], abs=0.05)
        assert h["axis_origin"] and len(h["axis_origin"]) == 3
    json.dumps(sc, allow_nan=False)     # strict-JSON-safe (matrices dropped)


def test_scene_exclusivity_and_unknown_body_are_honest(box_2holes_id):
    both = M.cad_scene(body_id=box_2holes_id, part_path="x.step")
    assert both["ok"] is False and "exactly one" in both["error"]
    nope = M.cad_scene(body_id="body_nope")
    assert nope["ok"] is False and "unknown_body_id" in nope["error"]


def test_measure_distance_two_points_is_10mm(box_id):
    # the common case: the viewer's two picked centroids → distance. No body needed.
    d = M.cad_measure(body_id=box_id, what="distance",
                      entity_a={"kind": "point", "point": [0, 0, 0]},
                      entity_b={"kind": "point", "point": [10, 0, 0]})
    assert d["ok"] is True
    assert d["distance_mm"] == pytest.approx(10.0, abs=1e-6)
    assert d["a_pos"] == [0, 0, 0] and d["b_pos"] == [10, 0, 0]
    json.dumps(d, allow_nan=False)


def test_measure_distance_face_centers_and_bad_entity(box_id):
    # selector-based entities resolve against the body (top→bottom face = 10mm);
    # a malformed entity is an honest fm.bad_entity, never a crash.
    d = M.cad_measure(body_id=box_id, what="distance",
                      entity_a={"kind": "face_center",
                                "selector": {"kind": "faces_by_normal",
                                             "direction": [0, 0, 1]}},
                      entity_b={"kind": "face_center",
                                "selector": {"kind": "faces_by_normal",
                                             "direction": [0, 0, -1]}})
    assert d["ok"] is True and d["distance_mm"] == pytest.approx(10.0, abs=1e-4)
    bad = M.cad_measure(body_id=box_id, what="distance",
                        entity_a={"kind": "point", "point": [0, 0]},
                        entity_b={"kind": "point", "point": [1, 1, 1]})
    assert bad["ok"] is False and "fm.bad_entity" in bad["error"]
    # a selector entity with no body is refused honestly (not a crash).
    nb = M.cad_measure(what="distance",
                       entity_a={"kind": "face_center",
                                 "selector": {"kind": "faces_by_normal",
                                              "direction": [0, 0, 1]}},
                       entity_b={"kind": "point", "point": [0, 0, 0]})
    assert nb["ok"] is False and "fm.bad_entity" in nb["error"]


def test_section_z_mid_returns_new_body_half_volume(box_id):
    # cad_section is a REAL lineage edit: cut a 40×30×10 (=12000mm³) slab at z-mid,
    # keep the −Z half → a NEW body_id with ~half the volume + a parent link.
    sec = M.cad_section(body_id=box_id, axis="z", pos=0.5)
    assert sec["ok"] is True and sec["body_id"]
    assert sec["body_id"] != box_id
    assert sec["parent_body_id"] == box_id
    assert sec["plane_pos_mm"] == pytest.approx(5.0, abs=1e-3)
    assert sec["volume_mm3"] == pytest.approx(6000.0, rel=1e-4)
    # the minted half is a real session body — undo returns to the input.
    u = M.cad_undo(sec["body_id"])
    assert u["ok"] is True and u["body_id"] == box_id
    json.dumps(sec, allow_nan=False)


def test_section_positive_keep_and_bad_axis(box_id):
    pos = M.cad_section(body_id=box_id, axis="x", pos=0.5, keep="positive")
    assert pos["ok"] is True
    assert pos["volume_mm3"] == pytest.approx(6000.0, rel=1e-4)  # +X half of 12000
    bad = M.cad_section(body_id=box_id, axis="w")
    assert bad["ok"] is False and "fm.bad_axis" in bad["error"]


# ── V4: cad_components — LLM reasons about assembly BODIES (not only the browser)

@pytest.fixture(scope="module")
def plate_bolt_step(tmp_path_factory):
    """A 2-solid compound STEP: a 40×30×8 plate (vol 9600) + a Ø6 bolt cylinder
    r=3 h=15 (vol π·9·15 ≈ 424.12). The cad_components target assembly."""
    from build123d import Box, Cylinder, Pos
    from OCP.BRep import BRep_Builder
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from OCP.TopoDS import TopoDS_Compound

    plate = Box(40, 30, 8)
    bolt = Pos(0, 0, 8) * Cylinder(radius=3, height=15)
    builder = BRep_Builder()
    comp = TopoDS_Compound()
    builder.MakeCompound(comp)
    builder.Add(comp, plate.wrapped)
    builder.Add(comp, bolt.wrapped)
    step = str(tmp_path_factory.mktemp("plate_bolt") / "plate_bolt.step")
    w = STEPControl_Writer()
    w.Transfer(comp, STEPControl_AsIs)
    w.Write(step)
    return step


def test_components_two_solids_volumes_and_face_partition(plate_bolt_step):
    # the KEYSTONE: a 2-body plate+bolt splits into 2 components; the plate is
    # ~9600mm³ (6 faces), the bolt ~424mm³ (3 faces); the per-component
    # face_indices PARTITION the assembly's 9 faces (GLB 1:1) with no overlap.
    r = M.cad_components(part_path=plate_bolt_step)
    assert r["ok"] is True and r["n_components"] == 2
    assert r["deep"] is False
    comps = r["components"]
    vols = sorted(c["volume_mm3"] for c in comps)
    assert vols[1] == pytest.approx(9600.0, rel=1e-4)          # plate
    assert vols[0] == pytest.approx(math.pi * 9 * 15, rel=1e-3)  # bolt
    # comp_ids are 0..n-1 and every component reports face_indices + centroid.
    assert sorted(c["comp_id"] for c in comps) == [0, 1]
    for c in comps:
        assert c["face_indices"] and all(
            isinstance(i, int) for i in c["face_indices"])
        assert c["n_faces"] == len(c["face_indices"])
        assert len(c["centroid"]) == 3 and len(c["bbox_mm"]) == 6
    # the union of all face_indices is a clean partition of [0 .. total_faces).
    all_idx = sorted(i for c in comps for i in c["face_indices"])
    total_faces = sum(c["n_faces"] for c in comps)
    assert all_idx == list(range(total_faces))                # no gaps, no dupes
    assert total_faces == 9                                    # 6 plate + 3 bolt
    json.dumps(r, allow_nan=False)                            # strict-JSON-safe


def test_components_single_box_is_one_component(box_id):
    # a single-solid body has EXACTLY ONE component (n=1 is normal, not an error).
    r = M.cad_components(body_id=box_id)
    assert r["ok"] is True and r["n_components"] == 1
    c = r["components"][0]
    assert c["comp_id"] == 0
    assert c["volume_mm3"] == pytest.approx(12000.0, rel=1e-4)
    assert c["n_faces"] == 6 and sorted(c["face_indices"]) == [0, 1, 2, 3, 4, 5]
    json.dumps(r, allow_nan=False)


def test_components_deep_enriches_with_standard_part(plate_bolt_step):
    # deep=True reuses analyze_assembly (dedup + standard-part recognition); it
    # still returns 2 components, each carrying a signature class_id, and the
    # deep flag flips true. The Ø6 shank may be recognized as a fastener — we
    # only assert the enrichment RAN and the schema stays strict-JSON-safe.
    r = M.cad_components(part_path=plate_bolt_step, deep=True)
    assert r["ok"] is True and r["n_components"] == 2
    assert r["deep"] is True
    for c in r["components"]:
        assert c["class_id"] is not None          # deep attached a signature class
        assert c["instance_count"] >= 1
    json.dumps(r, allow_nan=False)


def test_components_exclusivity_and_unknown_body_are_honest(box_id):
    both = M.cad_components(body_id=box_id, part_path="x.step")
    assert both["ok"] is False and "exactly one" in both["error"]
    nope = M.cad_components(body_id="body_nope")
    assert nope["ok"] is False and "unknown_body_id" in nope["error"]
