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

from typing import Any, Literal

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
_RIGHT_FACE_SELECTOR: dict[str, Any] = {"kind": "face_named", "name": "right"}
_LEFT_FACE_SELECTOR:  dict[str, Any] = {"kind": "face_named", "name": "left"}
_BACK_FACE_SELECTOR:  dict[str, Any] = {"kind": "face_named", "name": "back"}
_FRONT_FACE_SELECTOR: dict[str, Any] = {"kind": "face_named", "name": "front"}

# COMPLEX-CAD fix (2026-06-08): map (axis_idx, sign) → entry face selector
# so non-Z features get the correct face. axis_idx: 0=X, 1=Y, 2=Z.
# Sign: +1 if axis_dir points TOWARD the entry-face exterior normal
# (i.e. the cut drills INTO the body opposite to the dir). Convention
# matches the catalog's axis_dir (points outward through the open face).
_AXIS_TO_FACE_SELECTOR: dict[tuple[int, int], dict[str, Any]] = {
    (2, +1): _DEFAULT_FACE_SELECTOR,  # +Z → top
    (2, -1): _BOTTOM_FACE_SELECTOR,
    (0, +1): _RIGHT_FACE_SELECTOR,
    (0, -1): _LEFT_FACE_SELECTOR,
    (1, +1): _BACK_FACE_SELECTOR,
    (1, -1): _FRONT_FACE_SELECTOR,
}


def _dominant_axis_sign(axis_dir: Any) -> tuple[int, int] | None:
    """Return (axis_idx, sign) for the dominant component of ``axis_dir``,
    or None if no component crosses the 0.5 threshold."""
    try:
        comps = [abs(float(axis_dir[i])) for i in range(3)]
    except Exception:
        return None
    if not comps or max(comps) < 0.5:
        return None
    axis_idx = comps.index(max(comps))
    try:
        sgn = 1 if float(axis_dir[axis_idx]) > 0 else -1
    except Exception:
        sgn = 1
    return (axis_idx, sgn)


def _direction_str_from_axis(axis_idx: int, into_sign: int) -> str:
    """COMPLEX-CAD fix (2026-06-08): direction string pointing INTO the body
    from the entry face. into_sign = -1 if the catalog's axis_dir points
    outward (so drilling direction is opposite); +1 if it already points
    inward.
    """
    char = "XYZ"[axis_idx]
    return f"{'+' if into_sign > 0 else '-'}{char}"

# Minimum predicted cut volume (mm³) for an emitted hole/pocket. Below this
# the executor's volume_decreased PostCondition (default 0.01 mm³) trips on
# OCCT roundoff and the whole plan FAILs even though the geometry is sound.
# Set to 2× the post-condition floor so sub-mm catalog noise (e.g. 0402
# capacitor terminal pads with d≈0.04 mm) drops out instead of poisoning the
# plan. Real holes on consumer-electronics scale parts are orders of
# magnitude above this.
_MIN_EMITTED_CUT_MM3 = 0.02

# COMPLEX-CAD pass-7 dehardcode (2026-06-09): single source for the
# "centre.z within X% of bbox.zmax → top-anchored boss" gate. Was
# duplicated at lines 1875 (_BOSS_TOP_ANCHOR_RATIO_GATE) and 2254
# (_BOSS_TOP_ANCHOR_RATIO); the two could drift on the next refactor.
# Empirical value — real top-face bosses have centre.z very close to
# bbox.zmax because the cylinder anchor sits on the top face. Conservative
# 10 % default keeps thin protrusions in while dropping internal cylinder
# fragments mis-classified by detect_bosses.
_BOSS_TOP_ANCHOR_RATIO = 0.10

# COMPLEX-CAD pass-8 (2026-06-09): silhouette guard threshold. A pocket
# whose top_d ≥ this fraction × the entry FACE's smaller in-plane extent
# (or whose depth ≥ this fraction × the perpendicular bbox extent) is
# treated as a "silhouette" — the detector accidentally walked the
# whole body outline rather than a real cavity. Empirical 0.90 catches
# detector errors on PinHeader / as1-oc-214; loosening to 0.95 would
# leak silhouettes back in, tightening to 0.80 starts dropping real
# pockets that span the body intentionally. Overridable via
# PHONE_DESIGNER_POCKET_SILHOUETTE_FRAC env var.
import os as _os
try:
    _POCKET_SILHOUETTE_FRAC = float(
        _os.environ.get("PHONE_DESIGNER_POCKET_SILHOUETTE_FRAC", "0.90")
    )
except ValueError:
    _POCKET_SILHOUETTE_FRAC = 0.90


def _predicted_cylinder_volume_mm3(diameter_mm: float, depth_mm: float) -> float:
    """Predicted cylinder volume for a hole/pocket cut. Used to drop catalog
    features whose cut would land below the volume_decreased threshold."""
    import math as _math
    if diameter_mm <= 0.0 or depth_mm <= 0.0:
        return 0.0
    r = float(diameter_mm) * 0.5
    return _math.pi * r * r * float(depth_mm)


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


def _body_volume_mm3(body: Any) -> float | None:
    """Best-effort body volume in mm³. Returns None on any failure (sheet
    bodies, OCCT exceptions, missing body).
    """
    if body is None:
        return None
    try:
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps

        shape = body.wrapped if hasattr(body, "wrapped") else body
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props)
        v = float(props.Mass())
        if v <= 0.0:
            return None
        return v
    except Exception:
        return None


def _primary_feature_axis(
    catalog: dict | None,
    bbox: tuple[float, float, float, float, float, float] | None,
) -> str:
    """Detect the body's primary feature axis ('X' | 'Y' | 'Z').

    PACK B (CTRL-Z-AXIS) — when the catalog's hole/pocket features
    overwhelmingly point along one cardinal axis, that axis is the
    "primary" extrude/drill axis. Downstream face_named="top"/"bottom"
    selectors only resolve ±Z faces, so features whose axis is NOT ±Z
    cannot be reached by the placeholder box plan and must be dropped.

    Heuristic:
      1. Tally each hole/pocket's dominant axis_dir component (X/Y/Z).
      2. If one axis wins by a 2:1 margin, return it.
      3. Otherwise fall back to the dominant bbox extent.
    """
    counts: dict[str, int] = {"X": 0, "Y": 0, "Z": 0}
    if catalog is not None:
        for src in ("holes", "pockets"):
            for f in (catalog.get(src) or []):
                ad = f.get("axis_dir")
                if not ad or len(ad) < 3:
                    continue
                try:
                    ax, ay, az = (
                        abs(float(ad[0])),
                        abs(float(ad[1])),
                        abs(float(ad[2])),
                    )
                except Exception:
                    continue
                if max(ax, ay, az) < 0.5:
                    continue
                if ax >= ay and ax >= az:
                    counts["X"] += 1
                elif ay >= az:
                    counts["Y"] += 1
                else:
                    counts["Z"] += 1
    total = counts["X"] + counts["Y"] + counts["Z"]
    if total > 0:
        ranked = sorted(counts.items(), key=lambda c: -c[1])
        top, second = ranked[0], ranked[1]
        # Decisive win: top has ≥2/3 of the votes.
        if top[1] >= 2 * second[1] and top[1] >= 2:
            return top[0]
    # Fallback: bbox extents.
    if bbox is not None:
        try:
            L = float(bbox[3]) - float(bbox[0])
            W = float(bbox[4]) - float(bbox[1])
            H = float(bbox[5]) - float(bbox[2])
            extents = [("X", L), ("Y", W), ("Z", H)]
            extents.sort(key=lambda c: -c[1])
            return extents[0][0]
        except Exception:
            pass
    return "Z"


def _feature_axis_is_z(feat: dict, threshold: float = 0.5) -> bool:
    """True if the feature's axis_dir is dominantly along ±Z (|z| > threshold).

    Used to gate emission of pockets / holes / mirror_feature steps when the
    body's primary axis is X or Y: features whose axis isn't ±Z cannot be
    reached by the placeholder box plan's face_named="top"/"bottom" face
    selector, so we drop them rather than emit broken steps.
    """
    ad = feat.get("axis_dir")
    if not ad or len(ad) < 3:
        return True  # missing axis ⇒ assume Z (existing default)
    try:
        return abs(float(ad[2])) > threshold
    except Exception:
        return True


def _pick_face_selector(
    axis_origin: Any,
    axis_dir: Any,
    bbox: tuple[float, float, float, float, float, float] | None,
) -> dict[str, Any]:
    """Choose entry-face selector based on the feature's axis & body bbox.

    COMPLEX-CAD fix (2026-06-08): generalized to all 6 axis-aligned faces.
    The catalog's ``axis_dir`` points outward through the feature's open
    face, so the entry face has the matching outward normal.

    Falls back to top when the axis is ambiguous (all components < 0.5),
    matching prior behavior.
    """
    if bbox is None and axis_dir is None:
        return _DEFAULT_FACE_SELECTOR
    if axis_dir is not None:
        sel = _dominant_axis_sign(axis_dir)
        if sel is not None:
            return _AXIS_TO_FACE_SELECTOR.get(sel, _DEFAULT_FACE_SELECTOR)
    # Fallback: distance from axis_origin to nearest Z face (legacy path).
    if bbox is not None and axis_origin is not None:
        try:
            z = float(axis_origin[2])
            zmin, zmax = float(bbox[2]), float(bbox[5])
            if abs(z - zmin) < abs(z - zmax):
                return _BOTTOM_FACE_SELECTOR
        except Exception:
            pass
    return _DEFAULT_FACE_SELECTOR


def _world_to_box_shift(
    bbox: tuple[float, float, float, float, float, float] | None,
) -> tuple[float, float, float]:
    """Compute the translation (tx, ty, tz) that maps world coordinates into
    the placeholder ``box`` skill's frame.

    The ``box`` skill builds an XY-centered slab whose bottom sits at Z=0
    (Align.CENTER, Align.CENTER, Align.MIN). The catalog stores feature
    positions in the *original* body's world frame. To align catalog
    features with the placeholder box we shift each coordinate by
    ``(-cx, -cy, -zmin)`` where ``cx, cy`` are the bbox XY centre and
    ``zmin`` is the bbox floor.

    Returns ``(0, 0, 0)`` when the bbox is unknown so callers degrade
    gracefully (existing behaviour). Callers that build plans on top of the
    original BREP (preserve_brep / import_step) should pass ``None`` so no
    shift is applied — the original world frame is already correct.
    """
    if bbox is None:
        return (0.0, 0.0, 0.0)
    xmin, ymin, zmin, xmax, ymax, _zmax = bbox
    cx = (float(xmin) + float(xmax)) * 0.5
    cy = (float(ymin) + float(ymax)) * 0.5
    return (-cx, -cy, -float(zmin))


