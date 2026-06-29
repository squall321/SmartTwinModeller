"""MCP server — drive the CAD modeller from an LLM client (Claude, …).

Exposes the reverse-engineering + GENERATION + manufacturing-analysis pipeline as
a small, ergonomic set of MCP tools over stdio. The LLM client becomes the
natural-language → spec interpreter: it discovers ops with ``cad_list_skills`` /
``cad_get_skill_schema`` (every one of the ~383 registered skills has a JSON-Schema
args model), composes a build spec, and calls ``cad_generate`` — no custom NL
parser needed.

Tools (all ``cad_*``-namespaced): cad_list_skills, cad_get_skill_schema,
cad_generate, cad_analyze, cad_estimate_cost, cad_recommend_process, cad_export.

STATE / ARTIFACT MODEL: geometry flows as FILES + a session BODY CACHE.
``cad_generate`` writes the result to a STEP (+ optional STL / editable .py) in a
workspace dir, caches the live body, and returns the file paths + ``resource_uris``
(file:// URIs) + a ``body_id``. The analysis/export tools accept EXACTLY ONE of
``body_id`` (resolved to the cached body / STEP — no re-import) or ``part_path``.
The workspace defaults to ``$PHONE_DESIGNER_MCP_WORKSPACE`` or a temp dir.

Every cost / process recommendation is grade='estimate' (a model, not a quote) and
says so. Tools never crash the server — they return a structured ``{ok: False,
error: ...}`` instead.

Launch:  python -m phone_designer.mcp_server     (stdio)
"""
from __future__ import annotations

import os
import re
import tempfile
import traceback
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("phone-designer-cad")

_WORKSPACE = Path(os.environ.get("PHONE_DESIGNER_MCP_WORKSPACE")
                  or tempfile.mkdtemp(prefix="pd_mcp_"))
_WORKSPACE.mkdir(parents=True, exist_ok=True)


def _safe_name(name: str) -> str:
    """Sanitise an artifact basename — no path traversal / separators."""
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", (name or "part").strip()) or "part"
    return base[:64]


# ── session body cache: cad_generate mints a body_id; later tools can pass the
# body_id (resolved to its cached body + STEP) instead of re-passing a file path.
# The server is one stdio process, so the cache lives for the session.
_BODIES: dict[str, dict] = {}
_BODY_SEQ = [0]


def _mint_body_id() -> str:
    _BODY_SEQ[0] += 1
    return f"body_{_BODY_SEQ[0]}"


def _uri(path: str) -> str:
    return "file:///" + str(path).replace("\\", "/").lstrip("/")


def _resolve(part_path: str | None, body_id: str | None):
    """Return (body, step_path) from EXACTLY ONE of body_id | part_path."""
    if bool(part_path) == bool(body_id):
        raise ValueError("provide exactly one of part_path | body_id")
    if body_id:
        rec = _BODIES.get(body_id)
        if rec is None:
            raise ValueError(f"unknown body_id '{body_id}' (generate one first)")
        return rec["body"], rec.get("step_path")
    return _import_step(part_path), part_path


def _err(exc: Exception) -> dict:
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(limit=3)}


def _ensure_skills() -> None:
    from phone_designer.plan.executor import _import_all_skills
    _import_all_skills()


def _import_step(path: str):
    from phone_designer.skills.create.import_step import ImportStep
    return ImportStep().apply(None, {"path": path}).body


def _write_step(body, path: str) -> bool:
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    shape = body.wrapped if hasattr(body, "wrapped") else body
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    w.Write(path)
    return os.path.exists(path) and os.path.getsize(path) > 0


