"""Top-level CLI (typer). Phase 0 stub — 본격 구현은 Phase 0-1 작업.

현 단계는 setup.ps1 의 안내 명령이 import 에러 없이 실행되어 다음 단계가 명확히
보이도록 하는 골격만 제공.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import typer

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Phone Designer — AI-assisted watch/phone housing designer.",
)


@app.command()
def test(
    scenario: str = typer.Option(..., "--scenario", "-s", help="시나리오 이름 (scenarios/<name>.yaml)"),
    mail: bool = typer.Option(False, "--mail", help="실행 후 자동 zip + SMTP 메일 전송"),
    scenarios_dir: Path = typer.Option(
        Path("scenarios"), "--scenarios-dir", help="시나리오 YAML 디렉토리"
    ),
):
    """시나리오 1개를 실행하고 run_logs/<timestamp>/ 에 결과 적재."""
    from phone_designer.scenarios.runner import run_scenario

    yaml_path = scenarios_dir / f"{scenario}.yaml"
    if not yaml_path.exists():
        typer.echo(f"[error] scenario YAML not found: {yaml_path}", err=True)
        sys.exit(2)

    result = run_scenario(yaml_path, send_mail=mail)
    raise typer.Exit(code=0 if result.outcome == "PASS" else 1)


@app.command()
def config(
    target: str = typer.Argument(..., help="'mail' | 'api-key'"),
):
    """자격증명 등록 (keyring). Phase 0 stub — 본격 구현은 Phase 0 작업."""
    if target == "mail":
        typer.echo("[stub] 메일 자격증명 등록 — Phase 0 의 keyring_storage.py 구현 후 사용.")
        typer.echo("       임시: SMTP 호스트/포트/계정/앱비번을 환경변수로 지정 가능.")
        typer.echo("         $env:PHONE_DESIGNER_SMTP_HOST = 'smtp.gmail.com'")
        typer.echo("         $env:PHONE_DESIGNER_SMTP_PORT = '587'")
        typer.echo("         $env:PHONE_DESIGNER_SMTP_USER = 'you@gmail.com'")
        typer.echo("         $env:PHONE_DESIGNER_SMTP_PASS = '<app-password>'")
        typer.echo("         $env:PHONE_DESIGNER_MAIL_TO   = 'home@example.com'")
    elif target == "api-key":
        typer.echo("[stub] Anthropic API key — Phase 0 구현 후 사용.")
        typer.echo("       임시: $env:ANTHROPIC_API_KEY = 'sk-ant-...'")
    else:
        typer.echo(f"[error] unknown target: {target} (expected 'mail' or 'api-key')", err=True)
        raise typer.Exit(code=2)


@app.command()
def generate(
    plan: Path = typer.Option(..., "--plan", help="plan YAML 경로"),
    out: Path = typer.Option(..., "--out", help="출력 STEP 경로"),
    mode: str = typer.Option("strict", "--mode", help="strict | loose"),
    report: Path = typer.Option(None, "--report",
                                help="(선택) 실행 provenance 보고서 JSON 출력 경로"),
):
    """Plan 실행 → STEP export."""
    from phone_designer.plan.executor import ExecutionMode, PlanExecutor
    from phone_designer.plan.yaml_io import load_plan, save_plan

    loaded = load_plan(plan)
    typer.echo(f">>> plan: {loaded.plan_name} ({len(loaded.steps)} steps)")

    mode_enum = ExecutionMode.STRICT if mode == "strict" else ExecutionMode.LOOSE
    result = PlanExecutor(loaded, mode=mode_enum).run()

    # V5 provenance — write the per-step report BEFORE the outcome gate so
    # a FAIL run still leaves a diagnosable artifact.
    if report is not None:
        import json
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(result.to_report_json(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        typer.echo(f">>> report: {report}")

    for s in loaded.steps:
        marker = {"pass": "  ok  ", "fail": "  FAIL", "skipped": "  SKIP",
                  "pending": "  ??  "}.get(s.status.value, "  ??  ")
        typer.echo(f"{marker} {s.id}  {s.skill}")
        if s.failure:
            typer.echo(f"        {s.failure.error_type}: {s.failure.message}")

    if result.outcome != "PASS":
        typer.echo(">>> outcome: FAIL", err=True)
        raise typer.Exit(code=1)

    # STEP export
    if result.final_body is None:
        typer.echo("[error] final_body is None — plan had no successful steps", err=True)
        raise typer.Exit(code=1)

    body = result.final_body
    shape = body.wrapped if hasattr(body, "wrapped") else body
    _write_step(shape, out)
    typer.echo(f">>> STEP: {out}")

    # 갱신된 freeze 가 적힌 plan 을 별도 (옵션) 저장하지 않고 그냥 끝
    raise typer.Exit(code=0)


def _write_step(shape, out_path: Path) -> None:
    """간단한 STEP write — XDE 없이 단일 shape."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    status = writer.Write(str(out_path))
    if status != IFSelect_RetDone:
        raise RuntimeError(f"STEP write failed: status={status}")


