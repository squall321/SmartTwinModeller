# SmartTwinModeller — Project Status

**마지막 갱신**: 2026-06-03 · **현 commit**: `4670c5e` · **GitHub**: https://github.com/squall321/SmartTwinModeller

## 한 줄 요약

LLM-driven **양방향** parametric CAD skill library — build123d / OCCT 7.8 기반. **338 등록 skill / 22 카테고리 / 23 도메인 카탈로그 / 116 테스트 파일 / 1324 PASS**. Watch v0 합성 + 7 round 의 도메인 확장 + reverse-engineering 풀파이프라인. **Skill audit v2 (in-process, real fixtures): 미완료 — harness 재실행 필요.**

## Skill audit reality (v2 — 2026-06-03)

v1 audit의 "194 all_broken" 헤드라인은 **harness 노이즈였음을 confess** — 159/194 (82%) 는 subprocess WinError 2 + Box singleton init pattern + 빈 selector fixture 때문이지 실제 skill 버그가 아니었음. v2는 in-process 단일 Python 세션 + 실제 face_named/sketch/catalog-spec fixture 로 재측정. 보고서: [`docs/skill_audit_report.md`](docs/skill_audit_report.md) — `## v2 in-process audit` 섹션.

| Bucket | v2 Count | 의미 |
|---|---|---|
| total_skills | 0 | (AUDIT_INCOMPLETE) |
| param_responsive | 0 | 인자에 따라 ΔV가 실제로 변하는 skill |
| static_geom (façade 의심) | 0 | ok 지만 ΔV 가 상수 |
| real_broken (geom) | 0 | 실제 runtime/OCCT 실패 |
| tag_only (by design) | 0 | tag-only — geom 변화 없는 것이 정상 |
| inspect_responsive | 0 | inspect-only, 출력 응답 확인 |
| inspect_broken | 0 | inspect-only, 실제 실패 |
| io_ok | 0 | I/O round-trip 성공 |
| macro | 0 | reverse_engineer 매크로 |
| not_invoked | 0 | 호출되지 않은 skill |

**v2 상태: AUDIT_INCOMPLETE.** `skill_audit_v2.py` 가 background task `bj71xxyyv` 로 launch 됐지만 summary JSON 파일이 stop hook 발화 전에 기록되지 않음. 출력 0 bytes (~30s) — 338 skill 모듈 + OCP import 로딩 중이었을 가능성 높음. **다음:** synchronous foreground PowerShell 로 재실행하여 5-8 분 완주 보장 후 bucket count 채울 것.

**Sample real_broken (v2):** `AUDIT_INCOMPLETE: skill_audit_v2.py launched in background (task bj71xxyyv) but summary file run_logs/_tmp/skill_audit_v2_summary.json was not written before stop hook fired. Output file was 0 bytes after ~30s — likely still loading 338 skill modules / OCP imports. Re-run the harness in foreground.`

**v1 → v2 차이 (harness 자백):** v1 의 raw 194 all_broken 중 67 × subprocess spawn fail + 65 × Box-singleton init fail + 27 × bad fixture args = **159 노이즈**. 실제 skill bug 추정은 30~70 수준이며 v2 완주 후에야 정확히 분류 가능.

## 1. 핵심 인프라

| 컴포넌트 | 역할 |
|---|---|
| `@skill` 데코레이터 | Pydantic Args + post_conditions + history_rules + manufacturing 메타 + manifest.json export |
| **Post-condition framework** | 매 skill 후 ΔV / face_count 자동 검증 → silent no-op 봉쇄 (cavity 무작동, RE placeholder cube 같은 버그를 catch) |
| **Selector 시스템** | 15 selector — atomic (face_named heuristic, faces_by_normal/area, edges_by_*, tagged) + combo (and/or/not/first_n/largest_n) |
| **Tagged selector v2** | history_rules 기반 input→output body tag 자동 전파 (MODIFIED_INHERIT/SPLIT_BRANCH/CONSUMED/GENERATED_NEW) |
| **SelectorFreeze** | sha256 topology_signature 로 plan 결정성 |
| **8 SketchSpec** | Circle/Rectangle/RoundedRect/Polygon/Slot/BSpline/Ellipse/Composite + corner_fillet |
| **Plan / PlanExecutor** | YAML 직렬화, STRICT/LOOSE 모드, freeze mismatch 처리 |
| **DFM analyzers** | wall_thickness raymarch (FP floor + confidence) / draft / undercut |
| **Watch v0 합성** | `phone-designer synthesize` — arrangement → Plan → STEP |

