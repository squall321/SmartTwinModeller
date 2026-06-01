"""slicer_hints — atomic, read-only 3D printing DFM inspect.

Compute fast, slicer-style summary statistics for the body in the given build
orientation:

  * estimated_layer_count       — body extent along build_direction / layer_height
  * longest_layer_perimeter_mm  — perimeter of the body's projected footprint
                                  (proxy for the longest cross-section)
  * footprint_area_mm2          — projected area onto the build plate
  * total_print_time_min        — rough estimate using typical print speed
                                  (volume / (layer_height * line_width * speed))

The numbers are not slicer-accurate but are useful for relative comparison
between orientations / designs and for ballpark cost estimation.

extras["slicer_hints"] schema:
    {
        "build_direction": [bx, by, bz],
        "layer_height_mm": float,
        "line_width_mm": float,
        "estimated_layer_count": int,
        "build_height_mm": float,
        "footprint_area_mm2": float,
        "longest_layer_perimeter_mm": float,
        "volume_mm3": float,
        "estimated_print_time_min": float,
        "estimated_filament_length_mm": float,
    }
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


# Typical FDM print speed in mm/s along the toolpath. Used only for a coarse
# total_print_time estimate. SLA / SLS slicer math differs but we expose the
# same hint and let the caller interpret.
_TYPICAL_PRINT_SPEED_MMPS = 50.0


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _body_volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    try:
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props)
        return float(props.Mass())
    except Exception:
        return 0.0


def _project_extents(shape, bdir: tuple[float, float, float]) -> tuple[float, float]:
    """Return (min_proj, max_proj) of the body's bbox corners projected onto
    the given unit direction."""
    (mn, mx) = _bbox(shape)
    pmin = math.inf
    pmax = -math.inf
    for cx in (mn[0], mx[0]):
        for cy in (mn[1], mx[1]):
            for cz in (mn[2], mx[2]):
                pv = cx * bdir[0] + cy * bdir[1] + cz * bdir[2]
                if pv < pmin:
                    pmin = pv
                if pv > pmax:
                    pmax = pv
    if not math.isfinite(pmin):
        return (0.0, 0.0)
    return (pmin, pmax)


def _bbox(shape) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """OCCT bbox (min, max). Returns zero box on failure."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    try:
        BRepBndLib.Add_s(shape, bb)
    except Exception:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    if bb.IsVoid():
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return ((xmin, ymin, zmin), (xmax, ymax, zmax))


def _footprint_estimate(
    shape, bdir: tuple[float, float, float],
) -> tuple[float, float]:
    """Return (footprint_area_mm2, longest_layer_perimeter_mm).

    Computed as the bounding-box cross section perpendicular to the build
    axis. Cheap and orientation-aware; not slicer-accurate but adequate for
    relative ordering. Falls back to whole-bbox area / perimeter when the
    build axis is non-axis-aligned (we still project the bbox).
    """
    (mn, mx) = _bbox(shape)
    sx, sy, sz = mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]

    # Axis-aligned shortcut: pick the two extents perpendicular to bdir.
    ax = abs(bdir[0])
    ay = abs(bdir[1])
    az = abs(bdir[2])
    if az >= 0.99:
        a, b = sx, sy
    elif ax >= 0.99:
        a, b = sy, sz
    elif ay >= 0.99:
        a, b = sx, sz
    else:
        # Mixed build direction — project bbox vertices onto a plane normal
        # to bdir and use the spread along two orthogonal in-plane axes.
        # Pick an in-plane axis u perpendicular to bdir.
        if abs(bdir[2]) < 0.9:
            ux, uy, uz = -bdir[1], bdir[0], 0.0
        else:
            ux, uy, uz = 1.0, 0.0, 0.0
        umag = math.sqrt(ux * ux + uy * uy + uz * uz)
        if umag < 1e-12:
            ux, uy, uz = 1.0, 0.0, 0.0
            umag = 1.0
        ux, uy, uz = ux / umag, uy / umag, uz / umag
        # v = bdir × u (right-hand)
        vx = bdir[1] * uz - bdir[2] * uy
        vy = bdir[2] * ux - bdir[0] * uz
        vz = bdir[0] * uy - bdir[1] * ux
        umin = vmin = math.inf
        umax = vmax = -math.inf
        for cx in (mn[0], mx[0]):
            for cy in (mn[1], mx[1]):
                for cz in (mn[2], mx[2]):
                    pu = cx * ux + cy * uy + cz * uz
                    pv = cx * vx + cy * vy + cz * vz
                    umin = min(umin, pu)
                    umax = max(umax, pu)
                    vmin = min(vmin, pv)
                    vmax = max(vmax, pv)
        a = umax - umin
        b = vmax - vmin
    a = max(0.0, a)
    b = max(0.0, b)
    area = a * b
    perim = 2.0 * (a + b)
    return (area, perim)


