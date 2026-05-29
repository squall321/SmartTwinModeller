# Gap Analysis — Automotive Interior Trim, Bezels, Vents

Domain angle: HVAC air vents (barrel + blades), bezels with foam-seal grooves,
tactile button caps, speaker grilles with hidden support ribs, cup-holder rings
with TPE over-mold, wire-harness clip mounts (SAE / J-1939), automotive-rated
USB-A / USB-C port bezel cutouts.

Reference benchmark vehicles for "concrete product example" callouts:
Hyundai Sonata DN8 instrument-panel center stack, Kia EV6 door-trim armrest,
Tesla Model Y center-console cup-holder ring, Mercedes W213 turbine vent,
Ford F-150 fuse-box harness routing.

---

## 1. Survey — what already exists in the library

Skills that **partially** touch this domain (and why each falls short):

| Existing skill | What it does | Why it does NOT cover automotive trim |
|---|---|---|
| `grille_pattern` (macro) | hex/grid/radial array of through-holes in a window | speaker-grille style only — no class-A surface preservation, no hidden support ribs behind the grille, no acoustic-fabric retainer pocket |
| `hole_array` | point-list cylindrical holes | not a vent — no aerodynamic louver geometry, no air-direction control |
| `o_ring_groove` (atomic) | planar-face circular ring groove, semi-circle/rect profile (v1 limited to ±Z) | only circular path; HVAC bezels need racetrack / D-shape / freeform foam-seal grooves with bulb cross-section |
| `snap_hook` | cantilever + box-lip on +Z faces | axis-aligned only — no off-axis or curved class-A snap, no PSA pull tab, no service-tool release ramp |
| `cable_clip` (modify_boss) | small L-shaped retainer on a planar face | infant-state — no SAE J-1739 dressed-loom geometry, no fir-tree push-mount, no rosebud / christmas-tree fastener |
| `cable_routing_channel` | semi-circular polyline channel in ±Z face | not a clip mount, has no retention features |
| `heat_stake_boss` | cylindrical post + flared crown | unrelated to trim |
| `louver` (`modify_sheet/louver.py`) | sheet-metal stamped 3-side-cut flap | wrong physics — this is a SHEET-METAL stamped louver, not a moulded HVAC barrel/blade with adjustable pivot |
| `dimple` (sheet) | stamped sheet dimple | not relevant — interior trim is moulded, not stamped |
| `text_emboss`, `embossed_pattern` | text and pattern emboss | could be used for brand mark but no automotive-grain (Mold-Tech MT-11xxx) wrap |
| `final_fillet` | fillet-all near-sharp edges | no A-class continuity guarantee (G2/G3), no curvature-monotonic enforcement |
| `draft_apply_auto` (modify_mold) | applies draft to all near-vertical faces | does not handle B-side undercuts that REQUIRE side-action core pulls — common in vent surrounds and click-in bezels |
| `surface_finish_tag` | metadata tag of surface finish | tagging only — no actual grain projection, no MT-11xxx visual approximation |

**Verdict:** the library is well-tooled for phone / wearable plastic housings,
but it has **zero coverage of the four canonical automotive-trim primitives**:
(a) adjustable HVAC blade with barrel pivot, (b) racetrack foam-seal groove
(closed-cell or open-cell PE/PUR), (c) Christmas-tree / fir-tree push fastener
for harness retention, (d) class-A surface region tagging with G2 continuity
preserved across feature edges.

---

## 2. Top Missing — concrete skills

Format: `skill_name` — one-line behavior. **Why** + standard + priority + OCCT.

### P0 — true blockers for an automotive-trim demo

