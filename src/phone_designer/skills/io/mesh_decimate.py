"""mesh_decimate — atomic. STL → decimated STL via trimesh (quadric / cluster).

Sister to `mesh_simplify` (which is OCCT-only vertex-cluster). This skill
delegates to `trimesh` so we can use quadric edge-collapse decimation when
the `fast-simplification` backend is installed. Falls back to trimesh's
own vertex-cluster simplifier when quadric isn't available or when the
caller explicitly asks for `method="cluster"`.

Why both? Quadric decimation preserves sharp features dramatically better
than grid-snap, but it needs an extra wheel; vertex-cluster is pure-python
and works everywhere. Picking the right one is a per-mesh decision —
let the caller decide.

Body passthrough: the input body is returned unchanged so the
`body_present` post-condition is satisfied. The decimated mesh is written
to `output_path` and the actual triangle counts land in
`extras["mesh_decimate"]`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="mesh_decimate",
    category="inspect",
    level="atomic",
    summary="Decimate an STL mesh via trimesh (quadric edge-collapse if "
            "`fast-simplification` is available, else vertex clustering). "
            "Body passthrough; writes the reduced mesh to `output_path`.",
    selector_kinds=[],
    history_rules={},
    produces_features=["stl_artifact", "decimate_stats"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[
        "fm.file_not_found",
        "fm.stl_parse_failed",
        "fm.stl_write_failed",
        "fm.dependency_missing",
    ],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class MeshDecimate(SkillBase):
    class Args(BaseModel):
        mesh_input_path: str = Field(min_length=1, description="입력 STL 경로")
        output_path: str = Field(min_length=1, description="출력(decimated) STL 경로")
        target_face_count: int = Field(
            gt=0, description="원하는 출력 face(triangle) 개수.",
        )
        method: Literal["quadric", "cluster"] = Field(
            default="quadric",
            description="quadric = simplify_quadric_decimation (needs "
                        "fast-simplification); cluster = vertex-cluster "
                        "fallback (pure trimesh).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        try:
            import numpy as np
            import trimesh
        except ImportError as e:  # pragma: no cover — environment is preinstalled
            raise RuntimeError(
                f"mesh_decimate: trimesh/numpy not installed: {e}",
            ) from e

        in_path = Path(args.mesh_input_path)
        if not in_path.exists():
            raise FileNotFoundError(
                f"mesh_decimate: input STL not found: {in_path}",
            )

        try:
            mesh = trimesh.load(str(in_path), force="mesh")
        except Exception as e:
            raise RuntimeError(
                f"mesh_decimate: failed to parse mesh {in_path}: {e}",
            ) from e
        if mesh is None or not hasattr(mesh, "faces") or len(mesh.faces) == 0:
            raise RuntimeError(f"mesh_decimate: empty mesh: {in_path}")

        n_in = int(len(mesh.faces))
        target = max(4, int(args.target_face_count))
        method_used = args.method
        decimated = None
        warn: str | None = None

        if args.method == "quadric":
            simplifier = getattr(mesh, "simplify_quadric_decimation", None)
            if simplifier is None:
                # method name differs across trimesh versions — try the
                # older spelling once.
                simplifier = getattr(mesh, "simplify_quadratic_decimation", None)
            if simplifier is not None:
                try:
                    decimated = simplifier(face_count=target)
                except TypeError:
                    # very old API expected a positional arg
                    try:
                        decimated = simplifier(target)
                    except Exception as e:
                        warn = f"quadric simplify raised: {e}"
                        decimated = None
                except Exception as e:
                    warn = f"quadric simplify raised: {e}"
                    decimated = None
            if decimated is None:
                # Quadric unavailable → fall back to cluster, record reason.
                method_used = "cluster"
                if warn is None:
                    warn = ("simplify_quadric_decimation unavailable "
                            "(install `fast-simplification`); fell back to "
                            "vertex clustering")

        if method_used == "cluster" and decimated is None:
            # Vertex clustering, implemented locally — trimesh ≥4 dropped the
            # helper. Strategy: snap each vertex to a regular grid, remap
            # triangles to the unique cluster id, drop degenerate / duplicate
            # faces, and rebuild a Trimesh from the cluster centroids.
            try:
                extents = np.asarray(mesh.extents, dtype=float)
                diag = float(np.linalg.norm(extents))
            except Exception:
                diag = 1.0
            n_verts = max(1, int(len(mesh.vertices)))
            baseline = diag / max(1.0, n_verts ** (1.0 / 3.0))
            ratio = max(target / max(1, n_in), 1e-6)
            pitch = max(baseline * (1.0 / ratio) ** 0.5, 1e-9)

            try:
                verts = np.asarray(mesh.vertices, dtype=float)
                faces = np.asarray(mesh.faces, dtype=np.int64)
                keys = np.floor(verts / pitch).astype(np.int64)
                # unique returns sorted unique rows + inverse-index map.
                _, inverse, counts = np.unique(
                    keys, axis=0, return_inverse=True, return_counts=True,
                )
                # cluster centroid = mean of original vertices in that bucket
                n_clusters = int(counts.shape[0])
                centroids = np.zeros((n_clusters, 3), dtype=float)
                np.add.at(centroids, inverse, verts)
                centroids /= counts[:, None]

                new_faces = inverse[faces]
                # drop triangles whose corners collapsed to <3 distinct ids
                ok = (
                    (new_faces[:, 0] != new_faces[:, 1])
                    & (new_faces[:, 1] != new_faces[:, 2])
                    & (new_faces[:, 0] != new_faces[:, 2])
                )
                new_faces = new_faces[ok]
                # dedup by sorted-triple
                sorted_faces = np.sort(new_faces, axis=1)
                _, unique_idx = np.unique(
                    sorted_faces, axis=0, return_index=True,
                )
                new_faces = new_faces[np.sort(unique_idx)]
                decimated = trimesh.Trimesh(
                    vertices=centroids, faces=new_faces, process=False,
                )
            except Exception as e:
                raise RuntimeError(
                    f"mesh_decimate: cluster fallback failed: {e}",
                ) from e

        if decimated is None or not hasattr(decimated, "faces"):
            raise RuntimeError(
                "mesh_decimate: both quadric and cluster paths returned no "
                "mesh — input may be degenerate.",
            )

        n_out = int(len(decimated.faces))
        if n_out == 0:
            raise RuntimeError(
                f"mesh_decimate: decimation produced 0 faces (target={target}, "
                f"method={method_used}). Try a larger target_face_count.",
            )

        out_path = Path(args.output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            decimated.export(str(out_path))
        except Exception as e:
            raise RuntimeError(
                f"mesh_decimate: STL write failed → {out_path}: {e}",
            ) from e
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError(
                f"mesh_decimate: output file is missing/empty: {out_path}",
            )

        return SkillResult(
            body=body,  # passthrough — caller's body is untouched.
            history=EntityHistoryMap(),
            extras={
                "mesh_decimate": {
                    "input_path": str(in_path),
                    "output_path": str(out_path),
                    "method_requested": args.method,
                    "method_used": method_used,
                    "target_face_count": int(target),
                    "faces_in": int(n_in),
                    "faces_out": int(n_out),
                    "vertices_in": int(len(mesh.vertices)),
                    "vertices_out": int(len(decimated.vertices)),
                    "warning": warn,
                },
            },
        )
