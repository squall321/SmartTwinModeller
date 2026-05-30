"""curvature_at_point — atomic, read-only.

For the first face matched by the selector, compute the local Gaussian, mean,
and principal curvatures at a single (u, v) parametric location via OCCT
``BRepLProp_SLProps``. The (u, v) inputs are *normalised* into the face's
parameter box (0 → FirstUParameter, 1 → LastUParameter), so callers don't
need to know the surface's parameter range.

Useful for DFM probing ("what's the curvature *right here* at the click
point?"), and as a quick sanity probe before kicking off the heavier
``curvature_map`` skill.

extras["curvature"] schema:
    {
      "face_idx": int,
      "uv":       [u_param, v_param],     # actual (not normalised) parameters
      "uv_norm":  [u_norm, v_norm],       # the requested (u, v) inputs
      "xyz":      [x, y, z],
      "gaussian": K,
      "mean":     H,
      "k1":       max_principal,
      "k2":       min_principal,
      "is_defined": bool,
    }

Body unchanged — post ``body_present``.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorBase, selector_from_dict
from phone_designer.skills._spec import SkillBase, SkillResult


def _load(family: str, name: str):
    import pathlib

    import yaml
    here = pathlib.Path(__file__).resolve()
    for root in here.parents:
        cand = root / "catalogs" / family / f"{name}.yaml"
        if cand.exists():
            return yaml.safe_load(cand.read_text())
    raise FileNotFoundError(f"catalog not found: {family}/{name}.yaml")


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _coerce_selector(s: Any) -> SelectorBase:
    if isinstance(s, SelectorBase):
        return s
    if isinstance(s, dict):
        return selector_from_dict(s)
    raise TypeError(f"unsupported selector type: {type(s).__name__}")


def _compute_curvature(face, u_norm: float, v_norm: float) -> dict[str, Any]:
    """Compute Gaussian + mean + principal curvatures at normalised (u, v)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepLProp import BRepLProp_SLProps

    adaptor = BRepAdaptor_Surface(face)
    u0 = adaptor.FirstUParameter()
    u1 = adaptor.LastUParameter()
    v0 = adaptor.FirstVParameter()
    v1 = adaptor.LastVParameter()

    u = u0 + (u1 - u0) * float(u_norm)
    v = v0 + (v1 - v0) * float(v_norm)

    tol = 1e-6
    out: dict[str, Any] = {
        "uv":      [round(u, 6), round(v, 6)],
        "uv_norm": [round(float(u_norm), 6), round(float(v_norm), 6)],
        "xyz":     [0.0, 0.0, 0.0],
        "gaussian": 0.0,
        "mean":     0.0,
        "k1":       0.0,
        "k2":       0.0,
        "is_defined": False,
    }

    try:
        slp = BRepLProp_SLProps(adaptor, u, v, 2, tol)
        p = slp.Value()
        out["xyz"] = [round(p.X(), 6), round(p.Y(), 6), round(p.Z(), 6)]
        if slp.IsCurvatureDefined():
            kmin = float(slp.MinCurvature())
            kmax = float(slp.MaxCurvature())
            out["gaussian"] = round(float(slp.GaussianCurvature()), 8)
            out["mean"]     = round(float(slp.MeanCurvature()), 8)
            out["k1"]       = round(kmax, 8)
            out["k2"]       = round(kmin, 8)
            out["is_defined"] = True
    except Exception:
        pass
    return out


@skill(
    name="curvature_at_point",
    category="inspect",
    level="atomic",
    summary="Compute Gaussian / mean / principal curvatures at a single "
            "normalised (u, v) location on the first matched face. Read-only "
            "— body unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["curvature_probe"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class CurvatureAtPoint(SkillBase):
    class Args(BaseModel):
        face_selector: dict
        u: float = Field(default=0.5, ge=0.0, le=1.0,
                         description="Normalised U in [0,1].")
        v: float = Field(default=0.5, ge=0.0, le=1.0,
                         description="Normalised V in [0,1].")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import resolve_faces

        if body is None:
            raise ValueError("curvature_at_point: body is None")

        shape = _occt_shape(body)
        if shape is None:
            raise ValueError("curvature_at_point: body.wrapped is None")

        sel = _coerce_selector(args.face_selector)
        faces = resolve_faces(shape, sel, body=body)
        if not faces:
            extras = {
                "curvature": {
                    "face_idx":   -1,
                    "uv":         [0.0, 0.0],
                    "uv_norm":    [args.u, args.v],
                    "xyz":        [0.0, 0.0, 0.0],
                    "gaussian":   0.0,
                    "mean":       0.0,
                    "k1":         0.0,
                    "k2":         0.0,
                    "is_defined": False,
                },
                "face_count_matched": 0,
            }
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras=extras,
            )

        face = faces[0]
        c = _compute_curvature(face, args.u, args.v)
        c["face_idx"] = 0

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "curvature": c,
                "face_count_matched": len(faces),
            },
        )
