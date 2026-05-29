# DFM Gap Analysis — CNC Machining + Sheet Metal

Date: 2026-05-29
Author: deep-analysis agent (DFM domain)
Library version: ~150 registered skills (manifest.json at root)

## Scope

This analysis covers *manufacturability* of subtractive (CNC 3/5-axis) and
press-brake / stamping sheet-metal processes. It does NOT cover injection
molding or casting (separate domain analyses). Targets are mobile-product
components: watch case middle frames (CNC Al unibody), camera-deco rings
(CNC brass), USB/connector covers, hinge backplates, internal shielding cans
and brackets (stamped CRS / SUS), and EMI gaskets (stamped + bent).

## 1. Survey — what's already in the library

### 1.1 Sheet metal coverage (`modify_sheet/`)

11 skills currently registered:

| skill | what it does | gaps |
|---|---|---|
| `sheet_base` | flat rectangular blank from t / W / L | no material handle, no grain direction tag |
| `bend_edge` | round bend along axis-aligned edge | hard-coded `k_factor: 0.4` in `manufacturing.extras` — not parametric per material |
| `flange` | bend + flat extension on a boundary edge | no `min_flange_length` validator (≥ 4·T + R rule) |
| `hem` | folded-back edge | no open / closed / teardrom variants, no rolled hem |
| `tab_slot` | mating tab/slot pair | OK |
| `jog` | offset (Z-step) on a flat panel | OK |
| `dimple` | half-round emboss | OK |
| `louver` | vented louver | OK |
| `bend_relief` | rect / V relief at bend end | does NOT enforce auto-size = 1.5·T width × 1·T depth (AISC §13) |
| `unfold` | flat pattern via bend-allowance | `k_factor` is a single user arg — no per-bend material lookup (DIN 6935) |
| `sheet_base` | base sheet | no material code or thickness gauge code |

### 1.2 CNC-adjacent inspect coverage (`inspect/`)

23 inspect skills, the relevant ones:

- `find_features` — finds holes / pockets / fillets / chamfers heuristically. No undercut classification per setup direction.
- `inspect_geometry` — generic measure.
- `cross_section`, `section_multi_plane`, `section_at_centroid` — slice.
- `silhouette` — projects outline along a direction. **Closest existing skill to setup-planning** but does not return per-face visibility map.
- `hole_alignment_check`, `hole_to_hole_distance`, `edge_to_edge_distance` — geometry queries.
- `tolerance_stack` — 1D stack only, no GD&T per-feature stack-up.
- GD&T skills: flatness / circularity / cylindricity / parallelism / perpendicularity / position. Annotation only — they **tag** datums, they do not enforce achievability per process.
- `mass_properties`, `surface_area_by_region`, `curvature_map`, `mesh_quality`, `selector_preview`.

### 1.3 Hole / pocket coverage

- `hole`, `hole_array` — plain cylindrical hole, no head (countersink / counterbore / spotface), no tap, no through/blind sequencing for drilled-then-tapped.
- `helical_thread_internal` / `helical_thread_external` — produces the helix geometry. Does **not** model the tap-drill diameter convention (e.g., 75% thread engagement per ANSI/ASME B1.13M) or chamfer-for-tap-lead.
- `extrude_pocket` / `extrude_pocket_blended` — pocket geometry. No tool-radius corner enforcement.
- `extrude_through` — punch-through. OK.

### 1.4 Process catalogs (`catalogs/processes/`)

`cnc_3axis.yaml` has flat scalar rules:
```yaml
min_tool_radius_mm: 0.5
max_aspect_ratio_pocket: 6.0
undercut_allowed: false
```
`sheet_metal_stamp.yaml` has:
```yaml
min_bend_radius_factor: 1.0   # R / thickness ≥ 1.0
```

