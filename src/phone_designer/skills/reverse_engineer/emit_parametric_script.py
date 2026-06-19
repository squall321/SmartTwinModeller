"""emit_parametric_script — reverse_engineer macro, read-only (2026-06-19).

Pillar RE → EDITABLE design-intent recovery. The other RE skills produce a flat,
one-shot reconstruction *plan*; this one emits the recovered design as a
self-contained, RE-RUNNABLE, EDITABLE **build123d Python script** whose top-level
named parameters are the recovered key dimensions. build123d is a real CAD kernel
(it exports STEP that opens in any CAD tool), so a build123d script with named
parameters IS an editable parametric model: change ``housing_length = 60`` at the
top, re-run, and the part regenerates and re-exports.

Honest framing (anti-fake-accuracy): the script reconstructs the SAME geometry as
the box-mode plan — emitting it as a script adds EDITABILITY, not fidelity. So the
result carries the script's own geometry_deviation HAUSDORFF vs the original
(computed when ``validate=True``), never a match_ratio claim, and the script's
fidelity is exactly the box-mode reconstruction's. ``result_grade='estimate'`` —
the recovered tree approximates the original; freeform/revolve bases and
relation-driven (mirror/pattern) parameterisation are future extensions. v1
covers the prismatic case: a box base + rectangular/circular pockets + cylindrical
holes, in the WORLD frame (base at the bbox centre, features at their catalog
world coords), so the emitted part shares the original's frame and is scored with
identity alignment.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


# ──────────────────────────────────────────────────────────────────────────────
# Pure script generation (no OCCT) — testable in isolation.

#: round emitted literals to this many decimals (catalog values carry sub-µm
#: numerical noise from the round-trip; 4 dp = 0.1 µm, well below any CAD use).
_ND = 4


def _r(v: Any) -> float:
    try:
        return round(float(v), _ND)
    except Exception:
        return 0.0


def _ident(name: str) -> str:
    """A safe python identifier for a parameter name."""
    out = "".join(c if (c.isalnum() or c == "_") else "_" for c in str(name))
    if not out or out[0].isdigit():
        out = "p_" + out
    return out


def _bbox_center_and_top(bbox: Any) -> tuple[float, float, float, float]:
    """(cx, cy, cz, zmax) from a 6-tuple world bbox (xmin..zmax)."""
    xmin, ymin, zmin, xmax, ymax, zmax = (float(c) for c in bbox[:6])
    return (
        (xmin + xmax) / 2.0, (ymin + ymax) / 2.0, (zmin + zmax) / 2.0, zmax,
    )


def build_parametric_script(
    plan_steps: list[dict],
    key_dimensions: list[dict] | None,
    bbox: Any,
) -> dict[str, Any]:
    """Map an ordered box-mode plan + key dimensions to a build123d script.

    Returns ``{"parameters": [...], "feature_tree": [...], "script": str,
    "coverage": {...}}``. ``coverage`` reports which step skills were emitted vs
    skipped (honest about what v1 does NOT cover)."""
    if not (isinstance(bbox, (list, tuple)) and len(bbox) >= 6):
        bbox = (-10.0, -10.0, 0.0, 10.0, 10.0, 10.0)
    cx, cy, cz, zmax = _bbox_center_and_top(bbox)

    # ── named parameters: base envelope is ALWAYS named; carry every resolvable
    #    key dimension as a named param so the user sees the editable handles.
    params: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    def _add_param(name: str, value: float, role: str) -> str:
        ident = _ident(name)
        n = ident
        i = 2
        while n in seen_names:
            n = f"{ident}_{i}"
            i += 1
        seen_names.add(n)
        params.append({"name": n, "value": _r(value), "role": role})
        return n

    # base envelope from the bbox (the housing L/W/H a user resizes most).
    L = _add_param("housing_length", bbox[3] - bbox[0], "envelope")
    W = _add_param("housing_width", bbox[4] - bbox[1], "envelope")
    H = _add_param("housing_height", bbox[5] - bbox[2], "envelope")

    # extra recovered key dims (informational handles; primary_bore is wired in).
    primary_bore_name: str | None = None
    primary_bore_val: float | None = None
    for d in key_dimensions or []:
        role = d.get("role")
        if role in ("envelope",):  # already covered by L/W/H
            continue
        nm = _add_param(d.get("name") or role or "dim", d.get("value_mm") or 0.0, role or "dim")
        if role == "primary_bore" and primary_bore_name is None:
            primary_bore_name = nm
            primary_bore_val = _r(d.get("value_mm") or 0.0)

    # ── feature tree + body lines ────────────────────────────────────────────
    feature_tree: list[dict[str, Any]] = [
        {"op": "base_box", "skill": "box", "params": [L, W, H]},
    ]
    lines: list[str] = [f"part = Pos({_r(cx)}, {_r(cy)}, {_r(cz)}) * Box({L}, {W}, {H})"]
    emitted: dict[str, int] = {}
    skipped: dict[str, int] = {}

    def _diam_token(diam: float) -> str:
        """Use the named primary_bore param when this diameter matches it."""
        if (primary_bore_name is not None and primary_bore_val
                and abs(_r(diam) - primary_bore_val) < 1e-6):
            return primary_bore_name
        return f"{_r(diam)}"

    for s in plan_steps:
        sk = s.get("skill")
        a = s.get("args") or {}
        if sk == "box":
            continue  # the base, already emitted
        if sk in ("extrude_pocket", "extrude_pocket_world"):
            sketch = a.get("sketch") or {}
            depth = _r(a.get("depth_mm"))
            kind = sketch.get("kind")
            px = _r(sketch.get("center_x_mm"))
            py = _r(sketch.get("center_y_mm"))
            zc = _r(zmax - depth / 2.0)
            if kind == "circle":
                dia = _r(sketch.get("diameter_mm") or sketch.get("radius_mm", 0) * 2)
                lines.append(
                    f"part -= Pos({px}, {py}, {zc}) * Cylinder({_diam_token(dia)}/2, {depth})"
                )
            else:  # rectangle / default
                lp = _r(sketch.get("length_mm") or 5.0)
                wp = _r(sketch.get("width_mm") or 5.0)
                lines.append(
                    f"part -= Pos({px}, {py}, {zc}) * Box({lp}, {wp}, {depth})"
                )
            feature_tree.append({"op": "pocket", "skill": sk, "at": [px, py], "depth": depth})
            emitted[sk] = emitted.get(sk, 0) + 1
        elif sk == "hole":
            pos = a.get("position") or [0, 0, 0]
            dia = _r(a.get("diameter_mm"))
            depth = _r(a.get("depth_mm"))
            hx, hy = _r(pos[0]), _r(pos[1])
            zc = _r(zmax - depth / 2.0)
            lines.append(
                f"part -= Pos({hx}, {hy}, {zc}) * Cylinder({_diam_token(dia)}/2, {depth})"
            )
            feature_tree.append({"op": "hole", "skill": sk, "at": [hx, hy], "depth": depth})
            emitted[sk] = emitted.get(sk, 0) + 1
        else:
            # honest: v1 does not emit this op (freeform base, fillets, threaded
            # holes via face-named position, etc.) — recorded in coverage.
            skipped[sk] = skipped.get(sk, 0) + 1

    header = [
        '"""Recovered parametric model (build123d) — reverse-engineered.',
        "",
        "Edit any parameter below and re-run to regenerate the part. Export with",
        "    from build123d import export_step; export_step(part, 'out.step')",
        "to open the edited model in any CAD tool.",
        '"""',
        "from build123d import *  # noqa: F401,F403",
        "",
        "# ── editable parameters (recovered key dimensions) ──────────────────",
    ]
    param_lines = [f"{p['name']} = {p['value']}  # {p['role']}" for p in params]
    body = ["", "# ── feature history ─────────────────────────────────────────────────", *lines]
    script = "\n".join([*header, *param_lines, *body, ""]) + "\n"

    return {
        "parameters": params,
        "feature_tree": feature_tree,
        "script": script,
        "coverage": {
            "emitted": emitted,
            "skipped": skipped,
            "fully_covered": not skipped,
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# The skill.


@skill(
    name="emit_parametric_script",
    category="reverse_engineer",
    level="macro",
    summary="Recover an EDITABLE build123d parametric script (named key dimensions "
            "+ ordered feature history) from a CAD body. build123d is a real CAD "
            "kernel, so the emitted script is an editable parametric model: change "
            "a dimension, re-run, re-export STEP. With validate=True it executes "
            "the generated script and scores it by geometry_deviation Hausdorff "
            "(never match_ratio) to prove it reconstructs the part.",
    selector_kinds=[],
    history_rules={},
    produces_features=["parametric_script"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.6,
    result_grade="estimate",
    post_conditions=[PostCondition(kind="body_present")],
)
class EmitParametricScript(SkillBase):
    class Args(BaseModel):
        verify: bool = Field(
            default=False,
            description="Execute the generated script and score its output by "
                        "geometry_deviation Hausdorff vs the original (proves the "
                        "editable script actually reconstructs the part). Slower.",
        )
        edit_check_scale: float = Field(
            default=0.0,
            description="If > 0 (e.g. 1.2), also re-run the script with "
                        "housing_length scaled by this factor and report the "
                        "resulting bbox X extent — proves the named parameter is "
                        "genuinely editable (the part regenerates).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
            ExtractFeatureCatalog,
        )
        from phone_designer.skills.reverse_engineer.identify_key_dimensions import (
            IdentifyKeyDimensions,
        )
        from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
            PlanFromFeatureCatalog,
        )

        out: dict[str, Any] = {"ok": False}
        try:
            cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"catalog: {type(exc).__name__}: {exc}"
            return SkillResult(body=body, history=EntityHistoryMap(),
                               extras={"parametric_script": out})

        bbox = cat.get("initial_bbox_mm")
        try:
            kd = IdentifyKeyDimensions().apply(body, {"catalog": cat}).extras.get(
                "key_dimensions")
        except Exception:
            kd = None

        # build the ordered box-mode plan (unique temp path to avoid races).
        import os
        import tempfile
        plan_steps: list[dict] = []
        try:
            from phone_designer.plan.yaml_io import load_plan
            pp = os.path.join(tempfile.mkdtemp(prefix="emit_ps_"), "p.yaml")
            PlanFromFeatureCatalog().apply(body, {
                "catalog": cat, "base_step_kind": "box", "plan_out_path": pp,
            })
            plan = load_plan(pp)
            plan_steps = [
                {"id": s.id, "skill": s.skill, "args": dict(s.args)} for s in plan.steps
            ]
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"plan: {type(exc).__name__}: {exc}"
            return SkillResult(body=body, history=EntityHistoryMap(),
                               extras={"parametric_script": out})

        gen = build_parametric_script(plan_steps, kd, bbox)
        out.update(gen)
        out["ok"] = True
        out["n_parameters"] = len(gen["parameters"])
        out["n_feature_nodes"] = len(gen["feature_tree"])

        if args.verify:
            out["hausdorff_mm"] = self._validate(gen["script"], body)
        if args.edit_check_scale and args.edit_check_scale > 0:
            out["edit_check"] = self._edit_check(
                gen["script"], gen["parameters"], args.edit_check_scale)

        return SkillResult(body=body, history=EntityHistoryMap(),
                           extras={"parametric_script": out})

    # ── validation helpers ──────────────────────────────────────────────────

    @staticmethod
    def _exec_script(script: str) -> Any:
        """Execute the generated build123d script and return ``part`` (or None)."""
        ns: dict[str, Any] = {}
        exec(compile(script, "<recovered_parametric_script>", "exec"), ns, ns)  # noqa: S102
        return ns.get("part")

    @staticmethod
    def _validate(script: str, body: Any) -> float | None:
        """geometry_deviation Hausdorff of the script's output vs the original —
        the anti-fake ground truth that the editable script reconstructs the
        part. World frame on both sides → identity alignment."""
        import os
        import tempfile

        from build123d import Part
        from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

        from phone_designer.skills.inspect.geometry_deviation import GeometryDeviation

        try:
            part = EmitParametricScript._exec_script(script)
            if part is None:
                return None
            shape = body.wrapped if hasattr(body, "wrapped") else body
            tmp = tempfile.mkdtemp(prefix="emit_ps_val_")
            ref = os.path.join(tmp, "orig.step")
            w = STEPControl_Writer()
            w.Transfer(shape, STEPControl_AsIs)
            w.Write(ref)
            regen = Part(part.wrapped) if hasattr(part, "wrapped") else part
            gd = GeometryDeviation().apply(regen, {
                "reference_step_path": ref,
                "linear_deflection_mm": 0.3,
                "align": "none",
            })
            return gd.extras["geometry_deviation"].get("hausdorff_mm")
        except Exception:
            return None

    @staticmethod
    def _edit_check(script: str, parameters: list[dict], scale: float) -> dict:
        """Re-run the script with housing_length scaled — proves the named
        parameter is genuinely editable (the part regenerates with the new
        size)."""
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib

        def _x_extent(part) -> float | None:
            try:
                shp = part.wrapped if hasattr(part, "wrapped") else part
                bb = Bnd_Box()
                BRepBndLib.AddOptimal_s(shp, bb)
                xmin, _ymin, _zmin, xmax, _ymax, _zmax = bb.Get()
                return round(float(xmax - xmin), 4)
            except Exception:
                return None

        try:
            base_x = _x_extent(EmitParametricScript._exec_script(script))
            edited = "\n".join(
                (ln if not ln.startswith("housing_length = ")
                 else f"housing_length = {round(float(ln.split('=')[1].split('#')[0]) * scale, 4)}"
                      f"  # edited x{scale}")
                for ln in script.splitlines()
            )
            edit_x = _x_extent(EmitParametricScript._exec_script(edited))
            ratio = (edit_x / base_x) if (base_x and edit_x) else None
            return {
                "base_x_mm": base_x,
                "edited_x_mm": edit_x,
                "scale": scale,
                "x_ratio": round(ratio, 4) if ratio else None,
                "is_parametric": bool(ratio and abs(ratio - scale) < 0.02),
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}
