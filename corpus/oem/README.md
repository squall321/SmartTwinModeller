# OEM Corpus

Drop OEM STEP/IGES/BREP files here. The `phone-designer corpus-test` CLI runs the
full RE pipeline (`extract_feature_catalog` → `plan_from_feature_catalog` →
`PlanExecutor`) on each and produces a fidelity report (face count, volume,
drift %, cube-collapse detection).

## Layout

```
corpus/oem/
  README.md          # this file (committed)
  _sample/           # tiny in-repo fixtures used to smoke-test the CLI
    simple_watch.step
  <vendor>/          # your private OEM drops — gitignored
    *.step / *.STEP / *.iges / *.brep
```

`.gitignore` excludes `*.step / *.STEP / *.stp` directly under
`corpus/oem/` so confidential OEM geometry is **never** committed. The
`_sample/` directory is whitelisted because its contents are public repo
fixtures.

## Run

```powershell
.\venv\Scripts\Activate.ps1
phone-designer corpus-test                                    # default dir / report path
phone-designer corpus-test --dir corpus\oem\acme `
                            --report-out docs\acme_report.md `
                            --tolerance-pct 25.0
```

Exit code `0` if every file regenerates within `--tolerance-pct` of its
original volume; `1` otherwise. The Markdown report lists per-file
`original_vol`, `regen_vol`, `drift_pct`, `face_count`, status, and any
exception message captured during the pipeline.