# ── discovery: let the LLM self-serve the skill library ───────────────────────
@mcp.tool()
def cad_list_skills(query: str = "", category: str = "", limit: int = 80) -> dict:
    """DISCOVERY (call FIRST to find op names for a cad_generate spec): list/search
    registered build/feature/analysis skills (name + one-line summary). Filter by a
    substring `query` and/or a `category` (create | modify_* | inspect |
    reverse_engineer). Fetch full args for a chosen op via cad_get_skill_schema."""
    try:
        _ensure_skills()
        from phone_designer.skills.export_manifest import build_manifest
        q, c = query.lower(), category.lower()
        out = []
        for s in build_manifest()["skills"]:
            if q and q not in s["name"].lower() and q not in (s.get("summary") or "").lower():
                continue
            if c and c not in (s.get("category") or "").lower():
                continue
            out.append({"name": s["name"], "level": s.get("level"),
                        "category": s.get("category"),
                        "summary": (s.get("summary") or "")[:160]})
        return {"ok": True, "n": len(out), "skills": out[:limit],
                "truncated": len(out) > limit}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cad_get_skill_schema(name: str) -> dict:
    """DISCOVERY (call after cad_list_skills, before cad_generate): get the
    JSON-Schema args model of one skill — the exact `args` to pass for that op in a
    cad_generate spec."""
    try:
        _ensure_skills()
        from phone_designer.skills.export_manifest import build_manifest
        for s in build_manifest()["skills"]:
            if s["name"] == name:
                return {"ok": True, "name": name, "summary": s.get("summary"),
                        "args_schema": s.get("args_schema")}
        return {"ok": False, "error": f"unknown skill '{name}'"}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ── generation: zero-base part from a declarative spec ────────────────────────
@mcp.tool()
def cad_generate(spec: list[dict], name: str = "part",
                 formats: list[str] | None = None) -> dict:
    """GENERATE a solid FROM SCRATCH from a spec — an ordered list of build steps
    [{"op": <skill_name>, "args": {...}}, ...] (first step usually a create skill
    like box/cylinder/gear, then features like hole/pocket; discover ops via
    cad_list_skills + cad_get_skill_schema). Writes the result to the workspace and
    returns the file paths + a build manifest. `formats` ⊆ {"step","stl","py"}
    (default ["step"]). Per-step failures are ISOLATED — check `status`
    (ok|partial|error) + steps[i].error to repair just the failing step."""
    try:
        _ensure_skills()
        from build123d import export_stl
        from phone_designer.skills.create.generate_from_spec import GenerateFromSpec
        name = _safe_name(name)
        res = GenerateFromSpec().apply(None, {"spec": spec, "plan_name": name})
        gen = res.extras["generated"]
        body = res.body
        files: dict[str, str] = {}
        fmts = [f.lower() for f in (formats or ["step"])]
        if body is not None and gen.get("is_solid"):
            stem = str(_WORKSPACE / name)
            if "step" in fmts and _write_step(body, stem + ".step"):
                files["step"] = stem + ".step"
            if "stl" in fmts:
                try:
                    export_stl(body, stem + ".stl")
                    files["stl"] = stem + ".stl"
                except Exception:  # noqa: BLE001
                    pass
            if "py" in fmts:
                try:
                    from phone_designer.skills.reverse_engineer.emit_parametric_script import (  # noqa: E501
                        EmitParametricScript,
                    )
                    ps = EmitParametricScript().apply(body, {}).extras.get(
                        "parametric_script") or {}
                    if ps.get("script"):
                        Path(stem + "_model.py").write_text(ps["script"], encoding="utf-8")
                        files["py"] = stem + "_model.py"
                except Exception:  # noqa: BLE001
                    pass
        # status: 'ok' (all built) | 'partial' (a solid IS written + analysable,
        # but some steps failed) | 'error' (no solid produced).
        if not gen.get("is_solid"):
            status = "error"
        elif gen.get("ok"):
            status = "ok"
        else:
            status = "partial"
        body_id = None
        if body is not None and gen.get("is_solid"):
            body_id = _mint_body_id()
            _BODIES[body_id] = {"body": body, "step_path": files.get("step"),
                                "name": name}
        return {"ok": gen.get("ok", False), "status": status,
                "body_id": body_id, "is_solid": gen.get("is_solid"),
                "volume_mm3": gen.get("volume_mm3"), "bbox_mm": gen.get("bbox_mm"),
                "n_steps": gen.get("n_steps"), "n_ok": gen.get("n_ok"),
                "steps": gen.get("steps"), "spec_errors": gen.get("spec_errors"),
                "files": files,
                "resource_uris": [_uri(p) for p in files.values()]}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ── analysis: each tool accepts EXACTLY ONE of body_id | part_path ────────────
