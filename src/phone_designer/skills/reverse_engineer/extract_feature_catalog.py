"""extract_feature_catalog — atomic, read-only.

Run every detector / classifier in sequence on the current body and merge
their outputs into a single ``extras["feature_catalog"]`` dict::

    {
      "holes":             [...],   # classify_holes
      "pockets":           [...],   # classify_pockets
      "bosses":            [...],   # detect_bosses
      "ribs":              [...],   # detect_ribs
      "lugs":              [...],   # detect_lugs
      "symmetries":        [...],   # detect_mirror_symmetry
      "patterns":          [...],   # detect_*_array (linear + circular)
      "standard_matches":  [...],   # match_standard_hole on every hole
    }

Body is unchanged (post body_present). Catches individual detector failures
so a single broken detector cannot poison the whole catalog.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _safe(fn, *args, **kwargs):
    """Run a detector — swallow its exception and return ``[]``-like default.

    Each detector is a "best effort" inspector; if one fails we still want
    the rest of the catalog to be populated.
    """
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


@skill(
    name="extract_feature_catalog",
    category="reverse_engineer",
    level="atomic",
    summary="Aggregate every feature detector (classify_holes / "
            "classify_pockets / detect_bosses / detect_ribs / detect_lugs / "
            "detect_mirror_symmetry / detect_linear_array / "
            "detect_circular_array / match_standard_hole) into a single "
            "feature_catalog dict on result.extras. Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["feature_catalog"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.6,
    post_conditions=[PostCondition(kind="body_present")],
)
class ExtractFeatureCatalog(SkillBase):
    class Args(BaseModel):
        pass

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.inspect.classify_holes import ClassifyHoles
        from phone_designer.skills.inspect.classify_pockets import ClassifyPockets
        from phone_designer.skills.inspect.detect_bosses import DetectBosses
        from phone_designer.skills.inspect.detect_circular_array import (
            DetectCircularArray,
        )
        from phone_designer.skills.inspect.detect_linear_array import (
            DetectLinearArray,
        )
        from phone_designer.skills.inspect.detect_lugs import DetectLugs
        from phone_designer.skills.inspect.detect_mirror_symmetry import (
            DetectMirrorSymmetry,
        )
        from phone_designer.skills.inspect.detect_ribs import DetectRibs
        from phone_designer.skills.inspect.match_standard_hole import (
            MatchStandardHole,
        )

        # ── classify_holes (already does its own standard match per hole) ──
        holes_res = _safe(ClassifyHoles().apply, body, {"match_standards": True})
        holes = holes_res.extras.get("holes", []) if holes_res else []

        # ── classify_pockets ───────────────────────────────────────────────
        pockets_res = _safe(ClassifyPockets().apply, body, {})
        pockets = pockets_res.extras.get("pockets", []) if pockets_res else []

        # ── detect_bosses ──────────────────────────────────────────────────
        bosses_res = _safe(DetectBosses().apply, body, {})
        bosses = bosses_res.extras.get("bosses", []) if bosses_res else []

        # ── detect_ribs ────────────────────────────────────────────────────
        ribs_res = _safe(DetectRibs().apply, body, {})
        ribs = ribs_res.extras.get("ribs", []) if ribs_res else []

        # ── detect_lugs ────────────────────────────────────────────────────
        lugs_res = _safe(DetectLugs().apply, body, {})
        lugs = lugs_res.extras.get("lugs", []) if lugs_res else []

        # ── detect_mirror_symmetry ─────────────────────────────────────────
        sym_res = _safe(DetectMirrorSymmetry().apply, body, {})
        symmetries = sym_res.extras.get("mirror_planes", []) if sym_res else []

        # ── detect_*_array (linear + circular) ─────────────────────────────
        patterns: list[dict] = []
        for kind in ("hole", "pocket", "boss"):
            lin_res = _safe(
                DetectLinearArray().apply, body,
                {"feature_kind": kind, "min_count": 3},
            )
            if lin_res:
                for run in lin_res.extras.get("linear_arrays", []) or []:
                    p = dict(run)
                    p["pattern_kind"] = "linear"
                    p["feature_kind"] = kind
                    patterns.append(p)

            circ_res = _safe(
                DetectCircularArray().apply, body,
                {"feature_kind": kind, "min_count": 4},
            )
            if circ_res:
                for ring in circ_res.extras.get("circular_arrays", []) or []:
                    p = dict(ring)
                    p["pattern_kind"] = "circular"
                    p["feature_kind"] = kind
                    patterns.append(p)

        # ── match_standard_hole — one call per hole's primary diameter ─────
        standard_matches: list[dict] = []
        for h in holes:
            diams = h.get("diameters_mm") or []
            if not diams:
                continue
            primary_d = min(diams)  # shaft (clearance) diameter
            mres = _safe(
                MatchStandardHole().apply,
                body,
                {"hole_diameter_mm": float(primary_d), "fit_kind": "auto"},
            )
            top = None
            if mres:
                matches = mres.extras.get("matches", []) or []
                top = matches[0] if matches else None
            standard_matches.append({
                "hole_id": h.get("id"),
                "diameter_mm": float(primary_d),
                "best_match": top,
            })

        feature_catalog = {
            "holes": holes,
            "pockets": pockets,
            "bosses": bosses,
            "ribs": ribs,
            "lugs": lugs,
            "symmetries": symmetries,
            "patterns": patterns,
            "standard_matches": standard_matches,
        }

        # Share the catalog with plan_from_feature_catalog (which accepts
        # ``catalog=None`` meaning "use last extracted"). Import is local so
        # the two skill modules can be loaded in any order.
        try:
            from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
                PlanFromFeatureCatalog,
            )
            PlanFromFeatureCatalog._LAST_CATALOG = feature_catalog
        except Exception:
            pass

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"feature_catalog": feature_catalog},
        )
