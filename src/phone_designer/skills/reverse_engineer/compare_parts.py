"""compare_parts — MACRO, read-only (Pillar COMPARE, phase-2, 2026-06-14).

The single skill a user invokes with TWO STEP files to get a structured
"what changed" report. It orchestrates the already-built COMPARE stack — every
dependency is an existing atomic skill / helper, reused, never reinvented:

    import_step  A + B
      -> extract_feature_catalog(A), extract_feature_catalog(B)
      -> register_bodies(B onto A)                 # best-fit rigid B->A
      -> feature_change_classify(catalog_a, catalog_b, transform_b=reg.T)
      -> geometry_deviation(body=B, reference=A, align='rigid',
                            transform_4x4=inverse(reg.T),  # maps A->B frame
                            region_centroids=A feature centroids)
      -> mass_properties(A), mass_properties(B)     # signed volume/CoG/mass/I
      -> read_step_pmi(A), read_step_pmi(B) -> _pmi_diff
      -> _part_similarity.similarity_and_classification

assembled into::

    extras["part_comparison"] = {
        "registration":       {...},   # transform_4x4, rmsd, method, confidence
        "feature_changes":    {...},   # by_kind moved/resized/added/removed
        "geometry_deviation": {...},   # hausdorff/rms/p95 + per_region heatmap
        "mass_delta":         {...},   # signed volume/area/CoG/mass/inertia
        "pmi_diff":           {...},   # added/removed/changed annotations
        "similarity_score":   float,
        "classification":     str,
        "scale_factor":       float | None,
        "_stages":            {stage: {"ok": bool, "error": str|None}},
        "_meta":              {part_a_path, part_b_path, linear_deflection_mm},
    }

Every stage is ``_safe``-wrapped (mirrors extract_feature_catalog._safe /
extract_quality_report._run_section): one failing stage degrades gracefully —
its slot carries None + an error string and the rest of the comparison still
assembles. compare_parts(A, A) -> all changes empty, similarity ~1.0,
hausdorff ~0; compare_parts(A, 1.5x-A) -> classification 'parametric_variant',
scale ~1.5.

Body: the macro loads its OWN bodies from the two paths, so it ignores the
input body and returns it unchanged (post body_present). Pass any body (e.g.
the loaded A) to satisfy the post-condition.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _safe(stage_name: str, stages: dict, fn, *args, **kwargs):
    """Run ``fn`` — on exception record (ok=False, error) in ``stages`` and
    return None so the macro keeps assembling. Mirrors
    extract_feature_catalog._safe + extract_quality_report._run_section."""
    t0 = time.perf_counter()
    try:
        out = fn(*args, **kwargs)
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


def _invert_rigid(transform_4x4: Any) -> list[list[float]] | None:
    """Invert a rigid 4x4 (R|t) -> (R.T | -R.T t). register_bodies emits the
    B->A matrix; geometry_deviation applies its transform to the REFERENCE, so
    to compare body=B against reference=A in B's frame we hand it the A->B
    (inverse) matrix."""
    try:
        M = np.array(transform_4x4, dtype=float)
        if M.shape != (4, 4):
            return None
        R = M[:3, :3]
        t = M[:3, 3]
        Rt = R.T
        ti = -Rt @ t
        inv = np.eye(4)
        inv[:3, :3] = Rt
        inv[:3, 3] = ti
        return [[float(v) for v in row] for row in inv]
    except Exception:
        return None


def _feature_centroids(catalog: dict | None) -> list[list[float]]:
    """Collect every feature centroid from a catalog (A's frame) for the
    geometry_deviation per-region heatmap. Reuses feature_fidelity_diff._xyz_of
    so the position key chain matches the rest of the RE stack."""
    from phone_designer.skills.reverse_engineer.feature_fidelity_diff import (
        _KINDS,
        _xyz_of,
    )

    out: list[list[float]] = []
    if not isinstance(catalog, dict):
        return out
    for kind in _KINDS:
        lst = catalog.get(kind)
        if not isinstance(lst, list):
            continue
        for e in lst:
            if not isinstance(e, dict):
                continue
            xyz = _xyz_of(e)
            if xyz is not None:
                out.append([float(xyz[0]), float(xyz[1]), float(xyz[2])])
    return out


def _mass_delta(mp_a: dict | None, mp_b: dict | None) -> dict[str, Any]:
    """Signed B - A deltas over the mass_properties payloads."""
    out: dict[str, Any] = {
        "volume_mm3_a": None, "volume_mm3_b": None, "volume_delta_mm3": None,
        "volume_delta_pct": None,
        "mass_g_a": None, "mass_g_b": None, "mass_delta_g": None,
        "centroid_a": None, "centroid_b": None, "centroid_shift_mm": None,
        "inertia_delta": None,
    }
    if not (isinstance(mp_a, dict) and isinstance(mp_b, dict)):
        return out

    def _f(d: dict, k: str) -> float | None:
        v = d.get(k)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    va = _f(mp_a, "volume_mm3")
    vb = _f(mp_b, "volume_mm3")
    out["volume_mm3_a"] = va
    out["volume_mm3_b"] = vb
    if va is not None and vb is not None:
        out["volume_delta_mm3"] = round(vb - va, 6)
        if abs(va) > 1e-12:
            out["volume_delta_pct"] = round((vb - va) / va * 100.0, 6)

    ma = _f(mp_a, "mass_g")
    mb = _f(mp_b, "mass_g")
    out["mass_g_a"] = ma
    out["mass_g_b"] = mb
    if ma is not None and mb is not None:
        out["mass_delta_g"] = round(mb - ma, 6)

    ca = mp_a.get("centroid")
    cb = mp_b.get("centroid")
    out["centroid_a"] = ca
    out["centroid_b"] = cb
    if (isinstance(ca, (list, tuple)) and isinstance(cb, (list, tuple))
            and len(ca) >= 3 and len(cb) >= 3):
        try:
            shift = float(np.linalg.norm(
                np.array(cb[:3], dtype=float) - np.array(ca[:3], dtype=float)))
            out["centroid_shift_mm"] = round(shift, 6)
        except Exception:
            pass

    ia = mp_a.get("inertia_diag")
    ib = mp_b.get("inertia_diag")
    if isinstance(ia, dict) and isinstance(ib, dict):
        delta: dict[str, float] = {}
        for k in ("Ixx", "Iyy", "Izz", "Ixy", "Ixz", "Iyz"):
            av = _f(ia, k)
            bv = _f(ib, k)
            if av is not None and bv is not None:
                delta[k] = round(bv - av, 6)
        out["inertia_delta"] = delta or None
    return out


@skill(
    name="compare_parts",
    category="reverse_engineer",
    level="macro",
    summary="MACRO: compare two STEP parts (A, B) end-to-end into a single "
            "'what changed' report — best-fit rigid registration, per-feature "
            "change classification (moved/resized/added/removed), geometric "
            "deviation (hausdorff/rms/p95 + per-region heatmap), signed mass / "
            "volume / CoG / inertia delta, PMI / GD&T annotation diff, and a "
            "composite similarity_score + classification (identical / "
            "parametric_variant / same_family_resized / design_change / "
            "unrelated, with an estimated scale_factor). Every stage is "
            "isolated so one failure degrades gracefully. Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["part_comparison"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.step_read_failed"],
    cost_hint=0.9,
    result_grade="estimate",
    post_conditions=[PostCondition(kind="body_present")],
)
class CompareParts(SkillBase):
    class Args(BaseModel):
        part_a_path: str = Field(
            min_length=1, description="Reference part A — STEP/STP path.")
        part_b_path: str = Field(
            min_length=1, description="Comparison part B — STEP/STP path.")
        density_g_per_cm3_a: float = Field(
            default=1.0, ge=0.0,
            description="Material density for A's mass (g/cm3). Default 1.0 "
                        "-> mass_g equals volume_cm3.")
        density_g_per_cm3_b: float = Field(
            default=1.0, ge=0.0,
            description="Material density for B's mass (g/cm3).")
        linear_deflection_mm: float = Field(
            default=0.2, gt=0.0,
            description="Tessellation chord tolerance for geometry_deviation.")
        hausdorff_tol_mm: float = Field(
            default=0.5, gt=0.0,
            description="Geometric tolerance below which a high-similarity "
                        "pair is labelled 'identical'.")
        value_bucket_mm: float = Field(
            default=0.5, gt=0.0,
            description="PMI value quantisation for pairing annotations.")

        model_config = {"arbitrary_types_allowed": True}

    def _apply(self, body: Any, args: Args) -> SkillResult:
        # Local imports — same convention as extract_feature_catalog /
        # extract_quality_report so skill modules load in any order.
        from phone_designer.skills.create.import_step import ImportStep
        from phone_designer.skills.inspect.geometry_deviation import (
            GeometryDeviation,
        )
        from phone_designer.skills.inspect.mass_properties import MassProperties
        from phone_designer.skills.inspect.register_bodies import RegisterBodies
        from phone_designer.skills.pmi.read_step_pmi import ReadStepPmi
        from phone_designer.skills.reverse_engineer._part_similarity import (
            similarity_and_classification,
        )
        from phone_designer.skills.reverse_engineer._pmi_diff import pmi_diff
        from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
            ExtractFeatureCatalog,
        )
        from phone_designer.skills.reverse_engineer.feature_change_classify import (
            FeatureChangeClassify,
        )

        path_a = Path(args.part_a_path)
        path_b = Path(args.part_b_path)
        if not path_a.exists():
            raise FileNotFoundError(f"compare_parts: part A not found -> {path_a}")
        if not path_b.exists():
            raise FileNotFoundError(f"compare_parts: part B not found -> {path_b}")

        stages: dict[str, dict] = {}

        # ── load both bodies ───────────────────────────────────────────────
        res_a = _safe("import_a", stages,
                      lambda: ImportStep().apply(None, {"path": str(path_a)}))
        res_b = _safe("import_b", stages,
                      lambda: ImportStep().apply(None, {"path": str(path_b)}))
        body_a = res_a.body if res_a is not None else None
        body_b = res_b.body if res_b is not None else None
        if body_a is None or body_b is None:
            # Cannot proceed without both bodies — return an honest stub.
            comparison = {
                "registration": None, "feature_changes": None,
                "geometry_deviation": None, "mass_delta": None,
                "pmi_diff": None, "similarity_score": None,
                "classification": "unknown", "scale_factor": None,
                "_stages": stages,
                "_meta": {
                    "part_a_path": str(path_a), "part_b_path": str(path_b),
                    "linear_deflection_mm": float(args.linear_deflection_mm),
                },
            }
            return SkillResult(
                body=(body_a if body_a is not None else body),
                history=EntityHistoryMap(),
                extras={"part_comparison": comparison},
            )

        # ── feature catalogs ───────────────────────────────────────────────
        cat_res_a = _safe("catalog_a", stages, lambda: ExtractFeatureCatalog()
                          .apply(body_a, {}))
        cat_res_b = _safe("catalog_b", stages, lambda: ExtractFeatureCatalog()
                          .apply(body_b, {}))
        catalog_a = (cat_res_a.extras.get("feature_catalog", {})
                     if cat_res_a is not None else {})
        catalog_b = (cat_res_b.extras.get("feature_catalog", {})
                     if cat_res_b is not None else {})

        # ── register B onto A (best-fit rigid) ─────────────────────────────
        reg_res = _safe("register", stages, lambda: RegisterBodies().apply(
            body_b, {
                "reference_shape": body_a,
                "catalog_a": catalog_a or None,
                "catalog_b": catalog_b or None,
            }))
        registration = (reg_res.extras.get("registration")
                        if reg_res is not None else None)
        reg_transform_b = (registration.get("transform_4x4")
                           if isinstance(registration, dict) else None)

        # ── geometric deviation (the anti-fake ground truth) ───────────────
        # geometry_deviation transforms the REFERENCE; we want body=B vs
        # reference=A measured in B's frame. Two candidate alignments compete
        # and the one with the LOWER hausdorff wins:
        #   (a) IDENTITY (align='none') — the two STEP files share a frame
        #       (the common case: same-frame exports, A-vs-A, scaled variants).
        #   (b) REGISTRATION (align='rigid', A->B = inverse of the B->A reg).
        # Picking the better avoids a degenerate symmetric-part registration
        # FLIPPING a part onto itself (linkrods A-vs-A: the near-symmetric
        # inertia frame yields a feature-fit that mislabels identity as a
        # design change). region_centroids come from A's features mapped into
        # B's frame so the per-region heatmap aligns with B's sample points.
        a_centroids = _feature_centroids(catalog_a)

        def _dev_for(align: str, transform_4x4, centroids):
            args_gd = {
                "reference_step_path": str(path_a),
                "align": align,
                "linear_deflection_mm": float(args.linear_deflection_mm),
                "region_centroids": centroids,
            }
            if align == "rigid":
                args_gd["transform_4x4"] = transform_4x4
            return GeometryDeviation().apply(body_b, args_gd).extras[
                "geometry_deviation"]

        # Candidate (a): identity — A and B already in a shared frame.
        gd_identity = _safe(
            "geometry_deviation_identity", stages,
            lambda: _dev_for("none", None, a_centroids or None))

        # Candidate (b): registration rigid transform (when one was found).
        gd_registered = None
        inv_T = _invert_rigid(reg_transform_b) if reg_transform_b is not None else None
        if inv_T is not None:
            reg_centroids = None
            if a_centroids:
                try:
                    M = np.array(inv_T, dtype=float)
                    R, t = M[:3, :3], M[:3, 3]
                    reg_centroids = [
                        [float(v) for v in (R @ np.array(c, dtype=float) + t)]
                        for c in a_centroids
                    ]
                except Exception:
                    reg_centroids = None
            gd_registered = _safe(
                "geometry_deviation_registered", stages,
                lambda: _dev_for("rigid", inv_T, reg_centroids))

        # Pick the alignment that best superimposes the parts (lower hausdorff).
        def _h(gd):
            return gd.get("hausdorff_mm", float("inf")) if isinstance(gd, dict) else float("inf")

        if gd_registered is not None and _h(gd_registered) < _h(gd_identity):
            geom_dev = gd_registered
            transform_b = reg_transform_b      # B->A used to map B's catalog
            alignment_used = "registration"
        elif gd_identity is not None:
            geom_dev = gd_identity
            transform_b = None                 # identity — B already in A frame
            alignment_used = "identity"
        else:
            # Both deviation attempts failed — last-ditch bbox-center align so
            # the anti-fake gate still reports SOMETHING honest.
            gd_res = _safe("geometry_deviation_bbox", stages,
                           lambda: GeometryDeviation().apply(body_b, {
                               "reference_step_path": str(path_a),
                               "align": "bbox_center",
                               "linear_deflection_mm": float(
                                   args.linear_deflection_mm),
                           }))
            geom_dev = (gd_res.extras.get("geometry_deviation")
                        if gd_res is not None else None)
            transform_b = reg_transform_b
            alignment_used = "bbox_center"
        if isinstance(geom_dev, dict):
            geom_dev["alignment_used"] = alignment_used

        # ── per-feature change classification (B mapped into A's frame) ────
        # Uses the SAME transform the deviation picked, so the change report
        # and the geometric ground truth agree on the frame.
        fc_res = _safe("feature_changes", stages,
                       lambda: FeatureChangeClassify().apply(body_a, {
                           "catalog_a": catalog_a or {},
                           "catalog_b": catalog_b or {},
                           "transform_b": transform_b,
                       }))
        feature_changes = (fc_res.extras.get("feature_changes")
                           if fc_res is not None else None)

        # ── mass / volume / CoG / inertia delta ────────────────────────────
        mp_res_a = _safe("mass_a", stages, lambda: MassProperties().apply(
            body_a, {"density_g_per_cm3": float(args.density_g_per_cm3_a)}))
        mp_res_b = _safe("mass_b", stages, lambda: MassProperties().apply(
            body_b, {"density_g_per_cm3": float(args.density_g_per_cm3_b)}))
        mp_a = mp_res_a.extras if mp_res_a is not None else None
        mp_b = mp_res_b.extras if mp_res_b is not None else None
        mass_delta = _safe("mass_delta", stages,
                           lambda: _mass_delta(mp_a, mp_b)) or {}

        # ── PMI / GD&T diff ────────────────────────────────────────────────
        pmi_res_a = _safe("pmi_a", stages,
                          lambda: ReadStepPmi().apply(body_a, {"path": str(path_a)}))
        pmi_res_b = _safe("pmi_b", stages,
                          lambda: ReadStepPmi().apply(body_b, {"path": str(path_b)}))
        pmi_a = (pmi_res_a.extras.get("pmi") if pmi_res_a is not None else None)
        pmi_b = (pmi_res_b.extras.get("pmi") if pmi_res_b is not None else None)
        pmi_report = _safe("pmi_diff", stages, lambda: pmi_diff(
            pmi_a, pmi_b, value_bucket_mm=float(args.value_bucket_mm)))

        # ── similarity + classification ────────────────────────────────────
        # _part_similarity reads B in A's frame, so feed it B's catalog mapped
        # through the registration transform (same op feature_change_classify
        # does internally). Reuse that module's _transform_catalog so the frame
        # math is shared, not re-implemented.
        cat_b_in_a = catalog_b
        if transform_b is not None and catalog_b:
            from phone_designer.skills.reverse_engineer.feature_change_classify import (
                _flatten_4x4,
                _transform_catalog,
            )
            rt = _safe("map_catalog_b", stages, lambda: _flatten_4x4(transform_b))
            if rt is not None:
                R, t = rt
                cat_b_in_a = _transform_catalog(catalog_b, R, t)

        sim = _safe("similarity", stages, lambda: similarity_and_classification(
            catalog_a or {}, cat_b_in_a or {},
            registration=registration,
            geometry_deviation=geom_dev,
            mass_delta=mass_delta,
            hausdorff_tol_mm=float(args.hausdorff_tol_mm),
        )) or {}

        comparison = {
            "registration": registration,
            "feature_changes": feature_changes,
            "geometry_deviation": geom_dev,
            "mass_delta": mass_delta,
            "pmi_diff": pmi_report,
            "similarity_score": sim.get("similarity_score"),
            "classification": sim.get("classification", "unknown"),
            "scale_factor": sim.get("scale_factor"),
            "scale_uniformity_cv": sim.get("scale_uniformity_cv"),
            "similarity_evidence": sim.get("evidence"),
            "result_grade": "estimate",
            "_stages": stages,
            "_meta": {
                "part_a_path": str(path_a),
                "part_b_path": str(path_b),
                "linear_deflection_mm": float(args.linear_deflection_mm),
            },
        }

        return SkillResult(
            body=body_a,
            history=EntityHistoryMap(),
            extras={"part_comparison": comparison},
        )
