"""TRACK 3-4 SPIKE — do assembly mates persist across a compound rebuild?

Go/no-go evidence for the assembly-DOF bet (roadmap 3-4): 'tag-propagation으로
mate가 compound rebuild를 생존함을 spike로 증명하면 Go'.

The spike is this test file. Verdict: GO — with the tag-backed store of
``skills/assembly/_mate_persistence.py``:

  * mate skills persist NOTHING today (pinned below);
  * a plain ``_pd_mates`` body attribute does NOT survive a rebuild (pinned);
  * face tags in ``body._pd_tags`` DO survive every compound rebuild
    (add_component / move_component / mate_*) because ``SkillBase.apply``
    re-anchors them by bbox-center nearest-match — and they re-resolve to the
    RIGHT component's face (pinned);
  * two real failure modes exist and are DETECTED (never silent) by the
    recorded-area guard: cross-component mis-anchor after a >5 mm move, and
    coincident-face collapse (both pinned).

Fixture: plate 20x20x8 with a Ø5 through hole at origin + a Ø3 pin, mated
concentric. Pin length 24 keeps the mated pin-OD centroid (0,0,11) more than
5 mm (the propagation TOL) away from every plate face centroid, so re-anchor
behavior is unambiguous. The h=10 pin variant makes the pin-OD and hole-ID
centroids exactly coincide — the collapse case.
"""
from __future__ import annotations

import json
import math
import os

import pytest

os.environ.setdefault("PHONE_DESIGNER_UI_HEADLESS", "1")

from phone_designer.skills._resolvers import _all_faces, _face_area, _face_center, resolve_faces
from phone_designer.skills._selectors import TaggedSelector
from phone_designer.skills.assembly._compound import list_components
from phone_designer.skills.assembly._mate_persistence import (
    component_of_face,
    list_mates,
    mate_tag_name,
    parse_mate_tag,
    record_mate,
    resolve_mate,
)
from phone_designer.skills.assembly.add_component import AddComponent
from phone_designer.skills.assembly.mate_concentric import MateConcentric
from phone_designer.skills.assembly.move_component import MoveComponent
from phone_designer.skills.compose.tag_face import get_tags


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

PIN_OD_AREA_24 = 2 * math.pi * 1.5 * 24     # 226.195
PIN_OD_AREA_10 = 2 * math.pi * 1.5 * 10     # 94.248
HOLE_ID_AREA = 2 * math.pi * 2.5 * 8        # 125.664
PLATE_TOP_AREA = 400 - math.pi * 2.5 ** 2   # 380.365 (the mis-anchor magnet)

SEL_PIN_OD_24 = {"kind": "faces_by_area", "min": 200.0, "max": 250.0}
SEL_PIN_OD_10 = {"kind": "faces_by_area", "min": 80.0, "max": 100.0}
SEL_HOLE_ID = {"kind": "faces_by_area", "min": 120.0, "max": 130.0}


def _plate_with_hole():
    from build123d import Part
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    box = BRepPrimAPI_MakeBox(gp_Pnt(-10, -10, 0), gp_Pnt(10, 10, 8)).Shape()
    cyl = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(0, 0, -1), gp_Dir(0, 0, 1)), 2.5, 10.0
    ).Shape()
    return Part(BRepAlgoAPI_Cut(box, cyl).Shape())


def _write_step(shape, path) -> str:
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    w.Write(str(path))
    return str(path)


@pytest.fixture(scope="module")
def step_files(tmp_path_factory):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    d = tmp_path_factory.mktemp("mate_spike")
    ax = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    return {
        "pin24": _write_step(BRepPrimAPI_MakeCylinder(ax, 1.5, 24.0).Shape(),
                             d / "pin24.step"),
        "pin10": _write_step(BRepPrimAPI_MakeCylinder(ax, 1.5, 10.0).Shape(),
                             d / "pin10.step"),
        "bracket": _write_step(
            BRepPrimAPI_MakeBox(gp_Pnt(50, 50, 0), gp_Pnt(56, 56, 6)).Shape(),
            d / "bracket.step"),
    }


def _mated_assembly(step_files, pin_key="pin24", pin_sel=SEL_PIN_OD_24):
    """add_component x2 -> mate_concentric. Returns the mate SkillResult."""
    r1 = AddComponent().apply(_plate_with_hole(), {"name": "plate"})
    r2 = AddComponent().apply(r1.body, {
        "name": "pin", "source_step_path": step_files[pin_key],
        "translation": (30.0, 0.0, 0.0),
    })
    return MateConcentric().apply(r2.body, {
        "component_a": "pin", "face_selector_a": pin_sel,
        "component_b": "plate", "face_selector_b": SEL_HOLE_ID,
    })