@app.command()
def reproduce(
    reference: Path = typer.Option(..., "--reference", help="reference STEP 경로"),
    out: Path = typer.Option(..., "--out", help="출력 STEP 경로"),
    plan_out: Path = typer.Option(None, "--plan-out", help="(선택) 자동 생성 plan YAML 저장"),
    part_name: str = typer.Option(None, "--part",
                                  help="XDE 어셈블리에서 특정 부품만 (예: housing). "
                                       "지정 안 하면 첫 housing 또는 첫 부품."),
):
    """Reference STEP → topology 분석 → 자동 plan → 실행 → STEP export.

    예:
      phone-designer reproduce --reference fixtures/simple_watch_housing_only.step --out auto.step
      phone-designer reproduce --reference fixtures/simple_watch.step --part housing --out auto_h.step
    """
    from phone_designer.plan.executor import PlanExecutor
    from phone_designer.plan.yaml_io import save_plan
    from phone_designer.reference import (
        TopologyAnalyzer, classify_parts, feature_to_plan, read_xde_step,
    )

    # 1. STEP 읽기 — XDE 부품 분리 시도, 실패 시 단일 shape
    parts = read_xde_step(reference, load_shapes=True)
    if not parts:
        typer.echo("[error] STEP 의 free-shape 가 없음", err=True)
        raise typer.Exit(code=1)

    # 부품 선택
    target = None
    if part_name:
        for p in parts:
            if p.name.lower() == part_name.lower():
                target = p
                break
        if target is None:
            typer.echo(f"[error] part '{part_name}' 없음. 가용: "
                       f"{[p.name for p in parts]}", err=True)
            raise typer.Exit(code=1)
    else:
        cat_map = classify_parts(parts)
        if "housing" in cat_map and cat_map["housing"]:
            target = cat_map["housing"][0]
            typer.echo(f">>> auto-select housing: {target.name}")
        else:
            target = parts[0]
            typer.echo(f">>> auto-select first part: {target.name}")

    # 2. topology 분석
    catalog = TopologyAnalyzer().analyze(target.shape)
    typer.echo(f">>> catalog: {catalog.summary()}")

    # 3. feature → plan
    plan = feature_to_plan(catalog, target.shape, plan_name=f"reproduce_{target.name}")
    typer.echo(f">>> plan: {len(plan.steps)} steps")
    for s in plan.steps:
        typer.echo(f"    - {s.id}  {s.skill}  args={list(s.args.keys())}")

    if plan_out is not None:
        save_plan(plan, plan_out)
        typer.echo(f">>> plan YAML: {plan_out}")

    # 4. 실행
    result = PlanExecutor(plan).run()
    for s in plan.steps:
        marker = {"pass": "  ok  ", "fail": "  FAIL"}.get(s.status.value, "  ??  ")
        typer.echo(f"{marker} {s.id}  {s.skill}")
        if s.failure:
            typer.echo(f"        {s.failure.error_type}: {s.failure.message}")

    if result.outcome != "PASS" or result.final_body is None:
        typer.echo(">>> reproduce FAILED", err=True)
        raise typer.Exit(code=1)

    # 5. STEP export
    shape = result.final_body.wrapped if hasattr(result.final_body, "wrapped") else result.final_body
    _write_step(shape, out)
    typer.echo(f">>> STEP: {out}")
    raise typer.Exit(code=0)


@app.command()
def validate(
    plan: Path = typer.Option(..., "--plan", help="plan YAML"),
    budget: Path = typer.Option(..., "--budget", help="ManufacturingBudget YAML"),
    process: str = typer.Option(None, "--process",
                                 help="단일 process. 미지정 시 budget 의 모든 process"),
):
    """Plan → DFM 검증 (skill-process 호환 + wall + draft + undercut).

    예:
      phone-designer validate --plan plans/simple_watch_outer.yaml --budget budgets/al.yaml
    """
    from phone_designer.manufacturing.budget import ManufacturingBudget
    from phone_designer.manufacturing.dfm.runner import run_dfm
    from phone_designer.plan.executor import PlanExecutor
    from phone_designer.plan.yaml_io import load_plan

    loaded_plan = load_plan(plan)
    loaded_budget = ManufacturingBudget.from_yaml(budget)
    typer.echo(f">>> plan:   {loaded_plan.plan_name} ({len(loaded_plan.steps)} steps)")
    typer.echo(f">>> budget: processes={loaded_budget.allowed_processes}")

    # 1. skill ↔ process 호환
    compat = loaded_budget.validate_plan(loaded_plan)
    if not compat["ok"]:
        typer.echo(">>> skill-process compatibility violations:", err=True)
        for v in compat["violations"]:
            typer.echo(f"    - {v['step_id']} ({v['skill']}): {v['reason']}", err=True)
    else:
        typer.echo(">>> skill-process compatibility: OK")

    # 2. plan 실행
    typer.echo(">>> executing plan...")
    result = PlanExecutor(loaded_plan).run()
    if result.outcome != "PASS" or result.final_body is None:
        typer.echo(">>> plan execution FAILED — DFM skipped", err=True)
        raise typer.Exit(code=1)

    shape = result.final_body.wrapped if hasattr(result.final_body, "wrapped") \
            else result.final_body

    # 3. DFM (process 별)
    target_processes = [process] if process else loaded_budget.allowed_processes
    overall = "OK"
    for proc_code in target_processes:
        typer.echo(f">>> DFM check: {proc_code}")
        try:
            report = run_dfm(shape, process_code=proc_code)
        except Exception as e:
            typer.echo(f"    [error] {e}", err=True)
            continue
        typer.echo(f"    {report.summary()}")
        for v in report.all_violations[:5]:
            typer.echo(f"      - {v.kind}: {v.message}")
        if len(report.all_violations) > 5:
            typer.echo(f"      ... +{len(report.all_violations) - 5} more")
        if report.outcome.value == "violate":
            overall = "VIOLATE"

    typer.echo(f">>> overall DFM: {overall}")
    raise typer.Exit(code=0 if overall == "OK" else 1)


