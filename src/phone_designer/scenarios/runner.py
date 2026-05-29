"""Scenario runner — Phase 0 stub.

지원 kind (현 골격):
  import_check          모듈 import 검증
  run_python            스크립트 실행
  check_file_exists     파일 존재 검증
  system_info           Python/OS/OCCT 버전 캡처
  log_summary           로그 레벨 카운트 검증

미구현 kind 는 SKIP 으로 처리. Phase 0 작업으로 확장.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


@dataclass
class StepResult:
    kind: str
    outcome: str       # PASS | FAIL | SKIP
    details: dict = field(default_factory=dict)


@dataclass
class ScenarioResult:
    name: str
    outcome: str       # PASS | FAIL
    run_dir: Path
    steps: list[StepResult] = field(default_factory=list)
    started_at: str = ""
    duration_s: float = 0.0


def _new_run_dir(scenario_name: str) -> Path:
    base = Path(os.environ.get("PHONE_DESIGNER_RUN_DIR", "run_logs"))
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run = base / f"{ts}_{scenario_name}"
    run.mkdir(parents=True, exist_ok=True)
    (run / "screenshots").mkdir(exist_ok=True)
    return run


def _log_line(run_dir: Path, level: str, **fields):
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        **fields,
    }
    with (run_dir / "log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _step_import_check(run_dir: Path, step: dict, context: dict) -> StepResult:
    fails: list[tuple[str, str]] = []
    oks: list[str] = []
    for m in step.get("modules", []):
        try:
            __import__(m)
            oks.append(m)
            _log_line(run_dir, "INFO", phase="import_check", module=m, ok=True)
        except Exception as e:
            fails.append((m, str(e)))
            _log_line(run_dir, "ERROR", phase="import_check", module=m, error=str(e))
    return StepResult(
        kind="import_check",
        outcome="PASS" if not fails else "FAIL",
        details={"ok": oks, "failed": [{"module": m, "error": e} for m, e in fails]},
    )


def _step_run_python(run_dir: Path, step: dict, context: dict) -> StepResult:
    """run_python — script 필드를 sys.executable 인자로 split.

    예:
      script: "fixtures/make_simple_watch.py"
      script: "-m phone_designer.skills.export_manifest --out manifest.json"
    """
    script = step["script"]
    timeout = step.get("timeout_s", 120)
    args = script.split() if isinstance(script, str) else script

    _log_line(run_dir, "INFO", phase="run_python", argv=args, timeout_s=timeout)
    proc = subprocess.run(
        [sys.executable] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    # 출력 파일명에 사용할 안전한 stem
    stem = args[1] if (args and args[0] == "-m") else Path(args[0]).stem if args else "run"
    stem = stem.replace("/", "_").replace(".", "_").replace("\\", "_")
    (run_dir / f"{stem}.stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (run_dir / f"{stem}.stderr.txt").write_text(proc.stderr, encoding="utf-8")

    _log_line(
        run_dir, "INFO" if proc.returncode == 0 else "ERROR",
        phase="run_python", argv=args, returncode=proc.returncode,
    )
    return StepResult(
        kind="run_python",
        outcome="PASS" if proc.returncode == 0 else "FAIL",
        details={"returncode": proc.returncode, "argv": args},
    )


def _step_check_file_exists(run_dir: Path, step: dict, context: dict) -> StepResult:
    paths = [Path(p) for p in step.get("paths", [])]
    missing = [p for p in paths if not p.exists()]
    for p in paths:
        _log_line(
            run_dir, "INFO" if p.exists() else "ERROR",
            phase="check_file_exists", path=str(p), exists=p.exists(),
        )
    return StepResult(
        kind="check_file_exists",
        outcome="PASS" if not missing else "FAIL",
        details={"missing": [str(p) for p in missing]},
    )


def _step_system_info(run_dir: Path, step: dict, context: dict) -> StepResult:
    info: dict[str, Any] = {
        "python_version": sys.version,
        "os": platform.platform(),
        "machine": platform.machine(),
    }
    try:
        import build123d
        info["build123d_version"] = build123d.__version__
    except Exception:
        info["build123d_version"] = "n/a"
    try:
        import OCP
        info["occt_via_OCP"] = "ok"
    except Exception:
        info["occt_via_OCP"] = "n/a"
    try:
        import pyvista
        info["pyvista_version"] = pyvista.__version__
    except Exception:
        info["pyvista_version"] = "n/a"

    (run_dir / "system_info.txt").write_text(
        "\n".join(f"{k}: {v}" for k, v in info.items()), encoding="utf-8"
    )
    _log_line(run_dir, "INFO", phase="system_info", **info)
    return StepResult(kind="system_info", outcome="PASS", details=info)


def _step_log_summary(run_dir: Path, step: dict, context: dict) -> StepResult:
    log_path = run_dir / "log.jsonl"
    if not log_path.exists():
        return StepResult(kind="log_summary", outcome="FAIL", details={"reason": "no log.jsonl"})
    counts: dict[str, int] = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            counts[json.loads(line)["level"]] = counts.get(json.loads(line)["level"], 0) + 1
        except Exception:
            pass
    expect = step.get("expect", {}).get("level_counts", {})
    failures = []
    for level, rule in expect.items():
        actual = counts.get(level, 0)
        if "max" in rule and actual > rule["max"]:
            failures.append({"level": level, "actual": actual, "max": rule["max"]})
        if "min" in rule and actual < rule["min"]:
            failures.append({"level": level, "actual": actual, "min": rule["min"]})
    return StepResult(
        kind="log_summary",
        outcome="PASS" if not failures else "FAIL",
        details={"counts": counts, "failures": failures},
    )


def _step_viewport_offscreen(run_dir: Path, step: dict, context: dict) -> StepResult:
    """PyVista offscreen PNG 캡처. primitive (box) 또는 load_step 으로 입력."""
    from phone_designer.logging.viewport_capture import (
        CaptureSpec, capture_polydata, capture_step_file,
    )
    out_dir = run_dir / "screenshots"
    label = step.get("label", "viewport")
    views = step.get("views", ["iso", "top", "side", "front"])
    spec = CaptureSpec(out_dir=out_dir, label=label, views=views)

    try:
        if "load_step" in step:
            paths = capture_step_file(Path(step["load_step"]), spec)
        elif step.get("primitive") == "box":
            import pyvista as pv
            size = step.get("size", [10, 10, 10])
            mesh = pv.Cube(x_length=size[0], y_length=size[1], z_length=size[2])
            paths = capture_polydata(mesh, spec)
        else:
            return StepResult(
                kind="viewport_offscreen", outcome="FAIL",
                details={"reason": "primitive or load_step 필요"},
            )
        _log_line(
            run_dir, "INFO", phase="viewport_offscreen",
            label=label, views=views, count=len(paths),
        )
        return StepResult(
            kind="viewport_offscreen", outcome="PASS",
            details={"paths": [str(p) for p in paths]},
        )
    except Exception as e:
        _log_line(run_dir, "ERROR", phase="viewport_offscreen", error=str(e))
        return StepResult(kind="viewport_offscreen", outcome="FAIL", details={"error": str(e)})


def _step_read_xde_step(run_dir: Path, step: dict, context: dict) -> StepResult:
    """XDE STEP reader — 어셈블리 트리 + 부품 네이밍 추출. context[as] 에 결과 저장."""
    try:
        from OCP.STEPCAFControl import STEPCAFControl_Reader
        from OCP.TDocStd import TDocStd_Document
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.XCAFDoc import XCAFDoc_DocumentTool
        from OCP.TDF import TDF_LabelSequence
        from OCP.TDataStd import TDataStd_Name

        path = Path(step["path"])
        if not path.exists():
            return StepResult(
                kind="read_xde_step", outcome="FAIL",
                details={"reason": f"STEP not found: {path}"},
            )

        reader = STEPCAFControl_Reader()
        reader.SetNameMode(True)
        reader.SetColorMode(True)
        reader.SetLayerMode(True)
        doc = TDocStd_Document(TCollection_ExtendedString("doc"))
        reader.ReadFile(str(path))
        reader.Transfer(doc)

        shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
        labels = TDF_LabelSequence()
        shape_tool.GetFreeShapes(labels)

        # TDataStd_Name 의 GUID + FindAttribute 패턴 — OCP 7.8 의 정석.
        # (Get_s / IsSet_s 는 OCP binding 에서 노출 불안정.)
        name_guid = TDataStd_Name.GetID_s()
        parts_info = []
        for i in range(1, labels.Length() + 1):
            label = labels.Value(i)
            name = "<unnamed>"
            name_attr = TDataStd_Name()
            if label.FindAttribute(name_guid, name_attr):
                ext = name_attr.Get()  # TCollection_ExtendedString
                if hasattr(ext, "ToExtString"):
                    name = ext.ToExtString()
                else:
                    name = str(ext)
            parts_info.append({"index": i, "name": name})

        _log_line(
            run_dir, "INFO", phase="read_xde_step",
            path=str(path), part_count=len(parts_info),
        )

        bound_name = step.get("as", "assembly")
        context[bound_name] = parts_info

        return StepResult(
            kind="read_xde_step", outcome="PASS",
            details={"parts": parts_info, "as": bound_name},
        )
    except Exception as e:
        _log_line(run_dir, "ERROR", phase="read_xde_step", error=str(e))
        return StepResult(kind="read_xde_step", outcome="FAIL", details={"error": str(e)})


def _step_check_parts(run_dir: Path, step: dict, context: dict) -> StepResult:
    """추출된 어셈블리의 부품 이름 검증."""
    bound = step.get("of", "assembly")
    parts_info = context.get(bound)
    if parts_info is None:
        return StepResult(
            kind="check_parts", outcome="FAIL",
            details={"reason": f"'{bound}' not in context — read_xde_step 먼저"},
        )
    expected_names = step.get("expected_names", [])
    name_match = step.get("name_match", "exact")  # exact | any_case | contains

    actual_names = [p["name"] for p in parts_info]

    def _normalize(s: str) -> str:
        return s.lower() if name_match == "any_case" else s

    norm_actual = [_normalize(n) for n in actual_names]
    missing = []
    for exp in expected_names:
        if name_match == "contains":
            if not any(_normalize(exp) in _normalize(a) for a in actual_names):
                missing.append(exp)
        else:
            if _normalize(exp) not in norm_actual:
                missing.append(exp)

    _log_line(
        run_dir, "INFO" if not missing else "ERROR",
        phase="check_parts", actual=actual_names, missing=missing,
    )
    return StepResult(
        kind="check_parts", outcome="PASS" if not missing else "FAIL",
        details={"actual": actual_names, "missing": missing},
    )


def _step_face_count(run_dir: Path, step: dict, context: dict) -> StepResult:  # noqa: ARG001
    """부품 별 face count 범위 검증. (현 골격: 미구현 SKIP — 본격 구현 시 OCCT face 순회 필요)"""
    _log_line(run_dir, "WARNING", phase="face_count", reason="not yet implemented")
    return StepResult(
        kind="face_count", outcome="SKIP",
        details={"reason": "face_count 검증 미구현 — Phase 0 추가 작업"},
    )


def _load_step_shape(path: Path):
    """STEP 파일 → 단일 TopoDS_Shape (어셈블리는 첫 free shape 만)."""
    from OCP.STEPControl import STEPControl_Reader
    from OCP.IFSelect import IFSelect_RetDone

    reader = STEPControl_Reader()
    status = reader.ReadFile(str(path))
    if status != IFSelect_RetDone:
        raise RuntimeError(f"STEP read failed: {path}")
    reader.TransferRoots()
    return reader.OneShape()


def _shape_metrics(shape) -> dict:
    """face count + edge count + volume + bbox."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE

    fc = 0
    it = TopExp_Explorer(shape, TopAbs_FACE)
    while it.More():
        fc += 1
        it.Next()
    ec = 0
    it = TopExp_Explorer(shape, TopAbs_EDGE)
    while it.More():
        ec += 1
        it.Next()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    vol = props.Mass()
    bb = Bnd_Box()
    # AddOptimal_s — fillet/dome 후 triangulation 으로 부풀려지지 않은 정확한 bbox.
    BRepBndLib.AddOptimal_s(shape, bb)
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return {
        "face_count": fc,
        "edge_count": ec,
        "volume": round(vol, 3),
        "bbox": [round(xmin, 3), round(ymin, 3), round(zmin, 3),
                 round(xmax, 3), round(ymax, 3), round(zmax, 3)],
    }