def _recorded(step_files):
    """Mated assembly with the mate persisted via record_mate."""
    body = _mated_assembly(step_files).body
    rec = record_mate(body, "concentric", "pin", "plate",
                      SEL_PIN_OD_24, SEL_HOLE_ID)
    return body, rec


# ---------------------------------------------------------------------------
# SPIKE STEP 1+2 — where does the mate data live today? (answer: nowhere)
# ---------------------------------------------------------------------------

def test_mate_skill_persists_nothing_today(step_files):
    r = _mated_assembly(step_files)
    body = r.body
    # geometry is mated: pin OD axis centroid sits on the hole axis (x=y=0)
    comps = dict(list_components(body))
    pin_od = [f for f in _all_faces(comps["pin"]) if 200 < _face_area(f) < 250][0]
    cx, cy, _ = _face_center(pin_od)
    assert abs(cx) < 1e-6 and abs(cy) < 1e-6
    # ...but NO persistent mate data anywhere on the body:
    assert getattr(body, "_pd_mates", None) is None
    assert get_tags(body) == {}
    # only transient extras (component_names + the V5 step metrics)
    assert set(r.extras.keys()) == {"component_names", "_step_metrics"}


def test_plain_attribute_is_lost_on_compound_rebuild(step_files):
    """The `_pd_pmi_dimensions attach pattern` alone does NOT survive:
    add_component builds a fresh Part and copies only _pd_component_names."""
    body = _mated_assembly(step_files).body
    body._pd_mates = [{"type": "concentric", "a": "pin", "b": "plate"}]
    r = AddComponent().apply(
        body, {"name": "bracket", "source_step_path": step_files["bracket"]}
    )
    assert getattr(r.body, "_pd_mates", None) is None  # gone — hence the tag store


# ---------------------------------------------------------------------------
# SPIKE STEP 3 — tag-backed mate record SURVIVES compound rebuilds
# ---------------------------------------------------------------------------

def test_recorded_mate_survives_add_component_rebuild(step_files):
    body, rec = _recorded(step_files)
    assert rec["index"] == 0 and rec["complete"] is True

    r = AddComponent().apply(
        body, {"name": "bracket", "source_step_path": step_files["bracket"]}
    )
    mates = list_mates(r.body)
    assert len(mates) == 1
    m = mates[0]
    assert m["type"] == "concentric"
    assert m["component_a"] == "pin" and m["component_b"] == "plate"
    assert m["complete"] is True

    res = resolve_mate(r.body, 0)
    assert res["ok"] is True
    # re-resolution lands on the RIGHT entities of the RIGHT components
    assert component_of_face(r.body, res["sides"]["a"]["face"]) == "pin"
    assert component_of_face(r.body, res["sides"]["b"]["face"]) == "plate"
    assert abs(res["sides"]["a"]["resolved_area_mm2"] - PIN_OD_AREA_24) < 0.01
    assert abs(res["sides"]["b"]["resolved_area_mm2"] - HOLE_ID_AREA) < 0.01


def test_recorded_mate_survives_two_sequential_rebuilds(step_files):
    body, _ = _recorded(step_files)
    b1 = AddComponent().apply(
        body, {"name": "bracket", "source_step_path": step_files["bracket"]}
    ).body
    b2 = MoveComponent().apply(
        b1, {"name": "bracket", "translation": (10.0, 0.0, 0.0)}
    ).body
    res = resolve_mate(b2, 0)
    assert res["ok"] is True
    assert component_of_face(b2, res["sides"]["a"]["face"]) == "pin"
    assert component_of_face(b2, res["sides"]["b"]["face"]) == "plate"


def test_recorded_mate_tracks_sub_tol_move(step_files):
    """A <5mm move of the mated pin: the ref re-anchors onto the MOVED face
    (tracks the entity, not stale coordinates)."""
    body, rec = _recorded(step_files)
    old_center = rec["sides"]["a"]["current_ref_center"]
    b = MoveComponent().apply(
        body, {"name": "pin", "translation": (0.0, 2.0, 0.0)}
    ).body
    res = resolve_mate(b, 0)
    assert res["ok"] is True
    face_a = res["sides"]["a"]["face"]
    assert component_of_face(b, face_a) == "pin"
    c = _face_center(face_a)
    assert abs(c[1] - (old_center[1] + 2.0)) < 1e-6  # followed the +2mm y move


# ---------------------------------------------------------------------------
# SPIKE STEP 4 — the two REAL failure modes, detected (never silent)
# ---------------------------------------------------------------------------

