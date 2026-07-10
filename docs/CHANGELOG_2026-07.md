# SmartTwinModeller — Engineering Changelog, 2026-07 push

Arc: `290ca0e` (sketch_extrude/revolve) → `b34c3e4` (HEAD). Manifest at HEAD: 419
skills (389 at the Tier-1 baseline, after the sketch quartet; per-commit counts
cited below). Every number in this document is a
pinned, executed value from the cited commit message or a committed test — per the
house rule, nothing here is self-scored or estimated unless labeled so.

---

## 1. General-CAD gap roadmap — 38 gaps shipped (4 tiers + the one "hard" item)

The 47-agent gap analysis found 38 verified gaps vs the then-389-skill library
(`plans/` roadmap). `a55a75d` closed the last of them: "the general-CAD roadmap is
fully shipped and review-hardened." Some gaps were closed by arg-adds (fillet G2,
`2142520`) or a SketchSpec kind rather than a new skill; the new skills, one line
each with the headline verified value:

### From-scratch create quartet (`290ca0e`, `b10b11f`)

- `sketch_extrude` — arbitrary line/arc/spline profile → solid; D-shape = 785.398.
- `sketch_revolve` — Z-axis-locked (non-Z axis is a structural OCCT trap →
  `fm.unsupported_revolve_axis`); bushing 360° = 402.124, 90° = 100.531.
- `sketch_sweep` — section along line+arc path; bent pipe d6 = Pappus-exact 1486.279;
  refuses >30° non-G1 joints and self-intersecting bends (OCCT builds them silently).
- `sketch_loft` — ≥2 sections, ruled|smooth; ruled frustum 10²→4² h10 = 520.0
  (prismatoid-exact). Wire-cast hang trap fixed: `.Shape()` results MUST be cast back
  with `TopoDS.Wire_s` or ThruSections/MakePipeShell hangs forever (not raise).

### Tier 1 — base body verbs (`a878ac6`, manifest 389→394)

- `intersect` — the third boolean (Common); 20³ ∩ +10X = 4000; `fm.no_intersection`.
- `move_body` — rigid translate+rotate; volume-invariant 8000; leave_copy → 16000.
- `mirror_body` — real-geometry reflect; 1000, merge=True → 2000 symmetric body.
- `scale_body` — uniform 2× = 64000; anisotropic [2,3,1] = 48000 (vol×sx·sy·sz).
- `split_body` — plane split into real bodies; z=0 → 4000+4000; keep both|pos|neg.

### Tier 2 — inspect/interop/defeature (`d65a5f0` 394→397, `9768af2` 397→400)

- `oriented_bounding_box` — 30×20×10 box at 45°: AABB balloons to 35.36, OBB [10,20,30].
- `delete_face_defeature` — true BRepAlgoAPI_Defeaturing; fillet removal heals
  7572.6 → exactly 8000.0; `fm.defeature_volume_collapse` guard.
- `fill_surface_patch` — N-sided c0/g1/g2 fill; 400 mm² patch over a 4-edge loop.
- `dxf_export` — section|silhouette|flat_pattern loops → LWPOLYLINE (the loops
  existed internally but could never leave the system before).
- `section_to_sketch` — solid section → executable SketchSpec; 600 area → ×10 = 6000.
- `offset_sketch` — 2D parallel offset; 20² inward-2 → extrude = 1280 exactly.

### Tier 3 — curves/surfaces/patterns (`179ee55`, `93ac2dd` 400→404)

- poles-spline (`PolesSplineSketch`, a SketchSpec *kind*, not a skill) — Class-A
  closed B-spline/NURBS BY CONTROL POLYGON; convex-hull proof: bbox 18.33 < 20 poles
  square (pole-driven, not interpolated); works in all 4 sketch_* skills with zero
  per-skill changes. OCCT gotcha: periodic B-spline needs n+1 uniform knots.
- `create_open_surface` — open shell, NOT a solid; lofted skin area 670.820; is_solid
  honestly False via TopAbs_SOLID count (VolumeProperties returns a nonzero
  divergence-theorem pseudo-mass for open shells — volume>0 LIES).
