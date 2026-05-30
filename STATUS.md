# SmartTwinModeller — Project Status

마지막 갱신: 2026-05-30

## 한 줄 요약

LLM-driven parametric CAD skill library — build123d / OCCT 기반. **258 등록 skill / 16 카테고리 / 18+ 표준 카탈로그 / 74 테스트 파일**. Watch housing v0 자동합성 동작, 다음은 다양한 제품 도메인 확장.

## 무엇을 가능하게 했나

### 1) Skill 시스템 (LLM tool catalog 의 기반)

- **@skill 데코레이터** — Pydantic Args + post_conditions + history_rules + manufacturing 메타데이터 + manifest.json export
- **258 atomic/macro skill** 이 16 카테고리에 걸쳐 등록됨 (manifest.json 으로 LLM 이 발견 가능)
- **Post-condition framework** — 매 skill 실행 후 volume_decreased / volume_increased / face_count_changed / body_present 자동 검증, silent no-op 봉쇄
- **Selector 시스템** — 16개 selector (atomic 11 + combo 5) 로 face/edge 매칭, face_named heuristic + tagged selector v2 (cross-step tag chain via EntityHistoryMap)
- **SelectorFreeze** — matched_count + sha256 topology_signature 로 plan 결정성 보장
- **8종 SketchSpec** (Circle / Rectangle / RoundedRect / Polygon / Slot / BSpline / Ellipse / Composite — corner_fillet 지원)

### 2) 카테고리별 skill 분포 (16 categories)

| 카테고리 | 개수 | 대표 skill |
|---|---|---|
| **modify_pocket** | 64 | hole, extrude_pocket, extrude_pocket_blended, extrude_pocket_to_curved_floor, helical_thread_internal, swept_pocket_along_curve, revolve_pocket, text_engrave, counterbore_hole, countersink_hole, hex_socket_recess, dowel_pin_hole, nut_trap_hex, snap_ring_groove_external, bearing_bore, o_ring_groove_radial_spec, clearance_hole, tap_drill_hole, connector_cutout_from_catalog, magnet_pocket_axial, sim_tray_pocket, vent_membrane_pocket, …  |
| **inspect** | 41 | inspect_geometry, measure, cross_section, silhouette, find_features, selector_preview, symmetry_axes, mass_properties, curvature_map, surface_area_by_region, section_multi_plane, gdt_flatness, gdt_perpendicularity, gdt_cylindricity, hole_alignment_check, inspect_wall_thickness, … |
| **modify_boss** | 25 | mounting_pad, boss_with_hole, rib, snap_hook, lug_pair, o_ring_groove, crown_shaft_hole, extrude_boss_blended, revolve_boss, swept_boss_along_curve, loft_boss_between_sketches, text_emboss, spring_bar_lug_pair, gusset, heat_stake_boss, standoff, magsafe_magnet_ring, … |
| **modify_curvature** | 19 | fillet_predicate, chamfer_predicate, variable_radius_fillet, variable_radius_fillet_with_law, chamfer_asymmetric, chamfer_two_distance, chamfer_distance_angle, chamfer_conic, vertex_chamfer, face_face_fillet, surface_blend_g1, surface_blend_g2, surface_extend_tangent, surface_split_with_curve, loft_side_profile, surface_offset, swept_relief, shell_variable_thickness |
| **create** | 19 | box, disc_with_dome, rounded_slab, cylinder, sphere, cone, torus, wedge, prism_n_sided, bspline_surface, surface_thicken_variable, gear_external_involute, gear_internal_involute, helical_spring, coil_spring_rectangular, import_step, … |
| **modify_pattern** | 11 | linear_pattern, circular_pattern, mirror_feature, path_pattern, variable_pattern, nested_pattern, mirror_about_two_planes, honeycomb_pattern, knurl_pattern, voronoi_lattice, gyroid_lattice |
| **modify_sheet** | 10 | sheet_base, bend_edge, flange, hem, tab_slot, unfold, louver, jog, dimple, bend_relief |
| **assembly** | 9 | add_component, move_component, mate_planar, mate_concentric, mate_axis, mate_at_distance, interference_check, fastener_array, bom_extract |
| **compose** | 5 | union, subtract, tag_face, biocompat_region_tag, class_a_surface_tag |
| **modify_finish** | 5 | final_fillet_all_sharp_edges, deburring, sanding_pass, surface_finish_tag, cleanability_radius_enforce |
| **io** | 5 | import_step, stl_import, stl_export, mesh_to_brep, brep_to_mesh |
| **modify_mold** | 4 | parting_surface, core_cavity_split, draft_apply_auto, ejector_pin_clearance |
| **modify_hole** | 3 | hole, hole_array, grille_pattern (hole/array variants) |
| **modify_fillet** | 3 | (variants of fillet) |
| **modify_antenna** | 2 | antenna_slit, polymer_inlay |
| **modify_plateau** | 1 | extrude_plateau |