def test_large_move_cross_anchor_is_detected_not_silent(step_files):
    """Moving the pin 15mm does NOT drop its mate ref — propagate_tags silently
    re-anchors it onto the nearest surviving face within 5mm: the PLATE TOP
    (centroid (0,0,8), 3mm from the old pin-OD centroid (0,0,11)). The raw
    tagged resolver then returns that WRONG face. The recorded-area guard in
    resolve_mate converts this silent corruption into an explicit
    'area_mismatch'."""
    body, _ = _recorded(step_files)
    b = MoveComponent().apply(
        body, {"name": "pin", "translation": (15.0, 0.0, 0.0)}
    ).body

    # (a) raw mechanism failure pinned: the store now carries the plate face
    tags = get_tags(b)
    a_name = [n for n in tags if parse_mate_tag(n) and parse_mate_tag(n)["side"] == "a"][0]
    ref = tags[a_name][0]
    assert abs(ref.measure - PLATE_TOP_AREA) < 0.01          # 380.37, not 226.20
    shape = b.wrapped
    wrong = resolve_faces(shape, TaggedSelector(tag=a_name), body=b)
    assert wrong and component_of_face(b, wrong[0]) == "plate"  # cross-component!

    # (b) the guard catches it: recorded area rides the immutable tag NAME
    res = resolve_mate(b, 0)
    assert res["ok"] is False
    side_a = res["sides"]["a"]
    assert side_a["status"] == "area_mismatch"
    assert side_a["face"] is None                            # wrong face NOT returned
    assert abs(side_a["recorded_area_mm2"] - PIN_OD_AREA_24) < 0.01
    assert abs(side_a["resolved_area_mm2"] - PLATE_TOP_AREA) < 0.01
    # the unmoved side stays healthy
    assert res["sides"]["b"]["status"] == "ok"
    assert component_of_face(b, res["sides"]["b"]["face"]) == "plate"


def test_coincident_mate_faces_collapse_is_detected(step_files):
    """h=10 pin lands flush: pin-OD and hole-ID centroids coincide at (0,0,4).
    Center-only nearest-match cannot discriminate them — both sides resolve to
    the SAME face. The area guard flags the collapsed side."""
    body = _mated_assembly(step_files, pin_key="pin10", pin_sel=SEL_PIN_OD_10).body
    record_mate(body, "concentric", "pin", "plate", SEL_PIN_OD_10, SEL_HOLE_ID)
    # rebuild once so the refs go through propagate_tags nearest-match
    b = AddComponent().apply(
        body, {"name": "bracket", "source_step_path": step_files["bracket"]}
    ).body

    tags = get_tags(b)
    names = {parse_mate_tag(n)["side"]: n for n in tags if parse_mate_tag(n)}
    fa = resolve_faces(b.wrapped, TaggedSelector(tag=names["a"]), body=b)
    fb = resolve_faces(b.wrapped, TaggedSelector(tag=names["b"]), body=b)
    assert fa and fb and fa[0].IsSame(fb[0])                 # collapse pinned

    res = resolve_mate(b, 0)
    assert res["ok"] is False
    statuses = {s: res["sides"][s]["status"] for s in ("a", "b")}
    # exactly one side kept its true face; the other is flagged, not faked
    assert sorted(statuses.values()) == ["area_mismatch", "ok"]


# ---------------------------------------------------------------------------
# helpers: refusals + strict-JSON safety
# ---------------------------------------------------------------------------

def test_record_mate_refusals_are_raw_and_reachable(step_files):
    body = _mated_assembly(step_files).body
    with pytest.raises(RuntimeError, match="component 'ghost' not found"):
        record_mate(body, "concentric", "ghost", "plate",
                    SEL_PIN_OD_24, SEL_HOLE_ID)
    with pytest.raises(RuntimeError, match="matched 0 faces"):
        record_mate(body, "concentric", "pin", "plate",
                    {"kind": "faces_by_area", "min": 9000.0}, SEL_HOLE_ID)
    with pytest.raises(RuntimeError, match="must be different"):
        record_mate(body, "concentric", "pin", "pin",
                    SEL_PIN_OD_24, SEL_PIN_OD_24)
    with pytest.raises(ValueError, match="must not contain"):
        mate_tag_name(0, "con:centric", "a", "pin", 1.0)
    with pytest.raises(ValueError, match="malformed"):
        parse_mate_tag("__mate__:0:concentric:a")   # truncated reserved tag
    # non-reserved tags are simply not mate tags (no false positives)
    assert parse_mate_tag("TOP_RIM") is None


def test_list_mates_is_strict_json_safe_and_ordered(step_files):
    body, _ = _recorded(step_files)
    # second mate on the same assembly gets the next index
    rec2 = record_mate(body, "planar", "pin", "plate",
                       SEL_PIN_OD_24, SEL_HOLE_ID)
    assert rec2["index"] == 1
    b = AddComponent().apply(
        body, {"name": "bracket", "source_step_path": step_files["bracket"]}
    ).body
    mates = list_mates(b)
    assert [m["index"] for m in mates] == [0, 1]
    assert [m["type"] for m in mates] == ["concentric", "planar"]
    round_tripped = json.loads(json.dumps(mates))            # strict-JSON-safe
    assert round_tripped == mates
