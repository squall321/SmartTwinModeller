export const meta = {
  name: 'p1-wave-light',
  description: 'P1 skill expansion in light waves: 2-3 heavy agents per parallel() call, sequenced',
  phases: [
    { title: 'Wave1', detail: 'Helmholtz + acoustic finishing (3 agents)' },
    { title: 'Wave2', detail: 'LLM control-plane infra (3 agents)' },
    { title: 'Wave3', detail: 'Surface-aesthetics tools (3 agents)' },
    { title: 'Reconcile', detail: '1 agent — register + pytest + report' },
  ],
}

const TOP = `Project d:/SmartTwinModeller. Python venv\\Scripts\\python.exe.
Catalog loader inline (no shared module):
  def _load(family, name):
      import yaml, pathlib
      root = pathlib.Path(__file__).resolve().parents[5]
      return yaml.safe_load((root / "catalogs" / family / f"{name}.yaml").read_text())
Template: modify_pocket/hole.py, modify_pocket/extrude_pocket_blended.py.
DO NOT touch shared infra (_spec/_registry/_post_conditions/_resolvers/_selectors/_sketch/_sketch_to_solid/_history/_tag_propagation/executor.py).
DO NOT edit export_manifest.py — return import_lines.
DO NOT run pytest in your pack — the Reconcile stage at end runs the full suite ONCE.

⚠ MUST CALL StructuredOutput at end. Partial results OK. Not calling = total failure.`

