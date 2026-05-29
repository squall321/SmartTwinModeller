# Mechanical Power Tools, Gearboxes, Actuators, Pumps — Skill Gap Analysis

Scope: motor housings (BLDC / brushed / stepper), gearbox housings (planetary,
spur, worm), small actuators, fluid/gear pumps, cordless power-tool platforms.
Concretely: a Makita / Milwaukee / DeWalt cordless drill body, a Bosch impact
driver gearbox, a NEMA-23 stepper mount, a small peristaltic / gerotor pump
housing.

---

## 1. Survey — what already touches this domain

The library already has decent coverage for the EXTERIOR / cosmetic side and
a handful of rotary primitives, but very little that resolves the
"shaft + bearing + seal + flange" geometry that defines power-tool internals.

What is already present and reusable:

| Existing skill | Relevance to power-tool domain |
|---|---|
| `create.cylinder` / `create.disc_with_dome` | Motor can body, end caps |
| `create.gear_external_involute` | Spur pinion / output gear (ISO 1328 involute) |
| `create.gear_internal_involute` | Ring gear for planetary stages |
| `create.helical_spring` / `create.coil_spring_rectangular` | Clutch springs, brush springs |
| `modify_pocket.helical_thread_external` / `_internal` | Chuck nose thread, retaining nut |
| `modify_pocket.hole`, `hole_array` | Bolt circles (manual point layout) |
| `modify_pocket.knurl_pattern` | Grip on a hand-held shaft, chuck collar |
| `modify_boss.boss_with_hole`, `mounting_pad`, `standoff` | Generic mount features |
| `modify_boss.heatsink_fin` | Single linear fin — but no radial / motor-can fin pattern |
| `modify_boss.o_ring_groove` | Sealed gearbox lid (static O-ring only — no lip seal cavity) |
| `modify_boss.rib`, `gusset` | Housing stiffeners |
| `modify_boss.battery_dock_pad` | Battery contact strips (Li-Ion pack interface) |
| `modify_boss.hinge_pin_boss` | Trigger lever pivot post (notched, anti-rotation) |
| `modify_boss.snap_hook` | Housing clam-shell snaps |
| `modify_pattern.circular_pattern` | Bolt-circle layout once one hole exists |
| `inspect.hole_alignment_check` | Verifying coaxial bearing seats — already useful |
| `inspect.gdt_cylindricity` / `gdt_position` | Bore-quality / bolt-circle position |
| `inspect.mass_properties` | Inertia of rotor — used for motor sizing |
| `assembly.mate_concentric`, `mate_axis`, `mate_at_distance`, `fastener_array` | Bearing-to-bore mating |

What is conspicuously MISSING for this domain:

1. **No fit-aware bore / shaft.** Holes are nominal diameter only — no ISO 286
   tolerance-class (H7 / k6 / m6) baked into the geometry or even into the
   skill metadata. A press-fit bearing seat and a slip-fit shaft hole are
   geometrically the same `hole` call today.
2. **No motor-can / stator interface skill.** The exterior is a `cylinder`
   primitive, but there is no "stator stack lamination cavity" or "BLDC stator
   bore with locating shoulder" macro.
3. **No bearing-seat geometry.** Bearings (608ZZ, 6000-series, 6900-series)
   need a counterbore + shoulder + retaining-ring groove. There's only
   plain `hole` and `o_ring_groove` (which is a static seal groove, not a
   retaining ring groove).
4. **No lip seal cavity.** ISO 6194-1 radial shaft seals have very specific
   bore tolerances, lead-in chamfers, and shoulder geometry. None of that
   exists.
5. **No NEMA / IEC motor flange pattern.** No skill to drop a NEMA-17 / 23 / 34
   bolt circle with the correct pilot diameter. Today you'd have to compute
   bolt positions and call `hole_array`.
6. **No radial cooling fin array.** `heatsink_fin` is a single linear fin on
   a flat face. Real motor cans have N radial fins extruded along the cylinder
   axis. Same for gerotor / hydraulic-block radiator surfaces.
7. **No brush-holder slot pattern.** Brushed DC motor end caps have two
   diametrically opposed rectangular slots with a spring retainer pocket and
   wire pass-through.
8. **No T-slot battery rail.** Cordless tool families (Makita LXT, Milwaukee
   M18, DeWalt 20V MAX) all use a slide-on T-rail with locking detents.
   Today this requires manual sketch + pocket calls.
