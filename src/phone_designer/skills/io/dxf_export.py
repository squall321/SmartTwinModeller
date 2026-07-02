"""dxf_export — atomic, read-only. Write 2D curves of a body to a DXF file.

The universal 2D fab format (laser / waterjet / plasma cutting, sheet-metal
nesting). The repo ALREADY computes the exact 2D loops — this skill turns them into
the manufacturing artifact that previously could never leave the system.

Four sources (``source``):
  * ``section`` (default) — the body's cross-section at a plane, projected into that
    plane's in-plane frame (reuses inspect/cross_section polylines).
  * ``silhouette`` — ALL edges brute-projected along a view direction (reuses
    inspect/silhouette polylines_2d). Hidden edges are included — this is NOT a
    cut-ready outer outline; identical duplicate loops and zero-extent
    (view-parallel) edges are dropped, but visibility is not computed. For a
    laser/waterjet cut file use ``section`` or ``flat_pattern``.
  * ``flat_pattern`` — the sheet-metal flat blank outline (reuses
    detect_sheet_metal flat_pattern.outline_2d).
  * ``hlr`` — hidden-line-removal drawing view (reuses inspect/hlr_view):
    visible curves (VCompound + visible outlines) land on the ``VISIBLE``
    layer, hidden curves (HCompound + hidden outlines) on the ``HIDDEN``
    layer (dashed linetype when available). ``plane_normal`` is the view
    direction (camera → body). If hlr_view fell back to the brute silhouette
    (face budget / HLR failure) the HIDDEN layer is empty and extras carry
    the honest ``label='non_cut_ready'``.

Each 2D loop is written as a DXF LWPOLYLINE (closed if it closes within tol) via
``ezdxf`` (already installed as a build123d dependency). Read-only — the body is
unchanged; only the filesystem is touched.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult

_EPS_CLOSE = 1e-3


def write_layered_dxf(
    path: str | Path,
    layer_loops: dict[str, list[list[tuple[float, float]]]],
    *,
    layer_linetypes: dict[str, str] | None = None,
) -> dict[str, int]:
    """Write 2D loops onto named DXF layers. Returns {layer: n_polylines}.

    Shared by DxfExport(source='hlr') and inspect/drawing_sheet so both emit
    the exact same DXF convention (mm units, LWPOLYLINE, close-within-tol).
    ALL requested layers are created even when empty — a drawing consumer can
    rely on VISIBLE/HIDDEN existing. Raises ValueError('fm.dxf_write_failed:…')
    on any I/O / ezdxf failure; raw error preserved.
    """
    try:
        import ezdxf
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"fm.dxf_write_failed: ezdxf unavailable: {exc}")

    out_path = Path(path)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"fm.dxf_write_failed: {type(exc).__name__}: {exc}")

    doc = ezdxf.new(setup=True)
    doc.units = 4  # mm
    msp = doc.modelspace()
    linetypes = layer_linetypes or {}
    written: dict[str, int] = {}
    for layer_name, loops in layer_loops.items():
        if layer_name != "0" and layer_name not in doc.layers:
            attribs: dict[str, Any] = {}
            lt_req = linetypes.get(layer_name)
            if lt_req:
                # ezdxf's setup=True ships DASHED but NOT a 'HIDDEN'
                # linetype — fall back so hidden layers still render dashed.
                for lt in (lt_req, "DASHED"):
                    if lt in doc.linetypes:
                        attribs["linetype"] = lt
                        break
            doc.layers.add(layer_name, **attribs)
        n = 0
        for loop in loops:
            if len(loop) < 2:
                continue
            closed = (math.hypot(loop[0][0] - loop[-1][0],
                                 loop[0][1] - loop[-1][1]) <= _EPS_CLOSE)
            pts = loop[:-1] if closed and len(loop) > 2 else loop
            msp.add_lwpolyline(pts, close=closed,
                               dxfattribs={"layer": layer_name})
            n += 1
        written[layer_name] = n

    try:
        doc.saveas(str(out_path))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"fm.dxf_write_failed: {type(exc).__name__}: {exc}")
    if not out_path.exists():
        raise ValueError(f"fm.dxf_write_failed: file not written → {out_path}")
    return written


def _inplane_frame(normal):
    """Orthonormal (u, v) in the plane with the given normal (Gram-Schmidt)."""
    nx, ny, nz = normal
    m = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    n = (nx / m, ny / m, nz / m)
    ref = (0.0, 0.0, 1.0) if abs(n[2]) < 0.9 else (1.0, 0.0, 0.0)
    # u = normalize(ref - (ref·n) n)
    d = ref[0] * n[0] + ref[1] * n[1] + ref[2] * n[2]
    ux, uy, uz = ref[0] - d * n[0], ref[1] - d * n[1], ref[2] - d * n[2]
    um = math.sqrt(ux * ux + uy * uy + uz * uz) or 1.0
    u = (ux / um, uy / um, uz / um)
    v = (n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2],
         n[0] * u[1] - n[1] * u[0])
    return u, v


@skill(
    name="dxf_export",
    category="inspect",
    level="atomic",
    summary="Write the body's 2D curves to a DXF file. source='section' "
            "(cross-section at a plane, projected 2D — laser/waterjet/plasma, "
            "nesting) | 'silhouette' (all-edges projection along a view dir; "
            "hidden edges included — NOT a cut-ready outline) | 'flat_pattern' "
            "(sheet-metal blank outline — cut-ready) | 'hlr' (hidden-line-"
            "removal drawing view: VISIBLE + HIDDEN curves on separate DXF "
            "layers; plane_normal = view direction). Each loop → a DXF "
            "LWPOLYLINE via ezdxf. Read-only — only the filesystem is touched.",
    selector_kinds=[],
    history_rules={},
    produces_features=["dxf_artifact"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.no_curves", "fm.dxf_write_failed"],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="body_present")],
)
class DxfExport(SkillBase):
    class Args(BaseModel):
        path: str = Field(min_length=1, description="Output .dxf path.")
        source: Literal["section", "silhouette", "flat_pattern", "hlr"] = Field(
            default="section",
            description="Which 2D geometry to export.")
        plane_origin: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 0.0),
            description="For source='section': a point on the section plane.")
        plane_normal: tuple[float, float, float] = Field(
            default=(0.0, 0.0, 1.0),
            description="For source='section': the section-plane normal. For "
                        "'silhouette'/'hlr': the view direction (camera → "
                        "body).")
        hlr_max_face_count: int | None = Field(
            default=None, ge=1,
            description="For source='hlr': override hlr_view's exact-HLR "
                        "face budget (over budget → honest silhouette "
                        "fallback, HIDDEN layer empty, label "
                        "'non_cut_ready'). None → hlr_view default.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("fm.no_curves: dxf_export needs a body.")

        if args.source == "hlr":
            return self._apply_hlr(body, args)

        loops_2d = self._collect_loops(body, args)
        if not loops_2d:
            raise ValueError(
                f"fm.no_curves: source='{args.source}' produced no 2D loops "
                "(the plane may miss the body, or the flat pattern was refused).")

        try:
            import ezdxf
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"fm.dxf_write_failed: ezdxf unavailable: {exc}")

        out_path = Path(args.path)
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"fm.dxf_write_failed: {type(exc).__name__}: {exc}")

        doc = ezdxf.new(setup=True)
        doc.units = 4  # mm
        msp = doc.modelspace()
        n_written = 0
        for loop in loops_2d:
            if len(loop) < 2:
                continue
            closed = (math.hypot(loop[0][0] - loop[-1][0],
                                 loop[0][1] - loop[-1][1]) <= _EPS_CLOSE)
            pts = loop[:-1] if closed and len(loop) > 2 else loop
            msp.add_lwpolyline(pts, close=closed)
            n_written += 1

        if n_written == 0:
            raise ValueError("fm.no_curves: no loop had ≥2 points.")

        try:
            doc.saveas(str(out_path))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"fm.dxf_write_failed: {type(exc).__name__}: {exc}")
        if not out_path.exists():
            raise ValueError(f"fm.dxf_write_failed: file not written → {out_path}")

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "written_path": str(out_path),
                "source": args.source,
                "n_polylines": n_written,
            },
        )

    def _apply_hlr(self, body: Any, args: Args) -> SkillResult:
        """source='hlr' — layered drawing view (VISIBLE / HIDDEN)."""
        from phone_designer.skills.inspect.hlr_view import HlrView

        hlr_args: dict[str, Any] = {"view_direction": list(args.plane_normal)}
        if args.hlr_max_face_count is not None:
            hlr_args["max_face_count"] = args.hlr_max_face_count
        ex = HlrView().apply(body, hlr_args).extras
        outline = ex.get("outline", {})
        visible = (list(ex.get("visible_polylines_2d", []))
                   + list(outline.get("visible_polylines_2d", [])))
        hidden = (list(ex.get("hidden_polylines_2d", []))
                  + list(outline.get("hidden_polylines_2d", [])))
        if not (visible or hidden):
            raise ValueError(
                "fm.no_curves: source='hlr' produced no 2D curves.")

        written = write_layered_dxf(
            args.path,
            {"VISIBLE": visible, "HIDDEN": hidden},
            layer_linetypes={"HIDDEN": "HIDDEN"},
        )
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "written_path": str(Path(args.path)),
                "source": "hlr",
                "n_polylines": sum(written.values()),
                "layers": written,
                # honest passthrough: 'non_cut_ready' when hlr_view fell back
                # to the brute silhouette (HIDDEN layer empty in that case).
                "hlr_mode": ex.get("mode"),
                "label": ex.get("label"),
                "note": ex.get("note"),
            },
        )

    def _collect_loops(self, body, args) -> list[list[tuple[float, float]]]:
        """Return a list of 2D (x,y) loops for the chosen source."""
        if args.source == "section":
            from phone_designer.skills.inspect.cross_section import CrossSection
            ex = CrossSection().apply(body, {
                "plane_origin": list(args.plane_origin),
                "plane_normal": list(args.plane_normal)}).extras
            u, v = _inplane_frame(args.plane_normal)
            o = args.plane_origin
            out = []
            for poly in ex.get("polylines", []):
                loop = []
                for (x, y, z) in poly:
                    dx, dy, dz = x - o[0], y - o[1], z - o[2]
                    pu = dx * u[0] + dy * u[1] + dz * u[2]
                    pv = dx * v[0] + dy * v[1] + dz * v[2]
                    loop.append((round(pu, 4), round(pv, 4)))
                if loop:
                    out.append(loop)
            return out

        if args.source == "silhouette":
            from phone_designer.skills.inspect.silhouette import Silhouette
            ex = Silhouette().apply(body, {
                "view_direction": list(args.plane_normal)}).extras
            # Brute projection emits EVERY edge: coincident edges (e.g. a
            # box's top + bottom rims) project to identical 2D loops and
            # view-parallel edges collapse to a single point. Dedupe exact
            # duplicates (either direction) and drop zero-extent loops so the
            # DXF is not littered with double-traced / degenerate polylines.
            out = []
            seen: set[tuple] = set()
            for poly in ex.get("polylines_2d", []):
                if not poly:
                    continue
                loop = [(round(p[0], 4), round(p[1], 4)) for p in poly]
                us = [p[0] for p in loop]
                vs = [p[1] for p in loop]
                if (max(us) - min(us) <= _EPS_CLOSE
                        and max(vs) - min(vs) <= _EPS_CLOSE):
                    continue  # degenerate: edge parallel to the view direction
                key = tuple(loop)
                if key in seen or tuple(reversed(loop)) in seen:
                    continue  # exact duplicate of an already-kept loop
                seen.add(key)
                out.append(loop)
            return out

        # flat_pattern
        from phone_designer.skills.inspect.detect_sheet_metal import DetectSheetMetal
        ex = DetectSheetMetal().apply(body, {}).extras
        outline = (((ex.get("sheet_metal") or {}).get("flat_pattern") or {})
                   .get("outline_2d"))
        if not outline:
            return []
        return [[(round(p[0], 4), round(p[1], 4)) for p in outline]]
