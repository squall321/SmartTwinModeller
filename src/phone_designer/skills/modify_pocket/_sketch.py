"""SketchSpec — pocket/plateau 의 2D 단면 표현.

Phase 1: circle / rectangle / rounded_rect.
Phase 4 advanced: polygon / slot / bspline_closed / composite / ellipse — closed-loop
2D shapes that can be filleted at vertices and extruded into prisms.
Phase 4 curved-area: parametric / polar / lissajous — analytical curve sketches
sampled into points then interpolated as periodic B-splines.
"""
from __future__ import annotations

import math
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator

EPS_CLOSE = 1e-3  # 1 µm — segment endpoint match tolerance
_TWO_PI = 6.283185307179586  # 2π as plain float (no math import at field-default time)


# ── original (Phase 1) sketches ──────────────────────────────────────────────


class CircleSketch(BaseModel):
    kind: Literal["circle"] = "circle"
    diameter_mm: float = Field(gt=0)
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0


class RectangleSketch(BaseModel):
    kind: Literal["rectangle"] = "rectangle"
    length_mm: float = Field(gt=0)
    width_mm: float = Field(gt=0)
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0


class RoundedRectSketch(BaseModel):
    kind: Literal["rounded_rect"] = "rounded_rect"
    length_mm: float = Field(gt=0)
    width_mm: float = Field(gt=0)
    corner_r_mm: float = Field(gt=0)
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0


# ── advanced (Phase 4) sketches ──────────────────────────────────────────────


class PolygonSketch(BaseModel):
    """Closed polygon defined by ≥3 vertices. Last vertex auto-connects to first.

    `corner_fillet_r` > 0 applies a uniform 2D fillet on every vertex via
    BRepFilletAPI_MakeFillet2d.
    """

    kind: Literal["polygon"] = "polygon"
    vertices: list[tuple[float, float]] = Field(min_length=3)
    corner_fillet_r: float = Field(default=0.0, ge=0.0)
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0

    @field_validator("vertices")
    @classmethod
    def _vertices_non_degenerate(cls, v: list[tuple[float, float]]):
        # Reject consecutive duplicates (would produce zero-length edges).
        for i in range(len(v)):
            a = v[i]
            b = v[(i + 1) % len(v)]
            if math.hypot(a[0] - b[0], a[1] - b[1]) < EPS_CLOSE:
                raise ValueError(
                    f"polygon vertex {i} and {(i+1) % len(v)} are coincident"
                )
        return v


class SlotSketch(BaseModel):
    """Capsule shape: two semicircular end caps joined by two straight lines.

    `length_mm` is the overall length including end caps.
    `width_mm` is the diameter of the end caps (also the slot width).
    """

    kind: Literal["slot"] = "slot"
    length_mm: float = Field(gt=0)
    width_mm: float = Field(gt=0)
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0
    rotation_deg: float = 0.0

    @model_validator(mode="after")
    def _length_at_least_width(self):
        if self.length_mm < self.width_mm:
            raise ValueError(
                f"slot length_mm ({self.length_mm}) must be ≥ width_mm "
                f"({self.width_mm})"
            )
        return self


class BSplineSketch(BaseModel):
    """Closed periodic B-spline interpolating through `fit_points`.

    Built via `GeomAPI_Interpolate(points, PeriodicFlag=True, tol)`.
    """

    kind: Literal["bspline_closed"] = "bspline_closed"
    fit_points: list[tuple[float, float]] = Field(min_length=3)
    degree: int = Field(default=3, ge=2, le=5)  # informational
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0


class EllipseSketch(BaseModel):
    kind: Literal["ellipse"] = "ellipse"
    radius_x_mm: float = Field(gt=0)
    radius_y_mm: float = Field(gt=0)
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0
    rotation_deg: float = 0.0


# ── analytical curve sketches (Phase 4 curved-area) ─────────────────────────


class ParametricCurveSketch(BaseModel):
    """Parametric curve x(t), y(t) sampled in [t_min, t_max].

    Both `x_expr` and `y_expr` are simpleeval expressions in variable `t`.
    Allowed funcs: sin/cos/tan/exp/sqrt/abs; consts: pi, e.
    The sampled points are interpolated as a periodic B-spline if
    `close_curve` is True (typical for closed-loop sketches), otherwise as a
    non-periodic spline whose first/last endpoints will be auto-closed with a
    short tangent segment by the wire builder.
    """

    kind: Literal["parametric"] = "parametric"
    x_expr: str
    y_expr: str
    t_min: float = 0.0
    t_max: float = _TWO_PI
    samples: int = Field(default=100, ge=10, le=2000)
    close_curve: bool = True
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0

    @model_validator(mode="after")
    def _range_valid(self):
        if self.t_max <= self.t_min:
            raise ValueError(
                f"parametric t_max ({self.t_max}) must be > t_min ({self.t_min})"
            )
        return self


