# DFM: Injection Molding Deep-Dive — Skill Gap Analysis

Scope: production injection molding DFM features for consumer-electronics housings
(watch / phone / earbud / IoT). Targets thermoplastics (ABS, PC, PC/ABS, PA6-GF30, POM,
PMMA, TPE overmold). Where relevant, cite SPI/SPE, VDI 3400, MoldTech, ISO 294, ISO 2768
tolerance classes.

---

## 1. Survey — what already touches injection molding

### 1.1 modify/mold category (the only IM-specific bucket today)

| Skill | What it does | DFM coverage |
|---|---|---|
| `parting_surface` | Generates **planar** rectangular parting face at a chosen Z, with the body's silhouette cut out. Output goes into `extras["parting_surface_shape"]`. | Planar only — no stepped, no shut-off, no slide-direction handling. |
| `core_cavity_split` | Half-space ∩ body to produce `core_solid` / `cavity_solid` via BRepAlgoAPI_Common (using finite boxes as half-spaces). | Strictly planar split. No tongue-and-groove interlock, no shut-off surfaces, no insert pockets. |
| `draft_apply_auto` | `BRepOffsetAPI_DraftAngle` on near-vertical planar faces relative to a single +Z/-Z pull. | Single pull direction only. Cannot handle multi-direction (side cores). Skips non-planar faces. No "already-drafted" detection. No witness on textured regions (texture demands +1° per VDI 3400 grade above 30). |
| `ejector_pin_clearance` | Boolean-cuts cylindrical pin clearances at supplied XY positions. | The user must already know pin positions — no automatic distribution, no flatness check, no witness-mark cosmetic-side avoidance. |

### 1.2 Tangentially related (touch IM but live in other categories)

- `shell_variable_thickness` — per-face thickness for a **shell**, but solid bodies with non-uniform walls (rib stacks, boss clusters) have no equalization tool.
- `rib` — straight stiffening rib (start→end, width/height/draft). Declares `min_wall_mm: 0.6` and `width_recommended: wall_thickness * 0.6`, but enforcement happens only via plan validator — there is no skill that, given a rib and a wall, **derives the rib width** to keep below the 60% sink-mark threshold.
- `boss_with_hole`, `heat_stake_boss`, `standoff`, `mounting_pad` — produce bosses but do not auto-core, do not auto-shape the base fillet, and do not check the boss OD ≤ 2.5× nominal wall rule (Bayer/Covestro thermoplastic boss design guide).
- `surface_finish_tag` — tags a face with a finish type (`polish | matte | texture_a | texture_b`) and `target_ra_um`, but the catalog is **not aligned with VDI 3400 grades** (no roughness Ra in µm per VDI step, no MoldTech codes, no SPI A1/A2 mapping).
- `inspect/find_features` — heuristic; recognizes hole / pocket / fillet / chamfer. **Does NOT detect** rib-wall T-junctions, gate-marks-prone faces, thin-section pinch points, or undercuts.
- `mesh_quality` — mesh-only metric, not a moldflow proxy.

### 1.3 Catalog data declared, but not yet emitted as skills

`lat.md/manufacturing.md` describes a `DFMReport` with `wall_violations`,
`draft_violations`, `undercut_violations` plus a ray-march `wall_thickness_raymarch`
prototype, but **no skill** in `inspect/` returns any of these. The DFM v0 mentioned in
the doc is unimplemented.

### 1.4 Verdict

The IM bucket has the **four crudest skills** (split + parting + draft + ejector) and
nothing else. Every item in the brief — variable wall equalization, gate location
selection, runner sizing, cooling channels, slide-action geometry, sink-mark prediction,
shrinkage compensation, texture region masking, hot-runner sprue, witness-mark
minimization — is **missing entirely**. This is the largest single coverage gap in the
manifest.

---

## 2. Top missing skills (production-grade DFM)

Ordered by priority. Each entry assumes the host scaffolding exists (registry,
PostCondition, SelectorRef).

