"""joint_check — atomic, read-only. Fastener-path integrity across a joint.

THE BUG CLASS THIS CATCHES (gearbox v1, 2026-07): the cover AND the housing
flange were BOTH drilled clearance Ø5.5 for "M5" bolts — a bolt dropped through
the stack has NOTHING to thread into. Every per-part check passes (each hole is
a perfectly fine hole); only looking at the coaxial STACK across the joint
reveals that no member is a tap-drill bore.

Algorithm:
    1. Load the shape from ``path`` (STEP, possibly a multi-body compound) or
       use the current body. Split into components (TopAbs_SOLID iteration;
       a shape with no solids is treated as one component).
    2. Per component, collect cylindrical BORE faces only (the oriented face
       normal points TOWARD the axis — convex boss/fillet cylinders are
       excluded). Each face carries its exact axial band from the restricted
       V-parameter range of the cylinder adaptor.
    3. Merge same-component faces that share axis line + radius and overlap
       (seam-split halves, boolean fragments) into single hole records.
    4. Group holes ACROSS components by shared axis line (angle + line-distance
       tolerance), then chain them along the axis (gap ≤ ``max_axial_gap_mm``).
       A chain touching ≥ 2 distinct components is a fastener STACK.
    5. For each stack, pick the best-matching bolt nominal from
       ``bolt_nominals_mm``: members are classified per nominal as
           clearance  — d ≥ nominal, within the ISO-273 clearance band
           tap_drill  — d < nominal, within ±0.15 of the standard tap drill
                        (M3→2.5, M4→3.3, M5→4.2, M6→5.0, M8→6.8, M10→8.5)
           other      — matches neither band
       and the nominal maximizing (classified count, then lowest deviation
       from the ideal tap / medium-clearance diameter) wins.
    6. Verdict per stack:
           ok_threaded    — ≥1 clearance member + a TERMINAL tap-drill member
                            (the bolt passes the clearance holes and threads
                            into the far end)
           all_clearance  — every classified member is clearance: the v1 bug,
                            the bolt has nothing to grip
           all_tapped     — every classified member is tap-drill (no clearance
                            hole — both parts would need synchronized threads;
                            unusual, flagged)
           indeterminate  — no member matched any nominal, or the tap-drill
                            member is buried mid-stack

extras schema (strict-JSON-safe — no NaN/inf):
    {"ok": True,
     "stacks": [
        {"axis_point": [x,y,z], "axis_dir": [x,y,z],
         "nominal_guess": float | None, "designation": "M5" | None,
         "members": [
            {"comp": int, "d": float, "depth": float,
             "class": "clearance"|"tap_drill"|"other", "span": [s0, s1]},
            ...  # sorted along the axis
         ],
         "verdict": "ok_threaded"|"all_clearance"|"all_tapped"|"indeterminate"},
        ...
     ],
     "summary": {"stack_count": int, "ok_threaded": int, "all_clearance": int,
                 "all_tapped": int, "indeterminate": int,
                 "component_count": int, "bore_count": int}}

Failure modes:
    fm.no_holes — no coaxial cross-component hole stack exists (plain solids,
                  a single-body input, or bores that never line up).

body 는 변경하지 않는다 (post ``body_present``).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _load_step(path: str):
    """STEP 파일 → OCCT TopoDS_Shape (mm). Mirrors geometry_deviation."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"joint_check: STEP not found: {p}")
    reader = STEPControl_Reader()
    status = reader.ReadFile(str(p))
    if status != IFSelect_RetDone:
        raise RuntimeError(f"joint_check: STEP parse failed: {p} (status={status})")
    reader.TransferRoots()
    return reader.OneShape()


def _components(shape) -> list[Any]:
    """Split into per-component solids. A shape with no TopAbs_SOLID (open
    shell import) degrades to a single pseudo-component = the whole shape."""
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    solids: list[Any] = []
    it = TopExp_Explorer(shape, TopAbs_SOLID)
    while it.More():
        solids.append(it.Current())
        it.Next()
    return solids if solids else [shape]


