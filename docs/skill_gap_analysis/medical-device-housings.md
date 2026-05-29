# Skill Gap Analysis — Medical Device Housings

Domain: handheld diagnostic instruments, body-worn (wearable) monitors, and
implantable-adjacent housings (charging pucks, programmer wands, surgical
console enclosures).

Author: Deep-analysis subagent
Date: 2026-05-29

---

## 1. Survey — what the current library already covers

The library has ~150 skills and several of them already touch this domain. The
relevant existing assets, grouped by what aspect of medical device design they
help with:

### Sealing / waterproofing primitives
- `modify_boss.o_ring_groove` — circular O-ring groove on a planar +Z/-Z face
  only, depth/inner/outer diameter. v1 is the only profile (rectangular).
- `modify_curvature.shell_variable_thickness`, `surface_offset` — shell + offset
  walls (useful for housing skin).
- `modify_curvature.face_face_fillet`, `fillet_predicate`, `variable_radius_fillet`
  — good toolbox for blending internal corners.

### Snap / fastener primitives
- `modify_boss.snap_hook` — box-cantilever + box-lip, axis-aligned only (no
  return shelf, no anti-overstress stop).
- `modify_boss.heat_stake_boss` — shaft + chamfer crown.
- `modify_boss.boss_with_hole`, `crown_shaft_hole`, `standoff`, `mounting_pad`
  — generic boss family. `lug_pair` for hinge eyes.
- `modify_boss.hinge_pin_boss` — cylinder + axial through + radial notches.

### Cable / strain relief
- `modify_boss.cable_clip` — L-clip with overhanging lip (undercut — flagged
  for side-action mold).
- `modify_pocket.cable_routing_channel` — semi-circular swept groove along
  polyline.

### Surface finish / metadata
- `modify_finish.surface_finish_tag` — Ra (µm) metadata tag on faces; supports
  polish / matte / texture_a / texture_b. Stored in `body._pd_finish`.
- `modify_finish.deburring`, `sanding_pass`, `final_fillet` — convex corner
  rounding passes.

### Vents
- `modify_pocket.breathing_hole_array` — hex-packed through-hole array clipped
  to a 2D region sketch on planar +Z/-Z face.
- `modify_pocket.grille_pattern`, `honeycomb_pattern`.

### Inspect / verification
- `inspect.gdt_flatness`, `gdt_parallelism`, `gdt_perpendicularity`,
  `gdt_position`, `gdt_cylindricity`, `gdt_circularity` — GD&T toleranceing.
- `inspect.tolerance_stack` — stack-up analysis.
- `inspect.hole_alignment_check`, `hole_to_hole_distance`.
- `inspect.interference_check` (assembly).

### Mold prep
- `modify_mold.draft_apply_auto`, `parting_surface`, `core_cavity_split`,
  `ejector_pin_clearance`.

### Assembly
- `assembly.fastener_array`, `bom_extract`, mates (planar, axis, concentric,
  at_distance), `interference_check`.

### Sheet metal (for instrument cart panels, console chassis)
- `modify_sheet.sheet_base`, `bend_edge`, `bend_relief`, `flange`, `hem`,
  `tab_slot`, `dimple`, `louver`, `jog`, `unfold`.

### What's missing entirely
- No gasket-aware "matched seal land" pair generator (top + bottom mating land
  with controlled compression).
- No IPx-rated drainage/equalization vent skill (Gore-style vent inserts).
- No captive-screw / tethered-screw fastener skill.
- No membrane-keypad bonding step / overlay recess skill.
- No biocompatibility / patient-contact material region tag.
- No autoclave-compatible flat-seal skill (controlled Ra + flatness coupled).
- No "no-biofilm-trap" auto-fillet predicate (R ≥ 1.0 mm enforced on
  internal corners of patient-contact surfaces).
- No connector cutout from a parametric catalog (DIN 41612, IEC 60603-2,
  Lemo, push-pull medical bayonet).
- No strain-relief boot / flex-relief generator on cable exit.
- No drop-resistant rib lattice on the inside of a housing wall.

---

## 2. Top missing skills (prioritized)

