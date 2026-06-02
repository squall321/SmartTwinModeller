"""plan_from_feature_catalog — atomic, read-only.

Convert a ``feature_catalog`` (produced by ``extract_feature_catalog``) into
an ordered Plan YAML of build skills.

Heuristic ordering (largest → finest, base shape first):
  1. ``s_base`` — a placeholder ``box`` step sized from the body bbox so the
     plan is self-contained (real reconstruction would refine this).
  2. Pockets (larger top-d first).
  3. Bosses (largest height first).
  4. Lugs.
  5. Ribs.
  6. Holes (largest diameter first, with standard-match aware skill picks).
  7. Circular arrays of holes (count ≥ 6) wrapped into a ``circular_pattern``
     reference.

Step args are populated with positions / diameters / depths extracted from
the catalog. ``face_selector`` fields are left as a generic
``{"kind":"face_named","name":"top"}`` placeholder so downstream LLM /
operator can refine the anchor face.

Plan YAML is written to ``plans/reconstructed_plan.yaml`` and also embedded
into ``extras["generated_plan"]``.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


# ──────────────────────────────────────────────────────────────────────────────
# Inline catalog loader (per pack rules — kept for symmetry with other
# reverse_engineer skills even though plan generation itself does not load
# any standards catalog directly).


def _load(family, name):
    import yaml, pathlib
    root = pathlib.Path(__file__).resolve().parents[4]
    path = root / "catalogs" / family / f"{name}.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text())


# ──────────────────────────────────────────────────────────────────────────────
# Step builders


_DEFAULT_FACE_SELECTOR: dict[str, Any] = {"kind": "face_named", "name": "top"}
_BOTTOM_FACE_SELECTOR: dict[str, Any] = {"kind": "face_named", "name": "bottom"}


def _new_step(id_: str, skill_name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"id": id_, "skill": skill_name, "args": args}


def _body_bbox(body: Any) -> tuple[float, float, float, float, float, float] | None:
    """Compute optimal bbox (xmin,ymin,zmin,xmax,ymax,zmax) for body.

    Returns None if body is unavailable or bbox computation fails.
    """
    if body is None:
        return None
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib

        shape = body.wrapped if hasattr(body, "wrapped") else body
        bb = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, bb)
        return bb.Get()  # (xmin, ymin, zmin, xmax, ymax, zmax)
    except Exception:
        return None


def _pick_face_selector(
    axis_origin: Any,
    axis_dir: Any,
    bbox: tuple[float, float, float, float, float, float] | None,
) -> dict[str, Any]:
    """Choose top vs bottom face selector based on feature axis & body bbox.

    Top-face features have axis_origin.z near zmax (or axis pointing -Z from
    above). Bottom-face features have axis_origin.z near zmin (or axis +Z
    from below). Falls back to top when ambiguous or bbox unavailable.
    """
    if bbox is None:
        return _DEFAULT_FACE_SELECTOR
    try:
        # Primary signal: axis_dir points outward through the OPEN face of
        # the hole/pocket (extract_feature_catalog convention). +Z ⇒ open
        # face is the body's top; -Z ⇒ bottom.
        if axis_dir is not None:
            try:
                az = float(axis_dir[2])
                if abs(az) > 0.5:
                    return (
                        _DEFAULT_FACE_SELECTOR if az > 0
                        else _BOTTOM_FACE_SELECTOR
                    )
            except Exception:
                pass
        # Fallback: distance from axis_origin.z to the nearer Z face.
        if axis_origin is not None:
            z = float(axis_origin[2])
            zmin, zmax = float(bbox[2]), float(bbox[5])
            if abs(z - zmin) < abs(z - zmax):
                return _BOTTOM_FACE_SELECTOR
        return _DEFAULT_FACE_SELECTOR
    except Exception:
        return _DEFAULT_FACE_SELECTOR


def _hole_step(
    idx: int,
    hole: dict,
    std_match: dict | None,
    bbox: tuple[float, float, float, float, float, float] | None = None,
) -> dict:
    """Pick the most specific hole skill for this hole descriptor.

    Decision tree:
      - "counterbore"  → counterbore_hole + thread_spec
      - "countersink"  → countersink_hole + thread_spec
      - "threaded"     → tap_drill_hole + thread_spec
      - with standard_match → clearance_hole + thread_spec
      - fallback       → hole (raw diameter / depth / direction)
    """
    htype = hole.get("type", "simple")
    diams = hole.get("diameters_mm") or []
    primary_d = float(min(diams)) if diams else 3.4
    depth = float(hole.get("depth_mm") or 5.0)
    axis_dir = hole.get("axis_dir") or [0.0, 0.0, -1.0]
    axis_origin = hole.get("axis_origin") or [0.0, 0.0, 0.0]
    face_sel = _pick_face_selector(axis_origin, axis_dir, bbox)

    # ── thread spec source: prefer the hole's own standard_match (which
    #    classify_holes attached), fall back to the per-hole standard match
    #    pulled from extract_feature_catalog.standard_matches. Both sources
    #    are gated by a confidence floor (0.6) — below that we treat the
    #    match as noise and fall back to the raw geometric hole, avoiding
    #    bogus thread specs from coarse one-mm-slop matches.
    _STD_MATCH_MIN_CONF = 0.6
    thread_spec: str | None = None
    hole_sm = hole.get("standard_match")
    if isinstance(hole_sm, dict):
        conf = float(hole_sm.get("confidence") or 0.0)
        if conf >= _STD_MATCH_MIN_CONF:
            thread_spec = hole_sm.get("thread_spec")
    if thread_spec is None and isinstance(std_match, dict):
        bm = std_match.get("best_match") or {}
        if bm:
            conf = float(bm.get("confidence") or 0.0)
            if conf >= _STD_MATCH_MIN_CONF:
                thread_spec = bm.get("thread_spec")

    sid = f"s_hole_{idx}"

    if htype == "counterbore" and thread_spec:
        return _new_step(sid, "counterbore_hole", {
            "face_selector": face_sel,
            "position_xy": [float(axis_origin[0]), float(axis_origin[1])],
            "thread_spec": thread_spec,
            "fit": "medium",
            "depth_mm": depth,
        })
    if htype == "countersink" and thread_spec:
        return _new_step(sid, "countersink_hole", {
            "face_selector": face_sel,
            "position_xy": [float(axis_origin[0]), float(axis_origin[1])],
            "thread_spec": thread_spec,
            "fit": "medium",
            "depth_mm": depth,
        })
    if htype == "threaded" and thread_spec:
        return _new_step(sid, "tap_drill_hole", {
            "face_selector": face_sel,
            "position_xy": [float(axis_origin[0]), float(axis_origin[1])],
            "thread_spec": thread_spec,
            "depth_mm": depth,
        })
    if thread_spec:
        return _new_step(sid, "clearance_hole", {
            "face_selector": face_sel,
            "position_xy": [float(axis_origin[0]), float(axis_origin[1])],
            "thread_spec": thread_spec,
            "fit": "medium",
            "depth_mm": depth,
        })

    # Direction inferred from dominant axis component.
    dir_str = _axis_dir_to_str(axis_dir)
    return _new_step(sid, "hole", {
        "position": [
            float(axis_origin[0]), float(axis_origin[1]), float(axis_origin[2]),
        ],
        "diameter_mm": primary_d,
        "depth_mm": depth,
        "direction": dir_str,
    })


def _axis_dir_to_str(axis_dir) -> str:
    ax, ay, az = (float(axis_dir[0]), float(axis_dir[1]), float(axis_dir[2]))
    components = (("X", ax), ("Y", ay), ("Z", az))
    dom = max(components, key=lambda c: abs(c[1]))
    return f"{'+' if dom[1] >= 0 else '-'}{dom[0]}"


def _pocket_step(
    idx: int,
    pocket: dict,
    bbox: tuple[float, float, float, float, float, float] | None = None,
) -> dict:
    ptype = pocket.get("type", "blind")
    top_d = float(pocket.get("top_d_mm") or 0.0)
    depth = float(pocket.get("depth_mm") or 1.0)
    origin = pocket.get("axis_origin") or [0.0, 0.0, 0.0]
    axis_dir = pocket.get("axis_dir") or [0.0, 0.0, -1.0]
    face_sel = _pick_face_selector(origin, axis_dir, bbox)
    sid = f"s_pocket_{idx}"

    # Circular pockets whose depth dominates → treat as a raw hole.
    if top_d > 0 and depth / max(top_d, 1e-3) >= 1.5:
        return _new_step(sid, "hole", {
            "position": [float(origin[0]), float(origin[1]), float(origin[2])],
            "diameter_mm": top_d,
            "depth_mm": depth,
            "direction": _axis_dir_to_str(axis_dir),
        })

    # Default — extrude_pocket with placeholder rectangular sketch sized to
    # the measured top diameter.
    return _new_step(sid, "extrude_pocket", {
        "face_selector": face_sel,
        "sketch": {
            "kind": "rect",
            "width_mm": top_d if top_d > 0 else 5.0,
            "height_mm": top_d if top_d > 0 else 5.0,
            "position_xy": [float(origin[0]), float(origin[1])],
        },
        "depth_mm": depth,
    })


def _boss_step(
    idx: int,
    boss: dict,
    bbox: tuple[float, float, float, float, float, float] | None = None,
) -> dict:
    btype = boss.get("type", "prismatic")
    center = boss.get("center") or [0.0, 0.0, 0.0]
    height = float(boss.get("height_mm") or 1.0)
    size = float(boss.get("diameter_or_size_mm") or 4.0)
    # Bosses grow upward from their anchor face; choose the closer body face
    # based on the boss centre Z.
    face_sel = _pick_face_selector(center, [0.0, 0.0, 1.0], bbox)
    sid = f"s_boss_{idx}"

    if btype == "cylindrical":
        # If a hole is implied (e.g. seat boss) use boss_with_hole, else
        # mounting_pad. We default to mounting_pad without a hole.
        return _new_step(sid, "mounting_pad", {
            "face_selector": face_sel,
            "position_xy": [float(center[0]), float(center[1])],
            "diameter_mm": size,
            "height_mm": height,
        })

    # Prismatic / conical fallback — mounting_pad with the measured size.
    return _new_step(sid, "mounting_pad", {
        "face_selector": face_sel,
        "position_xy": [float(center[0]), float(center[1])],
        "diameter_mm": size,
        "height_mm": height,
    })


def _rib_step(idx: int, rib: dict) -> dict:
    sid = f"s_rib_{idx}"
    length = float(rib.get("length_mm") or 10.0)
    thickness = float(rib.get("thickness_mm") or 1.0)
    height = float(rib.get("height_mm") or 3.0)
    # Rib needs start/end + width/height/up_axis; without knowing the cluster
    # axis we anchor a placeholder centred on origin along +X.
    return _new_step(sid, "rib", {
        "start": [-length / 2.0, 0.0, 0.0],
        "end": [length / 2.0, 0.0, 0.0],
        "width_mm": thickness,
        "height_mm": height,
        "up_axis": "+Z",
    })


def _lug_step(idx: int, lug: dict) -> dict:
    sid = f"s_lug_{idx}"
    axis = lug.get("axis") or [1.0, 0.0, 0.0]
    sep = float(lug.get("separation_mm") or 10.0)
    # Anchor centre of pair at origin, oriented along the pair axis.
    return _new_step(sid, "lug_pair", {
        "center": [0.0, 0.0, 0.0],
        "axis": [float(axis[0]), float(axis[1]), float(axis[2])],
        "separation_mm": sep,
        "boss_diameter_mm": 6.0,
        "hole_diameter_mm": 2.5,
        "height_mm": 4.0,
    })


def _sweep_boss_step(idx: int, feat: dict) -> dict:
    """Emit a ``swept_boss_along_curve`` step from a sweep_features entry."""
    sid = f"s_sweep_boss_{idx}"
    profile_d = float(feat.get("profile_diameter_mm") or 2.0)
    path_points = feat.get("path_points") or []
    return _new_step(sid, "swept_boss_along_curve", {
        "face_selector": _DEFAULT_FACE_SELECTOR,
        "profile_sketch": {"kind": "circle", "diameter_mm": profile_d},
        "path_points": [
            [float(p[0]), float(p[1]), float(p[2])]
            for p in path_points
        ],
        "path_type": "polyline",
    })


def _sweep_pocket_step(idx: int, feat: dict) -> dict:
    """Emit a ``swept_pocket_along_curve`` step from a sweep_features entry."""
    sid = f"s_sweep_pocket_{idx}"
    profile_d = float(feat.get("profile_diameter_mm") or 2.0)
    path_points = feat.get("path_points") or []
    return _new_step(sid, "swept_pocket_along_curve", {
        "face_selector": _DEFAULT_FACE_SELECTOR,
        "profile_sketch": {"kind": "circle", "diameter_mm": profile_d},
        "path_points": [
            [float(p[0]), float(p[1]), float(p[2])]
            for p in path_points
        ],
        "path_type": "polyline",
    })


def _loft_boss_step(idx: int, feat: dict) -> dict:
    """Emit a ``loft_boss_between_sketches`` step from a loft_features entry."""
    sid = f"s_loft_boss_{idx}"
    lower_d = float(feat.get("lower_diameter_mm") or 6.0)
    upper_d = float(feat.get("upper_diameter_mm") or 4.0)
    height = float(feat.get("height_mm") or 4.0)
    cx, cy = feat.get("center_xy") or [0.0, 0.0]
    return _new_step(sid, "loft_boss_between_sketches", {
        "face_selector": _DEFAULT_FACE_SELECTOR,
        "lower_sketch": {
            "kind": "circle",
            "diameter_mm": lower_d,
            "center_x_mm": float(cx),
            "center_y_mm": float(cy),
        },
        "upper_sketch": {
            "kind": "circle",
            "diameter_mm": upper_d,
            "center_x_mm": float(cx),
            "center_y_mm": float(cy),
        },
        "height_mm": height,
    })


def _loft_pocket_step(idx: int, feat: dict) -> dict:
    """Emit a ``loft_pocket_between_sketches`` step from a loft_features entry."""
    sid = f"s_loft_pocket_{idx}"
    upper_d = float(feat.get("upper_diameter_mm") or 6.0)
    lower_d = float(feat.get("lower_diameter_mm") or 4.0)
    depth = float(feat.get("height_mm") or 4.0)
    cx, cy = feat.get("center_xy") or [0.0, 0.0]
    return _new_step(sid, "loft_pocket_between_sketches", {
        "face_selector": _DEFAULT_FACE_SELECTOR,
        "upper_sketch": {
            "kind": "circle",
            "diameter_mm": upper_d,
            "center_x_mm": float(cx),
            "center_y_mm": float(cy),
        },
        "lower_sketch": {
            "kind": "circle",
            "diameter_mm": lower_d,
            "center_x_mm": float(cx),
            "center_y_mm": float(cy),
        },
        "depth_mm": depth,
    })


# ──────────────────────────────────────────────────────────────────────────────
# Specialized handlers — text / magnet / bearing / o-ring / swept relief
#
# Each handler returns either a step dict (when it matches with confidence
# >= 0.6) or None (caller falls back to the generic emission path). Catalog
# lookups are best-effort: a missing catalog yields None so plan generation
# stays robust in CI sandboxes that strip data files.

_SPECIALIZED_MIN_CONF = 0.6


def _text_step(
    idx: int,
    feat: dict,
    bbox: tuple[float, float, float, float, float, float] | None = None,
) -> dict | None:
    """Emit a text_engrave / text_emboss step from a text-feature entry.

    Expected feature shape (defensive — the detector is not yet implemented,
    but we accept any future schema that follows the convention)::

        {
          "kind": "engrave" | "emboss" | "text_engrave" | "text_emboss",
          "text": str,
          "font_name": str,            # optional, default "Arial"
          "font_size_mm": float,        # optional, default 5.0
          "depth_mm" / "height_mm": float,
          "center_xy" or "center_x_mm"+"center_y_mm": ...,
          "rotation_deg": float,
          "axis_origin": [x,y,z],       # for face_selector inference
          "axis_dir":    [x,y,z],
          "confidence": float,
        }
    """
    conf = float(feat.get("confidence") or 1.0)
    if conf < _SPECIALIZED_MIN_CONF:
        return None
    text = feat.get("text")
    if not text or not isinstance(text, str) or not text.strip():
        return None

    kind = str(feat.get("kind") or "engrave").lower()
    is_emboss = "emboss" in kind
    skill_name = "text_emboss" if is_emboss else "text_engrave"

    origin = feat.get("axis_origin") or [0.0, 0.0, 0.0]
    axis_dir = feat.get("axis_dir") or [0.0, 0.0, 1.0]
    face_sel = _pick_face_selector(origin, axis_dir, bbox)

    cxy = feat.get("center_xy")
    if cxy is not None:
        cx_mm, cy_mm = float(cxy[0]), float(cxy[1])
    else:
        cx_mm = float(feat.get("center_x_mm") or 0.0)
        cy_mm = float(feat.get("center_y_mm") or 0.0)

    args: dict[str, Any] = {
        "face_selector": face_sel,
        "text": text,
        "font_name": str(feat.get("font_name") or "Arial"),
        "font_size_mm": float(feat.get("font_size_mm") or 5.0),
        "center_x_mm": cx_mm,
        "center_y_mm": cy_mm,
        "rotation_deg": float(feat.get("rotation_deg") or 0.0),
        "bold": bool(feat.get("bold") or False),
        "italic": bool(feat.get("italic") or False),
    }
    if is_emboss:
        args["height_mm"] = float(
            feat.get("height_mm") or feat.get("depth_mm") or 0.3
        )
    else:
        args["depth_mm"] = float(
            feat.get("depth_mm") or feat.get("height_mm") or 0.3
        )

    sid = f"s_text_{idx}"
    return _new_step(sid, skill_name, args)


def _match_magnet_pocket(pocket: dict) -> tuple[str, float] | None:
    """If ``pocket`` looks like an axial NdFeB disc magnet recess, return
    ``(magnet_spec, confidence)``; else None.

    Match rule: top_d ≈ magnet_d ± 0.15 mm AND depth ≈ magnet_t ± 0.10 mm.
    Confidence = 1 / (1 + diameter_dev + 2 * depth_dev).
    """
    top_d = float(pocket.get("top_d_mm") or 0.0)
    depth = float(pocket.get("depth_mm") or 0.0)
    if top_d <= 0.0 or depth <= 0.0:
        return None
    catalog = _load("magnets", "ndfeb")
    if catalog is None:
        return None
    best_spec: str | None = None
    best_conf = 0.0
    for spec, entry in (catalog.get("magnets") or {}).items():
        md = float(entry.get("magnet_d_mm") or 0.0)
        mt = float(entry.get("magnet_t_mm") or 0.0)
        if md <= 0.0 or mt <= 0.0:
            continue
        dd = abs(top_d - md)
        dt = abs(depth - mt)
        if dd > 0.20 or dt > 0.20:
            continue
        conf = 1.0 / (1.0 + dd + 2.0 * dt)
        if conf > best_conf:
            best_conf = conf
            best_spec = spec
    if best_spec is None or best_conf < _SPECIALIZED_MIN_CONF:
        return None
    return (best_spec, best_conf)


def _magnet_pocket_step(
    idx: int,
    pocket: dict,
    magnet_spec: str,
    bbox: tuple[float, float, float, float, float, float] | None = None,
) -> dict:
    origin = pocket.get("axis_origin") or [0.0, 0.0, 0.0]
    axis_dir = pocket.get("axis_dir") or [0.0, 0.0, -1.0]
    face_sel = _pick_face_selector(origin, axis_dir, bbox)
    sid = f"s_magnet_pocket_{idx}"
    return _new_step(sid, "magnet_pocket_axial", {
        "face_selector": face_sel,
        "position_xy": [float(origin[0]), float(origin[1])],
        "magnet_spec": magnet_spec,
        "retention": "glue",
    })


def _match_bearing_bore(pocket: dict) -> tuple[str, float] | None:
    """If ``pocket`` looks like a deep-groove ball bearing seat, return
    ``(bearing_spec, confidence)``; else None.

    Match rule: top_d ≈ outer_d ± 0.15 mm AND depth ≈ width ± 0.50 mm
    (depth tolerance is wider — many designs press a bearing into a
    through-bore deeper than the bearing width).
    """
    top_d = float(pocket.get("top_d_mm") or 0.0)
    depth = float(pocket.get("depth_mm") or 0.0)
    if top_d <= 0.0 or depth <= 0.0:
        return None
    catalog = _load("standards", "bearings_metric")
    if catalog is None:
        return None
    best_spec: str | None = None
    best_conf = 0.0
    for spec, entry in (catalog.get("bearings") or {}).items():
        od = float(entry.get("outer_d_mm") or 0.0)
        w = float(entry.get("width_mm") or 0.0)
        if od <= 0.0 or w <= 0.0:
            continue
        dd = abs(top_d - od)
        dw = abs(depth - w)
        if dd > 0.20:
            continue
        # depth gates: very loose because bores can be deeper than width.
        if dw > 1.0 and depth < w:
            continue
        conf = 1.0 / (1.0 + dd + 0.25 * dw)
        if conf > best_conf:
            best_conf = conf
            best_spec = str(spec)
    if best_spec is None or best_conf < _SPECIALIZED_MIN_CONF:
        return None
    return (best_spec, best_conf)


def _bearing_bore_step(
    idx: int,
    pocket: dict,
    bearing_spec: str,
) -> dict:
    origin = pocket.get("axis_origin") or [0.0, 0.0, 0.0]
    axis_dir = pocket.get("axis_dir") or [0.0, 0.0, -1.0]
    sid = f"s_bearing_bore_{idx}"
    return _new_step(sid, "bearing_bore", {
        "axis_origin": [
            float(origin[0]), float(origin[1]), float(origin[2]),
        ],
        "axis_direction": [
            float(axis_dir[0]), float(axis_dir[1]), float(axis_dir[2]),
        ],
        "bearing_spec": bearing_spec,
        "with_shoulder": False,
    })


def _match_o_ring_groove(revolve_feat: dict) -> tuple[float, float, float] | None:
    """If a revolve_feature looks like an annular o-ring groove, return
    ``(outer_d_mm, inner_d_mm, depth_mm)``; else None.

    The detector emits revolve_features with only a bbox + axis. We treat the
    bbox XY extent as the outer diameter and the Z extent as the groove
    depth. For a true ring we need a non-degenerate annular cross-section
    (some bbox XY extent), and a shallow depth (<= 5 mm) consistent with a
    typical AS568 cs.
    """
    bb = revolve_feat.get("bbox")
    if not bb or len(bb) < 6:
        return None
    try:
        xmin, ymin, zmin, xmax, ymax, zmax = (float(c) for c in bb)
    except Exception:
        return None
    outer = max(xmax - xmin, ymax - ymin)
    depth = zmax - zmin
    if outer <= 0.5 or depth <= 0.1 or depth > 5.0:
        return None
    # Approximate inner d as outer d minus 2*depth*1.2 (groove width slightly
    # wider than depth). This is a coarse proxy — the o_ring_groove skill
    # only uses outer/inner/depth directly.
    inner = max(outer - 2.0 * depth * 1.5, 0.5)
    if inner >= outer:
        return None
    return (outer, inner, depth)


def _o_ring_groove_step(
    idx: int,
    revolve_feat: dict,
    outer_d: float,
    inner_d: float,
    depth: float,
    bbox: tuple[float, float, float, float, float, float] | None = None,
) -> dict:
    axis_origin = revolve_feat.get("axis_origin") or [0.0, 0.0, 0.0]
    axis_dir = revolve_feat.get("axis_direction") or [0.0, 0.0, 1.0]
    face_sel = _pick_face_selector(axis_origin, axis_dir, bbox)
    sid = f"s_oring_groove_{idx}"
    return _new_step(sid, "o_ring_groove", {
        "face_selector": face_sel,
        "outer_diameter_mm": float(outer_d),
        "inner_diameter_mm": float(inner_d),
        "depth_mm": float(depth),
        "center_x_mm": float(axis_origin[0]),
        "center_y_mm": float(axis_origin[1]),
    })


def _try_swept_relief(feat: dict) -> dict | None:
    """If a swept pocket has a straight XY-plane path, emit a swept_relief
    step instead of swept_pocket_along_curve. Returns the step dict or None.

    Conditions:
      - exactly two path points (straight segment)
      - both endpoints at the same Z (planar XY path) — swept_relief v1 only
        supports XY-plane paths
      - profile_diameter_mm available → mapped to width_mm/depth_mm
    """
    path_points = feat.get("path_points") or []
    if len(path_points) != 2:
        return None
    p0 = path_points[0]
    p1 = path_points[1]
    try:
        z0, z1 = float(p0[2]), float(p1[2])
    except Exception:
        return None
    if abs(z1 - z0) > 0.01:
        return None
    profile_d = float(feat.get("profile_diameter_mm") or 2.0)
    # confidence proxy from segment length / profile aspect — short stubs are
    # likely detector noise.
    import math as _math
    seg = _math.sqrt(
        (float(p1[0]) - float(p0[0])) ** 2
        + (float(p1[1]) - float(p0[1])) ** 2
    )
    if seg < max(2.0 * profile_d, 1.0):
        return None
    return {
        "start": [float(p0[0]), float(p0[1]), float(p0[2])],
        "end": [float(p1[0]), float(p1[1]), float(p1[2])],
        "width_mm": float(profile_d),
        "depth_mm": float(profile_d),
    }


def _swept_relief_step(idx: int, payload: dict) -> dict:
    sid = f"s_swept_relief_{idx}"
    return _new_step(sid, "swept_relief", payload)


def _revolve_pocket_step(idx: int, feat: dict) -> dict:
    """Emit a ``revolve_pocket`` step from a revolve_features entry."""
    sid = f"s_revolve_pocket_{idx}"
    axis_origin = feat.get("axis_origin") or [0.0, 0.0, 0.0]
    axis_dir = feat.get("axis_direction") or [0.0, 0.0, 1.0]
    angle = float(feat.get("angle_deg") or 360.0)
    return _new_step(sid, "revolve_pocket", {
        "profile_sketch": {
            "kind": "rectangle",
            "length_mm": 1.0,
            "width_mm": 1.0,
            "center_x_mm": 4.0,
        },
        "axis_origin": [
            float(axis_origin[0]), float(axis_origin[1]), float(axis_origin[2]),
        ],
        "axis_direction": [
            float(axis_dir[0]), float(axis_dir[1]), float(axis_dir[2]),
        ],
        "angle_deg": angle,
    })


def _bbox_overlap_with_xy(
    bbox_a, center_xy, radius: float = 1.0,
) -> bool:
    """True if the XY projection of `bbox_a` contains the point `center_xy`
    expanded by `radius`. Used to filter spurious bosses whose footprint sits
    inside an already-captured sweep/loft feature."""
    if bbox_a is None or center_xy is None:
        return False
    try:
        xmin, ymin = float(bbox_a[0]), float(bbox_a[1])
        xmax, ymax = float(bbox_a[3]), float(bbox_a[4])
        cx, cy = float(center_xy[0]), float(center_xy[1])
        r = float(radius)
        return (xmin - r) <= cx <= (xmax + r) and (ymin - r) <= cy <= (ymax + r)
    except Exception:
        return False


def _pocket_is_axis_aligned(pocket: dict) -> bool:
    """True iff the pocket axis snaps to ±X / ±Y / ±Z within ~10°.

    Pockets whose axis is diagonal (e.g. (0.667, 0.667, 0.333)) are typically
    detector artefacts from chains of unrelated cylindrical faces and should
    be skipped rather than emitted as a (huge) extrude_pocket step.
    """
    axis = pocket.get("axis_dir") or [0.0, 0.0, 1.0]
    try:
        ax, ay, az = (
            abs(float(axis[0])), abs(float(axis[1])), abs(float(axis[2])),
        )
    except Exception:
        return False
    # Require one dominant component ≥ 0.95 (cos 18°).
    return max(ax, ay, az) >= 0.95


def _circular_pattern_step(
    idx: int,
    ring: dict,
    profile_diameter_mm: float,
    feature_depth_mm: float,
    bbox: tuple[float, float, float, float, float, float] | None = None,
) -> dict:
    """Wrap a circular-array of holes into a ``circular_pattern`` step.

    Args match the registered ``circular_pattern`` skill: face_selector,
    profile_diameter_mm, operation, feature_depth_mm, count, pitch_radius_mm,
    center_x_mm, center_y_mm, start_angle_deg, total_sweep_deg.
    """
    sid = f"s_pattern_circ_{idx}"
    center = ring.get("center") or [0.0, 0.0, 0.0]
    axis = ring.get("axis") or [0.0, 0.0, 1.0]
    count = int(ring.get("count") or 6)
    radius = float(ring.get("radius_mm") or 5.0)
    face_sel = _pick_face_selector(center, axis, bbox)
    return _new_step(sid, "circular_pattern", {
        "face_selector": face_sel,
        "profile_diameter_mm": float(profile_diameter_mm),
        "operation": "hole",
        "feature_depth_mm": float(feature_depth_mm),
        "count": count,
        "pitch_radius_mm": radius,
        "center_x_mm": float(center[0]),
        "center_y_mm": float(center[1]),
        "start_angle_deg": 0.0,
        "total_sweep_deg": 360.0,
    })


def _linear_pattern_step(
    idx: int,
    run: dict,
    profile_diameter_mm: float,
    feature_depth_mm: float,
    bbox: tuple[float, float, float, float, float, float] | None = None,
) -> dict | None:
    """Wrap a linear-array of holes into a ``linear_pattern`` step.

    The registered ``linear_pattern`` skill supports ``direction in {"X","Y"}``
    only. If the array direction is not axis-aligned along X or Y, return
    None so the caller falls back to per-hole emission.
    """
    direction_vec = run.get("direction") or [1.0, 0.0, 0.0]
    positions = run.get("positions") or []
    count = int(run.get("count") or len(positions))
    spacing = float(run.get("spacing_mm") or 0.0)
    if count < 2 or spacing <= 0.0 or not positions:
        return None

    dx, dy = abs(float(direction_vec[0])), abs(float(direction_vec[1]))
    dz = abs(float(direction_vec[2]))
    if dz > 0.5:
        # Vertical line of holes — linear_pattern only supports X / Y.
        return None
    if dx >= dy and dx > 0.5:
        axis_letter: str = "X"
    elif dy > dx and dy > 0.5:
        axis_letter = "Y"
    else:
        return None

    # Anchor offset: first position's XY relative to body center (origin).
    first = positions[0]
    start_x = float(first[0])
    start_y = float(first[1])
    # The seed feature is at axis_origin of the first; the seed's Z is
    # represented by the face_selector. We borrow the first point's axis-dir
    # heuristic via Z dominance: holes typically have axis_dir ≈ ±Z, so
    # default to top face when z is near zmax.
    first_origin = list(first)
    face_sel = _pick_face_selector(first_origin, [0.0, 0.0, -1.0], bbox)

    sid = f"s_pattern_lin_{idx}"
    return _new_step(sid, "linear_pattern", {
        "face_selector": face_sel,
        "profile_diameter_mm": float(profile_diameter_mm),
        "operation": "hole",
        "feature_depth_mm": float(feature_depth_mm),
        "count": count,
        "spacing_mm": spacing,
        "direction": axis_letter,
        "start_offset_x_mm": start_x,
        "start_offset_y_mm": start_y,
    })


def _hole_xy_in_ring(
    hole: dict, ring: dict, radius_tol: float = 0.5
) -> bool:
    """True iff the hole's axis_origin lies on the ring's circle."""
    origin = hole.get("axis_origin") or [0.0, 0.0, 0.0]
    center = ring.get("center") or [0.0, 0.0, 0.0]
    radius = float(ring.get("radius_mm") or 0.0)
    if radius <= 0.0:
        return False
    dx = float(origin[0]) - float(center[0])
    dy = float(origin[1]) - float(center[1])
    dz = float(origin[2]) - float(center[2])
    # Distance projected onto the ring plane — approximate as raw 3D distance
    # for axis-aligned (Z) rings which is the dominant case.
    import math as _math
    d = _math.sqrt(dx * dx + dy * dy + dz * dz)
    return abs(d - radius) <= radius_tol


def _hole_xy_on_line(
    hole: dict, run: dict, pos_tol: float = 0.5
) -> bool:
    """True iff the hole's axis_origin matches any of the run positions."""
    origin = hole.get("axis_origin") or [0.0, 0.0, 0.0]
    positions = run.get("positions") or []
    ox, oy, oz = float(origin[0]), float(origin[1]), float(origin[2])
    for p in positions:
        if (
            abs(float(p[0]) - ox) <= pos_tol
            and abs(float(p[1]) - oy) <= pos_tol
            and abs(float(p[2]) - oz) <= pos_tol
        ):
            return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Plan builder


