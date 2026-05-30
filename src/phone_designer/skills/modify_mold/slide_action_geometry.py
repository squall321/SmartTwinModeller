"""slide_action_geometry — atomic. Build a slide-action block sized to
release a single undercut face.

Workflow:
    1. Locate the undercut face by index (``undercut_face_selector.face_idx``)
       on the body's face list, returned in deterministic order by
       ``_all_faces``.
    2. Compute the face centroid and outward normal (the slide-pull
       direction is the *negative* of the requested ``pull_direction``
       projected onto the face normal — i.e. the slide pulls along the
       undercut face's outward normal so the part can release).
    3. Build a rectangular slide block (size taken from the body bbox +
       ``slide_clearance_mm``) sitting in front of the undercut face, with
       its near plane parallel to the parting plane (perpendicular to the
       undercut normal) and offset by ``slide_clearance_mm``.
    4. Return the slide block as the new body (modify_mold category — this
       skill adds the slide tooling solid). ``post=volume_changed``.

extras:
    extras["slide_block_volume_mm3"] = float
    extras["parting_plane"]          = {"origin":[x,y,z],"normal":[nx,ny,nz]}
    extras["slide_pull_direction"]   = [nx,ny,nz]
    extras["undercut_face_idx"]      = int
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


class _UndercutFaceSelector(BaseModel):
    """Minimal selector — pick a face by deterministic index.

    Phase 1 accepts only ``face_idx``; richer selector kinds are wired in
    later via the existing _selectors / _resolvers infrastructure.
    """
    face_idx: int = Field(..., ge=0)


def _face_centroid_normal(face) -> tuple[
    tuple[float, float, float], tuple[float, float, float],
] | None:
    """Return (centroid, outward_normal) for a face, or None on failure."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_REVERSED

    try:
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        c = props.CentreOfMass()
    except Exception:
        return None
    surf = BRepAdaptor_Surface(face)
    try:
        if surf.GetType() == GeomAbs_Plane:
            n = surf.Plane().Axis().Direction()
            nx, ny, nz = n.X(), n.Y(), n.Z()
        else:
            u0, u1 = surf.FirstUParameter(), surf.LastUParameter()
            v0, v1 = surf.FirstVParameter(), surf.LastVParameter()
            u = 0.5 * (u0 + u1)
            v = 0.5 * (v0 + v1)
            d1u = surf.DN(u, v, 1, 0)
            d1v = surf.DN(u, v, 0, 1)
            nx = d1u.Y() * d1v.Z() - d1u.Z() * d1v.Y()
            ny = d1u.Z() * d1v.X() - d1u.X() * d1v.Z()
            nz = d1u.X() * d1v.Y() - d1u.Y() * d1v.X()
    except Exception:
        return None
    nmag = math.sqrt(nx * nx + ny * ny + nz * nz)
    if nmag < 1e-12:
        return None
    nx, ny, nz = nx / nmag, ny / nmag, nz / nmag
    if face.Orientation() == TopAbs_REVERSED:
        nx, ny, nz = -nx, -ny, -nz
    return ((c.X(), c.Y(), c.Z()), (nx, ny, nz))


def _orthonormal_basis(n: tuple[float, float, float]):
    """Return (u, v) — two unit vectors orthogonal to n and each other."""
    nx, ny, nz = n
    # pick a helper vector that is not parallel to n
    if abs(nx) < 0.9:
        helper = (1.0, 0.0, 0.0)
    else:
        helper = (0.0, 1.0, 0.0)
    # u = n x helper
    ux = ny * helper[2] - nz * helper[1]
    uy = nz * helper[0] - nx * helper[2]
    uz = nx * helper[1] - ny * helper[0]
    ulen = math.sqrt(ux * ux + uy * uy + uz * uz)
    ux, uy, uz = ux / ulen, uy / ulen, uz / ulen
    # v = n x u
    vx = ny * uz - nz * uy
    vy = nz * ux - nx * uz
    vz = nx * uy - ny * ux
    return (ux, uy, uz), (vx, vy, vz)


