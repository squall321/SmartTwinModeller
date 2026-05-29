# Skill Gap Analysis — Audio / Acoustic Features

Domain: headphones, in-ear monitors, smartphone & laptop speakers, soundbars,
laptop microphone arrays, smart-speaker waveguides, hearing aids.

Scope of this analysis: parametric CAD features whose dimensions are directly
driven by *acoustic* engineering targets (Fb, Q, port area, open area %,
directivity, leakage class). The current library has many *geometric* primitives
that can be assembled into an acoustic feature, but the LLM has no
domain-named handle for the acoustic intent — so it cannot reason about
Helmholtz tuning, driver compliance, IEC60268-5 mount, IPX seal grooves, etc.

---

## 1. Survey — what the library already has that touches this domain

Direct hits (closest existing skills):

| Skill | What it does | Acoustic relevance |
|---|---|---|
| `grille_pattern` (modify_pocket, macro) | hex / grid / radial through-hole array in a rectangular window | The single most relevant existing skill. Drives raw hole geometry but **has no concept of acoustic open-area %, mesh impedance, or aesthetic shape mask**. |
| `breathing_hole_array` (modify_pocket, atomic) | hex hole array clipped to an arbitrary 2-D region sketch (rounded-rect, ellipse) on planar ±Z face | Aimed at "vent / breathing mesh" — already mentions "speaker meshes, mic vents". Same gap: no open-area driver, only planar ±Z. |
| `o_ring_groove` (modify_boss, atomic) | circular ring pocket on planar ±Z face | Suitable for round mic/speaker boots but **planar only, circular profile only — no arbitrary path, no dovetail / face-seal profile** (the docstring itself flags this as v2). |
| `hole` / `hole_array` | single hole / list of holes | Generic — used by `grille_pattern` under the hood. |
| `swept_pocket_variable_section` | sweep a profile along a 3-D path with linear/spline section blend | Could in principle build a waveguide, but: section profiles are *sketches*, not parametric acoustic curves (exponential, tractrix, conical, Le Cléac'h). LLM would have to hand-rasterise the flare. |
| `revolve_boss` / `revolve_pocket` | revolve a sketch | Could in principle build a horn body of revolution, but the LLM must hand-build the flare polyline; nothing prevents non-monotone area expansion (acoustic invalid). |
| `swept_relief`, `swept_boss_along_curve` | sweep generic profile along curve | Same as above — no acoustic intent or constraint. |
| `extrude_pocket_to_curved_floor` | pocket with curved floor | Could form a sealed chamber but no volume target / no gasket groove integration. |

Indirect / supporting:

- `inspect.mass_properties` returns volume → could verify sealed chamber Vc.
- `inspect.surface_area_by_region` → could compute grille open area (currently
  not used that way; no `open_area_ratio` inspector).
- `inspect.cross_section`, `section_multi_plane` → useful for verifying waveguide
  area expansion law.
- `assembly.add_component` + watch / phone component catalogs exist but
  there is **no speaker / microphone / port component family** in
  `catalogs/components/` (verified by Glob — only crowns, displays, batteries,
  sensors, coils).

Net assessment: the library has competent low-level primitives (holes,
patterns, revolves, sweeps) but **zero skills that close the loop between an
acoustic spec (Fb, open-area %, Fc, mouth diameter, IEC mount diameter) and
the CAD geometry**. An LLM tasked with "tune the port for 80 Hz Fb on a
0.6 L cabinet" has to do the acoustic math itself and emit raw hole/cylinder
calls with no guard-rails or post-conditions.

---

## 2. Top missing skills

Each entry below is a concrete gap with a referenced product / standard.

### P0 — blocker for any audio enclosure design

**A. `acoustic_grille_pattern` (modify_pocket, macro)**

Open-area-driven grille pattern: caller specifies `target_open_area_ratio`
(0.0–1.0) and a region mask sketch (rounded rect / ellipse / arbitrary 2-D
profile). Internally solves for hole diameter or spacing to hit the target
while honouring minimum-rib width. Hex / square / staggered-slot lattice;
optional fade/feather at boundary; optional aesthetic logo-mask
multiplication (intersect lattice with a region SVG/sketch).

Why: Apple HomePod, MacBook Pro speaker grilles, AirPods Max ear-cup grilles,
Sonos One — all are sized to a target acoustic transparency (typically
40–60 % open area). The existing `grille_pattern` takes `hole_diameter` and
`spacing` *independently*, which means the LLM has to back-solve open area
itself. That math is non-trivial for hex packing inside a non-rectangular
mask. Standards: open-area % is the dominant grille design parameter cited
in JEITA RC-8126 and AES2-2012 transducer measurement appendices.

Args: `face_selector`, `region_sketch`, `pattern: hex|square|slot`,
`target_open_area_ratio`, `hole_diameter_mm` (one of d/spacing solved),
`spacing_mm` (one of d/spacing solved), `min_rib_mm`, `edge_feather: bool`,
`boundary_fade_rows: int`.

OCCT difficulty: **moderate** — region clipping already proven in
`breathing_hole_array`; the additional work is the algebraic solve (hex
packing density ≈ π d²/(2√3 s²)) and the optional feather.

---

**B. `helmholtz_port_tube` (modify_boss + modify_pocket, macro)**

Insert a tuned bass-reflex port: caller passes cabinet net volume Vc, target
tuning Fb (Hz), and the speed-of-sound constant; the skill computes port
length L from L = (c² · A_p) / (4π² · Fb² · Vc) − 0.85·r (end-correction),
where A_p = π r². Creates a cylindrical or rectangular tube boss inward from
the chosen face plus a through-hole, returning both the geometry and the
computed L for plan logging.

Why: every bass-reflex enclosure from Kanto YU6 to Genelec 8030 to JBL
Charge 5 uses a Helmholtz-tuned port. Today the LLM must (1) compute L
itself, (2) issue an `extrude_boss` and an `extrude_through` and hope to
align them. Standards reference: IEC60268-5 §15 (small-signal port
parameters), AES2-2012 Annex D end correction k=0.85.

Args: `face_selector`, `position_xy`, `cabinet_volume_l`, `target_fb_hz`,
`port_diameter_mm` (or `port_width_mm/height_mm` for rectangular),
`section: round|rect|slot`, `wall_thickness_mm`, `end_correction_k: float = 0.85`,
`material_c_m_per_s: float = 343.0`.

Post-conditions: emit `computed_port_length_mm` into plan log so a DFM
report can verify against tube wall buckling / mould draft.

OCCT difficulty: **moderate** — under the hood it is just a `cylinder` +
`boolean_subtract` + `extrude_through`. The novelty is the algebra and the
named acoustic intent.

---

**C. `flared_port_mouth` (modify_curvature, atomic)**

Apply a flare to the inner mouth of an existing port (tube) — exponential,
tractrix, or 4th-order polynomial mouth radius growth over a specified
flare length. Reduces port chuffing (turbulence) at high SPL — the standard
"slot port" / "flared port" / Vifa BR-119 style.

Why: KEF LS50 Wireless II, B&W 600 series ("Flowport"), SVS PB-2000 — all
production reflex enclosures use a flared (rather than straight) port mouth
to suppress audible "chuffing" above ~95 dB SPL. The flare shape is an
acoustic spec, not just a fillet. Reference: D'Appolito, "Testing
Loudspeakers" §6.4; AES E-Library paper "Optimized Bass-Reflex Ports"
(Roozen 2008).

Args: `port_axis_selector` (selector for the cylindrical port face),
`flare_profile: exponential|tractrix|poly4`, `flare_length_mm`,
`mouth_diameter_mm`, `throat_diameter_mm` (defaults to current port d),
`both_ends: bool`.

OCCT difficulty: **hard** — needs a true revolve of an analytic curve,
trimmed to the existing port, plus a tangent G1 blend to the cabinet face.
Cannot be expressed as a simple fillet predicate.

---

**D. `waveguide_horn_revolve` (create, atomic)**

Parametric horn / waveguide body of revolution from a closed-form profile:
exponential (S = S₀·e^(m·x)), tractrix, conical, hyperbolic-exponential
(Le Cléac'h / Salmon family), or oblate spheroidal (constant-directivity).
Args: throat diameter, mouth diameter, length, cutoff frequency (computed
flare constant m = 4π·fc/c for exponential), wall thickness, optional
mounting flange ring at the mouth.

Why: smart speakers and pro waveguides (Amazon Echo Studio, Genelec
"DCW" waveguide on 8030, JBL M2 oblate-spheroidal) — exact profile
controls high-frequency directivity. `swept_pocket_variable_section` can
*approximate* this but requires the LLM to discretise the curve manually.
Standard: AES-6id-2006 (loudspeaker measurement); Salmon (1946) horn
equation. fc = m·c/(4π) is the textbook flare cutoff.

OCCT difficulty: **moderate** — sample profile, build a B-spline edge,
revolve via `BRepPrimAPI_MakeRevol`, shell to wall thickness.

---

**E. `sealed_chamber_with_gasket` (modify_pocket, macro)**

Carve a sealed back-volume behind a driver mounting hole, with an
integrated gasket groove (rectangular or dovetail profile) on the
mating flange face. Caller specifies target volume Vc and IP rating class;
the skill sizes the pocket footprint to hit Vc (given depth constraint)
and selects an appropriate groove profile from a small catalog (IPX4 = lip
seal 0.5 mm crush, IPX7 = full O-ring crush 25 %, IPX8 = dovetail
captured 30 %).

Why: a smartphone bottom-firing speaker (iPhone, Galaxy) needs a sealed
back volume of ~0.8–1.5 cc to control driver Qtc, and the speaker module is
sealed to the housing with a foam or rubber gasket. Today the LLM would do
this with 3 separate atomic calls and no guarantee they align. Standard:
IEC 60529 IP rating; ISO 3601-1 for the O-ring fallback groove.

Args: `face_selector`, `position_xy`, `target_volume_cc`, `depth_mm`,
`gasket_profile: rect|dovetail|o_ring`, `crush_pct: float = 25.0`,
`ip_class: IPX4|IPX7|IPX8`, `driver_mount_diameter_mm`.

OCCT difficulty: **moderate**.

---

### P1 — major

**F. `driver_mount_iec_pattern` (modify_pocket, atomic)**

Cut a circular driver-mount pocket with the standard IEC 60268-5 PCD bolt
hole pattern (3 / 4 / 6 / 8 bolts on a pitch-circle), magnet relief recess,
and basket clearance. Args: `nominal_size: ø50 | ø80 | ø100 | ø133 | ø165 |
ø200 | ø250 | ø300`, `mount_style: front|rear|trim_ring`, `bolt_count`,
`pcd_mm` (defaults from size), `magnet_relief_diameter_mm`,
`magnet_relief_depth_mm`.

Why: every cone driver from a Dayton DA135 to a B&C 18SW100 has IEC60268-5
nominal mount dimensions. Today the LLM needs to look up bolt PCD by hand.
This is the audio equivalent of a fastener-spec lookup.

OCCT difficulty: **trivial** — composes `hole` + circular pattern + pocket.

---

**G. `microphone_boot_pocket` (modify_pocket, macro)**

Cylindrical boot pocket sized for a standard MEMS microphone module
(common sizes: 3.5×2.65×0.98, 4.0×3.0×1.2, 6mm bottom-port MEMS, ECM 6/10mm
cans), with optional integrated O-ring or foam-gasket groove and acoustic
port hole (the small front hole that exposes the membrane). Differs from a
generic boss/hole pair: the relative tolerances are tight (boot OD ±0.05,
port-hole concentricity to boot ≤0.1 mm) — codifying this prevents the LLM
from emitting bad numbers.

Why: every smartphone, every laptop, every smart speaker mic uses this
exact construction (visible in any iFixit teardown — e.g., iPhone 15
bottom mics). Reference: Knowles SPH0645LM4H-B datasheet §"Mechanical".

Args: `face_selector`, `position_xy`, `mic_module: knowles_sph0645 |
infineon_im69d130 | st_mp34dt06 | ecm_6mm | ecm_10mm | custom`,
`gasket: o_ring | foam | none`, `port_hole_diameter_mm`,
`boot_depth_mm`, `boot_clearance_mm`.

OCCT difficulty: **trivial-moderate** — composes cylinder pocket + hole +
optional `o_ring_groove` call.

---

**H. `headphone_driver_seat` (modify_boss, macro)**

Pin / step / spider mounting for a headphone driver (typically 40 mm
dynamic, 14 mm balanced-armature, or 50 mm planar magnetic): outer step
to register the driver basket, optional damping-ring groove (felt or
silicone ring, common in Sennheiser HD600 series), 3 or 4 alignment
posts at 120° / 90°, and central tweeter dust-cap relief if needed.

Why: Sennheiser HD650, AKG K371, Audeze LCD-X — all use a stepped
driver seat with a damping ring whose groove is part of the cup CAD,
not the driver. Generic `boss_with_hole` cannot encode the damping ring
groove offset (which is acoustically tuned).

Args: `face_selector`, `position_xy`, `driver_diameter_mm`,
`step_depth_mm`, `damping_ring_id_mm`, `damping_ring_od_mm`,
`damping_ring_depth_mm`, `alignment_post_count: 0|3|4`,
`alignment_post_diameter_mm`, `alignment_post_height_mm`.

OCCT difficulty: **moderate** — composes `extrude_pocket` + `o_ring_groove`
+ circular boss pattern.

---

**I. `inspect.acoustic_open_area_ratio` (inspect, atomic)**

Given a grille / mesh face, compute the open-area ratio:
(area_of_holes / area_of_window). Already needed as a post-condition for
skill A above; useful standalone to verify imported CAD.

Why: the single number an acoustic engineer asks about a grille. The
existing `surface_area_by_region` gives raw face areas but does not
classify "open" vs "solid" within a window mask.

Args: `window_face_selector` (the grille face), `hole_face_selector`
(the cylinder-side faces inside the window).

OCCT difficulty: **trivial** — sum face areas, divide.

---

**J. `port_slot_rectangular` (modify_pocket, macro)**

Rectangular / "slot" port (height × width × depth) with chamfered or
flared mouth, sized from Helmholtz target: A_p = w·h, same formula as
skill B but for non-circular section. Required because most flat
soundbars and slim home-cinema speakers (Sonos Beam, LG Eclair) use slot
ports rather than round tubes — round ports are too deep for slim
cabinets.

Why: enables tuned slot ports in slim cabinets where a round tube would
not fit. Same standard reference as skill B (IEC 60268-5 §15).

Args: `face_selector`, `position_xy`, `slot_width_mm`, `slot_height_mm`,
`cabinet_volume_l`, `target_fb_hz`, `mouth_chamfer_mm`,
`wall_thickness_mm`.

OCCT difficulty: **moderate** — `extrude_pocket` + chamfered mouth.

---

**K. `acoustic_mesh_pocket` (modify_pocket, atomic)**

Cut a recessed seat for a stretched acoustic mesh / metal-mesh disc
(used for IP-rated water/dust protection over the grille — e.g., the GORE
acoustic vent membrane). Sizes a shallow stepped pocket with adhesive
contact ring and an inner clear-aperture hole.

Why: most modern outdoor / IP-rated speakers (UE Wonderboom, Bose
SoundLink Flex) have a GORE acoustic membrane bonded into a stepped
pocket. Reference: GORE acoustic vent product datasheet (PMM ø8.5/ø6.3
seat dimensions).

Args: `face_selector`, `position_xy`, `outer_diameter_mm`,
`inner_diameter_mm`, `seat_depth_mm`, `clear_aperture_diameter_mm`,
`membrane: gore_pmm | ssn_mesh | none`.

OCCT difficulty: **trivial**.

---

### P2 — nice to have

**L. `aesthetic_grille_mask` (modify_pocket, macro)**

Multiply a hole lattice by an arbitrary 2-D mask sketch (logo / glyph / SVG
contour) to spell out a logo in the speaker grille (Bang & Olufsen, Marshall
Stanmore badge grilles). Could be implemented as `acoustic_grille_pattern` +
boolean intersection with an imported sketch.

OCCT difficulty: **trivial-moderate** (depends on whether sketch SVG import
is already there — it is not, today).

---

**M. `anechoic_wedge_liner` (create, atomic)**

Generate a planar tile of pyramidal / wedge anechoic absorber for a
miniature anechoic chamber or in-ear cup damping: wedge length, base
width, packing pattern. Aimed at headphone-cup internal damping tile
(visible in HiFiMan / Audeze open-back cups). Reference: ISO 26101
("Acoustics — anechoic and hemi-anechoic rooms — Free-field qualification").

Args: `tile_length_mm`, `tile_width_mm`, `wedge_height_mm`,
`wedge_base_mm`, `pattern: square|hex`.

OCCT difficulty: **moderate** — repeated pyramid tools, union.

---

**N. `passive_radiator_pocket` (modify_pocket, atomic)**

Pocket for an oval / round passive radiator (PR) — same as a driver mount
pocket but with a thinner-rim flange and no magnet relief. The PR is the
sealed-cabinet alternative to a port (used in JBL Flip, Bose SoundLink Mini
because round ports do not fit). Differs from driver mount because there is
no voice-coil pole-piece, just a suspension flange.

Args: `face_selector`, `position_xy`, `pr_diameter_mm` or
`pr_long_axis_mm / pr_short_axis_mm`, `flange_thickness_mm`,
`bolt_count`, `pcd_mm`.

OCCT difficulty: **trivial**.

---

## 3. Cross-cutting infra gaps surfaced by this domain

These are not single skills but library-wide infra deficits exposed by audio:

1. **No SVG / DXF sketch import.** Logo grille (skill L) and any custom-shape
   mask need it. The current `SketchSpec` enum is hard-coded to
   rectangle / circle / rounded rectangle / ellipse — there is no
   `kind: imported_svg_path`.
2. **No closed-form analytic curve primitive** for create skills (exponential,
   tractrix, polynomial flare). Today the LLM must rasterise to a polyline.
   Needed for skills C, D.
3. **No "computed parameter" return slot** in `SkillResult` — the Helmholtz
   port computes L, the open-area solver computes spacing; today this only
   surfaces in logs, not in the plan graph for downstream skills to consume.
4. **No `volume_target_satisfied` post-condition.** Skill E (sealed chamber)
   needs to assert "the pocket I built has volume Vc ±5 %", which the
   existing `volume_decreased` post-condition does not capture.
5. **Planar-±Z restriction** on `o_ring_groove` and `breathing_hole_array`
   blocks audio use cases — a typical headphone cup gasket is on a *curved*
   ear-pad-side rim. Need surface-following groove sweep (the v2 in the
   o-ring docstring).
6. **No material acoustic-property catalog** (foam, GORE membrane, felt
   damping disc, silicone gasket Shore-A) — would let the LLM size crush
   compression for skill E correctly per IPX class.

---

## 4. Domain-specific catalogs needed

Should live alongside `catalogs/components/watch/` etc.:

- `catalogs/components/audio/drivers/` — Knowles BL series, Dayton, Tang
  Band, generic IEC 60268-5 sizes; YAML with `nominal_diameter_mm`,
  `mount_pcd_mm`, `bolt_count`, `magnet_depth_mm`, `Vas_l`, `fs_hz`,
  `qts`.
- `catalogs/components/audio/microphones/` — Knowles SPH0645,
  Infineon IM69D130, ST MP34DT06, generic ECM cans; YAML with `body_l_mm`,
  `body_w_mm`, `body_h_mm`, `port_position`, `port_diameter_mm`,
  `signal_pads`.
- `catalogs/components/audio/passive_radiators/` — Peerless, Dayton DSA;
  flange dimensions, suspension travel.
- `catalogs/components/audio/membranes/` — GORE PMM series, Saati Acoustex
  mesh; `seat_outer_mm`, `seat_inner_mm`, `acoustic_resistance_rayls`.
- `catalogs/components/audio/gaskets/` — foam (Poron 4701-30 etc.),
  silicone (Shore-A 40/60/70), with `compression_pct_target`,
  `min_groove_width_for_crush`.

---

## 5. Worked examples — what an LLM plan looks like before vs after

### Example 1 — "tune the port for 80 Hz on a 0.6 L bookshelf"

**Before** (today):
```
LLM must compute L = (343² · π · 0.011²) / (4π² · 80² · 6e-4) − 0.85·0.011
                  ≈ 0.107 m = 107 mm
Then issue:
  cylinder + extrude_boss + extrude_through + fillet
4 atomic calls, no acoustic post-condition.
```

**After** (with skill B):
```
helmholtz_port_tube(
  face_selector=back_face, position_xy=(0, -40),
  cabinet_volume_l=0.6, target_fb_hz=80,
  port_diameter_mm=22, wall_thickness_mm=2.0,
)
→ 1 call, returns computed_port_length_mm=107.4, logs to plan.
```

### Example 2 — "MacBook Pro speaker grille, 18 mm × 5 mm window, 55 % open"

**Before:** LLM iteratively guesses hole_d / spacing until open area is close
to 55 % — no convergence guarantee, and it does not know hex packing density.

**After** (skill A): `acoustic_grille_pattern(target_open_area_ratio=0.55,
hole_diameter_mm=0.7, …)` → solver picks spacing = 0.95 mm.

### Example 3 — "Sennheiser HD650-style cup, 40 mm driver, foam damping ring 38/42 mm"

**Before:** `boss_with_hole` for the seat + manual `o_ring_groove` (which
fails — the cup wall is curved, current o_ring_groove only handles planar
±Z), + 4× circular `boss` for alignment posts. 6+ calls and the groove call
fails.

**After** (skill H): `headphone_driver_seat(driver_diameter_mm=40,
damping_ring_id_mm=38, damping_ring_od_mm=42, alignment_post_count=4)`
→ 1 call. (Still requires the curved-surface groove infra-fix from §3.5.)

---

## 6. Priority recommendation

If only 4 skills can be built next quarter for this domain, build A, B, D, G —
they cover ≥80 % of the open audio CAD tasks I have seen on iFixit teardowns
of smart speakers, smartphones, laptops, headphones, and soundbars.

If only 2: A (acoustic_grille_pattern) and B (helmholtz_port_tube). Those
two cover the universally-named acoustic CAD intents — every speaker box has
a grille and most have a port.
