"""auto_place_fasteners — atomic assembly automation.

두 component 사이에서 ``collinear (공축) hole pair`` 를 자동 탐지하고 각 pair
에 cylindrical fastener body 를 추가한다. 일반적 시나리오: 상판/하판 모두에
같은 위치로 M3 clearance hole 이 뚫려 있으면 그 자리에 M3 screw 를 박는다.

Inputs
------
- hole_face_selector_a / hole_face_selector_b: cylindrical face 를 가리키는
  SelectorRef (예: ``faces_by_area``, ``faces_by_normal`` 등). 각 component
  안에서 cylinder face 들을 찾아 그것들을 ``홀 후보`` 로 간주한다. selector 가
  None / 빈 결과면 component 내 모든 cylindrical face 를 후보로 한다.
- fastener_thread_spec: "M3" 같은 표준 thread 사양. catalogs/standards/
  threads_metric.yaml 에서 outer/length/머리지름을 찾는다. 미지정 spec 이면
  기본 3 mm.

Algorithm
---------
1. component_a / component_b 의 모든 cylindrical face 를 추출.
   selector 가 지정되면 selector 결과로 필터.
2. cylinder face 마다 (axis_origin, axis_dir, radius, axis_span) 기록.
3. component_a x component_b 의 모든 pair 에 대해 _axes_collinear (소차이
   허용) 검사. collinear 면 pair 의 axis_span union 을 fastener 길이로 사용.
4. fastener body = thread outer_d 기준 cylinder (volume_increases).
5. fastener 들을 새로운 sub-component 로 compound 에 추가 (이름: "<thread>_<i>").

extras
------
``fastener_count``, ``fasteners`` (list of {name, axis_origin, axis_dir, length_mm,
diameter_mm}), ``component_names`` (전체 갱신).
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.assembly._compound import (
    build_compound,
    get_component_names,
    iter_compound_components,
)


# ---------------------------------------------------------------------------
# Catalog loader (inline, per pack rules)


def _load(family, name):
    import yaml, pathlib
    # parents: [0]=assembly, [1]=skills, [2]=phone_designer, [3]=src, [4]=repo root
    root = pathlib.Path(__file__).resolve().parents[4]
    path = root / "catalogs" / family / f"{name}.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text())


# ---------------------------------------------------------------------------
# Geometry helpers


def _all_faces(shape) -> list:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_MapOfShape

    seen = TopTools_MapOfShape()
    out = []
    it = TopExp_Explorer(shape, TopAbs_FACE)
    while it.More():
        f = it.Current()
        if not seen.Contains(f):
            seen.Add(f)
            out.append(TopoDS.Face_s(f))
        it.Next()
    return out


def _cylinder_info(face):
    """(origin, axis_dir_unit, radius) for a cylindrical face, else None."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Cylinder:
        return None
    cyl = surf.Cylinder()
    loc = cyl.Location()
    d = cyl.Axis().Direction()
    dx, dy, dz = d.X(), d.Y(), d.Z()
    mag = math.sqrt(dx * dx + dy * dy + dz * dz)
    if mag > 1e-12:
        dx, dy, dz = dx / mag, dy / mag, dz / mag
    return (
        (loc.X(), loc.Y(), loc.Z()),
        (dx, dy, dz),
        float(cyl.Radius()),
    )


def _face_bbox(face):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.Add_s(face, bb)
    except Exception:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    if bb.IsVoid():
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return (xmin, ymin, zmin), (xmax, ymax, zmax)


def _project(point, origin, axis) -> float:
    return (
        (point[0] - origin[0]) * axis[0]
        + (point[1] - origin[1]) * axis[1]
        + (point[2] - origin[2]) * axis[2]
    )


def _axes_collinear(o1, d1, o2, d2, angle_tol_deg=8.0, line_tol_mm=0.5) -> bool:
    dot = d1[0] * d2[0] + d1[1] * d2[1] + d1[2] * d2[2]
    if abs(abs(dot) - 1.0) > math.sin(math.radians(angle_tol_deg)):
        return False
    vx, vy, vz = o2[0] - o1[0], o2[1] - o1[1], o2[2] - o1[2]
    cx = vy * d1[2] - vz * d1[1]
    cy = vz * d1[0] - vx * d1[2]
    cz = vx * d1[1] - vy * d1[0]
    return math.sqrt(cx * cx + cy * cy + cz * cz) <= line_tol_mm


def _face_axis_span(face, axis_origin, axis_dir) -> tuple[float, float]:
    """Project the face's bbox corners onto the axis; return (lo, hi)."""
    bb_min, bb_max = _face_bbox(face)
    corners = []
    for x in (bb_min[0], bb_max[0]):
        for y in (bb_min[1], bb_max[1]):
            for z in (bb_min[2], bb_max[2]):
                corners.append((x, y, z))
    projs = [_project(c, axis_origin, axis_dir) for c in corners]
    return min(projs), max(projs)


