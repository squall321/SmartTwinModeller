"""auto_datum_planes — atomic, read-only.

Identify the three "best" datum planes (A / B / C in ASME Y14.5 sense) on
the body using a simple greedy rule:

  * Datum A = largest planar face.
  * Datum B = largest planar face whose normal is perpendicular (≥ ~85°) to
    Datum A's normal.
  * Datum C = largest remaining planar face whose normal is perpendicular to
    BOTH A and B.

If the body has no planar faces, ``extras["datums"]`` is the empty dict.
Each value is the face index (0-based, in the order returned by
``_all_faces`` — matching ``inspect_geometry``'s indexing) so a follow-up
GD&T skill can address it by ``faces_by_index`` selector if desired.

extras schema::

    {"datums": {"A": face_idx, "B": face_idx, "C": face_idx},
     "details": {
         "A": {"face_idx": i, "area_mm2": ..., "normal": [...], "center": [...]},
         ...
     }}

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


def _is_planar_normal(n: tuple[float, float, float]) -> bool:
    return not (n == (0.0, 0.0, 0.0))


def _norm(n: tuple[float, float, float]) -> tuple[float, float, float]:
    mag = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
    if mag < 1e-12:
        return (0.0, 0.0, 0.0)
    return (n[0] / mag, n[1] / mag, n[2] / mag)


def _abs_dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2])


@skill(
    name="auto_datum_planes",
    category="inspect",
    level="atomic",
    summary="Identify the three best datum planes (A/B/C) of a body: largest "
            "planar face = A, largest perpendicular planar = B, third "
            "orthogonal = C. Stores face indices in extras['datums']. "
            "Read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["datum_frame_auto"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class AutoDatumPlanes(SkillBase):
    class Args(BaseModel):
        pass

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import (
            _all_faces,
            _face_area,
            _face_center,
            _face_normal_at_center,
        )

        shape = _occt_shape(body)
        faces = _all_faces(shape)

        # Collect planar faces with (index, area, normal, center).
        planar: list[tuple[int, float, tuple[float, float, float],
                            tuple[float, float, float]]] = []
        for i, f in enumerate(faces):
            try:
                n_raw = _face_normal_at_center(f)
            except Exception:
                continue
            if not _is_planar_normal(n_raw):
                continue
            try:
                a = _face_area(f)
            except Exception:
                a = 0.0
            try:
                c = _face_center(f)
            except Exception:
                c = (0.0, 0.0, 0.0)
            planar.append((i, float(a), _norm(n_raw), c))

        # Sort by area descending.
        planar.sort(key=lambda x: -x[1])

        # Datum A = largest planar.
        datums: dict[str, int] = {}
        details: dict[str, dict[str, Any]] = {}

        if not planar:
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={"datums": {}, "details": {}},
            )

        a_entry = planar[0]
        datums["A"] = a_entry[0]
        details["A"] = {
            "face_idx": a_entry[0],
            "area_mm2": round(a_entry[1], 4),
            "normal": [round(v, 4) for v in a_entry[2]],
            "center": [round(v, 4) for v in a_entry[3]],
        }

        # cosine threshold for "perpendicular" — within ~5° of 90°
        # i.e. |cos θ| ≤ sin(5°) ≈ 0.0872. Loosen to 0.15 (~ within 8°).
        perp_tol = 0.15
        a_n = a_entry[2]

        # Datum B = next-largest planar face whose normal is ⟂ to A.
        b_entry = None
        for entry in planar[1:]:
            if _abs_dot(entry[2], a_n) <= perp_tol:
                b_entry = entry
                break
        # Fallback if nothing is strictly perpendicular: pick next-largest
        # face that isn't anti-parallel to A.
        if b_entry is None:
            for entry in planar[1:]:
                if _abs_dot(entry[2], a_n) < 0.95:  # not parallel
                    b_entry = entry
                    break
        if b_entry is not None:
            datums["B"] = b_entry[0]
            details["B"] = {
                "face_idx": b_entry[0],
                "area_mm2": round(b_entry[1], 4),
                "normal": [round(v, 4) for v in b_entry[2]],
                "center": [round(v, 4) for v in b_entry[3]],
            }

        # Datum C = next-largest face ⟂ to both A and B.
        if b_entry is not None:
            b_n = b_entry[2]
            c_entry = None
            for entry in planar:
                if entry[0] in (a_entry[0], b_entry[0]):
                    continue
                if (_abs_dot(entry[2], a_n) <= perp_tol
                        and _abs_dot(entry[2], b_n) <= perp_tol):
                    c_entry = entry
                    break
            if c_entry is None:
                # Fallback — pick something not nearly parallel to A or B.
                for entry in planar:
                    if entry[0] in (a_entry[0], b_entry[0]):
                        continue
                    if (_abs_dot(entry[2], a_n) < 0.95
                            and _abs_dot(entry[2], b_n) < 0.95):
                        c_entry = entry
                        break
            if c_entry is not None:
                datums["C"] = c_entry[0]
                details["C"] = {
                    "face_idx": c_entry[0],
                    "area_mm2": round(c_entry[1], 4),
                    "normal": [round(v, 4) for v in c_entry[2]],
                    "center": [round(v, 4) for v in c_entry[3]],
                }

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"datums": datums, "details": details},
        )
