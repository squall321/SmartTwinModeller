# Master Skill Gap Synthesis

**Date**: 2026-05-29
**Inputs**: 10 per-domain gap analyses in this directory.
**Method**: Deduplicate the 148 raw missing-skill entries by concept (not by exact wording), re-rank by cross-domain demand, then identify the highest-leverage next implementation pack.

---

## 1. Headline Counts

| Metric | Value |
|---|---|
| Per-domain reports analyzed | 10 |
| Raw missing-skill entries | 148 |
| Cross-cutting infra mentions | 76 |
| Catalog mentions | 70 |
| **Distinct skill concepts after dedup** | **~95** |
| **P0 skill concepts** (deduped) | **38** |
| **P1 skill concepts** (deduped) | **41** |
| **P2 skill concepts** (deduped) | **16** |

### Coverage verdict

- The library has ~150 OCCT primitives but is critically thin in three layers:
  1. **Named-noun catalogues** (connectors, bearings, magnets, coils, fasteners, O-rings, membranes, coin cells, microspeakers, LEDs, etc.) — every domain demands them.
  2. **Cross-cutting metadata systems** (class-A region tags, biocompat tags, cosmetic-side, colorway, undercut tags, fit-class tags) that downstream skills should consume.
  3. **DFM enforcement layer** for both CNC/sheet-metal and injection molding — the catalogs declare rules but no inspect skill checks them.

---

## 2. Top 20 Missing Skills (Ranked by Cross-Domain Demand × Priority)

Ranking heuristic: a P0 mentioned by 3 domains beats a P0 mentioned by 1; ties broken by OCCT feasibility.

