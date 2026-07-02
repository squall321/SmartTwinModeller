"""_session_tools — session primitives over a BODY OBJECT.

The MCP session layer (mcp_server.py + BodyStore) resolves ``body_id`` → body and
wires these four primitives into FastMCP tools. Everything here takes/returns the
body OBJECT itself — no store, no ids, no FastMCP imports — so the functions are
directly unit-testable and the wiring layer stays a thin adapter.

    import_body(path)             STEP file → body + honest part metadata
    modify_body(body, spec)       run a generate_from_spec-style step list ON body
    measure_body(body, what)      mass / obb / dimensions via the inspect skills
    preview_body(body, out_dir)   PNG previews via inspect/_report_render.build_views

Contracts (house rules):
  * Every returned dict is STRICT-JSON-safe once the ``body`` key is removed —
    ``json.dumps(meta, allow_nan=False)`` passes (non-finite floats become None).
  * ``is_solid`` = at least one TopAbs_SOLID sub-shape AND volume > 1e-6 mm³
    (VolumeProperties_s returns a nonzero pseudo-volume for open shells, so
    volume alone LIES — topology never does).
  * Structured refusals are ``ValueError`` with ``fm.*`` tokens:
      fm.unsupported_format / fm.file_not_found / fm.step_parse_failed /
      fm.too_many_faces / fm.empty_body / fm.bad_spec / fm.unknown_op /
      fm.unknown_measure
  * Raw per-step errors from the executor are reported verbatim (never masked).
  * ``preview_body`` under PHONE_DESIGNER_UI_HEADLESS returns the honest skip
    marker (skipped=True, note carries 'skipped_no_gl') — NEVER a fake/black PNG.
"""
from __future__ import annotations

import base64
import math
import os
from pathlib import Path
from typing import Any

__all__ = ["import_body", "modify_body", "measure_body", "preview_body"]

_SOLID_VOLUME_FLOOR_MM3 = 1e-6


# ──────────────────────────────────────────────────────────────────────────────
# shared helpers


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _json_safe(value: Any) -> Any:
    """Deep-copy ``value`` replacing non-finite floats with None.

    Guarantees json.dumps(..., allow_nan=False) passes on every metadata dict
    we return (the house STRICT-JSON rule). Tuples become lists.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _volume_mm3(body: Any) -> float:
    """Volume via the mass_properties inspect skill (single source of truth)."""
    from phone_designer.skills.inspect.mass_properties import MassProperties

    try:
        v = MassProperties().apply(body, {}).extras.get("volume_mm3")
        v = float(v or 0.0)
        return v if math.isfinite(v) else 0.0
    except Exception:  # noqa: BLE001 — a measurement failure is 0, not a crash
        return 0.0


def _is_solid(body: Any, volume_mm3: float) -> bool:
    """House rule: TopAbs_SOLID present AND volume > 1e-6 (open shells lie)."""
    if body is None or volume_mm3 <= _SOLID_VOLUME_FLOOR_MM3:
        return False
    try:
        from OCP.TopAbs import TopAbs_SOLID
        from OCP.TopExp import TopExp_Explorer

        return TopExp_Explorer(_occt_shape(body), TopAbs_SOLID).More()
    except Exception:  # noqa: BLE001
        return False


def _bbox_mm(body: Any) -> list[float] | None:
    """World-AABB extents [dx, dy, dz] — mirrors generate_from_spec._bbox_mm."""
    if body is None:
        return None
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib

        bb = Bnd_Box()
        BRepBndLib.Add_s(_occt_shape(body), bb)
        if bb.IsVoid():
            return None
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        return [round(xmax - xmin, 3), round(ymax - ymin, 3),
                round(zmax - zmin, 3)]
    except Exception:  # noqa: BLE001
        return None


# ──────────────────────────────────────────────────────────────────────────────
# import_body


def import_body(path: str, *, max_faces: int = 4000) -> dict:
    """Load a single-part STEP file into a session body.

    Mirrors the compose/_load_other ``step_path`` branch (STEPControl_Reader →
    TransferRoots → OneShape) and wraps the result as a build123d ``Part`` like
    create/import_step does, so every registered skill accepts it downstream.

    Returns ``{'body', 'step_path', 'n_faces', 'is_solid', 'volume_mm3',
    'bbox_mm'}``. Everything except ``body`` is STRICT-JSON-safe.

    Refusals (ValueError):
      * fm.unsupported_format — not a .step/.stp file (STEP only; no IGES here).
      * fm.file_not_found    — path does not exist.
      * fm.step_parse_failed — the STEP reader rejected the file.
      * fm.too_many_faces    — n_faces > max_faces; the session tools are for
        SINGLE parts. Use assembly_reverse_engineer / cad_analyze with
        part_path for big assemblies instead.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in (".step", ".stp"):
        raise ValueError(
            f"fm.unsupported_format: only STEP (.step/.stp) is supported here, "
            f"got '{suffix or '<no extension>'}' ({path}). IGES/other formats "
            f"are intentionally not supported — convert to STEP first.")
    if not p.exists():
        raise ValueError(f"fm.file_not_found: STEP not found: {p}")

    from build123d import Part
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    status = reader.ReadFile(str(p))
    if status != IFSelect_RetDone:
        raise ValueError(
            f"fm.step_parse_failed: STEP parse failed: {p} (status={status})")
    reader.TransferRoots()
    shape = reader.OneShape()

    from phone_designer.skills._resolvers import _all_faces

    n_faces = len(_all_faces(shape))
    if n_faces > max_faces:
        raise ValueError(
            f"fm.too_many_faces: part has {n_faces} faces > max_faces="
            f"{max_faces} — the session tools are for single parts; use "
            f"assembly_reverse_engineer / cad_analyze with part_path instead.")

    body = Part(shape)
    volume = _volume_mm3(body)
    return {
        "body": body,
        "step_path": path,
        "n_faces": n_faces,
        "is_solid": _is_solid(body, volume),
        "volume_mm3": _json_safe(round(volume, 3)),
        "bbox_mm": _json_safe(_bbox_mm(body)),
    }