@app.command()
def view(
    path: Path = typer.Argument(..., help="STEP/glb 파일 경로"),
    color: str = typer.Option("lightblue", help="mesh 표면 색"),
    show_edges: bool = typer.Option(True, help="edge 표시 여부"),
    second: Path = typer.Option(None, "--second", "-2",
                                 help="비교용 두번째 STEP/glb (반투명 빨강)"),
):
    """STEP 또는 glb 파일을 PyVista interactive viewer 로 띄움.

    예:
      phone-designer view fixtures/simple_watch.step
      phone-designer view out.step --second fixtures/simple_watch_housing_only.step
    """
    import pyvista as pv
    from phone_designer.logging.viewport_capture import _shape_to_polydata

    def _load(p: Path):
        suffix = p.suffix.lower()
        if suffix in (".step", ".stp"):
            from OCP.STEPControl import STEPControl_Reader
            from OCP.IFSelect import IFSelect_RetDone
            reader = STEPControl_Reader()
            status = reader.ReadFile(str(p))
            if status != IFSelect_RetDone:
                raise RuntimeError(f"STEP read failed: {p}")
            reader.TransferRoots()
            shape = reader.OneShape()
            return _shape_to_polydata(shape)
        elif suffix in (".glb", ".gltf"):
            return pv.read(str(p))
        else:
            raise ValueError(f"지원 안 함: {suffix} (.step/.stp/.glb/.gltf 만)")

    typer.echo(f">>> loading: {path}")
    mesh = _load(path)
    typer.echo(f">>> mesh:    {mesh.n_points} verts, {mesh.n_cells} faces")

    plotter = pv.Plotter(window_size=[1280, 800])
    plotter.add_mesh(mesh, color=color, show_edges=show_edges, edge_color="gray")

    if second is not None:
        typer.echo(f">>> overlay: {second}")
        mesh2 = _load(second)
        plotter.add_mesh(mesh2, color="red", opacity=0.4,
                          show_edges=False, label="reference")

    plotter.add_axes()
    plotter.show_bounds(grid="front", location="outer", color="gray")
    typer.echo(">>> viewer 띄움 — Q 또는 창 닫기로 종료.")
    typer.echo("    드래그=회전, Shift+드래그=평행이동, 휠=줌, R=리셋")
    plotter.show()


@app.command()
def screenshots(
    path: Path = typer.Argument(..., help="STEP 파일 경로"),
    out_dir: Path = typer.Option(Path("run_logs/_view"), help="PNG 저장 위치"),
    views: str = typer.Option("iso,top,side,front",
                               help="콤마 구분 — iso/top/bottom/side/front/back"),
):
    """STEP 파일에 대해 여러 각도 PNG 캡처 (회사 컴 메일용 빠른 캡처)."""
    from phone_designer.logging.viewport_capture import (
        CaptureSpec, capture_step_file,
    )
    spec = CaptureSpec(
        out_dir=out_dir,
        label=path.stem,
        views=[v.strip() for v in views.split(",")],
    )
    paths = capture_step_file(path, spec)
    for p in paths:
        typer.echo(f"  {p}")


@app.command()
def compare(
    a: Path = typer.Argument(..., help="첫 STEP 파일"),
    b: Path = typer.Argument(..., help="두번째 STEP 파일"),
    face_pct: float = typer.Option(15.0, help="face_count 허용 차이 %"),
    edge_pct: float = typer.Option(15.0, help="edge_count 허용 차이 %"),
    vol_pct: float = typer.Option(5.0, help="volume 허용 차이 %"),
    bbox_mm: float = typer.Option(1.0, help="bbox 각 축 허용 차이 mm"),
):
    """두 STEP 의 face/edge/volume/bbox 정량 비교 — compare runner kind 의 CLI."""
    from phone_designer.scenarios.runner import _load_step_shape, _shape_metrics

    sa = _load_step_shape(a)
    sb = _load_step_shape(b)
    ma = _shape_metrics(sa)
    mb = _shape_metrics(sb)

    def _pct(x, y):
        return abs(x - y) / max(abs(x), abs(y), 1e-9) * 100

    typer.echo(f">>> A {a.name}: faces={ma['face_count']} edges={ma['edge_count']} "
               f"vol={ma['volume']:.1f}mm³ bbox={ma['bbox']}")
    typer.echo(f">>> B {b.name}: faces={mb['face_count']} edges={mb['edge_count']} "
               f"vol={mb['volume']:.1f}mm³ bbox={mb['bbox']}")

    fcd = _pct(ma["face_count"], mb["face_count"])
    ecd = _pct(ma["edge_count"], mb["edge_count"])
    vd = _pct(ma["volume"], mb["volume"])
    bb_diffs = [abs(x - y) for x, y in zip(ma["bbox"], mb["bbox"])]

    typer.echo("")
    typer.echo(f"diff face_count: {fcd:.1f}% (tol {face_pct}%)")
    typer.echo(f"diff edge_count: {ecd:.1f}% (tol {edge_pct}%)")
    typer.echo(f"diff volume:     {vd:.1f}% (tol {vol_pct}%)")
    typer.echo(f"diff bbox max:   {max(bb_diffs):.3f}mm (tol {bbox_mm}mm)")

    fails = []
    if fcd > face_pct: fails.append("face_count")
    if ecd > edge_pct: fails.append("edge_count")
    if vd > vol_pct: fails.append("volume")
    if max(bb_diffs) > bbox_mm: fails.append("bbox")
    if fails:
        typer.echo(f">>> FAIL — exceeded: {fails}")
        raise typer.Exit(code=1)
    typer.echo(">>> PASS")
    raise typer.Exit(code=0)


@app.command()
def synthesize(
    arrangement: Path = typer.Option(..., "--arrangement", "-a",
                                       help="ComponentArrangement YAML"),
    out_plan: Path = typer.Option(..., "--out-plan", help="합성 plan YAML"),
    out_step: Path = typer.Option(None, "--out-step", help="(선택) 실행 후 STEP"),
):
    """ComponentArrangement → housing_synth_rule v0 → Plan → (선택) STEP."""
    import yaml as _yaml

    from phone_designer.components.arrangement import ComponentArrangement
    from phone_designer.plan.executor import PlanExecutor
    from phone_designer.plan.yaml_io import save_plan
    from phone_designer.planner.housing_synth_rule import HousingSynthRule

    raw = _yaml.safe_load(arrangement.read_text(encoding="utf-8"))
    arr = ComponentArrangement.model_validate(raw)
    typer.echo(f">>> arrangement: {len(arr.components)} components")

    plan = HousingSynthRule().synthesize(arr)
    typer.echo(f">>> synthesized plan: {len(plan.steps)} steps")
    save_plan(plan, out_plan)
    typer.echo(f">>> plan YAML: {out_plan}")

    if out_step is not None:
        result = PlanExecutor(plan).run()
        if result.outcome != "PASS" or result.final_body is None:
            typer.echo(">>> plan execution FAILED — STEP skip", err=True)
            raise typer.Exit(code=1)
        shape = (result.final_body.wrapped
                  if hasattr(result.final_body, "wrapped") else result.final_body)
        _write_step(shape, out_step)
        typer.echo(f">>> STEP: {out_step}")
    raise typer.Exit(code=0)