def _extract_cylindrical_holes(
    component_shape, selector: SelectorRef | None,
) -> list[tuple[tuple, tuple, float, tuple[float, float]]]:
    """component 의 cylindrical face 들을 (origin, axis_dir, radius, span) 으로.

    selector 가 주면 그 결과 face 들 중 cylinder 만 남김. 결과가 비면 fallback
    으로 component 의 모든 cylindrical face 사용.
    """
    candidates: list = []
    if selector is not None:
        try:
            from phone_designer.skills._resolvers import resolve_faces
            sel_faces = resolve_faces(component_shape, selector)
        except Exception:
            sel_faces = []
        candidates = sel_faces or _all_faces(component_shape)
    else:
        candidates = _all_faces(component_shape)

    records: list = []
    for f in candidates:
        info = _cylinder_info(f)
        if info is None:
            continue
        o, d, r = info
        span = _face_axis_span(f, o, d)
        records.append((o, d, r, span))
    return records


def _group_by_axis(
    records: list[tuple[tuple, tuple, float, tuple[float, float]]],
) -> list[tuple[tuple, tuple, float, tuple[float, float]]]:
    """공축인 cylinder face 들을 하나의 ``hole`` 로 병합."""
    groups: list[list[int]] = []
    group_axes: list[tuple[tuple, tuple]] = []
    for i, (o, d, _r, _span) in enumerate(records):
        placed = False
        for gi, (go, gd) in enumerate(group_axes):
            if _axes_collinear(go, gd, o, d):
                groups[gi].append(i)
                placed = True
                break
        if not placed:
            groups.append([i])
            group_axes.append((o, d))

    merged: list[tuple[tuple, tuple, float, tuple[float, float]]] = []
    for gi, idxs in enumerate(groups):
        go, gd = group_axes[gi]
        members = [records[i] for i in idxs]
        # smallest cylinder radius = clearance shaft radius for this hole
        r_min = min(m[2] for m in members)
        lo = min(m[3][0] for m in members)
        hi = max(m[3][1] for m in members)
        merged.append((go, gd, r_min, (lo, hi)))
    return merged


# ---------------------------------------------------------------------------
# Thread spec lookup


def _thread_outer_diameter(spec: str) -> float:
    """Look spec up in catalogs/standards/threads_metric.yaml; default 3 mm."""
    data = _load("standards", "threads_metric")
    if not data:
        return 3.0
    threads = data.get("threads", {})
    entry = threads.get(spec)
    if entry is None:
        return 3.0
    return float(entry.get("outer_d_mm", 3.0))


# ---------------------------------------------------------------------------
# Fastener body construction