# ──────────────────────────────────────────────────────────────────────────────
# modify_body


def modify_body(body: Any, spec: list[dict]) -> dict:
    """Apply a generate_from_spec-style step list ON an existing body.

    Builds Plan/Step exactly like create/generate_from_spec.py — the whole skill
    library is registered first, then EVERY step's op is pre-checked against the
    registry so an unknown op fails FAST (before any geometry is touched), then
    ``PlanExecutor(plan).run(initial_body=body)`` with per-step failure
    isolation (continue_on_step_failure=True).

    Returns ``{'body': new_body, 'report': {n_steps, n_ok, ok,
    steps: [{op, status, error}], volume_mm3, is_solid, bbox_mm}}``.
    ``report`` is STRICT-JSON-safe; per-step ``error`` strings are the raw
    executor failure messages (never masked). ``ok`` requires every step to
    pass AND the result to be a closed solid (house is_solid rule).

    Refusals (ValueError, raised BEFORE executing anything):
      * fm.empty_body  — body is None.
      * fm.bad_spec    — spec not a non-empty list of {op, args} objects.
      * fm.unknown_op  — a step names a skill the registry does not know.
    """
    if body is None:
        raise ValueError(
            "fm.empty_body: modify_body needs a body — import_body a STEP or "
            "generate one first.")
    if not isinstance(spec, list) or not spec:
        raise ValueError(
            "fm.bad_spec: spec must be a non-empty list of "
            "{'op': <skill>, 'args': {...}} steps.")

    from phone_designer.plan.executor import PlanExecutor, _import_all_skills
    from phone_designer.plan.model import Plan, Step
    from phone_designer.skills._registry import registry

    # Register the WHOLE skill library BEFORE the per-step registry pre-check —
    # otherwise a skill not yet imported is wrongly rejected as unknown.
    _import_all_skills()

    steps: list[Step] = []
    for i, item in enumerate(spec):
        if not isinstance(item, dict):
            raise ValueError(
                f"fm.bad_spec: step {i}: not an object "
                f"(got {type(item).__name__}).")
        op = item.get("op") or item.get("skill")
        if not op:
            raise ValueError(f"fm.bad_spec: step {i}: missing 'op'/'skill'.")
        sargs = item.get("args") or {}
        if not isinstance(sargs, dict):
            raise ValueError(
                f"fm.bad_spec: step {i}: 'args' must be an object, got "
                f"{type(sargs).__name__}.")
        try:
            registry.get(op)  # fail-fast pre-check: unknown op → clear refusal
        except Exception as exc:  # noqa: BLE001 — append the raw error, never mask
            raise ValueError(
                f"fm.unknown_op: step {i}: unknown skill '{op}' — check "
                f"cad_list_skills for the registry. ({exc})") from exc
        steps.append(Step(id=f"s{i}_{op}", skill=op, args=dict(sargs)))

    plan = Plan(plan_name="session_modify", steps=steps,
                continue_on_step_failure=True)
    result = PlanExecutor(plan).run(initial_body=body)
    new_body = result.final_body if result.final_body is not None else body

    step_report: list[dict] = []
    for s in plan.steps:
        status = getattr(s.status, "value", str(s.status))
        step_report.append({
            "op": s.skill,
            "status": status,
            # RAW executor failure message — appended verbatim, never masked.
            "error": (s.failure.message if getattr(s, "failure", None)
                      else None),
        })

    n_ok = sum(1 for s in step_report if s["status"] == "pass")
    volume = _volume_mm3(new_body)
    is_solid = _is_solid(new_body, volume)
    report = {
        "n_steps": len(step_report),
        "n_ok": n_ok,
        "ok": bool(new_body is not None and n_ok == len(step_report)
                   and is_solid),
        "steps": step_report,
        "volume_mm3": round(volume, 3),
        "is_solid": is_solid,
        "bbox_mm": _bbox_mm(new_body),
    }
    return {"body": new_body, "report": _json_safe(report)}


