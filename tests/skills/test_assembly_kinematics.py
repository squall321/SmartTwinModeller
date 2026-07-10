"""Assembly kinematics track (roadmap 3-4): mate_tag + assembly_dof + kinematic_sweep.

SHIP PINS (all analytic values derived in-file, then the skills must find them):

HINGED-LID fixture — known first-interference angle
    base box (0,0,0)-(60,39.5,5); lid built OPEN as a vertical slab
    (0,40,5)-(60,43,45) hinged on the world line {y=40, z=5} along +X; a boss
    (20,19,5)-(40,25,11) on the base top (h = 6 above z=5, near-hinge top edge
    at y=25). Driving the revolute by theta closes the lid: a point of the
    lid's inner face (the plane through the hinge) at hinge-distance s maps to
    (y,z) = (40 - s*sin(theta), 5 + s*cos(theta)). The face reaches the boss's
    near-hinge top edge Q=(y=25, z=11) when tan(theta) = (40-25)/(11-5), i.e.

        theta_c = atan2(15, 6) = 68.19859... deg        (hand-computable)

    The base back face sits at y=39.5 (0.5 mm hinge gap) so lid/base stay at
    volume-overlap 0 for all theta in [0,90] and the boss is genuinely the
    first contact. Overlap at theta=70 is an exact triangular prism:
    0.5 * (6 - 15*cot70) * (6*tan70 - 15) * 20 mm^3 (checked below).

SLIDER fixture — linear first contact
    rail (0,0,0)-(100,20,5); block (0,0,5)-(10,20,15) sliding along +X;
    stop (60,0,5)-(70,20,15) fixed to the rail. Block front face x=10 meets
    the stop at u = 50.0 exactly; an exact face-touch has ZERO common volume,
    so the honest first CONTACT (volume > 1uL) lands on the next sample.

Also pinned: mate persistence across a compound rebuild AND a STEP round-trip
(sidecar-reattach, geometric re-resolve), 4-bar -> fm.closed_loop, DOF numbers.
"""
from __future__ import annotations

import json
import math
import os

import pytest

os.environ.setdefault("PHONE_DESIGNER_UI_HEADLESS", "1")

from phone_designer.skills.assembly._compound import (
    build_compound,
    iter_solid_components,
    load_step_shape,
)
from phone_designer.skills.assembly._mate_persistence import (
    parse_mate_tag,
    record_mate,
    resolve_mate,
)
from phone_designer.skills.assembly.add_component import AddComponent
from phone_designer.skills.assembly.assembly_dof import AssemblyDof
from phone_designer.skills.assembly.kinematic_sweep import KinematicSweep
from phone_designer.skills.assembly.mate_tag import (
    MateTag,
    decode_kind_frame,
    encode_kind_frame,
    list_kinematic_mates,
    restore_mate_tags,
    serialize_mate_tags,
)
from phone_designer.skills.assembly.move_component import MoveComponent
from phone_designer.skills.compose.tag_face import get_tags, set_tags


# ---------------------------------------------------------------------------
# analytic expectations (derived here, found by the skills below)
# ---------------------------------------------------------------------------

HINGE_EXPECTED_DEG = math.degrees(math.atan2(15.0, 6.0))      # 68.19859...
SLIDER_EXPECTED_MM = 60.0 - 10.0                              # 50.0

_T70 = math.tan(math.radians(70.0))
# exact wedge lid∩boss at theta=70 (triangular prism, x-extent 20):
HINGE_OVERLAP_AT_70 = 0.5 * (6.0 - 15.0 / _T70) * (6.0 * _T70 - 15.0) * 20.0


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _mk_box(p0, p1):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(gp_Pnt(*p0), gp_Pnt(*p1)).Shape()


def _write_step(shape, path) -> str:
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    w.Write(str(path))
    return str(path)


@pytest.fixture(scope="module")
def steps(tmp_path_factory):
    d = tmp_path_factory.mktemp("assembly_kinematics")
    boxes = {
        "base": ((0, 0, 0), (60, 39.5, 5)),
        "lid": ((0, 40, 5), (60, 43, 45)),
        "boss": ((20, 19, 5), (40, 25, 11)),
        "rail": ((0, 0, 0), (100, 20, 5)),
        "block": ((0, 0, 5), (10, 20, 15)),
        "stop": ((60, 0, 5), (70, 20, 15)),
        "bracket": ((300, 300, 0), (306, 306, 6)),
        # decoy: front face (y=44) centroid (30,44,25) area 16 — 4 mm from the
        # lid anchor's recorded center, used to force a cross-anchor
        "decoy": ((28, 44, 23), (32, 45, 27)),
        "cube0": ((200, 0, 0), (210, 10, 10)),
        "cube1": ((220, 0, 0), (230, 10, 10)),
        "cube2": ((240, 0, 0), (250, 10, 10)),
        "cube3": ((260, 0, 0), (270, 10, 10)),
    }
    return {
        name: _write_step(_mk_box(p0, p1), d / f"{name}.step")
        for name, (p0, p1) in boxes.items()
    }


