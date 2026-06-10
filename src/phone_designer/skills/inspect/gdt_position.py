"""gdt_position — atomic, read-only.

GD&T position (true position) tolerance check for a hole axis. The matched
face must be cylindrical (hole or boss surface). The actual XY position of
the hole axis is compared against the nominal ``target_xy`` and the radial
deviation is computed:

    deviation_mm = sqrt((axis_x − target_x)² + (axis_y − target_y)²)

ASME Y14.5 convention: the position tolerance value is a diameter, so a hole
passes if ``2 × deviation_mm ≤ tolerance_mm``. ``tolerance_mm`` here is the
DIAMETRAL position-tolerance zone (the standard interpretation). We also
report ``deviation_mm`` (radial) for clarity.

Strategy:
    1. Resolve face_selector → first cylindrical face.
    2. Pull analytic cylinder axis (origin + direction) from OCCT.
    3. Project axis origin to z = target_z plane (or use as-is for XY only).
    4. Compute radial offset in XY plane vs target.

Datum reference frame (DRF) mode — ``datum_refs``
-------------------------------------------------
When ``datum_refs`` (e.g. ``["A", "B", "C"]``, precedence order) is given,
the deviation is evaluated in the 3-2-1 ASME Y14.5 datum reference frame
instead of world XY:

    * primary datum plane normal      → DRF +Z
    * secondary plane normal, projected orthogonal to +Z → DRF +X
    * DRF +Y = Z × X
    * origin: primary face center, slid along +X onto the secondary plane,
      then along +Y onto the tertiary plane. Because +X ⊥ primary normal and
      +Y carries no secondary-normal component, this is the exact 3-plane
      intersection for a full A|B|C frame (and a deterministic partial fix
      for 1- or 2-datum frames).

Datum geometry is looked up by label, first in the ``datum_table`` arg
(pass ``extras["datums"]`` from ``datum_plane_assign`` — entries carry
``face_center``/``normal``), else in ``body._pd_datums`` (populated by
``datum_target_assign`` — entries carry ``center``/``normal``).
``target_xy`` is then interpreted in DRF coordinates.

Axis-tilt evaluation — ``evaluate_axis_tilt``
---------------------------------------------
A tilted hole can sit dead-on target at one depth yet violate position at the
surface. With ``evaluate_axis_tilt=True`` the axis is evaluated at BOTH ends
of the cylindrical face's trimmed V-range (axis-parameter extent ≙ hole
depth); the worst end drives ``deviation_mm`` / pass-fail. The mid-depth
value is reported as ``deviation_mid_mm`` for comparison.

MMC / LMC bonus — ``material_condition``
----------------------------------------
With ``material_condition`` (``"MMC"`` | ``"LMC"``) and
``nominal_diameter_mm`` (the MMC/LMC size limit), the bonus tolerance follows
mmc_lmc_modifier's documented budget:

    bonus = abs(measured_size − MMC/LMC_size)

``measured_diameter_mm`` defaults to the as-modelled face diameter via
mmc_lmc_modifier._feature_size. The verdict then compares the diametral zone
against ``tolerance_mm + bonus`` (size-limit conformance itself is the size
check's job, not this skill's).

extras schema:
    {"position": {
        "deviation_mm": float,           # radial offset (worst end if tilt eval)
        "diametral_zone_mm": float,      # 2 * radial offset
        "pass": bool,
        "verdict": "pass" | "fail",
        "tolerance_mm": float,
        "bonus_tolerance_mm": float,         # 0.0 unless material_condition
        "adjusted_tolerance_mm": float,      # tolerance_mm + bonus
        "material_condition": "MMC"|"LMC"|None,
        "actual_xy": [x,y],              # axis anchor, in evaluation frame
        "target_xy": [x,y],
        "axis_direction": [x,y,z],       # world coords
        "hole_radius_mm": float,
        "drf_used": {"labels": [...], "origin": [...], "x_axis": [...],
                     "y_axis": [...], "z_axis": [...]} | None,
        "deviation_at_ends_mm": [d_end0, d_end1] | None,   # tilt eval only
        "deviation_mid_mm": float | None                   # tilt eval only
     }}
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorBase, selector_from_dict
from phone_designer.skills._spec import SkillBase, SkillResult


_EPS = 1e-9


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _coerce_selector(s: Any) -> SelectorBase:
    if isinstance(s, SelectorBase):
        return s
    if isinstance(s, dict):
        return selector_from_dict(s)
    raise TypeError(f"unsupported selector type: {type(s).__name__}")


def _cylinder_axis(face) -> tuple[tuple[float, float, float],
                                    tuple[float, float, float],
                                    float]:
    """Extract (axis_origin, axis_dir_unit, radius) from a cylindrical face.

    Raises ValueError if face is not a cylinder.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Cylinder:
        raise ValueError(
            "gdt_position requires a cylindrical face (the hole/boss surface)"
        )
    cyl = surf.Cylinder()
    ax = cyl.Axis().Direction()
    loc = cyl.Location()
    r = float(cyl.Radius())
    return (
        (loc.X(), loc.Y(), loc.Z()),
        (ax.X(), ax.Y(), ax.Z()),
        r,
    )


