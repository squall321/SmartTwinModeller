"""split_into_components — atomic, read-only.

Enumerate every TopoDS_Shell in the input body and report each as a
separate "component" entry. Designed for multi-shell inputs produced by
``mesh_to_brep`` on teardown / multi-part meshes — e.g. an iPhone glb
that yields ~65 disjoint shells (housing, front panel, back cover,
buttons, internal frames, ...) inside a single ``TopoDS_Compound``.

The skill **does not modify** the input body. Components live in
``extras["components"]`` so downstream skills (e.g.
``extract_feature_catalog(per_component=True)``) can iterate them.

extras schema::

    extras["components"] = [
        {
            "index":        int,                 # 0-based enumeration order
            "body_ref":     Part(<TopoDS_Shell>), # wrapped shell — usable as a body
            "shell_count":  1,                    # always 1 per entry by construction
            "closed":       bool,                 # BRepCheck_Shell.Closed() == NoError
            "bbox":         [xmin, ymin, zmin, xmax, ymax, zmax],
            "volume_mm3":   float,                # |VolumeProperties| of the shell
        },
        ...
    ]
    extras["component_count"]        = int
    extras["closed_component_count"] = int
    extras["skipped_small"]          = int   # shells dropped by min_volume_mm3

Tiny shells (< ``args.min_volume_mm3``) are treated as mesh artefacts and
omitted — these are typically stray triangles left over from decimation
or sewing slop. Default 0.1 mm³ catches sub-millimetre cube debris while
keeping anything the size of a screw head or larger.

post_conditions = [body_present]. Body is returned unchanged.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body):
    return body.wrapped if hasattr(body, "wrapped") else body


def _shell_volume_mm3(shell) -> float:
    """|VolumeProperties(shell).Mass()| — robust to open shells.

    For an open shell the signed volume integral is not physically
    meaningful (it depends on face orientation), but the magnitude is
    still useful as a rough size filter — the threshold catches
    triangles-as-debris (< 1 mm³) while passing anything part-shaped.
    """
    try:
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shell, props)
        return abs(float(props.Mass()))
    except Exception:
        return 0.0


def _shell_bbox(shell):
    """(xmin, ymin, zmin, xmax, ymax, zmax) or None on failure."""
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        bb = Bnd_Box()
        try:
            BRepBndLib.AddOptimal_s(shell, bb)
        except Exception:
            BRepBndLib.Add_s(shell, bb)
        if bb.IsVoid():
            return None
        return bb.Get()
    except Exception:
        return None


def _shell_is_closed(shell) -> bool:
    """BRepCheck_Shell.Closed() == BRepCheck_NoError — same idiom as
    mesh_to_brep's closure check."""
    try:
        from OCP.BRepCheck import BRepCheck_Shell, BRepCheck_Status
        checker = BRepCheck_Shell(shell)
        status = checker.Closed()
        return status == BRepCheck_Status.BRepCheck_NoError
    except Exception:
        return False


def _solid_shell_count(solid) -> int:
    """Number of shells in a solid (outer + each internal void). 1 = simple."""
    try:
        from OCP.TopAbs import TopAbs_SHELL
        from OCP.TopExp import TopExp_Explorer
        ex = TopExp_Explorer(solid, TopAbs_SHELL)
        n = 0
        while ex.More():
            n += 1
            ex.Next()
        return n
    except Exception:
        return 1


def _solid_is_closed(solid) -> bool:
    """A solid's OUTER shell closed? BRepCheck on the first shell.

    A genuine ``TopoDS_Solid`` from a STEP assembly is closed by construction;
    we still run the checker on its outer shell so a malformed import is
    reported honestly (closed=False) rather than assumed shut.
    """
    try:
        from OCP.TopAbs import TopAbs_SHELL
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS
        ex = TopExp_Explorer(solid, TopAbs_SHELL)
        if not ex.More():
            return False
        outer = TopoDS.Shell_s(ex.Current())
        return _shell_is_closed(outer)
    except Exception:
        return False