### 2.1 `inspect_wall_thickness` (P0, moderate)
**Behavior.** Ray-march each face inward along its inverted normal, return a list of
`(point, thickness_mm, face_id)` samples plus aggregate {min, max, p05, p95, ratio}.
Optionally returns a violation list against a `required_min_mm` / `required_max_mm`
band.
**Pydantic args.** `n_samples: int = 2000`, `required_min_mm: float | None`,
`required_max_mm: float | None`, `face_selector: SelectorRef | None`,
`max_ratio: float = 3.0` (target max:min).
**Why.** No-deformation injection requires max:min wall ratio ≤ ~3:1 (Moldflow / Bayer
Snap-Fit and Plastic Part Design guide). Without this, the LLM cannot reason about
sink, warp, or fill imbalance. Concrete example: Galaxy Buds case rear hinges have
1.0 mm shell adjacent to 3.0 mm hinge column — without this skill the agent cannot see
the 3:1 limit being hit.
**Standard.** Bayer "Snap-Fit Joints for Plastics" §4, SABIC "Material Selection Guide"
§wall uniformity, Moldflow's Confidence Rating.

### 2.2 `equalize_wall_thickness` (P0, hard)
**Behavior.** Given a target nominal wall and a max ratio, apply local coring
(internal cylindrical pocket) where solid thickness exceeds `max_wall_mm`. Returns the
modified body plus a `coring_features` list.
**Pydantic args.** `nominal_wall_mm: float = 2.0`, `max_wall_mm: float = 4.0`,
`min_coring_diameter_mm: float = 3.0`, `coring_strategy: Literal["circular","slot","honeycomb"]`,
`preserve_features: list[SelectorRef]`.
**Why.** Solid sections over 4 mm in semi-crystalline resins (PA, POM) cause sink and
long cycle times (cycle ∝ wall²). Standard fix: hollow out from the non-cosmetic side
with cores spaced ≥ 2× wall apart (Bayer §3.2). Concrete: a watch back lug solid block
becomes three cored slots.
**Standard.** SPE Plastic Part Design Guide Ch.3 "Wall Thickness", DIN ISO 294-2 sample
cooling time formulas.

### 2.3 `inspect_undercut_zones` (P0, moderate)
**Behavior.** For a given pull direction, identify all face patches occluded
(shadowed) along the pull. Returns per-undercut: `(face_id, area_mm2, max_depth_mm,
suggested_action: "lifter|slide|relief")`.
**Pydantic args.** `pull_direction: Literal["+Z","-Z","+X","-X","+Y","-Y","custom"]`,
`custom_vector: tuple | None`, `min_undercut_area_mm2: float = 0.5`.
**Why.** Drives the slide/lifter decision (cost). The existing `draft_apply_auto` does
not flag undercuts — it just adds draft to whatever it can see. Concrete: a side-port
USB-C cutout on a watch wraps around the parting line and demands a slide; the LLM
needs to be told.
**Standard.** Routsis Training "Tooling Fundamentals" §undercut.

### 2.4 `inspect_sink_mark_risk` (P0, moderate)
**Behavior.** Scan T-junctions where a rib/boss meets a cosmetic wall. For each
junction compute the local thickness-ratio (rib base / nominal wall). Flag any > 0.6
(injection rule of thumb) or compute a numeric sink-depth proxy
`Δ ≈ 0.05 × wall × (rib_base / wall)` mm.
**Pydantic args.** `cosmetic_face_selector: SelectorRef`,
`sink_ratio_threshold: float = 0.6`, `material: Literal["ABS","PC","PA6-GF30","POM","PP","PMMA"]`.
**Why.** Sink marks at rib intersections are the #1 cosmetic defect in injection
molded electronics. The skill must consider material shrinkage (POM 2.0%, PA6 1.6%,
ABS 0.5%, PC 0.6%) — high-shrinkage materials sink more from the same ratio. Concrete:
earbud charging cradle inner ribs at 0.8 × 1.2 mm wall = 0.67 ratio → predicted sink
visible.
**Standard.** Bayer "Plastic Part Design" §4.4 rib-to-wall ratio chart; PTC Creo
Moldex3D sink prediction.