## 2. 카테고리별 skill 분포 (22 categories, 338 skills)

| 카테고리 | 수 | 핵심 skill |
|---|---|---|
| **inspect** | 102 | geometry / measure / cross_section / silhouette / find_features / mass_properties / curvature_map / GD&T (flatness/perpendicularity/cylindricity/circularity/position) / DFM (wall_thickness/undercut/cosmetic/sink_mark) / LLM (dry_run/predict_post_conditions/find_skill_by_intent/selector_robustness) / Class-A (highlight_line/zebra) / RE classifiers (classify_pockets/holes, detect_bosses/ribs/standoffs/lugs, fit_plane/cylinder/sphere/cone/torus, symmetry/pattern detection, standard part match) / CNC (enforce_min_tool_radius/tool_reachability/pocket_aspect) / tessellate / 3D-print (overhang/support/orientation) |
| **modify_pocket** | 76 | hole 류 (clearance/tap_drill/counterbore/countersink/counterdrill/tapped) / blended (floor/opening R 내장) / 표준 driver (hex_socket/torx/phillips/pozidriv) / threaded inserts (heatset/helicoil/keensert) / dowel pin / nut trap / snap ring groove / keyseat / bearing bore / o-ring (radial/axial AS568) / pipe thread (NPT/BSP) / 도메인 (connector_cutout / sim_tray / display_bezel / camera_ring / side_button / vent_membrane / magnet / coin_cell / qi_coil / capacitive_touch / ecg_electrode / HR_optical_dome / face_id / nfc / esim / membrane_keypad / captive_screw / crown_seal_gland / sapphire_glass_seat / hinge_detent / stylus_tip / cable_routing / breathing_hole_array / led_smd / lens_seat / light_pipe_channel / text_engrave) / sweep/revolve/loft pocket / curved-floor pocket / EMI gasket groove / closed-path o_ring/foam_seal/adhesive groove |
| **modify_boss** | 27 | mounting_pad / boss_with_hole / rib / snap_hook / lug_pair / o_ring_groove / crown_shaft_hole / blended boss / sweep/revolve/loft boss / text_emboss / spring_bar_lug / gusset / heat_stake / standoff / magsafe_magnet_ring / cable_clip / hinge_pin / pcb_standoff / battery_dock_pad / haptic_motor_mount / barrel_pivot / christmas_tree_fastener / side_button_dome_mount |
| **create** | 25 | box / disc_with_dome / rounded_slab / cylinder / sphere / cone / torus / wedge / prism_n_sided / bspline_surface / surface_thicken_variable / gear_external/internal_involute / helical_spring / coil_spring_rectangular / worm_thread / gear_helical_involute / import_step |
| **assembly** | 14 | add/move_component / mate (planar/concentric/axis/at_distance) / interference_check / fastener_array / bom_extract / exploded_view / sub_assembly_tag / auto_place_fasteners / bolt_pattern_recognize / check_clearance_full |
| **modify_curvature** | 12 | fillet/chamfer predicate / variable_radius_fillet (+ with_law) / chamfer_asymmetric/two_distance/distance_angle/conic / vertex_chamfer / face_face_fillet / surface_blend_g1/g2 / surface_extend_tangent / surface_split / shell_variable_thickness / loft_side_profile / waveguide_horn_profile / optical_window_dome / swept_relief / surface_offset |
| **modify_pattern** | 11 | linear/circular/mirror/path / variable/nested / mirror_about_two_planes / honeycomb/knurl / voronoi/gyroid_lattice / hvac_blade_array / acoustic_grille_pattern / speaker_grille_curved_array / motor_flange_bolt_pattern |
| **modify_sheet** | 10 | sheet_base/bend_edge/flange/hem/tab_slot/unfold/louver/jog/dimple/bend_relief |
| **modify_finish** | 9 | final_fillet_all_sharp_edges / deburring / sanding_pass / surface_finish_tag / cleanability_radius_enforce / apply_anodize/paint/plating / apply_texture_region |
| **fem_cae** | 9 | export_mesh_for_fem / boundary_condition_tag / material_property_tag / mesh_refinement_zones / thermal_bc_tag / contact_pair / modal_analysis_setup / load_case_compose / export_abaqus_inp_v2 |
| **modify_mold** | 8 | parting_surface / core_cavity_split / draft_apply_auto / ejector_pin_clearance / cooling_channel_path / gating_position_candidates / runner_diameter_calc / slide_action_geometry / ejector_pin_pattern |
| **compose** | 5 | union / subtract / tag_face / biocompat_region_tag / class_a_surface_tag |
| **modify_chamfer** | 5 | chamfer_predicate + 4 variants |
| **repair** | 5 | shape_heal / sew_faces_to_shell / close_shell_to_solid / simplify_to_canonical / remove_micro_features |
| **pmi** | 5 | pmi_dimension_callout / pmi_surface_texture / pmi_weld_symbol / export_step_ap242_pmi / pmi_inspect_summary |
| **modify_3dprint** | 4 | raft_add / support_tree_path / infill_region_tag / add_support_brim |
| **modify_hole / fillet / antenna / plateau / reverse_engineer** | 11 합 | hole/array/grille + variants + antenna_slit/polymer_inlay + extrude_plateau + extract_feature_catalog / plan_from_feature_catalog |

