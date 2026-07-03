"""_mate_persistence — mate records that survive assembly compound rebuilds.

TRACK 3-4 SPIKE (2026-07-03). Empirical findings, pinned by
``tests/test_mate_persistence_spike.py``:

WHERE MATE DATA LIVES TODAY
    Nowhere. mate_axis / mate_planar / mate_concentric / mate_at_distance bake
    a rigid transform into the geometry and return
    ``extras={"component_names": ...}`` — no ``_pd_mates`` attribute, no tags.
    Once the SkillResult extras are gone, the mate is unrecoverable data.

WHAT SURVIVES A COMPOUND REBUILD
    add_component / move_component / mate_* all construct a brand-new
    ``Part(TopoDS_Compound)`` and copy ONLY ``_pd_component_names``. A plain
    body attribute (the ``_pd_pmi_dimensions`` attach pattern the roadmap
    3-4 sketch mentions) is therefore LOST on the very first rebuild — proven
    by the spike. The ONE body-level store that survives automatically is
    ``body._pd_tags``: ``SkillBase.apply`` runs ``propagate_tags`` after every
    skill and re-anchors each ``EntityRef`` onto the nearest output face
    (bbox-center nearest-match, TOL 5 mm).

MECHANISM ADOPTED HERE (GO evidence)
    A mate record rides the tag store under reserved tag names
        ``__mate__:{index}:{type}:{side}:{component}:{area_mm2}``
    with exactly one face ``EntityRef`` per side. Because the dict KEY is
    copied verbatim by ``propagate_tags`` while the ref's center/measure are
    rewritten at every rebuild, the tag NAME is the only immutable slot — so
    the recorded face area is encoded there and used as a consistency guard
    at resolve time.

HONEST LIMITS (all pinned by the spike tests)
    * Faces only. The tag store / resolver do not support edge tags
      (``_resolvers.resolve_edges`` returns [] for kind='tagged'), so
      mate_axis (edge-based) persistence must anchor via an adjacent FACE
      (e.g. the cylindrical face around the axis) — not the edge itself.
    * Sub-TOL (<5 mm) component moves re-anchor correctly (the ref tracks the
      entity, not stale coordinates). Moves beyond TOL do NOT merely drop the
      ref: propagate_tags can silently re-anchor it onto the nearest surviving
      face of ANOTHER component (observed: pin OD face -> plate top face,
      3 mm away, after a 15 mm pin move). ``resolve_mate`` detects this via
      the recorded-area guard and reports ``area_mismatch`` instead of
      returning the wrong face.
    * Coincident mate faces (concentric pin flush inside its hole with equal
      face centroids) collapse under center-only nearest-match: both sides
      resolve to the same face. Same guard detects the collapsed side.
    * Component RENAMING is not supported by the assembly skills; if it ever
      is, the component name embedded in the tag goes stale.

DOF accounting itself is a later track; this module only proves + hardens the
persistence substrate.
"""
from __future__ import annotations

from typing import Any

MATE_TAG_PREFIX = "__mate__"
_SEP = ":"
_SIDES = ("a", "b")


# ---------------------------------------------------------------------------
# tag-name codec — the tag NAME is the only rebuild-immutable slot
# ---------------------------------------------------------------------------

def mate_tag_name(index: int, mate_type: str, side: str,
                  component: str, area_mm2: float) -> str:
    """Encode one mate side as a reserved tag name.

    Raises ValueError when a field would corrupt the ':'-separated encoding.
    """
    for label, value in (("mate_type", mate_type), ("component", component)):
        if _SEP in value:
            raise ValueError(
                f"mate_tag_name: {label} {value!r} must not contain '{_SEP}'"
            )
    if side not in _SIDES:
        raise ValueError(f"mate_tag_name: side must be one of {_SIDES}, got {side!r}")
    return _SEP.join([
        MATE_TAG_PREFIX, str(int(index)), mate_type, side, component,
        f"{float(area_mm2):.3f}",
    ])


