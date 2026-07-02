"""point_cloud_import — atomic create. Raw point cloud (.xyz / vertex-only .ply) → geometry + stats.

The FIRST step of scan-to-CAD: ingest a raw scanner point cloud that previously
could not enter the system at all. Two ASCII formats are parsed:

  - ``.xyz`` — one point per line, ``x y z`` (extra trailing columns such as
    intensity or r g b are tolerated — the first 3 floats are taken; blank
    lines and ``#``/``//`` comment lines are skipped),
  - ``.ply`` — ASCII, **vertex elements only**. If the PLY declares faces it
    is a triangle mesh, not a raw point cloud — the skill refuses with
    ``fm.ply_has_faces`` and points the caller at the mesh_import path
    (``mesh_to_brep`` / ``stl_import``) instead.

Body: a real ``TopoDS_Compound`` of ``TopoDS_Vertex`` (one
``BRepBuilderAPI_MakeVertex`` per point) so downstream skills can run bbox /
OBB / registration queries on actual OCCT geometry. For clouds larger than
``subsample_body_vertices`` (default 50 000) the *compound* is built from an
evenly-strided subsample — the FULL point set still drives every statistic
(numpy), and ``extras`` carries an explicit subsample note.

Statistics (numpy, always over the FULL point set):
  - ``n_points``,
  - ``centroid`` [x, y, z],
  - ``aabb`` {min, max, size},
  - ``rms_from_centroid`` — RMS distance of points from the centroid,
  - ``best_fit_plane`` — least-squares plane via SVD: unit ``normal``,
    ``point`` (the centroid), and ``rms_distance`` of the cloud from the
    plane — the bread-and-butter scan flatness diagnostic. ``None`` when the
    cloud has fewer than 3 points.

HONESTY — watertight surface reconstruction is OUT OF SCOPE (no open3d /
Poisson here). This skill ingests, measures and plane-fits only;
``extras["reconstruction"]`` says so explicitly and ``is_solid`` is False by
definition (a compound of vertices encloses no volume).

extras schema:
    {
      "source_path": str,
      "format": "xyz" | "ply",
      "n_points": int,                       # FULL set
      "centroid": [x, y, z],
      "aabb": {"min": [...], "max": [...], "size": [...]},
      "rms_from_centroid": float,
      "best_fit_plane": {"normal": [...], "point": [...],
                         "rms_distance": float} | None,
      "body_vertex_count": int,              # vertices actually in the compound
      "subsampled": bool,
      "subsample_note": str | None,
      "is_solid": False,                     # by definition
      "reconstruction": "unsupported (ingest + fit only)",
      "max_points": int,
    }
All floats are rounded and strict-JSON-safe (no inf/nan — non-finite values
become None).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


_ROUND = 6


def _jf(x: float) -> float | None:
    """JSON-safe rounded float — non-finite values become None (never inf/nan)."""
    x = float(x)
    if not math.isfinite(x):
        return None
    return round(x, _ROUND)


def _jv(vec) -> list[float | None]:
    return [_jf(v) for v in vec]


def _infer_format(path: Path, explicit: str | None) -> str:
    if explicit:
        fmt = explicit.strip().lower()
        if fmt not in ("xyz", "ply"):
            raise ValueError(
                f"fm.unsupported_format: point_cloud_import supports 'xyz' or "
                f"'ply', got {explicit!r}")
        return fmt
    ext = path.suffix.lower().lstrip(".")
    if ext in ("xyz", "ply"):
        return ext
    raise ValueError(
        f"fm.unsupported_format: cannot infer point-cloud format from "
        f"extension {ext!r} — pass format='xyz' or format='ply' "
        f"(STL/OBJ meshes go through mesh_to_brep / stl_import instead)")


def _parse_xyz(path: Path, max_points: int) -> list[tuple[float, float, float]]:
    """'x y z [extra cols...]' per line; '#'/'//' comments + blank lines skipped."""
    pts: list[tuple[float, float, float]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for lineno, raw in enumerate(fh, start=1):
                s = raw.strip()
                if not s or s.startswith("#") or s.startswith("//"):
                    continue
                toks = s.split()
                if len(toks) < 3:
                    raise ValueError(
                        f"fm.point_cloud_parse_failed: {path.name} line "
                        f"{lineno}: expected at least 3 numeric columns, got "
                        f"{len(toks)} ({s[:60]!r})")
                try:
                    x, y, z = float(toks[0]), float(toks[1]), float(toks[2])
                except ValueError:
                    raise ValueError(
                        f"fm.point_cloud_parse_failed: {path.name} line "
                        f"{lineno}: first 3 columns are not numeric "
                        f"({s[:60]!r})")
                pts.append((x, y, z))
                if len(pts) > max_points:
                    raise ValueError(
                        f"fm.too_many_points: {path.name} has more than "
                        f"{max_points} points — raise max_points or decimate "
                        f"the scan first")
    except OSError as exc:
        raise ValueError(
            f"fm.point_cloud_parse_failed: {type(exc).__name__} reading "
            f"{path}: {exc}")
    if not pts:
        raise ValueError(
            f"fm.point_cloud_parse_failed: {path.name} contains no points "
            f"(only blank/comment lines?)")
    return pts


def _parse_ply(path: Path, max_points: int) -> list[tuple[float, float, float]]:
    """ASCII vertex-only PLY. Faces present → fm.ply_has_faces (use mesh_import)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise ValueError(
            f"fm.point_cloud_parse_failed: {type(exc).__name__} reading "
            f"{path}: {exc}")

    if not text or text[0].strip() != "ply":
        raise ValueError(
            f"fm.point_cloud_parse_failed: {path.name} does not start with "
            f"the 'ply' magic line")

    n_vertex: int | None = None
    n_face = 0
    vertex_props: list[str] = []
    current_element: str | None = None
    header_end: int | None = None
    is_ascii = False

    for i, line in enumerate(text[1:], start=1):
        s = line.strip()
        if s.startswith("comment") or not s:
            continue
        if s.startswith("format"):
            toks = s.split()
            if len(toks) >= 2 and toks[1] == "ascii":
                is_ascii = True
            else:
                raise ValueError(
                    f"fm.point_cloud_parse_failed: {path.name} is a "
                    f"binary PLY ({s!r}) — only ASCII PLY is supported; "
                    f"re-export as ASCII")
        elif s.startswith("element"):
            toks = s.split()
            if len(toks) != 3:
                raise ValueError(
                    f"fm.point_cloud_parse_failed: {path.name} malformed "
                    f"element line: {s!r}")
            current_element = toks[1]
            try:
                count = int(toks[2])
            except ValueError:
                raise ValueError(
                    f"fm.point_cloud_parse_failed: {path.name} non-integer "
                    f"element count: {s!r}")
            if current_element == "vertex":
                n_vertex = count
            elif current_element == "face":
                n_face = count
        elif s.startswith("property"):
            if current_element == "vertex":
                # 'property float x' → name is the last token
                vertex_props.append(s.split()[-1])
        elif s == "end_header":
            header_end = i
            break

    if header_end is None or not is_ascii:
        raise ValueError(
            f"fm.point_cloud_parse_failed: {path.name} PLY header has no "
            f"end_header / format line")
    if n_face > 0:
        raise ValueError(
            f"fm.ply_has_faces: {path.name} declares {n_face} faces — this "
            f"is a triangle MESH, not a raw point cloud. Use mesh_import "
            f"instead (skills: mesh_to_brep for STL→BREP sewing, or "
            f"stl_import; convert PLY→STL first if needed).")
    if n_vertex is None:
        raise ValueError(
            f"fm.point_cloud_parse_failed: {path.name} PLY header has no "
            f"'element vertex N' line")
    if n_vertex > max_points:
        raise ValueError(
            f"fm.too_many_points: {path.name} declares {n_vertex} vertices "
            f"> max_points={max_points} — raise max_points or decimate the "
            f"scan first")
    if n_vertex == 0:
        raise ValueError(
            f"fm.point_cloud_parse_failed: {path.name} declares 0 vertices")

    try:
        ix, iy, iz = (vertex_props.index("x"), vertex_props.index("y"),
                      vertex_props.index("z"))
    except ValueError:
        raise ValueError(
            f"fm.point_cloud_parse_failed: {path.name} vertex element lacks "
            f"x/y/z properties (found {vertex_props!r})")

    body_lines = text[header_end + 1:]
    if len(body_lines) < n_vertex:
        raise ValueError(
            f"fm.point_cloud_parse_failed: {path.name} header declares "
            f"{n_vertex} vertices but only {len(body_lines)} data lines "
            f"follow")

    pts: list[tuple[float, float, float]] = []
    need = max(ix, iy, iz) + 1
    for k in range(n_vertex):
        toks = body_lines[k].split()
        if len(toks) < need:
            raise ValueError(
                f"fm.point_cloud_parse_failed: {path.name} vertex line "
                f"{k + 1}: expected ≥{need} columns, got {len(toks)}")
        try:
            pts.append((float(toks[ix]), float(toks[iy]), float(toks[iz])))
        except ValueError:
            raise ValueError(
                f"fm.point_cloud_parse_failed: {path.name} vertex line "
                f"{k + 1}: x/y/z columns are not numeric "
                f"({body_lines[k][:60]!r})")
    return pts