9. **No vibration mount pad / grommet pocket.** Rubber bushing isolators for
   tool body / motor mount — a counterbored pocket with a shoulder lip.
10. **No keyway / spline / D-cut on a shaft.** Output shafts on gearboxes and
    pump rotors need ISO 14-spline, parallel keyway (DIN 6885), or D-flat. None
    of these exist.
11. **No retaining-ring groove (DIN 471 / 472).** Sharp-cornered rectangular
    groove on a shaft or in a bore — different geometry from `o_ring_groove`.
12. **No external/internal involute SPLINE shaft.** Different from gears —
    needed for motor-to-gearbox interfaces (e.g., RC car / cordless drill
    drive splines).
13. **No worm / worm-wheel.** Used in many gear-motor reducers and small
    actuators (e.g., automotive window lifts).
14. **No bevel / helical gear.** The two gear skills are SPUR only. Helical
    is the standard for power tools above ~50 W (quieter, higher torque
    density). Bevel for angle drills, right-angle attachments.
15. **No trigger / switch rocker pocket.** A tilted-axis pocket with a pivot
    boss inside it — a recurring power-tool pattern.
16. **No gerotor / trochoidal lobe profile.** Inner + outer rotor of a small
    oil / hydraulic pump.
17. **No labyrinth seal groove.** Multi-step circumferential pocket on a
    shaft, used in dust-resistant power-tool gearboxes.
18. **No oil drain / fill boss with pipe-thread (NPT / G).** Different thread
    form from the existing V-thread — taper for NPT, parallel for G.

---

## 2. Top missing skills (prioritized)

### P0 — true blockers for any cordless-tool / gearbox model

#### 2.1 `create.gear_helical_involute`
- WHAT: Cylindrical helical gear with involute profile + helix angle.
  Args: `module_mm`, `n_teeth`, `pressure_angle_deg` (default 20),
  `helix_angle_deg`, `hand` ("left"|"right"), `face_width_mm`, `bore_d_mm?`.
- WHY: Power-tool gearboxes (Bosch GSR, Makita DHP) use helical first stages
  for noise. RC-car transmissions, small EV reducers — same story.
- STANDARD: ISO 1328-1 (cylindrical gears, accuracy), DIN 3961, AGMA 2000-A88.
- PRIORITY: P0.
- OCCT: moderate. Sweep involute tooth along helical path then circular pattern.

#### 2.2 `modify_pocket.bearing_seat`
- WHAT: Bearing pocket = bore + shoulder + lead-in chamfer + optional retainer
  groove. Args: `position`, `axis`, `bearing_id` (e.g., "608ZZ", "6900",
  "MR105"), or raw `bore_d_mm`, `bore_depth_mm`, `shoulder_id_mm`,
  `chamfer_mm` (default 0.3), `fit_class` ("H7"|"K7"|"M7" for housing,
  ISO 286), `retainer_groove`: optional sub-object.
- WHY: EVERY gearbox in this domain. A 6000-series bearing seat is the most
  repeated feature in motor / pump housings.
- STANDARD: ISO 286 (fits), ISO 15/SKF general dimensions, ISO 464 (snap-ring).
- PRIORITY: P0.
- OCCT: trivial-to-moderate. Counterbore + chamfer + optional thin groove.

#### 2.3 `modify_boss.motor_flange_bolt_pattern`
- WHAT: Pilot boss + bolt circle on a face, parameterized by motor frame.
  Args: `face`, `position`, `motor_standard` ("NEMA17"|"NEMA23"|"NEMA34"|
  "IEC56"|"IEC63"|"IEC71") OR explicit `pilot_d_mm`, `pcd_mm`, `n_holes`,
  `hole_d_mm`, `hole_kind` ("clearance"|"tapped"), `tap_size?` (e.g. "M3").
- WHY: Mounting a NEMA-17 stepper to a robotic arm, an IEC frame motor to a
  pump body, mounting an output cap to a gearbox. The same pattern recurs in
  every actuator project.
- STANDARD: NEMA ICS-16, IEC 60072-1.
- PRIORITY: P0.
- OCCT: trivial. Pilot bore + circular pattern of holes.

#### 2.4 `modify_pocket.lip_seal_cavity`
- WHAT: Radial shaft-seal seat. Bore + lead-in chamfer + retention lip.
  Args: `position`, `axis`, `seal_id` (e.g., "TC 12x22x5") OR
  `shaft_d_mm`, `cavity_od_mm`, `cavity_depth_mm`, `lead_chamfer_mm`,
  `retention_lip` (bool — adds a 0.5 mm radial lip at the outboard end).
