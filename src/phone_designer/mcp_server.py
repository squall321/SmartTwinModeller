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


# ── session body store: cad_generate/cad_import mint a body_id; later tools pass
# the body_id (resolved to its cached body + STEP). Backed by BodyStore: LRU of
# live bodies (max 32) with STEP-snapshot durability — an evicted body is
# transparently re-imported from its snapshot (geometry-only; in-session face
# tags are lost on re-import, documented). Lineage (parent links) powers cad_undo.
from phone_designer.mcp_support._body_store import BodyStore

_STORE = BodyStore(max_live=int(os.environ.get("PHONE_DESIGNER_MCP_MAX_LIVE", "32")))
_NAMES: dict[str, str] = {}  # body_id -> artifact basename (display only)


def _put_body(body, step_path: str | None, name: str,
              parent_id: str | None = None, op_note: str = "") -> str:
    bid = _STORE.put(body, step_path=step_path, parent_id=parent_id,
                     op_note=op_note or name)
    _NAMES[bid] = name
    return bid


def _body_name(body_id: str | None) -> str | None:
    return _NAMES.get(body_id or "")


def _uri(path: str) -> str:
    return "file:///" + str(path).replace("\\", "/").lstrip("/")


def _resolve(part_path: str | None, body_id: str | None):
    """Return (body, step_path) from EXACTLY ONE of body_id | part_path."""
    if bool(part_path) == bool(body_id):
        raise ValueError("provide exactly one of part_path | body_id")
    if body_id:
        rec = _STORE.get(body_id)  # fm.unknown_body_id if absent
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

        from phone_designer.mcp_support._failure_enrich import enrich_failures
        from phone_designer.mcp_support._guarded_exec import run_guarded_plan
        name = _safe_name(name)
        # HANG-PROOF: the plan runs in a warm worker subprocess with a hard
        # timeout (PHONE_DESIGNER_SKILL_TIMEOUT_S, default 120; 0 = inline) — one
        # stuck OCCT builder cannot block the stdio server. Only the STEP crosses
        # the pipe; the cached body is re-imported from it.
        guarded = run_guarded_plan(spec, out_dir=str(_WORKSPACE), name=name)
        if not guarded.get("ok"):
            return {"ok": False, "status": "error",
                    "error": guarded.get("error"), "body_id": None}
        gen = guarded["generated"]
        step_path = guarded.get("step_path")
        body = None
        if step_path and gen.get("is_solid"):
            body = _import_step(step_path)
        files: dict[str, str] = {}
        fmts = [f.lower() for f in (formats or ["step"])]
        if body is not None and gen.get("is_solid"):
            stem = str(_WORKSPACE / name)
            if "step" in fmts and step_path:
                files["step"] = step_path
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
            body_id = _put_body(body, files.get("step") or step_path, name,
                                op_note=f"generate:{name}")
        # machine-actionable failures: merge each step's ORIGINAL args back in
        # (the executor report only carries op/status/error) so enrichment can
        # dry-run selectors; enrichment ADDS keys, never touches 'error'.
        steps = list(gen.get("steps") or [])
        for i, st in enumerate(steps):
            if i < len(spec) and isinstance(spec[i], dict) and "args" not in st:
                st["args"] = spec[i].get("args")
        steps = enrich_failures(steps, body=body)
        return {"ok": gen.get("ok", False), "status": status,
                "body_id": body_id, "is_solid": gen.get("is_solid"),
                "volume_mm3": gen.get("volume_mm3"), "bbox_mm": gen.get("bbox_mm"),
                "n_steps": gen.get("n_steps"), "n_ok": gen.get("n_ok"),
                "steps": steps, "spec_errors": gen.get("spec_errors"),
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
            name or _body_name(body_id) or "export"))
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
            name = _safe_name(_body_name(body_id) or "repaired")
            step = str(_WORKSPACE / f"{name}_repaired_{len(_NAMES) + 1}.step")
            if _write_step(res.body, step):
                files["step"] = step
            repaired_id = _put_body(res.body, files.get("step"), name,
                                    parent_id=body_id or None,
                                    op_note="repair_dfm")
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
                name = _safe_name(_body_name(body_id) or "repaired")
                step = str(_WORKSPACE / f"{name}_repaired_{len(_NAMES) + 1}.step")
                if _write_step(final_body, step):
                    files["step"] = step
                repaired_id = _put_body(final_body, files.get("step"), name,
                                        parent_id=body_id or None,
                                        op_note="dfm_workflow:repair")

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