SEL_BASE_TOP = {"kind": "faces_near_point", "point": (30.0, 19.75, 5.0)}
SEL_LID_INNER = {"kind": "faces_near_point", "point": (30.0, 40.0, 25.0)}
SEL_BOSS_TOP = {"kind": "faces_near_point", "point": (30.0, 22.0, 11.0)}
SEL_RAIL_TOP = {"kind": "faces_near_point", "point": (50.0, 10.0, 5.0)}
SEL_BLOCK_TOP = {"kind": "faces_near_point", "point": (5.0, 10.0, 15.0)}
SEL_STOP_TOP = {"kind": "faces_near_point", "point": (65.0, 10.0, 15.0)}

HINGE_FRAME = {"origin": (0.0, 40.0, 5.0), "axis": (1.0, 0.0, 0.0)}


def _assembly(steps, names):
    body = None
    for n in names:
        body = AddComponent().apply(
            body, {"name": n, "source_step_path": steps[n]}
        ).body
    return body


def _hinged(steps):
    """base + OPEN lid + boss; revolute(lid, base) at the back edge + fixed(boss, base)."""
    body = _assembly(steps, ["base", "lid", "boss"])
    body = MateTag().apply(body, {
        "kind": "revolute", "between": ("lid", "base"), "frame": HINGE_FRAME,
        "face_selector_a": SEL_LID_INNER, "face_selector_b": SEL_BASE_TOP,
    }).body
    body = MateTag().apply(body, {
        "kind": "fixed", "between": ("boss", "base"),
        "frame": {"origin": (30.0, 22.0, 5.0), "axis": (0.0, 0.0, 1.0)},
        "face_selector_a": SEL_BOSS_TOP, "face_selector_b": SEL_BASE_TOP,
    }).body
    return body


def _slider(steps):
    body = _assembly(steps, ["rail", "block", "stop"])
    body = MateTag().apply(body, {
        "kind": "slider", "between": ("block", "rail"),
        "frame": {"origin": (0.0, 0.0, 5.0), "axis": (1.0, 0.0, 0.0)},
        "face_selector_a": SEL_BLOCK_TOP, "face_selector_b": SEL_RAIL_TOP,
    }).body
    body = MateTag().apply(body, {
        "kind": "fixed", "between": ("stop", "rail"),
        "frame": {"origin": (65.0, 10.0, 5.0), "axis": (0.0, 0.0, 1.0)},
        "face_selector_a": SEL_STOP_TOP, "face_selector_b": SEL_RAIL_TOP,
    }).body
    return body


def _four_bar(steps):
    """ground-crank-coupler-rocker, 4 revolutes — a CLOSED loop (graph cycle)."""
    cubes = ["cube0", "cube1", "cube2", "cube3"]
    names = ["ground", "crank", "coupler", "rocker"]
    body = None
    for name, cube in zip(names, cubes):
        body = AddComponent().apply(
            body, {"name": name, "source_step_path": steps[cube]}
        ).body
    tops = {n: (205.0 + 20.0 * i, 5.0, 10.0) for i, n in enumerate(names)}
    pairs = [("crank", "ground"), ("coupler", "crank"),
             ("rocker", "coupler"), ("ground", "rocker")]
    for i, (a, b) in enumerate(pairs):
        body = MateTag().apply(body, {
            "kind": "revolute", "between": (a, b),
            "frame": {"origin": (210.0 + 20.0 * i, 5.0, 5.0), "axis": (0.0, 0.0, 1.0)},
            "face_selector_a": {"kind": "faces_near_point", "point": tops[a]},
            "face_selector_b": {"kind": "faces_near_point", "point": tops[b]},
        }).body
    return body


def _json_safe(extras: dict) -> dict:
    payload = {k: v for k, v in extras.items() if k != "_step_metrics"}
    return json.loads(json.dumps(payload, allow_nan=False))