def _step_compare(run_dir: Path, step: dict, context: dict) -> StepResult:  # noqa: ARG001
    """두 STEP 의 face/edge/volume/bbox 정량 비교.

    YAML:
      kind: compare
      a: <step path or context name>
      b: <step path or context name>
      tolerances:
        face_count_pct: 15
        edge_count_pct: 15
        volume_pct: 5
        bbox_mm: 1.0
    """
    a_ref = step.get("a")
    b_ref = step.get("b")
    if not (a_ref and b_ref):
        return StepResult(kind="compare", outcome="FAIL",
                           details={"reason": "a/b 필수"})

    try:
        a_path = Path(a_ref)
        b_path = Path(b_ref)
        a_shape = _load_step_shape(a_path)
        b_shape = _load_step_shape(b_path)
    except Exception as e:
        return StepResult(kind="compare", outcome="FAIL", details={"error": str(e)})

    am = _shape_metrics(a_shape)
    bm = _shape_metrics(b_shape)

    tols = step.get("tolerances", {})
    fc_tol = tols.get("face_count_pct", 15)
    ec_tol = tols.get("edge_count_pct", 15)
    vol_tol = tols.get("volume_pct", 5)
    bbox_tol = tols.get("bbox_mm", 1.0)

    failures = []

    def _pct_diff(a, b):
        return abs(a - b) / max(abs(a), abs(b), 1e-9) * 100

    fc_diff = _pct_diff(am["face_count"], bm["face_count"])
    if fc_diff > fc_tol:
        failures.append(f"face_count diff {fc_diff:.1f}% > {fc_tol}% "
                         f"(a={am['face_count']} b={bm['face_count']})")

    ec_diff = _pct_diff(am["edge_count"], bm["edge_count"])
    if ec_diff > ec_tol:
        failures.append(f"edge_count diff {ec_diff:.1f}% > {ec_tol}%")

    vol_diff = _pct_diff(am["volume"], bm["volume"])
    if vol_diff > vol_tol:
        failures.append(f"volume diff {vol_diff:.1f}% > {vol_tol}% "
                         f"(a={am['volume']} b={bm['volume']})")

    bbox_diffs = [abs(a - b) for a, b in zip(am["bbox"], bm["bbox"])]
    max_bbox = max(bbox_diffs)
    if max_bbox > bbox_tol:
        failures.append(f"bbox max diff {max_bbox:.3f}mm > {bbox_tol}mm")

    details = {
        "a_metrics": am,
        "b_metrics": bm,
        "diffs": {
            "face_count_pct": round(fc_diff, 1),
            "edge_count_pct": round(ec_diff, 1),
            "volume_pct": round(vol_diff, 1),
            "bbox_max_mm": round(max_bbox, 3),
        },
        "failures": failures,
    }
    _log_line(run_dir, "INFO" if not failures else "ERROR",
              phase="compare", **details)

    return StepResult(
        kind="compare",
        outcome="PASS" if not failures else "FAIL",
        details=details,
    )


