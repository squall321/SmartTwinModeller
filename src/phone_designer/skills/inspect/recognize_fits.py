"""recognize_fits — inspect macro, read-only (2026-06-20).

Pillar REPORT → turn measured cylindrical features (hole bores, round bosses /
shafts) into MANUFACTURING INTENT: an ISO 286 tolerance band for each feature
and a recommended standard fit (H7/g6, H7/k6, H7/p6 …) for a stated assembly
role. Closes the "we measure a Ø but never say how tight it must be" gap — the
clearance / transition / interference decision a mechanism designer must make.

HONEST split (the anti-fake-precision rule applies here too):

  * The tolerance-band MAGNITUDES are ISO 286 *standard reference values* —
    the IT-grade table and the fundamental deviations for letters g, f, h, k,
    n, p, s are hard-coded from the standard, and the opposite bound is derived
    from the IT grade so each cell stores one tabulated number, not two. These
    are exact, not estimated.
  * The fit-class RECOMMENDATION (which grade, which letter) is a heuristic
    mapping from the stated `role` and CANNOT be read off the geometry — a
    single measured instance does not reveal design intent. So the skill's
    ``result_grade='estimate'`` and ``assumptions`` says so plainly.

Two ways in:
  * geometry — extract_feature_catalog → each hole bore (min diameter) and each
    cylindrical boss becomes a feature; or
  * ``diameter_mm`` — score one nominal directly (no CAD), e.g. "what fit for a
    Ø12 shaft in a clearance role".

extras["fit_analysis"] = {
    "role", "hole_grade", "shaft_grade", "standard": "ISO 286",
    "features": [{kind, nominal_mm, position, size_band_mm,
                  tolerance:{designation, upper_mm, lower_mm},
                  recommended_fit:{designation, fit_type, clearance_mm:{min,max},
                                   note}}, ...],
    "assumptions": [...], "grade": "estimate",
}
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult

# ── ISO 286 reference data (microns) ───────────────────────────────────────────
# Nominal-size bands: "over LOW up to and including HIGH" (mm).
_BANDS: list[tuple[float, float]] = [
    (0, 3), (3, 6), (6, 10), (10, 18), (18, 30),
    (30, 50), (50, 80), (80, 120), (120, 180),
]
#: Standard tolerance grade IT value (microns), aligned with _BANDS.
_IT: dict[int, list[int]] = {
    6:  [6, 8, 9, 11, 13, 16, 19, 22, 25],
    7:  [10, 12, 15, 18, 21, 25, 30, 35, 40],
    8:  [14, 18, 22, 27, 33, 39, 46, 54, 63],
    9:  [25, 30, 36, 43, 52, 62, 74, 87, 100],
    11: [60, 75, 90, 110, 130, 160, 190, 220, 250],
}
#: Fundamental deviation (microns) — tolerance-zone POSITION for common shaft
#: letters, per band. ``"es"`` letters store the upper deviation; ``"ei"``
#: letters store the lower deviation; the other bound is derived from the IT
#: grade so only one tabulated number lives in each cell. ``None`` = the
#: standard splits that band (e.g. s>50mm) → not represented, reported honestly.
_FUND: dict[str, tuple[str, list[int | None]]] = {
    "h": ("es", [0, 0, 0, 0, 0, 0, 0, 0, 0]),
    "g": ("es", [-2, -4, -5, -6, -7, -9, -10, -12, -14]),
    "f": ("es", [-6, -10, -13, -16, -20, -25, -30, -36, -43]),
    "k": ("ei", [0, 1, 1, 1, 2, 2, 2, 3, 3]),
    "n": ("ei", [4, 8, 10, 12, 15, 17, 20, 23, 27]),
    "p": ("ei", [6, 12, 15, 18, 22, 26, 32, 37, 43]),
    "s": ("ei", [14, 19, 23, 28, 35, 43, None, None, None]),
}
#: role → (hole designation, shaft designation, plain-language note). The
#: recommendation — a heuristic, not read from geometry.
_FIT_BY_ROLE: dict[str, tuple[str, str, str]] = {
    "clearance":    ("H7", "g6", "close running / precise clearance"),
    "running":      ("H8", "f7", "free running — looser, lubricated motion"),
    "location":     ("H7", "h6", "location clearance — minimal play, hand slide"),
    "slide":        ("H7", "h6", "location clearance — minimal play, hand slide"),
    "transition":   ("H7", "k6", "true transition — light keying, removable"),
    "press":        ("H7", "p6", "locational interference / light press"),
    "interference": ("H7", "p6", "locational interference / light press"),
    "drive":        ("H7", "s6", "medium drive / shrink — semi-permanent"),
    "shrink":       ("H7", "s6", "medium drive / shrink — semi-permanent"),
}


def _band_index(d_mm: float) -> int | None:
    for i, (lo, hi) in enumerate(_BANDS):
        if lo < d_mm <= hi:
            return i
    return None


def _it_um(grade: int, bi: int) -> int | None:
    row = _IT.get(grade)
    return row[bi] if row is not None else None


def _parse_designation(desig: str) -> tuple[str, int]:
    """'H7' -> ('H', 7); 'g6' -> ('g', 6)."""
    letter = "".join(c for c in desig if c.isalpha())
    grade = int("".join(c for c in desig if c.isdigit()) or "0")
    return letter, grade


def _hole_dev_um(grade: int, bi: int) -> tuple[float, float] | None:
    """H-hole band: EI=0, ES=+IT. Returns (es, ei) microns."""
    it = _it_um(grade, bi)
    return (float(it), 0.0) if it is not None else None


def _shaft_dev_um(letter: str, grade: int, bi: int) -> tuple[float, float] | None:
    """Lowercase shaft letter band → (es, ei) microns, or None if unavailable."""
    spec = _FUND.get(letter.lower())
    it = _it_um(grade, bi)
    if spec is None or it is None:
        return None
    kind, vals = spec
    v = vals[bi]
    if v is None:
        return None
    if kind == "es":
        return float(v), float(v) - float(it)
    return float(v) + float(it), float(v)  # 'ei' letter


def _fit_clearance_mm(
    nominal: float, hole_desig: str, shaft_desig: str,
) -> dict[str, Any] | None:
    """Signed hole-minus-shaft range (mm) for a fit pair at ``nominal``.

    +ve = clearance (gap), -ve = interference (press). Returns fit_type label.
    """
    bi = _band_index(nominal)
    if bi is None:
        return None
    h_letter, h_grade = _parse_designation(hole_desig)
    s_letter, s_grade = _parse_designation(shaft_desig)
    hole = _hole_dev_um(h_grade, bi) if h_letter.upper() == "H" else None
    shaft = _shaft_dev_um(s_letter, s_grade, bi)
    if hole is None or shaft is None:
        return None
    es_h, ei_h = hole
    es_s, ei_s = shaft
    max_gap = es_h - ei_s      # loosest: max hole − min shaft
    min_gap = ei_h - es_s      # tightest: min hole − max shaft
    if min_gap >= 0:
        fit_type = "clearance"
    elif max_gap <= 0:
        fit_type = "interference"
    else:
        fit_type = "transition"
    return {
        "fit_type": fit_type,
        "clearance_mm": {"min": round(min_gap / 1000.0, 4),
                         "max": round(max_gap / 1000.0, 4)},
    }


def _tolerance_band_mm(
    nominal: float, designation: str, is_hole: bool,
) -> dict[str, Any] | None:
    """The feature's own ISO band (mm) for ``designation`` (e.g. 'H7', 'g6')."""
    bi = _band_index(nominal)
    if bi is None:
        return None
    letter, grade = _parse_designation(designation)
    dev = (_hole_dev_um(grade, bi) if (is_hole and letter.upper() == "H")
           else _shaft_dev_um(letter, grade, bi))
    if dev is None:
        return None
    es, ei = dev
    return {
        "designation": f"Ø{round(nominal, 3)} {designation}",
        "upper_mm": round(es / 1000.0, 4),
        "lower_mm": round(ei / 1000.0, 4),
    }