| # | Skill | Priority | Difficulty |
|---|---|---|---|
| 1 | `o_ring_groove_dovetail_path` | P0 | moderate |
| 2 | `face_seal_land_pair` | P0 | hard |
| 3 | `cleanability_radius_enforce` | P0 | moderate |
| 4 | `captive_screw_tether_pocket` | P0 | moderate |
| 5 | `ipx_vent_membrane_pocket` | P1 | moderate |
| 6 | `membrane_keypad_recess` | P0 | moderate |
| 7 | `cable_strain_relief_boot` | P1 | hard |
| 8 | `connector_cutout_from_catalog` | P0 | moderate |
| 9 | `biocompat_region_tag` | P0 | trivial |
| 10 | `autoclave_flat_seal_pair` | P1 | moderate |
| 11 | `drop_rib_lattice_inner_wall` | P2 | moderate |
| 12 | `tether_loop_anchor` | P2 | trivial |
| 13 | `equalization_diaphragm_pocket` | P2 | moderate |

Details below.

---

### #1. `o_ring_groove_dovetail_path` — P0, moderate

**What:** Sweep an O-ring groove (rectangular OR dovetail profile) along an
arbitrary planar path (rounded-rectangle, racetrack, custom polyline) on a
planar or gently curved face. v2 of the existing `o_ring_groove`.

**Pydantic args:**
- `face_selector: SelectorRef`
- `path_sketch: SketchSpec | list[tuple[float, float]]`
- `cord_diameter_mm: float` (O-ring nominal cross section, AS568 family)
- `gland_depth_mm: float`
- `gland_width_mm: float`
- `profile: Literal["rectangular", "dovetail", "trapezoidal_15deg", "half_round"]`
- `corner_radius_mm: float` (groove path corners, must be ≥ 3× cord diameter
  per Parker O-ring manual)
- `squeeze_pct: float` (for inspection / DFM report)

**Why it matters:** A typical handheld glucometer or pulse-ox housing has a
*rectangular* (rounded-rect) parting line, not circular. The existing
`o_ring_groove` is hardcoded to a single circular ring centered on a face,
which can't model the seal of a clamshell housing. Dovetail profile is
specifically used in service-replaceable seals (medical sensors) because the
O-ring is retained on disassembly.

**Standard:** ISO 3601-2:2016 (housings & dimensions). Parker O-Ring Handbook
ORD 5700 for gland sizing, AS568 dash numbers for cord. Dovetail per ISO 3601-2
Annex C. Squeeze 15-25 % for static face seal.

**Why a separate skill (not generalize v1):** The existing v1 takes
`inner_diameter / outer_diameter` (circular), so its arg surface is incompatible
with a path-swept groove. Cleaner as a sibling than a refactor.

---

### #2. `face_seal_land_pair` — P0, hard

**What:** Generate a paired sealing land on TWO mating bodies: a flat (or
shallow tongue-and-groove) land on body-A and the matching pocket / counter-land
on body-B, with controlled compression gap, lead-in chamfer, and an internal
"crush-stop" rib that prevents gasket over-compression.

**Pydantic args:**
- `body_a_selector / body_b_selector`
- `path_sketch: SketchSpec`
- `gasket_thickness_mm: float`
- `target_compression_pct: float` (typ 25-35 % for closed-cell silicone)
- `land_width_mm: float` (≥ 1.5× gasket thickness)
- `crush_stop_height_mm: float` (set so compression cannot exceed
  `gasket_thickness * (1 - max_compression_pct)`)
- `lead_in_chamfer_mm: float` (for assembly self-centering)

**Why:** Wearable continuous-glucose-monitor (CGM) base + transmitter, infusion
pump door, ultrasound probe housing seam — all use compressed-flat-gasket
seals, NOT O-rings, because the gasket must also conform to a contoured path
that the O-ring can't navigate. The crush-stop is the most-commonly-omitted
feature in junior designs and the #1 cause of seal leaks (over-compression →
permanent set → leak).

**Standard:** IEC 60529 IPX7/IPX8 ingress test envelope.
Gasket compression: ISO 3601-1 for elastomer, internal industrial practice for
crush-stop ratio (gasket vendors specify 25-35 %).

**Difficulty:** Hard — requires composing on TWO bodies and emitting a
paired-result (most skills here mutate ONE body). May need a new compound
output type or a wrapper that runs twice.

---

### #3. `cleanability_radius_enforce` — P0, moderate

