"""Selector → OCCT entity 매칭 resolver.

[[lat.md/concepts.md#selector]] 의 SelectorBase 트리를 받아 실제 face/edge list 반환.

Phase 1 의 범위: 단순 selector 직접 매칭. tagged / face_named 는 stub (Phase 2 에서
EntityHistoryMap 과 연동해 본격 동작).

Phase 2 에서:
  - tagged → history map propagated tag chain 추적
  - face_named → body 의 named face attribute 조회
  - 조합 selector (AndSel/OrSel/...) 통합
"""
from __future__ import annotations

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GCPnts import GCPnts_AbscissaPoint
from OCP.GeomAbs import GeomAbs_Plane
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_MapOfShape

from phone_designer.skills._selectors import SelectorBase

EPS = 0.01   # 10μm — OCCT linear tolerance 와 일관. 이전 1e-3 은 너무 빡빡.


def _all_edges(shape) -> list:
    """Unique TopoDS_Edge list (TopExp_Explorer 의 sub-shape 중복 제거 + 캐스팅)."""
    seen = TopTools_MapOfShape()
    out = []
    it = TopExp_Explorer(shape, TopAbs_EDGE)
    while it.More():
        e = it.Current()
        if not seen.Contains(e):
            seen.Add(e)
            out.append(TopoDS.Edge_s(e))
        it.Next()
    return out


def _all_faces(shape) -> list:
    """Unique TopoDS_Face list."""
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


def _edge_endpoints(edge):
    v1 = TopExp.FirstVertex_s(edge)
    v2 = TopExp.LastVertex_s(edge)
    return BRep_Tool.Pnt_s(v1), BRep_Tool.Pnt_s(v2)


def _edge_length(edge) -> float:
    return GCPnts_AbscissaPoint.Length_s(BRepAdaptor_Curve(edge))


def _face_center(face) -> tuple[float, float, float]:
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    c = props.CentreOfMass()
    return (c.X(), c.Y(), c.Z())


def _face_area(face) -> float:
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    return props.Mass()


def _face_normal_at_center(face):
    """Planar face 의 outward normal. 곡면 face 는 (0,0,0).

    face.Orientation() == TopAbs_REVERSED 면 surface plane normal 을 반전한다.
    OCCT 는 같은 surface 를 공유하는 face 를 orientation flag 로 구분하므로 raw
    plane normal 만 보면 fillet/cut 후의 bottom 이 +Z 로 잘못 잡힌다.
    """
    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Plane:
        return (0.0, 0.0, 0.0)
    pl = surf.Plane()
    n = pl.Axis().Direction()
    nx, ny, nz = n.X(), n.Y(), n.Z()
    if face.Orientation() == TopAbs_REVERSED:
        nx, ny, nz = -nx, -ny, -nz
    return (nx, ny, nz)


def resolve_edges(shape, selector: SelectorBase) -> list:
    """SelectorBase 트리 → OCCT edge list."""
    kind = selector.kind

    if kind == "axis_aligned_edges":
        axis = selector.axis
        out = []
        for e in _all_edges(shape):
            p1, p2 = _edge_endpoints(e)
            if axis == "Z" and abs(p1.X() - p2.X()) < EPS and abs(p1.Y() - p2.Y()) < EPS:
                out.append(e)
            elif axis == "X" and abs(p1.Y() - p2.Y()) < EPS and abs(p1.Z() - p2.Z()) < EPS:
                out.append(e)
            elif axis == "Y" and abs(p1.X() - p2.X()) < EPS and abs(p1.Z() - p2.Z()) < EPS:
                out.append(e)
        return out

    if kind == "edges_by_position":
        bb = selector.bbox
        mn, mx = bb.min, bb.max
        out = []
        for e in _all_edges(shape):
            p1, p2 = _edge_endpoints(e)
            cx, cy, cz = (p1.X() + p2.X()) / 2, (p1.Y() + p2.Y()) / 2, (p1.Z() + p2.Z()) / 2
            if (mn[0] <= cx <= mx[0]
                    and mn[1] <= cy <= mx[1]
                    and mn[2] <= cz <= mx[2]):
                out.append(e)
        return out

    if kind == "edges_by_length":
        out = []
        for e in _all_edges(shape):
            L = _edge_length(e)
            if (selector.min is None or L >= selector.min) and (
                    selector.max is None or L <= selector.max):
                out.append(e)
        return out

    if kind == "edges_on_face":
        face = _resolve_single_face(shape, selector.face)
        if face is None:
            return []
        seen = TopTools_MapOfShape()
        out = []
        it = TopExp_Explorer(face, TopAbs_EDGE)
        while it.More():
            e = it.Current()
            if not seen.Contains(e):
                seen.Add(e)
                out.append(TopoDS.Edge_s(e))
            it.Next()
        return out

    if kind in ("and", "or", "not", "first_n", "largest_n"):
        return _combo_resolve(shape, selector, kind="edge")

    if kind == "tagged":
        # tag 는 face 에만 부착 — edge tag 는 Phase 4 v2 의 별도 작업.
        # 현 단계는 빈 list.
        return []

    if kind == "face_named":
        # face_named 는 face_only selector — edge 요청에는 빈 list.
        return []

    raise NotImplementedError(f"edge resolver kind={kind} not implemented")