class PolarCurveSketch(BaseModel):
    """Polar curve r(theta) sampled in [theta_min, theta_max].

    `r_expr` is a simpleeval expression in variable `theta`. The sampled
    points (x = r·cos θ, y = r·sin θ) are always closed (periodic spline).
    """

    kind: Literal["polar"] = "polar"
    r_expr: str
    theta_min: float = 0.0
    theta_max: float = _TWO_PI
    samples: int = Field(default=200, ge=10, le=2000)
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0

    @model_validator(mode="after")
    def _range_valid(self):
        if self.theta_max <= self.theta_min:
            raise ValueError(
                f"polar theta_max ({self.theta_max}) must be > theta_min "
                f"({self.theta_min})"
            )
        return self


class LissajousCurveSketch(BaseModel):
    """Closed Lissajous figure (shorthand for a common parametric curve).

    x(t) = amp_x · sin(freq_x · t + phase)
    y(t) = amp_y · sin(freq_y · t)
    t ∈ [0, 2π].

    With integer freq_x / freq_y and a non-zero phase the curve is closed
    (lobes form a figure 8 / star). Always built as a periodic spline.
    """

    kind: Literal["lissajous"] = "lissajous"
    amp_x: float = Field(gt=0)
    amp_y: float = Field(gt=0)
    freq_x: float = Field(gt=0)
    freq_y: float = Field(gt=0)
    phase_deg: float = 90.0
    samples: int = Field(default=200, ge=10, le=2000)
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0


# ── composite segments ──────────────────────────────────────────────────────


class LineSegment(BaseModel):
    kind: Literal["line"] = "line"
    start: tuple[float, float]
    end: tuple[float, float]


class ArcSegment(BaseModel):
    """Arc from start to end on a circle of `radius`, direction selected by `ccw`.

    Validation: chord length ≤ 2·radius.
    """

    kind: Literal["arc"] = "arc"
    start: tuple[float, float]
    end: tuple[float, float]
    radius: float = Field(gt=0)
    ccw: bool = True

    @model_validator(mode="after")
    def _chord_fits(self):
        sx, sy = self.start
        ex, ey = self.end
        chord = math.hypot(ex - sx, ey - sy)
        if chord > 2 * self.radius + EPS_CLOSE:
            raise ValueError(
                f"arc chord {chord:.4f} > 2*radius ({2*self.radius:.4f})"
            )
        return self


class SplineSegment(BaseModel):
    """Open (non-periodic) interpolating B-spline through `points`."""

    kind: Literal["spline"] = "spline"
    points: list[tuple[float, float]] = Field(min_length=2)

    @property
    def start(self) -> tuple[float, float]:
        return self.points[0]

    @property
    def end(self) -> tuple[float, float]:
        return self.points[-1]


Segment = Annotated[
    Union[LineSegment, ArcSegment, SplineSegment],
    Field(discriminator="kind"),
]


class CompositeSketch(BaseModel):
    """Closed loop made of line/arc/spline segments.

    Segments are validated to form a closed chain: end of segment i must equal
    start of segment i+1 (within EPS_CLOSE), and last.end == first.start.
    """

    kind: Literal["composite"] = "composite"
    segments: list[Segment] = Field(min_length=2)
    corner_fillet_r: float = Field(default=0.0, ge=0.0)
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0

    @model_validator(mode="after")
    def _closed_loop(self):
        n = len(self.segments)
        for i in range(n):
            cur = self.segments[i]
            nxt = self.segments[(i + 1) % n]
            ex, ey = cur.end
            sx, sy = nxt.start
            if math.hypot(ex - sx, ey - sy) > EPS_CLOSE:
                raise ValueError(
                    f"composite segment {i}.end {cur.end} != "
                    f"segment {(i+1) % n}.start {nxt.start}"
                )
        return self


# ── discriminated union ─────────────────────────────────────────────────────


SketchSpec = Annotated[
    Union[
        CircleSketch,
        RectangleSketch,
        RoundedRectSketch,
        PolygonSketch,
        SlotSketch,
        BSplineSketch,
        EllipseSketch,
        CompositeSketch,
        ParametricCurveSketch,
        PolarCurveSketch,
        LissajousCurveSketch,
    ],
    Field(discriminator="kind"),
]