def _axis_v_range(face) -> tuple[float, float] | None:
    """Trimmed V-parameter range of a cylindrical face.

    For OCCT's cylinder parametrisation P(u, v) = loc + v·dir + r·(...), the
    V range is the feature's extent along the axis (≙ hole depth, measured
    from the surface location). Returns None when unavailable/degenerate.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Cylinder:
        return None
    v0 = float(surf.FirstVParameter())
    v1 = float(surf.LastVParameter())
    if not (math.isfinite(v0) and math.isfinite(v1)):
        return None
    if abs(v1 - v0) < _EPS:
        return None
    return (v0, v1)


# ── small vector helpers (3-tuples, no numpy dependency) ─────────────────────


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, s: float):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _unit(a):
    m = math.sqrt(_dot(a, a))
    if m < _EPS:
        raise ValueError("gdt_position: zero-length vector in DRF construction")
    return _scale(a, 1.0 / m)


def _resolve_datum_face_geometry(
    shape, body, entry: dict,
) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    """Full-precision (center, unit_normal) by re-resolving the datum's face.

    Datum tables store centers/normals rounded to 6 decimals — fine for
    labelling, but it costs ~1e-5 on a 20 mm lever arm. When the entry carries
    its ``selector`` (datum_plane_assign) or ``face_idx``
    (datum_target_assign), re-resolve the face on the live shape instead.
    Returns None when re-resolution is not possible.
    """
    from phone_designer.skills._resolvers import (
        _all_faces,
        _face_center,
        _face_normal_at_center,
        resolve_faces,
    )

    face = None
    sel_raw = entry.get("selector")
    if isinstance(sel_raw, dict):
        try:
            faces = resolve_faces(shape, _coerce_selector(sel_raw), body=body)
            face = faces[0] if faces else None
        except Exception:
            face = None
    if face is None:
        idx = entry.get("face_idx", -1)
        if isinstance(idx, int) and idx >= 0:
            try:
                all_faces = _all_faces(shape)
                if idx < len(all_faces):
                    face = all_faces[idx]
            except Exception:
                face = None
    if face is None:
        return None
    try:
        n = _face_normal_at_center(face)
        if n == (0.0, 0.0, 0.0):
            return None  # non-planar — let the caller fall back / fail
        return _face_center(face), _unit(n)
    except Exception:
        return None


def _datum_entry_geometry(
    label: str, entry: dict,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Normalise a datum-table entry to (center, unit_normal).

    Accepts both datum_plane_assign entries (``face_center``/``normal``) and
    datum_target_assign body._pd_datums entries (``center``/``normal``).
    """
    center = entry.get("face_center", entry.get("center"))
    normal = entry.get("normal")
    if center is None or normal is None:
        raise ValueError(
            f"gdt_position: datum '{label}' entry lacks center/normal "
            f"(keys present: {sorted(entry.keys())})"
        )
    if entry.get("is_planar") is False:
        raise ValueError(f"gdt_position: datum '{label}' is not planar")
    c = (float(center[0]), float(center[1]), float(center[2]))
    n = (float(normal[0]), float(normal[1]), float(normal[2]))
    if math.sqrt(_dot(n, n)) < _EPS:
        raise ValueError(f"gdt_position: datum '{label}' has a zero normal")
    return c, _unit(n)