# ──────────────────────────────────────────────────────────────────────────────
# measure_body


_MEASURES = ("mass", "obb", "dimensions")


def measure_body(body: Any, what: str = "mass") -> dict:
    """Measure a body via the inspect skills (read-only; body unchanged).

    what='mass'       → inspect/mass_properties extras
                        (volume_mm3, centroid, mass_g, inertia…)
    what='obb'        → inspect/oriented_bounding_box extras (obb + aabb)
    what='dimensions' → inspect/auto_dimension extras (its Args model is empty,
                        so skill defaults apply: key_dimensions, bbox,
                        cylinder_count)

    Returns ``{'what': what, **skill_extras}``, STRICT-JSON-safe.
    Unknown ``what`` → ValueError fm.unknown_measure; None body → fm.empty_body.
    """
    if body is None:
        raise ValueError("fm.empty_body: measure_body needs a body.")
    if what == "mass":
        from phone_designer.skills.inspect.mass_properties import MassProperties

        extras = MassProperties().apply(body, {}).extras
    elif what == "obb":
        from phone_designer.skills.inspect.oriented_bounding_box import (
            OrientedBoundingBox,
        )

        extras = OrientedBoundingBox().apply(body, {}).extras
    elif what == "dimensions":
        from phone_designer.skills.inspect.auto_dimension import AutoDimension

        extras = AutoDimension().apply(body, {}).extras
    else:
        raise ValueError(
            f"fm.unknown_measure: '{what}' — expected one of {_MEASURES}.")
    return _json_safe({"what": what, **dict(extras)})


# ──────────────────────────────────────────────────────────────────────────────
# preview_body


# Requested view name → build_views image id. build_views renders ONE shaded
# view ('iso') plus mid-plane SECTION views named by cutting-plane normal
# (section_x/y/z). 'front'/'top'/'side' therefore honestly map to SECTION
# renders — build_views has no orthographic shaded camera views.
_VIEW_ALIASES = {
    "iso": "iso",
    "front": "section_y",   # XZ mid-plane (normal +Y) — the front cut
    "top": "section_z",     # XY mid-plane (normal +Z) — the top cut
    "side": "section_x",    # YZ mid-plane (normal +X)
    "right": "section_x",
}


def preview_body(body: Any, out_dir: str,
                 views: tuple = ("iso", "front", "top")) -> dict:
    """Render preview PNGs of ``body`` into ``out_dir``.

    Reuses inspect/_report_render.build_views exactly the way
    emit_quality_report does (build_views(body) inside a try/except that
    degrades to a skip marker — a render must never crash the session).

    Returns ``{'images': {view: png_path | None}, 'skipped': bool,
    'note': str}`` (STRICT-JSON-safe).

    HONEST-SKIP contract: under PHONE_DESIGNER_UI_HEADLESS (or any missing-GL
    environment) build_views returns render_status='skipped_no_gl'; this
    function then returns skipped=True with 'skipped_no_gl' in the note and
    every image None — it NEVER writes an empty/black placeholder PNG.

    Honest mapping note: 'iso' is a shaded render; 'front'/'top'/'side' map to
    build_views' mid-plane SECTION renders (section_y/section_z/section_x) —
    not orthographic shaded views. Unmappable/unrendered views come back None.
    """
    views = tuple(views)
    try:
        from phone_designer.skills.inspect._report_render import build_views

        vr = build_views(body)
    except Exception as exc:  # render must never break the session
        vr = {
            "render_status": "skipped_no_gl",
            "images": {},
            "note": f"render error: {type(exc).__name__}: {exc}",
        }

    status = vr.get("render_status")
    if status != "ok":
        # Honest skip — no files written, no fake images.
        return _json_safe({
            "images": {v: None for v in views},
            "skipped": True,
            "note": f"{status}: {vr.get('note', '')}",
        })

    os.makedirs(out_dir, exist_ok=True)
    b64_images: dict = vr.get("images", {})
    images: dict[str, str | None] = {}
    written: list[str] = []
    for v in views:
        image_id = _VIEW_ALIASES.get(v, v)
        b64 = b64_images.get(image_id)
        if not b64:
            images[v] = None
            continue
        png_path = str(Path(out_dir) / f"{v}.png")
        with open(png_path, "wb") as fh:
            fh.write(base64.b64decode(b64))
        images[v] = png_path
        written.append(v)

    note = ("rendered " + ", ".join(written) if written
            else "render ok but none of the requested views were produced")
    note += (" — note: front/top/side are mid-plane SECTION renders "
             "(build_views' section_y/section_z/section_x), not orthographic "
             "shaded views.")
    return _json_safe({
        "images": images,
        "skipped": False,
        "note": note,
    })
