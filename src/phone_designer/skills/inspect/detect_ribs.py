"""detect_ribs — atomic, read-only.

A rib is a thin-wall stiffener: a long, thin block-like protrusion with
``length / thickness > min_aspect_ratio`` and
``thickness < max_thickness_mm``. Detection wraps ``detect_bosses`` and
applies the aspect-ratio + max-thickness filter on every prismatic boss
cluster.

The cluster bbox is sorted into (long, mid, short). Heuristic:

    length    = bbox extent along the longest axis
    thickness = bbox extent along the shortest axis
    height    = bbox extent along the intermediate axis

A cluster qualifies if::

    thickness  < args.max_thickness_mm
    length / thickness > args.min_aspect_ratio

extras schema::

    extras["ribs"] = [
        {
            "id": int,
            "length_mm": float,
            "thickness_mm": float,
            "height_mm": float,
            "base_face": int | None,   # cluster face with the largest area
        },
        ...
    ]
    extras["rib_count"] = int

Body unchanged — ``post_conditions=[body_present]``.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


# Face-count guard. detect_ribs walks detect_bosses output and re-scans
# every face in every cluster — same scaling concern as the underlying
# boss detector.
MAX_FACES_RIBS = 8000


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


@skill(
    name="detect_ribs",
    category="inspect",
    level="atomic",
    summary="Detect thin-wall stiffening ribs — prismatic boss clusters whose "
            "bbox is long-and-thin (length / thickness > min ratio and "
            "thickness < max). Wraps detect_bosses + aspect-ratio filter.",
    selector_kinds=[],
    history_rules={},
    produces_features=["rib_inventory"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class DetectRibs(SkillBase):
    class Args(BaseModel):
        min_aspect_ratio: float = Field(
            default=3.0, gt=1.0, le=100.0,
            description="length / thickness ratio above which a thin block "
                        "is considered a rib (rather than a cube-ish boss).",
        )
        max_thickness_mm: float = Field(
            default=3.0, gt=0, le=50.0,
            description="Maximum thickness (shortest bbox extent) for the "
                        "cluster to qualify as a rib.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces, _face_area
        from phone_designer.skills.inspect.detect_bosses import (
            DetectBosses,
            _faces_bbox,
        )

        shape = _occt_shape(body)
        faces = _all_faces(shape)

        if len(faces) > MAX_FACES_RIBS:
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={
                    "ribs": [], "rib_count": 0,
                    "skipped_reason": f"face_count {len(faces)} > {MAX_FACES_RIBS}",
                },
            )

        boss_res = DetectBosses().apply(body, {"min_height_mm": 0.1})
        bosses = boss_res.extras.get("bosses", [])

        ribs: list[dict] = []
        next_id = 0
        for b in bosses:
            if b["type"] != "prismatic":
                continue
            cluster = b["face_indices"]
            cluster_objs = [faces[i] for i in cluster if i < len(faces)]
            if not cluster_objs:
                continue
            (mn, mx) = _faces_bbox(cluster_objs)
            size = (mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2])
            sorted_sz = sorted(size, reverse=True)
            length = float(sorted_sz[0])
            height = float(sorted_sz[1])
            thickness = float(sorted_sz[2])

            if thickness <= 0:
                continue
            if thickness >= args.max_thickness_mm:
                continue
            if (length / thickness) < args.min_aspect_ratio:
                continue

            # pick base face = the cluster's face with largest area
            best_fi: int | None = None
            best_area = -1.0
            for fi in cluster:
                if fi >= len(faces):
                    continue
                try:
                    a = _face_area(faces[fi])
                except Exception:
                    a = 0.0
                if a > best_area:
                    best_area = a
                    best_fi = fi

            ribs.append({
                "id": next_id,
                "length_mm": round(length, 4),
                "thickness_mm": round(thickness, 4),
                "height_mm": round(height, 4),
                "base_face": best_fi,
            })
            next_id += 1

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"ribs": ribs, "rib_count": len(ribs)},
        )