#: shaft ladder (all H7 hole) spanning clearance → interference, for naming the
#: nearest standard fit to a MEASURED bore+shaft pair.
_FIT_LADDER: list[str] = ["g6", "f7", "h6", "k6", "n6", "p6", "s6"]


def _classify_measured_fit(
    hole_mm: float, shaft_mm: float,
) -> dict[str, Any] | None:
    """Recognise an ACTUAL fit: measured clearance + nearest standard ISO fit.

    The inverse of _recommend — given the as-measured bore and mating shaft, the
    fit_type follows from the sign of the real gap (not a heuristic), and the
    nearest standard designation is the ladder rung whose clearance midpoint is
    closest to the measured gap.
    """
    bi = _band_index(hole_mm)
    if bi is None:
        return None
    actual = round(hole_mm - shaft_mm, 4)  # +ve = gap, -ve = interference
    cands: list[tuple[float, str, dict[str, Any], float]] = []
    for s in _FIT_LADDER:
        fit = _fit_clearance_mm(hole_mm, "H7", s)
        if fit is None:
            continue
        cm = fit["clearance_mm"]
        mid = (cm["min"] + cm["max"]) / 2.0
        cands.append((abs(mid - actual), s, fit, round(mid, 4)))
    if not cands:
        return None
    cands.sort(key=lambda c: c[0])
    delta, s, fit, mid = cands[0]
    fit_type = "clearance" if actual > 0 else "interference" if actual < 0 else "transition"
    return {
        "hole_mm": round(hole_mm, 4),
        "shaft_mm": round(shaft_mm, 4),
        "actual_clearance_mm": actual,
        "fit_type": fit_type,
        "nearest_standard_fit": {
            "designation": f"H7/{s}",
            "fit_type": fit["fit_type"],
            "clearance_mm": fit["clearance_mm"],
            "midpoint_mm": mid,
            "delta_mm": round(delta, 4),
        },
    }


