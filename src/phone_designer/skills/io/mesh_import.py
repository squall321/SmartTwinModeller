"""mesh_import — atomic create. OBJ / PLY mesh file → BRep shell/solid.

Closes the scan-to-CAD front door: ``mesh_to_brep`` handles STL only (it
explicitly raises NotImplementedError for OBJ), and ``stl_import`` returns an
unsewn polygon soup. This skill parses the two most common neutral scanner /
DCC formats with PURE PYTHON (no new deps) and then reuses the proven
``mesh_to_brep`` build recipe:

Parsing
-------
* **OBJ** — ASCII ``v x y z`` + ``f a b c ...`` lines. Handles 1-based AND
  negative (relative) indices, the ``a/b``, ``a//c``, ``a/b/c`` token forms,
  and fan-triangulates polygons with >3 vertices. ``vn``/``vt``/``o``/``g``/
  ``usemtl``/... lines are ignored.
* **PLY** — full header parse (``element``/``property``/``property list``),
  then **both** ``ascii`` and ``binary_little_endian`` / ``binary_big_endian``
  bodies (``struct``-based). Extra vertex properties (normals, colors — the
  scanner case) are consumed and discarded; only x/y/z and the face
  ``vertex_indices`` list are kept. Polygon faces are fan-triangulated.

Build (mirrors mesh_to_brep, round-6 hardened)
----------------------------------------------
Per-triangle ``BRepBuilderAPI_MakePolygon`` → ``MakeFace``, degenerate
triangles skipped via ``mesh_to_brep._triangle_is_degenerate`` (imported, not
copied), chunked ``BRepBuilderAPI_Sewing`` with progress logging, closure
detection via ``BRepCheck_Shell``, and — when exactly one closed shell comes
out and ``try_make_solid`` — ``BRepBuilderAPI_MakeSolid``. If the resulting
solid has NEGATIVE signed volume (inward-wound source mesh — proven trap) we
run ``ShapeFix_Solid`` and, as a last resort, reverse the solid so the
reported volume is positive.

Honesty gates
-------------
``is_solid`` is True only when the result contains a ``TopAbs_SOLID`` **and**
its measured volume exceeds 1e-6 mm³ — ``VolumeProperties_s`` returns a
nonzero pseudo-mass for open shells, so the volume alone is NOT proof.
``volume_mm3`` is ``None`` (strict-JSON-safe) when the result is not a solid,
for the same reason. Open meshes import honestly as shells with
``free_edge_count > 0``.

extras schema:
    {
      "format": "obj" | "ply",
      "n_vertices": int,            # parsed from the file
      "n_triangles": int,           # after fan-triangulation
      "n_degenerate_skipped": int,
      "is_solid": bool,             # TopAbs_SOLID count AND volume > 1e-6
      "volume_mm3": float | None,   # None unless is_solid (pseudo-mass trap)
      "bbox_mm": [xmin, ymin, zmin, xmax, ymax, zmax],
      "free_edge_count": int,       # sewer's un-merged boundary edges
    }
"""
from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.io.mesh_to_brep import _triangle_is_degenerate


log = logging.getLogger(__name__)

_CHUNK = 5000  # sewing progress-log cadence (mirrors mesh_to_brep default)

Vec3 = tuple[float, float, float]
Tri = tuple[int, int, int]


# --------------------------------------------------------------------------- #
# format inference
# --------------------------------------------------------------------------- #

def _infer_format(path: str, explicit: str | None) -> str:
    """Resolve 'obj' | 'ply' from the explicit arg or the file extension."""
    if explicit:
        return explicit.strip().lower()  # pydantic Literal already validated
    ext = Path(path).suffix.lower().lstrip(".")
    if ext in ("obj", "ply"):
        return ext
    raise ValueError(
        f"fm.unsupported_format: cannot infer mesh format from extension "
        f"{ext!r} (expected .obj or .ply); pass format='obj' or format='ply'"
    )


# --------------------------------------------------------------------------- #
# shared parse helpers
# --------------------------------------------------------------------------- #

def _fan_triangulate(idxs: list[int], n_verts: int,
                     where: str) -> list[Tri]:
    """Polygon (0-based, validated) → fan triangles (v0, vi, vi+1)."""
    if len(idxs) < 3:
        raise ValueError(
            f"fm.mesh_parse_failed: {where}: face has {len(idxs)} vertices "
            "(need >= 3)")
    for i in idxs:
        if i < 0 or i >= n_verts:
            raise ValueError(
                f"fm.mesh_parse_failed: {where}: vertex index {i} out of "
                f"range [0, {n_verts})")
    return [(idxs[0], idxs[k], idxs[k + 1]) for k in range(1, len(idxs) - 1)]