@skill(
    name="point_cloud_import",
    category="create",
    level="atomic",
    summary="Ingest a raw scan point cloud (.xyz or vertex-only ASCII .ply) "
            "as a compound of OCCT vertices plus full numpy statistics "
            "(centroid, AABB, RMS, SVD best-fit plane) — the first step of "
            "scan-to-CAD. Watertight surface reconstruction is OUT OF SCOPE "
            "(ingest + fit only, no open3d); the body is never a solid. "
            "PLY files that contain faces are refused → use mesh_to_brep / "
            "stl_import.",
    selector_kinds=[],
    history_rules={"output_solid": HistoryRule.GENERATED_NEW},
    produces_features=["imported_point_cloud"],
    preserves=[],
    manufacturing={},
    failure_modes=[
        "fm.file_not_found",
        "fm.unsupported_format",
        "fm.point_cloud_parse_failed",
        "fm.ply_has_faces",
        "fm.too_many_points",
    ],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class PointCloudImport(SkillBase):
    class Args(BaseModel):
        path: str = Field(min_length=1,
                          description="점군 파일 경로 (.xyz 또는 ASCII .ply)")
        format: Literal["xyz", "ply"] | None = Field(
            default=None,
            description="'xyz' 또는 'ply'. 생략하면 확장자에서 추론.")
        max_points: int = Field(
            default=2_000_000, ge=1,
            description="Hard cap on the number of points ingested "
                        "(fm.too_many_points beyond this).")
        subsample_body_vertices: int = Field(
            default=50_000, ge=100,
            description="Max vertices materialized in the OCCT compound "
                        "body. Statistics ALWAYS use the full point set; "
                        "only the geometry is strided down.")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        import numpy as np
        from build123d import Part
        from OCP.BRep import BRep_Builder
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
        from OCP.gp import gp_Pnt
        from OCP.TopoDS import TopoDS_Compound

        p = Path(args.path)
        if not p.exists():
            raise ValueError(f"fm.file_not_found: point cloud not found: {p}")

        fmt = _infer_format(p, args.format)
        if fmt == "xyz":
            raw_pts = _parse_xyz(p, int(args.max_points))
        else:
            raw_pts = _parse_ply(p, int(args.max_points))

        pts = np.asarray(raw_pts, dtype=np.float64)  # (n, 3)
        n_points = int(pts.shape[0])

        # ---- FULL-set statistics (numpy) --------------------------------
        centroid = pts.mean(axis=0)
        aabb_min = pts.min(axis=0)
        aabb_max = pts.max(axis=0)
        aabb_size = aabb_max - aabb_min
        d = pts - centroid
        rms_from_centroid = float(np.sqrt(np.mean(np.sum(d * d, axis=1))))

        # Best-fit plane via SVD: smallest singular vector of the centered
        # cloud = plane normal; plane passes through the centroid.
        best_fit_plane: dict[str, Any] | None = None
        if n_points >= 3:
            _, _, vt = np.linalg.svd(d, full_matrices=False)
            normal = vt[2]
            nrm = float(np.linalg.norm(normal))
            if nrm > 0:
                normal = normal / nrm
            dist = d @ normal
            rms_plane = float(np.sqrt(np.mean(dist * dist)))
            best_fit_plane = {
                "normal": _jv(normal),
                "point": _jv(centroid),
                "rms_distance": _jf(rms_plane),
            }

        # ---- Body: compound of TopoDS_Vertex (subsampled if huge) -------
        cap = int(args.subsample_body_vertices)
        subsampled = n_points > cap
        if subsampled:
            idx = np.unique(
                np.linspace(0, n_points - 1, cap).round().astype(np.int64))
            body_pts = pts[idx]
        else:
            body_pts = pts

        comp = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(comp)
        for (x, y, z) in body_pts:
            builder.Add(
                comp,
                BRepBuilderAPI_MakeVertex(
                    gp_Pnt(float(x), float(y), float(z))).Vertex())
        body_vertex_count = int(body_pts.shape[0])

        subsample_note = (
            f"body compound holds {body_vertex_count} of {n_points} points "
            f"(evenly strided to subsample_body_vertices={cap}); all "
            f"statistics use the FULL {n_points}-point set"
        ) if subsampled else None

        history = EntityHistoryMap(
            rules={"output_solid": HistoryRule.GENERATED_NEW},
        )
        return SkillResult(
            body=Part(comp),
            history=history,
            extras={
                "source_path": str(p),
                "format": fmt,
                "n_points": n_points,
                "centroid": _jv(centroid),
                "aabb": {
                    "min": _jv(aabb_min),
                    "max": _jv(aabb_max),
                    "size": _jv(aabb_size),
                },
                "rms_from_centroid": _jf(rms_from_centroid),
                "best_fit_plane": best_fit_plane,
                "body_vertex_count": body_vertex_count,
                "subsampled": bool(subsampled),
                "subsample_note": subsample_note,
                # A compound of vertices encloses no volume — never a solid.
                "is_solid": False,
                "reconstruction": "unsupported (ingest + fit only)",
                "max_points": int(args.max_points),
            },
        )