### 2.5 `gate_location_candidates` (P0, hard)
**Behavior.** Read-only. Return a ranked list of candidate gate points on the
**non-cosmetic** face set, with scoring:
- flow-length-to-thickness ratio (L/t) ≤ material limit
  (ABS 150, PC 100, PA-GF 200, POM 130, PMMA 100)
- distance from witness-critical faces (tagged via `surface_finish_tag`)
- balance: prefers the centroid of mass-distribution
- avoids weld-line zones near logos / cutouts (heuristic).
Returns `[{point, normal, score, predicted_max_L_over_t, predicted_weld_line_zones}, …]`.
**Pydantic args.** `cosmetic_face_selector: SelectorRef`, `material: str`,
`wall_thickness_mm: float`, `gate_type: Literal["sub","edge","fan","hot_tip","valve"]`,
`n_candidates: int = 5`.
**Why.** Choosing the gate is the single highest-leverage IM decision. Without it the
agent can't even draft a tool concept. Concrete: a 110 mm watch strap link in PC/ABS
@ 1.0 mm wall has L/t ≈ 110 — needs two gates or a 1.2 mm wall, and the skill must say
so.
**Standard.** Bayer "Material Selection Spiral" L/t tables, ISO 294-1 standard fill
length test pieces.

### 2.6 `cosmetic_side_classify` (P0, moderate)
**Behavior.** Tag every face as `cosmetic | functional | hidden` based on:
- visibility from a user-supplied "viewing hemisphere" (set of viewpoint vectors)
- pull-direction shadow (faces facing the parting are gate/ejector candidates)
- existing `surface_finish_tag` overrides
Writes `body._pd_cosmetic` dict (analogous to `_pd_finish`). Gate, ejector,
witness-line skills should consume it.
**Pydantic args.** `viewing_vectors: list[tuple[float,float,float]]` (default = +Z, +X,
-X, +Y, -Y), `cosmetic_threshold_steradian: float`.
**Why.** Every other DFM skill (gate location, ejector layout, parting line, witness
avoidance) needs this classification. Today it does not exist — the LLM must guess. Concrete:
phone mid-frame inner faces are functional; outer band + camera deck are cosmetic.

### 2.7 `cold_runner_sizer` (P1, moderate)
**Behavior.** Read-only/compute-only. Given gate flow lengths and a target shear rate,
compute the **balanced runner diameters** (primary, secondary, tertiary) using the
Beaumont 5th-power rule and ANSI/SPI guidance: `D = (wall × √L × 4√(shot_g/3.7))^0.5`.
Returns `[{segment, diameter_mm, length_mm, predicted_pressure_drop_bar}, …]`.
**Pydantic args.** `gate_points: list[tuple]`, `shot_mass_g: float`, `wall_mm: float`,
`material: str`, `target_shear_rate_1s: float = 50000`, `n_cavities: int`.
**Why.** Cold runner Ø is normally the second-biggest scrap cost driver; under-sized →
short shot, over-sized → cycle time bloat. Concrete: 16-cavity watch button mold needs
balanced runner Ø 3.5 → 2.5 → 1.5 mm; agent should compute, not guess.
**Standard.** John Beaumont "Runner & Gating Design Handbook" Ch. 4–5; SPI 5th-power
balance rule.

### 2.8 `cooling_channel_path` (P1, hard)
**Behavior.** Generate a polyline path for a circulating cooling channel that holds
**2–3× channel-diameter spacing** from every cavity surface and **3–5× channel-diameter
center-to-center pitch** (Menges/Mohren cooling-line design). Returns wire / polyline
in `extras["cooling_path"]` plus predicted average cool time
`t = h² / (π² · α) · ln(4/π · (T_melt-T_mold)/(T_eject-T_mold))`.
**Pydantic args.** `core_or_cavity_solid: extras_ref`, `channel_diameter_mm: float = 8.0`,
`min_offset_mm: float | None` (defaults to 2×Ø), `target_pitch_mm: float | None`
(defaults to 4×Ø), `inlet_xy: tuple`, `outlet_xy: tuple`, `serpentine | conformal: Literal`.
**Why.** ~70% of cycle time is cooling; bad channel layout doubles tool cost. Even a
heuristic offset/pitch generator turns a 4-hour manual layout into seconds. Concrete:
earbud cradle cavity, channel Ø 5 mm, must stand off 10 mm from the cosmetic dome.
**Standard.** Menges/Mohren "How to Make Injection Molds" §13, DME "Mold Components"
catalog spacing tables.