- WHY: Every output shaft of every gearbox / pump in this domain. Dust seal
  on a drill chuck shaft, oil seal on a gerotor pump.
- STANDARD: ISO 6194-1 (rotary shaft seals), DIN 3760.
- PRIORITY: P0.
- OCCT: trivial. Stacked counterbores + chamfer.

#### 2.5 `modify_pocket.retaining_ring_groove`
- WHAT: External (DIN 471) or internal (DIN 472) retaining-ring groove —
  rectangular cross-section, sharp corners.
  Args: `target` ("shaft"|"bore"), `position`, `axis`, `nominal_d_mm`,
  `groove_d_mm`, `groove_w_mm`, OR `ring_size` (e.g., "12x1" → looks up
  the spec). Tagged `retaining_groove` face for inspection.
- WHY: Axially locating bearings & gears on shafts is THE standard practice.
  Today users would mis-use `o_ring_groove` (semicircular — wrong).
- STANDARD: DIN 471 (external), DIN 472 (internal), ISO 464.
- PRIORITY: P0.
- OCCT: trivial. Revolved sharp-cornered rectangle.

---

### P1 — major features for production credibility

#### 2.6 `modify_boss.radial_fin_array`
- WHAT: N straight axial fins extruded radially outward from a cylindrical
  face, evenly spaced. Args: `target_face` (cylindrical), `n_fins`,
  `fin_height_mm`, `fin_thickness_mm`, `start_z_mm`, `length_mm`,
  `top_taper_mm` (mold-release), `tip_radius_mm`.
- WHY: Brushless motor cans, hand-held tool housings, hydraulic-block
  cooling — universal pattern. `heatsink_fin` is the LINEAR sibling on a
  flat face; this is the cylindrical sibling.
- STANDARD: none formal — but matches `IEC 60034-6` IC-frame cooling marks.
- PRIORITY: P1.
- OCCT: moderate. Fin profile sketch + linear extrude + circular pattern.

#### 2.7 `modify_pocket.keyway_parallel`
- WHAT: Parallel keyway slot on a shaft (external) or in a bore (internal).
  Args: `target` ("shaft"|"bore"), `position`, `axis`, `shaft_d_mm`,
  `width_mm`, `depth_mm`, `length_mm`, `end_style` ("open"|"closed-radius"),
  OR `key_size` (e.g., "5x5" → DIN 6885 lookup).
- WHY: Standard torque-transmitting feature on output shafts (gear motors,
  pumps, small reducers).
- STANDARD: DIN 6885 (parallel keys & keyways), ISO 773.
- PRIORITY: P1.
- OCCT: trivial. Box-pocket on cylindrical surface.

#### 2.8 `create.gear_bevel_straight`
- WHAT: Straight-tooth bevel gear. Args: `module_mm`, `n_teeth`,
  `pitch_angle_deg`, `face_width_mm`, `pressure_angle_deg`,
  `mate_n_teeth` (to compute pitch cone properly), `bore_d_mm?`.
- WHY: Right-angle drills, mitre attachments, hand-held grinders' head.
- STANDARD: ISO 23509, DIN 3971.
- PRIORITY: P1.
- OCCT: hard. Conical involute + frustum extrusion.

#### 2.9 `modify_boss.tslot_battery_rail`
- WHAT: Pair of cordless-tool T-slot rails with locking detents.
  Args: `face`, `axis`, `slide_length_mm`, `platform` ("makita_lxt"|
  "milwaukee_m18"|"dewalt_20v"|"bosch_18v"|"custom"), OR explicit
  `rail_pitch_mm`, `rail_width_mm`, `rail_height_mm`, `t_shoulder_mm`,
  `detent_position_mm`, `detent_d_mm`.
- WHY: Every cordless-tool product. Without it you can't model the
  battery-mount interface — the most defining feature of the body shell.
- STANDARD: de-facto (no ISO), but each major ecosystem has fixed
  dimensions.
- PRIORITY: P1.
- OCCT: moderate. Mirrored extrude + half-sphere detent pockets.

#### 2.10 `modify_pocket.brush_holder_slot`
- WHAT: Pair of opposed rectangular slots with a spring-retainer pocket
  and lead-wire pass-through. Args: `face` (end-cap planar face),
  `commutator_d_mm`, `brush_w_mm`, `brush_thk_mm`, `brush_length_mm`,
  `lead_hole_d_mm`, `spring_pocket_d_mm`.