@mcp.tool()
def cad_analyze(part_path: str = "", body_id: str = "",
                processes: list[str] | None = None,
                estimate_cost: bool = False, recognize_fits: bool = False,
                sheet_metal: bool = False, measure_fits: bool = False) -> dict:
    """Run the single-part analysis on a cad_generate body_id OR a STEP part_path
    (exactly one): quality report (topology / wall / draft / blends / DFM) +
    optional cost, ISO-286 fits, sheet-metal bend table, assembly fits. Writes the
    HTML report to the workspace; returns the structured analysis. All
    manufacturing numbers are grade='estimate'."""
    try:
        _ensure_skills()
        from phone_designer.skills.reverse_engineer.analyze_part import AnalyzePart
        _, step_path = _resolve(part_path or None, body_id or None)
        if not step_path:
            return {"ok": False, "error": "no STEP file for this body_id"}
        out = Path(step_path).with_suffix(".report.html")
        pa = AnalyzePart().apply(None, {
            "part_path": step_path,
            "processes": processes or ["cnc_milling", "injection_molding"],
            "include_html": True, "estimate_cost": estimate_cost,
            "recognize_fits": recognize_fits, "sheet_metal": sheet_metal,
            "measure_fits": measure_fits,
        }).extras["part_analysis"]
        html = pa.pop("report_html", None)
        if html:
            out.write_text(html, encoding="utf-8")
        return {"ok": True,
                "report_html_path": str(out) if html else None,
                "resource_uris": [_uri(str(out))] if html else [],
                "part_id": pa.get("part_id"), "bbox_mm": pa.get("bbox_mm"),
                "feature_counts": (pa.get("feature_catalog") or {}).get("counts"),
                "cost_estimate": pa.get("cost_estimate"),
                "fit_analysis": pa.get("fit_analysis"),
                "sheet_metal": pa.get("sheet_metal"),
                "assembly_fit": pa.get("assembly_fit"),
                "grade": "estimate"}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cad_estimate_cost(part_path: str = "", body_id: str = "",
                      process: str = "cnc_3axis", material: str = "aluminum",
                      lot_size: int = 1000) -> dict:
    """Estimate unit cost + cycle time for a cad_generate body_id OR a STEP
    part_path (exactly one) in a process (cnc_3axis | cnc_5axis | injection_mold_pa
    | sheet_laser_brake | sheet_progressive_die | …). grade='estimate' — a
    transparent heuristic, not a quote."""
    try:
        _ensure_skills()
        from phone_designer.skills.inspect.estimate_cost import EstimateCost
        body, _ = _resolve(part_path or None, body_id or None)
        ce = EstimateCost().apply(body, {
            "process": process, "material": material, "lot_size": lot_size,
        }).extras["cost_estimate"]
        return {"ok": True, **ce}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cad_recommend_process(part_path: str = "", body_id: str = "",
                          material: str = "aluminum", lot_size: int = 1000) -> dict:
    """Recommend the cheapest VIABLE manufacturing process for a cad_generate
    body_id OR a STEP part_path (exactly one) at a given lot + material, with
    cost-vs-volume crossovers (synthesises cost + DFM + sheet detection).
    NOTE: slower than the other tools (multiple cost models). grade='estimate'."""
    try:
        _ensure_skills()
        from phone_designer.skills.inspect.recommend_process import RecommendProcess
        body, _ = _resolve(part_path or None, body_id or None)
        pr = RecommendProcess().apply(body, {
            "material": material, "lot_size": lot_size,
        }).extras["process_recommendation"]
        # trim the verbose matrices for the LLM; keep the decision-relevant parts
        return {"ok": True, "recommendation": pr.get("recommendation"),
                "ranking": pr.get("ranking"), "excluded": pr.get("excluded"),
                "crossovers": pr.get("crossovers"),
                "advisories_unpriced": pr.get("advisories_unpriced"),
                "overall_flag": pr.get("overall_flag"),
                "confidence_note": pr.get("confidence_note"), "grade": "estimate"}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cad_export(body_id: str = "", part_path: str = "",
               formats: list[str] | None = None, name: str = "") -> dict:
    """Re-export a cad_generate body_id (or an existing STEP part_path) to STEP /
    STL / editable .py in the workspace WITHOUT regenerating. `formats` ⊆
    {"step","stl","py"} (default ["step"])."""
    try:
        _ensure_skills()
        from build123d import export_stl
        body, src_step = _resolve(part_path or None, body_id or None)
        stem = str(_WORKSPACE / _safe_name(
            name or (_BODIES.get(body_id, {}).get("name") if body_id else None)
            or "export"))
        files: dict[str, str] = {}
        for f in [x.lower() for x in (formats or ["step"])]:
            if f == "step" and _write_step(body, stem + ".step"):
                files["step"] = stem + ".step"
            elif f == "stl":
                export_stl(body, stem + ".stl")
                files["stl"] = stem + ".stl"
            elif f == "py":
                from phone_designer.skills.reverse_engineer.emit_parametric_script import (  # noqa: E501
                    EmitParametricScript,
                )
                ps = EmitParametricScript().apply(body, {}).extras.get(
                    "parametric_script") or {}
                if ps.get("script"):
                    Path(stem + "_model.py").write_text(ps["script"], encoding="utf-8")
                    files["py"] = stem + "_model.py"
        return {"ok": bool(files), "files": files,
                "resource_uris": [_uri(p) for p in files.values()]}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cad_repair_dfm(body_id: str = "", part_path: str = "",
                   processes: list[str] | None = None,
                   pull_direction: list[float] | None = None,
                   apply: bool = True, max_hausdorff_mm: float | None = None) -> dict:
    """AUTO-FIX the manufacturability (DFM) of a cad_generate body_id OR a STEP
    part_path (exactly one): fillet failing internal corners to the tool radius
    and add draft to sub-min-draft walls, each kept ONLY if the DFM verdict
    strictly improves within a bounded (Hausdorff-guarded) geometry change — else
    reverted (worst-case == input). Thin wall / undercut / sink are SUGGEST-only.
    When the body changes AND apply=True, a NEW body_id is minted for the repaired
    part (the input body is never mutated) + its STEP is written. Returns the
    per-process before/after verdict, the fixes applied, the suggestions, and the
    repaired body_id. grade='estimate' — a heuristic repair, not a guarantee."""
    try:
        _ensure_skills()
        from phone_designer.skills.repair.repair_dfm import RepairDfm
        body, _ = _resolve(part_path or None, body_id or None)
        args: dict = {
            "processes": processes or ["cnc_milling", "injection_molding"],
            "pull_direction": pull_direction or [0.0, 0.0, 1.0],
            "apply": apply,
        }
        if max_hausdorff_mm is not None:
            args["max_hausdorff_mm"] = max_hausdorff_mm
        res = RepairDfm().apply(body, args)
        rep = res.extras["dfm_repair"]
        repaired_id = None
        files: dict[str, str] = {}
        if rep.get("body_changed") and res.body is not None:
            repaired_id = _mint_body_id()
            name = _safe_name((_BODIES.get(body_id, {}).get("name")
                               if body_id else None) or "repaired")
            step = str(_WORKSPACE / f"{name}_{repaired_id}.step")
            if _write_step(res.body, step):
                files["step"] = step
            _BODIES[repaired_id] = {"body": res.body,
                                    "step_path": files.get("step"), "name": name}
        return {"ok": True, "repaired_body_id": repaired_id,
                "body_changed": rep.get("body_changed"), "grade": rep.get("grade"),
                "before": rep.get("before"), "after": rep.get("after"),
                "fixes_applied": rep.get("fixes_applied"),
                "fixes_rejected": rep.get("fixes_rejected"),
                "suggestions": rep.get("suggestions"),
                "total_hausdorff_mm": rep.get("total_hausdorff_mm"),
                "summary": rep.get("summary"),
                "files": files,
                "resource_uris": [_uri(p) for p in files.values()]}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cad_dfm_workflow(body_id: str = "", part_path: str = "",
                     processes: list[str] | None = None,
                     pull_direction: list[float] | None = None,
                     material: str = "aluminum", lot_size: int = 1000,
                     repair: bool = True) -> dict:
    """ORCHESTRATE the full make-it-manufacturable-and-quote-it pipeline in ONE
    call on a cad_generate body_id OR a STEP part_path (exactly one):
      1. DFM-repair the part (auto-fix fillets/draft, Hausdorff-guarded; skip with
         repair=False to only analyse + quote the input);
      2. recommend the cheapest VIABLE process + unit cost on the RESULTING body.
    Returns the before/after DFM verdict, the repair applied (+ a repaired body_id
    + STEP when the geometry changed), and the process recommendation/cost for the
    final part. Chains repair_dfm + recommend_process so the cost reflects the
    repaired geometry. grade='estimate' throughout."""
    try:
        _ensure_skills()
        from phone_designer.skills.inspect.recommend_process import RecommendProcess
        body, _ = _resolve(part_path or None, body_id or None)

        repair_out: dict | None = None
        final_body = body
        repaired_id = None
        files: dict[str, str] = {}
        if repair:
            from phone_designer.skills.repair.repair_dfm import RepairDfm
            rres = RepairDfm().apply(body, {
                "processes": processes or ["cnc_milling", "injection_molding"],
                "pull_direction": pull_direction or [0.0, 0.0, 1.0],
                "apply": True,
            })
            rep = rres.extras["dfm_repair"]
            repair_out = {
                "grade": rep.get("grade"), "body_changed": rep.get("body_changed"),
                "before": rep.get("before"), "after": rep.get("after"),
                "fixes_applied": rep.get("fixes_applied"),
                "suggestions": rep.get("suggestions"),
                "total_hausdorff_mm": rep.get("total_hausdorff_mm"),
                "summary": rep.get("summary")}
            if rep.get("body_changed") and rres.body is not None:
                final_body = rres.body
                repaired_id = _mint_body_id()
                name = _safe_name((_BODIES.get(body_id, {}).get("name")
                                   if body_id else None) or "repaired")
                step = str(_WORKSPACE / f"{name}_{repaired_id}.step")
                if _write_step(final_body, step):
                    files["step"] = step
                _BODIES[repaired_id] = {"body": final_body,
                                        "step_path": files.get("step"), "name": name}

        pr = RecommendProcess().apply(final_body, {
            "material": material, "lot_size": lot_size,
            "pull_direction": pull_direction or [0.0, 0.0, 1.0],
        }).extras["process_recommendation"]
        quote = {"recommendation": pr.get("recommendation"),
                 "ranking": pr.get("ranking"), "crossovers": pr.get("crossovers"),
                 "overall_flag": pr.get("overall_flag"),
                 "confidence_note": pr.get("confidence_note")}

        return {"ok": True, "repaired_body_id": repaired_id,
                "priced_body": "repaired" if repaired_id else "input",
                "repair": repair_out, "quote": quote, "grade": "estimate",
                "files": files,
                "resource_uris": [_uri(p) for p in files.values()]}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
