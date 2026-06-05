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
        # accumulators populated by the various passes for the extras report
        quadric_passes: int = 0
        cluster_iters: int = 0
        cluster_pitch_used: float | None = None

        # ───── helpers ──────────────────────────────────────────────────────
        def _run_quadric(src_mesh, face_target):
            """Single quadric pass on ``src_mesh``; returns (out_mesh|None, warn|None)."""
            simp = getattr(src_mesh, "simplify_quadric_decimation", None)
            if simp is None:
                simp = getattr(src_mesh, "simplify_quadratic_decimation", None)
            if simp is None:
                return None, ("simplify_quadric_decimation unavailable "
                              "(install `fast-simplification`)")
            try:
                return simp(face_count=face_target), None
            except TypeError:
                try:
                    return simp(face_target), None
                except Exception as e:
                    return None, f"quadric simplify raised: {e}"
            except Exception as e:
                return None, f"quadric simplify raised: {e}"

        def _cluster_pass(src_mesh, pitch):
            """Snap+remap vertex-cluster decimation at the given pitch."""
            verts = np.asarray(src_mesh.vertices, dtype=float)
            faces_arr = np.asarray(src_mesh.faces, dtype=np.int64)
            keys = np.floor(verts / pitch).astype(np.int64)
            _, inverse, counts = np.unique(
                keys, axis=0, return_inverse=True, return_counts=True,
            )
            n_clusters = int(counts.shape[0])
            centroids = np.zeros((n_clusters, 3), dtype=float)
            np.add.at(centroids, inverse, verts)
            centroids /= counts[:, None]
            new_faces = inverse[faces_arr]
            ok = (
                (new_faces[:, 0] != new_faces[:, 1])
                & (new_faces[:, 1] != new_faces[:, 2])
                & (new_faces[:, 0] != new_faces[:, 2])
            )
            new_faces = new_faces[ok]
            sorted_faces = np.sort(new_faces, axis=1)
            _, unique_idx = np.unique(
                sorted_faces, axis=0, return_index=True,
            )
            new_faces = new_faces[np.sort(unique_idx)]
            return trimesh.Trimesh(
                vertices=centroids, faces=new_faces, process=False,
            )

        # ───── method = quadric (with quadric_chain on overshoot) ──────────
        if args.method == "quadric":
            current = mesh
            prev_count = n_in
            for _ in range(3):  # at most 3 quadric passes in the chain
                out, w = _run_quadric(current, target)
                if out is None:
                    warn = w
                    break
                quadric_passes += 1
                decimated = out
                n_cur = int(len(out.faces))
                # If we are within 1.5× of target we're done.
                if n_cur <= int(target * 1.5):
                    break
                # Plateau guard: if the pass didn't reduce the face count by
                # at least 10%, the backend has hit its per-shell floor —
                # additional passes won't help. Fall back to cluster.
                if n_cur >= int(prev_count * 0.9):
                    warn = (
                        f"quadric plateaued at {n_cur} faces (target "
                        f"{target}, prev {prev_count}); falling back to "
                        f"cluster pitch-search to reach target"
                    )
                    method_used = "cluster"
                    # Use the plateaued quadric output as the cluster input —
                    # we already paid for the smoothing it produced.
                    mesh = out
                    n_in = n_cur
                    decimated = None
                    break
                prev_count = n_cur
                current = out
            if decimated is None and method_used == "quadric":
                # Quadric unavailable → fall back to cluster, record reason.
                method_used = "cluster"
                if warn is None:
                    warn = ("simplify_quadric_decimation unavailable "
                            "(install `fast-simplification`); fell back to "
                            "vertex clustering")

        # ───── method = cluster — binary search on pitch ───────────────────
        if method_used == "cluster" and decimated is None:
            try:
                extents = np.asarray(mesh.extents, dtype=float)
                diag = float(np.linalg.norm(extents))
            except Exception:
                diag = 1.0
            n_verts = max(1, int(len(mesh.vertices)))
            baseline = diag / max(1.0, n_verts ** (1.0 / 3.0))
            ratio = max(target / max(1, n_in), 1e-6)
            pitch_seed = max(baseline * (1.0 / ratio) ** 0.5, 1e-9)

            # Bracket: pitch_lo under-decimates (faces too many),
            # pitch_hi over-decimates (faces too few). Start from the seed
            # and expand exponentially until we straddle the target.
            try:
                pitch_lo: float | None = None
                pitch_hi: float | None = None
                best_mesh = None
                best_err = float("inf")

                pitch = pitch_seed
                # Step 1: expand outward (≤8 expansions) to bracket the
                # target. We must end with BOTH pitch_lo and pitch_hi set
                # before step 2 can bisect — otherwise step 2 is skipped
                # and we ship the seed result (often wildly off-target).
                for _ in range(8):
                    cluster_iters += 1
                    cand = _cluster_pass(mesh, pitch)
                    n_cand = int(len(cand.faces))
                    err = abs(n_cand - target) / max(1, target)
                    if err < best_err:
                        best_err, best_mesh = err, cand
                        cluster_pitch_used = pitch
                    if err <= 0.15:
                        # Already within tolerance — no need to bisect further.
                        pitch_lo = pitch_hi = pitch
                        break
                    if n_cand >= target:
                        pitch_lo = pitch
                        if pitch_hi is not None:
                            break
                        pitch *= 2.0  # coarser bucket → fewer faces
                    else:
                        pitch_hi = pitch
                        if pitch_lo is not None:
                            break
                        pitch *= 0.5  # finer bucket → more faces

                # Step 2: bisect inside [pitch_lo, pitch_hi] until within ±15%.
                if (
                    pitch_lo is not None
                    and pitch_hi is not None
                    and pitch_lo != pitch_hi
                ):
                    for _ in range(8):
                        cluster_iters += 1
                        mid = 0.5 * (pitch_lo + pitch_hi)
                        cand = _cluster_pass(mesh, mid)
                        n_cand = int(len(cand.faces))
                        err = abs(n_cand - target) / max(1, target)
                        if err < best_err:
                            best_err, best_mesh = err, cand
                            cluster_pitch_used = mid
                        if err <= 0.15:
                            break
                        if n_cand >= target:
                            pitch_lo = mid
                        else:
                            pitch_hi = mid

                decimated = best_mesh if best_mesh is not None else _cluster_pass(mesh, pitch_seed)
                if cluster_pitch_used is None:
                    cluster_pitch_used = pitch_seed
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
                    "quadric_passes": int(quadric_passes),
                    "cluster_iterations": int(cluster_iters),
                    "cluster_pitch_used": cluster_pitch_used,
                },
            },
        )