These are **declarative rules without enforcement skills** — i.e., the library
states "min_tool_radius = 0.5mm" but has no skill that *examines* a part and
reports which internal corners violate this. The DFM v0 in `manufacturing.md`
only describes ray-march wall thickness + draft + undercut for *molding*.
**For CNC / sheet-metal there is no analogous DFM check pipeline.**

### 1.5 Modify_finish

- `deburring` — auto-fillet sharp corners. Generic, not tied to CNC tool path.
- `final_fillet` — same.
- `sanding_pass`, `surface_finish_tag` — finish annotation.

No `chamfer_for_tap_lead`, no `deburr_edges_by_access_direction` (only deburr
edges visible from a setup direction).

---

## 2. Top Missing Capabilities

Ordered by manufacturing impact. Each entry is grounded in a real product
constraint observed on watch / phone / camera-module hardware.

### P0-1 — `inspect.tool_reachability` (5-axis setup planning)

**What:** read-only inspect skill. Given a part + a list of candidate setup
directions (`["+Z","-Z","+X","-X","+Y","-Y"]` or arbitrary unit vectors), return
per-face a `{face_id: [reachable_from_directions]}` map and flag faces
reachable from zero directions = **structural undercut**. Uses ray-cast from
sample points along each direction outward to test occlusion by the same body.

**Args:** `setup_directions: list[Vec3]`, `samples_per_face: int = 32`,
`ray_offset_mm: float = 0.01`.

**Why:** When designing a watch middle-frame in Al-6061 (e.g., Galaxy Watch
Classic bezel), the planner needs to know whether the speaker grille slot on
the underside can be reached by an end-mill from -Z, or whether re-fixturing
to a side setup is needed. This drives both cost and whether 5-axis is
required. Today the library has `silhouette` (projects outline) but no
*per-face reachability* answer.

**Standard:** ASME B5.54 / ISO 230 machine capability terminology; classical
"line-of-sight from setup vector" predicate in CAM literature (e.g.,
Mastercam / hyperMILL accessibility analysis).

**Priority:** P0 — this gates the choice between 3-axis vs 5-axis vs DMU and
fundamentally changes BOM cost.

**OCCT difficulty:** moderate. Sample face normals, ray-cast against the same
body with `BRepIntCurveSurface_Inter`. The faces are already there; just need
a sample+ray harness.

---

### P0-2 — `inspect.cnc_undercut_check` (setup-direction undercut)

**What:** Identify faces whose normal has negative dot product with **every**
candidate setup direction in a fixed 3-axis setup list (i.e., not reachable
by a straight-tool plunge from any candidate). Returns face IDs + severity
score = magnitude of the worst dot product.

**Args:** `setup_directions: list[Literal["+X","-X","+Y","-Y","+Z","-Z"]]`,
`tolerance_deg: float = 5.0`.

**Why:** Distinct from `tool_reachability` (which checks *occlusion* by other
body parts). This one is the **geometric undercut definition** — face normal
pointing away from every allowed approach. Example: an O-ring groove on the
inside of a watch back-cover cannot be cut with a 3-axis setup from ±Z;
either the part is flipped and re-fixtured, or a slot-mill (T-cutter) is
needed, or the design is non-manufacturable on 3-axis. Today only
`modify_mold/parting_surface` cares about undercuts, and only for ejection.

**Standard:** Geometric definition used in ISO 14649 (STEP-NC) feature
recognition for milling.

**Priority:** P0 — without this, planner emits CNC plans that need impossible
setups.

**OCCT difficulty:** trivial. Face normal at center vs unit vector list.

---

### P0-3 — `modify.enforce_min_tool_radius` (auto-fillet internal corners)

**What:** Modifier that takes a part + a tool radius and **automatically
fillets every concave internal vertical edge** whose adjacent face dihedral
is < 180° (concave) with radius ≥ tool R. Equivalent to "round all internal
corners to the tool diameter" — a hard CNC rule because end-mills cannot cut
a sharp internal corner.

