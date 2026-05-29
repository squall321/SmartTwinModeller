"""Top-level CLI (typer). Phase 0 stub — 본격 구현은 Phase 0-1 작업.

현 단계는 setup.ps1 의 안내 명령이 import 에러 없이 실행되어 다음 단계가 명확히
보이도록 하는 골격만 제공.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

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
):
    """Plan 실행 → STEP export."""
    from phone_designer.plan.executor import ExecutionMode, PlanExecutor
    from phone_designer.plan.yaml_io import load_plan, save_plan

    loaded = load_plan(plan)
    typer.echo(f">>> plan: {loaded.plan_name} ({len(loaded.steps)} steps)")

    mode_enum = ExecutionMode.STRICT if mode == "strict" else ExecutionMode.LOOSE
    result = PlanExecutor(loaded, mode=mode_enum).run()

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


@app.command()
def version():
    """버전 출력."""
    from phone_designer import __version__
    typer.echo(__version__)


if __name__ == "__main__":
    app()