# --------------------------------------------------------------------------- #
# OBJ parser
# --------------------------------------------------------------------------- #

def _parse_obj(data: bytes) -> tuple[list[Vec3], list[Tri]]:
    """ASCII OBJ → (vertices, triangles). 0-based output indices.

    Handles 1-based positive indices, NEGATIVE (relative-to-current-count)
    indices, ``a``/``a/b``/``a//c``/``a/b/c`` face-token forms, and
    fan-triangulates polygons with more than 3 vertices.
    """
    text = data.decode("utf-8", errors="replace")
    verts: list[Vec3] = []
    tris: list[Tri] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        tag = parts[0]
        if tag == "v":
            if len(parts) < 4:
                raise ValueError(
                    f"fm.mesh_parse_failed: OBJ line {lineno}: vertex needs "
                    f"3 coordinates, got {len(parts) - 1}")
            try:
                verts.append((float(parts[1]), float(parts[2]),
                              float(parts[3])))
            except ValueError as exc:
                raise ValueError(
                    f"fm.mesh_parse_failed: OBJ line {lineno}: bad vertex "
                    f"coordinate: {exc}") from exc
        elif tag == "f":
            idxs: list[int] = []
            for tok in parts[1:]:
                head = tok.split("/")[0]  # a | a/b | a//c | a/b/c
                try:
                    i = int(head)
                except ValueError as exc:
                    raise ValueError(
                        f"fm.mesh_parse_failed: OBJ line {lineno}: bad face "
                        f"index token {tok!r}") from exc
                if i == 0:
                    raise ValueError(
                        f"fm.mesh_parse_failed: OBJ line {lineno}: face "
                        "index 0 is invalid (OBJ is 1-based)")
                # negative = relative to the vertex count AT THIS LINE
                idxs.append(len(verts) + i if i < 0 else i - 1)
            tris.extend(_fan_triangulate(idxs, len(verts),
                                         f"OBJ line {lineno}"))
        # vn / vt / vp / o / g / s / l / p / usemtl / mtllib → ignored
    if not verts or not tris:
        raise ValueError(
            f"fm.mesh_parse_failed: OBJ contains {len(verts)} vertices and "
            f"{len(tris)} triangles — not a usable mesh")
    return verts, tris


# --------------------------------------------------------------------------- #
# PLY parser (ascii + binary little/big endian)
# --------------------------------------------------------------------------- #

_PLY_TYPES: dict[str, tuple[str, int]] = {
    "char": ("b", 1), "int8": ("b", 1),
    "uchar": ("B", 1), "uint8": ("B", 1),
    "short": ("h", 2), "int16": ("h", 2),
    "ushort": ("H", 2), "uint16": ("H", 2),
    "int": ("i", 4), "int32": ("i", 4),
    "uint": ("I", 4), "uint32": ("I", 4),
    "float": ("f", 4), "float32": ("f", 4),
    "double": ("d", 8), "float64": ("d", 8),
}

_FACE_LIST_NAMES = ("vertex_indices", "vertex_index")


def _ply_type(name: str) -> tuple[str, int]:
    try:
        return _PLY_TYPES[name]
    except KeyError:
        raise ValueError(
            f"fm.mesh_parse_failed: PLY: unknown property type {name!r}")