1. **`hvac_blade_array`** — atomic.
   Generates N parallel rectangular blade solids inside a duct outlet, each
   with a coaxial pivot pin centered along its span; blades inherit a user
   `tilt_deg` so the array opens/closes uniformly.
   - Args: `outlet_bbox`, `n_blades:int`, `blade_thickness_mm`,
     `blade_chord_mm`, `pivot_axis:Literal["X","Y"]`, `pivot_diameter_mm`,
     `tilt_deg`, `inter_blade_gap_mm`, `end_clearance_mm`.
   - **Why:** every HVAC vent (Sonata DN8 center vents, Mercedes turbine vent
     when in "horizontal-blade" mode) requires this. Without it the LLM cannot
     model the most identifiable part of an interior. Currently faked as
     "rib + circular_pattern + hole" — fails because there is no pivot pin and
     no end-clearance with the cheek wall.
   - **Standard:** internal HVAC airflow tolerance — VDA 270 (interior airflow
     symmetry ± 2°). Pivot diameter typically Ø2.0–Ø3.5 mm per OEM cheek-sheet.
   - **OCCT:** moderate — N×(box + cylinder + boolean) per blade, then either
     fuse-into-housing or keep as separate sub-bodies for assembly.

2. **`barrel_pivot_socket_pair`** — atomic.
   Cuts the two facing cheek pockets in a duct housing that receive a blade
   pivot pin (one round through, one round blind/slotted for snap-in
   insertion). Optionally adds a retention bump that requires elastic
   deflection on assembly.
   - Args: `cheek_face_a`, `cheek_face_b`, `pivot_axis`, `pivot_diameter_mm`,
     `clearance_mm`, `snap_in:bool`, `retention_bump_height_mm`.
   - **Why:** without paired sockets the `hvac_blade_array` cannot rotate. This
     is the canonical mate-receiver and a true "single feature, two faces"
     skill — the library currently has no skill that places coupled features
     on two opposing faces at once.
   - **Standard:** typical pivot fit ISO 286 H9/h9 for plastic-on-plastic
     pivots; bump height 0.15–0.30 mm for ABS/PC snap-in.
   - **OCCT:** moderate — needs face-pair selector (new) + two coordinated
     blind-hole cuts in the same coordinate frame.

3. **`foam_seal_groove_racetrack`** — atomic / macro.
   Sweeps a U or bulb-shaped groove along a **racetrack / freeform closed
   curve** (not just a circle) on a planar face, with an outer retention lip.
   Profile choices: rect / D / bulb (mushroom).
   - Args: `face_selector`, `path:list[(x,y)]` (closed), `width_mm`,
     `depth_mm`, `profile:Literal["rect","D","bulb"]`, `lip_height_mm`.
   - **Why:** every HVAC bezel and every cluster instrument bezel is sealed
     against the IP carrier with a closed-cell PE or open-cell PUR foam strip
     compressed into a racetrack groove. The existing `o_ring_groove` is
     circular-only and (v1) ±Z-only. This is the #1 most-frequent skill in any
     real interior CAD.
   - **Standard:** DIN 7715-E groove dimensions for cellular elastomer;
     typical compression 25–40 % of free thickness; Mercedes MBN 10416 for
     interior foam-seal grooves.
   - **OCCT:** moderate — sweep of a closed wire; needs `BRepOffsetAPI_MakePipeShell`
     with profile orientation (Frenet / Auxiliary).

4. **`christmas_tree_fastener_post`** — atomic.
   Generates a fir-tree push-in fastener post: cylindrical shaft + N
   downward-flaring conical barbs with under-cuts; either as a male post on
   the trim or as a female receiver hole pattern.
   - Args: `face_selector`, `position_xy`, `shaft_d`, `n_barbs:int`,
     `barb_pitch_mm`, `barb_max_d`, `barb_undercut_deg`,
     `mode:Literal["male","female"]`.
   - **Why:** the universal harness / trim-to-BIW retainer
     (e.g. ITW Fastex W-Clip family, A. Raymond Christmas-tree).
     Currently impossible — the library has no concept of intentional
     undercut that mold splits with side-cores must respect.
   - **Standard:** SAE J1739 (push-in fastener interchangeability), VW TL 226
     for trim push-pin retention force (target 50–80 N pull-out, < 25 N
     insertion).
   - **OCCT:** moderate — stacked frustums + booleans; the undercut must be
     tagged so `draft_apply_auto` SKIPS these faces and `core_cavity_split`
     produces a side-action core.