# ── ISO metric fastener tables ────────────────────────────────────────────────
# Standard tap-drill diameters for coarse-pitch metric threads (d - pitch).
_TAP_DRILL_MM: dict[float, float] = {
    3.0: 2.5, 4.0: 3.3, 5.0: 4.2, 6.0: 5.0, 8.0: 6.8, 10.0: 8.5,
}
# ISO 273 clearance holes: (medium/normal, coarse/loose) per nominal.
_CLEARANCE_MM: dict[float, tuple[float, float]] = {
    3.0: (3.4, 3.6), 4.0: (4.5, 4.8), 5.0: (5.5, 5.8),
    6.0: (6.6, 7.0), 8.0: (9.0, 10.0), 10.0: (11.0, 12.0),
}
_TAP_BAND_MM = 0.15          # ± band around the standard tap drill
_CLEARANCE_SLACK_MM = 0.10   # tolerance above the loose clearance bound


def _tap_drill(nominal: float) -> float:
    """Standard tap drill; documented ≈0.85·d fallback for off-table nominals."""
    return _TAP_DRILL_MM.get(nominal, 0.85 * nominal)


def _clearance_band(nominal: float) -> tuple[float, float, float]:
    """(min, ideal_medium, max) clearance-hole diameter for a nominal."""
    med, loose = _CLEARANCE_MM.get(nominal, (1.1 * nominal, 1.2 * nominal))
    return nominal, med, loose + _CLEARANCE_SLACK_MM


def _classify(d: float, nominal: float) -> str:
    """clearance / tap_drill / other for one hole diameter vs one nominal."""
    tap = _tap_drill(nominal)
    if d < nominal and abs(d - tap) <= _TAP_BAND_MM:
        return "tap_drill"
    lo, _med, hi = _clearance_band(nominal)
    if lo <= d <= hi:
        return "clearance"
    return "other"


# ── cylindrical bore collection ───────────────────────────────────────────────