## 3. 표준 카탈로그 (23 디렉터리)

```
catalogs/standards/      ← ISO/DIN/ANSI/AS568 기계 부품
  threads_metric (M2-M10) / threads_imperial (#4-1/2") / drivers (hex/Torx/Phillips/Pozidriv)
  dowel_pins_iso2338 / hex_nuts_din934 / square_nuts / set_screws_din913
  snap_rings_din471/472 / keys_din6885 / woodruff_keys_din6888
  inserts_heatset/helicoil/keensert / pipe_threads_npt/bsp

catalogs/bearings/  deep_groove_ball (608/625/6000 series)
catalogs/seals/  iso6194 lip seals
catalogs/retaining_rings/  external/internal

catalogs/connectors/  USB-C/USB-A/Lightning/audio jack
catalogs/magnets/  ndfeb N42
catalogs/qi_coils/  WPC A11/A28
catalogs/coin_cells/  IEC60086 CR/SR/LR
catalogs/membranes/  Gore PMF/Acoustic
catalogs/microspeakers/  standard sizes
catalogs/motor_frames/  NEMA 17/23/34

catalogs/finishes/  anodize / paint / plating / textures / emi_gaskets
catalogs/sensors_health/  haptic motors / temperature
catalogs/audio/  mems_mics
catalogs/optical/  leds (SMD 0402-5050) / lenses (plano-convex/aspheric) / light_pipes
catalogs/automotive/  christmas_tree fasteners / wire_clips
catalogs/hvac/  blade arrays

catalogs/components/watch/  5 watch component specs
catalogs/arrangements/  배치 spec
catalogs/processes/  5 제조 process (die_cast_al / injection_mold_pa / cnc_3axis / cnc_5axis / sheet_metal_stamp)
catalogs/budgets/  watch_al_unibody / watch_plastic
catalogs/dfm_inspect/  DFM 매개변수
```

## 4. Reverse-Engineering 풀파이프라인

```
STEP/IGES/BREP/mesh
  └─ import_step / iges_import / brep_import / mesh_to_brep (10k cap 제거, unit auto-detect, closure 감지)
  └─ shape_heal / sew / close / simplify_to_canonical (broken topology 복구)
  └─ extract_feature_catalog (holes/pockets/bosses/ribs/lugs/symmetries/patterns/standard_matches 통합)
  └─ classify_* / detect_* / fit_* / match_standard_* (개별 분류기)
  └─ plan_from_feature_catalog (FeatureCatalog → Plan YAML)
  └─ PlanExecutor (실제 build123d skill 호출)
  └─ Re-export STEP
  └─ fidelity diff (volume / face_count / cube-collapse 감지)
```

**진화**:
| 단계 | 결과 |
|---|---|
| Baseline (Round-trip 첫 실행) | 2/7 within 5%, **5/7 cube collapse**, +69% avg drift — façade |
| Tier A (placeholder bbox 수정 + fidelity gate) | 0/3 cube collapse, 1/3 pass |
| **Tier B (sweep/revolve/loft + pattern 매핑)** | **3/3 pass** ✓ |
| Tier C (coverage 10 case + 매핑 풍부화 + mesh→BREP 검증) | iphone teardown glb 364k tri → BREP 가능 확인 |