- `trim_surface` — trim face or solid by plane; 20² face → 200 exact; solid → 4000.
- `helix_sweep` — threads/springs/augers; helix R10/p6/t3 length 189.353 exact,
  Pappus 2379.5 vs actual 2378.6 (0.04%); `fm.helix_section_exceeds_coil`.
- `pattern_seed_body` — pattern THE working solid (unlike the 7 profile-locked
  pattern skills); linear ×3 s20 → 3000; overlapping s5 → honest fused 2000.

### Tier 4 — interop + direct edit (`1d54da1` 404→408, `8595ab8` 408→411)

- `mesh_export` — OBJ/PLY from the BRepMesh triangulation; box → 12 triangles.
- `gltf_export` — .glb/.gltf via RWGltf_CafWriter; GLB magic verified.
- `curvature_comb` — edge curvature sampling; r=10 circle reads 0.1 at all 40 stations.
- `cosmetic_thread` — thread annotation w/o modeled helix; M6 → minor 4.917 (ISO 68-1).
- `move_face` — press-pull a planar face; 20³ top +5 → 10000, −5 → 6000; planar-only.
- `replace_face` — retarget a planar face; z=20 → z=17 = 6800 exact; trim-only
  (positive-offset-outside-body is honestly a no-op, not an additive grow).
- `path_array_orientation` — tangent-oriented array along a 3D path; 5×4³ → 320,
  Z preserved (path_pattern is XY-projected).

### The deferred "hard" gap (`82b740a`, 411→412)

- `deform_body` — free-form twist/taper (SolidWorks Flex class) via NurbsConvert →
  pole refine → displace → re-sew. HONEST: warped volume is APPROXIMATE and
  converges with `refine` — 80° twist: refine 0 → 28% low, refine 16 → 0.24%;
  taper 0.5 → 9333.33 = EXACT analytic frustum integral.

### Scan-to-CAD front door (`a55a75d`, 412→414 — closes gap #38)

- `mesh_import` — OBJ/PLY (ascii+binary) → sewn BRep; Box→OBJ→import = 4000.0 exact;
  open mesh → honest is_solid False + free_edge_count; volume_mm3 None (JSON null).
- `point_cloud_import` — .xyz/.ply cloud → vertex compound + SVD best-fit plane;
  10k-point seeded plane: rms 0.020. HONEST: watertight reconstruction out of scope
  (no open3d — standing project ruling).

---

## 2. Adversarial hardening — 34 probe-verified bugs fixed (`ddef879`)

Process: 6 domain reviewers → 34 candidate bugs → EVERY one independently
re-verified by a refuting agent WITH an executed probe. All 34 confirmed real; all
fixed; each pinned by a new or corrected test. Post-fix: 2036 passed / 17 skipped
(OEM corpus absent) / 0 failed.

Three critical, one line each on the trap:

1. **Inside-out deform solids** — `deform_body` re-sewed to a NEGATIVE signed-volume
   solid; the classifier put interior points OUTSIDE, and BRepAlgoAPI_Common returned
   the 584065 mm³ COMPLEMENT of the 15935 mm³ twisted box. Fix: `_signed_volume` +
   `TopoDS.Solid_s(solid.Complemented())` when negative.
2. **Input-mutating NurbsConvert handle sharing** — when input faces are already
   B-splines, NurbsConvert returns SHARED Geom handles, so `SetPole` corrupted the
   INPUT body (deform-of-deform corrupted the first result). Fix: `surf.Copy()`
   before touching poles. (Same commit: MakeFace(surface) silently FILLED
   through-holes — multi-wire refusal + face-count conservation gate.)
3. **cw complement arcs** — `_edge_arc_radius` built ccw=False arcs as the 270°
   COMPLEMENT with backwards parameterization: wrong geometry AND wrong tangents, so
   the sweep G1 guard falsely refused good cw paths and silently ACCEPTED 170° cusps.
   Fix: reversed-axis (−Z) circle with Sense=True → the forward-parameterized minor
   arc. Pin: cw bent pipe = exact Pappus 1486.279; 170° cusp refused.

