"""gdt_position — datum reference frame (DRF), axis-tilt, and MMC bonus.

Imports the skill module DIRECTLY (no manifest round-trip).

Fixtures are built with build123d; datum planes are assigned through the
datum_plane_assign skill and its ``extras['datums']`` table is handed to
gdt_position via ``datum_table``.
"""
from __future__ import annotations

import math

from build123d import Axis, Box, Cylinder, Pos

from phone_designer.skills.inspect.datum_plane_assign import DatumPlaneAssign
from phone_designer.skills.inspect.gdt_position import GdtPosition


# Hole-wall lateral area for a ⌀4–⌀4.3 hole through a 10 mm plate is
# ≈ 125–136 mm²; the 40×40×10 box faces are 400 / ≈1587 mm² → unique match.
_HOLE_WALL_SELECTOR = {"kind": "faces_by_area", "min": 100.0, "max": 200.0}


def _rot_zx(p, deg_z: float, deg_x: float):
    """Right-handed rotation: first about world Z, then about world X —
    mirrors ``shape.rotate(Axis.Z, deg_z).rotate(Axis.X, deg_x)``."""
    x, y, z = p
    cz, sz = math.cos(math.radians(deg_z)), math.sin(math.radians(deg_z))
    x, y = cz * x - sz * y, sz * x + cz * y
    cx, sx = math.cos(math.radians(deg_x)), math.sin(math.radians(deg_x))
    y, z = cx * y - sx * z, sx * y + cx * z
    return (x, y, z)


# ──────────────────────────────────────────────────────────────────────────────
# (a) DRF on a rotated plate — hand-computed deviation


def test_gdt_position_drf_rotated_plate_hand_computed():
    """40×40×10 plate (centered), through-hole at local (5.1, 3.0), whole part
    rotated Rz(30°) then Rx(20°).

    Datums (assigned on the ROTATED part): A = top (+Z local), B = +X face,
    C = +Y face. 3-2-1 DRF origin = corner (20, 20, 5) local; +X/+Y of the
    DRF = local +X/+Y. Hole axis in DRF coords: (5.1−20, 3.0−20) =
    (−14.9, −17.0). Against target (−15.0, −17.0):

        deviation = sqrt(0.1² + 0²) = 0.1   →   diametral zone = 0.2
    """
    plate = Box(40, 40, 10) - Pos(5.1, 3.0, 0) * Cylinder(2.0, 30)
    part = plate.rotate(Axis.Z, 30).rotate(Axis.X, 20)

    n_a = _rot_zx((0.0, 0.0, 1.0), 30, 20)
    n_b = _rot_zx((1.0, 0.0, 0.0), 30, 20)
    n_c = _rot_zx((0.0, 1.0, 0.0), 30, 20)

    datum_res = DatumPlaneAssign().apply(part, {"assignments": [
        {"label": "A", "face_selector": {
            "kind": "faces_by_normal", "direction": n_a, "tol_deg": 1.0}},
        {"label": "B", "face_selector": {
            "kind": "faces_by_normal", "direction": n_b, "tol_deg": 1.0}},
        {"label": "C", "face_selector": {
            "kind": "faces_by_normal", "direction": n_c, "tol_deg": 1.0}},
    ]})
    datums = datum_res.extras["datums"]
    assert set(datums.keys()) == {"A", "B", "C"}
    assert datum_res.extras["warnings"] == []

    r = GdtPosition().apply(part, {
        "face_selector": _HOLE_WALL_SELECTOR,
        "target_xy": (-15.0, -17.0),
        "tolerance_mm": 0.25,
        "datum_refs": ["A", "B", "C"],
        "datum_table": datums,
    })
    p = r.extras["position"]

    assert abs(p["deviation_mm"] - 0.1) <= 1e-6
    assert abs(p["diametral_zone_mm"] - 0.2) <= 1e-6
    assert p["pass"] is True
    assert p["verdict"] == "pass"
    assert abs(p["hole_radius_mm"] - 2.0) <= 1e-6
    assert r.body is part

    # The DRF itself: origin at the rotated (20, 20, 5) corner, axes = the
    # rotated local axes.
    drf = p["drf_used"]
    assert drf["labels"] == ["A", "B", "C"]
    for got, exp in zip(drf["origin"], _rot_zx((20.0, 20.0, 5.0), 30, 20)):
        assert abs(got - exp) <= 1e-6
    for axis_key, local in (("z_axis", (0.0, 0.0, 1.0)),
                            ("x_axis", (1.0, 0.0, 0.0)),
                            ("y_axis", (0.0, 1.0, 0.0))):
        for got, exp in zip(drf[axis_key], _rot_zx(local, 30, 20)):
            assert abs(got - exp) <= 1e-6

    # actual_xy is reported in DRF coordinates.
    assert abs(p["actual_xy"][0] - (-14.9)) <= 1e-6
    assert abs(p["actual_xy"][1] - (-17.0)) <= 1e-6


def test_gdt_position_drf_missing_datum_raises():
    plate = Box(40, 40, 10) - Cylinder(2.0, 30)
    try:
        GdtPosition().apply(plate, {
            "face_selector": _HOLE_WALL_SELECTOR,
            "target_xy": (0.0, 0.0),
            "datum_refs": ["A", "B"],
            "datum_table": {},
        })
        raised = False
    except ValueError as ex:
        raised = True
        assert "datum_refs" in str(ex)
    assert raised


# ──────────────────────────────────────────────────────────────────────────────
# (b) axis tilt — deviation grows with depth