def _parse_ply_header(data: bytes) -> tuple[str, list, bytes]:
    """→ (format, [(elem_name, count, [prop...])], body_bytes).

    prop = ("scalar", type_name, prop_name)
         | ("list", count_type, index_type, prop_name)
    """
    if not data.startswith(b"ply"):
        raise ValueError(
            "fm.mesh_parse_failed: PLY: file does not start with 'ply' magic")
    end = data.find(b"end_header")
    if end < 0:
        raise ValueError("fm.mesh_parse_failed: PLY: missing 'end_header'")
    nl = data.find(b"\n", end)
    if nl < 0:
        raise ValueError(
            "fm.mesh_parse_failed: PLY: no newline after 'end_header'")
    header = data[:nl].decode("ascii", errors="replace")
    body = data[nl + 1:]

    fmt: str | None = None
    elements: list[list] = []  # [name, count, [props]]
    for line in header.splitlines():
        s = line.strip()
        if (not s or s == "ply" or s.startswith("comment")
                or s.startswith("obj_info")):
            continue
        toks = s.split()
        if toks[0] == "format":
            if len(toks) < 2:
                raise ValueError(
                    "fm.mesh_parse_failed: PLY: malformed 'format' line")
            fmt = toks[1]
        elif toks[0] == "element":
            if len(toks) < 3:
                raise ValueError(
                    f"fm.mesh_parse_failed: PLY: malformed element line "
                    f"{s!r}")
            try:
                elements.append([toks[1], int(toks[2]), []])
            except ValueError as exc:
                raise ValueError(
                    f"fm.mesh_parse_failed: PLY: bad element count in "
                    f"{s!r}") from exc
        elif toks[0] == "property":
            if not elements:
                raise ValueError(
                    "fm.mesh_parse_failed: PLY: property before any element")
            if len(toks) >= 5 and toks[1] == "list":
                elements[-1][2].append(("list", toks[2], toks[3], toks[4]))
            elif len(toks) >= 3:
                elements[-1][2].append(("scalar", toks[1], toks[2]))
            else:
                raise ValueError(
                    f"fm.mesh_parse_failed: PLY: malformed property line "
                    f"{s!r}")
        elif toks[0] == "end_header":
            break
        # unknown header keywords tolerated
    if fmt not in ("ascii", "binary_little_endian", "binary_big_endian"):
        raise ValueError(
            f"fm.mesh_parse_failed: PLY: unsupported format {fmt!r} "
            "(expected ascii / binary_little_endian / binary_big_endian)")
    return fmt, elements, body


def _parse_ply(data: bytes) -> tuple[list[Vec3], list[Tri]]:
    """PLY (ascii OR binary little/big endian) → (vertices, triangles)."""
    fmt, elements, body = _parse_ply_header(data)

    verts: list[Vec3] = []
    polys: list[list[int]] = []

    if fmt == "ascii":
        toks = body.decode("ascii", errors="replace").split()
        cur = 0

        def take() -> str:
            nonlocal cur
            if cur >= len(toks):
                raise ValueError(
                    "fm.mesh_parse_failed: PLY ascii body ended prematurely")
            t = toks[cur]
            cur += 1
            return t

        for name, count, props in elements:
            for _ in range(count):
                vals: dict[str, float] = {}
                for prop in props:
                    if prop[0] == "scalar":
                        t = take()
                        if name == "vertex" and prop[2] in ("x", "y", "z"):
                            vals[prop[2]] = float(t)
                    else:  # list
                        n = int(take())
                        items = [take() for _ in range(n)]
                        if name == "face" and prop[3] in _FACE_LIST_NAMES:
                            polys.append([int(x) for x in items])
                if name == "vertex":
                    if len(vals) != 3:
                        raise ValueError(
                            "fm.mesh_parse_failed: PLY vertex element lacks "
                            "x/y/z properties")
                    verts.append((vals["x"], vals["y"], vals["z"]))
    else:
        prefix = "<" if fmt == "binary_little_endian" else ">"
        off = 0
        for name, count, props in elements:
            for _ in range(count):
                vals = {}
                for prop in props:
                    if prop[0] == "scalar":
                        code, size = _ply_type(prop[1])
                        (v,) = struct.unpack_from(prefix + code, body, off)
                        off += size
                        if name == "vertex" and prop[2] in ("x", "y", "z"):
                            vals[prop[2]] = float(v)
                    else:  # list
                        ccode, csize = _ply_type(prop[1])
                        icode, isize = _ply_type(prop[2])
                        (n,) = struct.unpack_from(prefix + ccode, body, off)
                        off += csize
                        items = struct.unpack_from(
                            prefix + str(int(n)) + icode, body, off)
                        off += int(n) * isize
                        if name == "face" and prop[3] in _FACE_LIST_NAMES:
                            polys.append([int(x) for x in items])
                if name == "vertex":
                    if len(vals) != 3:
                        raise ValueError(
                            "fm.mesh_parse_failed: PLY vertex element lacks "
                            "x/y/z properties")
                    verts.append((vals["x"], vals["y"], vals["z"]))

    tris: list[Tri] = []
    for k, poly in enumerate(polys):
        tris.extend(_fan_triangulate(poly, len(verts), f"PLY face {k}"))
    if not verts or not tris:
        raise ValueError(
            f"fm.mesh_parse_failed: PLY contains {len(verts)} vertices and "
            f"{len(tris)} triangles — not a usable mesh")
    return verts, tris


# --------------------------------------------------------------------------- #
# geometry helpers
# --------------------------------------------------------------------------- #