@skill(
    name="slide_action_geometry",
    category="modify/mold",
    level="atomic",
    summary="Build a slide-action tooling block sized to release a single "
            "undercut face. The block's parting plane is perpendicular to "
            "the undercut normal with slide_clearance_mm offset. Returns the "
            "slide block as the new body and records parting plane info in "
            "extras.",
    selector_kinds=["face_named"],
    history_rules={},
    produces_features=["slide_block"],
    preserves=[],
    manufacturing={
        "die_cast_al":       {"min_draft_deg": 1.0},
        "injection_mold_pa": {"min_draft_deg": 0.5},
    },
    failure_modes=["fm.no_undercut_face", "fm.slide_block_failed"],
    cost_hint=0.5,
    post_conditions=[PostCondition(kind="volume_changed")],
)
class SlideActionGeometry(SkillBase):
    class Args(BaseModel):
        undercut_face_selector: _UndercutFaceSelector
        pull_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
        slide_clearance_mm: float = Field(default=0.5, ge=0.0, le=50.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        from phone_designer.skills._resolvers import _all_faces

        shape = _occt_shape(body)
        if shape is None:
            raise RuntimeError("slide_action_geometry: body is None")

        faces = _all_faces(shape)
        idx = args.undercut_face_selector.face_idx
        if idx >= len(faces):
            raise RuntimeError(
                f"slide_action_geometry: face_idx {idx} out of range "
                f"(body has {len(faces)} faces)"
            )
        face = faces[idx]

        cn = _face_centroid_normal(face)
        if cn is None:
            raise RuntimeError(
                "slide_action_geometry: failed to compute centroid/normal "
                f"for face {idx}"
            )
        centroid, normal = cn

        # Slide pull direction = the face outward normal (the slide pulls
        # away from the part, perpendicular to the undercut surface).
        slide_dir = normal

        # Body bbox sets the slide block footprint.
        bb = Bnd_Box()
        try:
            BRepBndLib.AddOptimal_s(shape, bb)
        except Exception:
            BRepBndLib.Add_s(shape, bb)
        if bb.IsVoid():
            raise RuntimeError("slide_action_geometry: body bbox is void")
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        diag = math.sqrt(
            (xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2,
        )
        # Block dimensions: face-plane = diag x diag, depth (along slide dir)
        # = diag / 2 — generous enough to clear the undercut.
        half_extent = diag / 2.0
        depth = diag / 2.0

        # Local frame: slide_dir is the block's "depth" axis; u, v span the
        # parting plane perpendicular to slide_dir.
        u, v = _orthonormal_basis(slide_dir)

        # Block origin: centroid + clearance along slide_dir, then back by
        # half_extent along u and v so the centroid lies on the block's
        # near-face center.
        ox = centroid[0] + slide_dir[0] * args.slide_clearance_mm \
             - u[0] * half_extent - v[0] * half_extent
        oy = centroid[1] + slide_dir[1] * args.slide_clearance_mm \
             - u[1] * half_extent - v[1] * half_extent
        oz = centroid[2] + slide_dir[2] * args.slide_clearance_mm \
             - u[2] * half_extent - v[2] * half_extent

        # Build the block using a local axis system (origin, slide_dir, u).
        ax = gp_Ax2(
            gp_Pnt(ox, oy, oz),
            gp_Dir(slide_dir[0], slide_dir[1], slide_dir[2]),
            gp_Dir(u[0], u[1], u[2]),
        )
        # MakeBox(ax, dx, dy, dz) — dx along X (u), dy along Y (v), dz along Z (slide_dir)
        slide_block = BRepPrimAPI_MakeBox(
            ax,
            2.0 * half_extent,
            2.0 * half_extent,
            depth,
        ).Shape()

        # Compute slide-block volume for extras.
        block_volume = 2.0 * half_extent * 2.0 * half_extent * depth

        parting_plane = {
            "origin": [round(centroid[0] + slide_dir[0] * args.slide_clearance_mm, 4),
                       round(centroid[1] + slide_dir[1] * args.slide_clearance_mm, 4),
                       round(centroid[2] + slide_dir[2] * args.slide_clearance_mm, 4)],
            "normal": [round(slide_dir[0], 6),
                       round(slide_dir[1], 6),
                       round(slide_dir[2], 6)],
        }

        extras = {
            "slide_block_volume_mm3": round(block_volume, 4),
            "parting_plane":          parting_plane,
            "slide_pull_direction":   [round(c, 6) for c in slide_dir],
            "undercut_face_idx":      idx,
        }

        return SkillResult(
            body=Part(slide_block),
            history=EntityHistoryMap(),
            extras=extras,
        )