5. **`hvac_barrel_vent_housing`** — macro.
   Builds the full directional HVAC duct outlet from parameters: circular or
   oval outer ring, internal coaxial barrel that rotates inside, horizontal
   blade array spanning the barrel.
   Composes: `revolve_boss` (outer ring) + `revolve_pocket` (barrel cavity)
   + `barrel_pivot_socket_pair` + `hvac_blade_array`.
   - Args: `center`, `outer_d`, `outer_l`, `barrel_d`, `n_blades`,
     `default_tilt_deg`, `default_yaw_deg`.
   - **Why:** the iconic Mercedes "turbine vent", Audi RS dial vent,
     BMW round B-pillar vent. A single macro spec is the right granularity for
     LLM composition.
   - **Standard:** general — ergonomic reach SAE J1100, airflow direction VDA 270.
   - **OCCT:** hard — composition is the easy part, but rotating-barrel
     interference with horizontal blades must be checked at multiple `tilt_deg`
     positions.

### P1 — major capability gaps

6. **`overmold_pocket_with_keying`** — atomic.
   Creates the pocket in a hard substrate (PC/ABS) that will receive a TPE
   over-mold, including **mechanical key features**: through-holes, undercut
   channels, or sharp-edged "interlocking castellations" along the bond line.
   Optionally tags the bond face as `bond_2k_face`.
   - Args: `face_selector`, `pocket_path`, `pocket_depth_mm`,
     `keying:Literal["through_holes","undercut_channel","castellations"]`,
     `key_pitch_mm`, `key_size_mm`.
   - **Why:** every soft-touch grip ring on a cup-holder (Tesla Model Y center
     console), every soft armrest cap (EV6 door), every steering-wheel grip
     uses a 2-shot TPE/TPV over-mold. Mechanical keying is non-negotiable for
     bond strength.
   - **Standard:** typical TPE adhesion-by-shape per VDI 2019; SAE J1545 for
     interior-trim peel strength (> 8 N/25 mm).
   - **OCCT:** moderate — pocket + boolean array of keying features; needs
     bond-face tag propagation for downstream 2K mold split.

7. **`grille_with_acoustic_pocket`** — macro.
   Same as `grille_pattern` but adds (a) a recessed step on the rear side
   sized to seat a non-woven fabric / mesh, (b) hidden cross-ribs behind the
   visible hole array for structural support without showing on the A-side.
   - Args: superset of `grille_pattern` + `fabric_pocket_depth_mm`,
     `rib_pitch_mm`, `rib_height_mm`, `rib_orientation_deg`.
   - **Why:** door speaker grilles (e.g. Bose / Harman in EV6 doors) MUST hide
     a structural rib lattice on the rear so the thin perforated A-side does
     not vibrate at speaker pressure peaks. The existing `grille_pattern`
     produces only a hole pattern in a thin sheet — buzzes catastrophically.
   - **Standard:** Bose / Harman OEM packaging spec — rib pitch ≥ 8 mm,
     rib height ≥ 1.5 × wall.
   - **OCCT:** moderate — composition of `grille_pattern` + `extrude_pocket`
     (acoustic pocket) + `rib` array.

8. **`button_cap_tactile_dome`** — atomic.
   Creates a tactile button cap top surface: spherical/elliptical concave
   dwell area centered on the press axis, surrounded by a guard ring.
   Records its actuation axis as a tag for downstream travel-stack analysis.
   - Args: `face_selector`, `position_xy`, `cap_d_mm`,
     `dwell_radius_curve_mm` (concave R for fingertip), `guard_height_mm`,
     `actuation_axis:Literal["+Z","-Z"]`.
   - **Why:** every HVAC fan-speed button, every steering-wheel mute button.
     Currently impossible to model the "soft thumb dwell" curve — `extrude_boss_blended`
     gives an outward dome (wrong direction). Affects ergonomic reach analysis
     SAE J1100.
   - **Standard:** GMW 14872 (HMI button geometry); typical dwell R 8–12 mm,
     guard 0.2–0.4 mm proud.
   - **OCCT:** moderate — boss + revolve_pocket with concave R, fillet edges.