- WHY: Every brushed DC motor end cap — and there are STILL many in cheap
  power tools, cordless screwdrivers, RC, automotive applications.
- STANDARD: none formal — recurring de-facto layout.
- PRIORITY: P1.
- OCCT: moderate. Two mirrored rectangular pockets + cylindrical pocket
  + through-hole.

#### 2.11 `modify_pocket.trigger_rocker_pocket`
- WHAT: Angled rectangular pocket sized for a trigger rocker + an internal
  pivot boss for the pivot pin. Args: `face`, `position`, `axis`
  (tilt of trigger), `pocket_l_mm`, `pocket_w_mm`, `pocket_depth_mm`,
  `pivot_offset_mm`, `pivot_d_mm`, `pivot_height_mm`, `tilt_deg`.
- WHY: Cordless drill / impact driver / oscillating tool — the recurring
  human-interface pocket.
- STANDARD: none.
- PRIORITY: P1.
- OCCT: moderate. Pocket + boss with rotated axis.

#### 2.12 `modify_boss.vibration_grommet_pocket`
- WHAT: Stepped pocket for an elastomeric isolator (typical
  M3 / M4 / M5 grommet). Args: `face`, `position`, `grommet_id`
  (catalogue) OR explicit `outer_d_mm`, `outer_depth_mm`, `inner_d_mm`,
  `inner_depth_mm`, `flange_d_mm`, `flange_thk_mm`, `hole_d_mm`.
- WHY: Motor mounting plates, fan mounts inside tool bodies, pump-base
  feet. Reduces transmitted vibration & noise.
- STANDARD: de-facto. Reference McMaster-Carr 9311K series geometry.
- PRIORITY: P1.
- OCCT: trivial. Stacked counterbores.

---

### P2 — useful niche features

#### 2.13 `create.gear_worm` + `create.gear_worm_wheel`
- WHAT: Single/multi-start cylindrical worm + matching worm wheel.
  Args worm: `module_mm`, `n_starts`, `pitch_d_mm`, `length_mm`,
  `lead_angle_deg`, `bore_d_mm?`. Wheel: `module_mm`, `n_teeth`,
  `worm_pitch_d_mm`, `wheel_face_w_mm`, `bore_d_mm?`.
- WHY: Window-lift actuators, valve actuators, conveyor reducers.
- STANDARD: ISO 14521 (worm gears), DIN 3975.
- PRIORITY: P2.
- OCCT: hard (worm has helical+trapezoidal sweep; wheel needs throat radius).

#### 2.14 `create.spline_shaft_involute` + `modify_pocket.spline_bore_involute`
- WHAT: Involute spline shaft / mating bore. Args: `module_mm`, `n_teeth`,
  `pressure_angle_deg` (30 or 37.5 for splines), `length_mm`,
  `fit_class` ("flank-centered"|"diameter-centered").
- WHY: Motor-to-gearbox interfaces, automotive shafts, RC-drill output.
  More common than parallel keys at high torque.
- STANDARD: ISO 4156, DIN 5480, ANSI B92.1.
- PRIORITY: P2.
- OCCT: hard — involute curve at small module, many teeth.

#### 2.15 `create.gerotor_rotor_pair`
- WHAT: Outer (N+1 lobe) + inner (N lobe) trochoidal rotor pair of a
  gerotor pump. Args: `n_inner_lobes`, `eccentricity_mm`,
  `outer_pitch_d_mm`, `width_mm`, `inner_bore_d_mm`.
- WHY: Small oil pumps, hydraulic pilot pumps, automotive transmission
  feed pumps. Fully parametric trochoidal geometry not derivable from
  any existing primitive.
- STANDARD: none (geometry from Eisenmann/Lippe equations).
- PRIORITY: P2.
- OCCT: hard. Epitrochoid + hypotrochoid curve construction.

#### 2.16 `modify_pocket.thread_taper_npt` / `thread_parallel_g`
- WHAT: Real tapered pipe thread (NPT) or parallel pipe thread (BSPP / G).
  Args: `position`, `axis`, `size` ("1/8"|"1/4"|...), `depth_mm`.
- WHY: Oil-fill / drain / pressure-port bosses on gearbox / pump housings.
- STANDARD: ANSI B1.20.1 (NPT), ISO 228-1 (G).
- PRIORITY: P2.
- OCCT: moderate. Existing helical-thread machinery, but tapered axis +
  different thread form.