# ── Phase-1 session tools: stateful, self-correcting, hang-proof modelling ────
@mcp.tool()
def cad_import(path: str, max_faces: int = 4000) -> dict:
    """IMPORT an existing STEP file into the session as a body_id, so every other
    tool (cad_modify / cad_analyze / cad_measure / cad_quote_package …) can work on
    it without re-parsing the file. Refuses oversize parts (> max_faces faces) with
    a redirect to cad_analyze(part_path=…) / assembly_reverse_engineer."""
    try:
        _ensure_skills()
        from phone_designer.mcp_support._session_tools import import_body
        rec = import_body(path, max_faces=max_faces)
        body = rec.pop("body")
        name = _safe_name(Path(path).stem)
        body_id = _put_body(body, rec.get("step_path"), name,
                            op_note=f"import:{name}")
        return {"ok": True, "body_id": body_id, **rec}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cad_modify(body_id: str, spec: list[dict], name: str = "") -> dict:
    """MODIFY an existing session body with more build steps (same spec shape as
    cad_generate: [{"op": …, "args": {…}}, …]) WITHOUT resending the original
    spec. Runs hang-proofed (worker + timeout); on success mints a NEW body_id
    (the input body is never mutated — cad_undo returns to the parent). NOTE:
    geometry round-trips through STEP, so in-session face tags do not survive."""
    try:
        _ensure_skills()
        from phone_designer.mcp_support._failure_enrich import enrich_failures
        from phone_designer.mcp_support._guarded_exec import run_guarded_plan
        rec = _STORE.get(body_id)
        if not rec.get("step_path"):
            return {"ok": False, "error": f"body_id '{body_id}' has no STEP "
                    "snapshot to modify from"}
        new_name = _safe_name(name or f"{_body_name(body_id) or 'part'}_m{len(_NAMES) + 1}")
        guarded = run_guarded_plan(spec, initial_step_path=rec["step_path"],
                                   out_dir=str(_WORKSPACE), name=new_name)
        if not guarded.get("ok"):
            return {"ok": False, "error": guarded.get("error"), "body_id": None}
        gen = guarded["generated"]
        step_path = guarded.get("step_path")
        new_id = None
        new_body = None
        if step_path and gen.get("is_solid"):
            new_body = _import_step(step_path)
            ops = ",".join(s.get("op", "?") for s in spec[:3])
            new_id = _put_body(new_body, step_path, new_name,
                               parent_id=body_id, op_note=f"modify:{ops}")
        steps = list(gen.get("steps") or [])
        for i, st in enumerate(steps):
            if i < len(spec) and isinstance(spec[i], dict) and "args" not in st:
                st["args"] = spec[i].get("args")
        steps = enrich_failures(steps, body=new_body)
        return {"ok": bool(gen.get("ok")), "body_id": new_id,
                "parent_body_id": body_id, "is_solid": gen.get("is_solid"),
                "volume_mm3": gen.get("volume_mm3"), "bbox_mm": gen.get("bbox_mm"),
                "n_steps": gen.get("n_steps"), "n_ok": gen.get("n_ok"),
                "steps": steps,
                "files": {"step": step_path} if step_path else {},
                "resource_uris": [_uri(step_path)] if step_path else []}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cad_undo(body_id: str) -> dict:
    """UNDO one modelling step: return the PARENT body_id of a cad_modify /
    cad_repair_dfm result (bodies are immutable — undo is just moving back up the
    lineage). Includes the restored body's volume so the client can verify."""
    try:
        _ensure_skills()
        parent = _STORE.undo(body_id)
        if parent is None:
            return {"ok": False,
                    "error": f"fm.at_root: body_id '{body_id}' has no parent "
                             "(it was generated/imported directly)"}
        rec = _STORE.get(parent)
        from phone_designer.skills.inspect.mass_properties import MassProperties
        mp = MassProperties().apply(rec["body"], {}).extras
        return {"ok": True, "body_id": parent,
                "volume_mm3": mp.get("volume_mm3"),
                "op_note": rec.get("op_note"),
                "lineage": _STORE.lineage(parent)}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cad_measure(body_id: str = "", part_path: str = "",
                what: str = "mass") -> dict:
    """MEASURE a body (body_id or part_path, exactly one): what='mass' (volume,
    centroid, inertia), 'obb' (minimal oriented bounding box — true stock size),
    or 'dimensions' (auto-extracted key dimensions). Read-only."""
    try:
        _ensure_skills()
        from phone_designer.mcp_support._session_tools import measure_body
        body, _ = _resolve(part_path or None, body_id or None)
        return {"ok": True, "what": what, **measure_body(body, what=what)}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cad_preview(body_id: str = "", part_path: str = "",
                views: list[str] | None = None) -> dict:
    """RENDER preview images (PNG) of a body from standard views (iso/front/top/…)
    into the workspace, so the client can SEE what it modelled. In a no-GL
    (headless) environment this returns an honest skip marker instead of blank
    images."""
    try:
        _ensure_skills()
        from phone_designer.mcp_support._session_tools import preview_body
        body, _ = _resolve(part_path or None, body_id or None)
        out_dir = _WORKSPACE / "previews"
        out_dir.mkdir(parents=True, exist_ok=True)
        pv = preview_body(body, str(out_dir), views=tuple(views or ("iso", "front", "top")))
        uris = [_uri(p) for p in (pv.get("images") or {}).values() if p]
        return {"ok": True, **pv, "resource_uris": uris}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cad_preflight(spec: list[dict], body_id: str = "") -> dict:
    """PREFLIGHT a cad_generate/cad_modify spec WITHOUT executing it: per step —
    is the op known, do the args validate, and (when a body_id is given and the op
    targets faces/edges) how many entities does the selector match. Catch mistakes
    BEFORE paying for geometry. Fast (validation only)."""
    try:
        _ensure_skills()
        from phone_designer.mcp_support._failure_enrich import preflight
        body = _STORE.get(body_id)["body"] if body_id else None
        pf = preflight(spec, body=body)
        # ok = the TOOL ran; spec_ok = the SPEC verdict (all ops known + valid).
        return {"ok": True, "spec_ok": pf.pop("ok", None), **pf}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ── Phase-1 pillar tools: compare / variants / cheapest-variant / RFQ bundle ──