**What:** Predicate-based fillet that enforces a *minimum* internal corner
radius (default 1.0 mm) on every internal (concave) edge of a region selector,
to eliminate biofilm/bioburden traps. Reports which edges were too small,
which were enlarged, which couldn't fit the radius (interference). Inverse of
`final_fillet` — it's a **policy check + auto-remediation** rather than a
finishing pass.

**Pydantic args:**
- `region_selector: SelectorRef` (often a tagged `patient_contact` region)
- `min_internal_radius_mm: float = 1.0`
- `mode: Literal["enforce", "report_only", "best_effort"]`
- `tolerance_um: float = 50.0`

**Why:** FDA Reusable Medical Device cleaning guidance (2015) and AAMI TIR12
require internal corner R ≥ 0.5 mm minimum and R ≥ 1.0 mm for blood-contact
surfaces. Endoscope handles, surgical tray cassettes, anesthesia rebreather
manifolds. Current `final_fillet` only acts on sharp convex edges and doesn't
enforce a minimum — it picks a single global radius.

**Standard:** AAMI TIR12:2010 §5.3 (design for cleanability), AAMI ST79 for
sterilization-compatible geometry. ISO 17664. ISO 19227 (orthopedic implant
cleanliness).

**OCCT:** Edge-concave classification already exists in `sanding_pass`'s
helpers. Reuse + a min-radius geometric test on adjacent face curvatures.

---

### #4. `captive_screw_tether_pocket` — P0, moderate

**What:** Compound feature: cylindrical screw clearance pocket + circumferential
retention undercut + axial keeper-ring shelf, such that an inserted captive
screw cannot fall out even when unthreaded. Pairs with the captive screw
catalog (PEM PF11/PF50, Southco 47/48 series).

**Pydantic args:**
- `face_selector: SelectorRef`
- `screw_catalog_id: Literal["pem_pf11_m3", "pem_pf50_m3", "southco_47_m3", ...]`
- `panel_thickness_mm: float`
- `retention_method: Literal["snap_groove", "press_fit_collar", "swage_ring"]`
- `tether_length_mm: float | None` (if a lanyard tether is required)
- `lock_screw: bool = True` (whether mating thread is required)

**Why:** Battery-compartment door on a defibrillator (AED) must be captive —
ANSI/AAMI DF80 requires the door cannot be detached and lost during a code.
Same requirement on intraoperative monitor batteries (IEC 60601-1-12 home-use
medical). The existing `boss_with_hole` and `mounting_pad` make a hole but
don't model the *retention* geometry.

**Standard:** ANSI/AAMI DF80 §6.2, IEC 60601-1-12 §6.6, PEM catalog (PF11/PF50),
Southco 47/48 captive-fastener datasheets. ISO 4762 for the mating thread.

---

### #5. `ipx_vent_membrane_pocket` — P1, moderate

**What:** Recessed pocket on an external face sized for an adhesive-mounted
ePTFE pressure-equalization membrane (Gore PMF series, Donaldson Tetratex),
plus a corresponding through-hole pattern beneath, an annular bond-area land,
and a manufacturing tag specifying membrane part number and adhesive system.

**Pydantic args:**
- `face_selector`
- `membrane_catalog_id: Literal["gore_pmf100487", "gore_pmf200652", "donaldson_tx5613", ...]`
- `position_xy: tuple[float, float]`
- `recess_depth_mm: float` (to keep membrane flush/below for impact protection)
- `back_hole_diameter_mm: float`
- `back_hole_count: int`
- `back_hole_pattern: Literal["single", "ring", "hex_array"]`
- `bond_land_width_mm: float` (≥ 1.0 mm typ)

**Why:** Every IPX7 handheld with a battery (LiPo outgases on heat) needs a
vent — without one the housing flexes and inhales water at the seal as it
cools after sterilization. Gore PMF datasheets specify recess depth and
bond-land width. AED housings, surgical handpieces, surgical-robot endoscope
cameras all use this exact construction. Currently the LLM would compose this
from `extrude_pocket` + `hole` + tag, which loses the catalog spec.

**Standard:** IEC 60529 IPX7/IPX8, Gore PMF design guide DI-PMF-2022.

---

### #6. `membrane_keypad_recess` — P0, moderate