**Args:** `tool_radius_mm: float`, `setup_axis: Literal["+Z","-Z","+X","-X","+Y","-Y"]`
(only edges parallel to this axis are eligible — vertical edges relative to
the tool), `skip_edges: SelectorRef | None`.

**Why:** Classic CNC DFM rule. A 0.5 mm slot in the watch crown housing
cannot terminate in a sharp internal corner — the end-mill leaves a
tool-radius radius at the corner. Designers commonly forget; library should
auto-correct. Today `final_fillet` fillets *all* sharp edges with a single R
but does not understand "vertical" (tool-aligned) vs "horizontal" (floor),
and does not check the tool radius against the catalog `min_tool_radius_mm`.

**Standard:** Long-established CNC DFM convention; e.g., Protolabs CNC design
guidelines § "Internal radii"; ISO 13399 tool catalog.

**Priority:** P0.

**OCCT difficulty:** moderate. Edge classification (concavity, axis-alignment
relative to setup axis), then existing `fillet_predicate` does the work. New
skill is essentially a smart wrapper.

---

### P0-4 — `inspect.pocket_aspect_ratio_check`

**What:** For every detected pocket (uses existing `find_features`), compute
depth / smallest-cross-section-radius and flag pockets where
`depth > 4 × tool_diameter` (deflection / chatter risk) per the
`max_aspect_ratio_pocket` catalog rule (currently set to 6.0 for cnc_3axis).
Report face IDs + computed ratio + recommended tool diameter to satisfy 4×
rule.

**Args:** `max_aspect_ratio: float = 4.0`, `min_tool_diameter_mm: float = 1.0`.

**Why:** Camera-module CNC plates routinely have deep narrow IR-filter
recesses. If depth > 4 × tool Ø, the end-mill chatters and the floor finish
fails Ra. Designers need an automated lint. The catalog already declares the
rule but no skill enforces it.

**Standard:** Machining Handbook §16 "L:D ratio for end mills"; Sandvik /
ISCAR application guides.

**Priority:** P0.

**OCCT difficulty:** moderate. Requires the existing pocket detector + a
medial-axis-or-inscribed-circle approximation for the pocket cross-section.
For axis-aligned rectangular pockets it's trivial.

---

### P1-5 — `modify.drilled_then_tapped_hole` (sequence-aware tap hole)

**What:** Macro skill that emits **two** holes: a tap-drill diameter blind or
through-hole + an internal thread of the requested major Ø, sized so that
the tap-drill = `major_dia – pitch × 1.0825 × 0.75` (75% thread engagement,
ANSI/ASME B1.13M). Optionally adds a 90° countersink chamfer at the entry
(tap lead, typically 1.2 × pitch deep). Tags the hole with metadata
`{drill_dia, thread_dia, pitch, length_drilled, length_tapped}` so downstream
process planning knows the operation sequence.

**Args:** `position: Vec3`, `thread_spec: Literal["M2","M2.5","M3","M4","M5","M6"]`,
`tapped_depth_mm: float`, `drilled_depth_mm: float | None` (default
tapped + 2 × pitch), `add_chamfer_for_lead: bool = true`,
`through: bool = false`.

**Why:** Tapped holes in Al middle-frames for screw fastening are universal
in mobile teardowns. The library has `helical_thread_internal` but that
needs the user to know the tap-drill size and add a chamfer manually — the
LLM frequently gets it wrong. A macro that consumes only the *thread spec*
matches how machinists actually think.

**Standard:** ANSI/ASME B1.13M (metric coarse), DIN 13-1, ISO 965-1.

**Priority:** P1 — frequent, important, and currently error-prone.

**OCCT difficulty:** trivial. Two `Hole` calls + one `chamfer` call.

---

### P1-6 — `modify.countersink_hole` and `modify.counterbore_hole`

**What:** Two atomic skills. Countersink = conical recess for flat-head
screw, parameterized by major dia, angle (typically 82° / 90° / 100°), small
dia. Counterbore = cylindrical recess for socket-head cap screw,
parameterized by counterbore Ø, counterbore depth, through-hole Ø.