### 2.9 `slide_action_geometry` (P1, hard)
**Behavior.** Given a face set classified as undercut by `inspect_undercut_zones`,
generate the **slide block geometry**: the projected XY footprint of the side core,
the back-draft surface (1° standard), the heel block (10° wedge), and the pull stroke
length. Returns the slide solid in `extras["slide_solid"]` and an
`extras["slide_metadata"]` block with pull stroke / wedge angle.
**Pydantic args.** `undercut_face_selector: SelectorRef`,
`slide_direction: tuple[float,float,float]`, `back_draft_deg: float = 1.0`,
`heel_angle_deg: float = 10.0`, `safety_pull_mm: float = 2.0`.
**Why.** Slide actions add ~$5–15k per slide to tool cost. The agent must explicitly
produce the geometry to verify pull clearance and report cost impact. Concrete: phone
SIM tray pocket parallel to parting requires a side core ~12 mm pull stroke.
**Standard.** DME/Hasco standard slide kit dimensions; ASME Y14.5 datum for slide
parting.

### 2.10 `ejector_pin_layout_auto` (P1, moderate)
**Behavior.** Replace today's "user provides positions" with auto-placement:
distribute pins on a target pitch (typically 30–80 mm) on **non-cosmetic** faces with
local flatness checked, weight each pin by part mass ÷ pin tensile area
(target stress < 60% material yield), avoid `surface_finish_tag` regions.
**Pydantic args.** `target_pitch_mm: float = 50`, `pin_diameter_mm: float = 3.0`,
`material: str`, `cosmetic_avoid: bool = True`, `min_clear_from_edge_mm: float = 3.0`.
**Why.** Witness-mark minimization. A watch case rear has logo etching — any pin under
the logo zone is a reject. The skill must consult `_pd_cosmetic` and `_pd_finish`.
Concrete: AirPods case base has 4 pins on 35 mm grid; manual layout error → witness
on logo.
**Standard.** D-M-E "Mold Engineering Quarterly" pin-loading guidelines.

### 2.11 `apply_shrinkage_compensation` (P1, trivial-moderate)
**Behavior.** Scale the body by an anisotropic shrinkage factor — by default
isotropic per material lookup, but accepts per-axis vector for fiber-reinforced grades
(PA6-GF30 differs ~0.4% flow vs 1.2% cross-flow).
**Pydantic args.** `material: Literal["ABS","PC","PC_ABS","PA6","PA6_GF30","POM","PP","TPU","PMMA","HDPE"]`,
`custom_shrinkage_pct: tuple[float,float,float] | None`,
`flow_axis: Literal["+X","+Y","+Z"] = "+X"`.
**Default catalog (per ISO 294-4 & material data sheets):**
ABS 0.5%, PC 0.6%, PC/ABS 0.5%, PA6 1.6%, PA6-GF30 0.4/1.2%, POM 2.0%, PP 1.5%,
TPU 1.2%, PMMA 0.5%, HDPE 2.0%.
**Why.** The cavity must be cut **larger** than the part by this factor. Today nothing
applies this — the agent will hand off a nominal CAD to a toolmaker who will then
manually upscale, losing parametric history. Concrete: a POM gear at 8.000 mm OD
must be cut at 8.160 mm in steel.
**Standard.** ISO 294-4 "Determination of Moulding Shrinkage", material TDS per resin.