@app.command()
def ui():
    """빈 inspector GUI 실행 (메뉴에서 plan/STEP 열기)."""
    from phone_designer.ui import run_inspector
    raise typer.Exit(run_inspector())


@app.command()
def inspect(
    plan: Path = typer.Option(None, "--plan", help="plan YAML 즉시 실행"),
    step: Path = typer.Option(None, "--step", help="단일 STEP 즉시 표시"),
    reference: Path = typer.Option(None, "--reference", "-r",
                                    help="비교용 reference STEP (overlay)"),
):
    """Inspector GUI — plan 실행 결과 step 별 viewport, reference overlay 가능.

    예:
      phone-designer inspect --plan plans/simple_watch_outer.yaml \\
                              --reference fixtures/simple_watch_housing_only.step
      phone-designer inspect --step out.step
    """
    from phone_designer.ui import run_inspector
    if plan is None and step is None:
        typer.echo("[hint] --plan 또는 --step 지정. 빈 GUI 는 `phone-designer ui`.")
    raise typer.Exit(run_inspector(plan=plan, step=step, reference=reference))


@app.command("inspect-re")
def inspect_re(
    catalog: Path = typer.Option(
        None, "--catalog",
        help="JSON/YAML 파일에 저장된 feature_catalog (extract_feature_catalog 결과). "
             "지정 안 하면 --step 의 body 로부터 즉석 추출.",
    ),
    plan_a: Path = typer.Option(
        None, "--plan-a", help="좌측 (reference) plan YAML",
    ),
    plan_b: Path = typer.Option(
        None, "--plan-b", help="우측 (reconstructed) plan YAML",
    ),
    step: Path = typer.Option(
        None, "--step",
        help="(선택) viewport 에 띄울 STEP 파일. catalog 가 None 이면 여기서 추출.",
    ),
):
    """Inspector + RE 시각화 패널 (feature_catalog 트리 + plan diff)."""
    import json
    import yaml as _yaml

    from phone_designer.ui import run_inspector_with_panels

    catalog_dict: dict | None = None
    if catalog is not None:
        if not catalog.exists():
            typer.echo(f"[error] catalog file not found: {catalog}", err=True)
            raise typer.Exit(code=2)
        text = catalog.read_text(encoding="utf-8")
        try:
            if catalog.suffix.lower() == ".json":
                catalog_dict = json.loads(text)
            else:
                catalog_dict = _yaml.safe_load(text)
        except Exception as exc:
            typer.echo(f"[error] failed to parse catalog: {exc}", err=True)
            raise typer.Exit(code=2)
        # Tolerate "wrapped" catalogs from skill extras
        if isinstance(catalog_dict, dict) and "feature_catalog" in catalog_dict:
            catalog_dict = catalog_dict["feature_catalog"]
    elif step is not None:
        # Try to extract on-the-fly from the STEP
        try:
            from phone_designer.scenarios.runner import _load_step_shape
            from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
                ExtractFeatureCatalog,
            )
            shape = _load_step_shape(step)
            try:
                from build123d import Part
                body = Part(shape)
            except Exception:
                body = shape
            cat_res = ExtractFeatureCatalog().apply(body, {})
            catalog_dict = cat_res.extras.get("feature_catalog")
            typer.echo(f">>> catalog: extracted from {step.name}")
        except Exception as exc:
            typer.echo(f"[warn] could not extract catalog: {exc}")

    typer.echo(">>> launching inspect-re UI...")
    code = run_inspector_with_panels(
        panel="both",
        catalog=catalog_dict,
        plan_a=plan_a,
        plan_b=plan_b,
        step=step,
    )
    raise typer.Exit(code or 0)