**Args (countersink):** `position`, `direction`, `through_hole_dia_mm`,
`csk_dia_mm`, `csk_angle_deg: Literal[82,90,100,118]`.
**Args (counterbore):** `position`, `direction`, `through_hole_dia_mm`,
`cbore_dia_mm`, `cbore_depth_mm`.

**Why:** Every screw on a watch back-cover / camera-deco-ring / smartphone
mid-frame is either flat-head countersunk (cosmetic, flush) or SHCS
counterbored. Today the library makes the LLM compose `hole + revolve_pocket`
manually with two-feature DOF. A first-class atomic with the right
parameters maps to ISO 7721 (csk) and ISO 4762 (SHCS) standard parts.

**Standard:** ISO 7721 (flat-head countersink), DIN 974-1 (counterbore for
hex socket head), ISO 4762 / DIN 912 (SHCS host).

**Priority:** P1.

**OCCT difficulty:** trivial. Cone primitive + cylinder primitive, subtract.

---

### P1-7 — `inspect.access_clearance_check` (feature-to-feature collision for tool/spindle)

**What:** For each existing feature (hole, pocket, boss), report the
minimum clearance to the next feature along each setup direction, plus the
required spindle nose / collet clearance (default 12 mm Ø). Flags features
where a standard ER16 / HSK25 collet hits an adjacent boss.

**Args:** `setup_directions`, `collet_diameter_mm: float = 12.0`,
`collet_clearance_mm: float = 0.5`.

**Why:** A common failure mode on watch lugs: two M2 tapped holes ~3 mm
apart, but the tool's tool-holder is 8 mm Ø — the second hole can't be
machined without re-orienting. Library has `hole_to_hole_distance` (returns a
number) but does not compute *tool-side* clearance.

**Standard:** ISO 15488 collet dimensions; DIN 6499 ER collets.

**Priority:** P1 — caught late in CAM, expensive design churn.

**OCCT difficulty:** moderate. Sweep collet cylinder along approach axis,
boolean-intersect with body, report.

---

### P1-8 — `modify.k_factor_by_material` lookup + parametric `bend_edge`

**What:** Replace the hard-coded `k_factor: 0.4` in `bend_edge.manufacturing.extras`
with a per-material lookup. Add `material_code` arg to `bend_edge` / `flange` /
`hem`. K-factor curve per DIN 6935 (German press-brake standard) varies with
material and inner radius / thickness ratio:

| Material | R/T = 0.5 | R/T = 1 | R/T = 2 | R/T = 5 |
|---|---|---|---|---|
| Al 5052-H32 (soft) | 0.33 | 0.38 | 0.43 | 0.48 |
| CRS SPCC (mild) | 0.36 | 0.41 | 0.45 | 0.50 |
| SUS 304 (hard) | 0.40 | 0.45 | 0.48 | 0.50 |

**Args:** add `material_code: Literal["Al5052","Al6061","CRS","SUS304","SUS316","C1100"]`
to `bend_edge`, `flange`, `hem`, `unfold`. Internal lookup table.

**Why:** Unfolded flat patterns are how the press shop quotes — wrong
K-factor → wrong blank → scrap. Galaxy Watch back cover (SUS 304) needs
K=0.45 ish; a Pixel mid-frame bracket (Al 5052) needs K=0.38. Hard-coding 0.4
is wrong for both at the extremes.

**Standard:** DIN 6935 (Kalt-Biegen flacher Erzeugnisse), VDI 3389.

**Priority:** P1.

**OCCT difficulty:** trivial. Just a lookup-table arg expansion.

---

### P1-9 — `inspect.min_flange_length_check` + auto-extend