def resolve_faces(shape, selector: SelectorBase, *, body=None) -> list:
    """SelectorBase 트리 → OCCT face list.

    Args:
        shape: OCCT TopoDS_Shape — face explorer 의 base
        selector: SelectorBase 트리
        body: (선택) build123d Part 등. tagged selector 가 body._pd_tags 에서 tag 찾을 때 사용.
    """
    kind = selector.kind

    if kind == "faces_by_normal":
        import math
        direction = selector.direction
        d_len = math.sqrt(sum(x * x for x in direction))
        if d_len < EPS:
            return []
        dn = [x / d_len for x in direction]
        tol = math.cos(math.radians(selector.tol_deg))
        out = []
        for f in _all_faces(shape):
            n = _face_normal_at_center(f)
            if n == (0.0, 0.0, 0.0):
                continue
            dot = n[0] * dn[0] + n[1] * dn[1] + n[2] * dn[2]
            if dot >= tol:
                out.append(f)
        return out

    if kind == "faces_by_area":
        out = []
        for f in _all_faces(shape):
            A = _face_area(f)
            if (selector.min is None or A >= selector.min) and (
                    selector.max is None or A <= selector.max):
                out.append(f)
        return out

    if kind == "face_named":
        # Phase 2 stub: body 의 named face attribute 필요.
        # 본 단계에서는 well-known 이름만 휴리스틱으로 매칭.
        return _heuristic_named_face(shape, selector.name)

    if kind == "tagged":
        return _resolve_tagged_faces(shape, selector.tag, body=body)

    if kind in ("and", "or", "not", "first_n", "largest_n"):
        # combo — body 전파 (tagged 가 안에 있을 수 있음)
        return _combo_resolve(shape, selector, kind="face", body=body)

    raise NotImplementedError(f"face resolver kind={kind} not implemented")


def _resolve_tagged_faces(shape, tag: str, *, body=None) -> list:
    """tagged selector — body._pd_tags 의 tag → EntityRef → nearest face on shape.

    body 가 None 이면 (raw shape 만 전달된 경우) 매칭 불가 → 빈 list.
    실제 호출은 resolve_faces(shape, sel) 의 wrapper 에서 body 전달.

    Phase 4 v1 의 한계: body 인자가 resolve_faces 시그니처에 없어서 본 함수는
    빈 list 반환. tagged selector 사용 시 body 직접 전달하는 별도 entry point 가 필요.
    실제로 tag 가 효과 있으려면 `resolve_faces_with_body(shape, sel, body)` 같은
    extended API — Phase 4 v2 작업.
    """
    if body is None:
        return []

    from phone_designer.skills.compose.tag_face import get_tags

    tags_store = get_tags(body)
    refs = tags_store.get(tag, [])
    if not refs:
        return []

    # 현재 shape 의 face 들에서 nearest-match
    matched = []
    candidate_faces = _all_faces(shape)
    for ref in refs:
        best = None
        best_dist = float("inf")
        for f in candidate_faces:
            c = _face_center(f)
            d2 = ((c[0] - ref.bbox_center[0]) ** 2
                  + (c[1] - ref.bbox_center[1]) ** 2
                  + (c[2] - ref.bbox_center[2]) ** 2)
            if d2 < best_dist:
                best_dist = d2
                best = f
        # 거리 임계값 — 형상 변화로 어긋났더라도 합리 범위 내
        if best is not None and best_dist < 25.0:    # 5mm² ≈ 5mm 변위 허용
            matched.append(best)
    return matched