**What:** A shallow rectangular recess (10-200 µm typ) bounded by a precise
land for bonding a polyester/polycarbonate membrane keypad overlay (3M 467MP
adhesive), with optional embossed tactile-key bosses BELOW the membrane and
LED light-pipe through-holes. Membrane bond area must be flat to ≤ 0.05 mm
total runout and Ra ≤ 1.6 µm (no machining marks across the seal).

**Pydantic args:**
- `face_selector`
- `outline_sketch: SketchSpec`
- `recess_depth_mm: float = 0.15` (membrane thickness)
- `bond_land_width_mm: float = 2.0`
- `tactile_dome_centers: list[tuple[float, float]]` (button locations)
- `dome_diameter_mm: float`
- `dome_height_mm: float = 0.2`
- `lightpipe_diameter_mm: float | None`
- `flatness_target_um: float = 50`  (drives `gdt_flatness` callout)
- `bond_face_ra_target_um: float = 1.6`

**Why:** Patient monitors, infusion pumps, ventilators all use membrane
keypads because they're cleanable. The recess must be *exactly* the membrane
thickness so the bond face is flush — too deep and the overlay tents and
delaminates, too shallow and the membrane stands proud and gets caught.
Currently the LLM would compose this as a generic `extrude_pocket` and miss
the bond-land + flatness coupling.

**Standard:** 3M 467MP/468MP membrane keypad bonding TS-26. UL 50E for sealed
keypad enclosures. ISO 80369 connector clearance for medical button cutouts.

---

### #7. `cable_strain_relief_boot` — P1, hard

**What:** Generate a tapered, ribbed flexible boot on a cable exit, with
controlled stiffness gradient (root-to-tip wall taper, optional helical
relief slot, captured anti-pull-out collar to grip the cable jacket).

**Pydantic args:**
- `exit_face_selector`
- `cable_outer_diameter_mm: float`
- `boot_length_mm: float`
- `root_outer_diameter_mm: float`
- `tip_outer_diameter_mm: float`
- `wall_root_mm: float = 1.5`
- `wall_tip_mm: float = 0.6`
- `relief_pattern: Literal["concentric_ribs", "helical_slot", "bellows"]`
- `pull_out_collar: bool = True`
- `material_durometer_shore_a: int` (drives stiffness report)

**Why:** ECG cables, SpO2 sensor cables, ultrasound transducer cables all
fail at the strain relief — IEC 60601-1 §15.4.4 requires 60 N pull without
damage and 10× drop cycles without conductor breakage. The boot is typically
TPE over-molded onto the housing exit. Current cable_clip / cable_routing
skills don't model the *external* relief.

**Standard:** IEC 60601-1 §15.4.4 (cable mechanical strength),
IEC 60601-1-11 §10.1.2 (home use). UL 1581 §1080 (strain relief).

**Difficulty:** Hard — variable-taper sweep with optional helical sub-feature.
Requires composing `swept_boss_along_curve` + `variable_radius_fillet_with_law`
or a new primitive.

---

### #8. `connector_cutout_from_catalog` — P0, moderate

**What:** A catalog-driven cutout for a known medical/industrial connector. Caller
specifies a catalog ID; the skill emits the panel cutout, the mounting-screw
pattern, the keep-out volume for the connector body, and a tag for the back-side
pin clearance. Supports DIN 41612, IEC 60603-2, Lemo 0B/1B/2B, Redel SP/SR,
ODU MEDI-SNAP, Fischer push-pull, USB-A/B/C with sealing flanges.

**Pydantic args:**
- `face_selector`
- `connector_id: Literal["din41612_b32", "iec60603_2_dc37", "lemo_1b_5pin", "lemo_2b_8pin", "redel_sp_10pin", "odu_medi_snap_4pin", "fischer_103_a060", "usb_c_panel_sealed", ...]`
- `position_xy: tuple[float, float]`
- `orientation_deg: float`
- `keepout_behind_mm: float | None` (auto from catalog if None)
- `seal_to_panel: bool = True` (adds O-ring groove around cutout)

**Why:** Every patient-monitor backshell has a DC37 (IEC 60603-2) bus going
to the chassis backplane. Every endoscope handpiece has a Lemo or Redel
connector. Every neonatal incubator probe has an ODU MEDI-SNAP. Designers
currently look up the datasheet manually and translate to `extrude_through` —
error-prone (a 0.2 mm cutout error = no fit). A catalog table eliminates this.