**What:** Inspect that finds every flange in the body and reports flanges
where `flange_length < 4 × thickness + bend_radius` (the press-brake minimum
beyond which the flange can't be held against the die). Optionally
auto-extend to the minimum.

**Args:** `safety_factor: float = 1.0`, `auto_fix: bool = false`.

**Why:** Shielding cans for camera modules have many narrow flanges. If a
flange is too short, the press brake either tears it off or the part skips
on the die. The library's `flange` skill accepts any positive length; there
is **no validator**.

**Standard:** Bohler / SSAB press-brake handbooks; Air-Bend tooling minimum
flange = die-V-opening × 0.5 + R + T (industry rule of thumb ≈ 4T + R).

**Priority:** P1.

**OCCT difficulty:** moderate (locate flanges) + trivial (numeric compare).

---

### P1-10 — `inspect.hole_to_bend_distance_check`

**What:** For every hole on a sheet panel, compute the minimum distance from
the hole's nearest edge to the **start of the bend region** (the tangent
line where the toroidal sector begins). Flag holes that distort during
bending — rule: `distance ≥ 2 × thickness + bend_radius` (Smith — Sheet Metal
Forming Processes and Equipment).

**Args:** `safety_factor: float = 1.0`.

**Why:** Punched holes near a bend line oval out during forming because the
material flows. Connector cut-outs in stamped phone shielding routinely
violate this and need rework. Library detects holes (`find_features`) and
detects bend cylinders (`unfold` does it) but never cross-checks the two.

**Standard:** Stamping Handbook §7, KS B 0102 (KR) for ferrous; common
3T rule for non-ferrous.

**Priority:** P1.

**OCCT difficulty:** moderate (locate bend tangent lines on the toroidal
face) + trivial (numeric distance).

---

### P1-11 — `inspect.bend_relief_required_check` + auto-relief sizing

**What:** Find any bend whose end terminates *inside* the sheet (not at the
panel boundary) and verify a relief slot exists with `width ≥ 1.5 × T` and
`depth ≥ 1 × T`. Auto-generate if missing.

**Args:** `auto_fix: bool = false`, `relief_shape: Literal["rect","v"] = "rect"`.

**Why:** This is the missing automation for the existing `bend_relief`
atomic — currently the LLM has to remember to call `bend_relief` at every
internal bend terminus. Misses → tearing in production. SUS shielding cans
fail at this constantly.