def _volume(shape) -> float:
    """Signed volume via BRepGProp. NOTE: nonzero pseudo-mass for open shells
    — never use this alone as a solidity proof."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


def _count_solids(shape) -> int:
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    n = 0
    it = TopExp_Explorer(shape, TopAbs_SOLID)
    while it.More():
        n += 1
        it.Next()
    return n


def _bbox_of_vertices(verts: list[Vec3]) -> list[float]:
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    return [float(min(xs)), float(min(ys)), float(min(zs)),
            float(max(xs)), float(max(ys)), float(max(zs))]


# --------------------------------------------------------------------------- #
# skill
# --------------------------------------------------------------------------- #

@skill(
    name="mesh_import",
    category="create",
    level="atomic",
    summary="Read an OBJ or PLY triangle-mesh file (ASCII OBJ; ASCII + binary "
            "little/big-endian PLY — pure-Python parsers, no new deps) and "
            "sew the triangles into a BRep shell/solid with the proven "
            "mesh_to_brep recipe (per-triangle faces, chunked sewing, closure "
            "detection, negative-volume orientation fix). Watertight meshes "
            "lift to a TopoDS_Solid; open meshes return an honest shell with "
            "free_edge_count > 0 and volume_mm3=None. Closes the OBJ/PLY "
            "scan-to-CAD gap left by mesh_to_brep (STL only).",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["imported_mesh_brep"],
    preserves=[],
    manufacturing={},
    failure_modes=[
        "fm.file_not_found",
        "fm.unsupported_format",
        "fm.mesh_parse_failed",
        "fm.too_many_triangles",
        "fm.sewing_failed",
    ],
    cost_hint=0.6,
    post_conditions=[PostCondition(kind="body_present")],
)
class MeshImport(SkillBase):
    class Args(BaseModel):
        path: str = Field(min_length=1,
                          description="입력 메시 파일 경로 (.obj 또는 .ply)")
        format: Literal["obj", "ply"] | None = Field(
            default=None,
            description="'obj' 또는 'ply'. 생략하면 path 확장자에서 추론.",
        )
        sewing_tolerance_mm: float = Field(
            default=1e-3, gt=0,
            description="BRepBuilderAPI_Sewing tolerance. Mesh edges within "
                        "this distance are merged. Also gates the "
                        "degenerate-triangle filter.",
        )
        try_make_solid: bool = Field(
            default=True,
            description="After sewing, lift a single closed shell to a "
                        "TopoDS_Solid. Falls back to the shell when the mesh "
                        "isn't watertight.",
        )
        max_triangles: int = Field(
            default=200000, ge=1,
            description="Guard against pathological inputs — parsing more "
                        "triangles than this raises fm.too_many_triangles. "
                        "Decimate first (mesh_decimate / mesh_simplify).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeFace,
            BRepBuilderAPI_MakePolygon,
            BRepBuilderAPI_MakeSolid,
            BRepBuilderAPI_Sewing,
        )
        from OCP.BRepCheck import BRepCheck_Shell, BRepCheck_Status
        from OCP.gp import gp_Pnt
        from OCP.TopAbs import TopAbs_SHELL
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        # --- read ----------------------------------------------------------
        p = Path(args.path)
        if not p.exists():
            raise ValueError(f"fm.file_not_found: mesh file not found: {p}")
        fmt = _infer_format(args.path, args.format)
        try:
            data = p.read_bytes()
        except OSError as exc:
            raise ValueError(
                f"fm.file_not_found: cannot read {p}: "
                f"{type(exc).__name__}: {exc}") from exc

        # --- parse (wrap any raw error into the declared failure mode) ------
        try:
            if fmt == "obj":
                verts, tris = _parse_obj(data)
            else:
                verts, tris = _parse_ply(data)
        except ValueError as exc:
            if str(exc).startswith("fm."):
                raise
            raise ValueError(
                f"fm.mesh_parse_failed: {fmt.upper()}: {exc}") from exc
        except Exception as exc:
            raise ValueError(
                f"fm.mesh_parse_failed: {fmt.upper()}: "
                f"{type(exc).__name__}: {exc}") from exc

        if len(tris) > int(args.max_triangles):
            raise ValueError(
                f"fm.too_many_triangles: mesh has {len(tris)} triangles, "
                f"limit is {args.max_triangles}. Decimate first "
                "(mesh_decimate / mesh_simplify) or raise max_triangles.")

        # --- sew (mesh_to_brep recipe: per-triangle faces, chunked) ---------
        tol = float(args.sewing_tolerance_mm)
        sewer = BRepBuilderAPI_Sewing(tol)
        skipped = 0
        added = 0
        log.info("mesh_import: sewing %d triangles in chunks of %d "
                 "(tol=%.4g mm)", len(tris), _CHUNK, tol)
        for i, (a, b, c) in enumerate(tris, 1):
            try:
                p1 = gp_Pnt(*verts[a])
                p2 = gp_Pnt(*verts[b])
                p3 = gp_Pnt(*verts[c])
                if _triangle_is_degenerate(p1, p2, p3, tol):
                    skipped += 1
                    continue
                poly = BRepBuilderAPI_MakePolygon(p1, p2, p3, True)
                face_maker = BRepBuilderAPI_MakeFace(poly.Wire(), True)
                if not face_maker.IsDone():
                    skipped += 1
                    continue
                sewer.Add(face_maker.Face())
                added += 1
            except Exception:
                skipped += 1
                continue
            if i % _CHUNK == 0:
                log.info("mesh_import: progress %d/%d triangles added "
                         "(skipped=%d)", added, len(tris), skipped)

        if added == 0:
            raise ValueError(
                f"fm.sewing_failed: every triangle was degenerate or "
                f"rejected ({skipped} skipped). Try a larger "
                "sewing_tolerance_mm.")

        sewer.Perform()
        sewn = sewer.SewedShape()
        if sewn is None or sewn.IsNull():
            raise ValueError(
                "fm.sewing_failed: BRepBuilderAPI_Sewing produced null shape")

        try:
            free_edges = int(sewer.NbFreeEdges())
        except Exception:
            free_edges = -1

        # --- closure detection + solid lift (mirror mesh_to_brep) -----------
        result_shape = sewn
        volume: float | None = None
        if args.try_make_solid:
            try:
                shells: list = []
                exp = TopExp_Explorer(sewn, TopAbs_SHELL)
                while exp.More():
                    shells.append(TopoDS.Shell_s(exp.Current()))
                    exp.Next()

                closed_shells: list = []
                for shl in shells:
                    try:
                        checker = BRepCheck_Shell(shl)
                        if checker.Closed() == BRepCheck_Status.BRepCheck_NoError:
                            closed_shells.append(shl)
                    except Exception:
                        continue

                if len(shells) == 1 and len(closed_shells) == 1:
                    solid_maker = BRepBuilderAPI_MakeSolid(closed_shells[0])
                    if solid_maker.IsDone():
                        candidate = solid_maker.Solid()
                        if candidate is not None and not candidate.IsNull():
                            vol = _volume(candidate)
                            if vol < 0:
                                # inward-wound mesh (proven trap): fix
                                # orientation via ShapeFix_Solid, else reverse
                                try:
                                    from OCP.ShapeFix import ShapeFix_Solid
                                    sf = ShapeFix_Solid(candidate)
                                    sf.Perform()
                                    fixed = sf.Solid()
                                    if fixed is not None and not fixed.IsNull():
                                        v2 = _volume(fixed)
                                        if v2 > 0:
                                            candidate, vol = fixed, v2
                                except Exception:
                                    pass
                                if vol < 0:
                                    rev = candidate.Reversed()
                                    v3 = _volume(rev)
                                    if v3 > 0:
                                        candidate, vol = rev, v3
                            result_shape = candidate
                            volume = vol
                # multiple shells (closed or not) → keep the sewn compound so
                # callers can iterate ALL of them (iPhone-pipeline lesson).
            except Exception:
                pass  # fall back to whatever sewing produced

        # --- honest solidity gate -------------------------------------------
        # TopAbs_SOLID count AND volume > 1e-6: VolumeProperties_s returns a
        # nonzero pseudo-mass for OPEN shells, so volume alone proves nothing.
        n_solids = _count_solids(result_shape)
        is_solid = bool(n_solids > 0 and volume is not None
                        and volume > 1e-6)
        if not is_solid:
            volume = None  # strict-JSON-safe honesty: no pseudo-mass leaks
            log.warning(
                "mesh_import: result is NOT a watertight solid "
                "(free_edges=%d) — returning shell/compound.", free_edges)

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(
            body=Part(result_shape),
            history=history,
            extras={
                "source_path": str(p),
                "format": fmt,
                "n_vertices": int(len(verts)),
                "n_triangles": int(len(tris)),
                "n_degenerate_skipped": int(skipped),
                "is_solid": is_solid,
                "volume_mm3": (float(volume) if volume is not None else None),
                "bbox_mm": _bbox_of_vertices(verts),
                "free_edge_count": int(free_edges),
                "sewing_tolerance_mm": tol,
            },
        )