@app.command()
def analyze(
    part: Path = typer.Argument(..., help="분석할 CAD 파일 (STEP/IGES/BREP)"),
    out: Path = typer.Option(
        None, "--out", "-o",
        help="리포트 출력 경로. .html → 자가포함 HTML, .pdf → reportlab PDF "
             "(미설치면 HTML 로 폴백), .json → 전체 분석 JSON.",
    ),
    reconstruct: bool = typer.Option(
        False, "--reconstruct",
        help="박스모드 역설계 재구성 + geometry_deviation Hausdorff 채점 (느림).",
    ),
    processes: str = typer.Option(
        "cnc_milling,injection_molding", "--processes",
        help="DFM 평가 공정 (쉼표 구분).",
    ),
    timeout_s: int = typer.Option(
        0, "--timeout-s",
        help="0(기본)=in-process 분석(전체 기능, pdf 포함). >0 이면 분석을 "
             "watchdog 가 붙은 격리 subprocess 에서 실행 — 대형 어셈블리가 "
             "OCCT 안에서 무한 정지(hang)하는 대신 정직하게 timeout 으로 끝남 "
             "(Windows 엔 in-process 인터럽트가 불가능). 이 모드는 html/json 만 "
             "출력하며 pdf 는 html 로 폴백.",
    ),
):
    """단일 부품 front-door: CAD 파일 1개 → 품질 리포트(위상/벽두께/draft/blend/
    질량/DFM) + feature 카탈로그 + 편집가능 주요치수 + (선택)재구성 fidelity.

    예:
      phone-designer analyze part.step
      phone-designer analyze part.step -o report.html
      phone-designer analyze part.step -o report.pdf --reconstruct
      phone-designer analyze part.step -o analysis.json --reconstruct
      phone-designer analyze big_assembly.step -o r.html --timeout-s 300
    """
    if not part.exists():
        typer.echo(f"[error] file not found: {part}", err=True)
        raise typer.Exit(code=2)

    import json as _json

    from phone_designer.skills import export_manifest  # noqa: F401 — register
    from phone_designer.skills.reverse_engineer.analyze_part import AnalyzePart

    want_html = out is not None and out.suffix.lower() == ".html"
    want_pdf = out is not None and out.suffix.lower() == ".pdf"
    want_json = out is not None and out.suffix.lower() == ".json"

    # ── watchdog path — large assemblies that may HANG in OCCT (174s import +
    #    non-preemptible detector thread-pool). Delegate to the batch harness's
    #    per-file subprocess+watchdog (the only way to bound an uninterruptible
    #    OCCT call on Windows) so the run times out honestly instead of hanging.
    if timeout_s and timeout_s > 0:
        _analyze_with_watchdog(
            part, out, reconstruct,
            [p.strip() for p in processes.split(",") if p.strip()],
            timeout_s, want_json=want_json, want_pdf=want_pdf,
        )
        return

    typer.echo(f">>> analyzing {part.name} ...")
    res = AnalyzePart().apply(None, {
        "part_path": str(part),
        "processes": [p.strip() for p in processes.split(",") if p.strip()],
        "include_html": want_html or want_pdf or out is None,
        "pdf": want_pdf,
        "reconstruct": reconstruct,
    })
    pa = res.extras["part_analysis"]

    # console summary
    typer.echo(f"    part: {pa.get('part_id')}  bbox_mm: {pa.get('bbox_mm')}")
    counts = (pa.get("feature_catalog") or {}).get("counts") or {}
    if counts:
        typer.echo("    features: " + ", ".join(
            f"{k}={v}" for k, v in counts.items() if v))
    for d in (pa.get("key_dimensions") or [])[:6]:
        typer.echo(f"    dim  {d.get('name')}: {d.get('value_mm')} mm")
    rec = pa.get("reconstruction")
    if rec:
        typer.echo(
            f"    reconstruction: base={rec.get('base_mechanism')} "
            f"match={rec.get('match_ratio')} hausdorff_mm={rec.get('hausdorff_mm')}")
    for stage, info in (pa.get("_stages") or {}).items():
        if not info.get("ok"):
            typer.echo(f"    [stage {stage} failed] {info.get('error')}")

    if out is None:
        raise typer.Exit(0)

    out.parent.mkdir(parents=True, exist_ok=True)
    if want_pdf:
        pdf = pa.get("report_pdf") or {}
        pb = pdf.get("pdf_bytes")
        if pb:
            out.write_bytes(pb)
            typer.echo(f">>> wrote PDF ({pdf.get('pdf_engine')}) -> {out}")
        else:
            html = pa.get("report_html") or ""
            fallback = out.with_suffix(".html")
            fallback.write_text(html, encoding="utf-8")
            typer.echo(f"[note] no PDF engine ({pdf.get('note')}); "
                       f"wrote print-ready HTML -> {fallback}")
    elif want_html:
        out.write_text(pa.get("report_html") or "", encoding="utf-8")
        typer.echo(f">>> wrote HTML -> {out}")
    elif want_json:
        # drop the raw pdf bytes from the JSON (not serializable / huge)
        dump = dict(pa)
        if isinstance(dump.get("report_pdf"), dict):
            rp = dict(dump["report_pdf"])
            rp.pop("pdf_bytes", None)
            dump["report_pdf"] = rp
        out.write_text(_json.dumps(dump, indent=1, default=str), encoding="utf-8")
        typer.echo(f">>> wrote analysis JSON -> {out}")
    raise typer.Exit(0)


def _analyze_with_watchdog(part, out, reconstruct, processes, timeout_s,
                           *, want_json, want_pdf):
    """Run analyze_part in an isolated subprocess with a wall-clock watchdog
    (reuses the batch harness ``run_one``). The child is killed on expiry, so a
    huge assembly that hangs in OCCT (174s import + non-preemptible detector
    thread-pool) costs at most ``timeout_s`` instead of hanging the CLI. Writes
    the report to ``out`` and prints the console summary from the returned
    compact record. pdf falls back to html in this mode (the batch worker
    produces html/json)."""
    import shutil
    import tempfile

    from phone_designer.corpus import batch_analyze

    fmt = "json" if want_json else "html"
    if want_pdf:
        typer.echo("[note] --timeout-s watchdog mode produces HTML (not PDF); "
                   "writing print-ready HTML instead.")
    typer.echo(f">>> analyzing {part.name} (subprocess, {timeout_s}s watchdog) ...")
    with tempfile.TemporaryDirectory(prefix="analyze_wd_") as td:
        rec = batch_analyze.run_one(
            str(part), fmt, reconstruct, processes, td, timeout_s=timeout_s,
        )
        if not rec.get("ok"):
            err = rec.get("error") or "analysis produced no report"
            typer.echo(f"[error] {err}", err=True)
            if "TIMEOUT" in str(err):
                typer.echo(
                    "        large monolithic assemblies are a known scale "
                    "limit (174s import + non-preemptible detector pool); the "
                    "per-component path is the only route and yields feature "
                    "match_ratio, not a reconstructed-body hausdorff.", err=True)
            raise typer.Exit(code=1)

        typer.echo(
            f"    part: {rec.get('part_id')}  bbox_mm: {rec.get('bbox_mm')}  "
            f"({rec.get('duration_s')}s)")
        counts = rec.get("feature_counts") or {}
        if counts:
            typer.echo("    features: " + ", ".join(
                f"{k}={v}" for k, v in counts.items() if v))
        for d in (rec.get("key_dimensions") or [])[:6]:
            typer.echo(f"    dim  {d.get('name')}: {d.get('value_mm')} mm")
        recon = rec.get("reconstruction")
        if recon:
            typer.echo(
                f"    reconstruction: strategy={recon.get('strategy')} "
                f"base={recon.get('base_mechanism')} "
                f"match={recon.get('match_ratio')} "
                f"hausdorff_mm={recon.get('hausdorff_mm')}")

        if out is not None:
            report_name = (
                rec.get("report_json") if fmt == "json" else rec.get("report_html")
            )
            if report_name:
                target = out.with_suffix(".html") if want_pdf else out
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(Path(td) / report_name, target)
                typer.echo(f">>> wrote {fmt.upper()} -> {target}")
            else:
                typer.echo("[warn] no report file produced", err=True)
    raise typer.Exit(0)