def _make_fastener_cylinder(
    axis_origin: tuple[float, float, float],
    axis_dir: tuple[float, float, float],
    radius_mm: float,
    length_mm: float,
    proj_lo: float,
):
    """Build a TopoDS_Shape cylinder along (origin, axis_dir) starting at
    proj_lo along the axis from origin, of given length and radius.

    Implementation: BRepPrimAPI_MakeCylinder builds a +Z-aligned cylinder; we
    place it via a gp_Ax2 anchored at start_point with axis_dir.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    start = (
        axis_origin[0] + proj_lo * axis_dir[0],
        axis_origin[1] + proj_lo * axis_dir[1],
        axis_origin[2] + proj_lo * axis_dir[2],
    )
    ax2 = gp_Ax2(
        gp_Pnt(start[0], start[1], start[2]),
        gp_Dir(axis_dir[0], axis_dir[1], axis_dir[2]),
    )
    mk = BRepPrimAPI_MakeCylinder(ax2, radius_mm, length_mm)
    return mk.Shape()


# ---------------------------------------------------------------------------
# Skill


@skill(
    name="auto_place_fasteners",
    category="assembly",
    level="atomic",
    summary="Detect collinear cylindrical hole pairs between two named components "
            "and add a fastener (cylinder) body at each pair. Thread spec (e.g. "
            "M3) drives the fastener outer diameter via the metric thread catalog.",
    selector_kinds=["faces"],
    history_rules={"output_component": HistoryRule.GENERATED_NEW},
    produces_features=["fastener_pairs"],
    preserves=["assembly_topology"],
    manufacturing={},
    failure_modes=[
        "fm.component_not_found",
        "fm.no_hole_pairs",
    ],
    cost_hint=0.45,
    post_conditions=[PostCondition(kind="body_present")],
)
class AutoPlaceFasteners(SkillBase):
    class Args(BaseModel):
        component_a: str = Field(min_length=1)
        component_b: str = Field(min_length=1)
        hole_face_selector_a: SelectorRef | None = None
        hole_face_selector_b: SelectorRef | None = None
        fastener_thread_spec: str = Field(default="M3")
        name_prefix: str = Field(default="fastener", min_length=1)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        if body is None:
            raise RuntimeError("auto_place_fasteners: body is None")
        shape = body.wrapped if hasattr(body, "wrapped") else body
        prior_names = get_component_names(body)
        components = list(iter_compound_components(shape, prior_names))
        name_to_shape = {n: s for n, s in components}
        names = [n for n, _ in components]

        if args.component_a not in name_to_shape:
            raise RuntimeError(
                f"auto_place_fasteners: component_a '{args.component_a}' not found "
                f"(known: {names})"
            )
        if args.component_b not in name_to_shape:
            raise RuntimeError(
                f"auto_place_fasteners: component_b '{args.component_b}' not found "
                f"(known: {names})"
            )

        shape_a = name_to_shape[args.component_a]
        shape_b = name_to_shape[args.component_b]

        # Extract cylindrical hole records (grouped by axis).
        recs_a = _group_by_axis(
            _extract_cylindrical_holes(shape_a, args.hole_face_selector_a)
        )
        recs_b = _group_by_axis(
            _extract_cylindrical_holes(shape_b, args.hole_face_selector_b)
        )

        # Find collinear pairs.
        pairs: list[tuple[tuple, tuple, float, tuple[float, float]]] = []
        used_b: set[int] = set()
        for (oa, da, ra, span_a) in recs_a:
            for j, (ob, db, rb, span_b) in enumerate(recs_b):
                if j in used_b:
                    continue
                if not _axes_collinear(oa, da, ob, db):
                    continue
                # Merge the two spans along axis a.
                lo_a, hi_a = span_a
                # Project b's span endpoints onto axis a.
                start_b = (
                    ob[0] + span_b[0] * db[0],
                    ob[1] + span_b[0] * db[1],
                    ob[2] + span_b[0] * db[2],
                )
                end_b = (
                    ob[0] + span_b[1] * db[0],
                    ob[1] + span_b[1] * db[1],
                    ob[2] + span_b[1] * db[2],
                )
                pb_lo = _project(start_b, oa, da)
                pb_hi = _project(end_b, oa, da)
                lo = min(lo_a, pb_lo, pb_hi)
                hi = max(hi_a, pb_lo, pb_hi)
                shaft_r = min(ra, rb)
                pairs.append(((oa[0], oa[1], oa[2]),
                              (da[0], da[1], da[2]),
                              shaft_r,
                              (lo, hi)))
                used_b.add(j)
                break

        if not pairs:
            raise RuntimeError(
                "auto_place_fasteners: no collinear hole pairs found between "
                f"'{args.component_a}' and '{args.component_b}'"
            )

        # Fastener outer diameter from catalog (use thread outer_d as cylinder dia
        # so the cylinder is slightly larger than the clearance hole and adds a
        # measurable positive volume — the body of the bolt itself).
        outer_d = _thread_outer_diameter(args.fastener_thread_spec)
        fastener_r = 0.5 * outer_d

        # Build a fastener cylinder per pair.
        existing_shapes = [s for _, s in components]
        new_shapes = list(existing_shapes)
        new_names = list(names)
        fasteners_meta: list[dict[str, Any]] = []
        for i, (origin, axis_dir, _shaft_r, (lo, hi)) in enumerate(pairs):
            length = max(hi - lo, 1.0)
            cyl_shape = _make_fastener_cylinder(
                origin, axis_dir, fastener_r, length, lo,
            )
            inst_name = f"{args.name_prefix}_{args.fastener_thread_spec}_{i}"
            if inst_name in new_names:
                inst_name = f"{inst_name}_{len(new_names)}"
            new_names.append(inst_name)
            new_shapes.append(cyl_shape)
            fasteners_meta.append({
                "name": inst_name,
                "axis_origin": [round(c, 6) for c in origin],
                "axis_dir": [round(c, 6) for c in axis_dir],
                "length_mm": round(length, 6),
                "diameter_mm": round(outer_d, 6),
                "thread_spec": args.fastener_thread_spec,
            })

        comp = build_compound(new_shapes)
        result_body = Part(comp)
        result_body._pd_component_names = list(new_names)
        # Preserve any sub-assembly tags from prior body.
        if hasattr(body, "_pd_sub_assemblies"):
            result_body._pd_sub_assemblies = {
                k: list(v) for k, v in body._pd_sub_assemblies.items()
            }

        history = EntityHistoryMap(
            rules={"output_component": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(
            body=result_body,
            history=history,
            extras={
                "component_names": list(new_names),
                "fastener_count": len(fasteners_meta),
                "fasteners": fasteners_meta,
                "thread_spec": args.fastener_thread_spec,
            },
        )
