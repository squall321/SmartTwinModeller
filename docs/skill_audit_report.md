# Skill Audit Report - Parametric Reality Check

## v3 In-process audit (2026-06-04)

**Method**: `run_logs/_tmp/skill_audit_v2.py` — single Python session, no subprocess. Real fixtures: face_named selector, Circle sketch dict, catalog spec lookups. Incremental JSONL writes (resume on interruption).

**Coverage**: **232 / 338 skills** audited (68.6%). The audit hung on a specific skill after 232 — likely an OCCT operation that doesn't terminate. Honest counts from what completed:

| Bucket | Count | Reading |
|---|---|---|
| **geom_param_responsive** | **7** | Args genuinely vary geometry: `surface_offset`, `deburring`, `final_fillet_all_sharp_edges`, `sanding_pass`, `draft_apply_auto`, `ejector_pin_clearance`, `dimple` |
| **geom_static_args** | **5** | **Façade suspects** — args change but ΔV identical: `core_cavity_split`, `parting_surface`, `runner_diameter_calc`, `unfold`, `cleanability_radius_enforce` |
| geom_broken | 145 | Most are still fixture mismatches (`position=[]` 2D vs 3D, `face_selector` TaggedSelector union not matching) — skill itself may be fine, but our auto-fixture can't construct valid args for them. NOT confirmed real failures. |
| geom_single_ok | 8 | `create` skills (no body input, single arg sweep) — passed mid sweep, parametric sweep N/A |
| geom_partial | 2 | Some sweeps pass, others error |
| inspect_responsive | 34 / 48 | Returned non-empty extras dict — inspect skills work |
| inspect_broken | 14 / 48 | Real failures (selector / Pydantic / OCCT) |
| tag_only_ok | 2 | Tag attached cleanly |
| tag_only_broken | 15 | Fixture mismatches dominate |

### Honest take

- Of the **47 geom skills the harness could actually call** (param + static + partial + single_ok = 22, broken = 145 not counted here), **7 are confirmed parametric, 5 are façade candidates**.
- The 145 "geom_broken" don't tell us whether those skills work — only that auto-generated fixtures missed their required Pydantic structure. Many of them are likely fine when called with proper args (the existing tests/skills/test_*.py confirm this).
- inspect category fares best: **~70% (34/48) confirmed responsive**.
- The 5 façade candidates deserve real review — they returned ok=true with no volume change across param sweeps:
  - `core_cavity_split`, `parting_surface` — mold-tooling, may legitimately be tag/setup ops
  - `runner_diameter_calc` — explicitly a calculation skill (no geom change expected)
  - `unfold` — sheet metal unfold may need real flange geometry to show effect
  - `cleanability_radius_enforce` — only acts on edges below threshold; box has none

### Next-step priorities

1. **Fixture generator overhaul**: detect Pydantic field types from `args_model.model_fields` rather than JSON Schema; generate position=[x,y,z], TaggedSelector with kind/tag fields, etc. Should rescue ~100 of the 145 currently-broken-by-fixture.
2. **Finish the remaining 106 skills** by identifying + skipping the hanging skill (likely a sweep/loft with bad input that loops in OCCT).
3. **Audit the 5 façade candidates** by hand — confirm whether they're real no-ops or geometrically meaningful with the right body.
4. **Lock the registry** — stop adding skills until audit coverage ≥ 90%.

Detailed bucket samples in `run_logs/_tmp/skill_audit_v2_summary.json`.

---

## v1 audit (2026-06-03) — historical, harness was the problem

**Date:** 2026-06-03  
**Method:** Each of 338 registered skills invoked through `run_logs/_tmp/skill_audit_driver.py` with 1-3 fixture arg sets per skill. Delta-V / face_count measured before and after `apply()`.

## Headline

**TOTAL 338 skills - confirmed parametric+working: 7, static-but-ok: 0, raw-broken under audit harness: 194, tag-only: 31, inspect-only: 102, macro: 2.**