@app.command("analyze-batch")
def analyze_batch(
    paths: list[str] = typer.Argument(
        None,
        help="입력 CAD 파일/디렉토리/glob (예: corpus/oem/kicad__*.step). "
             "디렉토리는 안의 *.step/*.stp/*.iges/*.brep 를 모두 포함.",
    ),
    glob: str = typer.Option(
        None, "--glob",
        help="추가 입력 glob 패턴 (예: \"corpus/oem/*.step\").",
    ),
    out_dir: Path = typer.Option(
        ..., "--out-dir",
        help="부품별 리포트 + 통합 index.json/index.html 출력 디렉토리 (필수).",
    ),
    fmt: str = typer.Option(
        "html", "--format",
        help="부품별 리포트 형식: html | json | both.",
    ),
    reconstruct: bool = typer.Option(
        False, "--reconstruct",
        help="각 부품에 박스모드 재구성 + Hausdorff 채점 (느림).",
    ),
    processes: str = typer.Option(
        "cnc_milling,injection_molding", "--processes",
        help="DFM 평가 공정 (쉼표 구분).",
    ),
    timeout_s: int = typer.Option(
        None, "--timeout-s",
        help="파일당 subprocess watchdog (기본 300s). 한 개의 멈춘 OCCT "
             "호출이 배치 전체를 막지 않도록 격리.",
    ),
    workers: int = typer.Option(
        1, "--workers",
        help="병렬 worker 수 (기본 1=직렬). N>1 이면 파일별 subprocess 를 "
             "ProcessPoolExecutor 로 분산 (상한 min(cpu-2, n_files)). "
             "솔직한 주의: per-file timeout 은 벽시계 watchdog 이므로 경계에 "
             "걸친 파일은 경합 하에 거짓 timeout 가능 — 권위있는 실행은 "
             "--workers 1.",
    ),
):
    """배치 front-door: 여러 CAD 파일에 analyze_part 를 한번에 돌려 통합
    deliverable 을 생성. 각 파일은 watchdog 가 붙은 격리 subprocess 에서 분석되어
    하나의 나쁜/느린 파일이 배치 전체를 막지 않음.

    출력 (--out-dir 아래):
      <part_id>.html / .json   부품별 analyze_part 리포트
      index.json               {n_files, n_ok, n_failed, parts:[...]} 요약
      index.html               부품별 리포트를 링크하는 정렬가능 표

    예:
      phone-designer analyze-batch corpus/oem/kicad__C_0603_1608Metric.step \\
                                    corpus/oem/occt__linkrods.step \\
                                    --out-dir run_logs/batch
      phone-designer analyze-batch --glob "corpus/oem/kicad__*.step" \\
                                    --out-dir run_logs/batch --format both
      phone-designer analyze-batch corpus/oem/ --out-dir out --workers 4
    """
    from phone_designer.corpus import batch_analyze

    if fmt not in ("html", "json", "both"):
        typer.echo(f"[error] unknown --format: {fmt} (html|json|both)", err=True)
        raise typer.Exit(code=2)

    files = batch_analyze.resolve_inputs(list(paths or []), glob)
    if not files:
        typer.echo(
            "[error] no CAD files resolved from the given paths/--glob "
            "(accepts *.step/*.stp/*.iges/*.igs/*.brep, files, dirs, globs).",
            err=True,
        )
        raise typer.Exit(code=2)

    typer.echo(f">>> resolved {len(files)} input file(s)")
    eff_timeout = timeout_s or batch_analyze.PER_FILE_TIMEOUT_S
    index = batch_analyze.run_batch(
        files,
        out_dir=out_dir,
        fmt=fmt,
        reconstruct=reconstruct,
        processes=[p.strip() for p in processes.split(",") if p.strip()],
        timeout_s=eff_timeout,
        workers=workers,
        log=typer.echo,
    )

    typer.echo(
        f">>> done: {index['n_ok']}/{index['n_files']} ok, "
        f"{index['n_failed']} failed"
    )
    typer.echo(f">>> index.json: {Path(out_dir) / 'index.json'}")
    typer.echo(f">>> index.html: {Path(out_dir) / 'index.html'}")
    # non-zero exit only if EVERY file failed (a batch with some failures is a
    # valid, useful deliverable — the per-part records carry the errors).
    raise typer.Exit(code=1 if index["n_ok"] == 0 and index["n_files"] else 0)