def _collect_bores(solid) -> list[dict[str, Any]]:
    """All cylindrical BORE faces of one component.

    Returns records {origin, axis (sign-normalized unit), radius, p0, p1}
    where p0/p1 are the 3D endpoints of the face's axial band ON the axis
    line (from the restricted V-parameter range — exact, no bbox slop).
    Convex (boss / outer-fillet) cylinders are excluded: the oriented face
    normal of a bore points TOWARD the axis.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.gp import gp_Pnt, gp_Vec
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    it = TopExp_Explorer(solid, TopAbs_FACE)
    while it.More():
        face = TopoDS.Face_s(it.Current())
        it.Next()
        try:
            key = hash(face)
            if key in seen:
                continue
            seen.add(key)
        except TypeError:
            pass
        try:
            surf = BRepAdaptor_Surface(face)  # restricted to face bounds
            if surf.GetType() != GeomAbs_Cylinder:
                continue
            cyl = surf.Cylinder()
            loc = cyl.Location()
            zd = cyl.Axis().Direction()
            o = (loc.X(), loc.Y(), loc.Z())
            z = (zd.X(), zd.Y(), zd.Z())
            r = float(cyl.Radius())
            u0, u1 = float(surf.FirstUParameter()), float(surf.LastUParameter())
            v0, v1 = float(surf.FirstVParameter()), float(surf.LastVParameter())
            if not all(math.isfinite(x) for x in (u0, u1, v0, v1)):
                continue
            # Oriented normal at mid-UV — BRepGProp_Face accounts for the face
            # orientation, so a bore's normal points toward the axis.
            gf = BRepGProp_Face(face)
            pnt, vec = gp_Pnt(), gp_Vec()
            gf.Normal(0.5 * (u0 + u1), 0.5 * (v0 + v1), pnt, vec)
            p = (pnt.X(), pnt.Y(), pnt.Z())
            t = sum((p[i] - o[i]) * z[i] for i in range(3))
            foot = tuple(o[i] + t * z[i] for i in range(3))
            radial = tuple(p[i] - foot[i] for i in range(3))
            n = (vec.X(), vec.Y(), vec.Z())
            if sum(n[i] * radial[i] for i in range(3)) >= 0.0:
                continue  # normal points away from axis → boss / convex fillet
            p0 = tuple(o[i] + v0 * z[i] for i in range(3))
            p1 = tuple(o[i] + v1 * z[i] for i in range(3))
        except Exception:
            continue
        out.append({
            "origin": o, "axis": _normalize_axis(z), "radius": r,
            "p0": p0, "p1": p1,
        })
    return out


def _normalize_axis(ax: tuple[float, float, float]) -> tuple[float, float, float]:
    """Unit axis with deterministic sign (largest |component| → +)."""
    mag = math.sqrt(ax[0] ** 2 + ax[1] ** 2 + ax[2] ** 2)
    if mag < 1e-12:
        return (0.0, 0.0, 1.0)
    n = (ax[0] / mag, ax[1] / mag, ax[2] / mag)
    i = max(range(3), key=lambda k: abs(n[k]))
    if n[i] < 0:
        n = (-n[0], -n[1], -n[2])
    return n


def _line_distance(
    o1: tuple[float, float, float], d1: tuple[float, float, float],
    o2: tuple[float, float, float],
) -> float:
    """Perpendicular distance of point o2 from the line (o1, d1), |d1|=1."""
    v = (o2[0] - o1[0], o2[1] - o1[1], o2[2] - o1[2])
    c = (
        v[1] * d1[2] - v[2] * d1[1],
        v[2] * d1[0] - v[0] * d1[2],
        v[0] * d1[1] - v[1] * d1[0],
    )
    return math.sqrt(c[0] ** 2 + c[1] ** 2 + c[2] ** 2)


def _same_line(h: dict, ref_axis, ref_origin, angle_tol_deg: float, dist_tol: float) -> bool:
    dot = abs(sum(h["axis"][i] * ref_axis[i] for i in range(3)))
    if dot < math.cos(math.radians(angle_tol_deg)):
        return False
    return _line_distance(ref_origin, ref_axis, h["origin"]) <= dist_tol


def _merge_same_component(holes: list[dict], gap_mm: float = 0.5) -> list[dict]:
    """Merge one component's same-axis same-radius faces (seam halves /
    boolean fragments) into single hole records with a combined axial span
    (s0, s1 measured along the shared normalized axis)."""
    merged: list[dict] = []
    for h in holes:
        s_a = sum(h["p0"][i] * h["axis"][i] for i in range(3))
        s_b = sum(h["p1"][i] * h["axis"][i] for i in range(3))
        s0, s1 = min(s_a, s_b), max(s_a, s_b)
        placed = False
        for m in merged:
            if abs(m["radius"] - h["radius"]) > 0.02:
                continue
            if not _same_line(h, m["axis"], m["origin"], 1.0, 0.1):
                continue
            if s0 > m["s1"] + gap_mm or s1 < m["s0"] - gap_mm:
                continue
            m["s0"], m["s1"] = min(m["s0"], s0), max(m["s1"], s1)
            placed = True
            break
        if not placed:
            merged.append({
                "origin": h["origin"], "axis": h["axis"],
                "radius": h["radius"], "s0": s0, "s1": s1,
            })
    return merged


def _best_nominal(diameters: list[float], nominals: list[float]) -> float | None:
    """The nominal classifying the MOST members; ties broken by the lowest
    total deviation from the ideal tap / medium-clearance diameter."""
    best: tuple[int, float, float] | None = None  # (count, -dev, nominal)
    for n in nominals:
        count = 0
        dev = 0.0
        for d in diameters:
            cls = _classify(d, n)
            if cls == "tap_drill":
                count += 1
                dev += abs(d - _tap_drill(n))
            elif cls == "clearance":
                count += 1
                dev += abs(d - _clearance_band(n)[1])
        if count == 0:
            continue
        key = (count, -dev, n)
        if best is None or key > best:
            best = key
    return best[2] if best is not None else None


def _verdict(classes: list[str]) -> str:
    """classes are ordered along the axis. See module docstring."""
    tagged = [c for c in classes if c != "other"]
    if not tagged:
        return "indeterminate"
    has_cl = "clearance" in tagged
    has_tap = "tap_drill" in tagged
    if has_cl and not has_tap:
        return "all_clearance"
    if has_tap and not has_cl:
        return "all_tapped"
    # Mixed: the tap-drill member must be TERMINAL (either end of the chain)
    # for the bolt to pass every clearance hole and thread into the far side.
    if tagged[0] == "tap_drill" or tagged[-1] == "tap_drill":
        return "ok_threaded"
    return "indeterminate"


@skill(
    name="joint_check",
    category="inspect",
    level="atomic",
    summary="Fastener-path integrity across bolted joints: find coaxial hole "
            "STACKS across components (cylindrical bores, shared axis, axially "
            "chained), classify each member as clearance vs tap-drill for the "
            "best-matching bolt nominal, and give a per-stack verdict — "
            "ok_threaded / all_clearance (BOTH sides drilled clearance: the "
            "bolt has nothing to grip) / all_tapped / indeterminate. Accepts a "
            "multi-body STEP via `path` or the current (compound) body. "
            "Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["joint_check_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.no_holes"],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class JointCheck(SkillBase):
    class Args(BaseModel):
        path: str | None = Field(
            default=None,
            description="STEP file (possibly a multi-body compound assembly). "
                        "None → use the current body (which may itself be a "
                        "compound of solids).",
        )
        bolt_nominals_mm: list[float] = Field(
            default=[3.0, 4.0, 5.0, 6.0, 8.0, 10.0],
            description="Candidate metric bolt nominals (M-sizes) to match "
                        "stacks against.",
        )
        axis_tolerance_deg: float = Field(default=1.0, ge=0.0, le=15.0)
        coaxial_tolerance_mm: float = Field(
            default=0.25, ge=0.0,
            description="Max perpendicular distance between two bores' axis "
                        "lines to count as the same fastener axis.")
        max_axial_gap_mm: float = Field(
            default=2.0, ge=0.0,
            description="Max axial gap between chained stack members "
                        "(gaskets / small standoffs).")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if args.path is not None:
            shape = _load_step(args.path)
        elif body is not None:
            shape = _occt_shape(body)
        else:
            raise ValueError("joint_check: provide `path` or a current body")

        comps = _components(shape)

        # Per-component bore holes (seam/fragment-merged).
        holes: list[dict] = []
        for ci, solid in enumerate(comps):
            for m in _merge_same_component(_collect_bores(solid)):
                m["comp"] = ci
                holes.append(m)

        # Group across components by shared axis line.
        groups: list[dict] = []  # {axis, origin, holes: [...]}
        for h in holes:
            placed = False
            for g in groups:
                if _same_line(h, g["axis"], g["origin"],
                              args.axis_tolerance_deg, args.coaxial_tolerance_mm):
                    g["holes"].append(h)
                    placed = True
                    break
            if not placed:
                groups.append({"axis": h["axis"], "origin": h["origin"], "holes": [h]})

        # Chain each group along the axis; keep chains spanning ≥ 2 components.
        stacks: list[dict] = []
        for g in groups:
            axis, origin = g["axis"], g["origin"]
            members = sorted(g["holes"], key=lambda m: (m["s0"], m["s1"]))
            chains: list[list[dict]] = []
            for m in members:
                if chains and m["s0"] <= chains[-1][-1]["_hi"] + args.max_axial_gap_mm:
                    m["_hi"] = max(chains[-1][-1]["_hi"], m["s1"])
                    chains[-1].append(m)
                else:
                    m["_hi"] = m["s1"]
                    chains.append([m])
            for chain in chains:
                if len({m["comp"] for m in chain}) < 2:
                    continue
                diameters = [2.0 * m["radius"] for m in chain]
                nominal = _best_nominal(diameters, list(args.bolt_nominals_mm))
                classes = (
                    [_classify(d, nominal) for d in diameters]
                    if nominal is not None else ["other"] * len(diameters)
                )
                # Deterministic axis point: foot of the axis line nearest the
                # global origin (independent of which face seeded the group).
                t = sum(origin[i] * axis[i] for i in range(3))
                foot = tuple(origin[i] - t * axis[i] for i in range(3))
                stacks.append({
                    "axis_point": [round(v, 4) for v in foot],
                    "axis_dir": [round(v, 6) for v in axis],
                    "nominal_guess": nominal,
                    "designation": (
                        f"M{nominal:g}" if nominal is not None else None),
                    "members": [
                        {
                            "comp": int(m["comp"]),
                            "d": round(2.0 * m["radius"], 4),
                            "depth": round(m["s1"] - m["s0"], 4),
                            "class": cls,
                            "span": [round(m["s0"], 4), round(m["s1"], 4)],
                        }
                        for m, cls in zip(chain, classes)
                    ],
                    "verdict": _verdict(classes),
                })

        if not stacks:
            raise ValueError(
                "fm.no_holes: no coaxial cross-component fastener hole stack "
                f"found (components={len(comps)}, bores={len(holes)}) — "
                "joint_check needs a multi-body compound whose bores line up "
                "across a joint interface"
            )

        # Deterministic order: by axis point then nominal.
        stacks.sort(key=lambda s: (s["axis_point"], s["axis_dir"]))
        counts = {"ok_threaded": 0, "all_clearance": 0, "all_tapped": 0,
                  "indeterminate": 0}
        for s in stacks:
            counts[s["verdict"]] += 1
        extras = {
            "ok": True,
            "stacks": stacks,
            "summary": {
                "stack_count": len(stacks),
                **counts,
                "component_count": len(comps),
                "bore_count": len(holes),
            },
        }
        return SkillResult(
            body=body if body is not None else shape,
            history=EntityHistoryMap(),
            extras=extras,
        )