def _heuristic_named_face(shape, name: str) -> list:
    """face_named 의 stub — top/bottom/side 같은 일반 이름 휴리스틱 매칭.

    Phase 2 에서 history map 의 tagged face 와 연동 + face-local 이름 보존.

    Strategy (top 기준):
        1. planar face 중 normal ≈ +Z, z 중심 최대인 것
        2. (1) 없으면 단순히 z 중심 최대 face (곡면 dome 등) — Phase 1 의
           pocket/plateau 는 planar 만 받으므로 후속 step 에서 NotImplemented 가 날 수도.
    """
    faces = _all_faces(shape)
    if not faces:
        return []
    if name == "top":
        planar = [
            (f, _face_center(f)[2]) for f in faces
            if _face_normal_at_center(f)[2] > 0.9
        ]
        if planar:
            planar.sort(key=lambda x: -x[1])
            return [planar[0][0]]
        # fallback: 곡면 포함 z 최대 face
        ranked = sorted(((f, _face_center(f)[2]) for f in faces), key=lambda x: -x[1])
        return [ranked[0][0]]
    if name == "bottom":
        planar = [
            (f, _face_center(f)[2]) for f in faces
            if _face_normal_at_center(f)[2] < -0.9
        ]
        if planar:
            planar.sort(key=lambda x: x[1])
            return [planar[0][0]]
        ranked = sorted(((f, _face_center(f)[2]) for f in faces), key=lambda x: x[1])
        return [ranked[0][0]]
    return []


def _resolve_single_face(shape, selector: SelectorBase):
    faces = resolve_faces(shape, selector)
    return faces[0] if faces else None


def _combo_resolve(shape, selector: SelectorBase, kind: str, *, body=None) -> list:
    """And/Or/Not/FirstN/LargestN — inner selector 들을 resolve 한 뒤 합성.

    body 인자: tagged selector 가 inner 에 있을 때 body 전파 (face combos 만).
    edges 의 tagged 는 v1 에서 미지원 (빈 list 반환) — body 무관.
    """
    if kind == "edge":
        def resolve_inner(s):
            return resolve_edges(shape, s)
    else:
        def resolve_inner(s):
            return resolve_faces(shape, s, body=body)

    if selector.kind == "and":
        return _intersect_shapes(resolve_inner(selector.left), resolve_inner(selector.right))
    if selector.kind == "or":
        return _union_shapes(resolve_inner(selector.left), resolve_inner(selector.right))
    if selector.kind == "not":
        all_entities = _all_edges(shape) if kind == "edge" else _all_faces(shape)
        return _difference_shapes(all_entities, resolve_inner(selector.inner))
    if selector.kind == "first_n":
        ranked = _rank_for_sort(resolve_inner(selector.inner), selector.sort_by, kind)
        return [r[0] for r in ranked[:selector.n]]
    if selector.kind == "largest_n":
        ranked = _rank_for_sort(
            resolve_inner(selector.inner), selector.sort_by, kind, descending=True
        )
        return [r[0] for r in ranked[:selector.n]]
    return []


def _rank_for_sort(entities: list, sort_by: str, kind: str, descending: bool = False):
    def key(e):
        if kind == "edge":
            p1, p2 = _edge_endpoints(e)
            cx, cy, cz = (p1.X() + p2.X()) / 2, (p1.Y() + p2.Y()) / 2, (p1.Z() + p2.Z()) / 2
            if sort_by == "bbox_x":
                return cx
            if sort_by == "bbox_y":
                return cy
            if sort_by == "bbox_z":
                return cz
            if sort_by == "measure":
                return _edge_length(e)
            return 0
        else:
            cx, cy, cz = _face_center(e)
            if sort_by == "bbox_x":
                return cx
            if sort_by == "bbox_y":
                return cy
            if sort_by == "bbox_z":
                return cz
            if sort_by == "measure":
                return _face_area(e)
            if sort_by == "bbox_volume":
                return _face_area(e)  # 근사
            return 0
    pairs = [(e, key(e)) for e in entities]
    pairs.sort(key=lambda p: p[1], reverse=descending)
    return pairs


def _shape_id(s) -> int:
    """OCCT shape 의 hashable id.

    OCP 7.7+ 에서는 TopoDS_Shape 가 __hash__ 를 제공하므로 hash() 사용.
    구버전 호환을 위해 HashCode 폴백 유지.
    """
    try:
        return hash(s)
    except TypeError:
        return s.HashCode(2**31 - 1)


def _intersect_shapes(a: list, b: list) -> list:
    b_ids = {_shape_id(s) for s in b}
    return [s for s in a if _shape_id(s) in b_ids]


def _union_shapes(a: list, b: list) -> list:
    seen = set()
    out = []
    for s in a + b:
        sid = _shape_id(s)
        if sid not in seen:
            seen.add(sid)
            out.append(s)
    return out


def _difference_shapes(a: list, b: list) -> list:
    b_ids = {_shape_id(s) for s in b}
    return [s for s in a if _shape_id(s) not in b_ids]
