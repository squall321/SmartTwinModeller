# Gap Analysis: Wearables (smartwatch, ring, fitness tracker, earbud case)

Scope: caseback / strap / crown / glass / hinge / magnet / coil / light-pipe / sensor-PCB seat / coin-cell cavity. The questions are: can the current ~150-skill library reproduce a credible Apple Watch caseback, an AirPods Pro case lid hinge, a Garmin coin-cell battery door, an Oura ring sensor seat? Where does it fall short?

## 1. Survey — what exists today that touches wearables

Direct hits already in `phone_designer.skills`:

- `modify_boss.crown_shaft_hole` — macro: optional external plinth + bearing recess + shaft hole. Coaxial on one of the 6 cardinal directions. **Does not** model the crown-shaft dynamic seal gland or the wave-washer recess.
- `modify_boss.lug_pair` — macro: two box pads with a single through pin hole each. Axes limited to `+Y/-Y` or `+X/-X`. **Does not** model strap channel (curved underside that clears the strap horn), pin-shoulder counterbore, or quick-release lug shoulder ("Apple-style" tab slot).
- `modify_boss.o_ring_groove` — atomic: **only** circular ring groove on planar +Z/-Z faces. No rectangular or oval gland, no dovetail / half-round / X-ring profile, no draft-allowance for moulded face seal. ISO 3601 conformance is missing — there is no way to say "BS-016 nominal, 70-Sh hardness, 15 % squeeze".
- `modify_boss.hinge_pin_boss` — atomic: ring with axial through-hole and N radial alignment notches around the OD. Useful for AirPods-case hinge knuckles, but **does not** produce: detent ramp (the angular wedge that holds the lid open at 110°), torque-bias notch, or interleaved knuckle gap (left- and right-shell knuckle interleave is currently a manual subtract).
- `modify_boss.battery_dock_pad` — atomic: flat pad + 2 parallel contact ribs. Useful for prismatic LiPo, but **not** for coin cells (which need a *cylindrical* well with a peripheral spring contact rim and a centre disc-spring boss).
- `modify_boss.snap_hook` — atomic, axis-aligned only, no face-aligned tip.
- `modify_pocket.helical_thread_internal/external` — exists; could serve threaded caseback if the LLM knows the watch-thread standard (it doesn't — no preset).
- `modify_pocket.breathing_hole_array` — solves mic vent / acoustic mesh. Already handles AirPods-case mic vent.
- `modify_pocket.cable_routing_channel` — used for ribbon / FPC routing on the inside.
- `create.disc_with_dome` — entire watch outer envelope macro. Good starting point but not parametric for chamfered glass step.
- `create.helical_spring`, `create.coil_spring_rectangular` — physical springs, not magnet/coil pockets.
- `modify_curvature.shell_variable_thickness`, `surface_thicken_variable` — adequate for thin-wall ring interiors.
- `inspect.curvature_map` — useful for verifying skin-contact ring continuity.

Adjacent capability we lean on: `modify_pattern.circular_pattern` for crown serrations, `modify_pocket.knurl_pattern` for crown grip.

**Bottom line.** The library knows a smartwatch *exists* and has the right scaffold for 30 % of features (crown, lugs, o-ring, hinge knuckle). What is missing is the *seat-and-seal vocabulary* — the recesses that receive standard purchased components (sapphire glass, magnets, charging coils, button cells, light pipes, spring bars) at their **catalogued dimensions**. Without those, the LLM has to spell out diameters and depths from memory per call, which is exactly the kind of standards-lookup work the skill catalogue is meant to encapsulate.

## 2. Top Missing Skills

Numbered by priority. Each entry follows: name → behaviour → standard → why → OCCT difficulty.

### P0 — Blockers (cannot model a credible production wearable without these)

1. **`sapphire_glass_seat`** (modify_pocket, atomic)
   Round / cushion-shape step pocket with two coaxial levels: an outer **bezel-gasket gland** (typically 0.30 mm deep × 0.40 mm wide) and an inner **glass-disc step** (depth = glass_thickness + bond_gap, typically 0.05 mm clearance). Profile edge is chamfered at the LCM-defined 0.10 mm × 45° "lift edge" so the crystal can be press-fitted without micro-chipping.
   Args: `face_selector`, `glass_diameter_mm`, `glass_thickness_mm`, `bezel_gland_width_mm` (default 0.4), `bezel_gland_depth_mm` (default 0.3), `bond_gap_mm` (default 0.05), `lift_chamfer_mm` (default 0.10), `profile` (literal: circle | cushion | rectangle_radius).
   Standard: ISO 1413 (shock-resistant watch) implies bezel-glass interface tolerance; sapphire suppliers (Crystaloid, Comadur) publish 0.05 mm radial / 0.03 mm axial nominal step.
   Why: every smartwatch and most fitness-tracker has this exact 2-step pocket. Currently the LLM has to call `extrude_pocket` twice with hand-derived depths and miss the gland → re-test → fail IP rating.
   OCCT: **trivial** (two coaxial cuts + a chamfer).

2. **`coin_cell_cavity`** (modify_pocket, macro)
   Cylindrical well sized to an IEC 60086-3 standard cell (CR2032, CR2025, CR2016, SR41, SR44, LR41, LR44 …) plus **insertion clearance** (0.10 mm radial), **axial dome relief** (0.20 mm), **insulator ring shelf** at the negative-terminal side, and a small **finger-relief notch** to lift the cell out.
   Args: `face_selector`, `cell_designation` (literal of catalogued IEC 60086 codes), `radial_clearance_mm` (default 0.10), `axial_relief_mm` (default 0.20), `finger_notch_width_mm` (default 1.5).
   Standard: IEC 60086-3 dimension table (CR2032: Ø 20.0 × 3.2 mm; SR41: Ø 7.9 × 3.6 mm; LR44: Ø 11.6 × 5.4 mm).
   Why: Garmin Instinct, Casio G-Shock, Withings Activité, BLE beacons, hearing aids. Right now the LLM either guesses Ø 20.5 / 3.4 (wrong, fails to seat with insulator) or omits the dome relief and the cell rattles.
   OCCT: **trivial** (cylinder + step + small box notch).

3. **`spring_bar_lug_pair`** (modify_boss, macro — supersedes `lug_pair`)
   Strap-attachment lugs with **shoulder counterbore on inner faces only** (so the shouldered spring bar locks), conforming to ISO 18684 / ISO 22810 strap pitches **18 / 20 / 22 / 24 / 26 mm** between inner lug faces, with the through-hole at Ø 1.5 mm (standard) or Ø 1.8 mm (heavy-duty). Includes a curved underside (R = lug_thickness, leaving strap clearance) and optional quick-release tab slot (Ø 0.8 mm side relief at one lug only).
   Args: `pitch_mm` (literal 18 | 20 | 22 | 24 | 26 — ISO 18684), `pin_diameter_mm` (default 1.5), `shoulder_d_mm` (default 1.8), `shoulder_depth_mm` (default 0.6), `quick_release_side` (literal: none | top | bottom | both), `axis` (12_to_6 | 3_to_9), `underside_radius_mm`.
   Standard: ISO 18684 (watch strap dimensions); strap pin shoulders are an industry de-facto (Bergeon / Fixoflex).
   Why: the current `lug_pair` produces a plain through-hole and no shoulder, so a real spring bar wobbles. Pitch is not validated against the strap standard so the LLM can output Ø 19.5 mm — useless.
   OCCT: **trivial** (existing `lug_pair` + a stepped hole + small relief slot).

4. **`crown_seal_gland`** (modify_pocket, atomic)
   Annular gland inside the crown bore that receives the **dynamic shaft seal** (X-ring or u-cup). Profile is rectangular for X-ring (per ISO 3601-1 size BS-001 / -002 / -003 with 25 % radial squeeze) or trapezoidal for u-cup. Differs from `o_ring_groove` because the bore axis is normal to a cylindrical (not planar) face and the squeeze direction is radial, not axial.
   Args: `crown_bore_axis` (selector for the existing shaft cylindrical face), `ring_designation` (literal: BS-001 | BS-002 | BS-003 | u-cup-2x1.5 …), `count` (default 1 or 2 for redundant seal), `axial_position_mm`.
   Standard: ISO 3601-1 (o-ring sizes); ISO 22810 (waterproof watch) requires dynamic seal at the crown.
   Why: IP68 / 5 ATM watches all have this. Without it the crown shaft is dry-fit and the design is not waterproof.
   OCCT: **moderate** (annular cut on cylindrical face — need to pick correct axis).

5. **`magnet_pocket_axial`** (modify_pocket, atomic)
   Cylindrical pocket for a **disc neodymium magnet** with: nominal Ø + 0.05 mm radial clearance, depth = magnet_h − **proud_mm** (default −0.05, so the magnet sits 0.05 mm below the face for an even glue line), keep-out cylindrical zone (no metal closer than `keepout_mm` for magnetic short-circuit), and optional **polarization marker** dimple (0.3 mm deep on the south-pole side).
   Args: `face_selector`, `position_xy`, `magnet_diameter_mm` (literal 3 | 4 | 5 | 6 | 8 | 10 — common N52 disc sizes), `magnet_thickness_mm` (literal 1 | 1.5 | 2 | 3), `proud_mm` (default −0.05), `polarization_marker` (bool).
   Standard: K&J Magnetics / Supermagnete catalogue; ISO 7811 magnetic-stripe spec not relevant but pull-force tables drive the diameter choice.
   Why: AirPods case lid latch (Ø 4 × 1.5 mm × 4 magnets), MagSafe alignment ring (Ø 6 × 1 mm × 18 magnets), Apple Watch caseback charging magnet (Ø 8 × 2). Today the LLM uses `hole` and forgets the proud offset → glue squeeze-out.
   OCCT: **trivial**.

6. **`wireless_charging_coil_seat`** (modify_pocket, macro)
   Donut-shaped pocket for a wound wireless-charging coil (Qi A11 / A28, Apple Watch BPP). Floor is a **central spindle boss** (Ø ferrite_core_d) with an annular pocket around it (OD = coil_od + 0.2, ID = ferrite_core_d, depth = coil_thickness + 0.1), plus two small notches at 9 and 3 o'clock for **lead-wire exit**.
   Args: `face_selector`, `position_xy`, `coil_od_mm` (literal Qi A11=43, A28=50, AW=33 …), `coil_id_mm`, `coil_thickness_mm` (default 0.6), `ferrite_core_d_mm` (default coil_id − 0.5), `lead_wire_notch_width_mm` (default 1.0), `lead_wire_angles_deg` (default `[90, 270]`).
   Standard: WPC Qi v1.3 BPP / EPP transmitter coil dimensions (A11, A28); Apple Watch charging puck reverse-engineered docs.
   Why: every Apple Watch caseback, every TWS-case bottom shell, MagSafe Duo. Without this skill the LLM produces a flat-floor pocket and the magnet/ferrite alignment is off.
   OCCT: **trivial** (annular cut + 2 small notches).

7. **`hinge_detent_cam`** (modify_boss, atomic)
   Adds an **angular detent profile** to a hinge knuckle: a circular boss with two cam bumps (default at 0° = closed and 110° = open positions per AirPods Pro), each a half-cylinder bump of `bump_radius_mm` (typically 0.3) on the radial perimeter, against which a spring-loaded ball or leaf spring rides. Distinct from `hinge_pin_boss` whose radial notches are *axial* alignment, not *angular* detents.
   Args: `face_selector`, `bore_axis_direction`, `bore_diameter_mm`, `outer_diameter_mm`, `detent_angles_deg` (list, default `[0, 110]`), `bump_radius_mm` (default 0.3), `ramp_angle_deg` (default 20, the wedge ramp leading into each detent).
   Standard: no ISO but industry rule of thumb is 8–15° "engagement window" per detent.
   Why: AirPods Pro 2 case (110° lid stop), Galaxy Buds case, Pixel Buds case. Currently this is a sequence of swept_pocket calls — error-prone, no closed-form profile.
   OCCT: **moderate** (sweep with variable profile or boolean of N rotated cylinder bumps).

8. **`light_pipe_channel`** (modify_pocket, atomic)
   Through-channel that conducts LED light from a PCB-mounted LED to a visible window. Two coaxial sections: the **LED nest** end (square or circular, sized to LED package + 0.1 mm clearance — e.g. 0603, 0805, 1206, Pico LEDs) and the **window** end (lensed disc, typically Ø 1.6 mm). Between them a 2°–4° draft to maximise total-internal-reflection efficiency. The LED end may have a **diffuser pocket** (0.2 mm shallow chamfered cup) cut at the LED nest face.
   Args: `face_selector_window`, `face_selector_led`, `led_package` (literal 0603 | 0805 | 1206 | "pico" | "side-view-0402"), `window_diameter_mm` (default 1.6), `draft_angle_deg` (default 3), `diffuser_pocket_depth_mm` (default 0.2), `path` (straight | bent — bent is v2).
   Standard: LED packages per JEDEC SMD codes; total-internal-reflection rule (acrylic n ≈ 1.49) prescribes ≥ 42° wall angle for sidewall TIR.
   Why: AirPods case status LED, Charging-cradle indicators, Fitbit Charge battery LED. Today this is two `extrude_pocket` calls — wrong axial alignment is common.
   OCCT: **moderate** (loft between two profiles with draft).

### P1 — Major (production-quality designs would prefer these)

9. **`sensor_pcb_seat_ring`** (modify_pocket, atomic)
   For finger-rings (Oura, Ultrahuman) and watch underside HRM/SpO2 packages: a **conformal interior pocket** along the *inside cylindrical surface* of a thin ring (or curved caseback), sized to a flexible PCB strip. Floor follows the parent cylinder (so the PCB lies flat against the skin-contact surface). Includes a centred **window slot** for the photodiode and an LED hole pair (typical PPG sensor footprint: 2.0 mm × 2.0 mm PD + 2 × Ø 0.6 mm LED on a 4.5 mm pitch).
   Args: `inner_cylinder_face_selector`, `pcb_length_arc_mm`, `pcb_width_axial_mm`, `pcb_thickness_mm` (default 0.3), `pd_window_size_mm` (default 2.0), `led_pair_pitch_mm` (default 4.5), `led_hole_d_mm` (default 0.6).
   Standard: Maxim MAX86141, TI AFE4404 reference designs; ISO 80601-2-61 (pulse oximeter performance) implies optical isolation between LED and PD.
   Why: Oura ring, Ultrahuman Ring AIR, fitness band undersides. The library's `extrude_pocket` cannot produce a curved-floor seat conformal to the parent cylinder — `extrude_pocket_to_curved_floor` exists but takes a sketch, not a length+width on a cylindrical face.
   OCCT: **hard** (wrap a planar profile onto a cylinder, then cut).

10. **`caseback_o_ring_groove_rect`** (modify_boss, atomic — generalisation of `o_ring_groove`)
    Rectangular / oval / racetrack o-ring groove (i.e. follow the perimeter of a non-circular caseback) on a planar face, with **ISO 3601-1 / -2 compliant geometry**: groove width = 1.31 × cs (cross-section), depth = 0.79 × cs, root radius 0.2 mm minimum. Accepts a `path` (rectangle / racetrack / closed polyline) and an o-ring catalogue designation; sizes the groove from the standard.
    Args: `face_selector`, `path` (rectangle with corner radius | racetrack | polyline), `o_ring_designation` (literal BS-XXX | metric AS568 …), `gland_fill_pct` (default 70), `squeeze_pct` (default 20).
    Standard: ISO 3601-2 (housing-gland dimensions), MIL-G-5514.
    Why: Apple Watch is round (existing skill works); Garmin Fenix is round but Polar Vantage V3 has a racetrack caseback; many fitness trackers and Mi Band are rectangular with rounded corners. Currently impossible without manual sweep gymnastics.
    OCCT: **moderate** (path-driven sweep).

11. **`charging_pogo_pad_pair`** (modify_boss, atomic)
    Two coplanar spring-pin-target pads (typically 4 mm diameter, gold-plated copper insert in plastic) at standard pitches — Apple Watch magnetic charger uses 2 pads at 10 mm pitch; many smartwatches use 4 pads. Each pad has a slight (0.05 mm) recess so the pogo pin scrapes oxide on engagement. Pair is concentric with a magnet array.
    Args: `face_selector`, `pad_diameter_mm` (default 4.0), `pad_count` (literal 2 | 4 | 5), `pitch_mm`, `recess_mm` (default 0.05), `concentric_magnet_count` (default 4, places `magnet_pocket_axial` calls at NSEW).
    Standard: Mill-Max pogo connector datasheets; Apple Watch magnetic charging spec.
    Why: every smartwatch charging puck interface. Today this is a `hole_array` + a `mounting_pad` — but they aren't co-tagged for charging contact validation.
    OCCT: **trivial**.

12. **`heart_rate_lens_dome`** (modify_boss, macro)
    Caseback central protrusion holding the PPG glass dome — a Ø 8–10 mm cylindrical boss rising 0.5–0.8 mm above the caseback, top face has a stepped recess for the spherical sapphire / acrylic lens (R = 30 mm typical), and a peripheral light-shield rim (0.2 mm taller than the lens centre, to block ambient light reaching the photodiode).
    Args: `face_selector`, `position_xy`, `lens_radius_sphere_mm` (default 30), `lens_diameter_mm`, `proud_mm` (default 0.6), `shield_rim_height_mm` (default 0.2), `shield_rim_width_mm` (default 0.3).
    Standard: Apple Watch heart-rate sensor reverse-engineered geometry; FCC ID teardowns of Fitbit Sense.
    Why: every modern watch caseback has this — currently a 3-step manual macro of `extrude_boss_blended` + `revolve_pocket` + `rib`.
    OCCT: **moderate**.

13. **`earbud_case_floor_well`** (modify_pocket, macro)
    Pair of teardrop / lozenge wells in the bottom shell of a TWS case sized to the actual earbud body (per-product catalogue: AirPods Pro 2, Galaxy Buds Pro 3, Pixel Buds Pro), with **inductive-charging coil seat at the floor** (via `wireless_charging_coil_seat`), **magnet pocket at the stem tip** (via `magnet_pocket_axial`), and a soft-rubber gasket gland around the lip (via `o_ring_groove` on the rim profile).
    Args: `face_selector`, `bud_profile` (literal of catalogued models), `pair_centre_distance_mm`, `floor_clearance_mm` (default 0.4), `lid_clearance_mm` (default 0.6).
    Standard: vendor reverse engineering; Apple ID for fit clearance.
    Why: this is the *core* feature of any TWS case CAD. Without it the LLM must compose ~12 atomic calls per case.
    OCCT: **hard** (loft + multiple component pockets, but compositional).

### P2 — Nice (production-quality but lower frequency)

14. **`tactile_button_dome_well`** (modify_pocket, atomic)
    Small ø 4 / 5 / 6 mm shallow pocket sized to receive a tactile **metal dome** (Snaptron / Diptronics, e.g. SMT-44 4 mm × 0.25 mm height, click force 160 gf). Includes a **PCB-pad witness hole** below the dome centre and the standard 4-leg pocket dimensions.
    Args: `face_selector`, `position_xy`, `dome_part_number` (literal Snaptron SMT-44 | SMT-58 | …), `pcb_pad_clearance_d_mm`.
    Why: side-button under the crown on watches, button on fitness trackers.
    OCCT: **trivial**.

15. **`strap_quick_release_undercut`** (modify_boss, atomic)
    Apple Watch / Samsung-style quick-release strap groove on the inner face of a lug: a 1.0 mm wide × 0.6 mm deep groove that the strap's spring tab clicks into.
    Standard: Apple Watch strap connector reverse-engineered geometry.
    Why: enables the quick-release strap ecosystem.
    OCCT: **trivial**.

## 3. Cross-Cutting Infra Gaps

These are not skills but underpin many of the above:

- **Component catalogue resource (P0).** Pydantic enums or a JSON catalogue for: IEC 60086-3 coin cells, ISO 3601 o-rings, neodymium disc magnets (N42/N52 standard sizes), Qi coil specs (A11, A28, etc.), JEDEC SMD LED packages, Snaptron metal domes, ISO 18684 strap pitches, sapphire crystal supplier dimensions. Right now each skill would re-hardcode these tables. A shared `phone_designer.standards` module would let `coin_cell_cavity`, `magnet_pocket_axial`, `wireless_charging_coil_seat`, `sapphire_glass_seat`, `tactile_button_dome_well`, `spring_bar_lug_pair`, `light_pipe_channel` all reference the same canonical values.
- **Cylindrical-face selector kind (P0).** Several P0 skills (`crown_seal_gland`, `sensor_pcb_seat_ring`) need to act on a cylindrical / toroidal face, not a planar one. The current `_face_normal_at_center` resolver rejects anything with `|normal[2]| < 0.9`. We need a `cylindrical_face` selector with `axis_direction` and `radius` predicates.
- **Path-on-face primitive (P1).** Several skills (`caseback_o_ring_groove_rect`, `hinge_detent_cam`) need a closed planar path defined in face-local coordinates (rectangle with radius, racetrack, polyline). The `_sketch` helper exists in `modify_pocket` — promote it to a shared utility and allow it for `modify_boss` skills.
- **Wear-product manufacturing spec entries (P1).** Current `manufacturing` dict has `cnc_3axis`, `die_cast_al`, `injection_mold_pa`. Wearables use **MIM (metal injection moulding) for steel cases**, **forged + CNC 5-axis for titanium**, **LSR (liquid silicone rubber) overmoulding for straps and buttons**, **ceramic injection moulding for premium casebacks** (Apple Watch Edition). Need at least `mim_316l`, `cnc_5axis_ti`, `lsr_overmould`, `cim_zirconia` entries with their respective min-wall / min-draft rules.
- **IP-rating post-condition (P1).** New `PostCondition(kind="seal_continuity")` that walks the model and verifies every interface between two volumes (caseback↔body, crown↔housing, glass↔bezel) has an o-ring groove or gasket gland. Currently a wearable can be modelled with **no seal at all** and the system reports success.
- **Skin-contact surface continuity inspector (P2).** For ring / band underside: a `inspect.skin_contact_continuity` that asserts G1 continuity over a tagged "skin-contact" face set. Important because sharp edges cause skin irritation and FDA ISO 10993-10 contact rules.

## 4. Domain-Specific Catalogues Needed

- `phone_designer.standards.coin_cells` — IEC 60086-3 table (designation → Ø × h, voltage, chemistry).
- `phone_designer.standards.o_rings` — ISO 3601-1 / AS568 sizes (cross-section, ID, tolerance).
- `phone_designer.standards.magnets` — common disc N42/N52 SKUs.
- `phone_designer.standards.qi_coils` — WPC Qi 1.3 receiver / transmitter coil dimensions.
- `phone_designer.standards.strap_pitches` — ISO 18684 + quick-release vendor specs.
- `phone_designer.standards.smd_led_packages` — JEDEC 0402/0603/0805/1206/side-view.
- `phone_designer.standards.tactile_domes` — Snaptron / Diptronics catalogue.
- `phone_designer.standards.watch_glass` — Comadur / Crystaloid sapphire stock sizes.

## 5. Worked Examples (showing why these are real gaps)

### Apple Watch Series 10 caseback (target plan)
Today's library: `disc_with_dome` + `o_ring_groove` (round only — OK) + `crown_shaft_hole` + `lug_pair` + manual `extrude_boss_blended` for HRM dome + manual `hole_array` for pogo pins + manual `hole` x4 for magnets.
With proposed skills: `disc_with_dome` + `o_ring_groove` + `crown_shaft_hole` + `crown_seal_gland` (NEW) + `spring_bar_lug_pair` (NEW, replaces `lug_pair`) + `heart_rate_lens_dome` (NEW) + `wireless_charging_coil_seat` (NEW) + `charging_pogo_pad_pair` (NEW) + `magnet_pocket_axial` × 4 (NEW) + `sapphire_glass_seat` (NEW, front).
Step reduction: ~28 atomic calls → 10 macro calls. Manufacturability validation gains seal-continuity check.

### AirPods Pro 3 case body (target plan)
Today: very poor — no hinge detent, no lid magnet catch, no coil seat, no LED light pipe.
With proposed: `rounded_slab` + `earbud_case_floor_well` (NEW, places both buds) + `hinge_pin_boss` (existing) × 2 knuckles + `hinge_detent_cam` (NEW) + `magnet_pocket_axial` × 4 (lid latch) + `wireless_charging_coil_seat` (NEW) + `light_pipe_channel` (NEW, status LED) + `breathing_hole_array` (existing, mic vent) + `coin_cell_cavity` not needed (LiPo battery).

### Garmin Instinct 2 caseback (coin-cell, IP rated)
Today: cannot model the screw-down rectangular battery door with internal coin-cell cavity.
With proposed: `extrude_pocket` (door pocket) + `coin_cell_cavity` (NEW) + `caseback_o_ring_groove_rect` (NEW, racetrack gasket around door) + `helical_thread_internal` × 4 (existing, micro screws).

### Oura Ring Gen 4 (sensor-bearing finger ring)
Today: cannot place the PPG sensor on the inside cylinder.
With proposed: `cylinder` (ring blank) + `shell_variable_thickness` (existing) + `sensor_pcb_seat_ring` (NEW) + `magnet_pocket_axial` (NEW, charging contact) + `surface_finish_tag` for skin-contact zone + `inspect.skin_contact_continuity` (NEW infra).