9. **`port_cutout_usb_c_automotive`** — atomic (catalog-backed).
   Cuts a USB-C (or USB-A) port window with the **exact** automotive-grade
   chassis flange envelope: bezel relief, drain notch at bottom, retention
   barb pockets. Selectable from a catalog of standard connector envelopes.
   - Args: `face_selector`, `position_xy`, `port_type` (Literal of catalog
     key e.g. `USBC_TYPE2_AUTOMOTIVE` / `USBA_AEC_Q200`), `wall_thickness_mm`,
     `add_drain_notch:bool`.
   - **Why:** every automotive USB charging point (front of center console,
     rear-seat passenger). Hand-modeling the 8.94 × 3.26 mm bezel + side
     retention pockets per Molex / TE catalog is error-prone and the #1 cause
     of rework when the connector vendor changes.
   - **Standard:** USB IF Type-C R2.1 § 3.4 bezel envelope; AEC-Q200 for the
     automotive grade tag; SAE/USCAR-2 sealed-connector cavities.
   - **OCCT:** trivial once the catalog of envelopes exists — cutout is just
     a sketch extrude.

10. **`cup_holder_ring_with_overmold_groove`** — macro.
    Composite skill: cylindrical cup-holder bore + top flange + over-mold
    groove (calls `overmold_pocket_with_keying`) around the rim for the soft
    rubber grip + finger-relief scallops on the side wall.
    - Args: `center`, `bore_d_mm`, `bore_depth_mm`, `flange_w_mm`,
      `rim_overmold_w_mm`, `n_scallops`, `scallop_d_mm`.
    - **Why:** Tesla Model Y / Hyundai Ioniq 5 center console rear cup-holder.
      LLM needs a single named macro to compose this; otherwise it stitches
      cylinder/pocket/scallop steps and gets the rim parametrics wrong.
    - **Standard:** SAE J1100 H-point reach for cup-holder placement; typical
      bore 76–87 mm to accept 20-oz tumbler.
    - **OCCT:** moderate — composition only, but requires the new
      `overmold_pocket_with_keying` to exist first.

11. **`harness_routing_clip_fir_tree`** — atomic.
    Fir-tree push-mount harness clip body (the cable-side counterpart of
    `christmas_tree_fastener_post`) — saddle/cradle that holds an N-mm
    diameter loom with snap-over cap, footprinted onto a planar face.
    - Args: `face_selector`, `position_xy`, `bundle_d_mm`,
      `n_barbs_at_base:int`, `cap_style:Literal["snap","tie_slot"]`,
      `orientation_deg`.
    - **Why:** the actual production part used on every wiring harness;
      A. Raymond / ITW Fastex catalog parts are 80 % of all clip mounts in
      a vehicle. The existing `cable_clip` is a generic L — not a fir-tree
      saddle clip. Different physics (snap-over vs. lay-in).
    - **Standard:** SAE J1654 (harness retention force ≥ 30 N axial), VW TL 824.
    - **OCCT:** moderate — saddle revolve + fir-tree barb stack from skill #4.

12. **`class_a_surface_region_tag`** — atomic (metadata-rich).
    Tags a face (or set of faces) as a class-A region and asserts curvature
    continuity (G2 minimum) across its boundary edges with adjacent class-A
    faces; downstream skills like `final_fillet` and `surface_offset` must
    respect the tag (e.g. preserve curvature, use connected fillet only).
    Optional: emit a warning if a subsequent operation breaks G2.
    - Args: `face_selector`, `continuity:Literal["G1","G2","G3"]`,
      `roughness_ra_max_um`, `grain_id:str|None`.
    - **Why:** the entire **point** of automotive trim is the visible A-surface.
      Without first-class tagging the LLM has no way to constrain "don't
      fillet across this boundary" or "this is the bond line, not the
      A-surface". Currently `surface_finish_tag` only records a free-text
      tag — not enforced.
    - **Standard:** VDA 6.1 surface-quality classification; OEM-specific
      e.g. BMW GS 90100, GM GMW 14668.
    - **OCCT:** moderate — needs new "tagged face attribute" propagated by
      history-map; pure-metadata otherwise.

