"""analyze_part — macro, read-only. Single-part front door (2026-06-18).

The single-STEP analogue of ``compare_parts``: one call takes a CAD file and
returns the full single-part deliverable, instead of forcing the caller to wire
``import_step`` → ``extract_quality_report`` → ``emit_quality_report`` →
``extract_feature_catalog`` → ``identify_key_dimensions`` → (reconstruct) by
hand.

Each stage is isolated (``_safe``) so one failure (e.g. no PMI, a render
without GL, a too-big body) degrades the result gracefully rather than
aborting the whole analysis — the same convention as
``extract_feature_catalog._safe`` / ``compare_parts``.

``extras["part_analysis"]`` = {
    "report":         <QualityReportV1 dict>,   # topology / wall / draft /
                                                # blends / mass / mesh / DFM,
                                                # measured|estimate graded
    "report_html":    str | None,               # self-contained HTML
    "report_pdf":     {available, engine, ...}, # true .pdf when reportlab present
    "feature_catalog": {counts + the catalog},  # the RE feature inventory
    "key_dimensions": [...],                     # the editable driving dims
    "reconstruction": {match_ratio, hausdorff_mm, base_mechanism} | None,
    "bbox_mm":        [...],
    "_stages":        {stage: {ok, error, duration_s}},
}

Body unchanged (read-only macro). The reconstruction stage is OFF by default
(it runs the box-mode RE pipeline + a Hausdorff measurement, which is the
expensive part); turn it on with ``reconstruct=True``.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _safe(stage_name: str, stages: dict, fn):
    """Run ``fn`` — on exception record (ok=False, error) and return None so the
    macro keeps assembling. Mirrors compare_parts._safe."""
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


@skill(
    name="analyze_part",
    category="reverse_engineer",
    level="macro",
    summary="Single-part front door: import a CAD file and return the full "
            "deliverable in one call — a measured|estimate-graded quality "
            "report (topology / wall thickness / draft / edge blends / mass "
            "properties / mesh / DFM) with self-contained HTML (and a true "
            ".pdf when reportlab is installed), the reverse-engineering "
            "feature catalog, the editable key dimensions, and optionally a "
            "box-mode reconstruction scored by geometry_deviation Hausdorff. "
            "Read-only; each stage is failure-isolated.",
    selector_kinds=[],
    history_rules={},
    produces_features=["part_analysis"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.6,
    post_conditions=[PostCondition(kind="body_present")],
    result_grade="measured",
)
class AnalyzePart(SkillBase):
    class Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

        part_path: str = Field(
            description="Path to the CAD file to analyze (STEP/IGES/BREP — "
                        "anything import_step accepts).",
        )
        processes: list[str] = Field(
            default_factory=lambda: ["cnc_milling", "injection_molding"],
            description="DFM processes to evaluate for manufacturability.",
        )
        pull_direction: list[float] = Field(
            default_factory=lambda: [0.0, 0.0, 1.0],
            description="Mold/draft pull direction for the draft + DFM analysis.",
        )
        include_html: bool = Field(
            default=True,
            description="Emit the self-contained HTML report string.",
        )
        render_views: bool = Field(
            default=False,
            description="Embed headless section/iso PNG views in the HTML "
                        "(needs a GL context; falls back to a skip-note).",
        )
        pdf: bool = Field(
            default=False,
            description="Also emit a true .pdf (text + PNG) when reportlab is "
                        "installed; otherwise the print-optimized HTML is the "
                        "deliverable.",
        )
        reconstruct: bool = Field(
            default=False,
            description="Run the box-mode reverse-engineering reconstruction "
                        "and score it with geometry_deviation Hausdorff vs the "
                        "original. OFF by default (the expensive stage).",
        )
        base_profile_mode: Literal["off", "auto"] = Field(
            default="auto",
            description="Passed to the reconstruction planner when "
                        "reconstruct=True: 'auto' tries the freeform "
                        "silhouette / parametric / re-solidify bases (kept only "
                        "when their Hausdorff beats the box base).",
        )
        emit_script: bool = Field(
            default=False,
            description="Also recover an EDITABLE build123d parametric script "
                        "(named key dimensions + relation-driven feature history) "
                        "via emit_parametric_script. OFF by default.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.create.import_step import ImportStep
        from phone_designer.skills.inspect.emit_quality_report import (
            EmitQualityReport,
        )
        from phone_designer.skills.inspect._report_pdf import (
            build_pdf_deliverable,
        )
        from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
            ExtractFeatureCatalog,
        )
        from phone_designer.skills.reverse_engineer.identify_key_dimensions import (
            IdentifyKeyDimensions,
        )

        path = Path(args.part_path)
        stages: dict[str, dict] = {}
        part_id = path.stem

        # ── 1. import (front-door: a bad path degrades to an honest stub, not
        #    a traceback — the import stage records the failure) ─────────────
        def _do_import():
            if not path.exists():
                raise FileNotFoundError(f"file not found -> {path}")
            return ImportStep().apply(None, {"path": str(path)})

        imp = _safe("import", stages, _do_import)
        in_body = imp.body if imp is not None else None
        if in_body is None:
            return SkillResult(
                body=body, history=EntityHistoryMap(),
                extras={"part_analysis": {
                    "report": None, "report_html": None, "report_pdf": None,
                    "feature_catalog": None, "key_dimensions": None,
                    "reconstruction": None, "bbox_mm": None,
                    "_stages": stages, "part_id": part_id,
                    "error": "import failed — cannot analyze.",
                }},
            )
        shape = in_body.wrapped if hasattr(in_body, "wrapped") else in_body

        # ── 2. quality report (auto-runs extract_quality_report + dfm) + HTML
        emit = _safe("quality_report", stages, lambda: EmitQualityReport().apply(
            in_body, {
                "part_id": part_id,
                "processes": list(args.processes),
                "pull_direction": [float(c) for c in args.pull_direction],
                "include_html": bool(args.include_html),
                "render_views": bool(args.render_views),
            },
        ))
        report = report_html = None
        if emit is not None:
            report = emit.extras.get("quality_report_v1") or emit.extras.get("report")
            report_html = emit.extras.get("quality_report_html") or emit.extras.get("html")

        # ── 3. true .pdf (opt-in; honest about engine availability) ─────────
        report_pdf = None
        if args.pdf and isinstance(report, dict):
            pdf_out = _safe("pdf", stages,
                            lambda: build_pdf_deliverable(report))
            if pdf_out is not None:
                # Carry metadata + bytes presence, never inline the raw bytes
                # into the summary dict.
                report_pdf = {
                    "format": pdf_out.get("format"),
                    "pdf_available": pdf_out.get("pdf_available"),
                    "pdf_engine": pdf_out.get("pdf_engine"),
                    "pdf_bytes": pdf_out.get("pdf_bytes"),
                    "note": pdf_out.get("note"),
                }

        # ── 4. reverse-engineering feature catalog ─────────────────────────
        cat_res = _safe("feature_catalog", stages,
                        lambda: ExtractFeatureCatalog().apply(in_body, {}))
        catalog = cat_res.extras.get("feature_catalog") if cat_res is not None else None
        cat_summary = None
        if isinstance(catalog, dict):
            cat_summary = {
                "counts": {
                    k: len(catalog.get(k) or [])
                    for k in ("holes", "pockets", "bosses", "ribs", "lugs",
                              "symmetries", "patterns", "edge_blends",
                              "sweep_features", "loft_features",
                              "revolve_features")
                    if isinstance(catalog.get(k), list)
                },
                "base_thickness_mm": catalog.get("base_thickness_mm"),
                "catalog": catalog,
            }

        # ── 5. editable key dimensions ─────────────────────────────────────
        key_dims = None
        if isinstance(catalog, dict):
            kd = _safe("key_dimensions", stages,
                       lambda: IdentifyKeyDimensions().apply(
                           in_body, {"catalog": catalog}))
            if kd is not None:
                key_dims = kd.extras.get("key_dimensions")

        # ── 6. optional box-mode reconstruction + Hausdorff fidelity ───────
        reconstruction = None
        if args.reconstruct and isinstance(catalog, dict):
            reconstruction = _safe("reconstruction", stages,
                                   lambda: self._reconstruct(
                                       in_body, shape, catalog,
                                       args.base_profile_mode))

        # Surface the reconstruction fidelity (strategy + Hausdorff) in the HTML
        # deliverable — the routing win (sparse assembly box 1009mm -> preserve
        # 152mm) was otherwise invisible to the user. Additive + behind
        # reconstruct=True, so the default report HTML golden stays byte-identical.
        if args.include_html and report_html and isinstance(reconstruction, dict):
            report_html = self._inject_reconstruction_html(
                report_html, reconstruction)

        # ── 7. optional EDITABLE parametric build123d script ───────────────
        parametric_script = None
        if args.emit_script:
            def _emit():
                from phone_designer.skills.reverse_engineer.emit_parametric_script import (  # noqa: E501
                    EmitParametricScript,
                )
                return EmitParametricScript().apply(
                    in_body, {"verify": bool(args.reconstruct)},
                ).extras.get("parametric_script")
            parametric_script = _safe("parametric_script", stages, _emit)

        analysis = {
            "part_id": part_id,
            "bbox_mm": _bbox_mm(shape),
            "report": report,
            "report_html": report_html if args.include_html else None,
            "report_pdf": report_pdf,
            "feature_catalog": cat_summary,
            "key_dimensions": key_dims,
            "reconstruction": reconstruction,
            "parametric_script": parametric_script,
            "_stages": stages,
        }
        return SkillResult(
            body=body, history=EntityHistoryMap(),
            extras={"part_analysis": analysis},
        )

    @staticmethod
    def _reconstruct(in_body, shape, catalog, base_profile_mode):
        """Pick the reconstruction strategy, then score it by geometry_deviation
        Hausdorff vs the original.

        A true multi-body assembly (>=3 closed shells, per base_step_kind_chooser)
        is hopeless in box mode — a single placeholder slab cannot represent N
        sparse solids (as1_pe_203 box hausdorff 1009mm vs preserve 153mm). Route
        THOSE to preserve_brep (keep the original B-rep, re-apply the detected
        cuts). Any preserve failure (e.g. a huge assembly that times out / errors)
        FALLS BACK to box, so the worst case equals the prior box-only behaviour.
        Single bodies keep box + base_profile_mode='auto': the chooser's
        single-body 'preserve_brep' verdict is deliberately NOT acted on, because
        box + freeform 'auto' is better-or-equal there (e.g. Ventilator box
        0.048mm vs preserve 0.91mm). 2026-06-18.
        """
        chosen = "box"
        try:
            from phone_designer.skills.inspect.base_step_kind_chooser import (
                BaseStepKindChooser,
            )
            hint = (
                BaseStepKindChooser().apply(in_body, {}).extras
                .get("base_step_kind_hint") or {}
            )
            chosen = hint.get("chosen", "box")
        except Exception:
            chosen = "box"

        if chosen == "assembly":
            try:
                out = AnalyzePart._reconstruct_preserve(in_body, shape, catalog)
                if out is not None and out.get("hausdorff_mm") is not None:
                    out["strategy"] = "preserve_brep_assembly"
                    return out
            except Exception:
                pass  # any preserve failure → fall back to box below
        out = AnalyzePart._reconstruct_box(
            in_body, shape, catalog, base_profile_mode
        )
        if isinstance(out, dict):
            out.setdefault("strategy", "box")
        return out

    @staticmethod
    def _reconstruct_box(in_body, shape, catalog, base_profile_mode):
        """Box-mode RE reconstruction scored by geometry_deviation Hausdorff
        vs the original. Returns {match_ratio, hausdorff_mm, base_mechanism,
        plan_steps} or raises (caught by _safe)."""
        import os
        import tempfile

        from build123d import Part
        from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

        from phone_designer.plan.executor import PlanExecutor
        from phone_designer.plan.yaml_io import load_plan
        from phone_designer.skills.inspect.geometry_deviation import (
            GeometryDeviation,
        )
        from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
            ExtractFeatureCatalog,
        )
        from phone_designer.skills.reverse_engineer.feature_fidelity_diff import (
            FeatureFidelityDiff,
        )
        from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
            PlanFromFeatureCatalog,
        )

        # write a unique plan file so concurrent callers never race the shared
        # plans/reconstructed_plan.yaml (the Phase-0 plan_out_path fix).
        tmpdir = tempfile.mkdtemp(prefix="analyze_part_")
        plan_path = os.path.join(tmpdir, "plan.yaml")
        PlanFromFeatureCatalog().apply(in_body, {
            "catalog": catalog,
            "base_step_kind": "box",
            "base_profile_mode": base_profile_mode,
            "plan_out_path": plan_path,
        })
        plan = load_plan(plan_path)
        result = PlanExecutor(plan).run()
        regen = result.final_body
        if regen is None:
            raise RuntimeError("reconstruction produced no body")

        out: dict[str, Any] = {
            "plan_steps": len(plan.steps),
            "base_mechanism": plan.steps[0].skill if plan.steps else None,
        }

        # feature match ratio (box-frame remap)
        init = catalog.get("initial_bbox_mm")
        try:
            regen_cat = ExtractFeatureCatalog().apply(regen, {}).extras["feature_catalog"]
            if init and len(init) >= 6:
                cx = (init[0] + init[3]) / 2
                cy = (init[1] + init[4]) / 2
                zmin = init[2]
                regen_cat["frame_translation_mm"] = [-cx, -cy, -zmin]
            fid = FeatureFidelityDiff().apply(
                regen, {"catalog_a": catalog, "catalog_b": regen_cat},
            ).extras["feature_fidelity"]
            out["match_ratio"] = fid.get("overall_match_ratio")
        except Exception:
            out["match_ratio"] = None

        # geometric Hausdorff vs the original (anti-fake ground truth)
        try:
            ref = os.path.join(tmpdir, "orig.step")
            w = STEPControl_Writer()
            w.Transfer(shape, STEPControl_AsIs)
            w.Write(ref)
            transform = None
            if init and len(init) >= 6:
                cx = (init[0] + init[3]) / 2
                cy = (init[1] + init[4]) / 2
                zmin = init[2]
                transform = [[1, 0, 0, -cx], [0, 1, 0, -cy],
                             [0, 0, 1, -zmin], [0, 0, 0, 1]]
            gd = GeometryDeviation().apply(Part(regen) if not hasattr(regen, "wrapped") else regen, {
                "reference_step_path": ref,
                "linear_deflection_mm": 0.3,
                "align": "rigid" if transform else "none",
                **({"transform_4x4": transform} if transform else {}),
            })
            out["hausdorff_mm"] = gd.extras["geometry_deviation"].get("hausdorff_mm")
        except Exception:
            out["hausdorff_mm"] = None

        return out

    @staticmethod
    def _inject_reconstruction_html(html: str, recon: dict) -> str:
        """Append a 'Reconstruction fidelity' block to the quality-report HTML —
        strategy + base mechanism + the geometry_deviation HAUSDORFF (the
        ground-truth metric) + feature match_ratio. Inserted before </body>.
        Additive (only when reconstruct=True + include_html=True), so the default
        report HTML golden is byte-identical. 2026-06-18."""
        from html import escape

        haus = recon.get("hausdorff_mm")
        match = recon.get("match_ratio")
        strat = recon.get("strategy") or "box"
        base = recon.get("base_mechanism")
        haus_s = "—" if haus is None else f"{haus:.4g} mm"
        match_s = "—" if match is None else f"{match:.4g}"

        def _row(label: str, value: str, bold: bool = False) -> str:
            v = f"<b>{value}</b>" if bold else value
            return (
                "<tr><td style=\"padding:2px 14px 2px 0;color:#656d76\">"
                f"{escape(label)}</td><td>{v}</td></tr>"
            )

        block = (
            "<section style=\"margin:18px 0;padding:12px 14px;border:1px solid "
            "#d0d7de;border-radius:8px\">"
            "<h2 style=\"font-size:15px;margin:0 0 8px\">Reconstruction fidelity</h2>"
            "<table style=\"border-collapse:collapse;font-size:13px\">"
            + _row("strategy", escape(str(strat)))
            + _row("base mechanism", escape(str(base)) if base else "—")
            + _row("Hausdorff (ground truth)", escape(haus_s), bold=True)
            + _row("feature match_ratio", escape(match_s))
            + "</table>"
            "<p style=\"font-size:11px;color:#8c959f;margin:8px 0 0\">"
            "Reconstruction quality is judged by geometry_deviation Hausdorff "
            "(mm), not match_ratio.</p></section>"
        )
        idx = html.lower().rfind("</body>")
        if idx != -1:
            return html[:idx] + block + html[idx:]
        return html + block

    @staticmethod
    def _reconstruct_preserve(in_body, shape, catalog):
        """preserve_brep reconstruction for true multi-body assemblies: keep the
        original B-rep and re-apply the detected cuts, scored by geometry_deviation
        Hausdorff. Unlike the box path this stays in the ORIGINAL WORLD FRAME — the
        plan executes from initial_body=in_body and there is no world->box shift,
        so match_ratio uses no frame_translation and geometry_deviation aligns with
        identity. Returns {match_ratio, hausdorff_mm, base_mechanism, plan_steps}
        or raises (the caller catches and falls back to box). 2026-06-18."""
        import os
        import tempfile

        from build123d import Part
        from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

        from phone_designer.plan.executor import PlanExecutor
        from phone_designer.plan.yaml_io import load_plan
        from phone_designer.skills.inspect.geometry_deviation import (
            GeometryDeviation,
        )
        from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
            ExtractFeatureCatalog,
        )
        from phone_designer.skills.reverse_engineer.feature_fidelity_diff import (
            FeatureFidelityDiff,
        )
        from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
            PlanFromFeatureCatalog,
        )

        tmpdir = tempfile.mkdtemp(prefix="analyze_part_pre_")
        plan_path = os.path.join(tmpdir, "plan.yaml")
        PlanFromFeatureCatalog().apply(in_body, {
            "catalog": catalog,
            "base_step_kind": "preserve_brep",
            "plan_out_path": plan_path,
        })
        plan = load_plan(plan_path)
        # preserve_brep STARTS from the original body (the cuts no-op against the
        # real topology) — mirrors the corpus regress preserve lane.
        result = PlanExecutor(plan).run(initial_body=in_body)
        regen = result.final_body
        if regen is None:
            raise RuntimeError("preserve reconstruction produced no body")

        out: dict[str, Any] = {
            "plan_steps": len(plan.steps),
            "base_mechanism": plan.steps[0].skill if plan.steps else None,
        }

        # match ratio — SAME world frame, so no frame_translation remap.
        try:
            regen_cat = ExtractFeatureCatalog().apply(regen, {}).extras[
                "feature_catalog"
            ]
            fid = FeatureFidelityDiff().apply(
                regen, {"catalog_a": catalog, "catalog_b": regen_cat},
            ).extras["feature_fidelity"]
            out["match_ratio"] = fid.get("overall_match_ratio")
        except Exception:
            out["match_ratio"] = None

        # geometric Hausdorff vs the original — identity alignment (shared frame).
        try:
            ref = os.path.join(tmpdir, "orig.step")
            w = STEPControl_Writer()
            w.Transfer(shape, STEPControl_AsIs)
            w.Write(ref)
            gd = GeometryDeviation().apply(
                Part(regen) if not hasattr(regen, "wrapped") else regen, {
                    "reference_step_path": ref,
                    "linear_deflection_mm": 0.3,
                    "align": "none",
                },
            )
            out["hausdorff_mm"] = gd.extras["geometry_deviation"].get("hausdorff_mm")
        except Exception:
            out["hausdorff_mm"] = None

        return out
