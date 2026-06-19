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

import math
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
    patterns: list[dict] | None = None,
) -> dict[str, Any]:
    """Map an ordered box-mode plan + key dimensions to a build123d script.

    When ``patterns`` (the catalog's ``patterns`` array) is given, features that
    belong to a recovered linear/circular pattern are emitted as ONE representative
    + a parametric loop whose pitch (linear) or radius (circular) is a NAMED
    editable parameter — true design intent (change the pitch, the whole pattern
    moves) — instead of N independent cuts.

    Returns ``{"parameters": [...], "feature_tree": [...], "script": str,
    "coverage": {...}}``. ``coverage`` reports which step skills were emitted vs
    skipped (honest about what is NOT covered)."""
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

    # ── 1. each pocket/hole step → a positioned cut DESCRIPTOR (the build123d
    #       cutting solid, independent of where it is placed) ──────────────────
    def _descriptor(s: dict) -> dict | None:
        sk = s.get("skill")
        a = s.get("args") or {}
        if sk in ("extrude_pocket", "extrude_pocket_world"):
            sketch = a.get("sketch") or {}
            depth = _r(a.get("depth_mm"))
            zc = _r(zmax - depth / 2.0)
            if sketch.get("kind") == "circle":
                dia = _r(sketch.get("diameter_mm") or sketch.get("radius_mm", 0) * 2)
                solid = f"Cylinder({_diam_token(dia)}/2, {depth})"
            else:
                lp = _r(sketch.get("length_mm") or 5.0)
                wp = _r(sketch.get("width_mm") or 5.0)
                solid = f"Box({lp}, {wp}, {depth})"
            return {"cx": _r(sketch.get("center_x_mm")), "cy": _r(sketch.get("center_y_mm")),
                    "zc": zc, "solid": solid, "op": "pocket", "sk": sk}
        if sk == "hole":
            pos = a.get("position") or [0, 0, 0]
            depth = _r(a.get("depth_mm"))
            return {"cx": _r(pos[0]), "cy": _r(pos[1]), "zc": _r(zmax - depth / 2.0),
                    "solid": f"Cylinder({_diam_token(_r(a.get('diameter_mm')))}/2, {depth})",
                    "op": "hole", "sk": sk}
        return None

    descriptors: list[dict] = []
    for s in plan_steps:
        if s.get("skill") == "box":
            continue
        d = _descriptor(s)
        if d is None:
            sk = s.get("skill")
            skipped[sk] = skipped.get(sk, 0) + 1  # freeform base / fillets / etc.
        else:
            descriptors.append(d)

    # ── 2. group descriptors onto recovered patterns by position match ────────
    pat_list = list(patterns or [])
    _TOL = 0.5
    members_of: list[list[int]] = [[] for _ in pat_list]

    def _pattern_of(dx: float, dy: float) -> int | None:
        for pi, p in enumerate(pat_list):
            positions = p.get("positions") or []
            if positions:  # linear / explicit-position pattern
                for pp in positions:
                    if abs(_r(pp[0]) - dx) <= _TOL and abs(_r(pp[1]) - dy) <= _TOL:
                        return pi
            elif p.get("pattern_kind") == "circular":  # ring: stored as center+radius
                c = p.get("center") or [0.0, 0.0, 0.0]
                rad = _r(p.get("radius_mm") or 0.0)
                if rad > 0 and abs(
                        math.hypot(dx - _r(c[0]), dy - _r(c[1])) - rad) <= _TOL:
                    return pi
        return None

    grouped: set[int] = set()
    for di, d in enumerate(descriptors):
        pi = _pattern_of(d["cx"], d["cy"])
        if pi is not None:
            members_of[pi].append(di)

    needs_math = False
    # ── 3a. emit each REAL grouped pattern as a parametric loop ───────────────
    for pi, members in enumerate(members_of):
        if len(members) < 2:
            continue  # not actually a grouped pattern in this plan → leave individual
        p = pat_list[pi]
        rep = descriptors[members[0]]
        count = int(p.get("count") or len(members))
        kind = p.get("pattern_kind") or "linear"
        if kind == "circular":
            # ring stored as center + radius + angular pitch; the representative
            # member's angle fixes the loop's start so it reproduces the holes.
            c = p.get("center") or [0.0, 0.0, 0.0]
            cxs, cys = _r(c[0]), _r(c[1])
            r0 = _r(p.get("radius_mm")
                    or math.hypot(rep["cx"] - cxs, rep["cy"] - cys))
            a0 = _r(math.degrees(math.atan2(rep["cy"] - cys, rep["cx"] - cxs)))
            step_deg = _r(p.get("angular_pitch_deg") or (360.0 / max(count, 1)))
            rad = _add_param(f"pattern_{pi}_radius_mm", r0 or 1.0, "pattern_radius")
            needs_math = True
            lines.append(f"# circular pattern — {count}x {rep['op']}, editable radius")
            lines.append(f"for _i in range({count}):")
            lines.append(f"    _ang = _math.radians({a0} + _i * {step_deg})")
            lines.append(f"    _px = {cxs} + {rad} * _math.cos(_ang)")
            lines.append(f"    _py = {cys} + {rad} * _math.sin(_ang)")
            lines.append(f"    part -= Pos(_px, _py, {rep['zc']}) * {rep['solid']}")
        else:  # linear
            positions = p.get("positions") or []
            sx, sy = ((_r(positions[0][0]), _r(positions[0][1]))
                      if positions else (rep["cx"], rep["cy"]))
            direction = p.get("direction") or [1.0, 0.0, 0.0]
            dx = _r(direction[0])
            dy = _r(direction[1]) if len(direction) > 1 else 0.0
            pitch = _add_param(f"pattern_{pi}_pitch_mm", _r(p.get("spacing_mm")) or 1.0, "pattern_pitch")
            lines.append(f"# linear pattern — {count}x {rep['op']}, editable pitch")
            lines.append(f"for _i in range({count}):")
            lines.append(f"    _px = {sx} + _i * {pitch} * {dx}")
            lines.append(f"    _py = {sy} + _i * {pitch} * {dy}")
            lines.append(f"    part -= Pos(_px, _py, {rep['zc']}) * {rep['solid']}")
        feature_tree.append({"op": f"{kind}_pattern", "count": count, "members": len(members)})
        emitted[f"{kind}_pattern"] = emitted.get(f"{kind}_pattern", 0) + 1
        grouped.update(members)

    # ── 3a.5 — MIRROR pairs: a feature + its exact reflection about an axis-
    #          aligned plane through the bbox centre → ONE feature + a `mirror`
    #          op (edit one, both follow). Detected directly by geometric
    #          reflection (more reliable than the noisy global symmetry score);
    #          conservative — only an EXACT same-solid reflection pairs.
    _MTOL = 0.4
    for di, d in enumerate(descriptors):
        if di in grouped:
            continue
        for axis, ctr in (("x", cx), ("y", cy)):
            mx = _r(2 * cx - d["cx"]) if axis == "x" else d["cx"]
            my = _r(2 * cy - d["cy"]) if axis == "y" else d["cy"]
            if abs(mx - d["cx"]) < _MTOL and abs(my - d["cy"]) < _MTOL:
                continue  # feature lies ON the plane (self-symmetric) — not a pair
            partner = next(
                (dj for dj, e in enumerate(descriptors)
                 if dj != di and dj not in grouped
                 and abs(e["cx"] - mx) < _MTOL and abs(e["cy"] - my) < _MTOL
                 and e["solid"] == d["solid"] and abs(e["zc"] - d["zc"]) < _MTOL),
                None)
            if partner is None:
                continue
            if axis == "x":
                handle = _add_param(f"mirror_{di}_x_mm", d["cx"], "mirror_x")
                pos_expr = f"Pos({handle}, {d['cy']}, {d['zc']})"
                plane_expr = f"Plane(origin=({_r(ctr)}, 0, 0), z_dir=(1, 0, 0))"
            else:
                handle = _add_param(f"mirror_{di}_y_mm", d["cy"], "mirror_y")
                pos_expr = f"Pos({d['cx']}, {handle}, {d['zc']})"
                plane_expr = f"Plane(origin=(0, {_r(ctr)}, 0), z_dir=(0, 1, 0))"
            lines.append(f"# mirror pair about {axis}={_r(ctr)} — edit one, both follow")
            lines.append(f"_mir = {pos_expr} * {d['solid']}")
            lines.append("part -= _mir")
            lines.append(f"part -= mirror(_mir, about={plane_expr})")
            feature_tree.append({"op": "mirror_pair", "axis": axis})
            emitted["mirror_pair"] = emitted.get("mirror_pair", 0) + 1
            grouped.add(di)
            grouped.add(partner)
            break  # paired on this axis

    # ── 3b. emit every ungrouped descriptor individually ──────────────────────
    for di, d in enumerate(descriptors):
        if di in grouped:
            continue
        lines.append(f"part -= Pos({d['cx']}, {d['cy']}, {d['zc']}) * {d['solid']}")
        feature_tree.append({"op": d["op"], "skill": d["sk"], "at": [d["cx"], d["cy"]]})
        emitted[d["sk"]] = emitted.get(d["sk"], 0) + 1

    header = [
        '"""Recovered parametric model (build123d) — reverse-engineered.',
        "",
        "Edit any parameter below and re-run to regenerate the part. Export with",
        "    from build123d import export_step; export_step(part, 'out.step')",
        "to open the edited model in any CAD tool.",
        '"""',
        "from build123d import *  # noqa: F401,F403",
        *(["import math as _math"] if needs_math else []),
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

        gen = build_parametric_script(
            plan_steps, kd, bbox, patterns=cat.get("patterns"))
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