13 major (selected): mirror-merge of disjoint halves refused (was an `n_bodies=1`
lie); path-array frame roll fixed by parallel transport (probe: 168.75° → 11.25°
adjacent roll); sweep orientation='fixed' was silently Frenet; off-center sections
bypassed the self-intersection guards; helix adjacent-coil interference guard;
defeature refuses OCCT's silent no-op via `History().IsRemoved`; `move_face` refuses
tapered adjacent walls; `gltf_export` wrote 1000×-too-large units (SetLengthUnit
mm→m). 18 minor: unreachable `fm.*` modes made reachable, raw exception leaks
wrapped as structured refusals, NaN/∞ pole weights refused, `scale_body` anisotropic
volume-eps 0.86% lie on curved bodies, etc.

Earlier in the same push, `2142520` fixed 3 known issues flagged by the roadmap
session itself (see §5).

---

## 3. MCP Phase-1 — stateful, self-correcting, hang-proof LLM loop (`eddfb4b`)

The roadmap's "엔진 → 루프" turn: the MCP LLM client (which IS the NL→spec
interpreter — no custom parser) was blind, stateless and unprotected. All five
Phase-1 tracks shipped as `mcp_support/` modules wired into `mcp_server.py`:

- **Sessionization / BodyStore** — LRU of live bodies (max 32,
  `PHONE_DESIGNER_MCP_MAX_LIVE`) with STEP-snapshot durability: an evicted body_id
  transparently re-imports from its own STEP (geometry-only; `_pd_tags`/component
  names lost — documented, not hidden) + parent lineage. New tools: `cad_import`
  (oversize → structured redirect to assembly_reverse_engineer), `cad_modify` (spec
  on an existing body, mints a child body_id), `cad_undo` (walks lineage, returns
  the restored volume — exact to 1e-6), `cad_measure` (mass/obb/dimensions),
  `cad_preview` (PNG views; HONEST `skipped_no_gl` marker under no-GL, never a
  blank image).
- **Guarded worker** — cad_generate/cad_modify run in a warm worker subprocess with
  a hard timeout (`PHONE_DESIGNER_SKILL_TIMEOUT_S`, default 120; 0 = inline): one
  stuck OCCT builder can no longer block the stdio server. Timeout → `fm.timeout` +
  worker respawn (proven: a 30 s hang returns in <8 s and the next call works).
  Only the STEP crosses the pipe; worker fd-1 is dup2'ed to devnull so OCCT C-level
  chatter cannot corrupt the MCP protocol.
- **Preflight + failure enrichment (self-correction v1)** — failed steps carry
  `likely_cause` / `suggested_fix` / `selector_match_count` / selector suggestions;
  the raw error string is NEVER masked (pinned byte-identical). `cad_preflight`
  validates a spec without executing (`ok` = the tool ran; `spec_ok` = the verdict).
- **Pillar exposure** — `cad_compare` / `cad_variants` / `cad_cheapest_variant`
  wrap the finished-but-invisible compare/variants pillars; the strict viability
  gate survives the tool path (a marginal variant is never crowned — house ruling).
- **One-call RFQ** — `quote_package` skill (manifest 414→415) + `cad_quote_package`:
  zip{STEP, per-lot costs on the precomputed-driver fast path, process
  recommendation WITH exclusion reasons, DXF section}; `grade='estimate'` labeled
  INSIDE the manifest artifact, not just the API response. Lot-sweep monotonicity
  pinned.

99 tests across 7 new files; the 20 pre-existing MCP tests run green through the
worker lane. (`5dc634e` then fixed the CI collection interrupt — see §5.)

---

## 4. Phase-2 surfaces (`a68c882`, follow-ups `b34c3e4`) — manifest 415→419

All five roadmap Phase-2 tracks:

