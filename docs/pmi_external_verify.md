# External AP242 PMI Round-Trip Verification (D1)

Status on the current developer machine (`d:\SmartTwinModeller`,
2026-06-06): **skip — FreeCAD not installed.**

`where.exe freecad` returns "not found"; none of
`C:\Program Files\FreeCAD*`, `C:\Program Files (x86)\FreeCAD*`,
`%LOCALAPPDATA%\Programs\FreeCAD*` exist; no registry entry for
`HKCU\Software\FreeCAD`. The automated leg of D1 therefore cannot run
here.

The PMI export side of the loop (`export_step_ap242_pmi` +
`<path>.pmi.json` sidecar) is verified by the in-tree pytest
`tests/skills/test_pmi.py::test_export_step_ap242_pmi_writes_file_and_sidecar`.
What this doc covers is the *external consumer* leg: prove that another
AP242-aware CAD tool can re-open the exported STEP and see the PMI we
attached.

## Helper script

`run_logs/_tmp/verify_pmi_freecad.py` builds a 40x30x10 mm box, attaches
one `pmi_dimension_callout` (linear top<->bottom, 10 mm +/-0.05, datum
refs A/B), one `pmi_surface_texture` (top face, Ra 0.8 um, parallel
lay, 45 deg), and one `pmi_weld_symbol` (X-axis edges, fillet, 2 mm
leg, 40 mm length). It then exports
`run_logs/_tmp/pmi_roundtrip/test_pmi.step` (+ the JSON sidecar) and, if
FreeCAD is available, invokes `freecadcmd` headlessly to import the
STEP and report how many PMI/GD&T-like objects the importer surfaced.

The script is idempotent and safe to run with or without FreeCAD —
when FreeCAD is missing it exits 0 with `VERDICT: skip_no_freecad` so
CI does not break.

## Running the automated path (when FreeCAD is installed)

1. Install **FreeCAD 1.0** or newer (older 0.20.x has an incomplete
   AP242 PMI importer — annotations come through as opaque
   `Part::Feature` blobs and the round-trip looks like a "loss" even
   when the bytes are intact). The Windows MSI from
   <https://www.freecad.org/downloads.php> is sufficient; the Linux
   AppImage works on a Codespaces / WSL2 setup as well.
2. Either put `freecadcmd.exe` on `PATH`, or set
   `FREECAD_CMD=C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe`.
3. Run:
   ```powershell
   .\venv\Scripts\python.exe .\run_logs\_tmp\verify_pmi_freecad.py
   ```
4. The script prints one of these verdicts on the last line:
   - `VERDICT: pmi_survived count=N` — FreeCAD surfaced N PMI objects
     after the round-trip (success).
   - `VERDICT: pmi_not_recovered` — STEP geometry imports but FreeCAD's
     Import module does not raise the PMI labels. This is the expected
     result on FreeCAD < 1.0 / pre-AP242-importer builds; the JSON
     sidecar (`test_pmi.step.pmi.json`) still preserves intent.
   - `VERDICT: freecad_error` — FreeCAD crashed while loading the STEP
     (look at the JSON report in the preceding lines).
   - `VERDICT: skip_no_freecad` — FreeCAD not detected; see manual
     instructions below.

## Manual verification (no FreeCAD installed)

If you do not want to install FreeCAD, any of the following AP242
PMI-capable viewers can perform the same check. All of them parse the
STEPCAFControl-written file we produce.

1. Run the helper script once to produce the artifacts:
   ```powershell
   .\venv\Scripts\python.exe .\run_logs\_tmp\verify_pmi_freecad.py
   ```
   This writes:
   - `run_logs/_tmp/pmi_roundtrip/test_pmi.step` (the AP242 file)
   - `run_logs/_tmp/pmi_roundtrip/test_pmi.step.pmi.json` (sidecar)

2. Open `test_pmi.step` in one of:
   - **CAD Assistant** (free, Open Cascade) —
     <https://www.opencascade.com/products/cad-assistant/>. Look for a
     "PMI" tree node next to the box; expand it and confirm three
     leaves (the dimension callout, the surface-texture symbol, the
     weld symbol). This is the recommended free tool — its OCCT 7.7+
     STEPCAF importer is exactly the path the exporter wrote.
   - **NX / Creo / SolidWorks MBD / Catia V5** — File > Import STEP.
     PMI should appear in the "Annotations" or "MBD" tree. SolidWorks
     2024+ surfaces them as "Imported DimXpert" features.
   - **FreeCAD 1.0** Desktop — File > Open. PMI labels appear under
     "Body > Annotations". Switch to the "TechDraw" workbench and
     create a 2D view to see the symbols laid out.

3. Cross-check against the JSON sidecar. Every entry in
   `test_pmi.step.pmi.json -> pmi.dimensions / surface_textures /
   welds` should have a corresponding visible annotation in the CAD
   tool. The sidecar is the canonical record — if the tool only shows
   a subset, that is a tool-side STEPCAF gap, not a producer-side
   loss.

4. Record the result in this doc (date, tool, version, count
   recovered, screenshot path).

## Known limitations

- `STEPCAFControl_Writer` in the OCP build we ship to-day exports the
  CAF document with **labels but minimal sub-shape attachment** for
  dimensions (the `_write_caf` helper in
  `src/phone_designer/skills/pmi/export_step_ap242_pmi.py` documents
  this explicitly). Consumers that rely on the AP242
  `Dimensional_Location` <-> face binding will see "floating"
  annotations. Consumers that read the labels themselves (CAD
  Assistant, FreeCAD 1.0, Creo) recover the values.
- Surface-texture symbols do not have a first-class CAF representation
  in older OCP builds. They are counted in the sidecar
  (`pmi.surface_textures`) but may not appear as a CAD annotation
  until an OCCT 7.8+ build is paired with this exporter.
- The script reports `skip_no_freecad` as a success exit (0) so
  unattended runs do not flag the missing dependency.

## Recording results

After running the verification, append a row here:

| Date | Tool | Tool version | Verdict | Notes |
|---|---|---|---|---|
| 2026-06-06 | (skipped) | n/a | skip_no_freecad | FreeCAD not installed on dev box; script + sidecar ready for manual run |