#### 2.17 `modify_pocket.labyrinth_seal_groove`
- WHAT: N circumferential rectangular grooves on a shaft surface, evenly
  pitched. Args: `face` (cylindrical), `n_grooves`, `start_z_mm`,
  `pitch_mm`, `groove_w_mm`, `groove_d_mm`.
- WHY: Dust ingress protection on impact-wrench output shafts, fan-cooled
  motor shaft pass-through, low-cost alternative to a lip seal.
- STANDARD: none formal.
- PRIORITY: P2.
- OCCT: trivial. Repeated revolved rectangle.

#### 2.18 `inspect.bearing_seat_check`
- WHAT: Read-only check that a tagged bearing seat conforms to its
  declared fit class (cylindricity ≤ X μm, diameter inside H7 band,
  shoulder face perpendicular to bore axis to within Y μm).
  Args: `seat_tag`, `bearing_id`, `fit_class`.
- WHY: Production DFM gate. Without it the new `bearing_seat` skill could
  drift under modifications and silently produce a slip-fit.
- STANDARD: ISO 286 + cross-reference to `gdt_cylindricity` /
  `gdt_perpendicularity` already present.
- PRIORITY: P2.
- OCCT: trivial — composition of existing GD&T inspectors.

---

## 3. Cross-cutting infrastructure gaps

These aren't single skills — they're capabilities multiple skills would all
need:

1. **ISO 286 tolerance metadata on holes.** `hole` and `bearing_seat`
   should accept a `fit_class` enum ("H6", "H7", "K7", "M7", "P7") that
   either adjusts the nominal bore by the mean of the deviation band OR
   at minimum is recorded as inspection metadata. Without this, downstream
   GD&T checks don't know what to validate against.
2. **Bearing / seal / retaining-ring catalogue lookup.** A side-table that
   maps `"608ZZ"` → `(d=8, D=22, B=7, shoulder_min=10.5)`, `"TC 12x22x5"` →
   geometry, `"DIN471-12x1"` → groove geom. Multiple skills (`bearing_seat`,
   `lip_seal_cavity`, `retaining_ring_groove`) all need this; building it
   per-skill would be wasteful.
3. **Cylindrical-face local frame.** Many of the new skills (radial-fin
   array, labyrinth groove, keyway on shaft) operate in cylindrical
   (axis, theta, z) coordinates on a curved face. Today selectors give a
   face but not a parametric frame on it. A `_cyl_face_frame()` resolver
   would unblock items 2.6, 2.7, 2.17 cleanly.
4. **Coaxial bore alignment as a CONSTRAINT, not a check.** Gearbox housings
   have 2-4 bearing bores that MUST be coaxial. Today `hole_alignment_check`
   verifies after the fact; we need a planning primitive that anchors all
   bearing seats to a single shared axis tag.
5. **Helical / involute curve primitives in `_sketch`.** Several gear/spline
   skills would share a "build involute wire" and "build trochoid wire"
   helper that currently lives only inside `gear_external_involute.py`.
6. **Press-fit interference vs assembly tolerance** in
   `assembly.interference_check`. Today it reports any overlap as
   "interference"; with `fit_class` metadata we could whitelist intended
   ones (k6/H7 on a bearing seat).
7. **Standard motor-frame catalog** (NEMA / IEC) shared between
   `motor_flange_bolt_pattern` and the corresponding `motor_can_envelope`
   (when added). The frame data also feeds inertia/mass checks.

---

## 4. Catalogues / data tables needed

Implementing the P0/P1 skills cleanly requires a small set of static
parameter tables — these are domain-specific and should live alongside
the skills (e.g. `phone_designer/skills/_catalogues/`):

1. **Rolling bearings** — at minimum:
   - 6000–6009 series (deep groove), 608/688 (mini), 6900-series (thin),
     MR-series (instrument). Cols: d, D, B, r_min, shoulder_dia_min/max.
2. **Radial shaft seals** (ISO 6194-1, TC / TCV form):
   - shaft_d, cavity_od, width, lip_form.
3. **Retaining rings**:
   - DIN 471 external (1.5–100 mm shaft).
   - DIN 472 internal (8–100 mm bore).
   - Cols: nominal_d, groove_d, groove_w, ring_thickness.
4. **Parallel keys & keyways** (DIN 6885):
   - shaft_range, key_w, key_h, keyway_depth_shaft, keyway_depth_hub.
5. **Motor frames**:
   - NEMA 17 / 23 / 34 / 42: faceplate_w, pilot_d, pcd, n_holes, hole_d.
   - IEC 56 / 63 / 71 / 80 / 90: H, P, N, M, S, T.