- **Drawings + HLR (2-1)** — `hlr_view` (HLRBRep_Algo visible/hidden projection;
  face-budget fallback to brute silhouette honestly labeled `non_cut_ready`) +
  `drawing_sheet` (third-angle front/top/right + iso + section, title block,
  auto_dimension TABLE + hole table; **DRAFT FOR REVIEW baked into the sheet** —
  anchored callouts, no auto leader layout by design) + `dxf_export source='hlr'`
  with layered VISIBLE/HIDDEN DXF. Pin: box-with-hole front view = visible 4 /
  hidden 9 / outline 1 (the pre-retirement probe value). MCP: `cad_drawing`.
  `b34c3e4` PROMOTED the sheet into the RFQ zip (`grade='draft'`), with failure
  isolation pinned: a drawing failure never kills the quote (honest
  `{'status':'failed'}` section; costs/STEP/DXF untouched).
- **Parametric plans v2 (2-2)** — `parameters` table + `{"$expr": ...}` step args
  (simpleeval whitelist; cycle/undefined → `fm.expr_error`) + `plan_reexecute`
  (override params → re-run → volume/bbox deltas + honest selector-drift warnings,
  the SolidWorks-rebuild-error analogue) + `generate_from_spec parameters=` kwarg.
  BYTE-IDENTITY of param-less plans proven against ORIGINAL HEAD output (house
  rule: vs the original, not an ON/OFF toggle that shares the bug). Pin: wall
  +0.2 mm → analytic +379.392 mm³. MCP: `cad_reexecute`.
- **Assembly analysis (2-3)** — `analyze_assembly`: solid split →
  rotation-invariant signature dedup (2 identical bolts = 1 class, count=2) →
  per-class light analysis → budgeted interference/clearance matrix (labeled
  **static_pose_only** — mates are tags, not constraints) → standard-part
  recognition with the MANDATORY gate: catalog-part lines, never machined-cost.
  Pin: overlap volume 31.8086 == analytic π·1.5²·4.5. MCP: `cad_analyze_assembly`.
- **Catalog determinism (2-4, engine half)** — `extract_feature_catalog` now does
  canonical single-thread tessellation BEFORE the parallel detector fan-out.
  Mechanism (confirmed, not guessed): `detect_mirror_symmetry`'s meshing shifted
  later bbox reads → thread-schedule-dependent catalogs; pre-fix repro = 3 distinct
  catalogs in 6 runs. Pin: catalog JSON byte-identical 5×/fixture on 3 fixtures;
  the classify_holes memo and recommend_process 5.8× precompute fast paths
  preserved (19/19). Plus `corpus-profile` CLI (honest limits stated in output:
  single-run wall-clock, hotspot triage only, not a perf gate).
- **Recipes + replay eval (2-5)** — 58 executed-and-pinned recipes (`recipes/`,
  55 positive + 3 negative that teach `fm.*` structured refusals); every expected
  volume was MEASURED by execution, never guessed, and `test_recipes_execute.py`
  re-runs all of them in CI (anti-rot pin). `find_recipe` is Hangul-aware
  ('기어'/'구부러진 관' resolve — the reused tokenizer drops Hangul, substring
  fallback added). MCP: `cad_find_recipe`. `evals/nl2spec/` replay harness: 13
  task cards, deterministic, baseline JSON; `phone-designer nl2spec-eval` CLI
  (`b34c3e4`), verified live 13/13 with no baseline regressions. LIVE-LLM lane
  deliberately NOT a CI gate (Phase-3, nightly only).

---

## 5. Fixes & honesty items