**RE 인프라**:
- `corpus/oem/` 디렉터리 + `.gitignore` (OEM 비공개)
- `phone-designer corpus-test --dir corpus/oem` CLI — 디렉터리 walk → 파일별 fidelity report → exit code
- `FIDELITY_STRICT=1 pytest -m fidelity` — CI hard gate

## 5. Watch v0 자동 합성

- `fixtures/make_simple_watch.py` — 5-part Galaxy Watch style 합성 STEP
- `catalogs/components/watch/` — 5 component spec
- `catalogs/arrangements/watch_5part.yaml` — 배치
- `phone-designer synthesize` CLI — arrangement → Plan(YAML) → STEP (cavity / pad / display pocket / crown / PPG / final fillet)
- `phone-designer screenshots` — 5 view PNG 일괄

## 6. UI / CLI

**CLI (`phone-designer` entrypoint)**:
- `test` `generate` `reproduce` `validate` `synthesize` `compare` `view` `screenshots` `inspect` `ui` `version` `config`
- `corpus-test` (Tier C) — RE 검증 디렉터리 walk
- `inspect-re` (Round 7) — RE catalog + plan diff UI 패널 launch

**UI (PySide6 + pyvistaqt)**:
- Inspector 메인 윈도우 + plan step list + reference overlay
- `feature_catalog_panel` (Round 7) — RE detected feature tree
- `plan_diff_panel` (Round 7) — 두 Plan YAML side-by-side diff
- `highlight_overlay` (Round 7) — pyvista face/edge highlight

## 7. 도메인 커버리지 (제품군별)

| 제품군 | 커버 |
|---|---|
| **Smartphone / tablet** | sim_tray / display_bezel / camera_ring / side_button / face_id / nfc_coil / esim / magsafe_magnet_ring / antenna_isolation_slit |
| **Wearable (watch / earbud case)** | spring_bar_lug / crown_seal_gland / sapphire_glass_seat / hinge_detent / light_pipe_channel / haptic_motor / capacitive_touch / ECG / HR_optical |
| **Audio (speaker / mic / 헤드폰)** | acoustic_grille_pattern / speaker_acoustic_chamber / helmholtz_port / mems_mic_boot / waveguide_horn / bass_reflex_flared / speaker_baffle_window / anechoic_chamber_liner |
| **Power tool / motor housing** | bearing_seat / lip_seal_cavity / shaft 관련 / NEMA flange / cooling fin / brush slot |
| **Automotive interior / HVAC** | hvac_blade_array / barrel_pivot_socket_pair / hvac_barrel_vent_housing / christmas_tree_fastener / wire_clip_slot |
| **Medical** | membrane_keypad_recess / cleanability_radius_enforce / biocompat_region_tag / captive_screw_tether |
| **3D printing prep** | overhang_check / support_volume_estimate / orientation_optimize / raft / support_tree / infill_region_tag / slicer_hints |
| **Mold tooling** | parting_surface / core_cavity_split / cooling_channel_path / gating / runner / slide_action / ejector_pin_pattern |
| **Sheet metal** | bend_edge / flange / hem / tab_slot / unfold / louver / jog / dimple / bend_relief |
| **PMI / GD&T** | dimension_callout / surface_texture / weld_symbol / AP242 PMI export / flatness/perp/parallel/cylindricity/circularity/position GD&T check |
| **CAE bridge** | BC tag (fixed/pinned/force/pressure/temperature/thermal) / contact pair / material / mesh refinement / modal / load case / Abaqus INP v2 export |
| **Optical** | LED SMD pocket / lens seat / light pipe routing / optical window dome / ray_trace smoke |

## 8. 가능한 워크플로 (실제 활용 시나리오)