def _sample_at(samples, value):
    return next(s for s in samples if abs(s["value"] - value) < 1e-9)


# ---------------------------------------------------------------------------
# mate_tag — record + codec + refusals
# ---------------------------------------------------------------------------

def test_mate_tag_records_kind_frame_between(steps):
    body = _hinged(steps)
    mates = list_kinematic_mates(body)
    assert [m["index"] for m in mates] == [0, 1]
    m0, m1 = mates
    assert m0["kind"] == "revolute" and m0["between"] == ["lid", "base"]
    assert m0["frame"] == {"origin": [0.0, 40.0, 5.0], "axis": [1.0, 0.0, 0.0]}
    assert m0["complete"] is True
    assert m1["kind"] == "fixed" and m1["between"] == ["boss", "base"]
    # anchors recorded with the true face areas (lid inner 60x40, boss top 20x6)
    assert m0["sides"]["a"]["recorded_area_mm2"] == pytest.approx(2400.0, abs=0.01)
    assert m1["sides"]["a"]["recorded_area_mm2"] == pytest.approx(120.0, abs=0.01)
    # both mates resolve cleanly right after recording
    assert resolve_mate(body, 0)["ok"] is True
    assert resolve_mate(body, 1)["ok"] is True
    # extras of a fresh mate_tag call are strict-JSON-safe
    r = MateTag().apply(_assembly(steps, ["base", "lid"]), {
        "kind": "revolute", "between": ("lid", "base"), "frame": HINGE_FRAME,
        "face_selector_a": SEL_LID_INNER, "face_selector_b": SEL_BASE_TOP,
    })
    assert _json_safe(r.extras)["mate"]["kind"] == "revolute"


def test_mate_codec_roundtrip():
    # exact float round-trip through the rebuild-immutable tag-name slot
    kind, origin, axis = decode_kind_frame(
        encode_kind_frame("slider", (0.1, -2.5e-07, 3.0), (0.0, 0.0, 1.0))
    )
    assert kind == "slider"
    assert origin == (0.1, -2.5e-07, 3.0) and axis == (0.0, 0.0, 1.0)
    # non-kinematic mate types are None (no false positives), never guessed
    assert decode_kind_frame("concentric") is None
    # a kinematic-looking but corrupt type raises — corruption is never masked
    with pytest.raises(ValueError, match="malformed"):
        decode_kind_frame("revolute@1.0,2.0@0.0,0.0,1.0,9.9")
    with pytest.raises(ValueError, match="malformed"):
        decode_kind_frame("hinge@0.0,0.0,0.0@0.0,0.0,1.0")
    with pytest.raises(ValueError, match="non-finite"):
        encode_kind_frame("revolute", (0.0, 0.0, float("nan")), (0.0, 0.0, 1.0))


def test_mate_tag_refusals(steps):
    body = _assembly(steps, ["base", "lid"])
    with pytest.raises(RuntimeError, match=r"fm\.zero_axis"):
        MateTag().apply(body, {
            "kind": "revolute", "between": ("lid", "base"),
            "frame": {"origin": (0.0, 40.0, 5.0), "axis": (0.0, 0.0, 0.0)},
            "face_selector_a": SEL_LID_INNER, "face_selector_b": SEL_BASE_TOP,
        })
    with pytest.raises(RuntimeError, match=r"fm\.same_component"):
        MateTag().apply(body, {
            "kind": "fixed", "between": ("lid", "lid"), "frame": HINGE_FRAME,
            "face_selector_a": SEL_LID_INNER, "face_selector_b": SEL_LID_INNER,
        })
    with pytest.raises(RuntimeError, match="not found"):
        MateTag().apply(body, {
            "kind": "revolute", "between": ("ghost", "base"), "frame": HINGE_FRAME,
            "face_selector_a": SEL_LID_INNER, "face_selector_b": SEL_BASE_TOP,
        })
    with pytest.raises(RuntimeError, match="matched 0 faces"):
        MateTag().apply(body, {
            "kind": "revolute", "between": ("lid", "base"), "frame": HINGE_FRAME,
            "face_selector_a": {"kind": "faces_near_point", "point": (999.0, 999.0, 999.0)},
            "face_selector_b": SEL_BASE_TOP,
        })


# ---------------------------------------------------------------------------
# persistence pins — compound rebuild + STEP round-trip
# ---------------------------------------------------------------------------

