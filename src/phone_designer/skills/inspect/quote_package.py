"""quote_package — inspect macro, read-only (Phase-1 track 1-5, RFQ bundle).

ONE call turns the working body into the zip a buyer attaches to an RFQ:

    out_dir/quote_package.zip
      ├─ part.step        # the body, exported (STEPControl, AP242)
      ├─ section.dxf      # cross-section at the body centroid plane
      ├─ drawing/…        # Phase-2 drawing sheet (include_drawing=True):
      │                   #   part_drawing.html + per-view DXFs, grade='draft'
      └─ manifest.json    # the machine-readable contract (below)

manifest.json sections (completeness IS the contract):
  part                    — step filename, size, solids, volume
  costs                   — unit cost per (process × lot_size) via
                            inspect/estimate_cost, EVERY entry labeled
                            grade='estimate' IN the manifest (not just here)
  process_recommendation  — inspect/recommend_process result incl. the
                            excluded-process reasons (grade='estimate')
  quality_summary         — mass_properties + identify_key_dimensions
                            (CHOSEN over emit_quality_report: that path runs
                            extract_quality_report's full multi-section sweep
                            — multi-second, needless for an RFQ headline;
                            mass + key dims are headless, milliseconds, and
                            'measured'-grade. The manifest SAYS this.)
  dxf                     — the cross-section artifact entry
  drawing                 — (include_drawing=True, the default) the promoted
                            Phase-2 drawing_sheet: {html, dxf_views:[...],
                            grade='draft', label='DRAFT FOR REVIEW'}. FAILURE
                            ISOLATION: if the drawing fails the quote still
                            ships — the section becomes {'status':'failed',
                            'error':...} and every other artifact (costs /
                            step / dxf) is untouched. include_drawing=False
                            omits the section AND the zip entries entirely
                            (byte-compatible file set with the pre-drawing
                            contract).

PERFORMANCE (the 5.8x lever recommend_process pinned): the geometry drivers
(volume via mass_properties + feature catalog) are extracted ONCE here and
injected into every estimate_cost call via its ``precomputed`` arg — mirroring
recommend_process._extract_drivers exactly, so the per-lot cost table costs
milliseconds per lot instead of a catalog re-extraction each.

HONESTY:
  * every cost number is grade='estimate' (a model, not a quote) — labeled on
    each cost row AND on the recommendation block inside manifest.json;
  * a body that is not a closed solid (no TopAbs_SOLID or volume <= 1e-6 —
    VolumeProperties lies on open shells) is REFUSED (fm.no_solid_body):
    costing/sectioning an open shell would fabricate numbers;
  * per-unit cost is analytically non-increasing with lot (unit = base + T/L)
    — the test suite pins that monotonicity;
  * if the centroid section fails on Z, X/Y normals are tried; a total DXF
    failure is recorded honestly in the manifest (path=None + error), never
    silently dropped.

extras: {"zip_path": str, "manifest": {...}} — strict-JSON-safe
(json.dumps(..., allow_nan=False) asserted). Body unchanged (post
body_present). Read-only — only the filesystem is touched.
"""
from __future__ import annotations

import json
import math
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult

# Costable process keys — single-sourced from recommend_process._CANDIDATES so
# the accepted set never drifts (same convention as cost_min_variant_search).
from phone_designer.skills.inspect.recommend_process import _CANDIDATES

_COSTABLE_KEYS = [c[0] for c in _CANDIDATES if c[4]]

SCHEMA_VERSION = 1
_STEP_NAME = "part.step"
_DXF_NAME = "section.dxf"
_MANIFEST_NAME = "manifest.json"
_ZIP_NAME = "quote_package.zip"
_DRAWING_DIR = "drawing"
_DRAWING_PART_NAME = "part"


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _json_safe(obj: Any) -> Any:
    """Strict-JSON sanitiser (inf/nan -> None; tuples -> lists; unknown ->
    str). Local copy — skills must not depend on mcp_support."""
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