def _recommend(nominal: float, role: str) -> dict[str, Any] | None:
    pair = _FIT_BY_ROLE.get(role.strip().lower())
    if pair is None:
        return None
    hole_desig, shaft_desig, note = pair
    fit = _fit_clearance_mm(nominal, hole_desig, shaft_desig)
    if fit is None:
        return None
    return {
        "designation": f"{hole_desig}/{shaft_desig}",
        "note": note,
        **fit,
    }


@skill(
    name="recognize_fits",
    category="inspect",
    level="macro",
    summary="Recognise mating cylindrical features (hole bores, round bosses) and "
            "assign ISO 286 tolerance bands + a recommended standard fit "
            "(H7/g6, H7/k6, H7/p6 …) for a stated assembly role. Tolerance "
            "magnitudes are ISO-reference; the fit-class choice is a heuristic "
            "recommendation — result_grade='estimate'.",
    selector_kinds=[],
    history_rules={},
    produces_features=["fit_analysis"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.4,
    result_grade="estimate",
    post_conditions=[PostCondition(kind="body_present")],
)
class RecognizeFits(SkillBase):
    class Args(BaseModel):
        role: str = Field(
            default="clearance",
            description="Assembly role → preferred fit: clearance | running | "
                        "location | slide | transition | press | interference | "
                        "drive | shrink",
        )
        hole_grade: int = Field(
            default=7, ge=6, le=11,
            description="Default IT grade for hole bores (H-fit). 6,7,8,9,11.",
        )
        shaft_grade: int = Field(
            default=6, ge=6, le=11,
            description="Default IT grade for shafts / bosses (h-fit).",
        )
        diameter_mm: float | None = Field(
            default=None,
            description="Score this nominal Ø directly (no CAD); skips geometry.",
        )
        mating_diameter_mm: float | None = Field(
            default=None,
            description="Mating shaft Ø — with `diameter_mm` as the bore, RECOGNISE "
                        "the actual fit (measured clearance + nearest standard fit).",
        )
        max_features: int = Field(
            default=64, ge=1,
            description="Cap on reported features.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        role = (args.role or "clearance").strip().lower()
        assumptions: list[str] = [
            "Tolerance band magnitudes are ISO 286 standard reference values "
            "(IT grades + fundamental deviations for g,f,h,k,n,p,s).",
            "The fit-class RECOMMENDATION is a heuristic from the stated role — "
            "design intent is NOT recoverable from one measured instance; "
            "confirm against function.",
        ]
        if role not in _FIT_BY_ROLE:
            assumptions.append(f"unknown role '{role}' → 'clearance'")
            role = "clearance"

        features: list[dict[str, Any]] = []

        def _feature(kind: str, nominal: float, position, *,
                     fid=None, depth=None) -> dict[str, Any] | None:
            bi = _band_index(nominal)
            if bi is None:
                assumptions.append(
                    f"Ø{round(nominal, 3)} outside ISO table (0–180mm) — skipped")
                return None
            is_hole = kind == "hole"
            own = "H{}".format(args.hole_grade) if is_hole else "h{}".format(
                args.shaft_grade)
            rec = {
                "kind": kind,
                "nominal_mm": round(float(nominal), 4),
                "size_band_mm": list(_BANDS[bi]),
                "tolerance": _tolerance_band_mm(nominal, own, is_hole),
                "recommended_fit": _recommend(nominal, role),
            }
            if fid is not None:
                rec["id"] = fid
            if position is not None:
                rec["position"] = [round(float(v), 3) for v in position]
            if depth is not None:
                rec["depth_mm"] = round(float(depth), 4)
            return rec

        if args.diameter_mm is not None:
            f = _feature("hole", float(args.diameter_mm), None)
            if f is not None:
                if args.mating_diameter_mm is not None:
                    mf = _classify_measured_fit(
                        float(args.diameter_mm), float(args.mating_diameter_mm))
                    if mf is not None:
                        f["measured_fit"] = mf
                        assumptions.append(
                            "measured-fit mode — actual clearance from the supplied "
                            "bore+shaft pair; nearest standard fit named (fit_type is "
                            "the real gap sign, not a heuristic).")
                else:
                    assumptions.append("direct diameter mode — single nominal scored "
                                       "as a hole bore (no geometry).")
                features.append(f)
        else:
            from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
                ExtractFeatureCatalog,
            )
            try:
                cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
            except Exception as exc:  # noqa: BLE001
                cat = {}
                assumptions.append(
                    f"feature_catalog failed ({type(exc).__name__}); no features")
            for h in (cat.get("holes") or []):
                diams = h.get("diameters_mm") or []
                if not diams:
                    continue
                f = _feature("hole", float(min(diams)), h.get("axis_origin"),
                             fid=h.get("id"), depth=h.get("depth_mm"))
                if f is not None:
                    features.append(f)
                if len(features) >= args.max_features:
                    break
            for b in (cat.get("bosses") or []):
                if len(features) >= args.max_features:
                    break
                if b.get("type") != "cylindrical":
                    continue
                d = b.get("diameter_or_size_mm")
                if not d:
                    continue
                f = _feature("shaft", float(d), b.get("center"),
                             fid=b.get("id"), depth=b.get("height_mm"))
                if f is not None:
                    features.append(f)

        n_holes = sum(1 for f in features if f["kind"] == "hole")
        n_shafts = sum(1 for f in features if f["kind"] == "shaft")
        out = {
            "role": role,
            "hole_grade": args.hole_grade,
            "shaft_grade": args.shaft_grade,
            "standard": "ISO 286",
            "n_holes": n_holes,
            "n_shafts": n_shafts,
            "features": features,
            "assumptions": assumptions,
            "grade": "estimate",
        }
        return SkillResult(body=body, history=EntityHistoryMap(),
                           extras={"fit_analysis": out})