**Standard:** DIN 41612, IEC 60603-2, Lemo design catalog, ODU MEDI-SNAP
datasheet, IEC 60601-1 §8.4 (separation distances near patient-applied parts).

---

### #9. `biocompat_region_tag` — P0, trivial

**What:** Pure-metadata skill (no geometry change) that tags a region of
faces with a biocompatibility classification (ISO 10993 contact category) +
material constraint + cleaning method compatibility. Mirrors the pattern of
`surface_finish_tag` but for biocompat. Downstream DFM / FEA / regulatory
report consumes this.

**Pydantic args:**
- `region_selector: SelectorRef`
- `contact_category: Literal["non_contact", "skin_intact", "mucosal", "breached_skin", "blood_path_indirect", "blood_path_direct", "implant_short_term", "implant_long_term"]`
- `contact_duration: Literal["transient_<10min", "short_<24h", "prolonged_24h_30d", "permanent_>30d"]`
- `material_allowlist: list[str]` (e.g. `["PC_ISO10993_5", "Tritan_MX711", "silicone_LSR7070"]`)
- `cleaning_methods: list[Literal["wipe_iso_70", "wipe_h2o2", "autoclave_134c_18min", "etox", "gamma_25kgy", "vhp"]]`
- `latex_free: bool = True`
- `dehp_free: bool = True`

**Why:** Every medical-device design file MUST identify patient-contact
surfaces and their biocompatibility category at design time, because it
drives material selection, sterilization, supplier qualification, and 510(k)
test plan. The information lives in CAD comments today — making it a tagged
region promotes it to a queryable property and unblocks automated
biocompat / regulatory report generation.

**Standard:** ISO 10993-1:2018 (biological evaluation), ISO 14971 risk,
ISO 13485 §7.3.3 design inputs. ISO 17664 reprocessing. EU MDR Annex I 10.4.

**OCCT difficulty:** Trivial — just a `body._pd_biocompat = {...}` attach.

---

### #10. `autoclave_flat_seal_pair` — P1, moderate

**What:** A specialized variant of `face_seal_land_pair` for steam-autoclave
re-sterilizable housings. Generates *paired* flat sealing surfaces with:
- both surfaces tagged `surface_finish_tag(target_ra_um=0.8)` (mirror lap),
- both tagged `gdt_flatness(tolerance_um=10)`,
- material constraint baked in (PEI Ultem 1010 / PPSU / 316L SS allowlist),
- a thermal-expansion compensation rib that prevents seal breakage during
  the 134 °C autoclave cycle.

**Pydantic args:**
- `body_a_face_selector / body_b_face_selector`
- `seal_path_sketch: SketchSpec`
- `service_temperature_c: float = 134.0` (steam autoclave)
- `expansion_compensation: bool = True`
- `target_ra_um: float = 0.8`
- `target_flatness_um: float = 10.0`
- `clamp_force_n_per_mm: float` (FEA-relevant)

**Why:** Reusable surgical instruments (electrosurgical pencils, endoscope
flanges, reusable laryngoscope handles) survive thousands of autoclave cycles.
The seal lap must be flat enough that no steam ingress occurs as the housing
thermally cycles. Bond-and-seal is forbidden — only mechanical clamped seals.

**Standard:** ISO 17665-1 (moist heat sterilization), AAMI ST79 §7.5,
ISO 11607-1 (packaging) at the housing-system boundary.

---

### #11. `drop_rib_lattice_inner_wall` — P2, moderate

**What:** Generate a grid of internal stiffening ribs on the inside surface of
a housing wall, sized for a defined drop-impact load case. Driven by drop
height + mass; ribs are spaced per a quick stiffness formula and trimmed away
from sealing lands, PCB keep-outs, and snap-hook footprints.

**Pydantic args:**
- `inner_face_selector`
- `keepout_selectors: list[SelectorRef]`
- `drop_height_m: float = 1.0`
- `housing_mass_g: float`
- `rib_height_mm: float`
- `rib_thickness_mm: float` (≤ 0.6 × wall thickness to avoid sink)
- `pattern: Literal["grid", "diagonal", "honeycomb_internal"]`
- `spacing_mm: float | None` (auto from drop calc if None)