**Standard:** AISC sheet-metal forming guidelines § "Notch reliefs";
SMF (Sheet Metal Forming Equipment & Tooling Ass'n) standard.

**Priority:** P1.

**OCCT difficulty:** moderate (find bend terminus on edge interior).

---

### P2-12 — `inspect.spring_back_compensate` (bend angle correction)

**What:** Given bend radius, thickness, material yield strength and bend
angle, compute the over-bend angle needed to land at target after spring-back.
Modify the existing `bend_edge` to apply the over-bend automatically when
`compensate_springback: bool` is set.

**Args:** `target_angle_deg`, `material_code`, `tooling: Literal["air","bottom","coining"] = "air"`.

**Why:** A 90° bend in SUS 304 (yield 215 MPa) air-bent to a 1.5 mm radius
springs back ~2-4° — the press shop programs ~92° to land at 90°. Designers
asking for "90°" in the CAD model are technically wrong; the *flat pattern*
result the CNC turret needs depends on the spring-back compensation.

**Standard:** ASTM E290 bend testing; FEA-derived charts in DIN 6935 Annex C;
Boljanovic — *Sheet Metal Forming Processes and Die Design* Ch. 11.

**Priority:** P2 — nice to have, only matters when generating the actual
flat-pattern DXF for production.

**OCCT difficulty:** trivial (table lookup × angle adjustment).

---

### P2-13 — `inspect.grain_direction_check` for bends

**What:** Sheet stock comes with a rolling-grain direction. Bends **parallel
to the grain** are prone to cracking in hard tempers (Al 5052-H38, SUS 304
tempered). This inspect skill takes the sheet's grain axis and reports any
bend whose axis is parallel (< 30°) — flag as risk.

**Args:** `grain_axis: Literal["X","Y"]`, `risk_threshold_deg: float = 30.0`.

**Why:** SUS 304 1.4-temper used in micro-shielding cracks at parallel-grain
bends with R < T. Conventional rule: bend perpendicular to grain, OR
use R ≥ 2T for parallel-grain bends.

**Standard:** ASM Metals Handbook Vol. 14B § "Bendability"; ASTM E290.

**Priority:** P2.

**OCCT difficulty:** trivial (axis-vs-axis dot product).

---

### P2-14 — `modify.coined_bend` / `modify.bottom_bend` variants

**What:** Currently `bend_edge` produces a single bend geometry (air-bent
nominal). Add variants for coining (final R = punch nose R, no spring-back,
R / T can go below 1.0) and bottoming (R = die radius, no spring-back). These
affect the *final geometry* slightly and dramatically change cost.

**Args:** existing `bend_edge` + `bend_type: Literal["air","bottom","coining"]`.

**Why:** Coined bends are required when R/T < 1.0 (e.g., a 0.4 mm radius
bend in 0.5 mm SUS). The library currently accepts any R but the *catalog*
says `min_bend_radius_factor: 1.0`. The bend type changes the constraint.

**Standard:** SME *Die Design Handbook* 4th ed. Ch. 8.

**Priority:** P2.

**OCCT difficulty:** trivial (parameter expansion).

---

### P2-15 — `inspect.deep_pocket_corner_check` (vertical-floor fillet)

**What:** For pockets deeper than `2 × tool_diameter`, the floor must have
a fillet between floor and wall ≥ ball-end-mill radius (otherwise floor must
be machined with a flat end-mill leaving a sharp 90° internal corner that is
impossible to clean). Skill reports pockets violating this.

**Args:** `tool_diameter_mm`, `min_floor_corner_radius_mm`.

**Why:** Watch crown pocket interiors (deep narrow recess for the crown
spring) are typically machined with a 1 mm ball-end at the floor; the
floor-wall transition needs a 0.5 mm fillet. Designers omit and machinist
catches in CAM.

**Standard:** Sandvik machining handbook; same family as P0-3.

**Priority:** P2.

**OCCT difficulty:** moderate.

---

## 3. Cross-cutting infra gaps

These are not new skills but shared infrastructure the skills above depend
on. Without them, the skills above will be implemented as copy-pasted
heuristics — same problem the existing `unfold`/`find_features` have today.

1. **Setup-direction concept (`SetupAxis` type)**. The library has
   `Literal["+X","-X","+Y","-Y","+Z","-Z"]` repeated in many skills but no
   typed concept of *setup direction list* with associated cost (re-fixturing
   penalty per flip). Several P0 skills need this as a first-class arg.

2. **Material catalog (`catalogs/materials/*.yaml`)** with min bend R,
   K-factor table (R/T → K), Young's modulus, yield, grain rule. Currently
   `manufacturing.extras` is a free-form dict in each skill — material data
   is duplicated and inconsistent.

3. **Tool catalog (`catalogs/tools/*.yaml`)** with min radius, max length,
   collet Ø. Today `min_tool_radius_mm: 0.5` is just a number in the process
   YAML; can't reason about specific tools.

4. **Feature → process operation mapping** in manifest. Today
   `manufacturing` is a per-skill `{process: rules}` dict. There's no
   reverse map "this hole feature corresponds to a `drill + tap + chamfer`
   operation sequence" used by the LLM planner.

5. **Face / edge concavity helper**. Several proposed skills need
   "is this edge concave or convex?" — currently each skill rolls its own
   dihedral-angle calculation (see `deburring.py`). Lift it to
   `_resolvers.py`.

6. **Bend tangent-line locator**. `unfold.py` scans cylindrical faces and
   discards their tangent lines after computing bend allowance. For hole-to-
   bend distance and bend-relief checks, we need to expose the tangent lines
   as queryable entities.

7. **Per-bend metadata on `bend_edge` / `flange` / `hem` history**. Today
   `bend_arc_face` is tagged but the bend's R, T, angle, material is not
   stored on the entity. Downstream inspects re-derive from geometry every
   time.

---

## 4. Domain-specific catalogs needed

1. **`catalogs/materials/`** — per-material K-factor table, min bend R curve
   vs R/T, grain rule, yield strength.
   - Files: `Al_5052.yaml`, `Al_6061.yaml`, `CRS_SPCC.yaml`, `SUS_304.yaml`,
     `SUS_316.yaml`, `Cu_C1100.yaml`, `brass_C2680.yaml`.

2. **`catalogs/tools/`** — end-mill / drill / tap library with Ø, length,
   flutes, helix. Drives `enforce_min_tool_radius`, `pocket_aspect_ratio_check`,
   `access_clearance_check`.

3. **`catalogs/standard_holes/`** — per-thread-spec tap-drill table, head
   countersink table (ISO 7721), counterbore table (DIN 974-1).

4. **`catalogs/sheet_gauges/`** — preferred thicknesses by region (US AWG,
   metric DIN, JIS) — designers tend to spec "1mm sheet" but production uses
   the nearest standard gauge.

---

## 5. Examples — real product scenarios

1. **Galaxy Watch Classic bezel (Al 6061 CNC unibody)**. Today's library can
   model the OD, OD chamfer, sapphire seat, lug pockets. It cannot verify
   that the underside of the crown housing is reachable from -X (without a
   re-fixture) or that the 0.4 mm slot for the click-detent has the proper
   1 mm tool-radius corner. → P0-1, P0-2, P0-3.

2. **Galaxy phone middle-frame screw bosses**. Tapped M1.6 holes spaced
   2.5 mm apart. Library generates the threads but does not check that an
   ER11 collet (10 mm Ø) clears the adjacent boss. → P1-7.

3. **Pixel Watch back cover (SUS 304 stamped)**. The library bends the dome
   but uses K=0.4 — should be K=0.42-0.45 for SUS. Unfolded blank is
   ~0.3 mm wrong, scrapped lot. → P1-8.

4. **Camera-module shielding can (CRS 0.3 mm, multiple bends)**. Internal
   bend ends without relief tear during stamping. Currently the LLM has to
   manually invoke `bend_relief` at every internal terminus. → P1-11.

5. **Hinge backplate (SUS 1.0 mm)**. Connector cut-out 1.2 mm from the bend
   line ovals during forming. → P1-10.

6. **Watch crown spring pocket (Al, 5 mm deep, 1.5 mm Ø)**. depth/Ø > 3 → at
   the threshold of CNC limits but not flagged. → P0-4.

7. **Mid-frame SHCS counterbore for M2 screws**. Currently composed as `hole
   + revolve_pocket` by the LLM — ~30% of attempts produce wrong cbore depth
   (per anecdotal logs in `docs/reports`). → P1-6.

8. **Camera deco ring (brass C2680, CNC)**. Has cosmetic flat-head screw
   countersinks visible from outside. Today modeled with `hole + cone
   subtract`. The flat-head ISO 7721 angle is 90° — easy to get wrong. → P1-6.

---

## 6. Summary

Library has solid **geometry primitives** for sheet-metal forming (bend,
flange, hem, dimple, louver, jog, tab-slot, unfold, bend-relief), and for
CNC subtractive shapes (pockets, holes, threads, fillets, chamfers). The
**DFM enforcement layer is missing** for both processes — the library
declares rules in YAML catalogs but has no inspect skills that *cross-check
designed geometry against those rules*. For sheet metal specifically, the
K-factor is hard-coded, material is not a parameter, and the standard
production gotchas (hole-to-bend distance, min flange length, automatic
bend relief, spring-back) are entirely absent.

The 15 entries above, plus the 4 supporting catalogs, would close the gap
between "produces correct geometry" and "produces *manufacturable* geometry"
for the two highest-volume mobile-product processes.