def test_mate_survives_compound_rebuild(steps):
    body = _hinged(steps)
    before = list_kinematic_mates(body)
    rebuilt = AddComponent().apply(
        body, {"name": "bracket", "source_step_path": steps["bracket"]}
    ).body  # add_component constructs a brand-new Part(TopoDS_Compound)

    after = list_kinematic_mates(rebuilt)
    assert [(m["kind"], m["between"], m["frame"]) for m in after] == \
           [(m["kind"], m["between"], m["frame"]) for m in before]
    assert all(m["complete"] for m in after)
    # anchors re-resolve onto the RIGHT faces (recorded-area guard passes)
    for idx in (0, 1):
        res = resolve_mate(rebuilt, idx)
        assert res["ok"] is True
    assert res["sides"]["a"]["resolved_area_mm2"] == pytest.approx(120.0, abs=0.01)
    # DOF accounting still works on the rebuilt compound (bracket floats free)
    dof = AssemblyDof().apply(rebuilt, {"ground": "base"}).extras
    assert dof["mobility"] == 7 and dof["total_dof"] == 13


def _reimport_via_step(body, tmp_path):
    """STEP round-trip helper: geometry through STEP, mate store via sidecar."""
    from build123d import Part
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    def centroid(shape):
        p = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, p)
        c = p.CentreOfMass()
        return (c.X(), c.Y(), c.Z())

    from phone_designer.skills.assembly._compound import list_components

    originals = list_components(body)
    path = _write_step(body.wrapped, tmp_path / "roundtrip.step")
    shape = load_step_shape(path)
    solids = list(iter_solid_components(shape))
    assert len(solids) == len(originals)

    matched = {}
    for solid in solids:
        c = centroid(solid)
        name = next(
            n for n, orig in originals
            if math.dist(c, centroid(orig)) < 1e-3
        )
        assert name not in matched  # bijection
        matched[name] = solid
    names = [n for n, _ in originals]
    new_body = Part(build_compound([matched[n] for n in names]))
    new_body._pd_component_names = list(names)
    return new_body


def test_mate_survives_step_roundtrip(steps, tmp_path):
    body = _hinged(steps)
    before = list_kinematic_mates(body)

    # the sidecar itself must be strict-JSON-safe (it IS the round-trip vehicle)
    sidecar = json.loads(json.dumps(serialize_mate_tags(body), allow_nan=False))
    assert len(sidecar["tags"]) == 4  # 2 mates x 2 sides

    reimported = _reimport_via_step(body, tmp_path)
    assert list_kinematic_mates(reimported) == []  # honest: tags are NOT in STEP
    assert restore_mate_tags(reimported, sidecar) == 4

    after = list_kinematic_mates(reimported)
    assert [(m["kind"], m["between"], m["frame"]) for m in after] == \
           [(m["kind"], m["between"], m["frame"]) for m in before]
    # anchors re-resolve GEOMETRICALLY on the reimported faces, guard passes
    res = resolve_mate(reimported, 0)
    assert res["ok"] is True
    assert res["sides"]["a"]["resolved_area_mm2"] == pytest.approx(2400.0, abs=0.1)
    assert res["sides"]["b"]["resolved_area_mm2"] == pytest.approx(
        60.0 * 39.5, abs=0.1)

    # and the reimported assembly still finds the SAME first-contact angle
    dof = AssemblyDof().apply(reimported, {"ground": "base"}).extras
    assert dof["mobility"] == 1
    sweep = KinematicSweep().apply(reimported, {
        "mate_index": 0, "start": 0.0, "end": 90.0, "n_samples": 10,
    }).extras
    assert sweep["first_contact"]["value"] == pytest.approx(70.0)
    assert 0.0 < sweep["first_contact"]["value"] - HINGE_EXPECTED_DEG <= 10.0


# ---------------------------------------------------------------------------
# assembly_dof — bookkeeping + refusals
# ---------------------------------------------------------------------------