const PACK = {
  type: 'object', additionalProperties: false,
  required: ['skills', 'import_lines'],
  properties: {
    skills: { type: 'array', items: { type: 'string' } },
    import_lines: { type: 'array', items: { type: 'string' } },
    catalogs: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
}

phase('Wave1')
const wave1 = await parallel([
  () => agent(`${TOP}
PACK Wave1-A: speaker grille curved-array.
1. src/phone_designer/skills/modify_pattern/speaker_grille_curved_array.py — args(face_selector, center_xy, outer_d_mm, inner_d_mm, ring_count:int=3, hole_d_mm=0.8, holes_per_ring:int=24). Concentric ring grille. post volume_decreased.
2. tests/skills/test_speaker_grille.py (1 test).
3. CALL StructuredOutput.`, { label: 'w1:speaker-grille', schema: PACK, phase: 'Wave1' }),

  () => agent(`${TOP}
PACK Wave1-B: foam seal racetrack groove (if not already done).
1. Check if src/phone_designer/skills/modify_pocket/foam_seal_groove_racetrack.py exists. If yes, skip and return empty.
2. If no, create with args(face_selector, length_mm, width_mm, corner_r_mm, groove_w_mm, groove_d_mm). Build racetrack path inline (2 lines + 2 semicircles), make closed wire, prism-extrude inward. post volume_decreased.
3. tests/skills/test_foam_racetrack.py.
4. CALL StructuredOutput.`, { label: 'w1:foam-seal', schema: PACK, phase: 'Wave1' }),

  () => agent(`${TOP}
PACK Wave1-C: pencil/stylus tip clearance + button cap rounded actuation.
1. src/phone_designer/skills/modify_pocket/stylus_tip_clearance.py — args(face_selector, position_xy, tip_d_mm=2.5, throat_d_mm=4.0, depth_mm=8.0). Stepped pocket: throat wider near opening tapering to tip. post volume_decreased.
2. src/phone_designer/skills/modify_boss/button_cap_rounded_actuation.py — args(face_selector, position_xy, base_d_mm=6.0, dome_h_mm=1.0). Cylindrical post + spherical cap. post volume_increased.
3. tests/skills/test_stylus_button.py.
4. CALL StructuredOutput.`, { label: 'w1:stylus-button', schema: PACK, phase: 'Wave1' }),
])

phase('Wave2')
const wave2 = await parallel([
  () => agent(`${TOP}
PACK Wave2-A: dry_run / what-if skill for LLM workflow.
1. src/phone_designer/skills/inspect/dry_run_skill.py — args(target_skill_name:str, target_args:dict). Without executing the target skill, attempt to: (a) resolve any face/edge selectors in target_args using the current body and return matched_count; (b) load the target skill's @skill metadata (post_conditions, history_rules, expected_volume_sign); (c) predict whether the body would change (volume_increased/decreased/unchanged) and which faces would be involved. Body unchanged. extras["dry_run"]={"matched_selectors":[...], "predicted_volume_change":..., "predicted_face_change":..., "preconditions_passed":[]}. post body_present.
2. tests/skills/test_dry_run.py — verify a dry_run of extrude_pocket reports volume_decreased prediction without actually pocketing.
3. CALL StructuredOutput.`, { label: 'w2:dry-run', schema: PACK, phase: 'Wave2' }),

  () => agent(`${TOP}
PACK Wave2-B: selector_robustness_score + tag_inventory.
1. src/phone_designer/skills/inspect/selector_robustness_score.py — args(selector_dict:dict). Resolve the selector against the current body, return a 0-1 score based on: how many entities matched (1 = ideal, 0 / >1 = bad), and how stable under small perturbations (planned for v2). extras["selector_score"]={"score":..., "matched":..., "alternatives_suggested":[]}. Body unchanged. post body_present.
2. src/phone_designer/skills/inspect/tag_inventory.py — args(). List ALL tags currently attached to the body, with the history of each (when set, what skill set it, what surface_type). extras["tags"]=[{tag:str, set_by_skill:str, history:[...], current_resolution:{matched:N, face_centers:[...]}}]. Body unchanged. post body_present.
3. tests/skills/test_llm_meta.py.
4. CALL StructuredOutput.`, { label: 'w2:llm-meta', schema: PACK, phase: 'Wave2' }),

  () => agent(`${TOP}
PACK Wave2-C: find_skill_by_intent + skill_schema_introspect.
1. src/phone_designer/skills/inspect/find_skill_by_intent.py — args(natural_language_query:str, top_k:int=5). Search the registry's skill summaries (and category) for the closest matches via simple keyword overlap + bigram match (no embeddings for v1). Body unchanged. extras["suggestions"]=[{skill_name, summary, score, category}]. post body_present.
2. src/phone_designer/skills/inspect/skill_schema_introspect.py — args(skill_name:str). Look up skill in registry, return its Pydantic Args JSON Schema + post_conditions kinds + manufacturing constraints. Body unchanged. extras["schema"]={"args":..., "post_conditions":..., "manufacturing":..., "summary":...}. post body_present.
3. tests/skills/test_llm_search.py.
4. CALL StructuredOutput.`, { label: 'w2:llm-search', schema: PACK, phase: 'Wave2' }),
])

phase('Wave3')
const wave3 = await parallel([
  () => agent(`${TOP}
PACK Wave3-A: highlight-line / zebra view + Class-A continuity audit.
1. src/phone_designer/skills/inspect/highlight_line_view.py — args(view_direction:tuple=(0,0,1), light_count:int=8). For each face, sample N points, compute reflection vector under parallel light from view_direction, find where reflected ray angle changes sharply between adjacent samples → highlight line. extras["highlight_lines"]=[{face_idx, points:[...]}]. Body unchanged. post body_present.
2. src/phone_designer/skills/inspect/zebra_stripe_view.py — args(stripe_count:int=20, view_direction). Sample faces, project virtual stripes, find places where stripes don't align G1 across edges. extras["g1_discontinuities"]=[{edge_idx, severity}]. post body_present.
3. src/phone_designer/skills/inspect/continuity_audit.py — args(min_continuity:Literal["G0","G1","G2"]="G1"). Check every shared edge between faces for the specified continuity. extras["continuity_failures"]=[...]. post body_present.
4. tests/skills/test_class_a_audit.py.
5. CALL StructuredOutput.`, { label: 'w3:class-a-audit', schema: PACK, phase: 'Wave3' }),

  () => agent(`${TOP}
PACK Wave3-B: CNC reachability + min tool radius enforcement.
1. src/phone_designer/skills/inspect/enforce_min_tool_radius.py — args(min_radius_mm:float). For each concave internal edge, check if its fillet radius >= min_radius_mm. Auto-fillet edges below threshold OR report violations. post face_count_changed (allow_no_change=True).
2. src/phone_designer/skills/inspect/tool_reachability.py — args(tool_diameter_mm:float, max_aspect_ratio:float=4.0, setup_direction:tuple=(0,0,1)). For each pocket, check depth/diameter <= max_aspect_ratio AND clear approach from setup_direction. extras["unreachable_features"]=[{location, reason}]. Body unchanged. post body_present.
3. src/phone_designer/skills/inspect/pocket_aspect_ratio_check.py — args(max_aspect_ratio:float=4.0). For each detected pocket, compute depth/min_dimension. extras["pocket_aspect_violations"]=[{pocket_id, ratio, recommendation}]. Body unchanged. post body_present.
4. tests/skills/test_cnc_dfm.py.
5. CALL StructuredOutput.`, { label: 'w3:cnc-dfm', schema: PACK, phase: 'Wave3' }),

  () => agent(`${TOP}
PACK Wave3-C: helical involute gear + worm thread.
1. src/phone_designer/skills/create/gear_helical_involute.py — args(module_mm:float=1.0, teeth:int=20, helix_angle_deg:float=15.0, face_width_mm:float=10.0, pressure_angle_deg:float=20.0). Build helical involute spur gear via tooth profile extrusion with helical twist. post body_present.
2. src/phone_designer/skills/create/worm_thread.py — args(module_mm:float=1.0, lead_angle_deg:float=5.0, pitch_d_mm:float=20.0, length_mm:float=30.0). Helical thread on cylindrical shaft. post body_present.
3. tests/skills/test_gear_worm.py.
4. CALL StructuredOutput.`, { label: 'w3:gear-worm', schema: PACK, phase: 'Wave3' }),
])

const allLines = [...wave1, ...wave2, ...wave3].filter(Boolean).flatMap(p => p.import_lines || [])

phase('Reconcile')

const REC = {
  type: 'object', additionalProperties: false,
  required: ['total_skills', 'tests_passing', 'summary'],
  properties: {
    total_skills: { type: 'integer' },
    tests_passing: { type: 'integer' },
    tests_failing: { type: 'integer' },
    per_category: { type: 'object', additionalProperties: { type: 'integer' } },
    summary: { type: 'string' },
  },
}

const rec = await agent(`Reconcile.

1. Add these import lines to d:/SmartTwinModeller/src/phone_designer/skills/export_manifest.py (idempotent):
${allLines.map(l => '  ' + l).join('\n')}

2. Also survey skills/*/*.py vs export_manifest, register stragglers.

3. Run pytest ONCE: $env:PYTHONPATH = "src"; & "venv\\Scripts\\python.exe" -m pytest -q 2>&1 | Select-Object -Last 10

4. If failures, list first 6 likely_module + simple action recommendation in notes (don't fix here — just report).

5. Export manifest: $env:PYTHONPATH = "src"; & "venv\\Scripts\\python.exe" -m phone_designer.skills.export_manifest --out manifest.json

6. Read manifest.json → total + per-category. ≤100-word summary.

7. CALL StructuredOutput.`,
  { label: 'reconcile', schema: REC, phase: 'Reconcile' })

return { ...rec }