**Why:** IEC 60601-1 §15.3.4 requires 1 m drop survival on portable medical
equipment. Handheld diagnostic devices (glucometers, ECG event recorders)
fail this test as wall-only shells. Adding manual ribs is tedious; this
parameterizes the spacing/height from the drop spec.

**Standard:** IEC 60601-1 §15.3.4 (mechanical strength — portable),
IEC 60601-1-11 §10.1.3 (home use, 1.5 m), MIL-STD-810G Method 516.6
(transit drop).

---

### #12. `tether_loop_anchor` — P2, trivial

**What:** Small captive loop boss on the housing — a U-shaped lug with a
through-hole for a lanyard, drop-tether, or wrist-strap. Geometry: thin
arch-shaped boss with parallel side walls and a transverse hole sized for
2-3 mm cord.

**Pydantic args:**
- `face_selector`
- `position_xy / orientation_deg`
- `arch_height_mm`, `arch_width_mm`, `wall_thickness_mm`
- `cord_diameter_mm: float = 2.0`
- `min_breaking_strength_n: float = 50.0` (drives wall thickness check)

**Why:** Handheld point-of-care diagnostics (iSTAT, glucometers, fetal
heart-rate Dopplers) are required to have a tether attachment so they
don't drop. The loop needs a calculated breaking strength — too-thin
walls fail the IEC 60601 transport tests.

**Standard:** IEC 60601-1-11 §10.1 (handheld retention), MIL-STD-810G
transit-drop methodologies.

---

### #13. `equalization_diaphragm_pocket` — P2, moderate

**What:** A thin diaphragm region (~0.3-0.5 mm wall) integrally molded into a
housing wall to allow pressure equalization without a separate vent membrane.
Used in low-cost disposables. Defines a circular thin region with a
controlled flexure ring and a back-side support frame.

**Pydantic args:**
- `face_selector`
- `diaphragm_diameter_mm: float`
- `wall_thickness_at_diaphragm_mm: float = 0.4`
- `support_frame: Literal["ring", "cross", "spoke"]`
- `position_xy`

**Why:** Single-use disposables (single-use insulin patch pumps, single-use
endoscope handles) can't afford a Gore vent. An integrally molded diaphragm
flexes during cabin pressure changes (air transport) to prevent enclosure
deformation.

**Standard:** ISO 10548 (medical packaging pressure cycle), RTCA DO-160G
§4 (avionics enclosure pressure for air-transport medical devices).

---

## 3. Cross-cutting infrastructure gaps

These are NOT skills per se, but infrastructure that the medical-housing
skills above all need:

1. **Catalog backbone for connectors, membranes, captive fasteners.** A
   structured `data/catalogs/{connectors,vent_membranes,captive_fasteners}.json`
   plus a `phone_designer.catalogs` Python module to resolve catalog IDs into
   geometric specs. Many skills above name a `catalog_id` arg — they all
   need a single resolver.

2. **Paired-body output type.** `face_seal_land_pair` and `autoclave_flat_seal_pair`
   modify *two* bodies; current `SkillResult` carries one body. We need a
   `SkillPairResult` or a "follow-up action queue" pattern so a single skill
   call can emit two paired edits.

3. **Region tagging promoted to a first-class concept.** Today
   `surface_finish_tag` writes `body._pd_finish` and biocompat would write
   `body._pd_biocompat`. We need a unified `region_tag` namespace
   `body._pd_regions: dict[str, list[Region]]` so multiple downstream
   inspectors (cleaning, biocompat, sterilization, EMI) can query consistently.

4. **GD&T linkage from manufacturing tags.** `autoclave_flat_seal_pair`
   should auto-emit `gdt_flatness` callouts. Today GDT skills are separate
   calls — a way to *attach* a GDT callout as a side-effect of a manufacturing
   tag would compress LLM plans by 3-4 steps.

5. **Region keep-out predicate.** `drop_rib_lattice_inner_wall` needs to
   subtract several keep-out regions from a generated pattern. A reusable
   `_keepout_mask` helper would also benefit `breathing_hole_array`,
   `grille_pattern`, etc.

6. **Material-aware DFM checks.** O-ring squeeze validation, autoclave
   flatness, cleaning radius all depend on knowing the material. We need a
   `body._pd_material` (or per-region material map) and DFM check skills that
   read it instead of taking material as an arg per call.