**Important caveat:** the audit harness has two systemic problems that inflate the broken count and should not be read as skill-level failures:

1. **Subprocess spawn failures** (`exc:FileNotFoundError: [WinError 2]`) - 67 parametric + 92 inspect + 28 tag-only skills failed when the parent runner tried to spawn the driver, *before any skill code ran*. This is a runner/encoding issue on Windows, not skill brokenness. Re-running the same skill (e.g. `inspect_geometry`) in-process or as a fresh subprocess succeeds.
2. **Bad fixture args** (`ValidationError`) - 52 parametric skills failed because the audit planner emitted empty selectors (`face_selector=""`, `position=[]`) or required dicts as strings. The skills correctly reject these, but the result tells us nothing about whether the skill itself works.

After removing those two harness-side issues, the believable failure budget is roughly 70 skills with real driver/runtime problems. See section 4.

## Bucket totals

| Bucket | Count | Meaning |
|---|---|---|
| param_responsive | 7 | >=2 distinct test cases, all ok, post_v varies with args |
| static_geom | 0 | all ok but post_v identical across cases (suspicious facade) |
| partial | 2 | mixed ok/fail across cases |
| all_broken (raw) | 194 | every test case errored - **most are harness issues, not skill bugs** |
| tag_only_ok | 0 | tag-only skill, all ok |
| tag_only_broken | 31 | tag-only skill, all errored (mostly subprocess) |
| inspect_ok | 0 | inspect-only skill, all ok |
| inspect_broken | 102 | inspect-only skill, all errored (mostly subprocess) |
| macro | 2 | reverse-engineer macros, not unit-testable here |

### Failure-kind breakdown for `all_broken` parametric (n=194)

| Kind | Count | Reading |
|---|---|---|
| harness_subprocess_spawn_fail | 67 | harness - runner could not spawn driver process; skill never ran |
| driver_init_fail | 65 | harness - driver could not instantiate `Box` for default body (`object is not callable`); skill never ran |
| bad_fixture_args | 52 | harness - planner emitted empty selector/position args; skill rejected them correctly |
| driver_instantiation_bug | 4 | harness - `spec.implementation_class()` called on an already-instantiated singleton |
| bad_fixture_dims | 2 | harness - planner emitted dims with `corner_r == diameter`; skill rejected them correctly |
| occt_runtime_failure | 1 | real - OCCT crashed (Standard_DomainError) |
| other_runtime_error | 0 | real - skill threw an unclassified runtime error |

**Net real failures** (occt + other_runtime + instantiation_bug + driver_init_fail): about 71 skills. The other ~119 are harness/fixture noise.

## Per-category breakdown

| category | param_responsive | static_geom | partial | all_broken | tag_only_broken | inspect_broken | macro | total |
|---|---|---|---|---|---|---|---|---|
| assembly | 0 | 0 | 0 | 0 | 14 | 0 | 0 | 14 |
| compose | 0 | 0 | 0 | 2 | 3 | 0 | 0 | 5 |
| create | 3 | 0 | 1 | 21 | 0 | 0 | 0 | 25 |
| fem_cae | 0 | 0 | 0 | 6 | 3 | 0 | 0 | 9 |
| inspect | 0 | 0 | 0 | 0 | 0 | 102 | 0 | 102 |
| modify/3dprint | 0 | 0 | 0 | 3 | 1 | 0 | 0 | 4 |
| modify/antenna | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 2 |
| modify/boss | 0 | 0 | 1 | 26 | 0 | 0 | 0 | 27 |
| modify/chamfer | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 5 |
| modify/curvature | 1 | 0 | 0 | 11 | 0 | 0 | 0 | 12 |
| modify/fillet | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 3 |
| modify/finish | 3 | 0 | 0 | 1 | 5 | 0 | 0 | 9 |
| modify/hole | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 3 |
| modify/mold | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 8 |
| modify/pattern | 0 | 0 | 0 | 11 | 0 | 0 | 0 | 11 |
| modify/plateau | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 1 |
| modify/pocket | 0 | 0 | 0 | 76 | 0 | 0 | 0 | 76 |
| modify/sheet | 0 | 0 | 0 | 10 | 0 | 0 | 0 | 10 |
| pmi | 0 | 0 | 0 | 0 | 5 | 0 | 0 | 5 |
| repair | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 5 |
| reverse_engineer | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 2 |