def _build_drf(
    datum_geoms: list[tuple[str, tuple[float, float, float], tuple[float, float, float]]],
) -> tuple[tuple[float, float, float], tuple[float, float, float],
           tuple[float, float, float], tuple[float, float, float]]:
    """3-2-1 DRF (ASME Y14.5) from [(label, center, unit_normal), ...] in
    precedence order. Returns (origin, x_axis, y_axis, z_axis).

    primary normal → +Z; secondary normal projected ⊥Z → +X; +Y = Z×X.
    Origin: primary center slid along +X onto the secondary plane, then along
    +Y onto the tertiary plane — exact 3-plane intersection for 3 datums
    (sliding along +X keeps the point on the primary plane; sliding along +Y
    keeps it on both, since the secondary normal has no +Y component).
    """
    labels = [g[0] for g in datum_geoms]
    c1, n1 = datum_geoms[0][1], datum_geoms[0][2]
    z = _unit(n1)

    if len(datum_geoms) >= 2:
        n2 = datum_geoms[1][2]
        x_raw = _sub(n2, _scale(z, _dot(n2, z)))
        if math.sqrt(_dot(x_raw, x_raw)) < 1e-6:
            raise ValueError(
                f"gdt_position: secondary datum '{labels[1]}' is parallel to "
                f"primary '{labels[0]}' — cannot build the DRF x-axis"
            )
        x = _unit(x_raw)
    else:
        # Under-constrained frame: stable in-plane x from world X (else Y).
        x = None
        for seed in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)):
            x_raw = _sub(seed, _scale(z, _dot(seed, z)))
            if math.sqrt(_dot(x_raw, x_raw)) > 1e-6:
                x = _unit(x_raw)
                break
        assert x is not None  # two seeds cannot both be parallel to z

    y = _cross(z, x)

    origin = c1
    if len(datum_geoms) >= 2:
        c2, n2 = datum_geoms[1][1], datum_geoms[1][2]
        denom = _dot(n2, x)  # = |projection of n2 ⊥ z| > 1e-6 by construction
        origin = _add(origin, _scale(x, (_dot(n2, c2) - _dot(n2, origin)) / denom))
    if len(datum_geoms) >= 3:
        c3, n3 = datum_geoms[2][1], datum_geoms[2][2]
        denom = _dot(n3, y)
        if abs(denom) < 1e-6:
            raise ValueError(
                f"gdt_position: tertiary datum '{labels[2]}' cannot fix the "
                f"DRF origin (its normal is orthogonal to the DRF y-axis)"
            )
        origin = _add(origin, _scale(y, (_dot(n3, c3) - _dot(n3, origin)) / denom))

    return origin, x, y, z