def parse_mate_tag(tag_name: str) -> dict[str, Any] | None:
    """Reserved tag name -> field dict, or None when not a mate tag.

    A tag that starts with the reserved prefix but does not parse is a
    corruption — raise, never mask.
    """
    if not isinstance(tag_name, str) or not tag_name.startswith(MATE_TAG_PREFIX + _SEP):
        return None
    parts = tag_name.split(_SEP)
    if len(parts) != 6:
        raise ValueError(f"parse_mate_tag: malformed mate tag {tag_name!r}")
    _, idx_s, mate_type, side, component, area_s = parts
    if side not in _SIDES:
        raise ValueError(f"parse_mate_tag: malformed mate tag {tag_name!r} (side)")
    return {
        "index": int(idx_s),
        "type": mate_type,
        "side": side,
        "component": component,
        "recorded_area_mm2": float(area_s),
    }


# ---------------------------------------------------------------------------
# record / list / resolve
# ---------------------------------------------------------------------------

def _coerce_selector(sel):
    from phone_designer.skills._selectors import SelectorBase, selector_from_dict

    if isinstance(sel, SelectorBase):
        return sel
    if isinstance(sel, dict):
        return selector_from_dict(sel)
    raise TypeError(f"unsupported selector type: {type(sel).__name__}")


def _component_shapes(body) -> dict[str, Any]:
    from phone_designer.skills.assembly._compound import list_components

    comps = dict(list_components(body))
    if not comps:
        raise RuntimeError(
            "mate_persistence: body has no assembly components "
            "(_pd_component_names missing or compound empty)"
        )
    return comps


def _resolve_one_face_in_component(comps: dict[str, Any], component: str, sel):
    from phone_designer.skills._resolvers import resolve_faces

    if component not in comps:
        raise RuntimeError(
            f"mate_persistence: component '{component}' not found "
            f"(known: {sorted(comps)})"
        )
    faces = resolve_faces(comps[component], _coerce_selector(sel))
    if not faces:
        raise RuntimeError(
            f"mate_persistence: selector matched 0 faces on component "
            f"'{component}'"
        )
    return faces[0]


def _next_mate_index(tags: dict) -> int:
    idx = -1
    for name in tags:
        parsed = parse_mate_tag(name)
        if parsed is not None:
            idx = max(idx, parsed["index"])
    return idx + 1


def record_mate(body, mate_type: str, component_a: str, component_b: str,
                face_selector_a, face_selector_b) -> dict[str, Any]:
    """Attach a rebuild-surviving mate record to ``body._pd_tags``.

    Call AFTER the mate skill ran (the mate transform moves component_a; a
    tag recorded before the transform would be dropped or mis-anchored by the
    very rebuild the mate performs).

    Returns the strict-JSON-safe record (same shape as ``list_mates`` entries).
    """
    from phone_designer.skills._history import EntityRef
    from phone_designer.skills._resolvers import _face_area, _face_center
    from phone_designer.skills.compose.tag_face import get_tags, set_tags

    if component_a == component_b:
        raise RuntimeError(
            "mate_persistence: component_a and component_b must be different"
        )
    comps = _component_shapes(body)
    face_a = _resolve_one_face_in_component(comps, component_a, face_selector_a)
    face_b = _resolve_one_face_in_component(comps, component_b, face_selector_b)

    tags = dict(get_tags(body))
    index = _next_mate_index(tags)

    sides: dict[str, dict[str, Any]] = {}
    for side, component, face in (
        ("a", component_a, face_a),
        ("b", component_b, face_b),
    ):
        center = _face_center(face)
        area = _face_area(face)
        name = mate_tag_name(index, mate_type, side, component, area)
        tags[name] = [EntityRef(
            tag=name,
            kind="face",
            bbox_center=(round(center[0], 3), round(center[1], 3),
                         round(center[2], 3)),
            measure=round(area, 3),
        )]
        sides[side] = {
            "component": component,
            "recorded_area_mm2": round(area, 3),
            "current_ref_center": [round(c, 3) for c in center],
            "current_ref_area_mm2": round(area, 3),
        }
    set_tags(body, tags)

    return {
        "index": index,
        "type": mate_type,
        "component_a": component_a,
        "component_b": component_b,
        "sides": sides,
        "complete": True,
    }