13. **`mold_undercut_relief_with_side_action`** — atomic.
    Marks one or more faces as side-action core regions, then offsets/relieves
    the surrounding solid so the main A/B mold halves can release without
    fouling. Generates a placeholder side-core direction vector for the
    downstream `core_cavity_split`.
    - Args: `face_selector` (undercut faces), `side_pull_direction:Vector3`,
      `relief_mm`, `core_clearance_mm`.
    - **Why:** every snap-in bezel, every Christmas-tree clip, every side-grip
      detail on a cup-holder REQUIRES a side-action core. The current
      `draft_apply_auto` will happily over-draft these faces and destroy the
      undercut. This skill is the explicit "leave this alone, it gets a side
      core" tag — the missing link between class-A intent and mold split.
    - **Standard:** RTV / Moldflow standard practice; AGMA / SPI mold practice
      guide §4 (side actions).
    - **OCCT:** moderate — face tagging + small geometric clearance offset;
      the harder downstream `core_cavity_split` does the actual split.

### P2 — nice to have

14. **`grain_surface_projection`** — atomic / cosmetic.
    Projects a grain pattern (catalog: MT-11010 "leather", MT-11020 "pebble",
    MT-11050 "matte") onto a tagged class-A face as a tiny height field; for
    visualization + downstream tooling spec. Probably mesh-domain only.
    - Args: `face_selector`, `grain_catalog_id`, `amplitude_um`.
    - **Why:** entire interior is grained — the demo "looks right" if grains
      are visible. Mold-Tech grain catalog is the de-facto standard.
    - **Standard:** Mold-Tech / Standex grain catalog; VDA 270 for interior.
    - **OCCT:** hard if true B-rep, trivial if mesh-displacement.

15. **`detent_click_ramp`** — atomic.
    Small annular bump-ring on an internal cylindrical face that creates a
    tactile detent when an inner barrel rotates past it. Used in rotary HVAC
    barrel vents and rotary knob shafts.
    - Args: `cylindrical_face_selector`, `n_detents:int`, `bump_height_mm`,
      `bump_axial_width_mm`, `ramp_angle_deg`.
    - **Why:** the "click" feel as a vent barrel passes through center / end
      stops — without it the barrel-vent macro feels lifeless. Composes with
      `circular_pattern`.
    - **Standard:** none normative; OEM HMI feel spec (typical 0.3–0.5 N·mm
      detent torque).
    - **OCCT:** moderate — small revolved bump + circular_pattern on a
      cylindrical face; needs cylinder-unwrap aware placement.

---

## 3. Cross-Cutting Infrastructure Gaps

These are **not** automotive-specific skills — they are infrastructure that
the trim domain exposes as missing in the framework:

- **Closed freeform sweep path on arbitrary planar face** — multiple skills
  above (foam-seal groove, over-mold groove) need to sweep along a
  user-supplied **closed planar polyline** on a **non-±Z** face. The current
  helpers (`o_ring_groove`, `cable_routing_channel`) are circular-only or
  ±Z-restricted.
- **Face-pair selector** — `barrel_pivot_socket_pair` needs to pick two
  facing cheek faces and place coordinated features on them. The current
  `selector_kinds` enumeration has no `face_pair_facing` primitive.
- **Undercut-aware draft** — `draft_apply_auto` should accept a "skip-faces"
  selector or read an undercut-relief tag so it doesn't destroy intentional
  undercuts on Christmas-tree posts and snap-in lips.
- **First-class face metadata propagation** — class-A tag, bond-2K tag,
  undercut tag should ride the history map; today only the `tag_face` skill
  attaches a string and downstream skills don't read it.