| Rank | Skill / Concept | Cross-domain demand | Priority | OCCT |
|---|---|---|---|---|
| 1 | **`connector_cutout_from_catalog`** (USB-C / USB-A / Lightning / Lemo / Redel / DIN 41612 / Molex) | Mobile, Automotive, Medical (3 domains, all P0) | P0 | trivial-moderate |
| 2 | **`o_ring_groove_path`** — closed-loop swept groove on non-planar / non-circular path with profile selector (rect / D / dovetail / bulb) | Mobile, Wearables, Audio, Automotive, Medical, Power tools (6 domains) | P0 | moderate |
| 3 | **`magnet_pocket_axial`** + standardized NdFeB disc catalog | Mobile (MagSafe), Wearables (AirPods, lid, charging) (2 domains, both P0) | P0 | trivial |
| 4 | **`wireless_charging_coil_seat`** (Qi A11/A28/AW33) | Mobile, Wearables (2 domains, both P0) | P0 | trivial |
| 5 | **`bearing_seat`** (counterbore + shoulder + chamfer + optional retainer groove, catalog-keyed) | Power tools, Wearables (hinge), Medical (2-3 domains) | P0 | trivial-moderate |
| 6 | **`vent_membrane_pocket`** (Gore PMF / PMI, Saati Acoustex) — recessed seat + back-hole pattern + bond land | Mobile, Audio, Medical (3 domains) | P0/P1 | trivial |
| 7 | **`face_seal_land_pair`** / **`bonding_adhesive_groove_rect`** — paired sealing surfaces with controlled compression / adhesive squeeze-out trap | Medical, Mobile, Wearables (3 domains) | P0 | moderate-hard |
| 8 | **`inspect_wall_thickness`** (ray-march thickness sampler with band band/min/max/ratio) | DFM-IM, DFM-CNC, Medical (shared infra) | P0 | moderate |
| 9 | **`inspect_undercut_zones`** / **`cnc_undercut_check`** — per pull/setup direction shadow / dot product | DFM-IM, DFM-CNC, Automotive, Mobile (4 domains) | P0 | moderate |
| 10 | **`class_a_surface_tag`** + **`continuity_audit`** — class-A region metadata + G2/G3 audit | Surface-Aesthetics, Automotive, Mobile (3 domains) | P0 | moderate |
| 11 | **`speaker_acoustic_chamber`** / **`helmholtz_port_tube`** / **`acoustic_grille_pattern`** — driver-volume-port-grille | Mobile, Audio (2 domains, both P0) | P0 | moderate |
| 12 | **`coin_cell_cavity`** (IEC 60086-3, CR2032 etc.) | Wearables (1 domain P0, common in medical too) | P0 | trivial |
| 13 | **`captive_screw_tether_pocket`** / **`spring_bar_lug_pair`** / catalog-driven retention features | Medical, Wearables (2 domains, both P0) | P0 | moderate |
| 14 | **`motor_flange_bolt_pattern`** (NEMA / IEC), **`lip_seal_cavity`** (ISO 6194), **`retaining_ring_groove`** (DIN 471/472) | Power tools (3 P0 features in one domain) | P0 | trivial |
| 15 | **`foam_seal_groove_racetrack`** — closed freeform sweep groove on non-±Z face | Automotive, Medical (2 domains; sub-feature of #2) | P0 | moderate |
| 16 | **`hvac_blade_array`** + **`barrel_pivot_socket_pair`** + **`hvac_barrel_vent_housing`** (macro) | Automotive (1 domain, P0) | P0 | moderate-hard |
| 17 | **`christmas_tree_fastener_post`** / **`fir_tree_harness_clip`** + push-fastener catalog | Automotive, Power tools (2 domains) | P0/P1 | moderate |
| 18 | **`equalize_wall_thickness`** / **`inspect_sink_mark_risk`** / **`gate_location_candidates`** / **`cosmetic_side_classify`** — IM DFM core | DFM-IM (1 domain, 4 P0 skills) | P0 | moderate-hard |
| 19 | **`sim_tray_pocket`** / **`display_bezel_step_with_adhesive_groove`** / **`camera_ring_stepped`** / **`side_button_aperture`** / **`magsafe_magnet_ring`** | Mobile (1 domain, named-noun cluster) | P0 | trivial-moderate |
| 20 | **LLM control-plane quartet**: `dry_run_skill`, `suggest_selector_from_phrase`, `predict_post_conditions`, `find_skill_by_intent` | LLM meta (1 domain, dependency for all others) | P0 | moderate-hard |

---

## 3. Per-Domain Summary

| Domain | P0 | P1 | P2 | Biggest single ask |
|---|---|---|---|---|
| **Mobile device housings** | 7 | 7 | 1 | `usb_c_receptacle_cutout` + connector catalog (every iPhone / Galaxy needs it) |
| **Wearables & earbud cases** | 8 | 5 | 2 | `sapphire_glass_seat` + `coin_cell_cavity` + `wireless_charging_coil_seat` + component catalogs (IEC 60086-3, ISO 3601, Qi) |
| **Audio / acoustic features** | 5 | 6 | 3 | `acoustic_grille_pattern` (open-area %) + `helmholtz_port_tube` (Fb / Vc math) |
| **Mechanical power tools** | 5 | 7 | 6 | `bearing_seat` + `lip_seal_cavity` + `motor_flange_bolt_pattern` + helical/bevel gears |
| **Automotive trim** | 5 | 8 | 2 | `foam_seal_groove_racetrack` + `hvac_blade_array` family + `christmas_tree_fastener_post` |
| **Medical device housings** | 6 | 3 | 4 | `face_seal_land_pair` + `cleanability_radius_enforce` + connector catalog + biocompat region tag |
| **DFM injection molding deep** | 6 | 7 | 2 | `inspect_wall_thickness` + `cosmetic_side_classify` + `gate_location_candidates` + sink/undercut/equalize set |
| **DFM CNC + sheet metal** | 4 | 7 | 4 | `inspect.tool_reachability` + `inspect.cnc_undercut_check` + `enforce_min_tool_radius` + countersink/counterbore/tap macros |
| **Surface aesthetics (Class-A)** | 4 | 6 | 5 | `class_a_surface_tag` + `continuity_audit` + `zebra_stripe_view` + `crown_and_flair` |
| **LLM workflow meta-tools** | 4 | 6 | 4 | `find_skill_by_intent` + `dry_run_skill` + `predict_post_conditions` + `suggest_selector_from_phrase` |

---

## 4. Cross-Cutting Infrastructure Gaps

These themes appeared independently in 3+ domain reports. They are not skills — they are platform-level capabilities that gate the proposed skills.

### Theme A — Catalog backbone (mentioned by 9 of 10 domains)

A unified `phone_designer.standards` / `catalogs/` module with:
- Pydantic-typed catalog records (`ConnectorEnvelope`, `BearingSpec`, `OringSize`, `Magnet`, `Membrane`, `LED`, `MEMSMic`, `Microspeaker`, `CoinCell`, `QiCoil`, `Adhesive`, `PushFastener`, `MotorFrame`, `TapDrill`, `Material`).
- YAML data files version-pinned and CI-validated against schemas.
- Used by `..._from_catalog` skills as the **single source of truth** for geometric defaults.

### Theme B — Closed-path sweep on non-planar / non-circular faces (5 domains)

Current `o_ring_groove`, `breathing_hole_array`, `cable_routing_channel` are planar ±Z only. A shared `_path_on_face` primitive that accepts an arbitrary closed planar path in face-local coordinates would unblock: foam-seal grooves, racetrack O-rings, NFC antenna cavities, headphone cup gaskets, adhesive grooves, labyrinth seals.

### Theme C — Cylindrical-face selector / local frame (5 domains)

Many proposed skills need to act on **cylindrical / toroidal** faces with an `(axis, theta, z)` parametric frame: `crown_seal_gland`, `sensor_pcb_seat_ring`, `radial_fin_array`, `keyway_parallel`, `labyrinth_seal_groove`, `side_button_aperture`. The current `_face_normal_at_center` resolver rejects faces with `|normal[2]| < 0.9`.

### Theme D — First-class region / face metadata namespaces (6 domains)

Today `surface_finish_tag` writes `body._pd_finish`, `tag_face` writes `body._pd_tags`. New domains want:
- `body._pd_cosmetic` (IM DFM)
- `body._pd_class_a` (Surface aesthetics)
- `body._pd_biocompat` (Medical)
- `body._pd_colorway` (Surface aesthetics)
- `body._pd_undercut_tag` (Automotive + DFM)
- `body._pd_material` (DFM + Medical)

Recommend consolidating into a single `body._pd_face_attrs: dict[FaceRef, dict]` with namespaced keys + tag-propagation across history.

### Theme E — DFM inspect layer (3 domains)

CNC, IM and Medical all demand a "ray-march thickness sampler" + concavity classifier + setup-direction visibility map. These are shared low-level helpers, not per-domain skills.

### Theme F — Paired-body / multi-body output (3 domains)

`face_seal_land_pair`, `autoclave_flat_seal_pair`, `barrel_pivot_socket_pair`, `overmold_pocket_with_keying`, 2-shot trim modify **two bodies** with coupled geometry. Current `SkillResult` is single-body. Need `SkillPairResult` or a follow-up action queue.

### Theme G — Precondition / failure-mode runtime evaluators (LLM meta + DFM)

Today `preconditions: list[str]` and `failure_modes: list[str]` are **documentation only**. A registry of `pc.<ref> → (body, args) → (bool, reason, evidence)` evaluators + `fm.<ref> → diagnose()` playbooks would unlock `dry_run_skill`, `predict_post_conditions`, `explain_failure`, and DFM auto-repair.

### Theme H — Computed-parameter return slot in SkillResult (Audio + DFM)

`helmholtz_port_tube` computes L; `acoustic_grille_pattern` computes spacing; `cold_runner_sizer` computes Ø; `inspect_wall_thickness` computes ratio. Today these only surface in logs. Need first-class `computed: dict` field in `SkillResult` so downstream skills can consume them.

### Theme I — Selector preview / introspection (LLM meta + every domain)

`selector_preview` exists but `tag_inventory`, `skill_schema_introspect`, `selector_robustness_score`, and `body_state_summary` are missing. These are the cheap-but-load-bearing introspection primitives every multi-turn LLM flow needs.

### Theme J — Multi-direction / side-action draft (DFM + Automotive + Mobile)

`draft_apply_auto` accepts one pull direction. Real parts have side cores and slides. Need `pull_directions: list[Vec3]` + undercut-skip selector + side-action core direction tag.

### Theme K — Sandboxed body copy + dry-run executor mode (LLM meta)

`BRepBuilderAPI_Copy` + tag-dict copy + history reset + `ExecutionMode.PREDICT` in `PlanExecutor`. Required for every meta-tool that previews instead of mutates.

### Theme L — Embedding index + phrase grammar (LLM meta)

`build/skill_embeddings.npz` + `data/phrase_grammar.yaml` to power semantic skill search and natural-language → selector translation.

---

## 5. Catalogs to Build

Aggregated from the 70 catalog mentions across all 10 domains. Sorted by cross-domain reuse.

### Tier 1 — needed by 3+ domains

1. **`catalogs/connectors/`** — USB-A / USB-C / Lightning / Lemo (00/0B/1B/2B) / Redel / ODU MEDI-SNAP / DIN 41612 / IEC 60603-2 / SAE J1939 / Molex automotive. *(Mobile, Automotive, Medical, Power tools)*
2. **`catalogs/materials/`** — thermoplastics (ABS / PC / PC-ABS / PA6 / PA6-GF30 / POM / PMMA / PP / TPU / PEEK / PEI / Tritan / Radel), metals (Al 5052/6061, SUS 304/316, CRS, brass, Cu, Ti). Per material: shrinkage iso/aniso, K-factor curve, min bend R, max L/t, yield, melt T, autoclave compatibility, biocompat status. *(DFM-IM, DFM-CNC, Medical, Wearables)*
3. **`catalogs/o_rings/`** — ISO 3601-1 + AS568 dash numbers, cross-section, gland W/D, squeeze targets. *(Wearables, Mobile, Medical, Audio, Power tools)*
4. **`catalogs/magnets/`** — NdFeB N42/N52 disc (Ø×T standard sizes), pull force. *(Mobile MagSafe, Wearables, Audio drivers indirectly)*
5. **`catalogs/finishes/`** — VDI 3400 grade 12-45, MoldTech MT-11010..11500, SPI A1..D3 — each with Ra (µm), draft requirement (deg), process. *(DFM-IM, Automotive, Surface, Medical)*
6. **`catalogs/vent_membranes/`** — Gore PMF / PMI / Acoustic Vent, Saati Acoustex, Donaldson Tetratex — vent Ø, recess depth, bond land, adhesive, IPX. *(Mobile, Audio, Medical)*
7. **`catalogs/adhesives/`** — 3M 9495LE / 9472 / 467MP / VHB; Tesa 4972 / 61395 — thickness, optical, peel strength, biocompat. *(Mobile, Medical)*

### Tier 2 — needed by 2 domains

8. **`catalogs/bearings/`** — 6000 series + 608 + 6900 + MR-series — d / D / B / r / shoulder. *(Power tools, Wearables)*
9. **`catalogs/seals/`** — ISO 6194 radial shaft seals (TC/TCV form). *(Power tools, Medical)*
10. **`catalogs/retaining_rings/`** — DIN 471 ext + DIN 472 int. *(Power tools, Medical)*
11. **`catalogs/qi_coils/`** — WPC Qi v1.3 A11 / A28 / A33 / AW33 receiver and transmitter coils. *(Mobile, Wearables)*
12. **`catalogs/leds/`** — JEDEC 0402 / 0603 / 0805 / 1206 + side-view. *(Wearables, Audio, Automotive)*
13. **`catalogs/mems_mics/`** — Knowles SPH0645 / Infineon IM69D130 / ST MP34DT06 + ECM 6/10mm. *(Mobile, Audio)*
14. **`catalogs/microspeakers/`** — Knowles, Goertek, AAC by L×W×H + port + TSP. *(Mobile, Audio)*
15. **`catalogs/coin_cells/`** — IEC 60086-3 (CR2032 / 2025 / 2016, SR41/44, LR41/44). *(Wearables, Medical)*
16. **`catalogs/captive_fasteners/`** — PEM PF11/PF50, Southco 47/48. *(Medical, Power tools)*
17. **`catalogs/push_fasteners/`** — A. Raymond / ITW Fastex / TE — Christmas-tree, fir-tree. *(Automotive, Power tools)*
18. **`catalogs/motor_frames/`** — NEMA 17/23/34, IEC 56/63/71/80. *(Power tools, Automotive)*
19. **`catalogs/colorways/`** — Pantone + gloss + paint route. *(Surface, Automotive)*
20. **`catalogs/tap_drills/`** — ISO 965-1 / ANSI B1.13M tap-drill sizes per thread spec. *(DFM-CNC, Mobile, Power tools)*

### Tier 3 — single-domain but high-value

21. `catalogs/sapphire_crystals/` (Wearables — Crystaloid / Comadur)
22. `catalogs/strap_pitches/` (Wearables — ISO 18684)
23. `catalogs/tactile_domes/` (Wearables / Automotive — Snaptron / Diptronics)
24. `catalogs/stylus/` (Mobile — Apple Pencil, S-Pen)
25. `catalogs/cover_glass/` (Mobile — Gorilla Glass family, Schott)
26. `catalogs/hot_runner_nozzles/` (DFM-IM — Mold-Masters, Husky, Synventive)
27. `catalogs/cooling_lines/` (DFM-IM)
28. `catalogs/slide_kits/` (DFM-IM — DME/Hasco)
29. `catalogs/cold_runner_diameters/` (DFM-IM — Beaumont 5th-power)
30. `catalogs/sheet_gauges/` (DFM-CNC)
31. `catalogs/end_mills/` (DFM-CNC — by Ø / flute / length)
32. `catalogs/keys_keyways/` (Power tools — DIN 6885)
33. `catalogs/splines/` (Power tools — ISO 4156 / DIN 5480)
34. `catalogs/pipe_threads/` (Power tools — NPT, BSPP)
35. `catalogs/biocompat_polymers/` (Medical — Ultem / Radel / Tritan / Makrolon Rx / PEEK / LSR)
36. `catalogs/foam_seal_profiles/` (Automotive — DIN 7715-E)
37. `catalogs/grain_textures/` (Automotive / Surface — Mold-Tech MT-110xx)
38. `catalogs/audio_drivers/` (Audio — IEC 60268-5 standard mounts)
39. `catalogs/passive_radiators/` (Audio)

---

## 6. Recommended Next Implementation Pack (10 skills)

Selection criteria: high cross-domain reuse × high cost-per-error today × infrastructure that unblocks downstream packs. Each entry includes one-line rationale.

| # | Skill | Category | Rationale |
|---|---|---|---|
| 1 | **`connector_cutout_from_catalog`** | modify_pocket macro | Mentioned P0 by 3 domains (Mobile, Automotive, Medical). Every USB-C / Lemo / DIN cutout today is hand-spelled and wrong ~10–30 % of the time. Forces and validates the `catalogs/connectors/` backbone — pays off immediately. |
| 2 | **`o_ring_groove_path`** (closed-loop swept groove with profile + non-planar support) | modify_boss atomic | Mentioned by 6 domains (Mobile, Wearables, Audio, Automotive, Medical, Power tools). Unlocks racetrack gaskets, foam-seal grooves, headphone cup seals, watch backsides, adhesive bond lines. Forces the **closed-path sweep on non-±Z face** infra (Theme B). |
| 3 | **`vent_membrane_pocket`** (Gore PMF / Saati / generic) | modify_pocket macro | Mentioned P0/P1 by Mobile (waterproof), Audio (IPX speakers), Medical (pressure-equalization). Trivial OCCT; exercises the membrane catalog. |
| 4 | **`magnet_pocket_axial`** + **`wireless_charging_coil_seat`** (pair) | modify_pocket atomic + macro | Both P0 in Mobile + Wearables. Together unblock MagSafe rings, AirPods lid latches, Apple Watch charging caseback, S-Pen strip. Trivial OCCT; exercises magnet + Qi catalogs. |
| 5 | **`bearing_seat`** (catalog-keyed: 608ZZ / 6000 / 6900 / MR) + **`lip_seal_cavity`** (ISO 6194) | modify_pocket atomic | Two atomic P0 skills in Power tools; reused by Wearables (hinge bearings) and Medical (rotating handpieces). Cleanly forces the bearings + seals catalogs and the **fit-class metadata** (ISO 286). |
| 6 | **`inspect_wall_thickness`** (ray-march sampler) | inspect atomic | Shared P0 infra for DFM-IM, DFM-CNC, Medical (drop ribs), Audio (chamber walls). Underpins sink-mark, equalize-wall, boss-design-check, cooling-channel skills downstream. Returns the `_pd_thickness_samples` namespace. |
| 7 | **`cosmetic_side_classify`** + **`inspect_undercut_zones`** (paired) | inspect atomic | Two P0 skills in DFM-IM that gate everything downstream (gate location, ejector, slide action). Also satisfies DFM-CNC's `cnc_undercut_check`. Builds the `_pd_cosmetic` namespace shared with Surface aesthetics. |
| 8 | **`class_a_surface_tag`** + **`continuity_audit`** (paired) | modify_finish atomic + inspect | Both P0 in Surface aesthetics; Automotive depends on `class_a_surface_region_tag` for trim. Pure metadata + audit, low OCCT effort, opens the door for `zebra_stripe_view` / `crown_and_flair` next quarter. |
| 9 | **`acoustic_grille_pattern`** (open-area solver) + **`speaker_acoustic_chamber`** / **`helmholtz_port_tube`** (paired) | modify_pocket macro | All three P0 in Audio; first is reused by Mobile (speaker grilles), Automotive (HVAC mic grilles), Medical (vent meshes). Forces the **computed-parameter return slot** (Theme H). |
| 10 | **LLM control-plane P0 quartet**: `find_skill_by_intent` + `dry_run_skill` + `predict_post_conditions` + `tag_inventory` | inspect / meta | Without these, agentic Planner mode is economically infeasible (75K-token manifest blows session cap). All four reuse precondition-evaluator + sandbox-copy infra (Themes G + K). Ship as a single PR. |

### Why these 10 specifically (and not the obvious P0 supersets)

- **Mobile-specific catalog of named-noun cutouts** (USB-C, SIM tray, MagSafe ring, camera ring, side button) is high-impact but **single-domain**. Build it after the connector catalog backbone lands; the catalog itself is 80 % of the work.
- **HVAC blade / barrel vent family** (Automotive P0) is also single-domain and waits on the closed-path sweep infra (delivered by #2). Defer one quarter.
- **Medical `face_seal_land_pair`** + **`autoclave_flat_seal_pair`** wait on the **paired-body output type** infra (Theme F) — too risky to mix into the same pack.
- **Sink-mark / gate-location / equalize-wall** are all extensions of `inspect_wall_thickness` (#6). Once #6 lands they become ~1 week each.
- **Helical / bevel / worm gears** (Power tools P0/P1) are isolated heavy-OCCT items; better as a dedicated "gear pack" sprint.

### Estimated effort

- Pack of 10 skills + 4 catalog tiers + 5 infra themes ≈ **6 engineer-weeks of focused work**, where ~40 % is shared infra (catalog backbone + closed-path sweep + cylindrical frame + precondition registry + sandbox copy + face-attr namespace).
- After this pack, the next quarter's pack becomes nearly all skill-only work (catalogs and infra already in place), giving a 2-3× velocity multiplier for the second wave.

---

## 7. Implementation Sequencing (3 sprints)

### Sprint 1 (week 1-2) — Infrastructure

- Catalog backbone (`phone_designer.catalogs` + Pydantic schemas + YAML loader + CI validator).
- Closed-path sweep helper (`_path_on_face`) supporting rect / racetrack / polyline on any planar face.
- Cylindrical-face local frame resolver.
- Face-attribute namespace consolidation (`body._pd_face_attrs`) + tag propagation rules.
- Sandboxed body copy + `ExecutionMode.PREDICT`.
- Precondition evaluator registry.

### Sprint 2 (week 3-4) — Catalog Tier 1 + connector cutouts + sealing pack

- Ship Tier 1 catalogs (connectors, materials, O-rings, magnets, finishes, membranes, adhesives).
- Ship skills #1, #2, #3, #4 (connector cutout, O-ring path, vent membrane, magnet/coil).

### Sprint 3 (week 5-6) — DFM inspect + class-A + LLM meta

- Ship skills #5 (bearing seat / lip seal), #6 (`inspect_wall_thickness`), #7 (cosmetic + undercut classify), #8 (class-A tag + continuity audit), #9 (acoustic skills), #10 (LLM meta quartet).

After Sprint 3 the library can reproduce a credible iPhone 15 Pro chassis, an AirPods Pro 2 case, a Galaxy Watch caseback, a Sonoma center HVAC vent, and a handheld glucometer — each in 8-15 skill calls instead of 30-60.

---

## Appendix — Per-Report File List

- [audio-acoustic-features.md](./audio-acoustic-features.md)
- [automotive-trim-and-interior.md](./automotive-trim-and-interior.md)
- [dfm-cnc-and-sheet-metal.md](./dfm-cnc-and-sheet-metal.md)
- [dfm-injection-molding-deep.md](./dfm-injection-molding-deep.md)
- [llm-workflow-meta-tools.md](./llm-workflow-meta-tools.md)
- [mechanical-power-tools-and-actuators.md](./mechanical-power-tools-and-actuators.md)
- [medical-device-housings.md](./medical-device-housings.md)
- [mobile-device-housings.md](./mobile-device-housings.md)
- [surface-aesthetics-class-a.md](./surface-aesthetics-class-a.md)
- [wearables-and-earbud-cases.md](./wearables-and-earbud-cases.md)