_DISPATCH = {
    "import_check": _step_import_check,
    "run_python": _step_run_python,
    "check_file_exists": _step_check_file_exists,
    "system_info": _step_system_info,
    "log_summary": _step_log_summary,
    "viewport_offscreen": _step_viewport_offscreen,
    "read_xde_step": _step_read_xde_step,
    "check_parts": _step_check_parts,
    "face_count": _step_face_count,
    "compare": _step_compare,
}


def run_scenario(yaml_path: Path, send_mail: bool = False) -> ScenarioResult:
    spec = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    name = spec["name"]
    run_dir = _new_run_dir(name)
    print(f">>> scenario:  {name}")
    print(f">>> run_dir:   {run_dir}")

    # requires_oem: OEM CAD 없으면 SKIP (회사 컴 전용 시나리오 가드)
    if spec.get("requires_oem"):
        oem_path = Path(os.environ.get(
            "PHONE_DESIGNER_OEM_REF", "reference/galaxy_watch/converted.step",
        ))
        if not oem_path.exists():
            print(f">>> SKIPPED — requires_oem=true, OEM CAD 미존재 ({oem_path})")
            print(f">>>           회사 컴에서만 실행 가능")
            _log_line(run_dir, "WARNING", phase="skipped",
                       reason="requires_oem", oem_path=str(oem_path))
            (run_dir / "meta.json").write_text(
                json.dumps({
                    "scenario": name, "outcome": "SKIPPED",
                    "reason": "requires_oem", "oem_path": str(oem_path),
                    "run_dir": str(run_dir),
                    "hostname": platform.node(),
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return ScenarioResult(name=name, outcome="SKIPPED", run_dir=run_dir)

    started = time.time()
    started_iso = datetime.now(timezone.utc).isoformat()
    _log_line(run_dir, "INFO", phase="start", scenario=name, started_at=started_iso)

    results: list[StepResult] = []
    overall = "PASS"
    context: dict[str, Any] = {}    # step 간 공유 (read_xde_step → check_parts 등)
    for step in spec.get("steps", []):
        kind = step["kind"]
        fn = _DISPATCH.get(kind)
        if fn is None:
            _log_line(run_dir, "WARNING", phase="step_skip", kind=kind, reason="not implemented")
            results.append(StepResult(kind=kind, outcome="SKIP",
                                       details={"reason": "kind not implemented in Phase 0 stub"}))
            print(f"  SKIP  {kind} — runner kind 미구현 (Phase 0 작업)")
            continue
        try:
            r = fn(run_dir, step, context)
        except Exception as e:
            _log_line(run_dir, "ERROR", phase="step_exception", kind=kind, error=str(e))
            r = StepResult(kind=kind, outcome="FAIL", details={"exception": str(e)})
        results.append(r)
        marker = {"PASS": "  ok  ", "FAIL": "  FAIL", "SKIP": "  SKIP"}.get(r.outcome, "  ??  ")
        print(f"{marker} {kind}")
        if r.outcome == "FAIL":
            overall = "FAIL"

    duration = time.time() - started
    _log_line(run_dir, "INFO", phase="end", outcome=overall, duration_s=duration)

    # meta.json
    meta = {
        "scenario": name,
        "outcome": overall,
        "started_at": started_iso,
        "duration_s": duration,
        "run_dir": str(run_dir),
        "hostname": platform.node(),
        "steps": [
            {"kind": r.kind, "outcome": r.outcome, "details": r.details}
            for r in results
        ],
    }
    (run_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f">>> outcome:   {overall}")
    print(f">>> duration:  {duration:.2f}s")

    if send_mail:
        try:
            from phone_designer.scenarios.bundle import bundle_and_mail
            bundle_and_mail(run_dir, meta)
        except Exception as e:
            print(f"[warn] 메일 전송 실패: {e}")

    return ScenarioResult(
        name=name, outcome=overall, run_dir=run_dir,
        steps=results, started_at=started_iso, duration_s=duration,
    )