@app.command("corpus-test")
def corpus_test(
    dir: Path = typer.Option(
        Path("corpus/oem/"), "--dir", help="OEM 파일 디렉토리 (재귀 탐색)"
    ),
    report_out: Path = typer.Option(
        Path("docs/oem_corpus_report.md"), "--report-out",
        help="markdown 보고서 출력 경로",
    ),
    tolerance_pct: float = typer.Option(
        30.0, "--tolerance-pct",
        help="원본 대비 regen 부피 drift 허용 % (이내 = pass)",
    ),
):
    """OEM corpus 전체에 대해 RE 파이프라인 (extract → plan → executor) 을
    실행하고 fidelity 보고서를 생성.

    각 파일에 대해:
      1. STEP/IGES/BREP import → 원본 측정 (vol, face_count, bbox)
      2. extract_feature_catalog 로 catalog 추출
      3. plan_from_feature_catalog 로 plan 합성
      4. PlanExecutor 로 실행 → regen body 측정
      5. cube collapse (face_count <= 6 & regen 만) + drift % 판정

    exit 0  = 모든 파일이 tolerance 이내
    exit 1  = 1개 이상 violate / error
    """
    from phone_designer.plan.executor import PlanExecutor
    from phone_designer.plan.model import Plan
    from phone_designer.scenarios.runner import _load_step_shape, _shape_metrics
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )
    from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
        PlanFromFeatureCatalog,
    )

    suffixes = {".step", ".stp", ".iges", ".igs", ".brep"}
    if not dir.exists():
        typer.echo(f"[error] corpus dir not found: {dir}", err=True)
        raise typer.Exit(code=2)

    files = sorted(
        p for p in dir.rglob("*")
        if p.is_file() and p.suffix.lower() in suffixes
    )
    if not files:
        typer.echo(f"[warn] no STEP/IGES/BREP files under {dir}", err=True)

    rows: list[dict[str, Any]] = []
    for src in files:
        rel = src.relative_to(dir) if src.is_relative_to(dir) else src
        typer.echo(f">>> {rel}")
        row: dict[str, Any] = {
            "file": str(rel).replace("\\", "/"),
            "original_vol": None,
            "regen_vol": None,
            "drift_pct": None,
            "original_faces": None,
            "regen_faces": None,
            "status": "ERROR",
            "error": "",
        }
        try:
            shape = _load_step_shape(src)
            orig = _shape_metrics(shape)
            row["original_vol"] = float(orig["volume"])
            row["original_faces"] = int(orig["face_count"])

            # build123d Part wrapper expected downstream by skills
            try:
                from build123d import Part
                body = Part(shape)
            except Exception:
                body = shape

            # extract → plan
            cat_res = ExtractFeatureCatalog().apply(body, {})
            catalog = cat_res.extras.get("feature_catalog", {})
            plan_res = PlanFromFeatureCatalog().apply(body, {"catalog": catalog})
            plan_dict = plan_res.extras.get("generated_plan") or {}
            if not plan_dict or not plan_dict.get("steps"):
                row["status"] = "NO_PLAN"
                row["error"] = "plan_from_feature_catalog returned empty steps"
                rows.append(row)
                continue

            plan = Plan.model_validate(plan_dict)
            exec_res = PlanExecutor(plan).run()
            if exec_res.final_body is None:
                row["status"] = "EXEC_FAIL"
                row["error"] = (
                    f"executor outcome={exec_res.outcome}, "
                    f"errors={exec_res.error_count}"
                )
                rows.append(row)
                continue

            regen_shape = (
                exec_res.final_body.wrapped
                if hasattr(exec_res.final_body, "wrapped")
                else exec_res.final_body
            )
            regen = _shape_metrics(regen_shape)
            row["regen_vol"] = float(regen["volume"])
            row["regen_faces"] = int(regen["face_count"])

            ov = row["original_vol"] or 0.0
            rv = row["regen_vol"] or 0.0
            drift = (
                abs(ov - rv) / max(abs(ov), 1e-9) * 100.0 if ov else float("inf")
            )
            row["drift_pct"] = round(drift, 2)

            # cube-collapse heuristic — regen has only a base box surviving.
            cube_collapse = (
                row["regen_faces"] is not None
                and row["regen_faces"] <= 6
                and (row["original_faces"] or 0) > 6
            )
            if cube_collapse:
                row["status"] = "CUBE_COLLAPSE"
                row["error"] = (
                    f"regen face_count={row['regen_faces']} "
                    f"(orig={row['original_faces']})"
                )
            elif drift <= tolerance_pct:
                row["status"] = "PASS"
            else:
                row["status"] = "DRIFT"
                row["error"] = f"drift {drift:.1f}% > tol {tolerance_pct}%"

        except Exception as e:
            row["status"] = "ERROR"
            row["error"] = f"{type(e).__name__}: {e}"
        rows.append(row)

    # ── Render markdown report ────────────────────────────────────────────
    report_out.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# OEM Corpus Fidelity Report")
    lines.append("")
    lines.append(f"- corpus dir: `{dir}`")
    lines.append(f"- tolerance:  ±{tolerance_pct:.1f}% volume drift")
    lines.append(f"- files:      {len(rows)}")
    n_pass = sum(1 for r in rows if r["status"] == "PASS")
    lines.append(f"- passed:     {n_pass} / {len(rows)}")
    lines.append("")
    lines.append(
        "| file | orig_vol (mm³) | regen_vol (mm³) | drift % | "
        "orig_faces | regen_faces | status | error |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---|---|")
    for r in rows:
        def _fmt(v, suffix=""):
            if v is None:
                return "—"
            if isinstance(v, float):
                return f"{v:.2f}{suffix}"
            return f"{v}{suffix}"
        lines.append(
            f"| {r['file']} | {_fmt(r['original_vol'])} | "
            f"{_fmt(r['regen_vol'])} | {_fmt(r['drift_pct'])} | "
            f"{_fmt(r['original_faces'])} | {_fmt(r['regen_faces'])} | "
            f"{r['status']} | {r['error']} |"
        )
    lines.append("")
    report_out.write_text("\n".join(lines), encoding="utf-8")
    typer.echo(f">>> report: {report_out}")

    failed = [r for r in rows if r["status"] != "PASS"]
    if failed:
        typer.echo(
            f">>> {len(failed)}/{len(rows)} file(s) failed "
            f"(tolerance {tolerance_pct}%)",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f">>> all {len(rows)} file(s) within tolerance")
    raise typer.Exit(code=0)


