"""pmi_surface_texture — atomic, read-only.

Attach an ISO 1302 / ASME Y14.36 surface texture symbol to one or more faces
resolved by a selector. Stored as a dict in ``body._pd_surface_texture``
(keyed by texture id so multiple symbols may coexist).

Each record stores:
    {
        "ra_um": float,
        "profile": "Ra" | "Rz" | "Rmax",
        "direction": "any" | "parallel" | "perpendicular" | "crossed",
        "lay_angle_deg": float | None,
        "face_selector": selector_dict,
        "face_anchors": [[x,y,z], ...],   # centers of matched faces
        "face_count": int,
    }

extras["pmi_surface_texture"] = the appended record,
extras["pmi_surface_textures_total"] = total count.

body unchanged — ``body_present`` post-condition.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorBase, selector_from_dict
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _coerce_selector(s: Any) -> SelectorBase:
    if isinstance(s, SelectorBase):
        return s
    if isinstance(s, dict):
        return selector_from_dict(s)
    raise TypeError(f"unsupported selector type: {type(s).__name__}")


def _selector_to_dict(s: SelectorBase) -> dict:
    try:
        return s.model_dump(mode="json")
    except Exception:
        return {"kind": getattr(s, "kind", "unknown")}


@skill(
    name="pmi_surface_texture",
    category="pmi",
    level="atomic",
    summary="Attach an ISO 1302 surface-texture symbol (Ra/Rz/Rmax + lay "
            "direction + optional angle) to faces. Stored on "
            "body._pd_surface_texture for AP242 export. Read-only.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["pmi_surface_texture"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.no_match"],
    cost_hint=0.03,
    post_conditions=[PostCondition(kind="body_present")],
)
class PmiSurfaceTexture(SkillBase):
    class Args(BaseModel):
        face_selector: dict
        ra_um: float = Field(gt=0.0, le=100.0)
        profile: Literal["Ra", "Rz", "Rmax"] = "Ra"
        direction: Literal["any", "parallel", "perpendicular", "crossed"] = "any"
        lay_angle_deg: float | None = None

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("pmi_surface_texture: body is None")
        from phone_designer.skills._resolvers import _face_center, resolve_faces

        shape = _occt_shape(body)
        sel = _coerce_selector(args.face_selector)
        faces = resolve_faces(shape, sel, body=body)
        if not faces:
            raise ValueError(
                f"pmi_surface_texture: face_selector matched 0 faces "
                f"(kind={sel.kind})"
            )

        anchors: list[list[float]] = []
        for f in faces:
            try:
                c = _face_center(f)
                anchors.append([round(c[0], 6), round(c[1], 6), round(c[2], 6)])
            except Exception:
                pass

        record = {
            "ra_um": float(args.ra_um),
            "profile": args.profile,
            "direction": args.direction,
            "lay_angle_deg": (
                float(args.lay_angle_deg) if args.lay_angle_deg is not None else None
            ),
            "face_selector": _selector_to_dict(sel),
            "face_anchors": anchors,
            "face_count": len(faces),
        }

        try:
            existing = list(getattr(body, "_pd_surface_texture", []) or [])
        except Exception:
            existing = []
        existing.append(record)
        try:
            body._pd_surface_texture = existing
        except Exception:
            pass

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "pmi_surface_texture": record,
                "pmi_surface_textures_total": len(existing),
            },
        )