@mcp.tool()
def cad_compare(part_a: str = "", body_a: str = "",
                part_b: str = "", body_b: str = "") -> dict:
    """COMPARE two parts (rev-A vs rev-B; each side a STEP part path OR a session
    body_id): classification (identical / scaled / feature-changed / different),
    similarity score, scale factor, Hausdorff distance, per-feature diff.
    grade='estimate'."""
    try:
        _ensure_skills()
        from phone_designer.mcp_support._pillar_tools import compare_parts_tool
        _, a_path = _resolve(part_a or None, body_a or None)
        _, b_path = _resolve(part_b or None, body_b or None)
        if not a_path or not b_path:
            return {"ok": False, "error": "both sides need a STEP file"}
        return compare_parts_tool(a_path, b_path, str(_WORKSPACE))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cad_variants(part_path: str = "", body_id: str = "",
                 n_variants: int = 4) -> dict:
    """GENERATE a parametric VARIANT FAMILY of a part (body_id or part_path):
    identifies the key driving dimensions, then produces n scaled/varied family
    members with their parameter tables. grade='estimate'."""
    try:
        _ensure_skills()
        from phone_designer.mcp_support._pillar_tools import variants_tool
        _, step_path = _resolve(part_path or None, body_id or None)
        if not step_path:
            return {"ok": False, "error": "no STEP file for this body_id"}
        return variants_tool(step_path, n_variants=n_variants,
                             out_dir=str(_WORKSPACE))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cad_cheapest_variant(part_path: str = "", body_id: str = "",
                         process: str = "cnc_3axis", material: str = "aluminum",
                         lot_size: int = 1000) -> dict:
    """SEARCH the variant space for the CHEAPEST genuinely-viable variant of a part
    (body_id or part_path) in a process/material/lot. STRICT viability gate — a
    marginal candidate is never crowned. Slow (multiple cost evaluations).
    grade='estimate'."""
    try:
        _ensure_skills()
        from phone_designer.mcp_support._pillar_tools import cheapest_variant_tool
        _, step_path = _resolve(part_path or None, body_id or None)
        if not step_path:
            return {"ok": False, "error": "no STEP file for this body_id"}
        return cheapest_variant_tool(step_path, process=process,
                                     material=material, lot_size=lot_size,
                                     out_dir=str(_WORKSPACE))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cad_quote_package(body_id: str = "", part_path: str = "",
                      material: str = "aluminum",
                      lot_sizes: list[int] | None = None) -> dict:
    """ONE-CALL RFQ PACKAGE: bundle everything a supplier quote needs into a single
    zip — the STEP, per-lot cost estimates (grade='estimate', labeled inside the
    manifest), the process recommendation with exclusion reasons, a quality
    summary, and a DXF cross-section. Returns the zip path + manifest."""
    try:
        _ensure_skills()
        from phone_designer.skills.inspect.quote_package import QuotePackage
        body, _ = _resolve(part_path or None, body_id or None)
        out_dir = _WORKSPACE / f"quote_{_safe_name(_body_name(body_id) or 'part')}"
        qp = QuotePackage().apply(body, {
            "out_dir": str(out_dir), "material": material,
            "lot_sizes": lot_sizes or [1, 100, 1000],
        }).extras
        zip_path = qp.get("zip_path")
        return {"ok": True, "zip_path": zip_path,
                "manifest": qp.get("manifest"),
                "resource_uris": [_uri(zip_path)] if zip_path else []}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ── Phase-2 surfaces: recipes / drawings / parametric re-execute / assembly ───
