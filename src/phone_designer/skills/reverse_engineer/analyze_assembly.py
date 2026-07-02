"""analyze_assembly — macro, read-only. One-call multi-body STEP analysis.

Track 2-3 (2026-07-02): OEM STEP files arrive as ASSEMBLIES, but the
single-part front door (``analyze_part``) treats them as one monolithic body.
This macro is the assembly analogue: one call takes a multi-body STEP and
returns a versioned ``AssemblyReportV1`` rollup instead of forcing the caller
to wire split → dedup → per-part analysis → interference by hand.

Pipeline (every stage reuses a corpus-proven skill/helper):

    assembly_path
      ├─ import_step                          (front-door; bad path → honest
      │                                        fm.step_read_failed refusal stub)
      ├─ split_into_components(split_solids)  (SOLID-aware: one body per
      │                                        physical part; falls back to the
      │                                        shell split for mesh inputs)
      ├─ dedup by _component_signature.geometric_signature
      │      50 identical bolts ⇒ ONE signature class analyzed ONCE, result
      │      fanned out to every instance (same mechanism the
      │      assembly_reverse_engineer pipeline ships). Opt-in _re_cache reuse
      │      persists the light analysis across runs (cache=True).
      ├─ per-CLASS LIGHT analysis: ExtractFeatureCatalog counts + mass
      │      (the same sub-skills analyze_part's light stages call; the HEAVY
      │      per-part deliverable — quality report / reconstruction — is the
      │      opt-in ``deep_analysis=True`` flag, which reuses analyze_part).
      ├─ standard-part recognition (match_standard_hole / _bearing / _oring on
      │      an axisymmetric-probe measurement). MANDATORY GATE: a recognized
      │      standard part gets a CATALOG-PART line and NEVER a machined-cost
      │      estimate, even when estimate_cost=True.
      └─ pairwise interference + clearance matrix (interference_check +
             check_clearance_full via pair_filter), pair-budgeted: pairs are
             AABB-gap-sorted (closest first) and pairs beyond ``max_pairs``
             are reported as honestly SKIPPED, never silently dropped.

extras["assembly_analysis"] = AssemblyReportV1 (strict-JSON-safe — asserted
with json.dumps(..., allow_nan=False)):

    {
      "schema_version": 1,
      "kind": "AssemblyReportV1",
      "grade": "light",                     # honest: catalog counts + mass;
                                            # deep per-part report is opt-in
      "refusal": None | "fm.step_read_failed" | "fm.empty_assembly",
      "components": [                       # one entry per signature CLASS
        {"class_id", "count", "names", "volume_mm3", "total_volume_mm3",
         "bbox_mm", "closed", "face_count", "analysis_profile", "analysis",
         "standard_part", "cost_estimate", "cost_note", "deep_analysis",
         "skipped_for_budget", "cache_hit", "duration_s", "over_budget",
         "errors"},
        ...
      ],
      "interference": {"label": "static_pose_only", ...} | None,
      "totals": {...},
      "budget": {...},
      "_stages": {stage: {ok, error, duration_s}},
    }

Honest limits (stated here AND inside the artifact):
  * Mates are tags only in this system — the interference/clearance matrix is
    valid ONLY for the as-imported static pose (``label: static_pose_only``).
  * The per-component budget is COOPERATIVE: it is checked between classes
    (cumulative ``per_component_timeout_s x k``), so a single runaway OCCT
    call inside one class cannot be preempted — the overshoot is recorded
    honestly via ``duration_s`` + ``over_budget`` instead. 0.0 ⇒ skip every
    class (deterministic degenerate, used by tests); None ⇒ unlimited.
  * Standard-part recognition is a CANDIDATE match by axisymmetric dimensions
    only (Ø / width) — thread pitch and head geometry are NOT verified; the
    entry carries grade='estimate' + the basis string.
  * analyze_part has no built-in light profile, so the light path calls the
    same sub-skills analyze_part composes (ExtractFeatureCatalog +
    mass_properties) directly; ``deep_analysis=True`` reuses analyze_part
    itself per representative.

Body unchanged (read-only macro); safe to call with body=None + a path.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult

SCHEMA_VERSION = 1
REPORT_KIND = "AssemblyReportV1"

POSE_NOTE = (
    "static_pose_only — mates are tags, not solved constraints, in this "
    "system; interference/clearance results are valid ONLY for the "
    "as-imported pose."
)
STANDARD_PART_COST_NOTE = (
    "standard/catalog part — machined-cost estimate SUPPRESSED (mandatory "
    "gate); source this component as a catalog line item."
)
BUDGET_POLICY_NOTE = (
    "cooperative: checked between classes (cumulative "
    "per_component_timeout_s x k); a runaway OCCT call inside one class "
    "cannot be preempted — overshoot is recorded via duration_s/over_budget."
)


# ──────────────────────────────────────────────────────────────────────────────
# small local helpers (same conventions as analyze_part / quote_package)


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _json_safe(obj: Any) -> Any:
    """Strict-JSON sanitiser (inf/nan -> None; tuples -> lists; unknown ->
    str). Local copy per quote_package convention — skills must not depend on
    mcp_support."""
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        return float(obj) if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    item = getattr(obj, "item", None)  # numpy scalar
    if callable(item):
        try:
            return _json_safe(item())
        except Exception:
            pass
    return str(obj)


def _safe(stage_name: str, stages: dict, fn):
    """Run ``fn`` — on exception record (ok=False, error) and return None so
    the macro keeps assembling. Mirrors analyze_part._safe (raw error text is
    carried, never masked)."""
    t0 = time.perf_counter()
    try:
        out = fn()
        stages[stage_name] = {
            "ok": True, "error": None,
            "duration_s": round(time.perf_counter() - t0, 4),
        }
        return out
    except Exception as exc:  # noqa: BLE001 — deliberate stage isolation
        stages[stage_name] = {
            "ok": False, "error": f"{type(exc).__name__}: {exc}"[:300],
            "duration_s": round(time.perf_counter() - t0, 4),
        }
        return None


def _bbox_mm(shape) -> list[float] | None:
    """Plain (mesh-based) Add_s bbox — deterministic (never AddOptimal_s,
    which mutates the optimal-bbox cache; see extract_feature_catalog PACK B
    drift note)."""
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        bb = Bnd_Box()
        BRepBndLib.Add_s(shape, bb)
        if bb.IsVoid():
            return None
        return [round(float(c), 4) for c in bb.Get()]
    except Exception:
        return None


def _aabb_gap_mm(bb_a: list[float] | None, bb_b: list[float] | None) -> float:
    """Euclidean gap between two AABBs (0.0 when overlapping or unknown —
    unknown sorts first so it is CHECKED rather than silently skipped)."""
    if not bb_a or not bb_b or len(bb_a) < 6 or len(bb_b) < 6:
        return 0.0
    gap2 = 0.0
    for k in range(3):
        g = max(0.0, max(bb_a[k] - bb_b[k + 3], bb_b[k] - bb_a[k + 3]))
        gap2 += g * g
    return math.sqrt(gap2)


def _face_count(shape) -> int | None:
    try:
        from phone_designer.skills._resolvers import _all_faces
        return len(_all_faces(shape))
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# axisymmetric round-part probe (feeds the standard-part matchers)

_RADIUS_BAND_MM = 0.01     # radius grouping band (same order as signature band)
_AXIS_PARALLEL_TOL = 1e-4  # 1 - |dot| tolerance for parallel axes
_COAXIAL_TOL_MM = 0.05     # max radial offset between axis lines


def _axial_extent_mm(shape, origin, axis) -> float | None:
    """max - min of all vertex projections onto the axis (rigid-motion-true
    length measurement). None when the shape has no vertices."""
    try:
        from OCP.BRep import BRep_Tool
        from OCP.TopAbs import TopAbs_VERTEX
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS
        lo = hi = None
        ex = TopExp_Explorer(shape, TopAbs_VERTEX)
        while ex.More():
            p = BRep_Tool.Pnt_s(TopoDS.Vertex_s(ex.Current()))
            t = ((p.X() - origin[0]) * axis[0]
                 + (p.Y() - origin[1]) * axis[1]
                 + (p.Z() - origin[2]) * axis[2])
            lo = t if lo is None else min(lo, t)
            hi = t if hi is None else max(hi, t)
            ex.Next()
        if lo is None or hi is None:
            return None
        return float(hi - lo)
    except Exception:
        return None


def _axes_coaxial(loc_a, dir_a, loc_b, dir_b) -> bool:
    dot = abs(dir_a[0] * dir_b[0] + dir_a[1] * dir_b[1] + dir_a[2] * dir_b[2])
    if dot < 1.0 - _AXIS_PARALLEL_TOL:
        return False
    # radial distance of loc_b from the line (loc_a, dir_a): |(b-a) x dir_a|
    vx = loc_b[0] - loc_a[0]
    vy = loc_b[1] - loc_a[1]
    vz = loc_b[2] - loc_a[2]
    cx = vy * dir_a[2] - vz * dir_a[1]
    cy = vz * dir_a[0] - vx * dir_a[2]
    cz = vx * dir_a[1] - vy * dir_a[0]
    return math.sqrt(cx * cx + cy * cy + cz * cz) <= _COAXIAL_TOL_MM


def _round_part_probe(shape) -> dict | None:
    """Classify a component as a simple axisymmetric ('round') part.

    Returns one of (or None when the part is not simply axisymmetric —
    e.g. a hex head, a plate with holes, any freeform face):
        {"form": "solid_round", "diameter_mm", "length_mm"}
        {"form": "ring", "outer_d_mm", "bore_d_mm", "width_mm"}
        {"form": "oring", "cs_mm", "id_mm"}
    """
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import (
            GeomAbs_Cone,
            GeomAbs_Cylinder,
            GeomAbs_Plane,
            GeomAbs_Torus,
        )
        from phone_designer.skills._resolvers import _all_faces
    except Exception:
        return None

    planes: list = []       # (loc, normal)
    cyls: list = []         # (loc, dir, radius)
    cones: list = []        # (loc, dir)
    tori: list = []         # (major_r, minor_r)
    try:
        faces = _all_faces(shape)
    except Exception:
        return None
    for f in faces:
        try:
            surf = BRepAdaptor_Surface(f)
            t = surf.GetType()
            if t == GeomAbs_Plane:
                pl = surf.Plane()
                loc, n = pl.Location(), pl.Axis().Direction()
                planes.append(((loc.X(), loc.Y(), loc.Z()),
                               (n.X(), n.Y(), n.Z())))
            elif t == GeomAbs_Cylinder:
                cyl = surf.Cylinder()
                loc, d = cyl.Location(), cyl.Axis().Direction()
                cyls.append(((loc.X(), loc.Y(), loc.Z()),
                             (d.X(), d.Y(), d.Z()), float(cyl.Radius())))
            elif t == GeomAbs_Cone:
                cone = surf.Cone()
                loc, d = cone.Location(), cone.Axis().Direction()
                cones.append(((loc.X(), loc.Y(), loc.Z()),
                              (d.X(), d.Y(), d.Z())))
            elif t == GeomAbs_Torus:
                tor = surf.Torus()
                tori.append((float(tor.MajorRadius()),
                             float(tor.MinorRadius())))
            else:
                return None  # freeform / sphere / bspline → not simple-round
        except Exception:
            return None

    # o-ring: torus face(s) only, all with the same (banded) radii.
    if tori and not cyls and not cones and not planes:
        maj = {round(t[0] / _RADIUS_BAND_MM) for t in tori}
        mino = {round(t[1] / _RADIUS_BAND_MM) for t in tori}
        if len(maj) != 1 or len(mino) != 1:
            return None
        major_r, minor_r = tori[0]
        return {
            "form": "oring",
            "cs_mm": round(2.0 * minor_r, 4),
            "id_mm": round(2.0 * (major_r - minor_r), 4),
        }

    if not cyls or tori:
        return None  # no cylinder (a plain box) or mixed torus+cyl → not simple

    # all cylinder + cone axes must be mutually coaxial.
    loc0, dir0, _ = cyls[0]
    for loc, d, _r in cyls[1:]:
        if not _axes_coaxial(loc0, dir0, loc, d):
            return None
    for loc, d in cones:
        if not _axes_coaxial(loc0, dir0, loc, d):
            return None
    # every planar face must be a cap (normal parallel to the axis) — a hex
    # head / keyed shaft / plate fails here (honest non-recognition).
    for _loc, n in planes:
        dot = abs(n[0] * dir0[0] + n[1] * dir0[1] + n[2] * dir0[2])
        if dot < 1.0 - _AXIS_PARALLEL_TOL:
            return None

    # radius groups (banded like the component signature).
    groups = sorted({round(r / _RADIUS_BAND_MM) * _RADIUS_BAND_MM
                     for _l, _d, r in cyls})
    length = _axial_extent_mm(shape, loc0, dir0)

    if len(groups) == 1:
        return {
            "form": "solid_round",
            "diameter_mm": round(2.0 * groups[0], 4),
            "length_mm": round(length, 4) if length is not None else None,
        }
    if len(groups) == 2:
        return {
            "form": "ring",
            "outer_d_mm": round(2.0 * groups[1], 4),
            "bore_d_mm": round(2.0 * groups[0], 4),
            "width_mm": round(length, 4) if length is not None else None,
        }
    return None  # 3+ radius steps → a turned part, not a simple catalog shape


def _recognize_standard_part(rep_body: Any, probe: dict | None,
                             min_confidence: float) -> dict | None:
    """Map a round-part probe onto the standard catalogs (reused matcher
    skills). Returns a CANDIDATE entry (grade='estimate', basis stated) or
    None. Every match is gated on the matcher's own confidence so a random
    Ø50 disc never gets crowned 'M50'."""
    if not probe:
        return None
    form = probe.get("form")
    top: dict | None = None
    kind = None
    basis = None
    designation = None

    if form == "solid_round" and probe.get("diameter_mm"):
        from phone_designer.skills.inspect.match_standard_hole import (
            MatchStandardHole,
        )
        matches = MatchStandardHole().apply(rep_body, {
            "hole_diameter_mm": float(probe["diameter_mm"]),
            "fit_kind": "auto",
        }).extras.get("matches") or []
        # a SOLID shank must match the thread OUTER Ø, not a drill size.
        outer = [m for m in matches if m.get("fit") == "outer"]
        if outer:
            top = outer[0]
            kind = "fastener_or_pin"
            designation = top.get("thread_spec")
            basis = ("outer Ø only — thread pitch / head geometry NOT "
                     "verified")
    elif form == "ring" and probe.get("outer_d_mm") and probe.get("bore_d_mm"):
        from phone_designer.skills.inspect.match_standard_bearing import (
            MatchStandardBearing,
        )
        matches = MatchStandardBearing().apply(rep_body, {
            "outer_d_mm": float(probe["outer_d_mm"]),
            "bore_d_mm": float(probe["bore_d_mm"]),
            **({"width_mm": float(probe["width_mm"])}
               if probe.get("width_mm") else {}),
        }).extras.get("matches") or []
        if matches:
            top = matches[0]
            kind = "bearing_or_bushing"
            designation = top.get("bearing_spec")
            basis = ("OD/bore/width only — raceway geometry NOT verified")
    elif form == "oring" and probe.get("cs_mm") and probe.get("id_mm"):
        from phone_designer.skills.inspect.match_standard_oring import (
            MatchStandardORing,
        )
        matches = MatchStandardORing().apply(rep_body, {
            "cs_mm": float(probe["cs_mm"]),
            "id_mm": float(probe["id_mm"]),
        }).extras.get("matches") or []
        if matches:
            top = matches[0]
            kind = "oring"
            designation = top.get("dash")
            basis = "cross-section + inner Ø only — material NOT verified"

    if top is None:
        return None
    conf = top.get("confidence")
    if not isinstance(conf, (int, float)) or conf < min_confidence:
        return None
    return {
        "kind": kind,
        "designation": designation,
        "confidence": round(float(conf), 4),
        "match": top,
        "measured": dict(probe),
        "basis": basis,
        "grade": "estimate",  # a candidate recognition, not a certification
        "cost_policy": STANDARD_PART_COST_NOTE,
    }


# ──────────────────────────────────────────────────────────────────────────────


@skill(
    name="analyze_assembly",
    category="reverse_engineer",
    level="macro",
    summary="Assembly front door: import a multi-body STEP and return a "
            "versioned AssemblyReportV1 in one call — SOLID-aware component "
            "split, signature dedup (50 identical bolts = 1 analysis), "
            "per-class light analysis (feature-catalog counts + mass), "
            "standard-part recognition (catalog line, NEVER a machined-cost "
            "estimate — mandatory gate), and a pair-budgeted static-pose "
            "interference/clearance matrix. Heavy per-part deliverables are "
            "opt-in (deep_analysis / estimate_cost). Read-only; every stage "
            "failure-isolated; strict-JSON-safe output.",
    selector_kinds=[],
    history_rules={},
    produces_features=["assembly_analysis"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.step_read_failed", "fm.empty_assembly"],
    cost_hint=2.0,
    post_conditions=[PostCondition(kind="body_present")],
    result_grade="measured",
)
class AnalyzeAssembly(SkillBase):
    class Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

        assembly_path: str = Field(
            min_length=1,
            description="Path to the multi-body STEP/CAD file to analyze.",
        )
        min_volume_mm3: float = Field(
            default=0.1, ge=0.0,
            description="Components below this volume are dropped at split "
                        "time (mesh debris guard; 0 keeps everything).",
        )
        dedup_instances: bool = Field(
            default=True,
            description="Group identical components by geometric_signature "
                        "and analyze ONCE per class (50 identical bolts = 1 "
                        "analysis). False ⇒ every instance is its own class.",
        )
        per_component_timeout_s: float | None = Field(
            default=30.0, ge=0.0,
            description="COOPERATIVE per-class analysis budget: class k may "
                        "start only while elapsed < per_component_timeout_s x "
                        "(k+1); classes beyond it are recorded as honest "
                        "skipped_for_budget entries. Checked BETWEEN classes "
                        "— a runaway OCCT call inside one class cannot be "
                        "preempted (overshoot recorded via over_budget). "
                        "0.0 ⇒ skip all analysis; None ⇒ unlimited.",
        )
        density_g_per_cm3: float = Field(
            default=1.0, ge=0.0,
            description="Uniform density for the mass rollup (honest limit: "
                        "one density for ALL components; per-material mass "
                        "needs per-component material knowledge we don't have).",
        )
        recognize_standard_parts: bool = Field(
            default=True,
            description="Run the axisymmetric probe + match_standard_hole/"
                        "bearing/oring per class. Recognized parts get a "
                        "catalog line and NEVER a machined-cost estimate.",
        )
        standard_min_confidence: float = Field(
            default=0.85, ge=0.0, le=1.0,
            description="Minimum matcher confidence (1/(1+deviation_mm)) to "
                        "accept a standard-part candidate.",
        )
        interference: bool = Field(
            default=True,
            description="Compute the pairwise interference + clearance "
                        "matrix (static pose only — mates are tags).",
        )
        min_clearance_mm: float = Field(
            default=0.5, ge=0.0,
            description="check_clearance_full violation threshold.",
        )
        max_pairs: int = Field(
            default=300, ge=1,
            description="Pair budget for the interference/clearance matrix. "
                        "Pairs are AABB-gap-sorted (closest first); pairs "
                        "beyond the budget are reported as skipped_pairs, "
                        "never silently dropped.",
        )
        estimate_cost: bool = Field(
            default=False,
            description="Opt-in machined-cost ESTIMATE per NON-standard "
                        "class (estimate_cost skill). Standard parts are "
                        "gated to catalog lines regardless of this flag.",
        )
        cost_process: str = Field(
            default="cnc_3axis",
            description="Process for estimate_cost when estimate_cost=True.",
        )
        cost_material: str = Field(
            default="aluminum",
            description="Material for estimate_cost when estimate_cost=True.",
        )
        cost_lot_size: int = Field(
            default=1000, ge=1,
            description="Lot size for estimate_cost amortisation.",
        )
        deep_analysis: bool = Field(
            default=False,
            description="Opt-in HEAVY per-class deliverable: run analyze_part "
                        "(quality report stages) on each representative via a "
                        "temp STEP round-trip. OFF by default — the light "
                        "profile (catalog counts + mass) is the rollup.",
        )
        cache: bool = Field(
            default=False,
            description="Opt-in _re_cache reuse: persist each class's light "
                        "analysis keyed by geometric_signature so re-runs "
                        "(or other assemblies sharing the component) skip "
                        "re-analysis. False ⇒ no disk I/O.",
        )
        cache_dir: str | None = Field(
            default=None,
            description="Cache root when cache=True (None ⇒ "
                        "PHONE_DESIGNER_RE_CACHE_DIR or per-user temp).",
        )

    # ── stub / refusal ────────────────────────────────────────────────────

    @staticmethod
    def _stub_report(path: str, stages: dict, refusal: str,
                     error: str | None) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": REPORT_KIND,
            "source_path": str(path),
            "grade": "light",
            "refusal": refusal,
            "error": error,
            "split": None,
            "dedup": None,
            "components": [],
            "interference": None,
            "interference_skip_reason": refusal,
            "totals": {
                "components_total": 0,
                "signature_classes": 0,
                "classes_analyzed": 0,
                "classes_skipped_for_budget": 0,
                "standard_part_classes": 0,
                "total_volume_mm3": 0.0,
                "total_mass_g": None,
                "contact_pairs": 0,
                "clearance_violation_pairs": 0,
            },
            "budget": None,
            "_stages": stages,
        }

    # ── per-class light analysis ──────────────────────────────────────────

    @staticmethod
    def _light_analysis(rep_body: Any, density: float,
                        errors: dict) -> dict | None:
        """Catalog COUNTS + mass — the same sub-skills analyze_part's cheap
        stages compose (analyze_part itself has no light profile, so this
        calls them directly; deep_analysis=True reuses analyze_part whole)."""
        out: dict[str, Any] = {}
        try:
            from phone_designer.skills.reverse_engineer.extract_feature_catalog import (  # noqa: E501
                ExtractFeatureCatalog,
            )
            catalog = ExtractFeatureCatalog().apply(rep_body, {}).extras.get(
                "feature_catalog") or {}
            out["feature_counts"] = {
                k: len(catalog.get(k) or [])
                for k in ("holes", "pockets", "bosses", "ribs", "lugs",
                          "symmetries", "patterns", "edge_blends",
                          "sweep_features", "loft_features",
                          "revolve_features")
                if isinstance(catalog.get(k), list)
            }
            out["base_thickness_mm"] = catalog.get("base_thickness_mm")
        except Exception as exc:  # noqa: BLE001 — stage isolation
            errors["feature_catalog"] = f"{type(exc).__name__}: {exc}"[:300]
        try:
            from phone_designer.skills.inspect.mass_properties import (
                MassProperties,
            )
            mp = MassProperties().apply(rep_body, {
                "density_g_per_cm3": float(density),
            }).extras
            out["mass"] = {
                "volume_mm3": mp.get("volume_mm3"),
                "mass_g": mp.get("mass_g"),
                "density_g_per_cm3": mp.get("density_g_per_cm3"),
                "centroid": mp.get("centroid"),
            }
        except Exception as exc:  # noqa: BLE001
            errors["mass_properties"] = f"{type(exc).__name__}: {exc}"[:300]
        return out or None

    @staticmethod
    def _deep_analysis(rep_body: Any, errors: dict) -> dict | None:
        """Opt-in heavy path: temp-STEP round-trip + analyze_part (light-ish
        flags: no HTML/PDF/reconstruction). Returns the part_analysis dict
        minus the bulky HTML fields."""
        import tempfile

        from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

        from phone_designer.skills.reverse_engineer.analyze_part import (
            AnalyzePart,
        )
        try:
            tmpdir = tempfile.mkdtemp(prefix="analyze_assembly_deep_")
            step_path = str(Path(tmpdir) / "rep.step")
            w = STEPControl_Writer()
            w.Transfer(_occt_shape(rep_body), STEPControl_AsIs)
            w.Write(step_path)
            pa = AnalyzePart().apply(None, {
                "part_path": step_path,
                "include_html": False,
            }).extras.get("part_analysis") or {}
            pa.pop("report_html", None)
            pa.pop("report_pdf", None)
            return pa
        except Exception as exc:  # noqa: BLE001
            errors["deep_analysis"] = f"{type(exc).__name__}: {exc}"[:300]
            return None

    # ── interference / clearance matrix ───────────────────────────────────

    @staticmethod
    def _interference_matrix(instances: list[dict], max_pairs: int,
                             min_clearance_mm: float, stages: dict) -> dict:
        from build123d import Part

        from phone_designer.skills.assembly._compound import build_compound
        from phone_designer.skills.assembly.check_clearance_full import (
            CheckClearanceFull,
        )
        from phone_designer.skills.assembly.interference_check import (
            InterferenceCheck,
        )

        asm = Part(build_compound(
            [_occt_shape(inst["body_ref"]) for inst in instances]))
        asm._pd_component_names = [inst["name"] for inst in instances]

        # deterministic pair budget: AABB-gap-sort (closest first), then
        # (i, j) as the tiebreak — unknown bboxes sort first (gap 0.0) so
        # they get CHECKED, not silently skipped.
        n = len(instances)
        all_pairs: list[tuple[float, int, int]] = []
        for i in range(n):
            for j in range(i + 1, n):
                gap = _aabb_gap_mm(instances[i].get("bbox_mm"),
                                   instances[j].get("bbox_mm"))
                all_pairs.append((round(gap, 6), i, j))
        all_pairs.sort()
        checked = all_pairs[:max_pairs]
        skipped = len(all_pairs) - len(checked)
        pair_filter = [(instances[i]["name"], instances[j]["name"])
                       for _g, i, j in checked]

        contacts: list[dict] = []
        violations: list[dict] = []
        inter = _safe("interference", stages, lambda: InterferenceCheck().apply(
            asm, {"pair_filter": pair_filter}))
        if inter is not None:
            for c in inter.extras.get("interferences") or []:
                contacts.append({
                    "a": c.get("a"), "b": c.get("b"),
                    "overlap_volume_mm3": c.get("overlap_volume"),
                })
        clear = _safe("clearance", stages, lambda: CheckClearanceFull().apply(
            asm, {"pair_filter": pair_filter,
                  "min_clearance_mm": float(min_clearance_mm)}))
        if clear is not None:
            violations = list(
                clear.extras.get("clearance_violations") or [])

        return {
            "label": "static_pose_only",
            "note": POSE_NOTE,
            "pair_budget": int(max_pairs),
            "pairs_total": len(all_pairs),
            "checked_pairs": len(checked),
            "skipped_pairs": skipped,
            "skipped_note": (
                "pairs beyond the budget were NOT checked (sorted by AABB "
                "gap, closest first) — absence of a contact for them is "
                "UNKNOWN, not verified." if skipped else None),
            "min_clearance_mm": float(min_clearance_mm),
            "contacts": contacts,
            "clearance_violations": violations,
        }

    # ── main ──────────────────────────────────────────────────────────────

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.create.import_step import ImportStep
        from phone_designer.skills.repair.split_into_components import (
            SplitIntoComponents,
        )
        from phone_designer.skills.reverse_engineer._component_signature import (
            geometric_signature,
        )
        from phone_designer.skills.reverse_engineer._re_cache import ReCache

        stages: dict[str, dict] = {}
        path = args.assembly_path

        # 1. import — front door: a bad path/parse degrades to an honest
        #    structured refusal stub, never a traceback.
        imp = _safe("import", stages, lambda: ImportStep().apply(
            None, {"path": path}))
        if imp is None or imp.body is None:
            report = self._stub_report(
                path, stages, refusal="fm.step_read_failed",
                error=stages.get("import", {}).get("error"))
            return SkillResult(body=body, history=EntityHistoryMap(),
                               extras={"assembly_analysis": _json_safe(report)})
        asm_body = imp.body

        # 2. SOLID-aware split (falls back to shells for mesh inputs).
        split = _safe("split", stages, lambda: SplitIntoComponents().apply(
            asm_body, {"min_volume_mm3": float(args.min_volume_mm3),
                       "split_solids": True}))
        split_extras = split.extras if split is not None else {}
        raw_comps = list(split_extras.get("components") or [])
        if not raw_comps:
            report = self._stub_report(
                path, stages, refusal="fm.empty_assembly",
                error=("no solid (or shell) components found — nothing to "
                       "analyze."))
            report["split"] = {
                "split_mode": split_extras.get("split_mode"),
                "component_count": 0,
                "skipped_small": split_extras.get("skipped_small", 0),
            }
            return SkillResult(body=body, history=EntityHistoryMap(),
                               extras={"assembly_analysis": _json_safe(report)})

        # instance records (deterministic naming by split order).
        instances: list[dict] = []
        for c in raw_comps:
            shape = _occt_shape(c["body_ref"])
            instances.append({
                "index": int(c["index"]),
                "name": f"comp_{int(c['index'])}",
                "body_ref": c["body_ref"],
                "volume_mm3": float(c.get("volume_mm3") or 0.0),
                "closed": bool(c.get("closed")),
                "bbox_mm": _bbox_mm(shape),
            })

        # 3. dedup by geometric signature.
        def _group() -> list[dict]:
            groups: dict[Any, dict] = {}
            for inst in instances:
                if args.dedup_instances:
                    try:
                        key: Any = geometric_signature(inst["body_ref"])
                    except Exception:
                        key = ("__nosig__", inst["index"])
                else:
                    key = ("__nodedup__", inst["index"])
                g = groups.get(key)
                if g is None:
                    groups[key] = g = {"signature": key, "members": []}
                g["members"].append(inst)
            # deterministic class ids: largest representative first.
            ordered = sorted(
                groups.values(),
                key=lambda g: (-g["members"][0]["volume_mm3"],
                               g["members"][0]["index"]))
            for cid, g in enumerate(ordered):
                g["class_id"] = cid
            return ordered

        classes = _safe("dedup", stages, _group) or []

        # 4. per-class LIGHT analysis under the cooperative budget.
        re_cache = ReCache(enabled=args.cache, cache_dir=args.cache_dir)
        budget = args.per_component_timeout_s
        t0 = time.perf_counter()
        component_rows: list[dict] = []
        for k, cls in enumerate(classes):
            members = cls["members"]
            rep = members[0]
            row: dict[str, Any] = {
                "class_id": cls["class_id"],
                "count": len(members),
                "names": [m["name"] for m in members],
                "volume_mm3": rep["volume_mm3"],
                "total_volume_mm3": round(
                    sum(m["volume_mm3"] for m in members), 6),
                "bbox_mm": rep["bbox_mm"],
                "closed": rep["closed"],
                "face_count": _face_count(_occt_shape(rep["body_ref"])),
                "analysis_profile": "light",
                "analysis": None,
                "standard_part": None,
                "cost_estimate": None,
                "cost_note": None,
                "deep_analysis": None,
                "skipped_for_budget": False,
                "cache_hit": False,
                "duration_s": None,
                "over_budget": False,
                "errors": {},
            }
            elapsed = time.perf_counter() - t0
            if budget is not None and elapsed >= budget * (k + 1):
                row["skipped_for_budget"] = True
                row["errors"]["budget"] = (
                    f"skipped_for_budget: elapsed {elapsed:.3f}s >= "
                    f"cumulative allowance {budget * (k + 1):.3f}s "
                    f"({budget}s/component x {k + 1} classes)")
                component_rows.append(row)
                continue

            t_cls = time.perf_counter()
            sig = cls["signature"] if args.dedup_instances else None
            cached = re_cache.get(sig) if sig is not None else None
            if cached is not None:
                row["analysis"] = {kk: vv for kk, vv in cached.items()
                                   if kk != "cache_hit"}
                row["cache_hit"] = True
            else:
                errors = row["errors"]
                row["analysis"] = self._light_analysis(
                    rep["body_ref"], args.density_g_per_cm3, errors)
                if sig is not None and row["analysis"] and not errors:
                    re_cache.put(sig, row["analysis"])

            if args.recognize_standard_parts:
                try:
                    probe = _round_part_probe(_occt_shape(rep["body_ref"]))
                    row["standard_part"] = _recognize_standard_part(
                        rep["body_ref"], probe,
                        float(args.standard_min_confidence))
                except Exception as exc:  # noqa: BLE001
                    row["errors"]["standard_part"] = (
                        f"{type(exc).__name__}: {exc}"[:300])

            # ── MANDATORY GATE: standard parts get catalog lines, NEVER a
            #    machined-cost estimate — regardless of estimate_cost=True.
            if row["standard_part"] is not None:
                row["cost_estimate"] = None
                row["cost_note"] = STANDARD_PART_COST_NOTE
            elif args.estimate_cost:
                try:
                    from phone_designer.skills.inspect.estimate_cost import (
                        EstimateCost,
                    )
                    row["cost_estimate"] = EstimateCost().apply(
                        rep["body_ref"], {
                            "process": args.cost_process,
                            "material": args.cost_material,
                            "lot_size": int(args.cost_lot_size),
                        }).extras.get("cost_estimate")
                    row["cost_note"] = (
                        "machined-cost ESTIMATE (grade='estimate') for ONE "
                        "instance; multiply by count for the class.")
                except Exception as exc:  # noqa: BLE001
                    row["errors"]["estimate_cost"] = (
                        f"{type(exc).__name__}: {exc}"[:300])

            if args.deep_analysis:
                row["deep_analysis"] = self._deep_analysis(
                    rep["body_ref"], row["errors"])

            row["duration_s"] = round(time.perf_counter() - t_cls, 4)
            if budget is not None and budget > 0 and row["duration_s"] > budget:
                row["over_budget"] = True  # honest overshoot record
            component_rows.append(row)
        stages["per_class_analysis"] = {
            "ok": True, "error": None,
            "duration_s": round(time.perf_counter() - t0, 4),
        }

        # 5. pair-budgeted interference/clearance matrix (static pose only).
        interference = None
        interference_skip_reason = None
        if not args.interference:
            interference_skip_reason = "disabled (interference=False)"
        elif len(instances) < 2:
            interference_skip_reason = "fewer than 2 components — no pairs"
        else:
            interference = _safe(
                "interference_matrix", stages,
                lambda: self._interference_matrix(
                    instances, int(args.max_pairs),
                    float(args.min_clearance_mm), stages))
            if interference is None:
                interference_skip_reason = stages.get(
                    "interference_matrix", {}).get("error")

        # 6. totals (honest partials: total_mass_g is None unless EVERY class
        #    carries a mass measurement).
        classes_analyzed = sum(
            1 for r in component_rows
            if not r["skipped_for_budget"] and r["analysis"] is not None)
        skipped_budget = sum(
            1 for r in component_rows if r["skipped_for_budget"])
        std_classes = sum(
            1 for r in component_rows if r["standard_part"] is not None)
        total_volume = round(sum(i["volume_mm3"] for i in instances), 6)
        masses = []
        for r in component_rows:
            m = ((r["analysis"] or {}).get("mass") or {}).get("mass_g")
            if isinstance(m, (int, float)) and math.isfinite(m):
                masses.append(float(m) * r["count"])
            else:
                masses = None
                break
        total_mass_g = round(sum(masses), 6) if masses else None

        report = {
            "schema_version": SCHEMA_VERSION,
            "kind": REPORT_KIND,
            "source_path": str(path),
            "grade": "light",  # honest: counts+mass rollup; deep is opt-in
            "grade_note": (
                "light profile = feature-catalog counts + mass per signature "
                "class (measured); standard-part lines are grade='estimate' "
                "candidates; deep_analysis/estimate_cost are opt-in."),
            "refusal": None,
            "error": None,
            "split": {
                "split_mode": split_extras.get("split_mode"),
                "component_count": len(instances),
                "skipped_small": split_extras.get("skipped_small", 0),
            },
            "dedup": {
                "enabled": bool(args.dedup_instances),
                "signature_classes": len(classes),
                "analysis_runs_saved": len(instances) - len(classes),
                "band_mm": 0.01,
                "cache": re_cache.stats(),
            },
            "components": component_rows,
            "interference": interference,
            "interference_skip_reason": interference_skip_reason,
            "totals": {
                "components_total": len(instances),
                "signature_classes": len(classes),
                "classes_analyzed": classes_analyzed,
                "classes_skipped_for_budget": skipped_budget,
                "standard_part_classes": std_classes,
                "total_volume_mm3": total_volume,
                "total_mass_g": total_mass_g,
                "total_mass_note": (
                    None if total_mass_g is None else
                    f"at uniform density "
                    f"{args.density_g_per_cm3} g/cm3 for ALL components"),
                "contact_pairs": len((interference or {}).get("contacts") or []),
                "clearance_violation_pairs": len(
                    (interference or {}).get("clearance_violations") or []),
            },
            "budget": {
                "per_component_timeout_s": budget,
                "policy": BUDGET_POLICY_NOTE,
                "analysis_elapsed_s": round(time.perf_counter() - t0, 4),
            },
            "_stages": stages,
        }

        report = _json_safe(report)
        # strict-JSON contract, asserted (raises loudly on regression).
        json.dumps(report, allow_nan=False)
        return SkillResult(body=body, history=EntityHistoryMap(),
                           extras={"assembly_analysis": report})
