export const meta = {
  name: 'synthetic-round-trip',
  description: 'Validate reverse-engineering pipeline by round-tripping our own synthetic STEP outputs',
  phases: [
    { title: 'Trip', detail: '2 parallel agents — each runs RE round-trip on a subset of STEP files' },
    { title: 'Report', detail: 'Aggregate metrics + write summary doc' },
  ],
}

const TOP = `Project d:/SmartTwinModeller. Python venv\\Scripts\\python.exe.
$env:PYTHONPATH = "src"

Round-trip protocol for each STEP file:
  1. Read original: $env:PYTHONPATH = "src"; & "venv\\Scripts\\python.exe" -c "<inline script>"
     The inline script:
       - imports phone_designer.skills.create.import_step
       - calls ImportStep on the file, gets body
       - records original_volume + bbox + face_count via inspect_geometry
       - calls reverse_engineer.extract_feature_catalog on body → feature catalog dict
       - calls reverse_engineer.plan_from_feature_catalog with the catalog → Plan YAML at /tmp/regen_plan.yaml
       - loads the Plan, executes via PlanExecutor → regenerated body
       - exports regen body to /tmp/<basename>_regen.step
       - records regen_volume + bbox + face_count
       - prints JSON: {"file": "...", "ok": true/false, "original_volume": ..., "regen_volume": ..., "volume_diff_pct": ..., "original_face_count": ..., "regen_face_count": ..., "plan_step_count": ..., "error": "..."}
  2. Capture JSON output, parse, append to results.

⚠ MUST CALL StructuredOutput at end.`

const TRIP_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['files_attempted', 'files_ok', 'results'],
  properties: {
    files_attempted: { type: 'integer' },
    files_ok: { type: 'integer' },
    results: {
      type: 'array',
      items: {
        type: 'object',
        required: ['file', 'ok'],
        properties: {
          file: { type: 'string' },
          ok: { type: 'boolean' },
          original_volume: { type: 'number' },
          regen_volume: { type: 'number' },
          volume_diff_pct: { type: 'number' },
          original_face_count: { type: 'integer' },
          regen_face_count: { type: 'integer' },
          plan_step_count: { type: 'integer' },
          error: { type: 'string' },
        },
      },
    },
  },
}

const FILES_A = [
  'fixtures/simple_watch.step',
  'fixtures/simple_watch_housing_only.step',
  'run_logs/_tmp/demo_advanced.step',
]

const FILES_B = [
  'run_logs/_tmp/demo_sweep_revolve.step',
  'run_logs/_tmp/demo_boss_sweep_loft.step',
  'run_logs/_tmp/demo_patterns_2_circular.step',
  'run_logs/_tmp/auto_repro.step',
]

phase('Trip')
const trips = await parallel([
  () => agent(`${TOP}

Files to round-trip:
${FILES_A.join('\n')}

For each file, follow the round-trip protocol. Write the per-file Python harness ONCE (e.g., as run_logs/_tmp/round_trip_harness.py) — accept file path as argv[1] — then invoke it per file.

The harness should be robust:
- import: try via skills.create.import_step (or directly OCP STEPControl_Reader if skill API too rigid)
- if extract_feature_catalog fails, record error + skip rest
- if plan_from_feature_catalog fails, record error
- if PlanExecutor fails, record error + still report what was extracted
- always print one JSON line per file

Aggregate the JSON outputs into a list, return via StructuredOutput.`,
    { label: 'trip:set-A', schema: TRIP_SCHEMA, phase: 'Trip' }),

  () => agent(`${TOP}

Files to round-trip:
${FILES_B.join('\n')}

Same protocol as set A. You may reuse run_logs/_tmp/round_trip_harness.py if set A wrote it; if not, write your own.

Aggregate JSON outputs, return via StructuredOutput.`,
    { label: 'trip:set-B', schema: TRIP_SCHEMA, phase: 'Trip' }),
])

const allResults = trips.filter(Boolean).flatMap(t => t.results || [])
const okCount = allResults.filter(r => r.ok).length

phase('Report')

const RPT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['files_total', 'files_ok', 'summary'],
  properties: {
    files_total: { type: 'integer' },
    files_ok: { type: 'integer' },
    avg_volume_diff_pct: { type: 'number' },
    avg_face_count_ratio: { type: 'number' },
    failure_modes: { type: 'array', items: { type: 'string' } },
    report_path: { type: 'string' },
    summary: { type: 'string' },
  },
}

const rpt = await agent(`Synthesize round-trip results.

Per-file results (${allResults.length} files):
${JSON.stringify(allResults, null, 2)}

Tasks:
1. Compute averages:
   - avg_volume_diff_pct over files where ok=true
   - avg_face_count_ratio = mean(regen_face_count / original_face_count)
2. Identify failure modes: group .error strings, summarize most common (e.g., "extract_feature_catalog hung", "plan_from_feature_catalog generated empty steps", "executor freeze mismatch"). List up to 8 distinct failure modes.
3. Write a comprehensive report to docs/round_trip_validation.md with:
   - Headline (X/Y files round-tripped successfully)
   - Per-file table (file, original_V, regen_V, %diff, original_F, regen_F, plan_steps, status)
   - Failure modes breakdown
   - Recommendations: which RE skill or infra component is the weakest link
4. Set report_path.
5. ≤150 word summary.
6. CALL StructuredOutput.`,
  { label: 'report', schema: RPT_SCHEMA, phase: 'Report' })

return { ...rpt }