@skill(
    name="split_into_components",
    category="repair",
    level="atomic",
    summary="Enumerate every TopoDS_Shell in the body and report each as a "
            "separate component (wrapped Part + closed-ness + bbox + volume). "
            "Body unchanged; components live in extras['components']. Designed "
            "for multi-shell mesh→BREP outputs (iPhone teardown produces ~65 "
            "shells in one Compound).",
    selector_kinds=[],
    history_rules={},
    produces_features=["components"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class SplitIntoComponents(SkillBase):
    class Args(BaseModel):
        min_volume_mm3: float = Field(
            default=0.1, ge=0.0,
            description="Drop shells whose |volume| is below this threshold — "
                        "these are mesh artefacts (stray triangles left by "
                        "decimation/sewing). Default 0.1 mm³ keeps anything "
                        "the size of a small screw head or larger. Set to 0 "
                        "to keep every shell regardless of size.",
        )
        max_components: int = Field(
            default=10000, ge=1,
            description="Hard cap on enumerated components. Mostly a "
                        "defensive guard against pathological inputs.",
        )
        # pillar-perf (phase-4, 2026-06-15): SOLID-aware split. Default False
        # keeps the historic per-SHELL enumeration (byte-identical to the
        # mesh→BREP iPhone-teardown contract). True explodes the compound into
        # its constituent SOLIDS instead — the right granularity for a STEP
        # assembly whose leaves are solid bodies (a solid with an internal
        # cavity owns TWO shells, so a shell-split over-counts and slices one
        # physical body into two bogus components: RC_Buggy 211 solids → 293
        # shells). When True the split falls back to the shell path if the
        # body carries no solids (a pure open-shell mesh).
        split_solids: bool = Field(
            default=False,
            description="Explode the compound into its constituent SOLIDS "
                        "(one body per physical part) instead of enumerating "
                        "shells. Right for STEP assemblies; the historic "
                        "shell split (default) is right for mesh→BREP "
                        "multi-shell inputs. Falls back to the shell split "
                        "when the body has no solids.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.TopAbs import TopAbs_SHELL
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        shape = _occt_shape(body)

        components: list[dict] = []
        skipped_small = 0
        index = 0

        # SOLID-aware split (phase-4): one body per physical part. Fall back to
        # the historic shell enumeration when the body has no solids.
        split_mode = "shell"
        if args.split_solids:
            from phone_designer.skills.assembly._compound import (
                iter_solid_components,
            )
            solids = list(iter_solid_components(shape))
            if solids:
                split_mode = "solid"
                for solid in solids:
                    if index >= args.max_components:
                        break
                    vol = _shell_volume_mm3(solid)
                    if vol < float(args.min_volume_mm3):
                        skipped_small += 1
                        continue
                    bbox = _shell_bbox(solid)
                    # A solid is closed by construction; report True unless the
                    # checker (run on its outer shell) disagrees.
                    closed = _solid_is_closed(solid)
                    components.append({
                        "index": index,
                        "body_ref": Part(solid),
                        "shell_count": _solid_shell_count(solid),
                        "closed": bool(closed),
                        "bbox": (
                            [round(c, 6) for c in bbox]
                            if bbox is not None else None
                        ),
                        "volume_mm3": round(vol, 6),
                    })
                    index += 1

        if split_mode == "shell":
            it = TopExp_Explorer(shape, TopAbs_SHELL)
            while it.More() and index < args.max_components:
                shell = TopoDS.Shell_s(it.Current())
                it.Next()

                vol = _shell_volume_mm3(shell)
                if vol < float(args.min_volume_mm3):
                    skipped_small += 1
                    continue

                bbox = _shell_bbox(shell)
                closed = _shell_is_closed(shell)

                components.append({
                    "index": index,
                    "body_ref": Part(shell),
                    "shell_count": 1,
                    "closed": bool(closed),
                    "bbox": (
                        [round(c, 6) for c in bbox]
                        if bbox is not None else None
                    ),
                    "volume_mm3": round(vol, 6),
                })
                index += 1

        closed_count = sum(1 for c in components if c["closed"])

        extras = {
            "components": components,
            "component_count": len(components),
            "closed_component_count": closed_count,
            "skipped_small": skipped_small,
            "split_mode": split_mode,  # 'solid' | 'shell' (phase-4)
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