def _build_plan(catalog: dict, body: Any = None) -> dict:
    holes = catalog.get("holes") or []
    pockets = catalog.get("pockets") or []
    bosses = catalog.get("bosses") or []
    ribs = catalog.get("ribs") or []
    lugs = catalog.get("lugs") or []
    patterns = catalog.get("patterns") or []
    sweep_features = catalog.get("sweep_features") or []
    loft_features = catalog.get("loft_features") or []
    revolve_features = catalog.get("revolve_features") or []
    # ``text_features`` is forward-compatible: extract_feature_catalog does
    # not currently emit it, but if a future detector adds engraved/embossed
    # text entries we map them here. Accept either ``text_features`` or
    # ``text_marks`` as the catalog key.
    text_features = (
        catalog.get("text_features")
        or catalog.get("text_marks")
        or []
    )
    base_thickness = catalog.get("base_thickness_mm")
    std_matches_by_hole = {
        sm.get("hole_id"): sm
        for sm in (catalog.get("standard_matches") or [])
        if isinstance(sm, dict)
    }

    # Body bbox → real base-box dimensions (L, W, H). Without a body we fall
    # back to a sensible placeholder so the plan stays self-contained.
    bbox = _body_bbox(body)
    if bbox is not None:
        xmin, ymin, zmin, xmax, ymax, zmax = bbox
        base_l = max(float(xmax - xmin), 1e-3)
        base_w = max(float(ymax - ymin), 1e-3)
        bbox_h = max(float(zmax - zmin), 1e-3)
        # Prefer the slab thickness measured from parallel planar faces — it
        # excludes the boss/sweep/loft height that bbox would otherwise add.
        if (
            base_thickness is not None
            and 0.0 < float(base_thickness) <= bbox_h
        ):
            base_h = float(base_thickness)
        else:
            base_h = bbox_h
    else:
        base_l, base_w, base_h = 50.0, 50.0, 10.0

    steps: list[dict] = []

    # 1. base shape placeholder ────────────────────────────────────────────
    steps.append(_new_step("s_base", "box", {
        "length_mm": base_l,
        "width_mm": base_w,
        "height_mm": base_h,
    }))

    # 2. Pockets, largest top_d first ──────────────────────────────────────
    #    Skip pockets whose axis is diagonal — those are detector artefacts
    #    (chains of unrelated cylindrical/planar faces grouped by adjacency)
    #    and emit a huge spurious extrude_pocket that inflates volume drift.
    #
    #    Before emitting the generic extrude_pocket / hole step we try the
    #    specialized matchers (magnet disc → magnet_pocket_axial, bearing
    #    seat → bearing_bore). Both gated by confidence >= 0.6 so noisy
    #    pockets fall through to the generic path.
    pockets_sorted = sorted(
        [p for p in pockets if _pocket_is_axis_aligned(p)],
        key=lambda p: -float(p.get("top_d_mm") or 0.0),
    )
    magnet_idx = 0
    bearing_idx = 0
    for i, p in enumerate(pockets_sorted):
        bearing_match = _match_bearing_bore(p)
        if bearing_match is not None:
            spec, _conf = bearing_match
            steps.append(_bearing_bore_step(bearing_idx, p, spec))
            bearing_idx += 1
            continue
        magnet_match = _match_magnet_pocket(p)
        if magnet_match is not None:
            spec, _conf = magnet_match
            steps.append(_magnet_pocket_step(magnet_idx, p, spec, bbox=bbox))
            magnet_idx += 1
            continue
        steps.append(_pocket_step(i, p, bbox=bbox))

    # 2b. Sweep / loft / revolve features — emitted BEFORE the per-boss loop
    #     so we can suppress overlapping detect_bosses entries (the swept and
    #     lofted bodies leave behind boss-like clusters that we don't want
    #     emitted twice).
    sweep_xy_envelopes: list[tuple] = []  # list of bboxes for overlap test
    swept_relief_idx = 0
    for i, feat in enumerate(sweep_features):
        if feat.get("kind") == "pocket":
            # Prefer swept_relief for straight, XY-plane 2-point pocket paths
            # — that's exactly the rectangular cross-section cutout the
            # swept_relief skill models. Falls back to the generic
            # swept_pocket_along_curve when the path is curved or off-plane.
            relief_payload = _try_swept_relief(feat)
            if relief_payload is not None:
                steps.append(_swept_relief_step(swept_relief_idx, relief_payload))
                swept_relief_idx += 1
            else:
                steps.append(_sweep_pocket_step(i, feat))
        else:
            steps.append(_sweep_boss_step(i, feat))
        if feat.get("bbox") is not None:
            sweep_xy_envelopes.append(tuple(feat["bbox"]))

    loft_xy_centers: list[tuple[tuple[float, float], float]] = []
    for i, feat in enumerate(loft_features):
        if feat.get("kind") == "pocket":
            steps.append(_loft_pocket_step(i, feat))
        else:
            steps.append(_loft_boss_step(i, feat))
        cxy = feat.get("center_xy")
        if cxy:
            radius = max(
                float(feat.get("lower_diameter_mm") or 0.0),
                float(feat.get("upper_diameter_mm") or 0.0),
            ) * 0.5
            loft_xy_centers.append(((float(cxy[0]), float(cxy[1])), radius + 0.5))

    oring_idx = 0
    for i, feat in enumerate(revolve_features):
        oring_dims = _match_o_ring_groove(feat)
        if oring_dims is not None:
            outer, inner, depth = oring_dims
            steps.append(
                _o_ring_groove_step(oring_idx, feat, outer, inner, depth, bbox=bbox)
            )
            oring_idx += 1
        else:
            steps.append(_revolve_pocket_step(i, feat))

    # 3. Bosses, tallest first ─────────────────────────────────────────────
    #    Suppress bosses whose centre footprint falls inside any sweep/loft
    #    feature already emitted above (they describe the same protrusion).
    def _boss_is_duplicate(b: dict) -> bool:
        center = b.get("center") or [0.0, 0.0, 0.0]
        cxy = (float(center[0]), float(center[1]))
        for env in sweep_xy_envelopes:
            if _bbox_overlap_with_xy(env, cxy, radius=1.0):
                return True
        for (lcx, lcy), lr in loft_xy_centers:
            dx = lcx - cxy[0]
            dy = lcy - cxy[1]
            if (dx * dx + dy * dy) <= (lr * lr):
                return True
        return False

    bosses_sorted = sorted(
        [b for b in bosses if not _boss_is_duplicate(b)],
        key=lambda b: -float(b.get("height_mm") or 0.0),
    )
    for i, b in enumerate(bosses_sorted):
        steps.append(_boss_step(i, b, bbox=bbox))

    # 4. Lugs ──────────────────────────────────────────────────────────────
    for i, lg in enumerate(lugs):
        steps.append(_lug_step(i, lg))

    # 5. Ribs ──────────────────────────────────────────────────────────────
    for i, rb in enumerate(ribs):
        steps.append(_rib_step(i, rb))

    # 5b. Text features (engrave / emboss) ────────────────────────────────
    #     Forward-compat: extract_feature_catalog does not yet detect text,
    #     but if a future detector populates ``text_features`` we emit one
    #     text_engrave / text_emboss step per entry (confidence-gated).
    text_idx = 0
    for feat in text_features:
        step = _text_step(text_idx, feat, bbox=bbox)
        if step is not None:
            steps.append(step)
            text_idx += 1

    # 6. Patterns — emit one circular_pattern or linear_pattern step per
    #    detected array of holes, then SUBTRACT the covered holes from the
    #    per-hole emission loop below using geometric matching (since
    #    detect_*_array does not carry hole-id linkage).
    handled_hole_ids: set = set()
    handled_holes_geom: list[dict] = []  # list of hole dicts already covered
    circ_idx = 0
    lin_idx = 0
    for pat in patterns:
        if pat.get("feature_kind") != "hole":
            continue
        count = int(pat.get("count") or 0)
        if count < 2:
            continue
        # Representative seed-hole geometry from the catalog. Prefer a hole
        # matched to this pattern (so the diameter/depth come from a real
        # measurement); fall back to the median hole.
        seed_hole = None
        if pat.get("pattern_kind") == "circular":
            for h in holes:
                if _hole_xy_in_ring(h, pat):
                    seed_hole = h
                    break
        elif pat.get("pattern_kind") == "linear":
            for h in holes:
                if _hole_xy_on_line(h, pat):
                    seed_hole = h
                    break
        if seed_hole is None and holes:
            seed_hole = holes[0]

        if seed_hole is None:
            continue

        diams = seed_hole.get("diameters_mm") or [3.4]
        seed_diam = float(min(diams))
        seed_depth = float(seed_hole.get("depth_mm") or 5.0)

        if pat.get("pattern_kind") == "circular" and count >= 4:
            steps.append(
                _circular_pattern_step(
                    circ_idx, pat, seed_diam, seed_depth, bbox=bbox,
                )
            )
            circ_idx += 1
            # Mark covered holes so the per-hole loop skips them.
            for h in holes:
                if _hole_xy_in_ring(h, pat):
                    hid = h.get("id")
                    if hid is not None:
                        handled_hole_ids.add(hid)
                    handled_holes_geom.append(h)
        elif pat.get("pattern_kind") == "linear" and count >= 3:
            step = _linear_pattern_step(
                lin_idx, pat, seed_diam, seed_depth, bbox=bbox,
            )
            if step is not None:
                steps.append(step)
                lin_idx += 1
                for h in holes:
                    if _hole_xy_on_line(h, pat):
                        hid = h.get("id")
                        if hid is not None:
                            handled_hole_ids.add(hid)
                        handled_holes_geom.append(h)

    # 7. Holes, largest diameter first ─────────────────────────────────────
    holes_sorted = sorted(
        holes,
        key=lambda h: -float(max(h.get("diameters_mm") or [0.0])),
    )
    for i, h in enumerate(holes_sorted):
        hid = h.get("id")
        if hid is not None and hid in handled_hole_ids:
            continue
        # Geometric dedup fallback for holes that lack a stable id.
        if any(h is hh for hh in handled_holes_geom):
            continue
        sm = std_matches_by_hole.get(hid)
        steps.append(_hole_step(i, h, sm, bbox=bbox))

    return {
        "schema_version": 1,
        "plan_name": "reconstructed_plan",
        "steps": steps,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Skill


@skill(
    name="plan_from_feature_catalog",
    category="reverse_engineer",
    level="atomic",
    summary="Convert a feature_catalog (from extract_feature_catalog) into an "
            "ordered Plan YAML of build skills (base box → pockets → bosses → "
            "lugs → ribs → patterns → holes). Writes plans/"
            "reconstructed_plan.yaml and attaches the plan to "
            "extras['generated_plan']. Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["generated_plan"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class PlanFromFeatureCatalog(SkillBase):
    # Module-level cache of the last catalog produced by
    # extract_feature_catalog, used when ``catalog=None``.
    _LAST_CATALOG: dict | None = None

    class Args(BaseModel):
        catalog: dict | None = None

    def _apply(self, body: Any, args: Args) -> SkillResult:
        import pathlib

        import yaml

        catalog = args.catalog
        if catalog is None:
            # Fall back to the previously cached catalog (if any). We also
            # opportunistically run extract_feature_catalog on the current
            # body so the skill is callable as a standalone one-shot.
            if PlanFromFeatureCatalog._LAST_CATALOG is not None:
                catalog = PlanFromFeatureCatalog._LAST_CATALOG
            else:
                from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
                    ExtractFeatureCatalog,
                )
                res = ExtractFeatureCatalog().apply(body, {})
                catalog = res.extras.get("feature_catalog", {})

        plan = _build_plan(catalog or {}, body=body)

        # Cache for chained calls.
        PlanFromFeatureCatalog._LAST_CATALOG = catalog

        # Write the YAML to plans/reconstructed_plan.yaml. The path is
        # resolved relative to the repository root (4 levels up from this
        # file: reverse_engineer → skills → phone_designer → src → repo).
        root = pathlib.Path(__file__).resolve().parents[4]
        plans_dir = root / "plans"
        try:
            plans_dir.mkdir(parents=True, exist_ok=True)
            (plans_dir / "reconstructed_plan.yaml").write_text(
                yaml.safe_dump(plan, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        except Exception:
            # Best-effort: a write failure must not break the skill (e.g.
            # read-only filesystems in CI sandboxes).
            pass

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"generated_plan": plan},
        )