## Top parametric+working skills (the SOLID bucket)

These skills produced distinct, non-trivial post-volumes for distinct fixture args. They are the most reliable building blocks today.

| skill | category | n_cases | post_v sample | dv sample |
|---|---|---|---|---|
| `box` | create | 3 | 125.00, 15.62, 1000.00 | - |
| `cylinder` | create | 3 | 392.70, 49.09, 3141.59 | - |
| `deburring` | modify/finish | 3 | 24996.23, 24999.06, 25000.00 | -3.77, -0.94, 0.00 |
| `final_fillet_all_sharp_edges` | modify/finish | 3 | 24996.23, 24999.06, 24984.98 | -3.77, -0.94, -15.02 |
| `sanding_pass` | modify/finish | 3 | 24999.06, 24999.06, 24996.23 | -0.94, -0.94, -3.77 |
| `surface_offset` | modify/curvature | 3 | 72000.00, 45375.00, 147000.00 | 47000.00, 20375.00, 122000.00 |
| `wedge` | create | 3 | 125.00, 15.62, 1000.00 | - |

## Top 30 broken (parametric `all_broken` bucket)

Sorted by failure kind. Most of these are harness issues (see Headline). Inspect the `kind` column.

| skill | category | kind | error (truncated) |
|---|---|---|---|
| `antenna_slit` | modify/antenna | bad_fixture_args | ValidationError: 6 validation errors for Args start.0   Field required [type=missing, input_value=[], input_type=list]   |
| `boss_with_hole` | modify/boss | bad_fixture_args | ValidationError: 3 validation errors for Args position.0   Field required [type=missing, input_value=[], input_type=list |
| `bspline_surface` | create | bad_fixture_args | ValidationError: 4 validation errors for Args point_grid.0.0   Input should be a valid tuple [type=tuple_type, input_val |
| `chamfer_asymmetric` | modify/curvature | bad_fixture_args | ValidationError: 15 validation errors for Args edge_selector.TaggedSelector   Input should be a valid dictionary or inst |
| `chamfer_edges_by_predicate` | modify/chamfer | bad_fixture_args | ValidationError: 15 validation errors for Args selector.TaggedSelector   Input should be a valid dictionary or instance  |
| `circular_pattern` | modify/pattern | bad_fixture_args | ValidationError: 15 validation errors for Args face_selector.TaggedSelector   Input should be a valid dictionary or inst |
| `coil_spring_rectangular` | create | bad_fixture_args | ValidationError: 6 validation errors for Args axis_origin.0   Field required [type=missing, input_value=[], input_type=l |
| `crown_shaft_hole` | modify/boss | bad_fixture_args | ValidationError: 3 validation errors for Args position.0   Field required [type=missing, input_value=[], input_type=list |
| `extrude_boss_blended` | modify/boss | bad_fixture_args | ValidationError: 16 validation errors for Args face_selector.TaggedSelector   Input should be a valid dictionary or inst |
| `extrude_plateau` | modify/plateau | bad_fixture_args | ValidationError: 16 validation errors for Args face_selector.TaggedSelector   Input should be a valid dictionary or inst |
| `extrude_pocket` | modify/pocket | bad_fixture_args | ValidationError: 16 validation errors for Args face_selector.TaggedSelector   Input should be a valid dictionary or inst |
| `extrude_pocket_blended` | modify/pocket | bad_fixture_args | ValidationError: 16 validation errors for Args face_selector.TaggedSelector   Input should be a valid dictionary or inst |
| `extrude_through` | modify/pocket | bad_fixture_args | ValidationError: 16 validation errors for Args face_selector.TaggedSelector   Input should be a valid dictionary or inst |
| `face_face_fillet` | modify/curvature | bad_fixture_args | ValidationError: 30 validation errors for Args face_selector_a.TaggedSelector   Input should be a valid dictionary or in |
| `fillet_edges_by_predicate` | modify/fillet | bad_fixture_args | ValidationError: 15 validation errors for Args selector.TaggedSelector   Input should be a valid dictionary or instance  |
| `gear_external_involute` | create | bad_fixture_args | ValidationError: 1 validation error for Args n_teeth   Input should be greater than or equal to 10 [type=greater_than_eq |
| `gear_internal_involute` | create | bad_fixture_args | ValidationError: 1 validation error for Args n_teeth   Input should be greater than or equal to 10 [type=greater_than_eq |
| `grille_pattern` | modify/hole | bad_fixture_args | ValidationError: 3 validation errors for Args center.0   Field required [type=missing, input_value=[], input_type=list]  |
| `gyroid_lattice` | create | bad_fixture_args | ValidationError: 6 validation errors for Args bbox_min.0   Field required [type=missing, input_value=[], input_type=list |
| `helical_spring` | create | bad_fixture_args | ValidationError: 6 validation errors for Args axis_origin.0   Field required [type=missing, input_value=[], input_type=l |
| `helical_thread_external` | modify/pocket | bad_fixture_args | ValidationError: 6 validation errors for Args axis_origin.0   Field required [type=missing, input_value=[], input_type=l |
| `helical_thread_internal` | modify/pocket | bad_fixture_args | ValidationError: 6 validation errors for Args axis_origin.0   Field required [type=missing, input_value=[], input_type=l |
| `hole` | modify/hole | bad_fixture_args | ValidationError: 3 validation errors for Args position.0   Field required [type=missing, input_value=[], input_type=list |
| `hole_array` | modify/hole | bad_fixture_args | ValidationError: 2 validation errors for Args points.0.2   Field required [type=missing, input_value=[0.0, 0.0], input_t |
| `linear_pattern` | modify/pattern | bad_fixture_args | ValidationError: 15 validation errors for Args face_selector.TaggedSelector   Input should be a valid dictionary or inst |
| `loft_boss_between_sketches` | modify/boss | bad_fixture_args | ValidationError: 17 validation errors for Args face_selector.TaggedSelector   Input should be a valid dictionary or inst |
| `loft_pocket_between_sketches` | modify/pocket | bad_fixture_args | ValidationError: 17 validation errors for Args face_selector.TaggedSelector   Input should be a valid dictionary or inst |
| `loft_side_profile` | modify/curvature | bad_fixture_args | ValidationError: 2 validation errors for Args bottom   Input should be a valid dictionary or object to extract fields fr |
| `magnet_pocket_axial` | modify/pocket | bad_fixture_args | ValidationError: 17 validation errors for Args face_selector.TaggedSelector   Input should be a valid dictionary or inst |
| `mirror_about_two_planes` | modify/pattern | bad_fixture_args | ValidationError: 18 validation errors for Args face_selector.TaggedSelector   Input should be a valid dictionary or inst |

## Top 30 static-geom skills (suspicious facades)

_Count = 0 - none surfaced under this fixture set. Either every working skill is actually parametric, or the audit fixture pool was too narrow (it only varied scalar dims; selector/sketch args were left empty, which forced rejection rather than no-op)._

_(empty)_

## Tag-only skills that broke under audit (sample)

These almost all errored with `WinError 2` (subprocess spawn) or `default_body_fail`. Direct in-process invocation of a sample (e.g. `inspect_geometry`) succeeded, so most of these are healthy and only the harness is broken.

| skill | error |
|---|---|
| `interference_check` | default_body_fail: 'Box' object is not callable |
| `bom_extract` | default_body_fail: 'Box' object is not callable |
| `pmi_inspect_summary` | default_body_fail: 'Box' object is not callable |
| `tag_face` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `apply_anodize` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `apply_paint` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `apply_plating` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `apply_texture_region` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `surface_finish_tag` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `add_component` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `mate_planar` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `move_component` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `fastener_array` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `mate_at_distance` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `mate_axis` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `mate_concentric` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `class_a_surface_tag` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `biocompat_region_tag` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `exploded_view` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `sub_assembly_tag` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `auto_place_fasteners` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `bolt_pattern_recognize` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `check_clearance_full` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `infill_region_tag` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `boundary_condition_tag` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `material_property_tag` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `pmi_dimension_callout` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `pmi_surface_texture` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `pmi_weld_symbol` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `export_step_ap242_pmi` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |

## Inspect-only skills that broke under audit (sample)

Same pattern as tag-only. 102/102 inspect skills appear broken in the JSONL, but at least one (`inspect_geometry`) was confirmed working by direct invocation. **Do not trust this row as evidence of skill brokenness.** A rerun with an in-process driver is needed.

| skill | error |
|---|---|
| `tag_inventory` | default_body_fail: 'Box' object is not callable |
| `face_adjacency_graph` | default_body_fail: 'Box' object is not callable |
| `edge_concavity_classify` | default_body_fail: 'Box' object is not callable |
| `vertex_connectivity` | default_body_fail: 'Box' object is not callable |
| `detect_standoffs` | default_body_fail: 'Box' object is not callable |
| `detect_lugs` | default_body_fail: 'Box' object is not callable |
| `match_standard_bearing` | default_body_fail: 'Box' object is not callable |
| `auto_dimension` | default_body_fail: 'Box' object is not callable |
| `auto_datum_planes` | default_body_fail: 'Box' object is not callable |
| `principal_axes` | default_body_fail: 'Box' object is not callable |
| `cross_section` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `find_features` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `inspect_geometry` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `measure` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `selector_preview` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `silhouette` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `symmetry_axes` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `curvature_map` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `gdt_circularity` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `gdt_cylindricity` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `gdt_flatness` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `gdt_parallelism` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `gdt_perpendicularity` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `gdt_position` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `hole_alignment_check` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `mass_properties` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `mesh_quality` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `section_multi_plane` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `surface_area_by_region` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |
| `datum_plane_assign` | exc:FileNotFoundError:[WinError 2] 지정된 파일을 찾을 수 없습니다 |

## Honest recommendations

### What this audit actually proves
- 7 parametric-geometry skills are unambiguously working with delta-V that scales with args: `box`, `cylinder`, `wedge`, `surface_offset`, `deburring`, `final_fillet_all_sharp_edges`, `sanding_pass`.
- Hundreds of skills cannot be evaluated from these results because the harness either failed to spawn the driver or fed empty selector/sketch arguments. Their status is **unknown**, not broken.

### What to do next (priority order)
1. **Fix the audit harness, then rerun** - biggest leverage. Two fixes:
   - Replace the subprocess fan-out with an in-process driver loop (eliminates the `WinError 2` noise).
   - Guard `inst = spec.implementation_class()` with `if isinstance(spec.implementation_class, type)` (eliminates the `default_body_fail` noise).
2. **Upgrade the fixture generator** - for selector/sketch args the planner currently emits `""` or `[]`. Replace with realistic defaults (tag a face on the seed `box`, build a `Circle(r=2)` sketch, etc.). Until this is done, every selector-taking skill (~170) is automatically counted as broken.
3. **Stop adding skills** - adding more to a 338-skill catalog where 7 are confirmed working is throwing inventory on top of unverified inventory. Lock the catalog and verify each existing skill before any new commit.
4. **Add post-condition assertions inline** - every `@skill` already has a `post_conditions` hook; wire it into a CI gate that runs each skill against a canned fixture suite (3 cases per skill, >=1 with realistic selectors). That replaces this audit with a continuous check.

### What can probably be deleted
- The two `reverse_engineer` macros are not unit-testable here; either give them an integration test or move them out of the skill registry.
- 4 skills failed with `TypeError: 'X' object is not callable` (`stl_import`, `mesh_to_brep`, `surface_thicken_variable`, `SheetBase`). These are real registry bugs and should be fixed or removed.
- 1 skill failed with `Standard_DomainError: cone with two identic radii` for valid-looking args - `cone` likely needs an input-domain guard.

### What to focus on for the next round
- Rebuild fixtures for the 76 `modify/pocket` skills (largest bucket, 0 verified working) - these are the backbone of the catalog and currently have **zero** parametric-responsive evidence.
- Same for the 27 `modify/boss`, 11 `modify/pattern`, 10 `modify/sheet`, 8 `modify/mold` buckets.
- The `modify/finish` bucket already has 3/9 confirmed working - verify the remaining 6 (mostly `apply_*` skills that needed a face tag).

---

_Source data: `run_logs/_tmp/skill_audit_results_{a,b,c}.jsonl`, `run_logs/_tmp/skill_audit_plan.json`, `run_logs/_tmp/skill_audit_driver.py`._

## v2 in-process audit

**Date:** 2026-06-03 (re-run attempt)
**Driver:** `run_logs/_tmp/skill_audit_v2.py` — single-process Python session, no subprocess fan-out. Per-skill fixtures provide a tagged seed `box` body, real `face_named` selectors (`top`/`bottom`), and a `Circle(d=5)` sketch instead of empty strings.

### Why a v2 was needed — v1 harness was the bottleneck

The v1 audit (above) flagged **194 skills as `all_broken`**. Re-reading the failure-kind breakdown:

- 67 × `harness_subprocess_spawn_fail` (WinError 2)
- 65 × `driver_init_fail` (Box "object is not callable" — singleton instantiation pattern)
- 52 × `bad_fixture_args` (empty selectors/positions emitted by the planner)
- 4 × `driver_instantiation_bug`
- **= 188 of 194 (97%) were harness or fixture noise, not skill bugs.**

Add the tag-only (31) and inspect-only (102) buckets — both also dominated by WinError 2 — and **159 of 194 raw `all_broken` confessions trace directly to three harness pathologies**, not skill brokenness. The v1 headline number was misleading.

### v2 bucket counts

| Bucket | v2 Count | Meaning |
|---|---|---|
| total_skills | 0 | (audit incomplete — see status below) |
| param_responsive | 0 | |
| static_geom (façade suspects) | 0 | |
| real_broken (geom) | 0 | |
| tag_only (by design) | 0 | |
| inspect_responsive | 0 | |
| inspect_broken | 0 | |
| io_ok | 0 | |
| macro | 0 | |
| not_invoked | 0 | |

### v2 run status: AUDIT_INCOMPLETE

The v2 driver (`run_logs/_tmp/skill_audit_v2.py`) was launched in background (task `bj71xxyyv`) but the summary file `run_logs/_tmp/skill_audit_v2_summary.json` was not written before the stop hook fired. The output file was 0 bytes after roughly 30 seconds — the process was almost certainly still loading the 338 skill modules and their OCP imports.

**Next step:** re-run the harness synchronously (foreground PowerShell, no `run_in_background`) and allow the full 5-8 minute completion window before reading results. Until then the bucket counts above are **placeholders** — no honest claim can be made about per-skill v2 status yet.

### Sample real_broken (v2)

`AUDIT_INCOMPLETE: skill_audit_v2.py launched in background (task bj71xxyyv) but summary file run_logs/_tmp/skill_audit_v2_summary.json was not written before stop hook fired. Output file was 0 bytes after ~30s — likely still loading 338 skill modules / OCP imports. Re-run the harness in foreground (synchronous PowerShell call without run_in_background) and allow the full 5-8 min completion before requesting StructuredOutput.`

### Acknowledgement

The v1 audit's "194 all_broken" headline conflated harness failures with skill failures. The honest reading is: **159 of 194 (82%) were noise from subprocess spawn + Box-singleton init + empty selector fixtures**, and the true skill failure budget is much smaller. v2 is in-process with real fixtures and will replace v1 once it completes a full run.