### 2.12 `texture_region_apply` (P1, moderate)
**Behavior.** Mask a face set with a texture spec — VDI 3400 grade (12 to 45) or
MoldTech (MT-11010 to MT-11050), plus SPI grade (A1–D3). Auto-bumps the local draft
requirement by `(grade − 24) × 0.5°` per the VDI 3400 published draft guide (e.g.,
grade 33 → +5° additional draft beyond the base 1°). Validates against
`inspect_draft` and emits violations.
**Pydantic args.** `face_selector: SelectorRef`,
`spec: Literal["VDI3400_G12","VDI3400_G24","VDI3400_G33","VDI3400_G39","VDI3400_G45","MT_11010","MT_11020","MT_11030","SPI_A1","SPI_A2","SPI_A3","SPI_B1","SPI_C1","SPI_D2"]`,
`ra_target_um: float | None` (auto-derived if None),
`min_required_draft_deg: float | None` (auto-derived).
**Why.** The `surface_finish_tag` skill exists but uses ad-hoc labels (`texture_a`,
`texture_b`) with no roughness mapping and no draft consequence. Production demands
explicit VDI/MT codes on the print. Concrete: watch case top has VDI 3400 grade 33
(Ra ~ 4.5 µm) leather-grain texture, demanding 5° draft, not the 1° auto-applied today.
**Standard.** VDI 3400 (1975) "Electroerodierte Oberflächen", MoldTech standard sheet,
SPI/SPE finish guide.

### 2.13 `boss_design_check` (P1, moderate)
**Behavior.** Inspect every boss in the body (via `find_features` extension). For each
boss: outer Ø ≤ 2.5× nominal wall? base fillet present and ≥ 0.25× wall? boss height
≤ 5× boss Ø? hole-to-OD wall thickness ≥ 0.6× nominal wall? Returns violations.
**Pydantic args.** `nominal_wall_mm: float`, `material: str`,
`include_self_tapping_check: bool = True` (PEM/SEMS pull-out load).
**Why.** Bosses are the second sink-mark hotspot. A typical mistake: making a 3 mm OD
boss in a 1 mm wall (3:1, sinks). The plan validator alone cannot see this — needs an
inspect skill. Concrete: every hinge-mount boss in a phone mid-frame.
**Standard.** Bayer "Snap-Fit Joints" §boss design, PEM® self-clinching fastener data
sheets.

### 2.14 `parting_line_step` (P2, hard)
**Behavior.** Generalize the existing planar `parting_surface` to a **stepped** /
**curved** parting surface that follows the body's silhouette ring around features
that don't sit on a single Z plane. Output goes into the same `extras` slot so
`core_cavity_split` consumes either kind.
**Pydantic args.** `silhouette_curve: optional`,
`fallback_z_mm: float`, `step_smoothing_radius_mm: float = 1.0`.
**Why.** Any non-flat parting joint (curved phone back, watch dome) needs this. Today
the agent can ONLY do planar parts. Concrete: AirPods earbud sphere → equatorial
parting curve is a great circle, not a plane.
**Standard.** Routsis "Mold Building" §parting surface construction.

### 2.15 `hot_runner_sprue_interface` (P2, moderate)
**Behavior.** Generate the hot-runner gate well: a counter-bored cylindrical pocket
on the cavity-side at the gate point, sized to mate with a standard nozzle (Mold-Masters,
Husky, Synventive catalogs). Returns the modified cavity solid + the nozzle clearance
geometry as a separate solid for tooling alignment.
**Pydantic args.** `gate_point: tuple`, `nozzle_series: Literal["MM_AccuValve","HK_UltraSync","SYN_eGate"]`,
`nozzle_tip_diameter_mm: float`, `well_depth_mm: float`, `gate_seal_diameter_mm: float`.
**Why.** ~70% of consumer-electronics tools use hot runners; the interface geometry
is fully standardized but tedious. Concrete: 4-drop hot manifold for an earbud cradle.
**Standard.** Mold-Masters Sprint catalog standard tip dimensions; SPI hot runner
section.

---

## 3. Cross-cutting infrastructure gaps

These are not skills — they are platform features all of the above need.

- **`_pd_cosmetic` namespace** parallel to `_pd_finish` / `_pd_tags`, populated by
  `cosmetic_side_classify`, consumed by gate/ejector/witness skills. Today the LLM has
  no way to communicate "this face must be witness-free".
- **Material catalog** (`catalogs/materials/*.yaml`) keyed by ISO 1043/1873 material
  code: shrinkage (iso/aniso), max L/t, melt/mold temps, ejection temp, glass content.
  Today material is just a string with no data attached.
