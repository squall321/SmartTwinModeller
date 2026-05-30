"""principal_axes — atomic, read-only.

Compute the principal axes of a body's mass distribution by diagonalising the
inertia tensor returned by ``GProp_GProps.MatrixOfInertia``. This is the
moment-of-inertia "PCA" of the part — useful for orientation alignment,
checking whether a part is slender (one moment dominates) or plate-like
(two close, one small), etc.

extras schema::

    {"principal_axes": [
        {"axis": [ex, ey, ez], "moment": float},
        {"axis": [ex, ey, ez], "moment": float},
        {"axis": [ex, ey, ez], "moment": float},
     ],
     "centroid": [x, y, z],
     "volume_mm3": float}

The list is sorted by moment ascending (so axis[0] is the axis with the
LOWEST moment — i.e. the longest dimension of the part).

Body unchanged — ``body_present`` post-condition.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _symm_3x3_eigen(m: list[list[float]]) -> list[tuple[float, tuple[float, float, float]]]:
    """Diagonalise a symmetric 3x3 matrix via Jacobi rotation.

    Returns list of (eigenvalue, eigenvector) sorted ascending by eigenvalue.
    """
    # Copy in-place.
    a = [row[:] for row in m]
    # Ensure symmetry.
    a[0][1] = a[1][0] = 0.5 * (a[0][1] + a[1][0])
    a[0][2] = a[2][0] = 0.5 * (a[0][2] + a[2][0])
    a[1][2] = a[2][1] = 0.5 * (a[1][2] + a[2][1])

    # Identity eigenvector matrix.
    v = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

    for _ in range(100):
        # off-diagonal max
        p, q = 0, 1
        max_off = abs(a[0][1])
        if abs(a[0][2]) > max_off:
            p, q = 0, 2
            max_off = abs(a[0][2])
        if abs(a[1][2]) > max_off:
            p, q = 1, 2
            max_off = abs(a[1][2])
        if max_off < 1e-12:
            break

        app = a[p][p]
        aqq = a[q][q]
        apq = a[p][q]
        if abs(apq) < 1e-18:
            break
        theta = (aqq - app) / (2.0 * apq)
        if theta >= 0:
            t = 1.0 / (theta + math.sqrt(1.0 + theta * theta))
        else:
            t = 1.0 / (theta - math.sqrt(1.0 + theta * theta))
        c_ = 1.0 / math.sqrt(1.0 + t * t)
        s_ = t * c_

        a[p][p] = app - t * apq
        a[q][q] = aqq + t * apq
        a[p][q] = a[q][p] = 0.0
        for i in range(3):
            if i != p and i != q:
                aip = a[i][p]
                aiq = a[i][q]
                a[i][p] = a[p][i] = c_ * aip - s_ * aiq
                a[i][q] = a[q][i] = s_ * aip + c_ * aiq
        for i in range(3):
            vip = v[i][p]
            viq = v[i][q]
            v[i][p] = c_ * vip - s_ * viq
            v[i][q] = s_ * vip + c_ * viq

    eigs: list[tuple[float, tuple[float, float, float]]] = []
    for k in range(3):
        ev = a[k][k]
        vec = (v[0][k], v[1][k], v[2][k])
        # normalise
        mag = math.sqrt(vec[0] ** 2 + vec[1] ** 2 + vec[2] ** 2)
        if mag > 1e-12:
            vec = (vec[0] / mag, vec[1] / mag, vec[2] / mag)
        eigs.append((float(ev), vec))
    eigs.sort(key=lambda x: x[0])
    return eigs


@skill(
    name="principal_axes",
    category="inspect",
    level="atomic",
    summary="Compute principal axes (PCA on mass) of the body via eigen-"
            "decomposition of OCCT's inertia tensor. Returns up to 3 axes "
            "sorted by moment ascending. Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["principal_axes"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class PrincipalAxes(SkillBase):
    class Args(BaseModel):
        pass

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps

        shape = _occt_shape(body)

        mat_rows: list[list[float]] = [
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
        ]
        volume = 0.0
        cx = cy = cz = 0.0
        try:
            props = GProp_GProps()
            BRepGProp.VolumeProperties_s(shape, props)
            volume = float(props.Mass())
            c = props.CentreOfMass()
            cx, cy, cz = c.X(), c.Y(), c.Z()
            mat = props.MatrixOfInertia()
            for i in range(3):
                for j in range(3):
                    mat_rows[i][j] = float(mat.Value(i + 1, j + 1))
        except Exception:
            pass

        try:
            eigs = _symm_3x3_eigen(mat_rows)
        except Exception:
            eigs = []

        principal: list[dict[str, Any]] = []
        for ev, vec in eigs:
            principal.append({
                "axis": [round(vec[0], 6), round(vec[1], 6), round(vec[2], 6)],
                "moment": round(ev, 6),
            })

        extras = {
            "principal_axes": principal,
            "centroid": [round(cx, 6), round(cy, 6), round(cz, 6)],
            "volume_mm3": round(volume, 6),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