@skill(
    name="slicer_hints",
    category="inspect",
    level="atomic",
    summary="Compute layer count, longest cross-section perimeter, and a "
            "rough print-time estimate for the body in the given build "
            "orientation. Read-only — body unchanged; report in "
            "extras['slicer_hints'].",
    selector_kinds=[],
    history_rules={},
    produces_features=["slicer_hints_report"],
    preserves=["body_topology"],
    manufacturing={
        "fdm":  {"extras": {"typical_print_speed_mmps": 50.0,
                            "layer_height_default_mm": 0.2,
                            "line_width_default_mm": 0.4}},
        "sla":  {"extras": {"layer_height_default_mm": 0.05,
                            "typical_layer_time_s": 8.0}},
        "sls":  {"extras": {"layer_height_default_mm": 0.1,
                            "self_supporting": True}},
    },
    failure_modes=["fm.zero_build_vector", "fm.zero_layer_height"],
    cost_hint=0.1,
    post_conditions=[PostCondition(kind="body_present")],
)
class SlicerHints(SkillBase):
    class Args(BaseModel):
        layer_height_mm: float = Field(default=0.2, gt=0.0, le=5.0)
        line_width_mm: float = Field(default=0.4, gt=0.0, le=5.0)
        build_direction: tuple[float, float, float] = Field(default=(0.0, 0.0, 1.0))
        print_speed_mmps: float = Field(default=_TYPICAL_PRINT_SPEED_MMPS,
                                        gt=0.0, le=1000.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        bx, by, bz = args.build_direction
        blen = math.sqrt(bx * bx + by * by + bz * bz)
        if blen < 1e-12:
            raise ValueError(
                "slicer_hints: build_direction must be non-zero",
            )
        bdir = (bx / blen, by / blen, bz / blen)

        shape = _occt_shape(body)
        pmin, pmax = _project_extents(shape, bdir)
        build_height = max(0.0, pmax - pmin)
        n_layers = int(math.ceil(build_height / args.layer_height_mm))
        if n_layers < 0:
            n_layers = 0

        footprint_area, perim = _footprint_estimate(shape, bdir)
        volume = _body_volume(shape)

        # Toolpath length ≈ volume / (layer_height * line_width).
        # Print time = toolpath_length / speed.
        denom = args.layer_height_mm * args.line_width_mm
        if denom > 0.0:
            toolpath_mm = volume / denom
            print_time_s = toolpath_mm / args.print_speed_mmps
        else:
            toolpath_mm = 0.0
            print_time_s = 0.0
        print_time_min = print_time_s / 60.0

        # Filament length ≈ volume / cross-section of 1.75 mm filament.
        filament_area = math.pi * (1.75 / 2.0) ** 2  # ~2.405 mm²
        filament_len = volume / filament_area if filament_area > 0 else 0.0

        report = {
            "build_direction": [round(c, 4) for c in bdir],
            "layer_height_mm": args.layer_height_mm,
            "line_width_mm": args.line_width_mm,
            "print_speed_mmps": args.print_speed_mmps,
            "estimated_layer_count": n_layers,
            "build_height_mm": round(build_height, 4),
            "footprint_area_mm2": round(footprint_area, 4),
            "longest_layer_perimeter_mm": round(perim, 4),
            "volume_mm3": round(volume, 4),
            "estimated_toolpath_length_mm": round(toolpath_mm, 2),
            "estimated_print_time_min": round(print_time_min, 2),
            "estimated_filament_length_mm": round(filament_len, 2),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"slicer_hints": report},
        )