def test_assembly_dof_hinged_lid(steps):
    body = _hinged(steps)
    extras = AssemblyDof().apply(body, {"ground": "base"}).extras
    # 3 components x 6 = 18; revolute constrains 5, fixed constrains 6
    assert extras["n_components"] == 3 and extras["n_mates"] == 2
    assert extras["total_dof"] == 18 - 11 == 7
    assert extras["mobility"] == 12 - 11 == 1
    per = {p["name"]: p for p in extras["per_component"]}
    assert per["base"]["is_ground"] and per["base"]["dof_relative_to_ground"] == 0
    assert per["lid"]["dof_relative_to_ground"] == 1
    assert per["lid"]["joint_path"] == [0]
    assert per["boss"]["dof_relative_to_ground"] == 0
    assert per["boss"]["joint_path"] == [1]
    assert all(p["connected_to_ground"] for p in extras["per_component"])
    assert {m["index"]: m["constrained_dof"] for m in extras["mates"]} == {0: 5, 1: 6}
    _json_safe(extras)

    # path-sum semantics with a different ground: from the lid, BOTH base and
    # boss sit behind the same revolute (chain), so each carries 1 DOF.
    per2 = {
        p["name"]: p["dof_relative_to_ground"]
        for p in AssemblyDof().apply(body, {"ground": "lid"}).extras["per_component"]
    }
    assert per2 == {"lid": 0, "base": 1, "boss": 1}


def test_assembly_dof_floating_component(steps):
    body = AddComponent().apply(
        _hinged(steps), {"name": "bracket", "source_step_path": steps["bracket"]}
    ).body
    extras = AssemblyDof().apply(body, {"ground": "base"}).extras
    assert extras["total_dof"] == 24 - 11 == 13
    assert extras["mobility"] == 18 - 11 == 7
    per = {p["name"]: p for p in extras["per_component"]}
    assert per["bracket"]["connected_to_ground"] is False
    assert per["bracket"]["dof_relative_to_ground"] == 6  # honest free floater


def test_assembly_dof_closed_loop_refusal(steps):
    with pytest.raises(RuntimeError, match=r"fm\.closed_loop"):
        AssemblyDof().apply(_four_bar(steps), {})
    # a parallel second mate between an already-mated pair is also a cycle
    body = _hinged(steps)
    body = MateTag().apply(body, {
        "kind": "revolute", "between": ("lid", "base"), "frame": HINGE_FRAME,
        "face_selector_a": SEL_LID_INNER, "face_selector_b": SEL_BASE_TOP,
    }).body
    with pytest.raises(RuntimeError, match=r"fm\.closed_loop"):
        AssemblyDof().apply(body, {})


def test_assembly_dof_unsupported_incomplete_and_ground_refusals(steps):
    # a mate recorded outside mate_tag (plain type) has no DOF semantics
    body = _hinged(steps)
    record_mate(body, "concentric", "lid", "base", SEL_LID_INNER, SEL_BASE_TOP)
    with pytest.raises(RuntimeError, match=r"fm\.unsupported_mate_kind"):
        AssemblyDof().apply(body, {})

    # a side dropped by tag propagation -> honest incomplete, never guessed
    body2 = _hinged(steps)
    tags = dict(get_tags(body2))
    side_a = next(
        n for n in tags
        if parse_mate_tag(n) and parse_mate_tag(n)["index"] == 0
        and parse_mate_tag(n)["side"] == "a"
    )
    tags.pop(side_a)
    set_tags(body2, tags)
    with pytest.raises(RuntimeError, match=r"fm\.incomplete_mate"):
        AssemblyDof().apply(body2, {})

    with pytest.raises(RuntimeError, match=r"fm\.component_not_found"):
        AssemblyDof().apply(_hinged(steps), {"ground": "ghost"})


# ---------------------------------------------------------------------------
# kinematic_sweep — THE ship pins
# ---------------------------------------------------------------------------

def test_kinematic_sweep_hinged_lid_finds_analytic_angle(steps):
    """Analytic theta_c = atan2(15, 6) = 68.1986 deg; sweep at 2 deg resolution
    must find first contact at 70.0 (the first sample PAST the true angle)."""
    extras = KinematicSweep().apply(_hinged(steps), {
        "mate_index": 0, "start": 0.0, "end": 90.0, "n_samples": 46,
    }).extras

    assert extras["units"] == "deg"
    assert extras["moving_components"] == ["lid"]
    assert extras["static_components"] == ["base", "boss"]
    assert extras["resolution"] == pytest.approx(2.0)

    fc = extras["first_contact"]
    assert fc is not None
    found, expected = fc["value"], HINGE_EXPECTED_DEG
    assert expected == pytest.approx(68.19859, abs=1e-4)   # the derivation
    assert found == pytest.approx(70.0)                    # what the sweep found
    assert 0.0 < found - expected <= extras["resolution"]  # within resolution
    assert fc["previous_clear_value"] == pytest.approx(68.0)

    samples = extras["samples"]
    s0 = _sample_at(samples, 0.0)
    # min clearance at theta=0 is the 0.5 mm hinge gap (lid-base), exactly
    assert s0["contact"] is False
    assert s0["min_clearance_mm"] == pytest.approx(0.5, abs=1e-6)
    s68 = _sample_at(samples, 68.0)
    # 0.2 deg before contact: lid-boss gap ~ 16.156*sin(0.1986deg) = 0.056 mm
    assert s68["contact"] is False
    assert 0.0 < s68["min_clearance_mm"] < 0.4
    s70 = _sample_at(samples, 70.0)
    assert s70["contact"] is True and s70["min_clearance_mm"] == 0.0
    assert s70["worst_pair"] == ["lid", "boss"]
    # exact analytic wedge volume of the penetration at 70 deg
    assert s70["overlap_volume_mm3"] == pytest.approx(HINGE_OVERLAP_AT_70, abs=0.02)
    assert HINGE_OVERLAP_AT_70 == pytest.approx(8.0249, abs=0.001)
    _json_safe(extras)