def test_gdt_position_axis_tilt_end_eval_exceeds_mid_eval():
    """Hole axis tilted 1° about X, crossing the target (0,0) exactly at the
    plate's mid-depth (z = 0). A single mid-depth evaluation reports ~0; the
    ends sit ≈ 5·tan(1°) ≈ 0.0873 mm off → end-eval must dominate.
    """
    plate = Box(40, 40, 10)
    part = plate - Cylinder(2.0, 40).rotate(Axis.X, 1.0)

    r = GdtPosition().apply(part, {
        "face_selector": _HOLE_WALL_SELECTOR,
        "target_xy": (0.0, 0.0),
        "tolerance_mm": 0.1,
        "evaluate_axis_tilt": True,
    })
    p = r.extras["position"]

    ends = p["deviation_at_ends_mm"]
    mid = p["deviation_mid_mm"]
    assert ends is not None and len(ends) == 2
    assert mid is not None

    # Single-point eval at mid-depth would report ~0 …
    assert mid <= 1e-6
    # … while both depth ends show the real tilt error (≈ 0.0873 mm, plus the
    # elliptical trim extension ≈ r·tan(1°)).
    assert min(ends) > 0.08
    assert max(ends) < 0.12
    # end-eval > mid-eval, and the worst end drives the reported deviation.
    assert max(ends) > mid
    assert abs(p["deviation_mm"] - max(ends)) <= 1e-9
    # diametral zone ≈ 0.175 mm > 0.1 mm → tilt makes the hole fail.
    assert p["pass"] is False
    assert p["verdict"] == "fail"


def test_gdt_position_tilt_extras_absent_on_fast_path():
    """Back-compat fast path: no tilt eval / no datums → new extras are
    inert (None / 0.0) and deviation matches the legacy anchor evaluation."""
    plate = Box(40, 40, 10)
    part = plate - Pos(5.0, 0.0, 0.0) * Cylinder(2.0, 30)

    p = GdtPosition().apply(part, {
        "face_selector": _HOLE_WALL_SELECTOR,
        "target_xy": (0.0, 0.0),
        "tolerance_mm": 0.5,
    }).extras["position"]

    assert abs(p["deviation_mm"] - 5.0) <= 1e-6
    assert p["pass"] is False
    assert p["drf_used"] is None
    assert p["deviation_at_ends_mm"] is None
    assert p["deviation_mid_mm"] is None
    assert p["bonus_tolerance_mm"] == 0.0
    assert p["adjusted_tolerance_mm"] == p["tolerance_mm"]


# ──────────────────────────────────────────────────────────────────────────────
# (c) MMC bonus — oversize hole passes at MMC where RFS fails


def test_gdt_position_mmc_bonus_oversize_hole_passes():
    """Hole nominal ⌀4.0 at MMC, drilled ⌀4.3 and 0.15 mm off target.

    Hand computation:
        deviation        = 0.15  → diametral zone = 0.30
        stated tolerance = 0.20  → RFS: 0.30 > 0.20  → FAIL
        MMC bonus        = |4.3 − 4.0| = 0.30
        adjusted         = 0.20 + 0.30 = 0.50 ≥ 0.30 → MMC: PASS
    """
    plate = Box(40, 40, 10)
    part = plate - Pos(0.15, 0.0, 0.0) * Cylinder(2.15, 30)

    base = {
        "face_selector": _HOLE_WALL_SELECTOR,
        "target_xy": (0.0, 0.0),
        "tolerance_mm": 0.2,
    }

    # RFS (no modifier): fails.
    rfs = GdtPosition().apply(part, dict(base)).extras["position"]
    assert abs(rfs["deviation_mm"] - 0.15) <= 1e-6
    assert abs(rfs["diametral_zone_mm"] - 0.3) <= 1e-6
    assert rfs["bonus_tolerance_mm"] == 0.0
    assert rfs["pass"] is False
    assert rfs["verdict"] == "fail"

    # MMC with explicit measured/nominal diameters: bonus rescues it.
    mmc = GdtPosition().apply(part, dict(
        base,
        material_condition="MMC",
        measured_diameter_mm=4.3,
        nominal_diameter_mm=4.0,
    )).extras["position"]
    assert abs(mmc["bonus_tolerance_mm"] - 0.3) <= 1e-9
    assert abs(mmc["adjusted_tolerance_mm"] - 0.5) <= 1e-9
    assert abs(mmc["diametral_zone_mm"] - 0.3) <= 1e-6
    assert mmc["pass"] is True
    assert mmc["verdict"] == "pass"
    assert mmc["material_condition"] == "MMC"

    # measured_diameter_mm omitted → measured from the face (⌀4.3) via
    # mmc_lmc_modifier._feature_size; same verdict.
    mmc2 = GdtPosition().apply(part, dict(
        base,
        material_condition="MMC",
        nominal_diameter_mm=4.0,
    )).extras["position"]
    assert abs(mmc2["bonus_tolerance_mm"] - 0.3) <= 1e-6
    assert mmc2["pass"] is True


def test_gdt_position_mmc_requires_nominal():
    plate = Box(40, 40, 10)
    part = plate - Cylinder(2.0, 30)
    try:
        GdtPosition().apply(part, {
            "face_selector": _HOLE_WALL_SELECTOR,
            "target_xy": (0.0, 0.0),
            "material_condition": "MMC",
        })
        raised = False
    except ValueError as ex:
        raised = True
        assert "nominal_diameter_mm" in str(ex)
    assert raised
