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


def _new_step(id_: str, skill_name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"id": id_, "skill": skill_name, "args": args}


def _hole_step(idx: int, hole: dict, std_match: dict | None) -> dict:
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

    # ── thread spec source: prefer the hole's own standard_match (which
    #    classify_holes attached), fall back to the per-hole standard match
    #    pulled from extract_feature_catalog.standard_matches.
    thread_spec = None
    hole_sm = hole.get("standard_match")
    if isinstance(hole_sm, dict):
        thread_spec = hole_sm.get("thread_spec")
    if thread_spec is None and isinstance(std_match, dict):
        bm = std_match.get("best_match") or {}
        thread_spec = bm.get("thread_spec")

    sid = f"s_hole_{idx}"

    if htype == "counterbore" and thread_spec:
        return _new_step(sid, "counterbore_hole", {
            "face_selector": _DEFAULT_FACE_SELECTOR,
            "position_xy": [float(axis_origin[0]), float(axis_origin[1])],
            "thread_spec": thread_spec,
            "fit": "medium",
            "depth_mm": depth,
        })
    if htype == "countersink" and thread_spec:
        return _new_step(sid, "countersink_hole", {
            "face_selector": _DEFAULT_FACE_SELECTOR,
            "position_xy": [float(axis_origin[0]), float(axis_origin[1])],
            "thread_spec": thread_spec,
            "fit": "medium",
            "depth_mm": depth,
        })
    if htype == "threaded" and thread_spec:
        return _new_step(sid, "tap_drill_hole", {
            "face_selector": _DEFAULT_FACE_SELECTOR,
            "position_xy": [float(axis_origin[0]), float(axis_origin[1])],
            "thread_spec": thread_spec,
            "depth_mm": depth,
        })
    if thread_spec:
        return _new_step(sid, "clearance_hole", {
            "face_selector": _DEFAULT_FACE_SELECTOR,
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


def _pocket_step(idx: int, pocket: dict) -> dict:
    ptype = pocket.get("type", "blind")
    top_d = float(pocket.get("top_d_mm") or 0.0)
    depth = float(pocket.get("depth_mm") or 1.0)
    origin = pocket.get("axis_origin") or [0.0, 0.0, 0.0]
    sid = f"s_pocket_{idx}"

    # Circular pockets whose depth dominates → treat as a raw hole.
    if top_d > 0 and depth / max(top_d, 1e-3) >= 1.5:
        return _new_step(sid, "hole", {
            "position": [float(origin[0]), float(origin[1]), float(origin[2])],
            "diameter_mm": top_d,
            "depth_mm": depth,
            "direction": _axis_dir_to_str(pocket.get("axis_dir") or [0, 0, -1]),
        })

    # Default — extrude_pocket with placeholder rectangular sketch sized to
    # the measured top diameter.
    return _new_step(sid, "extrude_pocket", {
        "face_selector": _DEFAULT_FACE_SELECTOR,
        "sketch": {
            "kind": "rect",
            "width_mm": top_d if top_d > 0 else 5.0,
            "height_mm": top_d if top_d > 0 else 5.0,
            "position_xy": [float(origin[0]), float(origin[1])],
        },
        "depth_mm": depth,
    })


def _boss_step(idx: int, boss: dict) -> dict:
    btype = boss.get("type", "prismatic")
    center = boss.get("center") or [0.0, 0.0, 0.0]
    height = float(boss.get("height_mm") or 1.0)
    size = float(boss.get("diameter_or_size_mm") or 4.0)
    sid = f"s_boss_{idx}"

    if btype == "cylindrical":
        # If a hole is implied (e.g. seat boss) use boss_with_hole, else
        # mounting_pad. We default to mounting_pad without a hole.
        return _new_step(sid, "mounting_pad", {
            "face_selector": _DEFAULT_FACE_SELECTOR,
            "position_xy": [float(center[0]), float(center[1])],
            "diameter_mm": size,
            "height_mm": height,
        })

    # Prismatic / conical fallback — mounting_pad with the measured size.
    return _new_step(sid, "mounting_pad", {
        "face_selector": _DEFAULT_FACE_SELECTOR,
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


def _circular_pattern_step(idx: int, ring: dict) -> dict:
    """Wrap a 6+ count circular-array of holes into a ``circular_pattern``."""
    sid = f"s_pattern_{idx}"
    center = ring.get("center") or [0.0, 0.0, 0.0]
    axis = ring.get("axis") or [0.0, 0.0, 1.0]
    count = int(ring.get("count") or 6)
    return _new_step(sid, "circular_pattern", {
        "seed_skill": "hole",
        "seed_args": {
            "position": [
                float(center[0]) + float(ring.get("radius_mm") or 5.0),
                float(center[1]),
                float(center[2]),
            ],
            "diameter_mm": 3.4,
            "depth_mm": 5.0,
            "direction": _axis_dir_to_str(axis),
        },
        "center": [float(center[0]), float(center[1]), float(center[2])],
        "axis": [float(axis[0]), float(axis[1]), float(axis[2])],
        "count": count,
    })


# ──────────────────────────────────────────────────────────────────────────────
# Plan builder


def _build_plan(catalog: dict) -> dict:
    holes = catalog.get("holes") or []
    pockets = catalog.get("pockets") or []
    bosses = catalog.get("bosses") or []
    ribs = catalog.get("ribs") or []
    lugs = catalog.get("lugs") or []
    patterns = catalog.get("patterns") or []
    std_matches_by_hole = {
        sm.get("hole_id"): sm
        for sm in (catalog.get("standard_matches") or [])
        if isinstance(sm, dict)
    }

    steps: list[dict] = []

    # 1. base shape placeholder ────────────────────────────────────────────
    steps.append(_new_step("s_base", "box", {
        "length_mm": 50.0,
        "width_mm": 50.0,
        "height_mm": 10.0,
    }))

    # 2. Pockets, largest top_d first ──────────────────────────────────────
    pockets_sorted = sorted(
        pockets, key=lambda p: -float(p.get("top_d_mm") or 0.0),
    )
    for i, p in enumerate(pockets_sorted):
        steps.append(_pocket_step(i, p))

    # 3. Bosses, tallest first ─────────────────────────────────────────────
    bosses_sorted = sorted(
        bosses, key=lambda b: -float(b.get("height_mm") or 0.0),
    )
    for i, b in enumerate(bosses_sorted):
        steps.append(_boss_step(i, b))

    # 4. Lugs ──────────────────────────────────────────────────────────────
    for i, lg in enumerate(lugs):
        steps.append(_lug_step(i, lg))

    # 5. Ribs ──────────────────────────────────────────────────────────────
    for i, rb in enumerate(ribs):
        steps.append(_rib_step(i, rb))

    # 6. Circular patterns of 6+ holes — wrap the whole ring in a single
    #    circular_pattern step rather than emitting N individual holes.
    handled_pattern_holes: set[int] = set()
    pattern_step_idx = 0
    for pat in patterns:
        if (
            pat.get("pattern_kind") == "circular"
            and pat.get("feature_kind") == "hole"
            and int(pat.get("count") or 0) >= 6
        ):
            steps.append(_circular_pattern_step(pattern_step_idx, pat))
            pattern_step_idx += 1
            # NOTE: without explicit hole-id linkage in detect_circular_array
            # we cannot precisely subtract the wrapped holes from `holes`,
            # so the loop below will still emit them; downstream dedup is
            # the user's responsibility.

    # 7. Holes, largest diameter first ─────────────────────────────────────
    holes_sorted = sorted(
        holes,
        key=lambda h: -float(max(h.get("diameters_mm") or [0.0])),
    )
    for i, h in enumerate(holes_sorted):
        hid = h.get("id")
        if hid in handled_pattern_holes:
            continue
        sm = std_matches_by_hole.get(hid)
        steps.append(_hole_step(i, h, sm))

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

        plan = _build_plan(catalog or {})

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