1. **Generative** — `arrangement.yaml + components.yaml → Plan → STEP → DFM check`
2. **Reverse-engineering** — `OEM STEP → heal → extract_feature_catalog → plan_from_feature_catalog → PlanExecutor → re-STEP + fidelity diff`
3. **Round-trip CI gate** — `FIDELITY_STRICT=1 pytest -m fidelity` (merge block)
4. **Corpus 검증** — `phone-designer corpus-test --dir corpus/oem --report-out report.md`
5. **PMI export** — `body + dimension/texture/weld tags → AP242 STEP (PMI 포함)`
6. **CAE handoff** — `body + BC/material/contact/modal tags → Abaqus INP`
7. **Mold tooling auto** — `body → parting_surface + draft → core/cavity split + cooling channel + gate/runner candidate`

## 9. 커밋 히스토리 (12 commits)

```
4670c5e Round 7 tail — abaqus + plan args fixes
36fcb69 Round 7 — PMI + CAE + UI + optical
ec76b82 Cleanup — RT doc + ignore scratch
9d91228 RE Tier C followup — mesh→BREP + CI gate + corpus CLI
772b3a4 RE Tier C — coverage + mapping enrichment
73cf747 RE Tier B — sweep/revolve/loft + pattern regen
be5df7e RE pipeline — placeholder bbox + fidelity gate
2b62486 Round 6 — 3D-print + FEM/CAE + finishes + biometric/phone/audio
9e6f6d9 Round 5 — 3D-print DFM + mold + GD&T + assembly + LLM control
ec91e1f Loosen worm_thread test bound
c8d2565 RE boost — 38 new skills
e30fd09 Initial — 220 skills baseline
```

## 10. 알려진 한계 / 다음 영역

- **곡면 face 위 pocket** — 평면 ±Z 만 지원 (`build_pocket_tool` 의 normal 처리 제한)
- **mesh→BREP** — open-shell 은 solid 화 안 됨, decimation 필요
- **G2 continuity blend** — surface_blend_g2 약식 (ShapeUpgrade 미통합)
- **PMI AP242 export** — 표준 준수 검증 안 됨 (실제 PMI consumer 로 round-trip 필요)
- **OEM STEP 실증** — 사용자가 `corpus/oem/` 에 드롭해야 진짜 다양성 데이터 수집 가능
- **LLM 자연어 → selector** — `find_skill_by_intent` v1 은 keyword 매칭만 (embedding 미적용)
- **UI 패널** — smoke test 만, 실제 사용성 검증 필요
- **CAE Abaqus INP** — PMI 와 마찬가지로 외부 consumer round-trip 필요

## 11. 디렉터리 구조

```
SmartTwinModeller/
├── src/phone_designer/
│   ├── skills/            # 338 skills, 22 categories
│   │   ├── repair/        (5)   ── shape healing
│   │   ├── reverse_engineer/ (2) ── catalog extract + plan generate
│   │   ├── pmi/           (5)   ── GD&T export
│   │   ├── fem_cae/       (9)   ── CAE bridge
│   │   ├── inspect/       (102) ── read-only analysis
│   │   ├── compose/       (5)   ── boolean + tag
│   │   ├── create/        (25)  ── base shapes + gears + springs
│   │   ├── io/            ── STEP/IGES/BREP/STL/mesh I/O
│   │   ├── modify_*/      ── pocket/boss/curvature/pattern/sheet/finish/mold/3dprint/antenna/hole/fillet/chamfer/plateau
│   │   └── assembly/      (14)  ── multi-body mating
│   ├── plan/              # Plan/Step model + executor + YAML I/O
│   ├── reference/         # XDE STEP reader + topology analyzer + feature_to_plan
│   ├── manufacturing/     # DFM (wall/draft/undercut), ProcessRegistry
│   ├── components/        # AABB collision, arrangement, envelope
│   ├── planner/           # housing_synth_rule
│   ├── ui/                # PySide6 inspector + panels (RE viz)
│   └── cli.py             # 13 commands
├── catalogs/              # 23 카테고리 디렉터리 (표준 + 도메인)
├── corpus/oem/            # OEM 비공개 드롭존 (.gitignore)
├── docs/
│   ├── round_trip_validation.md
│   └── skill_gap_analysis/  ── 10 도메인 + 마스터 종합
├── lat.md/                # 지식 그래프 (20 markdown)
├── tests/                 # 116 test 파일, 1324+ PASS
├── fixtures/              # 합성 watch + RE fixtures
├── plans/                 # synth plan YAML + workflow scripts
└── scripts/               # setup.ps1, ci_fidelity_gate.ps1, etc.
```