def list_mates(body) -> list[dict[str, Any]]:
    """Decode all mate records from the tag store. Strict-JSON-safe.

    A side whose tag was dropped by propagation appears as ``None`` and the
    record reports ``complete: False`` — honest degradation, never invented.
    NOTE: ``current_ref_*`` reflect the (possibly re-anchored) store; only
    ``recorded_area_mm2`` is immutable. Use ``resolve_mate`` for guarded
    entity resolution.
    """
    from phone_designer.skills.compose.tag_face import get_tags

    by_index: dict[int, dict[str, Any]] = {}
    for name, refs in get_tags(body).items():
        parsed = parse_mate_tag(name)
        if parsed is None:
            continue
        rec = by_index.setdefault(parsed["index"], {
            "index": parsed["index"],
            "type": parsed["type"],
            "component_a": None,
            "component_b": None,
            "sides": {"a": None, "b": None},
            "complete": False,
        })
        side = parsed["side"]
        ref = refs[0] if refs else None
        rec["sides"][side] = {
            "component": parsed["component"],
            "recorded_area_mm2": parsed["recorded_area_mm2"],
            "current_ref_center": (
                [float(c) for c in ref.bbox_center] if ref is not None else None
            ),
            "current_ref_area_mm2": (
                float(ref.measure) if ref is not None else None
            ),
        }
        rec[f"component_{side}"] = parsed["component"]
    out = []
    for idx in sorted(by_index):
        rec = by_index[idx]
        rec["complete"] = (
            rec["sides"]["a"] is not None and rec["sides"]["b"] is not None
        )
        out.append(rec)
    return out


def resolve_mate(body, index: int, area_rtol: float = 0.05) -> dict[str, Any]:
    """Re-resolve one mate's faces on the CURRENT compound, with the
    recorded-area consistency guard.

    Per side status:
        ok            — face found, area within ``area_rtol`` of the recorded
                        area (rigid motion preserves face area exactly).
        tag_missing   — the side's tag no longer exists in the store.
        no_face_match — tagged resolver found no face within its 5 mm window.
        area_mismatch — resolver returned a face whose area disagrees with
                        the immutable recorded area: the ref was silently
                        re-anchored onto a DIFFERENT face during a rebuild
                        (the spike's observed cross-component mis-anchor) or
                        collapsed onto its coincident partner face. The wrong
                        face is NOT returned.

    Returns {"index", "sides": {"a": {...}, "b": {...}}, "ok": bool}. Side
    dicts carry a live ``face`` (TopoDS_Face or None) — NOT JSON-safe; strip
    it before serializing.
    """
    from phone_designer.skills._resolvers import _face_area, resolve_faces
    from phone_designer.skills._selectors import TaggedSelector
    from phone_designer.skills.compose.tag_face import get_tags

    shape = body.wrapped if hasattr(body, "wrapped") else body
    tag_by_side: dict[str, tuple[str, dict]] = {}
    for name in get_tags(body):
        parsed = parse_mate_tag(name)
        if parsed is not None and parsed["index"] == int(index):
            tag_by_side[parsed["side"]] = (name, parsed)

    sides: dict[str, dict[str, Any]] = {}
    for side in _SIDES:
        if side not in tag_by_side:
            sides[side] = {
                "status": "tag_missing", "face": None, "component": None,
                "recorded_area_mm2": None, "resolved_area_mm2": None,
            }
            continue
        name, parsed = tag_by_side[side]
        recorded = parsed["recorded_area_mm2"]
        faces = resolve_faces(shape, TaggedSelector(tag=name), body=body)
        if not faces:
            sides[side] = {
                "status": "no_face_match", "face": None,
                "component": parsed["component"],
                "recorded_area_mm2": recorded, "resolved_area_mm2": None,
            }
            continue
        face = faces[0]
        resolved = _face_area(face)
        if recorded > 0 and abs(resolved - recorded) / recorded > area_rtol:
            sides[side] = {
                "status": "area_mismatch", "face": None,
                "component": parsed["component"],
                "recorded_area_mm2": recorded,
                "resolved_area_mm2": round(resolved, 3),
            }
            continue
        sides[side] = {
            "status": "ok", "face": face,
            "component": parsed["component"],
            "recorded_area_mm2": recorded,
            "resolved_area_mm2": round(resolved, 3),
        }
    return {
        "index": int(index),
        "sides": sides,
        "ok": all(s["status"] == "ok" for s in sides.values()),
    }


def component_of_face(body, face) -> str | None:
    """Which assembly component owns ``face`` (TopoDS IsSame identity)."""
    from phone_designer.skills._resolvers import _all_faces
    from phone_designer.skills.assembly._compound import list_components

    for name, sub in list_components(body):
        for f in _all_faces(sub):
            if f.IsSame(face):
                return name
    return None