@skill(
    name="gdt_position",
    category="inspect",
    level="atomic",
    summary="GD&T true-position: actual hole-axis XY vs target_xy. Deviation "
            "is radial offset; diametral zone = 2 × offset, compared to "
            "tolerance_mm (ASME Y14.5 diameter zone). Optional datum_refs "
            "evaluate in a 3-2-1 datum reference frame, evaluate_axis_tilt "
            "checks both hole-depth ends, and material_condition MMC/LMC adds "
            "bonus tolerance. Read-only — body unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["gdt_position_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class GdtPosition(SkillBase):
    class Args(BaseModel):
        face_selector: dict
        target_xy: tuple[float, float]
        tolerance_mm: float = Field(default=0.1, ge=0.0,
                                     description="Diametral position-tolerance zone (ASME Y14.5).")
        datum_refs: list[str] | None = Field(
            default=None,
            description="Datum plane labels in precedence order (e.g. "
                        "['A','B','C']). Builds a 3-2-1 DRF; target_xy is "
                        "then in DRF coordinates.")
        datum_table: dict[str, dict] | None = Field(
            default=None,
            description="Datum geometry by label — pass extras['datums'] from "
                        "datum_plane_assign. Defaults to body._pd_datums "
                        "(datum_target_assign).")
        evaluate_axis_tilt: bool = Field(
            default=False,
            description="Evaluate true position at both ends of the hole "
                        "depth and report the worst (catches tilted axes).")
        material_condition: Literal["MMC", "LMC"] | None = Field(
            default=None,
            description="Material-condition modifier on the position "
                        "tolerance — adds bonus = |measured − nominal| dia.")
        measured_diameter_mm: float | None = Field(
            default=None, gt=0.0,
            description="As-measured feature diameter; defaults to the "
                        "matched face's modelled diameter.")
        nominal_diameter_mm: float | None = Field(
            default=None, gt=0.0,
            description="MMC/LMC size limit the bonus is budgeted against. "
                        "Required when material_condition is set.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import resolve_faces

        shape = _occt_shape(body)
        sel = _coerce_selector(args.face_selector)
        faces = resolve_faces(shape, sel, body=body)
        if not faces:
            raise ValueError(
                f"gdt_position: face_selector matched 0 faces (kind={sel.kind})"
            )
        face = faces[0]

        origin, axis, radius = _cylinder_axis(face)
        tx, ty = float(args.target_xy[0]), float(args.target_xy[1])

        # ── evaluation frame: world XY (fast path) or datum reference frame ──
        drf_info: dict[str, Any] | None = None
        if args.datum_refs:
            table = args.datum_table
            if table is None:
                try:
                    table = dict(getattr(body, "_pd_datums", {}) or {})
                except Exception:
                    table = {}
            geoms = []
            missing = []
            for label in args.datum_refs:
                entry = table.get(label)
                if entry is None:
                    missing.append(label)
                    continue
                geom = _resolve_datum_face_geometry(shape, body, entry)
                if geom is None:
                    geom = _datum_entry_geometry(label, entry)
                geoms.append((label, geom[0], geom[1]))
            if missing:
                raise ValueError(
                    f"gdt_position: datum_refs {missing} not found — pass "
                    f"datum_table (datum_plane_assign extras['datums']) or "
                    f"assign via datum_target_assign (body._pd_datums)"
                )
            drf_origin, x_ax, y_ax, z_ax = _build_drf(geoms)

            def to_frame_xy(p):
                d = _sub(p, drf_origin)
                return (_dot(d, x_ax), _dot(d, y_ax))

            drf_info = {
                "labels": list(args.datum_refs),
                "origin": [round(c, 9) for c in drf_origin],
                "x_axis": [round(c, 9) for c in x_ax],
                "y_axis": [round(c, 9) for c in y_ax],
                "z_axis": [round(c, 9) for c in z_ax],
            }
        else:
            def to_frame_xy(p):
                return (float(p[0]), float(p[1]))

        def _dev(p_xy) -> float:
            return math.hypot(p_xy[0] - tx, p_xy[1] - ty)

        # If the axis is not vertical (Z-aligned), we still report XY offset at
        # the face's location point — this is the standard "actual position" of
        # the axis at its defining anchor, which OCCT gives us.
        anchor_xy = to_frame_xy(origin)
        worst = _dev(anchor_xy)

        dev_ends: list[float] | None = None
        dev_mid: float | None = None
        if args.evaluate_axis_tilt:
            v_range = _axis_v_range(face)
            if v_range is not None:
                v0, v1 = v_range
                p0 = _add(origin, _scale(axis, v0))
                p1 = _add(origin, _scale(axis, v1))
                pm = _add(origin, _scale(axis, 0.5 * (v0 + v1)))
                d0 = _dev(to_frame_xy(p0))
                d1 = _dev(to_frame_xy(p1))
                dev_mid = _dev(to_frame_xy(pm))
                dev_ends = [d0, d1]
                worst = max(d0, d1)

        diametral = 2.0 * worst

        # ── MMC/LMC bonus tolerance (mmc_lmc_modifier's documented budget) ──
        bonus = 0.0
        if args.material_condition is not None:
            if args.nominal_diameter_mm is None:
                raise ValueError(
                    "gdt_position: material_condition requires "
                    "nominal_diameter_mm (the MMC/LMC size limit)"
                )
            measured = args.measured_diameter_mm
            if measured is None:
                from phone_designer.skills.inspect.mmc_lmc_modifier import (
                    _feature_size,
                )
                _kind, measured, _internal = _feature_size(face)
            bonus = abs(float(measured) - float(args.nominal_diameter_mm))
        adjusted_tol = float(args.tolerance_mm) + bonus
        passed = bool(diametral <= adjusted_tol)

        extras = {
            "position": {
                "deviation_mm": round(worst, 6),
                "diametral_zone_mm": round(diametral, 6),
                "pass": passed,
                "verdict": "pass" if passed else "fail",
                "tolerance_mm": float(args.tolerance_mm),
                "bonus_tolerance_mm": round(bonus, 6),
                "adjusted_tolerance_mm": round(adjusted_tol, 6),
                "material_condition": args.material_condition,
                "actual_xy": [round(anchor_xy[0], 6), round(anchor_xy[1], 6)],
                "target_xy": [tx, ty],
                "axis_direction": [round(c, 6) for c in axis],
                "hole_radius_mm": round(radius, 6),
                "drf_used": drf_info,
                "deviation_at_ends_mm": (
                    [round(d, 6) for d in dev_ends] if dev_ends is not None else None
                ),
                "deviation_mid_mm": (
                    round(dev_mid, 6) if dev_mid is not None else None
                ),
            }
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