def _solid_stats(body: Any) -> tuple[int, float]:
    """(n_solids, volume_mm3). is_solid = SOLID count AND volume > 1e-6 —
    VolumeProperties_s reports garbage on open shells, so both are required."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    shape = _occt_shape(body)
    n = 0
    ex = TopExp_Explorer(shape, TopAbs_SOLID)
    while ex.More():
        n += 1
        ex.Next()
    vol = 0.0
    try:
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props)
        vol = float(props.Mass())
    except Exception:
        vol = 0.0
    return n, vol


@skill(
    name="quote_package",
    category="inspect",
    level="macro",
    summary="ONE-call RFQ bundle: export the working body to STEP, price it "
            "per lot size (estimate_cost with precomputed drivers — geometry "
            "extracted ONCE, the 5.8x fast path), recommend a process (with "
            "excluded-process reasons), attach a headless quality summary "
            "(mass_properties + key dimensions) and a DXF cross-section at "
            "the body centroid, plus (include_drawing=True, default) the "
            "Phase-2 drawing_sheet (third-angle HTML sheet + per-view DXFs, "
            "grade='draft', DRAFT FOR REVIEW label) under drawing/ — a "
            "drawing failure NEVER kills the quote, it is recorded honestly "
            "in manifest['drawing'] — and zip everything with a "
            "manifest.json in which EVERY cost artifact is labeled "
            "grade='estimate'. Refuses a non-solid body (fm.no_solid_body). "
            "Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["quote_package"],
    expansion=["mass_properties", "estimate_cost", "recommend_process",
               "dxf_export", "drawing_sheet"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.no_solid_body", "fm.invalid_args"],
    cost_hint=4.0,
    result_grade="estimate",
    post_conditions=[PostCondition(kind="body_present")],
)
class QuotePackage(SkillBase):
    class Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

        out_dir: str = Field(
            min_length=1,
            description="Directory for quote_package.zip + its artifacts "
                        "(created if missing).")
        material: str = Field(
            default="aluminum",
            description="Material key -> estimate_cost / recommend_process.")
        lot_sizes: list[int] = Field(
            default_factory=lambda: [1, 100, 1000],
            description="Lot sizes to price. Each gets a cost row per costed "
                        "process; per-unit cost is non-increasing with lot.")
        processes: list[str] | None = Field(
            default=None,
            description="Subset of the costable process keys "
                        f"{_COSTABLE_KEYS} to price AND to hand "
                        "recommend_process as candidates. None -> "
                        "recommend_process sees all candidates and the cost "
                        "table prices the RECOMMENDED process (fallback "
                        "cnc_3axis). Unknown keys RAISE (a typo is a bug).")
        include_drawing: bool = Field(
            default=True,
            description="Attach the Phase-2 drawing_sheet (third-angle HTML "
                        "sheet + per-view layered DXFs, grade='draft', "
                        "'DRAFT FOR REVIEW' label) under drawing/ in the zip "
                        "and as manifest['drawing']. A drawing failure NEVER "
                        "kills the quote: it is recorded honestly as "
                        "{'status':'failed','error':...} and the rest of the "
                        "package still ships. False -> the pre-drawing file "
                        "set exactly (no drawing/ entries, no manifest key).")

        @field_validator("lot_sizes")
        @classmethod
        def _lots_valid(cls, v):
            if not v:
                raise ValueError(
                    "fm.invalid_args: lot_sizes must be a non-empty list of "
                    "ints >= 1")
            bad = [x for x in v if not isinstance(x, int) or x < 1]
            if bad:
                raise ValueError(
                    f"fm.invalid_args: lot_sizes entries must be ints >= 1 "
                    f"(bad: {bad})")
            return v

        @field_validator("processes")
        @classmethod
        def _procs_valid(cls, v):
            if v is None:
                return v
            if not v:
                raise ValueError(
                    "fm.invalid_args: processes must be a non-empty subset of "
                    f"{_COSTABLE_KEYS}, or None for all")
            bad = [p for p in v if p not in _COSTABLE_KEYS]
            if bad:
                raise ValueError(
                    f"fm.invalid_args: unknown process key(s) {bad}; known "
                    f"costable keys: {_COSTABLE_KEYS}")
            return v

    # ──────────────────────────────────────────────────────────────────────
    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.inspect.estimate_cost import EstimateCost
        from phone_designer.skills.inspect.mass_properties import MassProperties
        from phone_designer.skills.inspect.recommend_process import RecommendProcess
        from phone_designer.skills.io.dxf_export import DxfExport
        from phone_designer.skills.io.step_export_v2 import StepExportV2
        from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
            ExtractFeatureCatalog,
        )
        from phone_designer.skills.reverse_engineer.identify_key_dimensions import (
            identify_key_dimensions,
        )

        # ── refuse a non-solid up front (both checks — house ruling) ──────
        if body is None:
            raise ValueError(
                "fm.no_solid_body: quote_package needs a body (got None).")
        n_solids, volume_mm3 = _solid_stats(body)
        if n_solids == 0 or volume_mm3 <= 1e-6:
            raise ValueError(
                f"fm.no_solid_body: quote_package refuses a non-solid body "
                f"(solids={n_solids}, volume={round(volume_mm3, 6)} mm3) — "
                "cost / mass / DXF section on an open shell would be "
                "fabricated numbers, not an RFQ.")

        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        lots = sorted(set(int(x) for x in args.lot_sizes))

        # ── extract the geometry drivers ONCE (the 5.8x lever) ────────────
        # Mirrors recommend_process._extract_drivers exactly, but keeps the
        # full catalog too (identify_key_dimensions reads it for the quality
        # summary) — one MassProperties + one ExtractFeatureCatalog, total.
        mp = MassProperties().apply(body, {"density_g_per_cm3": 1.0}).extras
        catalog = ExtractFeatureCatalog().apply(body, {}).extras[
            "feature_catalog"]
        precomputed = {
            "volume_mm3": mp.get("volume_mm3"),
            "n_holes": len(catalog.get("holes") or []),
            "n_pockets": len(catalog.get("pockets") or []),
            "n_bosses": len(catalog.get("bosses") or []),
            "max_wall_mm": catalog.get("base_thickness_mm"),
            "bbox": catalog.get("initial_bbox_mm"),
        }

        # ── STEP export ────────────────────────────────────────────────────
        step_path = out_dir / _STEP_NAME
        step_extras = StepExportV2().apply(body, {
            "path": str(step_path), "schema": "AP242", "with_colors": False,
        }).extras

        # ── process recommendation (with excluded reasons) ─────────────────
        rec_block = RecommendProcess().apply(body, {
            "material": args.material,
            "lot_size": max(lots),
            "candidates": args.processes,   # None -> all candidates
        }).extras["process_recommendation"]
        rec_manifest = {k: v for k, v in rec_block.items() if k != "_stages"}
        rec_manifest["grade"] = "estimate"  # explicit label IN the manifest

        # ── per-lot cost table (estimate_cost precomputed fast path) ──────
        if args.processes is not None:
            cost_procs = list(args.processes)
        else:
            recommended = (rec_block.get("recommendation") or {}).get("process")
            cost_procs = [recommended if recommended in _COSTABLE_KEYS
                          else "cnc_3axis"]
        costs: list[dict[str, Any]] = []
        for proc in cost_procs:
            for lot in lots:
                ce = EstimateCost().apply(body, {
                    "process": proc,
                    "material": args.material,
                    "lot_size": lot,
                    "precomputed": precomputed,
                }).extras["cost_estimate"]
                costs.append({
                    "process": proc,
                    "lot_size": lot,
                    "unit_cost_usd": ce.get("unit_cost_usd"),
                    "cycle_time_s": ce.get("cycle_time_s"),
                    "reliability": ce.get("reliability"),
                    "breakdown_usd": ce.get("breakdown_usd"),
                    "grade": ce.get("grade", "estimate"),  # 'estimate', pinned
                })

        # ── quality summary (headless-cheap path, and say so) ─────────────
        key_dims = identify_key_dimensions(catalog)
        quality_summary = {
            "method": "mass_properties+key_dimensions",
            "method_note": (
                "emit_quality_report was NOT used: it runs "
                "extract_quality_report's full multi-section sweep "
                "(multi-second). mass_properties + identify_key_dimensions "
                "are headless, milliseconds, and measured-grade — the honest "
                "RFQ headline."),
            "grade": "measured",
            "mass_properties": {
                "volume_mm3": mp.get("volume_mm3"),
                "centroid": mp.get("centroid"),
                "mass_g_at_density_1": mp.get("mass_g"),
                "density_note": "mass computed at density 1.0 g/cm3 "
                                "(mass_g == volume_cm3); scale by the real "
                                "material density.",
            },
            "key_dimensions": key_dims,
            "n_solids": n_solids,
        }

        # ── DXF cross-section at the body centroid plane ───────────────────
        centroid = [float(c) for c in (mp.get("centroid") or [0.0, 0.0, 0.0])]
        dxf_path = out_dir / _DXF_NAME
        dxf_entry: dict[str, Any] = {
            "path": None, "source": "section", "plane_origin": centroid,
            "plane_normal": None, "n_polylines": None, "error": None,
        }
        for normal in ([0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]):
            try:
                dx = DxfExport().apply(body, {
                    "path": str(dxf_path),
                    "source": "section",
                    "plane_origin": tuple(centroid),
                    "plane_normal": tuple(normal),
                }).extras
                dxf_entry.update({
                    "path": _DXF_NAME,
                    "plane_normal": normal,
                    "n_polylines": dx.get("n_polylines"),
                    "error": None,
                })
                break
            except Exception as exc:  # noqa: BLE001 — try the next normal
                dxf_entry["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"

        # ── Phase-2 drawing sheet (promoted: plan 2-1 → 1-5) ───────────────
        # FAILURE ISOLATION: a drawing failure must NOT kill the quote — the
        # zip still ships costs + step + dxf, and the failure is recorded
        # honestly in manifest['drawing'] (status='failed' + raw error).
        drawing_entry: dict[str, Any] | None = None
        drawing_files: list[tuple[Path, str]] = []  # (disk_path, zip arcname)
        if args.include_drawing:
            from phone_designer.skills.inspect.drawing_sheet import (
                DRAFT_LABEL,
                DrawingSheet,
            )
            drawing_dir = out_dir / _DRAWING_DIR
            try:
                ds = DrawingSheet().apply(body, {
                    "out_dir": str(drawing_dir),
                    "part_name": _DRAWING_PART_NAME,
                    "include_section": False,  # the zip already ships
                                               # section.dxf at the centroid
                }).extras["drawing_sheet"]
                written = ds.get("written") or {}
                html_disk = Path(written["html"])
                html_arc = f"{_DRAWING_DIR}/{html_disk.name}"
                drawing_files.append((html_disk, html_arc))
                dxf_views: list[str] = []
                for view_path in (written.get("dxf") or {}).values():
                    dxf_disk = Path(view_path)
                    dxf_arc = f"{_DRAWING_DIR}/{dxf_disk.name}"
                    drawing_files.append((dxf_disk, dxf_arc))
                    dxf_views.append(dxf_arc)
                drawing_entry = {
                    "status": "ok",
                    "html": html_arc,
                    "dxf_views": dxf_views,
                    "grade": ds.get("grade", "draft"),   # 'draft' — a DRAFT
                    "label": DRAFT_LABEL,                # sheet, not released
                }
            except Exception as exc:  # noqa: BLE001 — isolation, by contract
                drawing_files = []
                drawing_entry = {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    "html": None,
                    "dxf_views": [],
                }

        # ── manifest ────────────────────────────────────────────────────────
        artifacts = [_STEP_NAME]
        if dxf_entry["path"] is not None:
            artifacts.append(_DXF_NAME)
        artifacts.extend(arc for _, arc in drawing_files)
        artifacts.append(_MANIFEST_NAME)
        manifest = _json_safe({
            "schema_version": SCHEMA_VERSION,
            "kind": "quote_package",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "material": args.material,
            "lot_sizes": lots,
            "processes_costed": cost_procs,
            "cost_model": {
                "grade": "estimate",
                "fast_path": "estimate_cost.precomputed drivers — geometry "
                             "extracted once, reused across all lots",
                "note": "every cost figure in this manifest is a heuristic "
                        "MODEL (grade='estimate'), not a quote.",
            },
            "part": {
                "step_path": _STEP_NAME,
                "step_schema": step_extras.get("schema"),
                "file_size_bytes": step_extras.get("file_size_bytes"),
                "n_solids": n_solids,
                "volume_mm3": mp.get("volume_mm3"),
            },
            "costs": costs,
            "process_recommendation": rec_manifest,
            "quality_summary": quality_summary,
            "dxf": dxf_entry,
            # include_drawing=False omits the key entirely — the pre-drawing
            # manifest schema, byte-for-byte key set.
            **({"drawing": drawing_entry} if drawing_entry is not None else {}),
            "artifacts": artifacts,
        })
        manifest_path = out_dir / _MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, allow_nan=False, indent=2), encoding="utf-8")

        # ── zip ─────────────────────────────────────────────────────────────
        zip_path = out_dir / _ZIP_NAME
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(step_path, arcname=_STEP_NAME)
            if dxf_entry["path"] is not None:
                zf.write(dxf_path, arcname=_DXF_NAME)
            for disk_path, arcname in drawing_files:
                zf.write(disk_path, arcname=arcname)
            zf.write(manifest_path, arcname=_MANIFEST_NAME)

        extras = {"zip_path": str(zip_path), "manifest": manifest}
        json.dumps(extras, allow_nan=False)  # strict-JSON contract, asserted
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