@mcp.tool()
def cad_find_recipe(query: str, top_k: int = 5) -> dict:
    """FIND a proven spec RECIPE by intent (EN or KR — e.g. 'bent pipe', '기어',
    'counterbore holes'): returns ready-to-adapt cad_generate specs WITH their
    expected invariants (volume range, is_solid). Every recipe in the corpus is
    executed in CI, so a returned spec is known-good — start from one instead of
    composing cold."""
    try:
        _ensure_skills()
        from phone_designer.mcp_support._recipes import find_recipe
        hits = find_recipe(query, top_k=top_k)
        return {"ok": True, "n": len(hits), "recipes": hits}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cad_drawing(body_id: str = "", part_path: str = "",
                part_name: str = "", include_section: bool = True) -> dict:
    """Generate a third-angle ENGINEERING DRAWING SHEET (front/top/right + iso
    HLR views with hidden lines, optional section, title block, dimension table,
    hole table) as a self-contained HTML + layered VISIBLE/HIDDEN DXF per view.
    Labeled DRAFT FOR REVIEW — anchored callouts + tables, no automatic leader
    placement (v1)."""
    try:
        _ensure_skills()
        from phone_designer.skills.inspect.drawing_sheet import DrawingSheet
        body, _ = _resolve(part_path or None, body_id or None)
        pname = _safe_name(part_name or _body_name(body_id) or "part")
        out_dir = _WORKSPACE / "drawings"
        out_dir.mkdir(parents=True, exist_ok=True)
        ds = DrawingSheet().apply(body, {
            "out_dir": str(out_dir), "part_name": pname,
            "include_section": include_section,
        }).extras
        sheet = ds.get("drawing_sheet") or {}
        written = sheet.get("written") or {}
        uris = [_uri(p) for p in written.values()
                if isinstance(p, str) and os.path.exists(p)]
        return {"ok": True, "sheet": sheet, "resource_uris": uris,
                "grade": "draft"}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cad_reexecute(plan_path: str = "", plan: dict | None = None,
                  parameter_overrides: dict | None = None) -> dict:
    """RE-EXECUTE a saved parametric plan (schema v2) with parameter OVERRIDES —
    e.g. {'wall': 2.2} — and report the volume/bbox deltas vs the baseline plus
    any selector drift (the honest rebuild-error warning). The parametric
    re-edit loop for plans produced by cad_generate/cad_export('py')."""
    try:
        _ensure_skills()
        from phone_designer.skills.reverse_engineer.plan_reexecute import (
            PlanReexecute,
        )
        args: dict = {"parameter_overrides": parameter_overrides or {}}
        if plan_path:
            args["plan_path"] = plan_path
        if plan is not None:
            args["plan"] = plan
        rx = PlanReexecute().apply(None, args).extras["reexecute"]
        return {"ok": True, **rx}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cad_analyze_assembly(assembly_path: str,
                         per_component_timeout_s: float | None = None) -> dict:
    """Analyze a MULTI-BODY STEP assembly in one call: split into components,
    dedup identical parts by signature (50 bolts = 1 analysis), per-class light
    analysis, pairwise interference/clearance matrix (static pose only), and
    standard-part recognition — recognized standard parts get CATALOG lines,
    never machined-cost estimates. Returns the versioned AssemblyReportV1."""
    try:
        _ensure_skills()
        from phone_designer.skills.reverse_engineer.analyze_assembly import (
            AnalyzeAssembly,
        )
        args: dict = {"assembly_path": assembly_path}
        if per_component_timeout_s is not None:
            args["per_component_timeout_s"] = per_component_timeout_s
        rep = AnalyzeAssembly().apply(None, args).extras["assembly_analysis"]
        return {"ok": True, **rep}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