### 2.5) Reverse engineering (38 new skills, 2 new categories)

복잡한 STEP/IGES 파일을 받아 feature catalog 와 Plan(YAML) 으로 역공학하는 파이프라인이 추가됨.

- **Shape healing (repair/ — 5 skills)** — shape_heal (broken topology 일반 복구), sew_faces_to_shell (open shell 봉합), close_shell_to_solid (shell→solid), simplify_to_canonical (B-Spline → analytic 평면/원통/구/원뿔/원환), remove_micro_features (sliver face / 짧은 edge 제거)
- **Feature classifiers (inspect/)** — classify_pockets (blind / through / stepped / conical / spherical), classify_holes (simple / counterbore / countersink / threaded — 자동 표준 thread 매칭), detect_bosses, detect_ribs, detect_standoffs, detect_lugs
- **Best-fit primitives (inspect/)** — fit_plane, fit_cylinder, fit_sphere, fit_cone, fit_torus — 임의 face/face cluster 에서 RANSAC + least-squares 로 analytic primitive 추출
- **Topology graph (inspect/)** — face_adjacency_graph (face 노드 + shared edge 엣지), edge_concavity_classify (convex / concave / smooth / tangent), vertex_connectivity (vertex valence + incident face/edge)
- **Symmetry detection (inspect/)** — detect_mirror_symmetry (plane 후보 + matched face pair score), detect_rotational_symmetry (축 + order n + tolerance)
- **Pattern detection (inspect/)** — detect_linear_array (step vector + count), detect_circular_array (축 + 각 step + count)
- **Standard part matching (inspect/)** — match_standard_hole (threads_metric/imperial vs measured d/depth), match_standard_bearing (608/625/6000 series), match_standard_oring (AS568 dash), identify_fastener_recess (hex / Torx / Phillips / Pozidriv)
- **Plan reconstruction (reverse_engineer/ — 2 skills)** — extract_feature_catalog (healed solid → FeatureCatalog JSON), plan_from_feature_catalog (FeatureCatalog → Plan YAML, hole/pocket/boss/fillet/chamfer skill 호출 자동 생성)
- **File I/O (io/)** — iges_import, iges_export, brep_import, brep_export (OCCT native), step_export_v2 (AP242, PMI hint)
- **Auto-dimensioning & cross-sections (inspect/)** — auto_dimension (feature 자동 치수 산출), auto_datum_planes (principal axes 기반 datum 3 plane), cross_section_at_features (각 feature 중심 단면), principal_axes (관성 텐서 기반 주축)

이로써 SmartTwinModeller 는 generative CAD (Plan → STEP) 뿐 아니라 역방향 (STEP → Plan) 도 가능해진 양방향 파이프라인이 됨.

### 3) 표준 카탈로그 (ISO/DIN/ANSI/AS568)

`catalogs/standards/` + 도메인별 dirs:

- **`threads_metric.yaml`** — M2/M2.5/M3/M4/M5/M6/M8/M10 (ISO 724/4762/10642): outer_d, pitch, tap_drill, clearance close/medium/coarse, socket head, counterbore, countersink
- **`threads_imperial.yaml`** — #4-40, #6-32, #8-32, #10-32, 1/4-20, … (ANSI/ASME B18.2.1)
- **`drivers.yaml`** — hex socket A/F, Torx T-series, Phillips PH-series, Pozidriv PZ-series
- **`inserts_heatset.yaml`**, **`inserts_helicoil.yaml`**, **`inserts_keensert.yaml`**
- **`dowel_pins_iso2338.yaml`** — press-fit (H7) / slip-fit (H8) tables
- **`hex_nuts_din934.yaml`**, **`square_nuts.yaml`**, **`set_screws_din913.yaml`**
- **`snap_rings_din471.yaml`** (external), **`snap_rings_din472.yaml`** (internal)
- **`keys_din6885.yaml`** (parallel keys), **`woodruff_keys_din6888.yaml`**
- **`bearings_metric.yaml`** — 608 / 625 / 6000 series deep-groove
- **`o_rings_as568.yaml`** — AS568 dash sizes + face-seal land per ISO 3601-2
- **`pipe_threads_npt.yaml`**, **`pipe_threads_bsp.yaml`**
- **도메인별 카탈로그**: `connectors/`, `magnets/ndfeb.yaml`, `qi_coils/wpc.yaml`, `coin_cells/iec60086.yaml`, `membranes/gore.yaml`, `microspeakers/standard.yaml`, `motor_frames/nema.yaml`, `seals/iso6194.yaml`, `retaining_rings/`, `hvac/`, `automotive/`, `dfm_inspect/`

### 4) Watch v0 자동 합성 파이프라인