@app.command("corpus-regress")
def corpus_regress(
    mode: str = typer.Option(
        "preserve_brep", "--mode",
        help="preserve_brep | box | both",
    ),
    subset: str = typer.Option(
        "root", "--subset",
        help="root | complex | industrial | revolved | all",
    ),
    update_baseline: bool = typer.Option(
        False, "--update-baseline",
        help="corpus/baselines/<mode>_<subset>.json 재작성 (비교 생략)",
    ),
    json_out: Path = typer.Option(
        None, "--json-out", help="(선택) sweep 결과 + 비교 JSON 출력 경로"
    ),
    timeout_s: int = typer.Option(
        None, "--timeout-s",
        help="파일당 subprocess 타임아웃 (기본 300s)",
    ),
    workers: int = typer.Option(
        1, "--workers",
        help="병렬 worker 수 (기본 1=직렬, byte-identical). N>1 이면 "
             "ProcessPoolExecutor 로 파일별 subprocess 를 분산 — 각 worker 는 "
             "고유 plan 파일을 사용해 plans/reconstructed_plan.yaml race 회피. "
             "결과는 입력 순서로 재정렬되어 worker 수와 무관하게 동일. "
             "상한 min(cpu-2, n_files).",
    ),
):
    """Tracked corpus regression — RE round-trip 을 baseline 과 비교.

    각 파일을 격리된 subprocess (300s watchdog) 에서 실행:
      ImportStep → ExtractFeatureCatalog → PlanFromFeatureCatalog
      → PlanExecutor → ExtractFeatureCatalog(regen) → FeatureFidelityDiff

    exit 0 = baseline 대비 회귀 없음
    exit 1 = match_ratio 0.005 초과 하락 또는 신규 error 발생 파일 존재
    exit 2 = 사용법 오류 / baseline 없음
    """
    import json

    from phone_designer.corpus import regress

    if mode not in (*regress.MODES, "both"):
        typer.echo(f"[error] unknown --mode: {mode}", err=True)
        raise typer.Exit(code=2)
    if subset not in regress.SUBSETS:
        typer.echo(f"[error] unknown --subset: {subset}", err=True)
        raise typer.Exit(code=2)

    modes = list(regress.MODES) if mode == "both" else [mode]
    eff_timeout = timeout_s or regress.PER_FILE_TIMEOUT_S
    payloads: list[dict[str, Any]] = []
    any_regression = False

    for m in modes:
        records = regress.run_sweep(
            m, subset, timeout_s=eff_timeout, log=typer.echo, workers=workers
        )
        payload: dict[str, Any] = {
            "mode": m, "subset": subset, "records": records,
        }
        if update_baseline:
            path = regress.save_baseline(m, subset, records)
            typer.echo(f">>> baseline written: {path}")
        else:
            baseline = regress.load_baseline(m, subset)
            if baseline is None:
                typer.echo(
                    f"[error] no baseline at "
                    f"{regress.baseline_path(m, subset)} — run with "
                    f"--update-baseline first",
                    err=True,
                )
                raise typer.Exit(code=2)
            cmp = regress.compare_to_baseline(records, baseline)
            payload["comparison"] = cmp
            for r in cmp["regressions"]:
                typer.echo(f"REGRESSION {r['file']}: {r['reason']}", err=True)
            for r in cmp["improvements"]:
                typer.echo(
                    f"improved   {r['file']}: "
                    f"{r['baseline_match']} -> {r['current_match']}"
                )
            for f in cmp["new_files"]:
                typer.echo(f"info: new file (not in baseline): {f}")
            for f in cmp["missing_files"]:
                typer.echo(f"info: in baseline but not on disk: {f}")
            typer.echo(
                f">>> [{m}/{subset}] {len(records)} file(s): "
                f"{len(cmp['regressions'])} regression(s), "
                f"{len(cmp['improvements'])} improvement(s)"
            )
            if cmp["regressions"]:
                any_regression = True
        payloads.append(payload)

    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(
            json.dumps({"runs": payloads}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        typer.echo(f">>> json: {json_out}")

    raise typer.Exit(code=1 if any_regression else 0)


@app.command("compare-regress")
def compare_regress(
    update_baseline: bool = typer.Option(
        False, "--update-baseline",
        help="corpus/baselines/compare_pairs.json 재작성 (비교 생략)",
    ),
    init_manifest: bool = typer.Option(
        False, "--init-manifest",
        help="corpus/oem/compare_pairs.json 기본 pair manifest 생성 후 종료",
    ),
    json_out: Path = typer.Option(
        None, "--json-out", help="(선택) sweep 결과 + 비교 JSON 출력 경로"
    ),
    timeout_s: int = typer.Option(
        None, "--timeout-s",
        help="pair 당 subprocess 타임아웃 (기본 600s)",
    ),
):
    """COMPARE-pairs regression gate — compare_parts 를 baseline 과 비교.

    corpus/oem/compare_pairs.json 의 각 pair 를 격리된 subprocess 에서
    compare_parts 로 비교, classification / similarity / rmsd 를 기록.
    스케일 변형 pair 는 OCCT BRepBuilderAPI_Transform 으로 즉석 생성하므로
    큰 STEP 을 commit 하지 않아도 재현 가능. 소스 corpus 파일이 없는 pair 는
    skip (requires_oem-style).

    exit 0 = baseline 대비 회귀 없음
    exit 1 = coarse-family flip / 신규 error / similarity 0.05 초과 하락 /
             rmsd tol 초과 상승
    exit 2 = manifest / baseline 없음
    """
    import json

    from phone_designer.corpus import compare_regress as cr

    if init_manifest:
        path = cr.write_default_manifest()
        typer.echo(f">>> manifest written: {path}")
        raise typer.Exit(code=0)

    eff_timeout = timeout_s or cr.PER_PAIR_TIMEOUT_S
    try:
        records = cr.run_sweep(timeout_s=eff_timeout, log=typer.echo)
    except FileNotFoundError as exc:
        typer.echo(f"[error] {exc}", err=True)
        raise typer.Exit(code=2)

    payload: dict[str, Any] = {"records": records}

    if update_baseline:
        path = cr.save_baseline(records)
        typer.echo(f">>> baseline written: {path}")
        if json_out is not None:
            json_out.parent.mkdir(parents=True, exist_ok=True)
            json_out.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            typer.echo(f">>> json: {json_out}")
        raise typer.Exit(code=0)

    baseline = cr.load_baseline()
    if baseline is None:
        typer.echo(
            f"[error] no baseline at {cr.baseline_path()} — run with "
            f"--update-baseline first",
            err=True,
        )
        raise typer.Exit(code=2)

    cmp = cr.compare_to_baseline(records, baseline)
    payload["comparison"] = cmp
    for r in cmp["regressions"]:
        typer.echo(f"REGRESSION {r['id']}: {r['reason']}", err=True)
    for p in cmp["new_pairs"]:
        typer.echo(f"info: new pair (not in baseline): {p}")
    for p in cmp["skipped"]:
        typer.echo(f"info: skipped (source absent): {p}")
    for p in cmp["missing_pairs"]:
        typer.echo(f"info: in baseline but not run: {p}")
    typer.echo(
        f">>> {len(records)} pair(s): {len(cmp['regressions'])} regression(s)"
    )

    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        typer.echo(f">>> json: {json_out}")

    raise typer.Exit(code=1 if cmp["regressions"] else 0)


@app.command()
def version():
    """버전 출력."""
    from phone_designer import __version__
    typer.echo(__version__)


if __name__ == "__main__":
    app()