- **is_solid open-shell lie** (`2142520`) — `generate_from_spec` reported
  is_solid=True for open shells because VolumeProperties returns a divergence-
  theorem pseudo-mass. Now ALSO requires a TopAbs_SOLID sub-shape ("topology never
  lies"); open shells honestly False; surface-first workflows pass
  `validate_solid=False` (documented in the MCP-visible arg description).
- **JSON ∞** (`2142520`) — `curvature_comb` emitted `radius: inf` at straight
  stations; `json.dumps(inf)` produces non-standard `Infinity`, which strict MCP
  clients reject. Now None; pinned with `allow_nan=False` round-trip of the payload.
- **Fillet Class-A knobs** (`2142520`) — `continuity='g1'|'g2'` +
  `fillet_shape` on fillet_edges_by_predicate; defaults preserve historical
  behaviour exactly (45 existing fillet tests untouched).
- **helical_spring non-solid** (`b34c3e4`) — returned an open tube: MakePipe with a
  bare WIRE profile sweeps wire→SHELL, whose "volume" was pseudo-mass. Found by the
  recipes track, which honestly EXCLUDED the skill rather than pin a lying idiom
  (`a68c882`), then fixed: cap the profile into a FACE + shell→MakeSolid +
  Complemented lift. Verified: 1 TopAbs_SOLID, 990.282 vs Pappus 988.958 (0.13%);
  new recipe `helical_spring_coil.yaml`.
- **ezdxf / mcp deps** (`5dc634e`, `b34c3e4`) — ezdxf was only transitive via
  build123d (dxf_export would silently break on a dep-tree change) → declared
  `ezdxf>=1.3`. `mcp>=1.0` was undeclared, so all 31 MCP-surface tests silently
  SKIPPED in the Windows CI lane via importorskip → now declared and actually
  running. Also: one bare module-level import in a test interrupted the ENTIRE
  suite at collection → importorskip guard.
- **Box TIMEOUT elimination — gate promotion PENDING** — the committed box_root
  baseline (`67bac19`) carried 5 fine-pitch IC files as legitimate TIMEOUTs. A
  regenerated baseline (2026-07-02, currently in the working tree, uncommitted)
  records 55/55 executor PASS, 0 TIMEOUT records (slowest 383.9 s under the
  `--timeout-s 600` CI budget). The suite.yml box job REMAINS informational-only:
  promotion to blocking requires the roadmap 2-4 evidence — box-mode serial
  determinism (3 consecutive green sweeps vs its own baseline), which the box
  pipeline has NOT yet demonstrated (it historically fails against its own
  freshly-written baseline). preserve_brep root remains the deterministic BLOCKING
  gate (0.0 max abs diff across the 55 root files).

---

## Known open items

1. **Box gate promotion** — regenerated 0-TIMEOUT box_root baseline is uncommitted;
   informational→blocking promotion is gated on 3 consecutive green serial sweeps
   (box-mode reconstruction determinism is unproven; catalog-level determinism from
   2-4 is necessary but not sufficient). If honest numbers drop on regeneration,
   they are accepted per project law.
2. **Phase 3 big bets** (`plans/NEXT_ROADMAP.md`, each behind a go/no-go gate):
   - 3-1 Scan-to-CAD (segment → analytic fit → scan_to_brep); v1 machined/prismatic
     only, organic refused-with-reason. Go = ≥70% of the prismatic subset passes
     feature-catalog agreement on a tessellate-and-reconstruct gate; self-scored
     match ratios banned (top fake-accuracy risk).
   - 3-2 FEM loop (gmsh tet + CalculiX, optional-dep); results grade='estimate'
     with solver/mesh caveats. Go = clean skip-if-absent binary story + cantilever
     benchmark in tolerance band.
   - 3-3 Plan-as-feature-tree (suppress/insert ONLY — reorder rejected as unsafe);
     Go = EntityHistoryMap hot-path coverage ≥80%; incremental rebuild must be
     byte-equivalent to full re-run.
   - 3-4 Assembly DOF accounting + mate_drive/kinematic_sweep; closed-loop
     (4-bar) linkages get a detected structured refusal, not a solver.
   - 3-5 Constraint-lite 2D sketch: `sketch_relation_check` ships unconditionally;
     the solver freezes to verification-only if warm-start convergence misses
     target. Never marketed as a "parametric sketcher".
3. **Ongoing hardening tracks** — hypothesis fuzz nightly (falsifying examples
   committed as pinned seeds), perf-budget gate (prerequisite 2-4 determinism, now
   partially met at catalog level), corpus expansion (industrial 70 + revolved 37,
   first-sweep TIMEOUTs recorded honestly), NL→spec LIVE eval lane on the shipped
   replay skeleton (private hold-out split; nightly/manual only, never CI-blocking).

## 7. Web viewer V1→V3 + multi-body — SEE-and-POINT modelling loop (`6eebf4f`, `94f4b91`, `f1dae4c`)

The GL-free preview grew into a real browser CAD inspector; Claude stays the
modelling brain, the browser rotates/picks/shows.

- **The silently-broken pick loop, found and fixed** (`6eebf4f`): GLB is
  metres/Y-up, pick_face compared OCCT mm/Z-up centroids — a 1000×/90°
  mismatch my own probe had missed by feeding OCCT-mm points (false
  confidence). Fix: pick by GLB **primitive index** (prim[i] == _all_faces[i],
  1:1 in-order — re-proven raw); killed five mispick bugs at once. Plus F2
  namespace bridge (full generate→pick→get_selection→modify round-trip
  ok=True), F3 workspace pointer coupling, F4 traversal/CORS, F5 threading,
  F6 cache-by-size+mtime, F7 honest 400s; edge pick (`edges_near_point`
  fillets ONE clicked edge); per-face highlight, auto-refresh, camera presets.
- **V3** (`94f4b91`): `/scene` feature overlay (extract_feature_catalog
  face_indices map 1:1 to GLB primitives — hole idx → cylinder bore, verified
  0.06 mm), `/section` transient cut view (never mints a body), measure;
  MCP `cad_scene` / `cad_section` / `cad_measure(distance)` (24→26 tools).
- **Multi-body** (`f1dae4c`): face_idx→component map via iter_solid_components
  + IsSame (plate+bolt: comp0 faces[0-5], comp1 faces[6-8], partition
  complete); `/components` + `cad_components` (27 tools); per-component HSL
  color / select / isolate in the browser.

## 8. The gearbox live-fire exercise → 10 findings, all fixed (`ab9a547`, `f7e82b9`, `3b3449a`)

Built a real single-stage reducer housing via the MCP spec loop (11 steps,
engineered from the gear pair outward), produced the full deliverable set
(cover+assembly, cost/DFM, HLR drawing + RFQ zip), then fixed everything the
exercise exposed:

- **Design**: v2 split housing (parting through the shaft axis) kills the 12
  shoulder-bore undercuts (upper 0 / lower 1 = the drain cross-hole only);
  M5 tap-drill↔clearance pairing fixed; oil seals, dowels, breather, drain,
  external feet. v2.1 adds bearing-boss flange bulges — max unclamped flange
  span 100→40 mm, all 14 parting stacks ok_threaded. Cost sweep's honest
  refutation: for CNC-from-billet, thinner walls RAISE cost; height is the
  lever (h78 → $77.78, −8.3%).
- **System** (batch 1 `ab9a547`, batch 2 `f7e82b9`): rib ±X/±Y wall ribs;
  bearing catalog 62xx/63xx (+14 ISO 15 entries); classify_holes 300°
  angular-extent gate (pocket corner fillets are not holes — the 4 phantom
  Ø24 drawing rows died) + thread-guess confidence floor; **joint_check** (new
  skill, manifest 426) — coaxial-stack clearance/tap-drill verdicts, catches
  the both-sides-drilled-Ø5.5 bug mechanically; process-name aliases
  (cnc_3axis == cnc_milling, full-dict pinned); renderer depth cue (open
  cavity finally reads darker than the flange, deterministic); drawing
  section 45° even-odd hatch; bbox determinism (mesh-history-dependent
  AddOptimal → exact BRep bounds; cost reproduces to the cent).
- **Honest corpus rebaseline**: the arc gate dropped 4 preserve_brep root
  scores; adjudicated by measuring the parts — Crystal_SMD has only 90-92°
  castellation arcs, zero real holes, so the old 1.0 was inflated. Baselines
  regenerated, verify sweep 55 files / 0 regressions.
- **OCCT ACCESS_VIOLATION root-caused** (`3b3449a`): a hole-opening circle
  osculating a corner line (seam at the tangency vertex → zero-angle cusp)
  crashes MakeFillet's ChFi3d corner processing, and the poison rides
  G1-tangent chains. _fillet_crash_guard pre-validates (SEH catching is
  impossible — OSD::SetSignal unbound in this OCP build); skipped edges
  reported honestly; v1 results byte-stable. Minimal fixture in
  tests/skills/test_repair_dfm_segfault_guard.py.