- **Two-material body model** — `overmold_pocket_with_keying` implies the
  result is a 2K part. There is no skill that returns "body A + body B
  bonded along face X" — only single-body returns. Affects 2K trim, decoded
  buttons with light-pipe inserts, etc.
- **Connector / fastener catalog ingestion** — `port_cutout_usb_c_automotive`
  and `christmas_tree_fastener_post` should both pull dimensions from a JSON
  catalog of standard parts (USB IF / A. Raymond / ITW / Molex). Currently
  no catalog mechanism.
- **Curvature-continuity enforcement** — once class-A regions are tagged,
  `final_fillet` / `surface_offset` should fail or warn on G0 transitions.
- **Side-core direction vector as model attribute** — distinct from pull
  direction; needed by `core_cavity_split` to split correctly for vehicles
  with snap-in vent bezels.

---

## 4. Catalogs Needed

Per-domain JSON catalogs that should ship with the skill library:

1. **Automotive connector catalog** (USB-A, USB-C, HDMI, 8-pin SAE J1939,
   USCAR-2) — bezel envelope, retention pocket geometry, drain notch flag.
2. **Push-fastener catalog** — A. Raymond, ITW Fastex, TE 2-1419234-x —
   shaft Ø, barb pitch, max panel thickness, pull-out force.
3. **Foam-seal cross-section catalog** — DIN 7715-E rectangular / bulb /
   D-shape profiles; default compression %.
4. **Mold-Tech grain catalog** — MT-11010 .. MT-11500 with depth (µm) and
   visual descriptor; references VDA 270.
5. **OEM bezel-style catalog** — round / rect / racetrack outlets with
   typical wall thickness, draft, blade count, default chord — keyed by
   common vehicle programs as anonymized presets ("compact_sedan_dash_vent",
   "luxury_turbine_vent", "rear_bpillar_vent").

---

## 5. Concrete Product Examples (for test cases)

| Vehicle / part | Skills exercised | Why it's a good test |
|---|---|---|
| Hyundai Sonata DN8 center HVAC vent | `hvac_barrel_vent_housing`, `hvac_blade_array`, `barrel_pivot_socket_pair`, `foam_seal_groove_racetrack`, `christmas_tree_fastener_post` | covers all 5 P0 skills in one part |
| Kia EV6 door armrest cup-holder | `cup_holder_ring_with_overmold_groove`, `overmold_pocket_with_keying`, `class_a_surface_region_tag`, `mold_undercut_relief_with_side_action` | exercises 2K trim + class-A |
| Tesla Model Y center console USB-C bay | `port_cutout_usb_c_automotive`, `harness_routing_clip_fir_tree`, `cable_routing_channel` | catalog-backed cutout + downstream harness |
| Mercedes W213 turbine vent | `hvac_barrel_vent_housing`, `detent_click_ramp`, `class_a_surface_region_tag` | adds HMI feel + A-surface enforcement |
| Ford F-150 IP cluster bezel | `foam_seal_groove_racetrack`, `grille_with_acoustic_pocket`, `button_cap_tactile_dome` (HVAC fan buttons), `christmas_tree_fastener_post` | speaker grille + bezel seal + buttons |

---

## 6. Notes

- The single most-impactful single addition is **`foam_seal_groove_racetrack`**
  (P0) — it unblocks practically every bezel in an interior. Recommend
  implementing this first, even before the HVAC blade family, because it
  forces resolution of the "closed freeform sweep on non-±Z face" infra gap
  that several other P0 / P1 skills also need.
- The library has a strong "phone / wearable / mold-friendly geometry" bias
  visible in the manifest categorization (`modify_pattern`, `modify_pocket`,
  `modify_boss`). To handle automotive trim cleanly, consider adding a top-
  level `modify_trim` category for skills 1, 2, 5, 7, 10, 13 to keep the
  ontology honest.
- LLM composition argument: making 1/2/3/4 atomic with a single macro at #5
  is the right granularity — gives the LLM both leaf control (animate a
  single blade) and one-shot composition (drop a whole vent in).