- **VDI 3400 / MoldTech / SPI finish catalog** with Ra, draft requirement, EDM/etch
  process. Today the only finish vocabulary is `polish | matte | texture_a | texture_b`.
- **Ray-march thickness sampler** as a shared `_resolvers` helper — used by
  `inspect_wall_thickness`, `inspect_sink_mark_risk`, `cooling_channel_path`,
  `boss_design_check`.
- **Multi-pull-direction `draft_apply_auto`** — accept a list of `pull_directions`
  (one per slide/lifter region) instead of one global +Z/-Z.
- **Stepped half-space** for `core_cavity_split` — today it uses two finite boxes
  (planar only); needs a `(parting_surface_shape) → upper/lower TopoDS_Shape` builder.
- **PostCondition `wall_thickness_band`** — encode the 0.8 mm ≤ t ≤ 4.0 mm contract so
  any geometry-modifying skill can be checked.
- **Failure mode codes** for new skills: `fm.gate_l_over_t_exceeded`,
  `fm.cooling_channel_too_close`, `fm.sink_mark_predicted`, `fm.undercut_no_slide_budget`,
  `fm.boss_od_exceeds_wall_ratio`, `fm.texture_draft_insufficient`.

---

## 4. Domain-specific catalogs needed

| Catalog file | Content | Driving standard |
|---|---|---|
| `catalogs/materials/im_thermoplastics.yaml` | per-material: shrinkage (iso + flow/cross), max L/t, melt T, mold T, ejection T, Tg, density, modulus | ISO 1043, supplier TDS (Covestro / SABIC / DuPont) |
| `catalogs/finishes/vdi3400.yaml` | grade 12–45 → {Ra µm, draft_add_deg, EDM_amperage} | VDI 3400 |
| `catalogs/finishes/moldtech.yaml` | MT-11010…11050 → {Ra µm, etching depth µm, draft_add_deg} | MoldTech standard sheet |
| `catalogs/finishes/spi.yaml` | A1 / A2 / A3 / B1 / B2 / C1 / C2 / D1 / D2 / D3 → {Ra µm, finishing process} | SPI/SPE finish guide |
| `catalogs/runners/cold_runner_ranges.yaml` | resin family → trapezoidal vs full-round Ø ranges | Beaumont Runner & Gating |
| `catalogs/hot_runner_nozzles/*.yaml` | per-vendor (MoldMasters / Husky / Synventive / INCOE) nozzle tip dim families | vendor catalogs |
| `catalogs/cooling/coolant.yaml` | water vs oil h-coefficients, working T ranges | Menges/Mohren §13 |
| `catalogs/slides/dme_hasco_kits.yaml` | standard slide kit dimensions | DME/Hasco catalogs |

---

## 5. Concrete product examples used above

- **Galaxy Buds Live cradle** — 1.0 mm shell, 3.0 mm hinge column → wall-ratio + sink
- **Apple Watch series 7 case** — VDI 3400 G33 leather grain on cosmetic top
- **AirPods 2 charging case** — 4-drop hot runner, ejector layout under logo zone
- **POM watch crown gear** — 2% shrinkage scaling, anisotropic check
- **Galaxy A-series mid-frame** — undercut at SIM tray, slide-action geometry
- **iPhone 14 camera deck** — boss OD : wall ratio limits
- **Watch strap link (110 mm)** — gate location L/t boundary case

---

## 6. Priority summary

| Priority | Count | Skills |
|---|---|---|
| **P0 — blockers** | 6 | inspect_wall_thickness, equalize_wall_thickness, inspect_undercut_zones, inspect_sink_mark_risk, gate_location_candidates, cosmetic_side_classify |
| **P1 — major** | 7 | cold_runner_sizer, cooling_channel_path, slide_action_geometry, ejector_pin_layout_auto, apply_shrinkage_compensation, texture_region_apply, boss_design_check |
| **P2 — nice-to-have** | 2 | parting_line_step, hot_runner_sprue_interface |

Without the P0 set, the LLM cannot author a manufacturable injection-molded part —
it can only blindly add draft and split planes. The P1 set elevates the library from
"can draft a part" to "can specify a tool". The P2 set covers non-planar parting and
hot-runner vendor specifics.
