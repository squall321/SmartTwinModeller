"""identify_fastener_recess — atomic, read-only.

Given a planar face selector that points at the OPENING face of a fastener
recess (the polygonal/star/cross outline cut into the host surface), classify
the recess as one of {hex_socket, torx, phillips} and pick the closest catalog
size from ``catalogs/standards/drivers.yaml``.

Strategy
--------
1. Resolve the face via the selector. Sample its boundary edges' vertex point
   cloud → bbox + convex-hull-radius proxy.
2. Count the number of straight edges on the outline and estimate the
   inscribed (flat-to-flat) + circumscribed (point-to-point) diameter:
     - exactly 6 straight edges  → hex_socket. Match to hex_socket['<af>']
       by across_flats_mm vs the inscribed diameter.
     - 12 edges that alternate concave/convex (a 6-point star) OR a count of
       boundary edges around 12 with a strong inner/outer-radius ratio
       (~0.71)  → torx. Match by major_diameter_mm vs the outer diameter.
     - cross-shaped (4 lobes, 8 short straight edges arranged orthogonally)
       OR boundary mixing planar lobes with a small central square / circle
       → phillips. Match by major_diameter_mm vs the outer diameter.

Body is not modified — post_conditions=[body_present].

extras["match"] = {
    "recess_kind": "hex|torx|phillips",
    "size":        "M3|T15|PH2",
    "confidence":  0.0..1.0,
    "outer_diameter_mm": float,
    "across_flats_mm":   float | None,
    "edge_count":        int,
}
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorBase, selector_from_dict
from phone_designer.skills._spec import SkillBase, SkillResult


def _load(family, name):
    import yaml, pathlib
    root = pathlib.Path(__file__).resolve().parents[4]
    path = root / "catalogs" / family / f"{name}.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text())


def _coerce_selector(s: Any) -> SelectorBase:
    if isinstance(s, SelectorBase):
        return s
    if isinstance(s, dict):
        return selector_from_dict(s)
    raise TypeError(f"unsupported selector type: {type(s).__name__}")


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _face_outer_vertices(face) -> list[tuple[float, float, float]]:
    """Sample the OUTER wire of the face: one point per edge endpoint (deduped
    in winding order)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_VERTEX
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopoDS import TopoDS

    pts: list[tuple[float, float, float]] = []
    try:
        outer = BRepTools.OuterWire_s(face)
    except Exception:
        outer = None

    if outer is None:
        # fallback: every vertex of every edge.
        exp = TopExp_Explorer(face, TopAbs_VERTEX)
        seen: set[tuple] = set()
        while exp.More():
            v = TopoDS.Vertex_s(exp.Current())
            p = BRep_Tool.Pnt_s(v)
            key = (round(p.X(), 4), round(p.Y(), 4), round(p.Z(), 4))
            if key not in seen:
                seen.add(key)
                pts.append((p.X(), p.Y(), p.Z()))
            exp.Next()
        return pts

    exp = TopExp_Explorer(outer, TopAbs_EDGE)
    seen: set[tuple] = set()
    while exp.More():
        e = TopoDS.Edge_s(exp.Current())
        v1 = TopExp.FirstVertex_s(e)
        v2 = TopExp.LastVertex_s(e)
        for v in (v1, v2):
            p = BRep_Tool.Pnt_s(v)
            key = (round(p.X(), 4), round(p.Y(), 4), round(p.Z(), 4))
            if key not in seen:
                seen.add(key)
                pts.append((p.X(), p.Y(), p.Z()))
        exp.Next()
    return pts


