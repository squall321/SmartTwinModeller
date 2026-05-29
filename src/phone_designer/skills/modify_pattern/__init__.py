"""Modify / pattern skills — repeat a feature operation at computed transforms.

Each skill takes a circular feature template (profile_diameter, operation type,
depth/height) and applies it N times at positions computed from the pattern
parameters. Operations are atomic OCCT Cut/Fuse with cylinder tools — no
delegation to other skills.

Supported operations (all on planar ±Z faces in v0):
    pocket : flat-bottom cylindrical cavity of depth `feature_depth_mm`
    hole   : through-hole (cylinder long enough to traverse the body)
    boss   : cylindrical raised feature of height `feature_height_mm`

Private helpers `_validate_feature_args`, `_apply_circular_feature`, and
`_face_center_and_normal` live in this module so the 4 pattern skills share
the same atomic per-feature implementation.
"""
from __future__ import annotations

from typing import Literal


FeatureOp = Literal["pocket", "hole", "boss"]
# Through-hole cylinder length — generous so it traverses any reasonable phone
# body. Same convention as `hole.py` (which uses 200 mm).
_THROUGH_LEN_MM = 200.0


def _validate_feature_args(
    operation: str,
    feature_depth_mm: float | None,
    feature_height_mm: float | None,
) -> None:
    """Operation × required-arg consistency check.

    pocket → feature_depth_mm required (>0).
    hole   → both depth/height ignored (through cylinder).
    boss   → feature_height_mm required (>0).
    """
    if operation == "pocket":
        if feature_depth_mm is None or feature_depth_mm <= 0:
            raise ValueError(
                "operation='pocket' requires feature_depth_mm > 0"
            )
    elif operation == "boss":
        if feature_height_mm is None or feature_height_mm <= 0:
            raise ValueError(
                "operation='boss' requires feature_height_mm > 0"
            )
    elif operation == "hole":
        # No depth/height required — through-hole.
        pass
    else:
        raise ValueError(
            f"operation must be 'pocket' | 'hole' | 'boss', got {operation!r}"
        )


def _face_center_and_normal(face):
    """Return ((cx,cy,cz), (nx,ny,nz)) of a planar ±Z face. Raises otherwise."""
    from phone_designer.skills.modify_pocket._sketch_to_solid import (
        _face_plane_normal,
    )
    center, normal = _face_plane_normal(face)
    if abs(normal[2]) <= 0.95:
        raise NotImplementedError(
            "pattern skills v0 only support planar ±Z faces "
            f"(got normal={normal})"
        )
    return center, normal


def _build_cylinder_tool(
    *,
    cx: float, cy: float, cz: float,
    nz_sign: int,
    radius: float,
    operation: FeatureOp,
    feature_depth_mm: float | None,
    feature_height_mm: float | None,
):
    """Build the OCCT solid that will be Cut or Fused for one feature.

    Geometry conventions (v0, planar ±Z faces only):
      pocket / hole: cylinder grows INTO the body (opposite of face normal).
      boss:          cylinder grows OUT of the body (along the face normal).

    The cylinder is built with its base anchored at the face plane at (cx, cy)
    and its axis along ±Z. For pocket the base sits at the face and the body
    is `feature_depth_mm` long INTO the body. For hole it is `_THROUGH_LEN_MM`
    long INTO the body, but offset back by a small overrun so it cleanly
    pokes through both faces. For boss it grows OUTWARD by `feature_height_mm`.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    if operation == "pocket":
        length = float(feature_depth_mm)
        # Cylinder grows opposite to face normal (into the body).
        axis_z = -nz_sign
        base_z = cz
    elif operation == "hole":
        length = _THROUGH_LEN_MM
        axis_z = -nz_sign
        # Overrun the entry face by 1mm so the through-hole booleans cleanly
        # (avoids zero-thickness slivers at the entry rim).
        base_z = cz + nz_sign * 1.0
    elif operation == "boss":
        length = float(feature_height_mm)
        # Cylinder grows along the face normal (out of the body).
        axis_z = nz_sign
        base_z = cz
    else:
        raise ValueError(f"unknown operation {operation!r}")

    ax = gp_Ax2(gp_Pnt(cx, cy, base_z), gp_Dir(0.0, 0.0, float(axis_z)))
    mk = BRepPrimAPI_MakeCylinder(ax, radius, length)
    mk.Build()
    if not mk.IsDone():
        raise RuntimeError(
            f"BRepPrimAPI_MakeCylinder failed at ({cx},{cy},{base_z}) "
            f"r={radius} len={length}"
        )
    return mk.Shape()


def _apply_circular_feature(
    shape,
    *,
    face_cx: float, face_cy: float, face_cz: float, nz_sign: int,
    feature_x_mm: float, feature_y_mm: float,
    profile_diameter_mm: float,
    operation: FeatureOp,
    feature_depth_mm: float | None,
    feature_height_mm: float | None,
):
    """Apply ONE circular feature (pocket | hole | boss) to `shape`.

    `(feature_x_mm, feature_y_mm)` is the feature center IN FACE-LOCAL XY,
    relative to the face center. We translate to world XY via (face_cx + dx,
    face_cy + dy), keeping the cylinder anchor Z at the face plane.

    Returns the new TopoDS_Shape.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse

    radius = profile_diameter_mm / 2.0
    tool = _build_cylinder_tool(
        cx=face_cx + feature_x_mm,
        cy=face_cy + feature_y_mm,
        cz=face_cz,
        nz_sign=nz_sign,
        radius=radius,
        operation=operation,
        feature_depth_mm=feature_depth_mm,
        feature_height_mm=feature_height_mm,
    )

    if operation == "boss":
        op = BRepAlgoAPI_Fuse(shape, tool)
    else:  # pocket | hole
        op = BRepAlgoAPI_Cut(shape, tool)
    op.Build()
    if not op.IsDone():
        raise RuntimeError(
            f"pattern feature boolean failed (operation={operation}, "
            f"at face-local ({feature_x_mm},{feature_y_mm}))"
        )
    return op.Shape()


from phone_designer.skills.modify_pattern.circular_pattern import CircularPattern  # noqa: E402
from phone_designer.skills.modify_pattern.linear_pattern import LinearPattern  # noqa: E402
from phone_designer.skills.modify_pattern.mirror_about_two_planes import MirrorAboutTwoPlanes  # noqa: E402
from phone_designer.skills.modify_pattern.mirror_feature import MirrorFeature  # noqa: E402
from phone_designer.skills.modify_pattern.nested_pattern import NestedPattern  # noqa: E402
from phone_designer.skills.modify_pattern.path_pattern import PathPattern  # noqa: E402
from phone_designer.skills.modify_pattern.variable_pattern import VariablePattern  # noqa: E402

__all__ = [
    "LinearPattern", "CircularPattern", "MirrorFeature", "PathPattern",
    "VariablePattern", "NestedPattern", "MirrorAboutTwoPlanes",
]