7. **Standards compliance report skill** (`inspect.compliance_report`) — an
   inspector that scans every `_pd_*` tag and emits a report against a chosen
   standards set (`ISO_10993`, `IEC_60601_1`, `IEC_60529_IPX7`, `AAMI_TIR12`).
   This is what makes the tag effort actually pay off.

---

## 4. Domain-specific catalogs needed

A `phone_designer/catalogs/medical/` package should ship JSON tables for:

1. **Captive fasteners** — PEM PF11, PF50, FH/FHS, SI; Southco 47/48; Camcar
   SEMS. Each entry: shank/head dims, retention method, panel thickness range,
   torque spec.
2. **Vent membranes** — Gore PMF series (PMF100487, PMF200652, PMF200500),
   Donaldson Tetratex TX5613, W. L. Gore Acoustic Vent series. Entries: vent
   diameter, recess depth, bond land width, adhesive system, IPX rating, airflow.
3. **Medical connectors** — Lemo 00/0B/1B/2B/3B push-pull; Redel SP/SR/Plastic
   SP; ODU MEDI-SNAP & MINI-SNAP; Fischer Push-Pull 103/104; Binder 720 series;
   DIN 41612 B/C/D; IEC 60603-2 DA/DB/DC/DD; LSI Medical/Spectramed transducer
   connectors. Entries: panel cutout, mounting screw pattern, keep-out volumes,
   number of contacts, sealing rating.
4. **O-ring / gasket** — AS568 dash 001-475, ISO 3601 metric. Entries:
   cord diameter, AS568 ID, recommended gland W/D, squeeze %.
5. **Biocompat-rated polymers** — Sabic Ultem 1010, Solvay Radel R-5500,
   Eastman Tritan MX711/MX811 (DEHP-free), Bayer Makrolon Rx2530, Wacker LSR
   silicones, PEEK Vestakeep i4. Entries: ISO 10993 categories cleared,
   autoclave cycles before yellowing, gamma stability, CTE.
6. **Adhesive systems** — 3M 467MP/468MP (membrane bonding), Loctite 4541
   (biocompat cyanoacrylate), Henkel Hysol M-31CL. Entries: substrate
   compatibility, ISO 10993 status, cure time, peel strength.

---

## 5. Concrete product examples that exercise these gaps

To validate priorities, three realistic design tasks the LLM should be able
to complete end-to-end after the gaps are filled:

### Example A: Wearable continuous-glucose-monitor transmitter
- `face_seal_land_pair` against base patch (silicone gasket)
- `biocompat_region_tag` on skin-contact bottom face (ISO 10993 skin_intact,
  prolonged, latex_free)
- `cleanability_radius_enforce` on entire patient-contact region
- `ipx_vent_membrane_pocket` for body-heat equalization
- `tether_loop_anchor` for retention cord
- `captive_screw_tether_pocket` for battery door (replaceable cell version)

### Example B: Handheld electrosurgical pencil
- `autoclave_flat_seal_pair` (PEI Ultem 1010, 134 °C × 1000 cycles)
- `cleanability_radius_enforce(min_internal_radius_mm=1.0)`
- `connector_cutout_from_catalog(connector_id="lemo_1b_5pin")`
- `cable_strain_relief_boot` at activation cable exit
- `membrane_keypad_recess` for cut/coag buttons
- `biocompat_region_tag(contact_category="breached_skin", contact_duration="transient_<10min")`

### Example C: Intraoperative patient monitor (console)
- `connector_cutout_from_catalog` for DIN 41612 B32 internal bus + IEC 60603-2
  patient I/O bank
- `ipx_vent_membrane_pocket` (IPX1 fluid drip protection)
- `membrane_keypad_recess` with `lightpipe_diameter_mm` for status LEDs
- `drop_rib_lattice_inner_wall(drop_height_m=1.0, housing_mass_g=2500)`
- `captive_screw_tether_pocket` for service-access cover
- `surface_finish_tag(finish_type="matte", target_ra_um=1.6)` — wipe-cleanable
- `biocompat_region_tag(contact_category="skin_intact")` on accessible faces

If the LLM can plan all three of these with one skill per high-level concept
(no improvising from primitives), the medical-housing domain is well-covered.