def test_kinematic_sweep_slider_linear_first_contact(steps):
    """Analytic contact at u = 50.0 mm exactly ON a sample: the exact face
    touch has zero common volume so it is honestly NOT contact; first contact
    lands one 2 mm step later with volume 2*20*10 = 400 mm^3."""
    extras = KinematicSweep().apply(_slider(steps), {
        "mate_index": 0, "start": 0.0, "end": 80.0, "n_samples": 41,
    }).extras

    assert extras["units"] == "mm"
    assert extras["moving_components"] == ["block"]
    assert extras["static_components"] == ["rail", "stop"]

    fc = extras["first_contact"]
    found, expected = fc["value"], SLIDER_EXPECTED_MM
    assert found == pytest.approx(52.0)
    assert 0.0 < found - expected <= extras["resolution"] == pytest.approx(2.0)

    samples = extras["samples"]
    s50 = _sample_at(samples, 50.0)   # exact face-on-face touch
    assert s50["contact"] is False
    assert s50["overlap_volume_mm3"] == pytest.approx(0.0, abs=1e-6)
    s52 = _sample_at(samples, 52.0)
    assert s52["contact"] is True
    assert s52["overlap_volume_mm3"] == pytest.approx(400.0, abs=0.01)
    assert s52["worst_pair"] == ["block", "stop"]
    _json_safe(extras)


def test_kinematic_sweep_refusals(steps):
    sweep_args = {"mate_index": 0, "start": 0.0, "end": 90.0, "n_samples": 4}

    with pytest.raises(RuntimeError, match=r"fm\.no_mates"):
        KinematicSweep().apply(_assembly(steps, ["base", "bracket"]), sweep_args)
    with pytest.raises(RuntimeError, match=r"fm\.mate_not_found"):
        KinematicSweep().apply(_hinged(steps), {**sweep_args, "mate_index": 99})
    with pytest.raises(RuntimeError, match=r"fm\.mate_not_drivable"):
        KinematicSweep().apply(_hinged(steps), {**sweep_args, "mate_index": 1})
    with pytest.raises(RuntimeError, match=r"fm\.empty_range"):
        KinematicSweep().apply(_hinged(steps), {**sweep_args, "end": 0.0})
    with pytest.raises(RuntimeError, match=r"fm\.closed_loop"):
        KinematicSweep().apply(_four_bar(steps), sweep_args)
    with pytest.raises(Exception):  # pydantic: n_samples >= 2
        KinematicSweep().apply(_hinged(steps), {**sweep_args, "n_samples": 1})


def test_kinematic_sweep_stale_anchor_is_refused_not_driven(steps):
    """Move the lid 6 mm (> the 5 mm propagation TOL) with a decoy face 4 mm
    from the old anchor center: propagate_tags silently cross-anchors the lid
    ref onto the decoy (the spike's failure mode). The recorded-area guard
    turns that into an explicit refusal — the sweep never drives a mate whose
    world frame can no longer be trusted."""
    body = AddComponent().apply(
        _hinged(steps), {"name": "decoy", "source_step_path": steps["decoy"]}
    ).body
    moved = MoveComponent().apply(
        body, {"name": "lid", "translation": (0.0, 6.0, 0.0)}
    ).body
    res = resolve_mate(moved, 0)
    assert res["ok"] is False  # guard sees the cross-anchor (area 16 vs 2400)
    with pytest.raises(RuntimeError, match=r"fm\.mate_anchor_stale"):
        KinematicSweep().apply(moved, {
            "mate_index": 0, "start": 0.0, "end": 90.0, "n_samples": 4,
        })