def _hole_step(
    idx: int,
    hole: dict,
    std_match: dict | None,
    bbox: tuple[float, float, float, float, float, float] | None = None,
    shift: tuple[float, float, float] = (0.0, 0.0, 0.0),
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
    # COMPLEX-CAD pass-21 REVERTED. Even with pass-20 reorder (holes
    # before pockets) AND no clamp, as1_pe_203 box drops 0.625 → 0.516
    # because the deep 1016 mm holes still overlap with where 4 of the
    # 18 pockets sit on the chassis topology — whichever cut runs
    # second, BRepAlgoAPI_Cut no-ops on already-removed material.
    # Reorder doesn't fix geometric overlap; only a CSG-aware planner
    # or a depth-vs-position constraint solver would.
    if depth > 200.0:
        depth = 200.0
    axis_dir = hole.get("axis_dir") or [0.0, 0.0, -1.0]
    axis_origin = hole.get("axis_origin") or [0.0, 0.0, 0.0]
    face_sel = _pick_face_selector(axis_origin, axis_dir, bbox)
    # Re-anchor catalog world-frame coords into the placeholder box's frame
    # (shift = (-cx, -cy, -zmin)). Identity when shift is (0,0,0).
    ox = float(axis_origin[0]) + shift[0]
    oy = float(axis_origin[1]) + shift[1]
    oz = float(axis_origin[2]) + shift[2]

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

    # COMPLEX-CAD pass-12 (2026-06-09): in box mode (shift != identity)
    # the specialized hole skills (counterbore_hole / countersink_hole /
    # tap_drill_hole / clearance_hole) use face_named + position_xy. The
    # face_named resolves to the placeholder slab's OUTER face, not the
    # catalog's actual entry plane, so the cut drifts by the same
    # margin as the pocket case (now fixed via extrude_pocket_world).
    # Force the generic ``hole`` skill path (which uses world position +
    # direction) for box mode so cuts land at the catalog axis_origin.
    if shift != (0.0, 0.0, 0.0):
        thread_spec = None

    sid = f"s_hole_{idx}"

    if htype == "counterbore" and thread_spec:
        return _new_step(sid, "counterbore_hole", {
            "face_selector": face_sel,
            "position_xy": [ox, oy],
            "thread_spec": thread_spec,
            "fit": "medium",
            "depth_mm": depth,
        })
    if htype == "countersink" and thread_spec:
        return _new_step(sid, "countersink_hole", {
            "face_selector": face_sel,
            "position_xy": [ox, oy],
            "thread_spec": thread_spec,
            "fit": "medium",
            "depth_mm": depth,
        })
    if htype == "threaded" and thread_spec:
        return _new_step(sid, "tap_drill_hole", {
            "face_selector": face_sel,
            "position_xy": [ox, oy],
            "thread_spec": thread_spec,
            "depth_mm": depth,
        })
    if thread_spec:
        return _new_step(sid, "clearance_hole", {
            "face_selector": face_sel,
            "position_xy": [ox, oy],
            "thread_spec": thread_spec,
            "fit": "medium",
            "depth_mm": depth,
        })

    # Direction inferred from dominant axis component.
    dir_str = _axis_dir_to_str(axis_dir)
    # COMPLEX-CAD pass-8 (2026-06-09 REVERT): override stays all-modes.
    # The pass-7c BRepClass3d axis_origin standardiser got the entry side
    # right for Ventilator but DRIFTED ~1 mm on as1_pe_203 holes
    # (1.0 → 0.903 in preserve_brep). Until the probe is exact, the
    # bbox-face override preserves as1_pe_203 PERFECT; Ventilator's
    # 0.36 stays a known limitation tied to the override convention.
    # COMPLEX-CAD pass-12 (2026-06-09): entry-Z override only in
    # preserve_brep / import_step modes (shift == identity), where the
    # catalog's axis_origin may still point to the deep cap. In box mode
    # the catalog axis_origin (after shift) already lands at the correct
    # box-local entry of the hole; overriding to the bbox face moves
    # INTERIOR holes (e.g. as1_pe_203 hole 0 at world y=0 inside a body
    # extending y=-686..1524) by hundreds of mm to the +Y face. Skip
    # the override for box mode so interior holes land where the
    # catalog says they do.
    axis_sel = _dominant_axis_sign(axis_dir)
    if (
        axis_sel is not None
        and bbox is not None
        and shift == (0.0, 0.0, 0.0)
    ):
        axis_idx, sgn = axis_sel
        bmin_w = float(bbox[axis_idx])
        bmax_w = float(bbox[axis_idx + 3])
        entry_w = bmax_w if sgn > 0 else bmin_w
        entry_local = entry_w + float(shift[axis_idx])
        pos = [ox, oy, oz]
        pos[axis_idx] = entry_local
        ox, oy, oz = pos[0], pos[1], pos[2]
        dir_str = _direction_str_from_axis(axis_idx, -sgn)
    return _new_step(sid, "hole", {
        "position": [ox, oy, oz],
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
    shift: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict:
    top_d = float(pocket.get("top_d_mm") or 0.0)
    depth = float(pocket.get("depth_mm") or 1.0)
    # PACK F — clamp depth to extrude_pocket / hole ≤200 mm cap.
    if depth > 200.0:
        depth = 200.0
    origin = pocket.get("axis_origin") or [0.0, 0.0, 0.0]
    axis_dir = pocket.get("axis_dir") or [0.0, 0.0, -1.0]
    # PACK-C debug: opt-in via env var to surface false-pass-drift cases.
    import os as _os
    if _os.environ.get("PHONE_DESIGNER_DEBUG_POCKET") == "1":
        print(
            f"[pocket_step idx={idx}] axis_origin={origin} axis_dir={axis_dir} "
            f"top_d={top_d} depth={depth}"
        )
    face_sel = _pick_face_selector(origin, axis_dir, bbox)
    sid = f"s_pocket_{idx}"
    ox = float(origin[0]) + shift[0]
    oy = float(origin[1]) + shift[1]
    oz = float(origin[2]) + shift[2]

    # Circular pockets whose depth dominates → treat as a raw hole.
    if top_d > 0 and depth / max(top_d, 1e-3) >= 1.5:
        # COMPLEX-CAD pass-15 (2026-06-09): entry-Z correction is
        # preserve_brep-only (mirrors the _hole_step pass-12 fix). In
        # box mode the override moves interior cuts onto the slab's
        # outer face and the cut lands hundreds of mm from where the
        # catalog says it should.
        dir_str = _axis_dir_to_str(axis_dir)
        axis_sel = _dominant_axis_sign(axis_dir)
        if axis_sel is not None and bbox is not None and shift == (0.0, 0.0, 0.0):
            axis_idx, sgn = axis_sel
            bmin_w = float(bbox[axis_idx])
            bmax_w = float(bbox[axis_idx + 3])
            entry_w = bmax_w if sgn > 0 else bmin_w
            entry_local = entry_w + float(shift[axis_idx])
            pos = [ox, oy, oz]
            pos[axis_idx] = entry_local
            ox, oy, oz = pos[0], pos[1], pos[2]
            dir_str = _direction_str_from_axis(axis_idx, -sgn)
        return _new_step(sid, "hole", {
            "position": [ox, oy, oz],
            "diameter_mm": top_d,
            "depth_mm": depth,
            "direction": dir_str,
        })

    # Default — extrude_pocket with placeholder rectangular sketch sized to
    # the measured top diameter. RectangleSketch fields: kind="rectangle"
    # + length_mm + width_mm + center_x_mm + center_y_mm.
    size = top_d if top_d > 0 else 5.0
    # PACK B drift fix — clamp sketch L/W so the pocket can't be larger than
    # the body's XY footprint. The catalog frequently reports top_d_mm at
    # the OUTER cap of a stepped/through pocket which can equal or exceed
    # the body's shorter XY extent (e.g. JST B2B reports top_d=7.5 mm on a
    # 7.5 × 3.8 mm body — the pocket then carves the full 7.5 × 7.5 square
    # out of the body, collapsing Z drift to 40%+). Clamp each axis
    # independently to (body_extent - 2 * shift) so the pocket is fully
    # interior. Also reject the step entirely when the pocket centre falls
    # outside the body's XY bbox — those are detector artefacts on
    # off-axis cylinder chains and would emit a step that misses the body.
    if bbox is not None:
        try:
            xmin, ymin, _zmin, xmax, ymax, _zmax = bbox
            body_l = float(xmax) - float(xmin)
            body_w = float(ymax) - float(ymin)
            # leave a 1% slab margin so the pocket is strictly interior.
            max_l = max(body_l * 0.98, 0.1)
            max_w = max(body_w * 0.98, 0.1)
            length_mm = min(size, max_l)
            width_mm = min(size, max_w)
            # Reject pockets whose XY centre is outside the body's bbox in
            # local (shifted) coords: in box-mode the placeholder slab is
            # centred at (0,0) so the body-local extents are
            # (-body_l/2, body_l/2) × (-body_w/2, body_w/2). If the centre
            # sits more than half-extent + margin away, it's spurious.
            if shift != (0.0, 0.0, 0.0):
                if abs(ox) > body_l * 0.5 + 0.5 or abs(oy) > body_w * 0.5 + 0.5:
                    # Degenerate: the pocket would cut outside the slab.
                    # Skip by emitting a near-zero pocket centred on origin
                    # (executor's zero-delta skip path catches it).
                    length_mm = 0.1
                    width_mm = 0.1
                    ox = 0.0
                    oy = 0.0
        except Exception:
            length_mm = size
            width_mm = size
    else:
        length_mm = size
        width_mm = size
    # COMPLEX-CAD pass-11 (2026-06-09): re-enable world-pocket emission.
    # Standalone test of chained extrude_pocket_world cuts confirmed the
    # chain works (cut1 vol=2518800, cut2 vol=2517600, kinds=1 solid +
    # multi-shell). The earlier suspicion that the chain crashed was
    # actually subprocess-timing noise. Use world placement in box mode
    # so cuts land at the catalog's axis_origin verbatim instead of
    # being projected onto an outer placeholder face.
    use_world_pocket = (shift != (0.0, 0.0, 0.0))
    if use_world_pocket:
        # COMPLEX-CAD pass-10 (2026-06-09): the placeholder Box skill builds
        # the body in BOX-LOCAL coords (XY-centered at origin, Z floor 0).
        # ``ox/oy/oz`` are already in that frame (catalog axis_origin +
        # feat_shift). For preserve_brep mode shift is identity so the
        # values match the original body's world coords.
        #
        # Direction convention: classify_pockets stores ``axis_dir`` as
        # the INWARD direction (line 361: ``axis_dir = -plane_normal``),
        # so the tool needs to extend in +axis_dir for the cavity to
        # land inside the body. extrude_pocket_world's ``direction="out"``
        # extends along +axis_dir; "into" extends opposite. The "out"
        # branch here is the correct one for catalog-emitted pockets.
        return _new_step(sid, "extrude_pocket_world", {
            "world_origin": [ox, oy, oz],
            "axis_dir": [float(axis_dir[0]), float(axis_dir[1]), float(axis_dir[2])],
            "length_mm": float(length_mm),
            "width_mm": float(width_mm),
            "depth_mm": depth,
            "direction": "out",
        })

    # COMPLEX-CAD fix (2026-06-08): for non-Z faces emit the TWO in-plane
    # world coords as (center_x_mm, center_y_mm). The 3rd coord (the face's
    # normal axis) gets filled in by the resolver from the face centroid.
    # Without this, world Z is dropped on side-face pockets and every cut
    # stacks at the face centroid → zero-delta SKIP.
    face_name = face_sel.get("name") if isinstance(face_sel, dict) else None
    if face_name in ("left", "right"):
        cx_emit, cy_emit = oy, oz
    elif face_name in ("front", "back"):
        cx_emit, cy_emit = ox, oz
    else:
        cx_emit, cy_emit = ox, oy
    return _new_step(sid, "extrude_pocket", {
        "face_selector": face_sel,
        "sketch": {
            "kind": "rectangle",
            "length_mm": float(length_mm),
            "width_mm": float(width_mm),
            "center_x_mm": float(cx_emit),
            "center_y_mm": float(cy_emit),
        },
        "depth_mm": depth,
    })


def _boss_step(
    idx: int,
    boss: dict,
    bbox: tuple[float, float, float, float, float, float] | None = None,
    shift: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict:
    center = boss.get("center") or [0.0, 0.0, 0.0]
    height = float(boss.get("height_mm") or 1.0)
    size = float(boss.get("diameter_or_size_mm") or 4.0)
    # Bosses grow upward from their anchor face; choose the closer body face
    # based on the boss centre Z.
    face_sel = _pick_face_selector(center, [0.0, 0.0, 1.0], bbox)
    sid = f"s_boss_{idx}"
    cx = float(center[0]) + shift[0]
    cy = float(center[1]) + shift[1]

    # BBOX-AWARE BOSS HEIGHT CAP — detectors frequently report a boss
    # ``height_mm`` that spans the FULL local cylinder length (e.g.
    # linkrods reports height=2.0 mm for a boss whose center sits at z=1.0
    # in a 2.0 mm tall body, so the cylinder fragment terminates at z=2.0
    # — the actual protrusion above the top face is 1.0 mm). Emitting the
    # raw height inflates the regen bbox Z extent past the original.
    #
    # When the bbox is known we cap height_mm to (bbox.zmax - center.z),
    # which is the maximum protrusion that keeps the boss inside the
    # original body's Z extent. If the result is non-positive the boss is
    # internal to the slab and we degrade to a 1 mm shoulder so the step
    # still validates.
    if bbox is not None:
        try:
            zmax = float(bbox[5])
            cz = float(center[2])
            allowed = max(zmax - cz, 0.0)
            if allowed > 0.0:
                height = min(height, allowed)
            else:
                height = min(height, 1.0)
        except Exception:
            pass

    # PLAN-DEPTH-CEILING fix: the mounting_pad skill caps height_mm at 20 mm
    # (Field(gt=0, le=20)). On large assembly parts the detector often spans
    # boss heights at the full body Z extent (e.g. 200 mm on as1-oc-214) —
    # those bosses are usually internal cylinder fragments mis-classified as
    # bosses. Clamp to the skill's upper bound so the step at least validates
    # and runs; the zero-delta skip path catches it from there if the cut
    # lands in already-removed material.
    if height > 20.0:
        height = 20.0
    elif height <= 0.0:
        height = 1.0

    # mounting_pad expects a SketchSpec (CircleSketch | RectangleSketch | …)
    # + height_mm. Both branches emit a circular pad at the projected XY.
    return _new_step(sid, "mounting_pad", {
        "face_selector": face_sel,
        "sketch": {
            "kind": "circle",
            "diameter_mm": size,
            "center_x_mm": cx,
            "center_y_mm": cy,
        },
        "height_mm": height,
    })


def _rib_step(idx: int, rib: dict, base_top_z: float = 0.0) -> dict:
    sid = f"s_rib_{idx}"
    length = float(rib.get("length_mm") or 10.0)
    thickness = float(rib.get("thickness_mm") or 1.0)
    height = float(rib.get("height_mm") or 3.0)
    # Rib needs start/end + width/height/up_axis. The detector reports only
    # length/thickness/height (no anchor), so we centre the rib on the top
    # face of the placeholder box (z = base_top_z). With up_axis="+Z" and
    # Align.MIN on the rib_box, the rib grows ABOVE the slab, producing a
    # real volume_increased delta. Anchoring at z=0 would put the rib
    # entirely INSIDE the slab and the post_condition would see no change.
    return _new_step(sid, "rib", {
        "start": [-length / 2.0, 0.0, float(base_top_z)],
        "end": [length / 2.0, 0.0, float(base_top_z)],
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
    shift: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict:
    origin = pocket.get("axis_origin") or [0.0, 0.0, 0.0]
    axis_dir = pocket.get("axis_dir") or [0.0, 0.0, -1.0]
    face_sel = _pick_face_selector(origin, axis_dir, bbox)
    sid = f"s_magnet_pocket_{idx}"
    return _new_step(sid, "magnet_pocket_axial", {
        "face_selector": face_sel,
        "position_xy": [float(origin[0]) + shift[0], float(origin[1]) + shift[1]],
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
    shift: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict:
    origin = pocket.get("axis_origin") or [0.0, 0.0, 0.0]
    axis_dir = pocket.get("axis_dir") or [0.0, 0.0, -1.0]
    sid = f"s_bearing_bore_{idx}"
    return _new_step(sid, "bearing_bore", {
        "axis_origin": [
            float(origin[0]) + shift[0],
            float(origin[1]) + shift[1],
            float(origin[2]) + shift[2],
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
    shift: tuple[float, float, float] = (0.0, 0.0, 0.0),
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
        "center_x_mm": float(axis_origin[0]) + shift[0],
        "center_y_mm": float(axis_origin[1]) + shift[1],
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


def _revolve_pocket_step(
    idx: int,
    feat: dict,
    shift: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict:
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
            float(axis_origin[0]) + shift[0],
            float(axis_origin[1]) + shift[1],
            float(axis_origin[2]) + shift[2],
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


def _clamp_feature_depth_mm(depth: float) -> float:
    """Clamp depth to the pattern skills' ``feature_depth_mm`` bound (0,200].

    PACK F (PLAN-DEPTH-CEILING-PATTERN): linear_pattern / circular_pattern /
    mirror_feature all declare ``feature_depth_mm: Field(gt=0, le=200)``. On
    large assembly parts (e.g. pythonocc__11752 — a 1280×144×133 mm chassis
    plate) the seed hole's depth comes from the bbox extent and can be
    1000+ mm, which fails Args validation BEFORE the step ever runs. Clamp
    here so the step at least validates; if the cut lands in already-removed
    material the executor's zero-delta-volume skip path handles it from
    there.
    """
    d = float(depth)
    if d <= 0.0:
        return 1.0
    if d > 200.0:
        return 200.0
    return d


def _circular_pattern_step(
    idx: int,
    ring: dict,
    profile_diameter_mm: float,
    feature_depth_mm: float,
    bbox: tuple[float, float, float, float, float, float] | None = None,
    shift: tuple[float, float, float] = (0.0, 0.0, 0.0),
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
        "feature_depth_mm": _clamp_feature_depth_mm(feature_depth_mm),
        "count": count,
        "pitch_radius_mm": radius,
        "center_x_mm": float(center[0]) + shift[0],
        "center_y_mm": float(center[1]) + shift[1],
        "start_angle_deg": 0.0,
        "total_sweep_deg": 360.0,
    })


def _linear_pattern_step(
    idx: int,
    run: dict,
    profile_diameter_mm: float,
    feature_depth_mm: float,
    bbox: tuple[float, float, float, float, float, float] | None = None,
    shift: tuple[float, float, float] = (0.0, 0.0, 0.0),
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
    # PACK F — linear_pattern caps spacing_mm at 500 mm. On large assemblies
    # (e.g. pythonocc__11752) the detected hole-run spacing can exceed
    # 500 mm; clamp here so Args validates.
    if spacing > 500.0:
        spacing = 500.0
    # linear_pattern caps count at 200.
    if count > 200:
        count = 200

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
    start_x = float(first[0]) + shift[0]
    start_y = float(first[1]) + shift[1]
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
        "feature_depth_mm": _clamp_feature_depth_mm(feature_depth_mm),
        "count": count,
        "spacing_mm": spacing,
        "direction": axis_letter,
        "start_offset_x_mm": start_x,
        "start_offset_y_mm": start_y,
    })


# ──────────────────────────────────────────────────────────────────────────────
# Symmetry-aware mirror_feature emission
#
# The mirror_feature skill (modify_pattern/mirror_feature.py) takes a circular
# feature template (profile_diameter_mm + operation in {pocket,hole,boss}) and
# reflects it across an axis-aligned world plane (XZ | YZ | XY). To collapse
# a pair of catalog holes into one mirror_feature step we need:
#
#   1. A mirror plane from catalog['symmetries'] with symmetry_score >= 0.7 AND
#      a plane_normal close to a cardinal world axis (so it maps to one of the
#      Literal["XZ","YZ","XY"] options the skill accepts).
#   2. Two holes whose axis_origin XY positions reflect across that plane
#      (within tol), with matching diameter and depth.
#
# Holes that lie ON the mirror plane (self-symmetric) are left alone -- a
# mirror_feature with coincident original + reflection produces two stacked
# features at the same point.

_SYMMETRY_MIN_SCORE = 0.7
_AXIS_ALIGN_COS = 0.95   # |dot(normal, axis)| above this snaps the plane
_PAIR_XY_TOL = 0.5       # mm: reflection match tolerance in XY
_PAIR_Z_TOL = 0.5        # mm: same Z plane (both holes on the same face)
_PAIR_DIAM_TOL = 0.2     # mm
_PAIR_DEPTH_TOL = 0.5    # mm

# PACK D (SYMMETRY-ON-BASE) — defensive guard.
#
# Skill names that build the body from scratch (category="create" in the
# registry) MUST NEVER be wrapped by a mirror_feature step. mirror_feature is
# a modify/pattern macro that reflects an INDIVIDUAL feature (hole/pocket/
# boss) across an axis-aligned world plane. Applying it to the base extrude
# itself would mirror the entire body and produce a degenerate union (e.g.
# Ventilator's 45,450% drift / 400× bbox blowup).
#
# The symmetry-aware emission block (6b below) only ever iterates the
# catalog's ``holes`` list, but we explicitly enforce the rule both as a
# runtime guard inside the block and as a precondition on the input list so
# that any future refactor (e.g. broadening the block to operate on a unified
# "features" list that mixes in base entries) trips this check rather than
# silently emitting a body-mirror.
_NON_MIRRORABLE_SKILLS: frozenset[str] = frozenset({
    "box", "import_step", "preserve_brep",
    "cylinder", "sphere", "cone", "torus", "wedge", "prism_n_sided",
    "rounded_slab", "disc_with_dome", "gear_external_involute",
    "gear_internal_involute", "gear_helical_involute", "coil_spring_rectangular",
    "helical_spring", "worm_thread", "voronoi_lattice", "gyroid_lattice",
    "bspline_surface", "mesh_to_brep", "stl_import", "brep_import", "iges_import",
    "sheet_base", "surface_thicken_variable",
})


def _is_base_or_create_step(step_or_feat: Any) -> bool:
    """True if ``step_or_feat`` looks like a base/create step rather than a
    catalog feature dict.

    Catalog feature dicts (holes, pockets, etc.) carry geometric keys like
    ``axis_origin`` / ``diameters_mm`` and have NO ``skill`` field. Plan step
    dicts always carry an ``id`` AND a ``skill`` field; a step whose skill is
    in ``_NON_MIRRORABLE_SKILLS`` (or whose id starts with ``"s_base"``) MUST
    NOT be wrapped by mirror_feature.
    """
    if not isinstance(step_or_feat, dict):
        return False
    sid = step_or_feat.get("id")
    if isinstance(sid, str) and sid.startswith("s_base"):
        return True
    skill_name = step_or_feat.get("skill")
    if isinstance(skill_name, str) and skill_name in _NON_MIRRORABLE_SKILLS:
        return True
    return False


def _snap_plane_to_axis_label(plane_normal) -> str | None:
    """Return 'YZ' / 'XZ' / 'XY' if ``plane_normal`` is close to ±X / ±Y / ±Z.

    The mirror_feature skill names planes by the axis NEGATED by the mirror --
    so a plane whose normal is the X axis is the YZ plane. None when the
    normal is diagonal (no cardinal axis is dominant).
    """
    try:
        nx, ny, nz = (
            float(plane_normal[0]),
            float(plane_normal[1]),
            float(plane_normal[2]),
        )
    except Exception:
        return None
    import math as _math
    nm = _math.sqrt(nx * nx + ny * ny + nz * nz)
    if nm < 1e-9:
        return None
    nx, ny, nz = nx / nm, ny / nm, nz / nm
    ax, ay, az = abs(nx), abs(ny), abs(nz)
    if ax >= _AXIS_ALIGN_COS:
        return "YZ"
    if ay >= _AXIS_ALIGN_COS:
        return "XZ"
    if az >= _AXIS_ALIGN_COS:
        return "XY"
    return None


def _select_mirror_plane(symmetries: list) -> dict | None:
    """Pick the highest-scoring axis-aligned mirror plane meeting the score
    floor. Returns ``{label, origin, normal, score}`` or None.

    Skips XY (horizontal) planes — for holes on a slab's top/bottom face the
    reflection lands at the same XY position, producing a degenerate two-
    feature step that adds no value over a single hole.

    NOTE: When several qualifying planes exist, ``_qualifying_mirror_planes``
    enumerates ALL of them so the caller can score each by how many feature
    pairs it actually produces. This helper is kept for callers that just
    want the best score (used in unit tests).
    """
    best: dict | None = None
    for plane in _qualifying_mirror_planes(symmetries):
        if best is None or plane["score"] > best["score"]:
            best = plane
    return best


def _qualifying_mirror_planes(symmetries: list) -> list[dict]:
    """All axis-aligned mirror planes with score >= floor, descending score.

    Skips XY planes (no effect on XY position of holes on a horizontal face).
    """
    out: list[dict] = []
    for sym in symmetries or []:
        if not isinstance(sym, dict):
            continue
        score = float(sym.get("symmetry_score") or 0.0)
        if score < _SYMMETRY_MIN_SCORE:
            continue
        label = _snap_plane_to_axis_label(sym.get("plane_normal"))
        if label is None or label == "XY":
            continue
        origin = sym.get("plane_origin") or (0.0, 0.0, 0.0)
        normal = sym.get("plane_normal") or (1.0, 0.0, 0.0)
        out.append({
            "label": label,
            "origin": tuple(float(c) for c in origin),
            "normal": tuple(float(c) for c in normal),
            "score": score,
        })
    out.sort(key=lambda p: -p["score"])
    return out


def _reflect_xy_across_plane(
    x: float, y: float, plane_label: str, plane_origin: tuple,
) -> tuple[float, float]:
    """Reflect the (x, y) point across an axis-aligned mirror plane."""
    mox, moy = float(plane_origin[0]), float(plane_origin[1])
    if plane_label == "YZ":
        return (2.0 * mox - x, y)
    if plane_label == "XZ":
        return (x, 2.0 * moy - y)
    # XY plane reflects Z -- XY positions unchanged.
    return (x, y)


def _hole_signature_key(hole: dict) -> tuple[float, float]:
    """Diameter + depth pair used to match mirrored holes."""
    diams = hole.get("diameters_mm") or [0.0]
    d = float(min(diams)) if diams else 0.0
    depth = float(hole.get("depth_mm") or 0.0)
    return (d, depth)


def _find_mirrored_hole_pairs(
    holes: list, plane: dict,
    bbox: tuple[float, float, float, float, float, float] | None = None,
) -> list[tuple[int, int]]:
    """Return list of (i, j) index pairs of holes that mirror each other
    across ``plane``. Each hole is matched at most once. Holes lying on the
    plane (self-symmetric) are skipped.

    COMPLEX-CAD pass-8 (2026-06-09): pair-matching tolerances now scale
    with the body's bbox diagonal so industrial assemblies (5 m chassis)
    don't lose mirror-pair detection to a 0.5 mm fixed gate that is
    0.01 % of bbox at that scale. On phone-scale bodies the
    floor (_PAIR_*_TOL) wins; on industrial bodies the bbox-relative
    component (0.05 % of diag) wins.
    """
    if not plane or not holes:
        return []
    label = plane["label"]
    origin = plane["origin"]
    pairs: list[tuple[int, int]] = []
    used: set[int] = set()

    # Adaptive tolerances: floor + bbox-fraction.
    bbox_diag = 0.0
    if bbox is not None:
        try:
            bbox_diag = (
                (float(bbox[3]) - float(bbox[0])) ** 2
                + (float(bbox[4]) - float(bbox[1])) ** 2
                + (float(bbox[5]) - float(bbox[2])) ** 2
            ) ** 0.5
        except Exception:
            bbox_diag = 0.0
    tol_xy = max(_PAIR_XY_TOL, bbox_diag * 0.0005)
    tol_z = max(_PAIR_Z_TOL, bbox_diag * 0.0005)
    tol_diam = max(_PAIR_DIAM_TOL, bbox_diag * 0.0002)
    tol_depth = max(_PAIR_DEPTH_TOL, bbox_diag * 0.0005)

    centers: list[tuple[float, float, float] | None] = []
    sigs: list[tuple[float, float]] = []
    for h in holes:
        ao = h.get("axis_origin")
        if ao is None:
            centers.append(None)
        else:
            try:
                centers.append((float(ao[0]), float(ao[1]), float(ao[2])))
            except Exception:
                centers.append(None)
        sigs.append(_hole_signature_key(h))

    for i in range(len(holes)):
        if i in used:
            continue
        ci = centers[i]
        if ci is None:
            continue
        rx, ry = _reflect_xy_across_plane(ci[0], ci[1], label, origin)
        # Self-symmetric hole (on the mirror plane) -- skip.
        if abs(rx - ci[0]) < tol_xy and abs(ry - ci[1]) < tol_xy:
            continue
        for j in range(i + 1, len(holes)):
            if j in used:
                continue
            cj = centers[j]
            if cj is None:
                continue
            if abs(cj[0] - rx) > tol_xy:
                continue
            if abs(cj[1] - ry) > tol_xy:
                continue
            if abs(cj[2] - ci[2]) > tol_z:
                continue
            di, depi = sigs[i]
            dj, depj = sigs[j]
            if abs(di - dj) > tol_diam:
                continue
            if abs(depi - depj) > tol_depth:
                continue
            pairs.append((i, j))
            used.add(i)
            used.add(j)
            break
    return pairs


def _mirror_feature_step(
    idx: int,
    hole: dict,
    plane: dict,
    bbox: tuple[float, float, float, float, float, float] | None,
    shift: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict | None:
    """Emit a ``mirror_feature`` step for ``hole`` mirrored across ``plane``.

    Returns None when the registered skill's args cannot be satisfied (e.g.
    missing diameter/depth, or values out of the pydantic gt/le bounds).
    """
    diams = hole.get("diameters_mm") or []
    if not diams:
        return None
    profile_d = float(min(diams))
    if profile_d <= 0.0 or profile_d > 100.0:
        return None
    depth = float(hole.get("depth_mm") or 0.0)
    if depth <= 0.0:
        return None
    # PACK F — clamp instead of drop. Holes whose depth exceeds the skill's
    # 200 mm cap previously caused mirror_feature emission to skip the pair
    # silently, leaving them to fall through to per-hole emission with the
    # same upstream depth problem. Clamp here so the mirror step at least
    # validates.
    if depth > 200.0:
        depth = 200.0

    origin = hole.get("axis_origin") or [0.0, 0.0, 0.0]
    axis_dir = hole.get("axis_dir") or [0.0, 0.0, -1.0]
    face_sel = _pick_face_selector(origin, axis_dir, bbox)

    # mirror_feature uses face-local XY: at runtime the skill resolves the
    # target face, computes its centroid (fcx, fcy, fcz), and converts our
    # original_x_mm / original_y_mm by ADDING the face centroid. We invert
    # that here by subtracting the bbox-XY centre (a good approximation of
    # the top/bottom face centroid for a slab) so the world XY of the
    # original feature matches the catalog hole's axis_origin.
    if bbox is not None:
        face_cx = (float(bbox[0]) + float(bbox[3])) * 0.5
        face_cy = (float(bbox[1]) + float(bbox[4])) * 0.5
    else:
        face_cx = 0.0
        face_cy = 0.0
    original_x = float(origin[0]) - face_cx
    original_y = float(origin[1]) - face_cy

    sid = f"s_mirror_feature_{idx}"
    return _new_step(sid, "mirror_feature", {
        "face_selector": face_sel,
        "profile_diameter_mm": profile_d,
        "operation": "hole",
        "feature_depth_mm": depth,
        "count": 2,
        "mirror_plane": plane["label"],
        "mirror_origin": [
            float(plane["origin"][0]) + shift[0],
            float(plane["origin"][1]) + shift[1],
            float(plane["origin"][2]) + shift[2],
        ],
        "original_x_mm": original_x,
        "original_y_mm": original_y,
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


BaseStepKind = Literal["box", "import_step", "preserve_brep"]


def _write_body_as_step(body: Any, plan_name: str) -> str | None:
    """Write the input body to a STEP file under run_logs/_tmp/.

    Returns the absolute path string on success, or None if the write
    failed (e.g. body is None, OCCT export error, read-only filesystem).
    """
    if body is None:
        return None
    try:
        import pathlib

        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

        root = pathlib.Path(__file__).resolve().parents[4]
        tmp_dir = root / "run_logs" / "_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        step_path = tmp_dir / f"{plan_name}_base.step"
        shape = body.wrapped if hasattr(body, "wrapped") else body
        writer = STEPControl_Writer()
        writer.Transfer(shape, STEPControl_AsIs)
        status = writer.Write(str(step_path))
        if status != IFSelect_RetDone:
            return None
        return str(step_path)
    except Exception:
        return None


def _pick_base_shape(
    body: Any,
    base_l: float, base_w: float, base_h: float,
    bbox: tuple | None,
) -> tuple[str, dict]:
    """Pick (skill_name, args) for the base step.

    Topology-driven heuristic: total area per surface type → ratios decide
    cylinder / sphere / torus / box. All thresholds are general (area ratio +
    bbox aspect), NEVER per-file. Falls back to box on any failure.

    Returns ("box", {length_mm, width_mm, height_mm}) by default.
    """
    if body is None:
        return ("box", {"length_mm": base_l, "width_mm": base_w, "height_mm": base_h})
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepGProp import BRepGProp
        from OCP.GeomAbs import (
            GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane,
            GeomAbs_Sphere, GeomAbs_Torus,
        )
        from OCP.GProp import GProp_GProps

        from phone_designer.skills._resolvers import _all_faces

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = _all_faces(shape)
        if not faces:
            raise RuntimeError("no faces")

        area_by_kind: dict[str, float] = {
            "cylinder": 0.0, "sphere": 0.0, "torus": 0.0,
            "cone": 0.0, "plane_circular": 0.0, "other": 0.0,
        }
        cyl_axis_dirs: list[tuple] = []
        cyl_radii: list[float] = []
        sph_radii: list[float] = []
        tor_majors: list[float] = []
        tor_minors: list[float] = []
        cone_apex_radii: list[float] = []
        cone_half_angles: list[float] = []
        cone_axes: list[tuple] = []
        # Circular planar caps — used for exact cylinder-height extraction.
        # Stored as (axis_origin_z_along_normal, normal_dir, radius).
        circ_caps: list[tuple] = []
        total_area = 0.0

        for f in faces:
            try:
                surf = BRepAdaptor_Surface(f)
                kind_int = surf.GetType()
                props = GProp_GProps()
                BRepGProp.SurfaceProperties_s(f, props)
                a = float(props.Mass())
                total_area += a
                if kind_int == GeomAbs_Cylinder:
                    area_by_kind["cylinder"] += a
                    c = surf.Cylinder()
                    d = c.Axis().Direction()
                    cyl_axis_dirs.append((d.X(), d.Y(), d.Z()))
                    cyl_radii.append(float(c.Radius()))
                elif kind_int == GeomAbs_Sphere:
                    area_by_kind["sphere"] += a
                    sph_radii.append(float(surf.Sphere().Radius()))
                elif kind_int == GeomAbs_Torus:
                    area_by_kind["torus"] += a
                    tt = surf.Torus()
                    tor_majors.append(float(tt.MajorRadius()))
                    tor_minors.append(float(tt.MinorRadius()))
                elif kind_int == GeomAbs_Cone:
                    area_by_kind["cone"] += a
                    cc = surf.Cone()
                    cone_apex_radii.append(float(cc.RefRadius()))
                    cone_half_angles.append(float(cc.SemiAngle()))
                    cd = cc.Axis().Direction()
                    cone_axes.append((cd.X(), cd.Y(), cd.Z()))
                elif kind_int == GeomAbs_Plane:
                    # Detect circular planar caps for exact cyl-height extraction.
                    # Circular face has area = π·r² with a SINGLE outer edge.
                    import math as _math
                    try:
                        pl = surf.Plane()
                        loc = pl.Location()
                        n = pl.Axis().Direction()
                        nx, ny, nz = n.X(), n.Y(), n.Z()
                        r_est = _math.sqrt(a / _math.pi) if a > 0 else 0.0
                        # Project location onto its own normal to get a 1D coord.
                        z_along = loc.X() * nx + loc.Y() * ny + loc.Z() * nz
                        circ_caps.append((z_along, (nx, ny, nz), r_est))
                        area_by_kind["plane_circular"] += a
                    except Exception:
                        area_by_kind["other"] += a
                else:
                    area_by_kind["other"] += a
            except Exception:
                continue

        if total_area <= 0:
            raise RuntimeError("zero total area")

        ratio_cyl  = area_by_kind["cylinder"] / total_area
        ratio_sph  = area_by_kind["sphere"]   / total_area
        ratio_tor  = area_by_kind["torus"]    / total_area
        ratio_cone = area_by_kind["cone"]     / total_area

        # Pick highest-confidence non-prismatic shape; generic thresholds.
        if ratio_sph > 0.5 and sph_radii:
            return ("sphere", {"radius_mm": max(sph_radii)})
        if ratio_tor > 0.4 and tor_majors and tor_minors:
            return ("torus", {
                "major_radius_mm": max(tor_majors),
                "minor_radius_mm": max(tor_minors),
            })
        # Cone: dominated by GeomAbs_Cone faces.
        if ratio_cone > 0.5 and cone_apex_radii:
            # Estimate height from bbox along the cone axis.
            ax = cone_axes[0]
            ax_abs = (abs(ax[0]), abs(ax[1]), abs(ax[2]))
            if bbox is not None:
                (xmin, ymin, zmin), (xmax, ymax, zmax) = bbox
                if ax_abs[2] >= max(ax_abs[0], ax_abs[1]):
                    height_mm = float(zmax - zmin)
                elif ax_abs[1] >= ax_abs[0]:
                    height_mm = float(ymax - ymin)
                else:
                    height_mm = float(xmax - xmin)
            else:
                height_mm = max(base_l, base_w, base_h)
            return ("cone", {
                "radius_lower_mm": max(cone_apex_radii),
                "radius_upper_mm": 0.1,
                "height_mm": max(0.1, height_mm),
            })
        if ratio_cyl > 0.6 and cyl_radii:
            # Try exact height from paired circular caps first
            # (parallel normals + colinear axes with the cylinder).
            ax = cyl_axis_dirs[0]
            ax_abs = (abs(ax[0]), abs(ax[1]), abs(ax[2]))
            cyl_r = max(cyl_radii)
            exact_height_mm: float | None = None
            if len(circ_caps) >= 2:
                # Caps that are parallel to the cylinder axis and whose
                # radius matches the cylinder radius within 5 %.
                aligned = []
                for z_along, n, r_est in circ_caps:
                    dot = abs(n[0] * ax[0] + n[1] * ax[1] + n[2] * ax[2])
                    if dot < 0.95:
                        continue
                    if cyl_r > 1e-6 and abs(r_est - cyl_r) / cyl_r > 0.10:
                        continue
                    aligned.append(z_along)
                if len(aligned) >= 2:
                    exact_height_mm = float(max(aligned) - min(aligned))
            if exact_height_mm is None and bbox is not None:
                (xmin, ymin, zmin), (xmax, ymax, zmax) = bbox
                if ax_abs[2] >= max(ax_abs[0], ax_abs[1]):
                    exact_height_mm = float(zmax - zmin)
                elif ax_abs[1] >= ax_abs[0]:
                    exact_height_mm = float(ymax - ymin)
                else:
                    exact_height_mm = float(xmax - xmin)
            if exact_height_mm is None:
                exact_height_mm = max(base_l, base_w, base_h)
            return ("cylinder", {
                "diameter_mm": 2.0 * cyl_r,
                "height_mm": max(0.1, exact_height_mm),
            })
    except Exception:
        pass
    return ("box", {"length_mm": base_l, "width_mm": base_w, "height_mm": base_h})


def _build_plan(
    catalog: dict,
    body: Any = None,
    base_step_kind: BaseStepKind = "box",
    plan_name: str = "reconstructed_plan",
) -> dict:
    holes = catalog.get("holes") or []
    pockets = catalog.get("pockets") or []
    bosses = catalog.get("bosses") or []
    ribs = catalog.get("ribs") or []
    lugs = catalog.get("lugs") or []
    patterns = catalog.get("patterns") or []
    symmetries = catalog.get("symmetries") or []
    sweep_features = catalog.get("sweep_features") or []
    loft_features = catalog.get("loft_features") or []
    revolve_features = catalog.get("revolve_features") or []

    # COMPLEX-CAD fix (2026-06-08): in preserve_brep mode the executor
    # starts from the ORIGINAL body, so any ADDITIVE step (boss / rib /
    # lug / sweep-boss / loft-boss) DUPLICATES topology the body already
    # has. On 11752 this inflated regen volume by 320 %. Drop all
    # additive feature lists here so only SUBTRACTIVE steps
    # (pockets / holes / sweep-pocket / loft-pocket / revolve-pocket)
    # remain. The result is a no-op-or-shrink chain that converges to
    # the original body, not an inflated version of it.
    #
    # Patterns are ALSO dropped in preserve_brep mode — they describe
    # arrays of features that the original body already contains. On
    # Ventilator, leaving the circular_pattern in produced an extra
    # 13-hole shallow ring on top of the existing deep ring (regen 27
    # holes vs orig 14). Same rationale as bosses: the pattern's
    # geometry is already present in the body, the step would duplicate.
    if base_step_kind == "preserve_brep":
        bosses = []
        ribs = []
        lugs = []
        patterns = []
        sweep_features = [f for f in sweep_features if (f.get("kind") or "pocket") != "boss"]
        loft_features = [f for f in loft_features if (f.get("kind") or "pocket") != "boss"]
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

    # PACK B — primary feature axis detection. When the body's hole/pocket
    # axes overwhelmingly point along X or Y (e.g. KiCad chip packages with
    # terminal-pad cylinders drilled into the SIDE of the package), the
    # placeholder box plan cannot reach them via face_named="top"/"bottom"
    # selectors. Features with non-±Z axes are filtered out below so the
    # plan degrades gracefully to just the base box rather than failing on
    # zero-delta volume cuts.
    # PACK B drift fix — prefer the pre-detector bbox snapshot recorded by
    # extract_feature_catalog. Re-computing _body_bbox(body) at plan time
    # returns the inflated post-detector AddOptimal_s cache (~+0.5-1.0 mm
    # on every axis) which inflates the placeholder box and shows up as
    # 10-20% bbox-volume drift even when every feature step is correct.
    _initial_bb = catalog.get("initial_bbox_mm")
    if (
        isinstance(_initial_bb, (list, tuple))
        and len(_initial_bb) >= 6
    ):
        try:
            bbox = tuple(float(c) for c in _initial_bb[:6])  # type: ignore[assignment]
        except Exception:
            bbox = _body_bbox(body)
    else:
        bbox = _body_bbox(body)
    primary_axis = _primary_feature_axis(catalog, bbox)
    # When the primary axis is NOT Z we additionally rewrite the base box
    # extents so the longest axis maps to L, middle to W, shortest to H —
    # this keeps the placeholder box's silhouette close to the original
    # part even when bbox.z is not the dominant extent.
    if bbox is not None:
        xmin, ymin, zmin, xmax, ymax, zmax = bbox
        base_l = max(float(xmax - xmin), 1e-3)
        base_w = max(float(ymax - ymin), 1e-3)
        bbox_h = max(float(zmax - zmin), 1e-3)
        # NOTE — historical "sorted_extents" rewrite for non-Z primary axis
        # has been REMOVED. It corrupted the regen bbox for bodies whose
        # feature axis is X or Y but whose silhouette already matches its
        # world-frame extents (e.g. pythonocc__as1-oc-214: 200x150x84 was
        # being silently rewritten to 200x84x150, ballooning bbox drift to
        # 16-76%). Keeping the catalog's world-frame extents means the regen
        # bbox matches the orig bbox up to feature-induced perturbation,
        # which is exactly what the corpus fidelity metric measures.
        #
        # Prefer the slab thickness measured from parallel planar faces — it
        # excludes the boss/sweep/loft height that bbox would otherwise add.
        # Sanity floor — a detected slab thickness < bbox_h / 10 is almost
        # always a paper-thin shell artefact (mesh→BREP open shell whose
        # front and back faces sit ~0.03 mm apart). Reject that and use the
        # bbox z-extent instead.
        #
        # CORPUS-DRIFT GATE — corpus parts (real CAD models) frequently
        # report a base_thickness that captures a PCB-pad / mounting-flange
        # slab much thinner than the actual outer envelope (e.g.
        # CP_Elec_3x5.4 reports slab=3.3 mm for a 5.4 mm-tall capacitor
        # body; Ventilator reports slab=8.6 mm for an 18 mm-tall fan
        # rotor; linkrods reports slab=1.0 mm for a 2.0 mm slab). The slab
        # approximation truncates the regen bbox in Z, inflating
        # bbox-volume drift in the corpus metric from ~5% to 30-76%.
        #
        # We fall back to bbox_h when the body has NO catalog feature
        # capable of extending the regen Z extent above the slab top —
        # i.e. no TOP-FACE bosses (centre near zmax), no ribs, no sweep
        # features and no loft features. INTERNAL bosses (centre far below
        # zmax) are filtered downstream and never extend Z, so they don't
        # count for this gate. In the no-extender case the slab is the
        # ENTIRE plan in terms of Z extent and using bbox_h is the only
        # way to match the orig bbox. When such features ARE present
        # (synth fixtures, demo plans) the slab + feature combination
        # already gives correct Z so the slab stays.
        # COMPLEX-CAD pass-7: use the module-level _BOSS_TOP_ANCHOR_RATIO
        _BOSS_TOP_ANCHOR_RATIO_GATE = _BOSS_TOP_ANCHOR_RATIO
        zspan_for_gate = max(bbox_h, 1e-3)
        top_face_bosses = []
        for _b in (catalog.get("bosses") or []):
            try:
                _c = _b.get("center") or [0.0, 0.0, 0.0]
                _cz = float(_c[2])
                if (zmax - _cz) <= _BOSS_TOP_ANCHOR_RATIO_GATE * zspan_for_gate:
                    top_face_bosses.append(_b)
            except Exception:
                continue
        # PACK B drift fix — only count ribs as a Z-extender when they
        # don't all collapse to one signature. The rib detector regularly
        # mistakes pin shaft cylinders on KiCad parts (PinHeader, button)
        # for ribs and emits 10-20 identical entries. Those are dropped
        # later by the rib-dedup pass, so they MUST NOT influence the
        # base-height choice here — otherwise has_z_extender stays True
        # and the slab clamps base_h to base_thickness (≈ 2.54 mm) even
        # though the orig bbox is 11.54 mm tall.
        _raw_ribs = catalog.get("ribs") or []
        _rib_sig_count: set[tuple] = set()
        for _rb in _raw_ribs:
            _rib_sig_count.add((
                int(round(float(_rb.get("length_mm") or 0.0) * 100)),
                int(round(float(_rb.get("thickness_mm") or 0.0) * 100)),
                int(round(float(_rb.get("height_mm") or 0.0) * 100)),
            ))
        _real_ribs_likely = bool(_raw_ribs) and not (
            len(_raw_ribs) >= 2 and len(_rib_sig_count) == 1
        )
        has_z_extender = bool(
            top_face_bosses
            or _real_ribs_likely
            or (catalog.get("sweep_features") or [])
            or (catalog.get("loft_features") or [])
        )
        # Z-DOMINANT EXTENT — when bbox_h is the LARGEST extent of the
        # bbox, the body is "taller than wide" and the slab approximation
        # is almost certainly wrong (e.g. CP_Elec_3x5.4: 5.4 mm tall in a
        # 3.8 × 3.31 footprint reports slab=3.3 mm but the cap cylinder
        # dominates Z). Force bbox_h in that case.
        z_dominant = bbox_h >= max(base_l, base_w)
        # SLAB-FITS-VOLUME — when the slab box (base_l × base_w ×
        # base_thickness) is within ~30% of the body's actual volume, the
        # slab approximation captures the body well (synth fixtures
        # simple_watch and simple_watch_housing_only are designed this
        # way: orig vol ≈ 0.9 × slab_box vol). For corpus parts where the
        # slab is much smaller than the body (Ventilator: slab_box covers
        # 56% of orig vol; linkrods: 51%; CP_Elec_3x5.4: 94% — but
        # Z-dominant trips first), the slab is wrong and we use bbox_h.
        body_vol = _body_volume_mm3(body)
        slab_vol = float(base_l) * float(base_w) * float(base_thickness or 0.0)
        slab_fits_volume = False
        if body_vol is not None and slab_vol > 0.0:
            # Slab fits when body volume is ≥ 70% of the slab box AND
            # ≤ 105% (only a small overshoot for rounding — a body whose
            # volume exceeds the slab box clearly extends ABOVE the slab,
            # e.g. as1-oc-214 ratio=1.27 means 27% more material lives
            # above slab top: NOT a slab body).
            _ratio = body_vol / slab_vol
            # PACK B drift fix — tightened from [0.70, 1.05]. A ratio at or
            # above 1.0 means the body has material extending ABOVE the
            # slab top (e.g. BGA-49: ratio=1.05 from solder balls; CP_Elec:
            # cylinder dominates). Slab is a better match only when the
            # body fits inside the slab box with little headroom — tighten
            # the upper bound so true near-bbox slab bodies still match
            # (simple_watch / housing_only stay at ratio ≈ 0.9).
            slab_fits_volume = 0.85 <= _ratio <= 1.00
        # PACK B drift fix — tightened the no-extender slab acceptance
        # threshold from 0.70 to 0.95. When NO Z-extender feature exists
        # (no top bosses, ribs, sweeps, lofts) the placeholder slab IS
        # the entire plan in Z — using a 70-95% slab leaves the regen
        # 5-30% shorter than orig (e.g. BGA-49: slab=1.7 mm vs
        # bbox_h=2.11 mm → 19.6% drift). Slab geometry that matches the
        # bbox within 5% is fine; below that, prefer bbox_h.
        _slab_near_bbox = (
            base_thickness is not None
            and float(base_thickness) >= 0.95 * bbox_h
        )
        if (
            base_thickness is not None
            and bbox_h / 10.0 <= float(base_thickness) <= bbox_h
            and not z_dominant
            and (
                has_z_extender
                or _slab_near_bbox
                or slab_fits_volume
            )
        ):
            base_h = float(base_thickness)
        else:
            base_h = bbox_h
    else:
        base_l, base_w, base_h = 50.0, 50.0, 10.0

    steps: list[dict] = []
    plan_description: str | None = None

    # 1. base shape ────────────────────────────────────────────────────────
    #    Three modes:
    #      - "box"            : axis-aligned bbox-sized box (default; loses
    #                           rounded corners and outer contour but stays
    #                           fully parametric and self-contained).
    #      - "import_step"    : write the original body to a STEP file under
    #                           run_logs/_tmp/<plan_name>_base.step and emit
    #                           an import_step step pointing to it. Preserves
    #                           true outer geometry at the cost of being
    #                           non-parametric.
    #      - "preserve_brep"  : skip s_base entirely. The executor must
    #                           receive the original body via the
    #                           ``initial_body`` kwarg.
    if base_step_kind == "import_step":
        step_path = _write_body_as_step(body, plan_name)
        if step_path is not None:
            steps.append(_new_step("s_base", "import_step", {
                "path": step_path,
                "scale": 1.0,
            }))
        else:
            # Fallback: write failed (no body, or OCCT export error) →
            # degrade gracefully to the bbox box so the plan still runs.
            steps.append(_new_step("s_base", "box", {
                "length_mm": base_l,
                "width_mm": base_w,
                "height_mm": base_h,
            }))
    elif base_step_kind == "preserve_brep":
        # No s_base step. Caller must supply ``initial_body`` to
        # PlanExecutor.run() so the first non-base step receives the
        # original BREP surface intact.
        plan_description = (
            "base_step_kind=preserve_brep — this plan omits s_base. The "
            "executor must be invoked with PlanExecutor.run(initial_body=...) "
            "so the original outer BREP surface is preserved."
        )
    else:
        # Default — pick principled non-prismatic base when the input body's
        # face inventory strongly suggests cylinder / sphere / torus. Falls
        # back to "box" when topology is generic. All thresholds are pure
        # area ratios; NO per-file hardcoding.
        picked_skill, picked_args = _pick_base_shape(
            body, base_l, base_w, base_h, bbox,
        )
        steps.append(_new_step("s_base", picked_skill, picked_args))

    # Coordinate-frame shift: catalog features are in the original body's
    # world frame, but the ``box`` placeholder is XY-centred at the origin
    # with its bottom at Z=0. Translate world coords by (-cx, -cy, -zmin) so
    # downstream pocket / hole / boss steps land INSIDE the placeholder box.
    # For ``import_step`` and ``preserve_brep`` modes the original world frame
    # is preserved so no shift is applied.
    if base_step_kind == "box":
        feat_shift = _world_to_box_shift(bbox)
    else:
        feat_shift = (0.0, 0.0, 0.0)

    # 2. Pockets, largest top_d first ──────────────────────────────────────
    #    Skip pockets whose axis is diagonal — those are detector artefacts
    #    (chains of unrelated cylindrical/planar faces grouped by adjacency)
    #    and emit a huge spurious extrude_pocket that inflates volume drift.
    #
    #    Before emitting the generic extrude_pocket / hole step we try the
    #    specialized matchers (magnet disc → magnet_pocket_axial, bearing
    #    seat → bearing_bore). Both gated by confidence >= 0.6 so noisy
    #    pockets fall through to the generic path.
    # COMPLEX-CAD fix (2026-06-08): used to drop non-Z pockets when primary
    # axis was X or Y (PACK B comment). Now that _heuristic_named_face
    # supports front/back/left/right and _pick_face_selector picks the
    # correct face from axis_dir, side-drilled pockets can be emitted
    # against their natural entry face. Keep _pocket_is_axis_aligned to
    # filter genuine diagonals.
    _pockets_axis_filtered = [p for p in pockets if _pocket_is_axis_aligned(p)]
    # COMPLEX-CAD fix (2026-06-08): dedup pockets that share (axis_origin,
    # axis_dir, top_d, depth) within tolerance. The as1_pe_203 catalog
    # repeatedly emits axis_origin = body centroid as a fallback for
    # poorly-resolved features, producing many "pockets" that all collapse
    # to the same face-centroid prism. Without this, only the first cut
    # removes material and the rest become zero-delta SKIPs.
    # COMPLEX-CAD pass-6 dehardcode (2026-06-09):
    # XY tolerance derived from bbox diagonal (0.2 % matches the
    # adaptive xyz tolerance in feature_fidelity_diff); dim bucket uses
    # FIXED reference bucket sizes so the quotient is bounded — the old
    # code's `top_d / max(top_d * 0.05, 1.0)` collapsed every pocket with
    # top_d > 20 mm to bucket ~20 (since the divisor grew with top_d).
    try:
        _bbox_diag_for_dedup = (
            (float(bbox[3]) - float(bbox[0])) ** 2
            + (float(bbox[4]) - float(bbox[1])) ** 2
            + (float(bbox[5]) - float(bbox[2])) ** 2
        ) ** 0.5 if bbox is not None else 0.0
    except Exception:
        _bbox_diag_for_dedup = 0.0
    _DEDUP_XY_MM = max(1.0, _bbox_diag_for_dedup * 0.002)

    def _pocket_key(p: dict) -> tuple:
        ao = p.get("axis_origin") or [0, 0, 0]
        ad = p.get("axis_dir") or [0, 0, 1]
        # Round each scalar to the nearest _DEDUP_XY_MM bucket; for dims
        # use 5 % of the dim ITSELF (no division-by-self). Two pockets
        # cluster only if their top_d values are within 5 % of each other,
        # which holds across all scales.
        td = float(p.get("top_d_mm") or 0)
        dp = float(p.get("depth_mm") or 0)
        td_bucket = round(td * 20.0) if td > 0 else 0  # 5 % buckets ↔ ×20
        dp_bucket = round(dp * 20.0) if dp > 0 else 0
        return (
            round(float(ao[0]) / _DEDUP_XY_MM),
            round(float(ao[1]) / _DEDUP_XY_MM),
            round(float(ao[2]) / _DEDUP_XY_MM),
            round(float(ad[0])),
            round(float(ad[1])),
            round(float(ad[2])),
            td_bucket,
            dp_bucket,
        )

    _seen_pocket_keys: set = set()
    _deduped: list = []
    for p in _pockets_axis_filtered:
        k = _pocket_key(p)
        if k in _seen_pocket_keys:
            continue
        _seen_pocket_keys.add(k)
        _deduped.append(p)
    pockets_sorted = sorted(
        _deduped,
        key=lambda p: -float(p.get("top_d_mm") or 0.0),
    )
    magnet_idx = 0
    bearing_idx = 0
    for i, p in enumerate(pockets_sorted):
        # Drop pockets whose predicted cut volume is below the
        # volume_decreased PostCondition threshold. Without this guard,
        # sub-mm catalog noise on tiny parts (e.g. 0402 capacitor terminal
        # pads with d≈0.04 mm, depth≈0.2 mm → ~0.00025 mm³) produces a hole
        # step that cuts a geometrically valid but sub-threshold sliver,
        # tripping the PostCondition and FAILing the entire plan.
        _td = float(p.get("top_d_mm") or 0.0)
        _dp = float(p.get("depth_mm") or 0.0)
        if _predicted_cylinder_volume_mm3(_td, _dp) < _MIN_EMITTED_CUT_MM3:
            continue
        # PACK B drift fix — drop pockets whose top_d is comparable to the
        # body's smallest XY extent. The pocket detector occasionally
        # walks the entire body silhouette and reports a pocket whose
        # outer cap spans the full slab (e.g. PinHeader: top_d=12.7 mm on
        # a 5.08 × 12.7 slab; the placeholder rectangular sketch then
        # cuts the entire slab away, collapsing regen Z drift to >99%).
        # We treat top_d ≥ 0.90 × min(body_l, body_w) as a silhouette
        # detection and skip emission — the slab alone is a closer match
        # to the original than slab-minus-silhouette.
        # COMPLEX-CAD fix (2026-06-08): silhouette guard now tests the
        # pocket against the entry FACE's in-plane extents (perpendicular
        # to axis_dir), not just XY. The previous test compared top_d to
        # min(body_l, body_w), so a side-face pocket whose top_d spans
        # the body's HEIGHT (not its XY width) — e.g. as1-oc-214's
        # spurious 100×100 pockets on a 200×150×84 part with face_min=84
        # — silently passed and produced large erroneous side cuts that
        # ate adjacent real pockets via zero-delta SKIP.
        if bbox is not None and _td > 0.0:
            try:
                _bl = float(bbox[3]) - float(bbox[0])
                _bw = float(bbox[4]) - float(bbox[1])
                _bh = float(bbox[5]) - float(bbox[2])
                _extents = [_bl, _bw, _bh]
                _sel = _dominant_axis_sign(p.get("axis_dir") or [0, 0, 1])
                if _sel is not None:
                    _axis_idx, _ = _sel
                    _in_plane = [_extents[k] for k in range(3) if k != _axis_idx]
                    _face_min = min(_in_plane)
                else:
                    _face_min = min(_bl, _bw)
                if _face_min > 0.0 and _td >= _POCKET_SILHOUETTE_FRAC * _face_min:
                    continue
                # Also drop pockets whose depth eats most of the body
                # along the drill axis — face-spanning artefact.
                if _sel is not None:
                    _axis_extent = _extents[_sel[0]]
                    if _axis_extent > 0.0 and float(p.get("depth_mm") or 0) >= _POCKET_SILHOUETTE_FRAC * _axis_extent:
                        continue
            except Exception:
                pass
        bearing_match = _match_bearing_bore(p)
        if bearing_match is not None:
            spec, _conf = bearing_match
            steps.append(_bearing_bore_step(bearing_idx, p, spec, shift=feat_shift))
            bearing_idx += 1
            continue
        magnet_match = _match_magnet_pocket(p)
        if magnet_match is not None:
            spec, _conf = magnet_match
            steps.append(
                _magnet_pocket_step(magnet_idx, p, spec, bbox=bbox, shift=feat_shift)
            )
            magnet_idx += 1
            continue
        steps.append(_pocket_step(i, p, bbox=bbox, shift=feat_shift))

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
                _o_ring_groove_step(
                    oring_idx, feat, outer, inner, depth,
                    bbox=bbox, shift=feat_shift,
                )
            )
            oring_idx += 1
        else:
            steps.append(_revolve_pocket_step(i, feat, shift=feat_shift))

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

    # INTERNAL-BOSS GUARD — mounting_pad always grows ABOVE the face it
    # anchors to (face_named="top" → the current top face of the body). So
    # any boss whose centre sits well below bbox.zmax is, by definition, an
    # INTERNAL cylinder fragment that the detector mis-classified rather
    # than a real protrusion off the top face. Emitting it puts a stacking
    # column above the slab and inflates regen Z (e.g. as1-oc-214 three
    # internal-z bosses pushed regen Z from 84 mm to 144 mm; linkrods one
    # side-protrusion at z=1 mm pushed regen Z from 2 mm to 3 mm).
    #
    # Real top-face bosses have center.z very close to bbox.zmax (because
    # the cylinder anchor sits ON the top face). We treat any boss whose
    # centre is more than ~10% of bbox.zmax below zmax as internal and
    # skip it. This is conservative — top-anchored bosses with measurable
    # height still have center.z within a small fraction of zmax.
    # COMPLEX-CAD pass-7: use module-level _BOSS_TOP_ANCHOR_RATIO (line ~66)
    def _boss_is_internal(b: dict) -> bool:
        if bbox is None:
            return False
        try:
            center = b.get("center") or [0.0, 0.0, 0.0]
            cz = float(center[2])
            zmin, zmax = float(bbox[2]), float(bbox[5])
            zspan = max(zmax - zmin, 1e-3)
            # Skip if centre sits more than 10% of bbox.Z below the top.
            if (zmax - cz) > _BOSS_TOP_ANCHOR_RATIO * zspan:
                return True
            return False
        except Exception:
            return False

    # PACK A — non-Z primary axis suppression for bosses. mounting_pad
    # always grows +Z from the resolved face; on bodies whose primary
    # feature axis is X or Y the "bosses" the detector emits are usually
    # internal cylinder fragments mis-classified as protrusions, and
    # growing +Z from a non-existent top face pushes the pad outside
    # the body bbox (see USB-A: a side-anchored pad at (0,-7.43) extends
    # bbox Y from 15.26 to 15.47 mm). Drop all bosses when primary axis
    # isn't Z so the regen bbox stays bounded.
    if primary_axis != "Z":
        bosses = []
    bosses_sorted = sorted(
        [
            b for b in bosses
            if not _boss_is_duplicate(b) and not _boss_is_internal(b)
        ],
        key=lambda b: -float(b.get("height_mm") or 0.0),
    )
    for i, b in enumerate(bosses_sorted):
        steps.append(_boss_step(i, b, bbox=bbox, shift=feat_shift))

    # 4. Lugs ──────────────────────────────────────────────────────────────
    for i, lg in enumerate(lugs):
        steps.append(_lug_step(i, lg))

    # 5. Ribs ──────────────────────────────────────────────────────────────
    #    Anchor on top face of the placeholder box (or original body top
    #    when preserve_brep / import_step). For "box" mode the slab top is
    #    at z=base_h; for the other modes we use the bbox z-max (already in
    #    world coords, no shift needed since feat_shift is identity there).
    if base_step_kind == "box":
        rib_top_z = float(base_h)
    elif bbox is not None:
        rib_top_z = float(bbox[5])
    else:
        rib_top_z = 0.0
    # PACK A — non-Z primary axis suppression. When the body's primary
    # feature axis is X or Y (e.g. KiCad USB_A_Horizontal connector whose
    # holes drill into the side faces), the rib detector picks up the
    # body's INTERNAL planar shelf as a "rib" but the rib_step skill anchors
    # every rib at the slab-top centre, growing +Z above the placeholder
    # box and inflating Z drift (USB-A: 1 rib of height 1.06 mm pushes
    # regen Z from 11.53 to 12.59 → 10.7 % drift). When the primary axis
    # isn't Z we drop all ribs as detector noise.
    if primary_axis != "Z":
        ribs = []
    # PACK B drift fix — dedup ribs by (length, thickness, height) signature
    # before emission. The rib detector mistakes parallel cylinder shafts
    # (e.g. PinHeader pin legs, button leg pairs) as ribs and emits 10-20
    # identical entries that all anchor at the same XY centre, stacking
    # height on top of the slab and inflating Z drift dramatically. Real
    # ribs vary in position so an identical (l, t, h) triple is almost
    # certainly noise.
    _seen_rib_sig: set[tuple[int, int, int]] = set()
    _ribs_dedup: list[dict] = []
    for _rb in ribs:
        sig = (
            int(round(float(_rb.get("length_mm") or 0.0) * 100)),
            int(round(float(_rb.get("thickness_mm") or 0.0) * 100)),
            int(round(float(_rb.get("height_mm") or 0.0) * 100)),
        )
        if sig in _seen_rib_sig:
            continue
        _seen_rib_sig.add(sig)
        _ribs_dedup.append(_rb)
    # When the detector returns ≥2 identical-signature ribs we treat the
    # whole batch as a noise cluster and drop them entirely. A real design
    # rarely repeats the exact same rib geometry at the same anchor — and
    # the rib_step skill anchors every rib at the slab-top centre, so
    # multiple identical ribs would stack on top of each other and inflate
    # Z drift.
    if len(ribs) >= 2 and len(_ribs_dedup) == 1:
        _ribs_dedup = []
    for i, rb in enumerate(_ribs_dedup):
        steps.append(_rib_step(i, rb, base_top_z=rib_top_z))

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
        # PACK F — pattern skills cap profile_diameter at 100 mm. Big seed
        # holes (≥100 mm) come from misclassified bbox-scale features on
        # large assemblies; clamp so Args validates and let the executor's
        # zero-delta-skip catch the cut if it makes no sense.
        if seed_diam > 100.0:
            seed_diam = 100.0
        elif seed_diam <= 0.0:
            seed_diam = 3.4
        seed_depth = float(seed_hole.get("depth_mm") or 5.0)

        # Sub-threshold guard mirrors the per-hole/pocket loop: if the
        # predicted cut volume across the pattern is below the
        # volume_decreased PostCondition floor, skip emission. This drops
        # tiny circular_pattern / linear_pattern groups that would FAIL on
        # OCCT roundoff (e.g. 0402 capacitor terminal arrays).
        if _predicted_cylinder_volume_mm3(seed_diam, seed_depth) * count < _MIN_EMITTED_CUT_MM3:
            continue
        # COMPLEX-CAD pass-16 (2026-06-09): in box mode (shift != identity)
        # the circular_pattern / linear_pattern skills use face_named, so
        # they put the ring of holes on the placeholder slab's outer face
        # (not the actual catalog feature plane). Ventilator's 13-hole
        # ring at world z=6.93 ended up at the box top face z=18, off
        # by 11 mm. Skip pattern emission in box mode and let the
        # per-hole loop emit individual hole cuts at world coords.
        if feat_shift != (0.0, 0.0, 0.0):
            # Box mode: do not pre-handle pattern holes; per-hole loop
            # will emit each one via the generic 'hole' skill at world
            # coords (pass 12 fix).
            continue
        if pat.get("pattern_kind") == "circular" and count >= 4:
            steps.append(
                _circular_pattern_step(
                    circ_idx, pat, seed_diam, seed_depth,
                    bbox=bbox, shift=feat_shift,
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
                lin_idx, pat, seed_diam, seed_depth,
                bbox=bbox, shift=feat_shift,
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

    # 6b. Symmetry-aware mirror_feature collapse ──────────────────────────
    #     For each high-confidence axis-aligned mirror plane in the catalog,
    #     find pairs of leftover (not-yet-handled) holes that reflect across
    #     the plane and emit one mirror_feature step per pair instead of two
    #     hole steps. mirror_feature only supports circular features with a
    #     YZ/XZ/XY plane name, so diagonal planes and non-hole features are
    #     skipped (per the task spec: "skip emission rather than emit broken
    #     steps").
    #
    #     A body can have multiple high-score mirror planes (e.g. a centred
    #     box is symmetric about BOTH YZ and XZ). The right plane for a
    #     specific feature pair is the one that actually maps one hole onto
    #     the other -- which the score alone cannot tell us. So we enumerate
    #     all qualifying planes and pick the one that produces the most
    #     pairs. Ties break on score (highest first).
    mirror_pair_count = 0
    unhandled_holes: list[dict] = []
    for h in holes:
        # PACK D (SYMMETRY-ON-BASE) — defensive guard. If anything that walks
        # like a base/create step somehow ends up in the ``holes`` list
        # (future refactor, catalog corruption, …), refuse to wrap it. Mirror
        # is for individual modify_* features only — mirroring a base extrude
        # produces a 400× body union, e.g. Ventilator's 45,450% drift.
        if _is_base_or_create_step(h):
            continue
        hid = h.get("id")
        if hid is not None and hid in handled_hole_ids:
            continue
        if any(h is hh for hh in handled_holes_geom):
            continue
        # PACK B — only ±Z holes can be collapsed into mirror_feature when
        # the placeholder box plan is in use; non-Z holes get face_named
        # selectors that can't reach them.
        if primary_axis != "Z" and not _feature_axis_is_z(h):
            continue
        # Sub-threshold cut filter — mirrors the per-hole/per-pocket guard.
        # The mirror_feature step cuts TWO cylinders, so we test 2× the
        # single-cylinder volume against the post-condition floor.
        _diams_m = h.get("diameters_mm") or []
        _d_m = float(min(_diams_m)) if _diams_m else 0.0
        _dp_m = float(h.get("depth_mm") or 0.0)
        if 2.0 * _predicted_cylinder_volume_mm3(_d_m, _dp_m) < _MIN_EMITTED_CUT_MM3:
            continue
        unhandled_holes.append(h)

    best_plane: dict | None = None
    best_pairs: list[tuple[int, int]] = []
    for plane in _qualifying_mirror_planes(symmetries):
        pairs_here = _find_mirrored_hole_pairs(unhandled_holes, plane, bbox=bbox)
        if len(pairs_here) > len(best_pairs):
            best_plane = plane
            best_pairs = pairs_here

    if best_plane is not None and best_pairs:
        mirror_idx = 0
        for local_i, local_j in best_pairs:
            hole_i = unhandled_holes[local_i]
            hole_j = unhandled_holes[local_j]
            # PACK D — second-line guard: never produce a mirror_feature step
            # for anything that looks like a base/create step. The upstream
            # filter already drops these, but emission is the place where a
            # bad wrap actually corrupts the plan, so we re-check here.
            if _is_base_or_create_step(hole_i) or _is_base_or_create_step(hole_j):
                continue
            step = _mirror_feature_step(
                mirror_idx, hole_i, best_plane, bbox=bbox, shift=feat_shift,
            )
            if step is None:
                # mirror_feature args couldn't be satisfied -- leave the pair
                # alone so the per-hole loop emits two separate hole steps.
                continue
            steps.append(step)
            mirror_idx += 1
            mirror_pair_count += 1
            # Mark BOTH holes of the pair as handled so the per-hole loop
            # skips them.
            for h in (hole_i, hole_j):
                hid = h.get("id")
                if hid is not None:
                    handled_hole_ids.add(hid)
                handled_holes_geom.append(h)

    # 7. Holes, largest diameter first ─────────────────────────────────────
    # COMPLEX-CAD fix (2026-06-08): primary_axis != "Z" guard removed.
    # Side-drilled holes now emit against their natural entry face via the
    # generalized _pick_face_selector + _hole_step 6-axis correction.
    holes_sorted = sorted(
        holes,
        key=lambda h: -float(max(h.get("diameters_mm") or [0.0])),
    )
    # When the body's bbox XY aspect ratio is near square (likely a cap or
    # cylinder), holes whose XY position lies outside the inscribed circle
    # of the bbox will graze a region the placeholder box-mode plan ate
    # away via prior pockets (round-cap → boxy-placeholder mismatch). Drop
    # those: the cut volume collapses to roundoff and trips
    # volume_decreased even though the geometry is technically valid.
    inscribed_r: float | None = None
    if base_step_kind == "box" and bbox is not None:
        _hx = (float(bbox[3]) - float(bbox[0])) * 0.5
        _hy = (float(bbox[4]) - float(bbox[1])) * 0.5
        if _hx > 0 and _hy > 0 and 0.85 <= min(_hx, _hy) / max(_hx, _hy) <= 1.18:
            inscribed_r = min(_hx, _hy)

    for i, h in enumerate(holes_sorted):
        hid = h.get("id")
        if hid is not None and hid in handled_hole_ids:
            continue
        # Geometric dedup fallback for holes that lack a stable id.
        if any(h is hh for hh in handled_holes_geom):
            continue
        # Sub-threshold cut filter (mirrors the pocket loop): drop holes
        # whose cylinder cut volume falls below the volume_decreased
        # PostCondition floor. Prevents catalog noise on tiny parts from
        # FAILing the entire plan on roundoff-scale deltas.
        _diams = h.get("diameters_mm") or []
        _d = float(min(_diams)) if _diams else 0.0
        # COMPLEX-CAD fix (2026-06-07): match the _hole_step default depth
        # fallback (line 284: ``depth = float(hole.get("depth_mm") or 5.0)``).
        # Earlier ``or 0.0`` silently dropped any catalog hole whose
        # depth_mm was unset — on a 1018-face industrial part this caused
        # 74 of 143 holes to vanish before plan emission.
        _dp = float(h.get("depth_mm") or 5.0)
        if _predicted_cylinder_volume_mm3(_d, _dp) < _MIN_EMITTED_CUT_MM3:
            continue
        # Round-body guard: skip ±Z holes whose XY position falls outside
        # the bbox's inscribed circle on near-square bodies. The placeholder
        # box covers the full rect bbox, but earlier pocket steps round off
        # the corners — a hole in that already-removed region cuts ~0 mm³.
        if inscribed_r is not None:
            _origin = h.get("axis_origin") or [0.0, 0.0, 0.0]
            _axis = h.get("axis_dir") or [0.0, 0.0, -1.0]
            try:
                _az = abs(float(_axis[2]))
            except Exception:
                _az = 0.0
            if _az > 0.5:
                try:
                    _ox = float(_origin[0])
                    _oy = float(_origin[1])
                    import math as _math
                    _r_xy = _math.sqrt(_ox * _ox + _oy * _oy)
                    # margin = half the hole diameter so a hole tangent to
                    # the inscribed circle still emits.
                    if _r_xy > inscribed_r - 0.5 * _d:
                        continue
                except Exception:
                    pass
        sm = std_matches_by_hole.get(hid)
        steps.append(_hole_step(i, h, sm, bbox=bbox, shift=feat_shift))

    plan: dict[str, Any] = {
        "schema_version": 1,
        "plan_name": plan_name,
        "steps": steps,
        # PACK F — auto-generated RE plans opt into graceful step-failure.
        # A single bad step from catalog noise (Args validation drift,
        # selector miss on a much-mutated body, OCCT roundoff post_condition
        # trip) should not mass-SKIP the remaining steps. Hand-authored
        # plans opt out by leaving the flag at its False default.
        "continue_on_step_failure": True,
    }

    # Compose the plan description from any base-step notes plus the
    # symmetry-collapse summary (when at least one pair was emitted).
    description_parts: list[str] = []
    if plan_description is not None:
        description_parts.append(plan_description)
    if mirror_pair_count > 0:
        description_parts.append(
            f"symmetry-deduplicated: {mirror_pair_count} feature pairs "
            f"collapsed into mirror_feature steps"
        )
    if description_parts:
        plan["description"] = " | ".join(description_parts)

    # COMPLEX-CAD pass-20 (2026-06-10): in box mode, reorder so HOLES
    # are emitted BEFORE pockets. Deep cylinder cuts then carve their
    # full depth first; subsequent pocket cuts hit the post-hole body
    # and naturally produce zero-delta SKIPs only where they ACTUALLY
    # overlap (instead of all pocket cuts running BEFORE the holes and
    # then deep holes carving over them). Same set of cuts, different
    # order — yields the same union in CSG terms but produces a regen
    # body whose pocket geometry the detector can still classify.
    if feat_shift != (0.0, 0.0, 0.0):
        steps_list = plan.get("steps") or []
        base_steps = [s for s in steps_list if s.get("id", "").startswith("s_base")]
        hole_skills = {
            "hole", "clearance_hole", "counterbore_hole",
            "countersink_hole", "tap_drill_hole", "dowel_pin_hole",
            "hole_array",
        }
        hole_steps = [
            s for s in steps_list
            if s not in base_steps and s.get("skill") in hole_skills
        ]
        other_steps = [
            s for s in steps_list
            if s not in base_steps and s.get("skill") not in hole_skills
        ]
        plan["steps"] = base_steps + hole_steps + other_steps

    return plan


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
        base_step_kind: Literal["box", "import_step", "preserve_brep"] = "box"

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

        plan = _build_plan(
            catalog or {},
            body=body,
            base_step_kind=args.base_step_kind,
        )

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