- **fixtures/make_simple_watch.py** — Galaxy Watch-style 5-part 합성 STEP 생성 (XDE 명명)
- **catalogs/components/watch/** — 5 component spec (display 44mm AMOLED, battery 350mAh, crown, PPG sensor, wireless coil)
- **catalogs/arrangements/watch_5part.yaml** — 배치 + housing envelope hint
- **`phone-designer synthesize`** CLI — arrangement → Plan(YAML) → STEP 자동 합성
- 생성 단계: base disc_with_dome → 내부 cavity (face orientation bug 수정 후 정상 동작) → mounting_pad / display pocket / crown shaft hole / PPG window → 마감 fillet
- **렌더링**: PyVista 로 iso/top/bottom/side/front PNG 캡처

### 5) Plan 시스템 + 결정성

- **Plan / Step 모델** (Pydantic) — YAML 직렬화/역직렬화, schema_version=1
- **PlanExecutor** — STRICT (selector freeze mismatch=FAIL) / LOOSE (mismatch=새 매칭 사용) 모드
- **SelectorFreeze** — 첫 실행 후 자동 캡처, 재실행시 topology_signature 비교로 같은 면 매칭 검증

### 6) Manufacturing / DFM (Phase 6)

- **catalogs/processes/** — 5 process YAML (die_cast_al, injection_mold_pa, cnc_3axis, cnc_5axis, sheet_metal_stamp)
- **ManufacturingBudget** — plan validate (safe_eval 기반)
- **DFM analyzers**: wall_thickness raymarch (false-positive floor + confidence) / draft (planar / pull_direction) / undercut
- **inspect/inspect_wall_thickness, inspect_undercut_zones, cosmetic_side_classify, inspect_sink_mark_risk** — 곡면 솔리드에서 DFM 검사

### 7) Reference (Phase 5)

- **XDE STEP reader** — STEPCAFControl_Reader 로 part name 보존
- **TopologyAnalyzer** — fillet / chamfer / hole / pocket auto-detection
- **feature_to_plan** — FeatureCatalog → Plan 자동 생성

### 8) Inspector UI

- **`phone-designer inspect`** — PySide6 + pyvistaqt 기반 plan step list + reference overlay
- **`phone-designer screenshots`** — 5 view PNG 일괄 캡처 (xy/yx/xz/zx/yz/zy 매핑 정정 완료)

### 9) Gap 분석 (95 missing skills 식별)

- **`docs/skill_gap_analysis/`** — 10 도메인 (모바일/wearable/오디오/기계동력/자동차/의료/사출DFM/CNC&판금DFM/Class-A 미관/LLM workflow) deep gap 보고
- **`docs/skill_gap_analysis/00_MASTER_SYNTHESIS.md`** — 통합 마스터: 38 P0 / 41 P1 / 16 P2 + 12 cross-cutting infra theme + 39 catalogs

### 10) lat.md/ 지식 그래프 (20 markdown files)

plan, concepts, persistent-naming, plan-determinism, skills, reference, components, manufacturing, llm, ui, architecture, phases, decisions, risks, glossary, project, dev-test, setup, work-pc-tests, backlog

## 진행 중 (백그라운드 workflow)

- **`w8ka26cay`** — P1 light wave 확장 (Wave1: speaker grille / foam seal / stylus·button → Wave2: dry_run / selector robustness / skill search → Wave3: Class-A audit / CNC reachability / helical involute gear → Reconcile)

## 알려진 한계 / 다음 라운드 후보

- **곡면 face 위 pocket** — 현재 ±Z 평면 face 만 지원
- **closed-path sweep on non-planar** — racetrack O-ring 등 미지원
- **G2 continuity blend** — surface_blend_g2 는 약식 (ShapeUpgrade 미통합)
- **Mold parting surface** — 자동 생성 미구현
- **Mesh boolean** — STL boolean / repair 미구현
- **LLM 자연어→selector** — find_skill_by_intent v1 은 keyword 매칭만 (embedding 미적용)

## 디렉터리 구조

```
SmartTwinModeller/
├── src/phone_designer/
│   ├── skills/            # 194 skill, 16 category
│   ├── plan/              # Plan/Step model + executor + YAML I/O
│   ├── reference/         # XDE STEP reader + topology analyzer
│   ├── manufacturing/     # DFM (wall/draft/undercut), ProcessRegistry
│   ├── components/        # AABB collision, arrangement, envelope
│   ├── planner/           # housing_synth_rule (rule-based v0)
│   ├── ui/                # PySide6 inspector
│   └── cli.py             # 11 commands
├── catalogs/
│   ├── standards/         # ISO/DIN/ANSI 기계 부품 표준
│   ├── components/        # 부품 spec (watch, ...)
│   ├── arrangements/      # 배치 spec
│   ├── processes/         # 가공 process spec
│   └── (도메인별)         # connectors, magnets, qi_coils, …
├── docs/
│   └── skill_gap_analysis/   # 10 도메인 + 마스터 종합
├── lat.md/                # 지식 그래프
├── tests/                 # 74 test 파일, 650+ PASS
├── fixtures/              # 합성 watch STEP 생성기
└── plans/                 # 합성 plan YAML + workflow scripts
```