def _count_outer_edges(face) -> tuple[int, int]:
    """Return (n_straight_edges, n_total_edges) on the face's outer wire."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepTools import BRepTools
    from OCP.GeomAbs import GeomAbs_Line
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer

    try:
        outer = BRepTools.OuterWire_s(face)
    except Exception:
        return (0, 0)
    n_straight = 0
    n_total = 0
    exp = TopExp_Explorer(outer, TopAbs_EDGE)
    while exp.More():
        e = exp.Current()
        n_total += 1
        try:
            c = BRepAdaptor_Curve(e)
            if c.GetType() == GeomAbs_Line:
                n_straight += 1
        except Exception:
            pass
        exp.Next()
    return n_straight, n_total


def _xy_radius_stats(
    pts: list[tuple[float, float, float]],
) -> tuple[float, float, float, float]:
    """Project to the face plane (use bbox-PCA fallback: just XY in world).
    Return (cx, cy, r_min, r_max) — min/max distance of a vertex from centroid.
    Works ok for any planar opening face regardless of orientation since we
    project on the dominant in-plane axes via SVD when numpy is available;
    otherwise fall back to raw XY.
    """
    if not pts:
        return (0.0, 0.0, 0.0, 0.0)

    try:
        import numpy as np

        arr = np.array(pts, dtype=float)
        c = arr.mean(axis=0)
        centered = arr - c
        # 2 dominant axes of the cloud → planar (face) coords.
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
        u = vh[0]
        v = vh[1]
        proj = np.stack([centered @ u, centered @ v], axis=1)
        radii = np.linalg.norm(proj, axis=1)
        return (
            float(c[0]),
            float(c[1]),
            float(radii.min()),
            float(radii.max()),
        )
    except Exception:
        n = len(pts)
        cx = sum(p[0] for p in pts) / n
        cy = sum(p[1] for p in pts) / n
        radii = [
            math.sqrt((p[0] - cx) ** 2 + (p[1] - cy) ** 2)
            for p in pts
        ]
        return (cx, cy, min(radii), max(radii))


def _best_in_table(
    table: dict, key_field: str, measured: float,
) -> tuple[str, float, float]:
    """Find catalog key closest to ``measured`` on ``key_field``. Returns
    (key, catalog_value, deviation)."""
    best_key = None
    best_val = 0.0
    best_dev = float("inf")
    for k, entry in table.items():
        v = entry.get(key_field)
        if v is None:
            continue
        dev = abs(measured - float(v))
        if dev < best_dev:
            best_dev = dev
            best_val = float(v)
            best_key = k
    return (str(best_key) if best_key is not None else "", best_val, best_dev)


@skill(
    name="identify_fastener_recess",
    category="inspect",
    level="atomic",
    summary="Identify a fastener driver recess (hex / torx / phillips) by "
            "inspecting the outline of the selected planar face, then look "
            "the closest catalog size up in drivers.yaml. Read-only — body "
            "unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["fastener_recess_match"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class IdentifyFastenerRecess(SkillBase):
    class Args(BaseModel):
        face_selector: dict

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import resolve_faces

        shape = _occt_shape(body)
        sel = _coerce_selector(args.face_selector)
        faces = resolve_faces(shape, sel, body=body)
        if not faces:
            raise ValueError(
                f"identify_fastener_recess: face_selector matched 0 faces "
                f"(kind={sel.kind})"
            )
        face = faces[0]

        pts = _face_outer_vertices(face)
        n_straight, n_total = _count_outer_edges(face)
        _, _, r_min, r_max = _xy_radius_stats(pts)

        outer_d = 2.0 * r_max
        # Star ratio: torx inner/outer ≈ 0.71. Hex equals 1.0 if measured on
        # the inscribed (flat) circle vs circumscribed (point) circle the
        # ratio is cos(30°) ≈ 0.866. Phillips cross has inner≈0 (lobes go to
        # centre) so ratio is small.
        ratio = (r_min / r_max) if r_max > 1e-9 else 0.0

        catalog = _load("standards", "drivers") or {}

        # Heuristic classification.
        # 1) Exactly 6 straight edges → regular hex.
        # 2) 12 straight edges (Torx star is approximated with 12 straight
        #    segments in many CAD constructions) AND ratio between 0.55..0.85
        #    → torx.
        # 3) Otherwise, if there are >= 8 edges and ratio is small (< 0.4)
        #    AND many of those edges are straight → phillips cross.
        # 4) Fallback uses ratio alone.
        recess_kind: str
        if n_straight == 6 and n_total == 6:
            recess_kind = "hex_socket"
        elif n_straight >= 10 and 0.55 <= ratio <= 0.85:
            recess_kind = "torx"
        elif n_straight >= 8 and ratio < 0.45:
            recess_kind = "phillips"
        elif n_total == 6:
            # OCCT may have merged colinear edges — still 6-sided shape.
            recess_kind = "hex_socket"
        elif 0.55 <= ratio <= 0.85:
            recess_kind = "torx"
        elif ratio < 0.45:
            recess_kind = "phillips"
        else:
            # Default to hex if reasonably round-ish.
            recess_kind = "hex_socket"

        size = ""
        confidence = 0.0
        across_flats_mm: float | None = None
        if recess_kind == "hex_socket":
            table = catalog.get("hex_socket", {})
            # For a regular hexagon all 6 vertices sit on the circumscribed
            # circle, so r_min ≈ r_max ≈ R (the point-to-point radius). The
            # AF (flat-to-flat) distance equals R * sqrt(3) = 2 * R * cos(30°).
            af_estimate = 2.0 * r_max * math.cos(math.radians(30.0))
            across_flats_mm = round(af_estimate, 4)
            key, _val, dev = _best_in_table(table, "across_flats_mm", af_estimate)
            size = key
            confidence = round(1.0 / (1.0 + dev), 4)
        elif recess_kind == "torx":
            table = catalog.get("torx", {})
            key, _val, dev = _best_in_table(table, "major_diameter_mm", outer_d)
            size = key
            confidence = round(1.0 / (1.0 + dev), 4)
        else:  # phillips
            table = catalog.get("phillips", {})
            key, _val, dev = _best_in_table(table, "major_diameter_mm", outer_d)
            size = key
            confidence = round(1.0 / (1.0 + dev), 4)

        match = {
            "recess_kind": recess_kind,
            "size": size,
            "confidence": confidence,
            "outer_diameter_mm": round(outer_d, 4),
            "across_flats_mm": across_flats_mm,
            "edge_count": n_total,
            "straight_edge_count": n_straight,
            "ratio_inner_outer": round(ratio, 4),
        }

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"match": match},
        )
