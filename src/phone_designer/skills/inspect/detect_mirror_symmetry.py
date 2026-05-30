"""detect_mirror_symmetry — atomic, read-only.

Test candidate planes and measure how close the body is to mirror-symmetric
about each plane. For every test plane we sample the body surface, reflect the
sample set through the plane, and compute the average nearest-neighbour
distance from the reflected samples back to the original sample cloud. The
score in [0, 1] is mapped from that distance (1 = perfect mirror, 0 = far).

For ``"auto"`` we use a coarse PCA over the body's sample point cloud and try
the three principal-axis planes (centroid + eigenvector as normal).

extras["mirror_planes"] = [
    {"plane_origin":[x,y,z], "plane_normal":[nx,ny,nz], "symmetry_score":0..1},
    ...
]

Body returned unchanged.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _bbox(shape) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bb)
    except Exception:
        try:
            BRepBndLib.Add_s(shape, bb)
        except Exception:
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    if bb.IsVoid():
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return ((xmin, ymin, zmin), (xmax, ymax, zmax))


def _sample_surface_points(shape, target: int = 200,
                            linear_deflection: float = 0.5) -> list[tuple[float, float, float]]:
    """Tessellate the body and collect mesh node positions. Returns up to ``target``
    points, stride-sampled if the mesh is denser.
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    try:
        mesher = BRepMesh_IncrementalMesh(
            shape, float(linear_deflection), False, math.radians(20.0), True,
        )
        mesher.Perform()
    except Exception:
        pass

    pts: list[tuple[float, float, float]] = []
    it = TopExp_Explorer(shape, TopAbs_FACE)
    while it.More():
        try:
            face = TopoDS.Face_s(it.Current())
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(face, loc)
            if tri is not None:
                trsf = loc.Transformation()
                n_nodes = int(tri.NbNodes())
                for i in range(1, n_nodes + 1):
                    p = tri.Node(i).Transformed(trsf)
                    pts.append((float(p.X()), float(p.Y()), float(p.Z())))
        except Exception:
            pass
        it.Next()

    if not pts:
        return pts
    if len(pts) <= target:
        return pts
    stride = max(1, len(pts) // target)
    return pts[::stride][:target]


def _reflect_point(p: tuple[float, float, float],
                    origin: tuple[float, float, float],
                    normal: tuple[float, float, float]) -> tuple[float, float, float]:
    """Reflect ``p`` through plane (origin, unit normal)."""
    nx, ny, nz = normal
    nm = math.sqrt(nx * nx + ny * ny + nz * nz)
    if nm < 1e-12:
        return p
    nx, ny, nz = nx / nm, ny / nm, nz / nm
    dx, dy, dz = p[0] - origin[0], p[1] - origin[1], p[2] - origin[2]
    d = dx * nx + dy * ny + dz * nz
    return (p[0] - 2.0 * d * nx,
            p[1] - 2.0 * d * ny,
            p[2] - 2.0 * d * nz)


def _mean_nearest_distance(
    a_pts: list[tuple[float, float, float]],
    b_pts: list[tuple[float, float, float]],
) -> float:
    """For each point in ``a_pts``, distance to nearest in ``b_pts``. Return mean.

    Uses numpy when available, falls back to O(N*M) pure-python otherwise.
    """
    if not a_pts or not b_pts:
        return float("inf")
    try:
        import numpy as np

        A = np.asarray(a_pts, dtype=float)
        B = np.asarray(b_pts, dtype=float)
        total = 0.0
        chunk = 256
        for i in range(0, len(A), chunk):
            sub = A[i:i + chunk]
            d2 = ((sub[:, None, :] - B[None, :, :]) ** 2).sum(-1)
            total += float(np.sqrt(d2.min(axis=1)).sum())
        return total / float(len(A))
    except Exception:
        total = 0.0
        for ax, ay, az in a_pts:
            best = float("inf")
            for bx, by, bz in b_pts:
                d2 = (ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2
                if d2 < best:
                    best = d2
            total += math.sqrt(best)
        return total / float(len(a_pts))


def _bbox_diagonal(shape) -> float:
    (mn, mx) = _bbox(shape)
    dx, dy, dz = mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _score_from_distance(mean_dist: float, diag: float) -> float:
    """Map nearest-neighbour distance to a [0,1] score.

    score = exp(-k * mean_dist / diag),  k = 50 → noticeable falloff once the
    mismatch reaches ~2% of the bbox diagonal.
    """
    if not math.isfinite(mean_dist):
        return 0.0
    if diag <= 1e-9:
        return 1.0 if mean_dist < 1e-6 else 0.0
    norm = mean_dist / diag
    return float(math.exp(-50.0 * norm))


def _pca_axes(pts: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """Principal axes (unit vectors) of the point cloud. Returns up to 3 axes."""
    if len(pts) < 3:
        return []
    try:
        import numpy as np

        arr = np.asarray(pts, dtype=float)
        c = arr.mean(axis=0)
        centered = arr - c
        cov = centered.T @ centered / max(1, len(pts) - 1)
        w, v = np.linalg.eigh(cov)
        # eigh returns ascending eigenvalues — reverse for descending importance.
        order = np.argsort(w)[::-1]
        axes = [tuple(float(x) for x in v[:, idx]) for idx in order]
        return axes
    except Exception:
        return []


def _centroid(pts: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    if not pts:
        return (0.0, 0.0, 0.0)
    n = float(len(pts))
    return (
        sum(p[0] for p in pts) / n,
        sum(p[1] for p in pts) / n,
        sum(p[2] for p in pts) / n,
    )


def _candidate_planes(
    test_planes: list[str],
    pts: list[tuple[float, float, float]],
    bbox_center: tuple[float, float, float],
) -> list[tuple[str, tuple[float, float, float], tuple[float, float, float]]]:
    """Return list of (label, origin, normal). Origin defaults to bbox center for
    axis-aligned planes; for ``auto`` we use the point-cloud centroid.
    """
    out: list[tuple[str, tuple[float, float, float], tuple[float, float, float]]] = []
    seen: set[str] = set()
    for tp in test_planes:
        key = tp.upper()
        if key in seen:
            continue
        seen.add(key)
        if key == "XZ":
            # XZ plane → normal along Y
            out.append(("XZ", bbox_center, (0.0, 1.0, 0.0)))
        elif key == "YZ":
            out.append(("YZ", bbox_center, (1.0, 0.0, 0.0)))
        elif key == "XY":
            out.append(("XY", bbox_center, (0.0, 0.0, 1.0)))
        elif key == "AUTO":
            origin = _centroid(pts) if pts else bbox_center
            for i, ax in enumerate(_pca_axes(pts)):
                out.append((f"auto_pc{i}", origin, ax))
    return out


@skill(
    name="detect_mirror_symmetry",
    category="inspect",
    level="atomic",
    summary="Detect mirror-symmetry planes by reflecting a sampled point cloud "
            "of the body and comparing to the original. Supports axis-aligned "
            "XZ/YZ/XY tests and PCA-derived 'auto' planes. Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["mirror_planes"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class DetectMirrorSymmetry(SkillBase):
    class Args(BaseModel):
        test_planes: list[str] = Field(default_factory=lambda: ["XZ", "YZ", "XY", "auto"])
        sample_count: int = Field(default=200, ge=10, le=5000)
        linear_deflection_mm: float = Field(default=0.5, gt=0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        shape = _occt_shape(body)

        pts = _sample_surface_points(
            shape, target=args.sample_count,
            linear_deflection=args.linear_deflection_mm,
        )
        (mn, mx) = _bbox(shape)
        bbox_center = ((mn[0] + mx[0]) / 2.0,
                       (mn[1] + mx[1]) / 2.0,
                       (mn[2] + mx[2]) / 2.0)
        diag = math.sqrt(
            (mx[0] - mn[0]) ** 2 + (mx[1] - mn[1]) ** 2 + (mx[2] - mn[2]) ** 2,
        )

        planes = _candidate_planes(args.test_planes, pts, bbox_center)
        results: list[dict[str, Any]] = []

        for _label, origin, normal in planes:
            if not pts:
                score = 0.0
                mean_dist = float("inf")
            else:
                reflected = [_reflect_point(p, origin, normal) for p in pts]
                mean_dist = _mean_nearest_distance(reflected, pts)
                score = _score_from_distance(mean_dist, diag)
            results.append({
                "plane_origin": [round(c, 6) for c in origin],
                "plane_normal": [round(c, 6) for c in normal],
                "symmetry_score": round(float(score), 6),
                "mean_distance_mm": round(float(mean_dist) if math.isfinite(mean_dist) else -1.0, 6),
            })

        # Sort highest-score first so callers can take results[0] as best guess.
        results.sort(key=lambda r: r["symmetry_score"], reverse=True)

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"mirror_planes": results},
        )
