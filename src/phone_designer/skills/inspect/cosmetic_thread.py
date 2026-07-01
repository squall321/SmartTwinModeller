"""cosmetic_thread — atomic, read-only annotation.

Tag a cylindrical bore/boss as *threaded* WITHOUT modeling the helix. This is
the DEFAULT thread representation on a CAD body — a modeled swept-V helix is
heavy (hundreds of spline faces, slow booleans, huge mesh) and almost never
needed for design intent, DFM, or a drawing callout. Instead we attach a small
metadata dict describing the thread and return the body geometrically
UNCHANGED.

The thread dimensions (major/minor diameter, pitch) come from the ISO metric
thread catalog reused from
``modify_pocket/tap_drill_hole.py`` (``catalogs/standards/threads_metric.yaml``):

    major_dia_mm = outer_d_mm            (catalog nominal outer diameter)
    pitch_mm     = pitch_mm              (catalog coarse pitch, unless the
                                          designation carries an explicit
                                          ``MxP`` pitch)
    minor_dia_mm = major_dia_mm - 1.0825 * pitch_mm   (ISO 68-1 internal
                                          thread basic minor diameter D1)

Designation forms accepted:
    'M6'        -> coarse-pitch M6  (pitch from catalog = 1.0)
    'M6x1.0'    -> M6 with an explicit pitch of 1.0 mm (fine/coarse override)
    'M6X1'      -> same (case- and separator-insensitive)

If ``face_selector`` is given, the tagged cylindrical face(s) are resolved and
recorded (count + which face index) so downstream steps / drawings know which
feature carries the thread. If omitted, the annotation applies to the whole
body.

The annotation is stored two ways:
    1. ``setattr(part, '_pd_thread', {...})`` on the returned body — a durable
       attribute that survives alongside ``_pd_tags`` for downstream skills.
    2. ``result.extras['cosmetic_thread'] = {...}`` — the diagnostic surface
       for the caller / MCP client.

extras schema:
    {"cosmetic_thread": {
        "designation": str,          # normalized, e.g. "M6x1.0"
        "major_dia_mm": float,       # nominal outer (catalog outer_d_mm)
        "minor_dia_mm": float,       # ISO 68-1 D1 = major - 1.0825*pitch
        "pitch_mm": float,
        "class": str,                # thread tolerance class, e.g. "6H"
        "depth_mm": float | None,    # threaded depth (None = full / through)
        "tapped_faces": int          # # cylindrical faces the selector tagged
     }}

body 는 변경하지 않는다 (post ``body_present``).
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


#: ISO 68-1 basic profile: internal thread minor diameter (D1) height factor.
#: D1 = D - 2 * (5/8) * H  where H = (sqrt(3)/2) * P  ->  D1 = D - 1.08253175*P.
_ISO_MINOR_INTERNAL_FACTOR = 1.08253175


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _load_threads() -> dict:
    import pathlib

    import yaml

    root = pathlib.Path(__file__).resolve().parents[4]
    data = yaml.safe_load(
        (root / "catalogs" / "standards" / "threads_metric.yaml").read_text()
    )
    return data.get("threads", {})


def _parse_designation(designation: str) -> tuple[str, float | None]:
    """Split 'M6', 'M6x1.0', 'M6X1' into (catalog_key, explicit_pitch|None).

    Returns the M-series catalog key ('M6', 'M2.5', ...) plus an optional
    explicit pitch pulled from the ``xP`` suffix. Whitespace and case are
    normalized; the separator may be 'x' or 'X'.
    """
    if not isinstance(designation, str) or not designation.strip():
        raise ValueError("cosmetic_thread: designation must be a non-empty string")
    s = designation.strip().replace(" ", "")
    m = re.fullmatch(r"[Mm](\d+(?:\.\d+)?)(?:[xX](\d+(?:\.\d+)?))?", s)
    if not m:
        raise ValueError(
            f"cosmetic_thread: cannot parse designation '{designation}' — "
            f"expected e.g. 'M6' or 'M6x1.0'"
        )
    dia_txt = m.group(1)
    # Normalize the catalog key: drop a trailing '.0' (M6.0 -> M6) so it lines
    # up with the catalog keys (which are 'M6', 'M2.5', ...).
    dia_val = float(dia_txt)
    if dia_val == int(dia_val):
        key = f"M{int(dia_val)}"
    else:
        key = f"M{dia_txt}"
    explicit_pitch = float(m.group(2)) if m.group(2) is not None else None
    return key, explicit_pitch


def _resolve_cyl_face_count(body: Any, selector: SelectorRef | None) -> int:
    """Count how many resolved faces are cylindrical (best-effort).

    Returns 0 when no selector is given (whole-body annotation) or when
    resolution / surface classification fails — never raises, so the
    annotation itself is robust.
    """
    if selector is None:
        return 0
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder

        from phone_designer.skills._resolvers import resolve_faces

        shape = _occt_shape(body)
        faces = resolve_faces(shape, selector, body=body)
        count = 0
        for f in faces:
            try:
                if BRepAdaptor_Surface(f).GetType() == GeomAbs_Cylinder:
                    count += 1
            except Exception:
                pass
        return count
    except Exception:
        return 0


@skill(
    name="cosmetic_thread",
    category="inspect",
    level="atomic",
    summary="Annotate a cylindrical bore/boss as threaded WITHOUT modeling the "
            "helix — the default, lightweight thread representation. Looks up "
            "major/minor diameter + pitch from the ISO metric thread catalog "
            "(threads_metric.yaml), stores the spec on the body via _pd_thread, "
            "and surfaces it in extras['cosmetic_thread']. Read-only — body "
            "geometry unchanged.",
    selector_kinds=["faces"],
    history_rules={},
    produces_features=["cosmetic_thread"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class CosmeticThread(SkillBase):
    class Args(BaseModel):
        designation: str = Field(
            min_length=1,
            description="Metric thread designation, e.g. 'M6' or 'M6x1.0'. "
                        "An explicit 'xP' suffix overrides the catalog pitch.",
        )
        face_selector: SelectorRef | None = Field(
            default=None,
            description="Optional selector for the cylindrical face(s) to tag. "
                        "If omitted, the whole body is annotated.",
        )
        thread_class: str = Field(
            default="6H",
            description="Thread tolerance class (e.g. '6H' internal, '6g' "
                        "external). Recorded only — not geometrically applied.",
        )
        depth_mm: float | None = Field(
            default=None,
            description="Threaded depth into the bore/boss. None = full / "
                        "through-thread.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("cosmetic_thread: body is None")

        key, explicit_pitch = _parse_designation(args.designation)

        threads = _load_threads()
        if key not in threads:
            raise ValueError(
                f"cosmetic_thread: unknown thread '{key}' (from "
                f"'{args.designation}') — available: {sorted(threads)}"
            )
        entry = threads[key]

        major_dia_mm = float(entry["outer_d_mm"])
        pitch_mm = (
            float(explicit_pitch)
            if explicit_pitch is not None
            else float(entry["pitch_mm"])
        )
        minor_dia_mm = major_dia_mm - _ISO_MINOR_INTERNAL_FACTOR * pitch_mm

        # Normalized designation for the record — always include the pitch so
        # the annotation is unambiguous ('M6' coarse -> 'M6x1.0').
        pitch_txt = (f"{pitch_mm:g}")
        norm_designation = f"{key}x{pitch_txt}"

        tapped_faces = _resolve_cyl_face_count(body, args.face_selector)

        thread_meta = {
            "designation": norm_designation,
            "major_dia_mm": round(major_dia_mm, 5),
            "minor_dia_mm": round(minor_dia_mm, 5),
            "pitch_mm": round(pitch_mm, 5),
            "class": str(args.thread_class),
            "depth_mm": (float(args.depth_mm) if args.depth_mm is not None else None),
            "tapped_faces": int(tapped_faces),
        }

        # Durable attribute on the returned body — survives alongside _pd_tags.
        try:
            setattr(body, "_pd_thread", dict(thread_meta))
        except Exception:
            # Some raw TopoDS shapes may reject attribute assignment; the extras
            # surface below is authoritative regardless.
            pass

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"cosmetic_thread": thread_meta},
        )
