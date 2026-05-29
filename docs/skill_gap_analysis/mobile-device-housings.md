# Gap Analysis: Mobile Device Housings (smartphone / tablet / e-reader)

Scope: outer chassis + display window + camera island + IO cutouts (USB-C / Lightning / 3.5 mm jack / SIM tray) + side buttons + speaker/mic acoustic ports + wireless charging / MagSafe / NFC coil seats + co-molded plastic insert pockets + antenna gaps + edge ergonomics. Target reproducibility: a credible iPhone 15 Pro mid-frame, a Galaxy S24 Ultra rear shell, an iPad Pro 12.9" stylus side, a Kindle Paperwhite back cover.

## 1. Survey — what exists today that touches this domain

Direct hits in the current registry (101 manifest skills + 5 unregistered in source tree):

- `create.rounded_slab` — macro. Foundational slab + corner radius. Adequate for an iPhone-class envelope but **does not** capture top/bottom asymmetric radius (S-shape Apple "halo" arc) nor side wall waist taper (Galaxy "Edge" curve).
- `modify_antenna.antenna_slit` — atomic. Linear straight slit only. Width 0.5–1 mm. **Does not** model the multi-segment slit network on iPhone (~12 slits arranged in a rectangular ring) nor the laser-cut "tri-slot" pattern.
- `modify_antenna.polymer_inlay` — atomic. Unions a polymer fill of the same path as the slit. **Does not** model the visible Vapor-blast band or color-matched PC/ABS overmolded plug surface flush with the body's anodized finish.
- `modify_pocket.grille_pattern` — macro. hex / grid / radial hole array in a rectangular window. Width-limited (≤200 mm) and produces straight cylindrical holes; **no** conical mic port profile and **no** curved-array (radial-fanout) for speaker grilles on rounded edges.
- `modify_pocket.breathing_hole_array` — atomic. Region-clipped hex pattern on planar ±Z faces, in source tree (registered). Solves mic membrane vent, but **does not** apply to curved bottom-edge speaker grilles where the holes must follow the silhouette of a rounded bottom edge.
- `modify_pocket.hole`, `hole_array` — drilled cylinders. Used today for any IO cutout but lacks the **chamfered lead-in**, **stepped guide bore**, or **radius-corner slot** that USB-C and Lightning shells require.
- `modify_pocket.extrude_pocket`, `extrude_plateau`, `extrude_through` — generic rectangular cutouts. Two-step display step-down (`bezel + adhesive groove`) is currently a hand-built two-pocket sequence; no semantic skill ties them together.
- `modify_pocket.extrude_pocket_to_curved_floor`, `wrap_sketch_on_curved_surface`, `swept_pocket_along_curve`, `swept_pocket_variable_section` — covers a curved camera-bump edge, but no **stepped camera-ring with three coaxial diameters** macro (cosmetic outer ring + lens barrel clearance + IR sensor relief).
- `modify_boss.snap_hook`, `mounting_pad`, `boss_with_hole`, `standoff` — internal mech. No **side-button rocker pivot** (the small molded bridge + flex relief) nor **SIM-tray socket** (rectangular cavity with two parallel sliding rails + eject hole).
- `modify_boss.o_ring_groove` — circular planar groove only. **No rectangular bonding-adhesive groove** for display lamination (3M 9495 / Tesa 4972 tape relief).
- `modify_curvature.loft_side_profile`, `chamfer_*`, `face_face_fillet`, `variable_radius_fillet*` — supports edge ergonomic schedule but no preset for the **"3D-curved CNC arc"** (iPhone 12+ flat band with R0.5 top + R0.3 bottom asymmetry) or for the **Gorilla Glass 2.5D edge** (R0.4 transparent rollover).
- `modify_pattern.circular_pattern`, `linear_pattern`, `mirror_feature`, `path_pattern` — enough for MagSafe magnet ring placement *if* the magnet pocket itself exists (it doesn't).
- `modify_mold.draft_apply_auto`, `parting_surface`, `core_cavity_split` — useful for back covers but no preset for **co-molded TPU + PC bumper** parting at the chamfered seam.
- `assembly.add_component`, `interference_check` — present, can validate USB-C connector clearance if model imported.
- `create.import_step` — can pull OEM USB-C STEP, but no **standardized cutout generator** that derives the chassis hole from the connector envelope.

Adjacent macros we'd lean on: `modify_pocket.honeycomb_pattern` (vent), `text_engrave` / `text_emboss` (regulatory marks).

**Bottom line.** The library has all the *primitive verbs* to model a phone, but lacks the *named noun vocabulary* — the parametrically-correct cutouts for USB-C, SIM tray, MagSafe, speaker port, side-button bezel, display step-down, NFC coil — that should be drop-in by spec ID. Every reproduction today must re-derive the cutout dimensions from a connector datasheet, which is exactly what the skill catalogue should encapsulate. Antenna handling is a single straight slit, when real iPhones have a *network* of 4–12 slits + polymer plugs at exact splice points.

## 2. Top Missing Skills

Numbered by priority. Each entry: name → behaviour → standard → why → OCCT difficulty.

### P0 — Blockers (cannot model a credible production phone without these)

1. **`usb_c_receptacle_cutout`** (modify_pocket, macro)
   Chassis cutout for a USB Type-C receptacle. Outer **rectangular slot 8.94 mm × 2.56 mm** (tongue clearance per USB-IF spec 2.1), inner **stepped lead-in chamfer** 0.20 mm × 30° around the mouth, optional **EMI ground spring contact relief** (two 0.6 × 0.3 mm side notches at the receptacle midline), and rear **shield can clearance** (a deeper +Z pocket 9.40 × 3.20 × `shield_depth` for the metal shroud). Corner radius 0.30 mm (mandatory — straight corners chip the anodize).
   Args: `face_selector`, `position_xy`, `orientation_deg`, `with_shield_pocket` (bool, default true), `shield_depth_mm` (default 7.35 for vertical mount, 6.65 for mid-mount), `lead_in_chamfer_mm` (default 0.20), `emi_relief` (bool, default true).
   Standard: USB Type-C Cable and Connector Specification Rev 2.1 §3.2.1 (receptacle envelope 8.94 × 2.56 mm tongue, 8.34 × 2.56 mm interface plug entrance).
   Why: every iPhone 15+, every Android, every iPad. Today the LLM types `extrude_through` with a 9 × 3 hole (wrong — 0.06 mm too wide, plug rocks), no chamfer (plug scratches the bezel), no shield pocket (mid-mount receptacle won't sit). One skill removes 80 % of the most common reproduction error.
   OCCT: **trivial** (rounded rectangle + stepped pocket).

2. **`sim_tray_pocket`** (modify_pocket, macro)
   Slot for a nano-SIM or eSIM hybrid tray. **Rectangular through-cavity 12.30 × 9.30 mm × `tray_thickness` + 0.05 mm** (nano-SIM tray = 12.30 × 8.80 × 0.67 mm per ETSI TS 102 221 mini-UICC 4FF, plus 0.50 mm gasket lip), two **parallel guide rails** 0.20 mm deep on the long sides, a **0.70 mm Ø eject pinhole** offset 1.50 mm from one short edge, and a **0.10 mm interference seal lip** on the inner long edge for the silicone IP gasket.
   Args: `face_selector`, `position_xy`, `orientation_deg`, `sim_form_factor` (literal: 4FF_nano | esim_hybrid | dual_4ff), `tray_thickness_mm` (default 0.67), `gasket_seal` (bool, default true), `eject_hole_side` (literal: top | bottom).
   Standard: ETSI TS 102 221 "4FF" nano-SIM (12.30 × 8.80 × 0.67 mm). Dual SIM trays follow same standard with shared shell.
   Why: every cellular phone and many tablets. Currently impossible to spec without arithmetic on a SIM datasheet, and the eject pinhole is always wrong (LLM tends to drift to 1.0 mm which jams the eject pin).
   OCCT: **trivial** (extrude_through + two thin pocket strips + small cylinder).

3. **`display_bezel_step_with_adhesive_groove`** (modify_pocket, macro)
   Two-coaxial-step pocket on the front face: **outer optical-bond step** (depth = cover_glass_thickness, typically 0.55 mm for Gorilla Glass Victus + 0.05 mm bond gap) and **inner LCM step** (depth = panel_thickness, typically 1.20 mm) with a **0.40 × 0.20 mm rectangular adhesive groove** around the inner perimeter for liquid optically clear adhesive (LOCA) or pressure-sensitive adhesive (PSA) tape relief. Optionally produces the **0.10 mm × 45° lift chamfer** at the cover-glass step.
   Args: `face_selector`, `cover_glass_outline_sketch`, `cover_glass_thickness_mm` (default 0.55), `panel_thickness_mm` (default 1.20), `adhesive_groove_width_mm` (default 0.40), `adhesive_groove_depth_mm` (default 0.20), `bond_gap_mm` (default 0.05), `lift_chamfer_mm` (default 0.10).
   Standard: Corning Gorilla Glass 7i / Victus 2 nominal 0.55 mm; 3M 9495LE tape = 0.17 mm + relief.
   Why: every smartphone, tablet, e-reader. Today the LLM calls `extrude_pocket` twice and skips the adhesive groove → display delaminates in thermal cycling. This is the single most-repeated front-face feature, deserving a named noun.
   OCCT: **trivial** (two coaxial pockets + a thin sweep groove).

4. **`camera_ring_stepped`** (modify_boss + modify_pocket, macro)
   Coaxial **triple-step camera island**: outer **cosmetic ring boss** (Ø_outer, height = bump_height, with R0.20 perimeter fillet), middle **lens hood lip** (Ø_lens + 0.10 mm clearance, height = lens_hood_h), inner **lens barrel clearance pocket** (Ø_lens + 0.05 mm, depth = lens_barrel_h), and optional **flash / LiDAR / IR satellite holes** (list of `(Ø, dx, dy)` tuples). Conformance with iPhone Pro stepped "camera plateau" style or Galaxy "ring camera" style via `style` literal.
   Args: `face_selector`, `center_xy`, `style` (literal: ios_plateau | android_ring | pixel_bar), `outer_d_mm`, `bump_height_mm`, `lens_d_mm`, `lens_hood_h_mm`, `lens_barrel_h_mm`, `satellites` (list of `(d_mm, dx_mm, dy_mm, kind)` with kind = flash | lidar | tof_ir | mic).
   Standard: no ISO, but supplier modules (Sony IMX989, Samsung GN3) publish lens barrel Ø 11–18 mm with ±0.05 mm seat tolerance.
   Why: most visually distinctive feature of modern phones; reproduction without a dedicated skill takes 6–10 atomic calls and is error-prone (lens hood lip is usually missing → glare flare in product photos).
   OCCT: **moderate** (plateau + 2 coaxial pockets + N satellite holes).

5. **`side_button_aperture`** (modify_pocket, macro)
   Rectangular through-slot for a side button (power, volume up/down, action button) with: **rounded corner radius 0.30 mm**, **outward bezel chamfer 0.15 × 45°** on the cosmetic side, **inward retention shoulder** (0.10 mm wide × 0.20 mm deep flange that prevents the button cap from falling out), and an optional **silicone gasket gland** (0.30 × 0.20 mm rectangular sweep). Pitch and length conform to the spring-tact switch travel of 0.25 mm + cap throw.
   Args: `face_selector` (the cylindrical side face), `position_along_edge_mm`, `length_mm`, `width_mm` (default 2.20), `bezel_chamfer_mm` (default 0.15), `retention_shoulder_mm` (default 0.10), `with_gasket` (bool, default true).
   Standard: Alps SKHH series tact switch travel 0.25 mm; Apple "Action Button" 16 mm × 2.2 mm.
   Why: every phone has 2–4 of these. `extrude_through` produces no shoulder, so the button falls inside the chassis during over-mold demolding.
   OCCT: **moderate** (cut + retention shoulder requires a non-uniform offset).

6. **`magsafe_magnet_ring`** (modify_pocket + modify_pattern, macro)
   Ring of **18 magnet pockets** arranged in a Ø 56.0 mm pitch circle on the back face, each pocket Ø 5.5 × 1.6 mm with a 0.05 mm `proud` recess (magnet sits sub-flush), plus a **center alignment magnet** (Ø 8 × 1) and a **secondary identification magnet** (Ø 3 × 1) at the 6-o'clock position 28.0 mm from center. Produces also the **closed-loop sweep cut** at the inner ring that accepts the NFC alignment coil (1 mm wide × 0.3 mm deep, Ø 36 mm).
   Args: `face_selector`, `center_xy`, `n_perimeter_magnets` (default 18), `perimeter_pitch_d_mm` (default 56.0), `magnet_d_mm` (default 5.5), `magnet_h_mm` (default 1.6), `proud_mm` (default -0.05), `include_center_magnet` (bool, default true), `include_id_magnet` (bool, default true), `include_alignment_coil_groove` (bool, default true).
   Standard: Apple MagSafe ecosystem spec (Made for MagSafe MFM); Qi 1.3 EPP center coil Ø 36 mm.
   Why: iPhone 12 onwards and all MagSafe-compatible cases / accessories. Today this is `circular_pattern` of `hole` calls — but pitch radius is rarely right (Apple = 28 mm from center, not 27 or 29) and the secondary ID magnet at 6 o'clock is always forgotten.
   OCCT: **trivial** (circular_pattern + 2 holes + 1 annular sweep).

7. **`speaker_acoustic_chamber`** (modify_pocket, macro)
   Internal **rectangular cavity + tuned port** for a microspeaker (typically 11×15×3 mm rectangular driver per Knowles / Goertek catalog). Produces: driver seat pocket (X × Y × `driver_h` + 0.05 clearance), **back-volume cavity** (default 1.0 cm³ per Helmholtz tuning for low-end response), **front port tunnel** (rectangular sweep from driver face to the bottom-edge grille face, tunnel cross-section = `port_area_mm2`), and the **grille hole pattern** at the exit (delegates to `breathing_hole_array`). All internal faces optionally lined with mesh / damping foam seat (0.20 mm offset for mesh adhesion).
   Args: `face_selector`, `driver_module` (literal: knowles_2403 | goertek_1115 | ams_3015 | custom), `back_volume_cm3` (default 1.0), `port_area_mm2` (default 6.0), `port_path` (list of XYZ waypoints), `grille_at_port_exit` (bool, default true).
   Standard: Knowles/Goertek microspeaker datasheets; Helmholtz resonator design for back-volume tuning (no ISO, industry tooling).
   Why: every phone has a bottom-firing speaker. Currently `extrude_pocket` + `grille_pattern` independently, with no link between driver size and chamber volume. Mid-frequency tuning is wrong → tinny audio.
   OCCT: **moderate** (multi-step pocket + swept port channel + delegated grille pattern).

### P1 — Major (large quality jump but workarounds exist)

8. **`mic_port_conical`** (modify_pocket, atomic)
   Conical microphone port: **outer Ø 0.80 mm** (cosmetic) tapering inward over `chassis_thickness − mesh_seat` mm to an **inner Ø 1.20 mm** chamber that seats the MEMS mic mesh (Saati Acoustex). Includes optional **0.10 mm radius hyperbolic blend** at the outer mouth to prevent wind whistle. Distinct from a straight `hole` because the cone profile drives acoustic SNR.
   Args: `face_selector`, `position_xy`, `outer_d_mm` (default 0.80), `inner_d_mm` (default 1.20), `mesh_seat_depth_mm` (default 0.30), `with_hyperbolic_mouth` (bool).
   Standard: Knowles SPH0645 MEMS mic acoustic port recommendation (0.7–0.9 mm outer, 1.1–1.3 mm inner).
   Why: 3–4 mic ports per phone (top, bottom, rear, beam-forming). A straight cylinder mic port whistles at wind speeds > 5 m/s — a known design defect in early reproductions.
   OCCT: **trivial** (revolve a 2-segment line profile).

9. **`bonding_adhesive_groove_rect`** (modify_pocket, atomic)
   Rectangular sweep groove along a closed planar perimeter (typically the display bezel or back-glass bezel) sized to clear a die-cut PSA tape (3M 9495LE = 0.17 mm or Tesa 61395 = 0.20 mm). Profile is a **rectangle with 0.05 mm × 45° outer chamfer** to act as adhesive squeeze-out trap, and depth = `tape_thickness + 0.05` mm. Distinguishes from `o_ring_groove` by rectangular profile and arbitrary closed-loop planar path.
   Args: `face_selector`, `inner_outline_sketch`, `tape_part` (literal: 3M_9495LE | 3M_VHB_4905 | tesa_61395 | custom), `width_mm` (default 0.60), `depth_mm` (default tape_thickness + 0.05), `squeeze_out_chamfer_mm` (default 0.05).
   Standard: 3M optically clear adhesive 9495LE (0.17 mm) and Tesa 4972 (0.10 mm) datasheets.
   Why: cover-glass to mid-frame bond, back-glass to mid-frame bond, display lamination — all three surfaces use this groove. Without it the adhesive squeezes onto the visible bezel.
   OCCT: **trivial** (closed-path sweep cut).

10. **`pencil_magnetic_strip`** (modify_pocket + modify_boss, macro)
    Side-edge **flat seat** for a magnetically-attached stylus (Apple Pencil Gen 2, S-Pen Magnet Charging). Produces: a **shallow concave saddle** (R = pencil_d / 2 + 0.05, length = `magnetic_strip_length_mm`), a **strip of 10–12 small disc magnets** (Ø 1.5 × 0.8 mm) inset 0.10 mm below the saddle surface, and **two pogo-pin clearance holes** (Ø 0.80 mm) at the strip center for wireless-charging coupling. Distinct from MagSafe ring because it is a *linear* strip along a side edge, not a circular pattern on a flat face.
    Args: `edge_selector` (the cylindrical side edge), `start_xyz`, `length_mm` (default 90.0 for iPad Pro 11"), `pencil_d_mm` (default 8.9 for Apple Pencil 2), `n_magnets` (default 12), `pogo_pin_pair` (bool, default true).
    Standard: Apple Pencil 2nd gen (Ø 8.9 mm); S-Pen Pro (Ø 5.8 mm).
    Why: iPad Pro / Air, Galaxy Tab S, Surface Pro. The exact saddle radius and magnet inset depth determine whether the pencil stays attached when shaken. Today: 6+ atomic calls.
    OCCT: **moderate** (sweep concave + linear pattern of magnet pockets).

11. **`nfc_loop_antenna_cavity`** (modify_pocket, atomic)
    Closed-loop **flat planar groove** on the inside face of the rear cover that seats a flex-PCB NFC antenna loop (typical: 38 × 28 mm rectangular with 4 mm corner radius). Depth = `flex_thickness + 0.05` (usually 0.15 mm), with two **wire-exit notches** at the connector side and a **0.30 mm wide × 0.10 mm deep alignment rib** down one long side for assembly registration. Distinct from antenna_slit (which goes through the wall).
    Args: `face_selector` (inner ±Z face), `outline_sketch` (closed loop), `flex_thickness_mm` (default 0.10), `depth_mm` (default 0.15), `connector_side` (literal: top | bottom | left | right), `with_alignment_rib` (bool, default true).
    Standard: NFC Forum tag types (TT2/TT4) imply loop area ≥ 600 mm²; ISO/IEC 14443 13.56 MHz.
    Why: every NFC-capable phone. Without the cavity the rear cover's metal coating shorts the loop → NFC range drops to < 1 cm.
    OCCT: **trivial** (closed sweep cut).

12. **`co_molded_insert_pocket`** (modify_pocket, atomic)
    Rectangular or freeform pocket on an **internal** face of a metal frame that will receive a co-molded plastic insert (TPU bumper, PC RF window). Produces: floor of the pocket plus **retention undercut slots** along the perimeter (`undercut_depth_mm` deep × `undercut_height_mm` tall, every `undercut_pitch_mm`) so the plastic mechanically locks into the metal after insert-molding. Also drills **resin gate access hole** (Ø 1.5 mm) at the side of the pocket.
    Args: `face_selector`, `outline_sketch`, `pocket_depth_mm`, `undercut_depth_mm` (default 0.40), `undercut_height_mm` (default 0.30), `undercut_pitch_mm` (default 8.0), `gate_hole_position_xyz`.
    Standard: insert-molding DFG guidelines (Murata / Foxconn); ASTM D638 adhesion tests imply mechanical undercuts > chemical bond.
    Why: every iPhone-style chassis has 3–6 of these — top antenna strip, bottom antenna strip, side rail RF windows. Today modeled as flat pocket with no undercut → plastic falls out under thermal cycling.
    OCCT: **moderate** (pocket + perimeter undercut as a small circular pattern of trapezoidal cuts).

13. **`ergonomic_edge_schedule`** (modify_curvature, macro)
    Applies a **variable-radius corner / edge schedule** along the phone's perimeter that produces the iPhone-style asymmetric edge: top edge R 0.50 mm (cosmetic), bottom edge R 0.30 mm (palm comfort), corner-region transition R 1.20 mm. Wraps `variable_radius_fillet_with_law` with a named preset selector. Includes `style` presets: `apple_flat`, `samsung_curve`, `google_pixel_arc`, `oneplus_sandstone`.
    Args: `top_edge_selector`, `bottom_edge_selector`, `corner_selector`, `style` (literal preset), `top_r_mm`, `bottom_r_mm`, `corner_r_mm`, `transition_length_mm` (default 12.0).
    Standard: no ISO; ergonomic radius guideline ≥ 0.25 mm to avoid skin pinch (ANSI/HFES 100-2007).
    Why: defines the brand identity. Currently the LLM applies one constant `fillet_edges_by_predicate`, which produces a "soap bar" with no character.
    OCCT: **moderate** (variable_radius_fillet with law function).

14. **`anodize_masking_keepout`** (inspect + modify_finish, atomic)
    Tags a face region as **anodize-masked** (or laser-mark / NCVM / PVD masked) and verifies that no machined edge crosses the masking boundary closer than `min_offset_mm` (typically 0.50 mm). Does not modify geometry — produces a `surface_mask_tag` plus a **CSV report** of offending features. Distinct from `tag_face` because it carries process semantics.
    Args: `face_selector`, `process` (literal: anodize_typeII | anodize_typeIII | NCVM | DLC | nano_PVD | laser_mark), `min_keepout_mm` (default 0.50), `assert_fail` (bool, default false).
    Standard: MIL-A-8625 Type II / Type III anodize masking guidance; ASTM B580 hard coat.
    Why: real production failure — a screw hole drilled 0.3 mm inside the anodize mask line shows a bright aluminum ring around the cosmetic surface. Inspect-time skill.
    OCCT: **trivial** (face tag + distance query).

### P2 — Nice to have (catalogue completeness, edge-case usability)

15. **`waterproof_membrane_window`** (modify_pocket, atomic)
    Recessed seat for an **ePTFE Gore acoustic vent** or pressure-equalization membrane (Ø 3.0 mm typical Gore PMI-100070). Two-step: outer adhesive lip (Ø + 1.0 mm × 0.10 deep) + center exhaust through-hole (Ø `vent_d_mm`). The adhesive lip needs flat, planar finish per Gore application note. Distinct from grille_pattern because it's a single sealed membrane site.
    Args: `face_selector`, `position_xy`, `membrane_part` (literal: gore_PMI-100070 | saati_AP9900 | custom), `vent_d_mm` (default 1.50), `adhesive_lip_w_mm` (default 0.50).
    Standard: Gore Protective Vents PMI series application notes; IPX7 / IP68 pressure-equalization requirement.
    Why: every IP68 phone has 2–3 of these (over each mic, over the pressure sensor). Today modeled as a plain hole — no adhesive lip → leak.
    OCCT: **trivial**.

## 3. Cross-cutting infrastructure gaps

- **Connector catalogue.** The library has no first-class **connector envelope library** keyed by datasheet (Foxconn UB Type-C, JAE DX07, Amphenol Lightning if Apple-licensed). Every cutout skill above would benefit from a shared `ConnectorEnvelope` record (X × Y × Z + tongue position + shield height + retention finger shape). Today every `usb_c_*` call would re-spell the same numbers.
- **Acoustic component catalogue.** Microspeaker (Knowles, Goertek, AAC) and MEMS mic (Knowles, Infineon) modules with X/Y/Z + acoustic port position. The `speaker_acoustic_chamber` skill above is half-useless without this catalogue lookup.
- **Magnet catalogue.** NdFeB N52 disc magnets by Ø × T (3 × 1, 4 × 1, 5 × 1.6, 6 × 2, 8 × 2, 10 × 3) with pull-force annotation. Driven by MagSafe and Pencil features.
- **Cover-glass material catalogue.** Corning (Gorilla Glass 6 / 7i / Victus / Victus 2) and Schott (Xensation) by `glass_thickness × bending_radius × surface_compression`. Plays into `display_bezel_step_with_adhesive_groove` defaults.
- **Adhesive tape catalogue.** 3M 9495LE / 9472LE / 4905 / VHB; Tesa 4972 / 61395 — by thickness, optical clarity, and shear strength. Drives `bonding_adhesive_groove_rect` defaults.
- **Polymer overmold compatibility.** Two-shot insert molding compatibility table: TPU on PC, PC/ABS on PA, etc. Drives `co_molded_insert_pocket` material annotation.
- **Edge-region semantic selectors.** No `top_edge`, `bottom_edge`, `volume_button_side`, `power_button_side`, `usb_port_edge` named-region selectors — these are required for `ergonomic_edge_schedule` and for placing buttons consistently across reproductions. Currently rely on bbox-derived selectors, which break when the slab dimensions change.
- **Antenna network spec.** `antenna_slit` is one straight slit. A real phone has a network: bottom 4 slits, top 2 slits, sides 2–6 slits, with **gap polymer plugs at exact splice points**. A `antenna_slit_network` macro that takes a list of (start_xyz, end_xyz) pairs + polymer fill flag would be much more aligned with real geometry.
- **IP-rating verification.** No inspect skill that walks all `o_ring_groove` + `bonding_adhesive_groove_rect` + `waterproof_membrane_window` instances and certifies "IP68 envelope is closed" (no through-holes outside sealed regions).
- **Co-molded parting line semantic.** `parting_surface` exists for molding, but not for **multi-material co-molding** where parting is between metal frame and polymer insert — different DFM rules apply.

## 4. Domain-specific catalogues needed

These are read-only datasheet tables the skills must reference (each row keyed by part number → returns the geometric defaults).

1. **USB Type-C / Lightning / 3.5 mm jack** receptacle envelopes (USB-IF Spec Rev 2.1 §3.2; Apple MFI; ISO/IEC 60130-9).
2. **Nano-SIM / micro-SIM / eSIM tray** geometry (ETSI TS 102 221).
3. **Microspeaker** form factors (Knowles, Goertek, AAC) — X/Y/Z + port position + acoustic Thiele-Small.
4. **MEMS microphone** form factors (Knowles SPH06xx series, Infineon IM69D130) — package + acoustic port Ø.
5. **NdFeB magnet** disc sizes for MagSafe / stylus / hinge applications.
6. **NFC antenna** flex-PCB outline sizes (38×28 / 42×30 / 44×30 are typical).
7. **Wireless charging coil** (Qi A11, A28, A33, AW33) — OD / ID / thickness / ferrite-core Ø.
8. **Cover-glass** thicknesses (Corning Gorilla Glass family; Schott Xensation).
9. **Optical-clear adhesive / PSA tape** thicknesses (3M, Tesa).
10. **ePTFE acoustic vent** membranes (Gore PMI / Saati Acoustex Air).
11. **Tact switch / dome switch** travel + cap dimensions for side buttons (Alps SKHH).
12. **Stylus** dimensions (Apple Pencil 1 / 2 / 3 / Pro; S-Pen; Surface Pen).

## 5. Examples — what becomes possible

Once 8–15 skills above are implemented:

- **iPhone 15 Pro chassis reproduction (8 skill calls instead of ~50):**
  1. `rounded_slab(146.6×70.6×8.25, R6)`
  2. `display_bezel_step_with_adhesive_groove(front, glass=victus2, panel=1.20)`
  3. `camera_ring_stepped(rear, style=ios_plateau, outer_d=42, lenses=3+lidar+flash)`
  4. `usb_c_receptacle_cutout(bottom_edge, with_shield_pocket=True)`
  5. `speaker_acoustic_chamber(bottom_edge, driver=knowles_2403, port_to_grille)` ×2
  6. `mic_port_conical(top_edge, 0.8/1.2)` + `(bottom_edge)` + `(rear_beamform)`
  7. `side_button_aperture(left_edge, action_button + volume_up + volume_down)`
  8. `magsafe_magnet_ring(rear, center=(0,0), n=18, with_center_magnet)`
  9. `nfc_loop_antenna_cavity(rear_inner, outline=...)`
  10. `ergonomic_edge_schedule(style=apple_flat, top_r=0.5, bottom_r=0.3)`
  11. `sim_tray_pocket(left_edge, form=4FF_nano)`
  12. `co_molded_insert_pocket(side_rail, 6× RF windows)`
  13. `antenna_slit_network([12 slits])` → `polymer_inlay` each
  14. `anodize_masking_keepout(cosmetic_perimeter, 0.5mm)` → verify

- **iPad Pro 12.9 stylus side reproduction:** `pencil_magnetic_strip(right_edge, length=90, pencil_d=8.9, with_pogo)` — single call. Currently ~10 atomic calls.

- **Galaxy S24 Ultra back shell:** `magsafe_magnet_ring(skip)` + `camera_ring_stepped(style=android_ring)` ×4 (separate lens rings, not a plateau) + S-Pen silo (a new `stylus_silo_pocket` could be a P2 add).

- **Kindle Paperwhite back:** `usb_c_receptacle_cutout` + `side_button_aperture(power)` + `waterproof_membrane_window` (pressure equalization for IPX8) + ergonomic edge — IP68 envelope verification ensures the device is sealed.

In all four cases the reproduction goes from 30–60 atomic calls (with arithmetic and standard lookup the LLM gets wrong ~15 % of the time) to 8–14 named-noun calls with datasheet-correct defaults baked in.