6. **Cordless tool battery interfaces** (T-rail dims): Makita LXT,
   Milwaukee M18, DeWalt 20V MAX, Bosch 18V, Ryobi One+.
7. **Pipe threads**: NPT 1/16 – 1, BSPP/G 1/8 – 1; major/minor at gauge
   plane, taper, length.
8. **Fit class deviations** (ISO 286): minimal subset — H6, H7, H8, k6,
   m6, n6, p6, K7, M7, P7 — over the diameter ranges relevant to the
   above bearings (3–100 mm).

These tables are small (a few KB of JSON each) but unblock a lot.

---

## 5. Concrete worked examples

How would these missing skills compose in real designs?

### 5.1 Cordless drill body shell (Makita-style)
```
create.rounded_slab                        # palm body
+ modify_boss.tslot_battery_rail           # battery interface  [P1, missing]
+ modify_pocket.trigger_rocker_pocket      # trigger             [P1, missing]
+ modify_boss.hinge_pin_boss               # forward/reverse selector (have)
+ modify_pocket.brush_holder_slot          # if brushed motor    [P1, missing]
+ modify_boss.radial_fin_array             # motor can cooling   [P1, missing]
+ modify_boss.motor_flange_bolt_pattern    # gearbox attach      [P0, missing]
+ modify_boss.snap_hook  (×8)              # clam-shell closure  (have)
+ modify_pocket.lip_seal_cavity            # output shaft        [P0, missing]
+ modify_pocket.bearing_seat (608ZZ)       # output bearing      [P0, missing]
```
At least 6 of 10 features require new skills.

### 5.2 Planetary gearbox housing (e.g., 30 mm dia, 3-stage)
```
create.cylinder                                  # outer can
+ create.gear_internal_involute                  # ring gear     (have)
+ modify_pocket.bearing_seat ×3 (6700, 6701)     # carrier brgs  [P0, missing]
+ modify_pocket.retaining_ring_groove ×2         # axial locate  [P0, missing]
+ modify_pocket.lip_seal_cavity                  # output        [P0, missing]
+ modify_boss.motor_flange_bolt_pattern (NEMA17) # motor input   [P0, missing]
+ modify_pocket.keyway_parallel ("5x5")          # output shaft  [P1, missing]
+ modify_pocket.thread_parallel_g ("1/8")        # oil port      [P2, missing]
+ inspect.bearing_seat_check ×3                  # DFM gate      [P2, missing]
```
The current library cannot build a single complete bearing pocket today.

### 5.3 NEMA-23 stepper motor mount bracket
```
create.box                                  # plate (have)
+ modify_boss.motor_flange_bolt_pattern(NEMA23)  # bolt circle  [P0, missing]
+ modify_pocket.hole_array                  # base mounting (have)
+ modify_boss.vibration_grommet_pocket ×4   # isolators        [P1, missing]
+ modify_curvature.fillet_predicate         # cosmetic         (have)
```
The defining feature — the NEMA bolt pattern — is a one-line gap.

### 5.4 Small gerotor oil pump
```
create.cylinder                              # body
+ modify_pocket.bearing_seat ×2              # drive shaft     [P0, missing]
+ modify_pocket.lip_seal_cavity              # shaft seal      [P0, missing]
+ create.gerotor_rotor_pair                  # rotors          [P2, missing]
+ modify_pocket.thread_taper_npt ("1/4")     # inlet/outlet    [P2, missing]
+ modify_boss.motor_flange_bolt_pattern(IEC56) # drive motor   [P0, missing]
```
This product is essentially impossible to model today.

---

## 6. Notes / open questions

- The existing spur gear skill is high quality. Following the same
  involute-wire pattern is the right path for helical / bevel / spline.
- The existing `hole` skill should probably grow a `fit_class` field
  rather than spawning many close-cousin skills. Same for any future
  `shaft` primitive.
- A `_catalogues/` subpackage with versioned JSON tables would let the
  LLM planner answer "what bearing size for a 6 mm shaft?" without
  hard-coding domain knowledge into skill bodies.
- `assembly.fastener_array` already exists — the new `motor_flange_bolt_pattern`
  should ultimately delegate to it once a fastener catalogue lands.
- Worth double-checking whether `modify_boss.heatsink_fin` should be
  refactored to delegate to a more general "linear fin array" with the
  proposed `radial_fin_array` sibling sharing helpers.
