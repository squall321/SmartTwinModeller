# MCP Client Guide — driving the phone-designer CAD server from an LLM

Audience: the LLM client (Claude, …) that talks to this server over MCP stdio, and the
integrator wiring it up. **The client IS the natural-language → spec interpreter** — there
is no custom NL parser in the server. You discover ops, compose a build spec, execute it,
read the structured failures, and repair your own spec. This document is the contract.

Server name: `phone-designer-cad` · Launch: `venv/Scripts/python.exe -m phone_designer.mcp_server` (stdio)
> ⚠️ **venv python 필수.** bare `python`(시스템 파이썬)은 mcp 패키지가 있으면 서버가 뜨고 tools/list까지
> 답하지만 — build123d가 없어 **모든 geometry 툴이 ModuleNotFoundError로 죽습니다** (감사에서 실증:
> discovery green / build dead). `.mcp.json`도 `venv/Scripts/python.exe`를 명시합니다.
>
> **알려진 한계 (head-of-line blocking):** 27개 툴은 sync def라 mcp 1.28 FastMCP가 이벤트루프에서
> 직접 실행합니다 — 긴 CAD 호출 1건이 도는 동안 tools/list조차 큐에 대기합니다 (감사 실측: 427초).
> 단일 클라이언트(Claude Code)에선 지연일 뿐 교착은 아니며, 호출 자체의 행은 워커 watchdog이 격리합니다.
> 구조적 해결(툴별 async offload)은 백로그.
Skill library behind the tools: **430 registered skills** (measured via
`build_manifest()`; the module docstring's "~383" predates the Tier-1..4 + Phase-1/2
batches). Category breakdown: inspect 132, modify/pocket 77, create 38, modify/boss 27,
reverse_engineer 20, modify/curvature 16, assembly 14, modify/pattern 11, modify/sheet 10,
repair 9, fem_cae 9, modify/finish 9, modify/mold 8, transform 7, compose 6, pmi 6,
modify/chamfer 5, modify/3dprint 4, modify/hole 3, modify/fillet 3, modify/antenna 2,
io 2, modify/plateau 1.

Environment knobs:

| env var                          | default        | meaning                                    |
|----------------------------------|----------------|--------------------------------------------|
| `PHONE_DESIGNER_MCP_WORKSPACE`   | fresh temp dir | artifact dir (STEP/STL/HTML/PNG)           |
| `PHONE_DESIGNER_SKILL_TIMEOUT_S` | `120` (s)      | hard per-plan worker timeout; `0` = inline |
| `PHONE_DESIGNER_MCP_MAX_LIVE`    | `32`           | LRU cap on live session bodies             |

Every tool returns structured JSON and **never crashes the server** — an internal fault
comes back as `{ok: false, error: "<ExcType>: <msg>", trace: "..."}`.

---

## 1. Tool reference (27 `@mcp.tool`s)

> 이후 추가된 4개: `cad_scene`(피처 카탈로그+face_indices), `cad_section`(절단 절반 → 새 body),
> `cad_components`(멀티바디 분해), `cad_get_selection`(뷰어 픽 브리지). `cad_measure`는
> `what='distance'` 모드 추가. 아래 23개 서술은 그대로 유효.

### Discovery — call these before composing a spec

**`cad_list_skills(query="", category="", limit=80)`** — list/search the registered
skills (name + one-line summary). Call FIRST when you need an op name for a
`cad_generate` spec and no recipe covers the intent. `query` is a substring match on
name+summary; `category` is a substring match on the category string, so
`category="modify"` matches every `modify/*` family and `category="create"` /
`"inspect"` / `"reverse_engineer"` narrow to those. Response is `{ok, n, skills,
truncated}` — check `truncated` and refine rather than raising `limit` blindly.

**`cad_get_skill_schema(name)`** — the JSON-Schema args model of ONE skill: the exact
`args` shape to put in a spec step. Use after `cad_list_skills`, before `cad_generate`.
Unknown names return `{ok: false, error: "unknown skill '…'"}` — that is your cue to
search again, not to guess a near-miss spelling.

**`cad_find_recipe(query, top_k=5)`** — find a proven spec RECIPE by intent, English or
Korean ("bent pipe", "기어", "counterbore holes"). Returns ready-to-adapt `cad_generate`
specs WITH their executed-and-pinned expected invariants (`is_solid`, volume range).
Every recipe in `recipes/` is re-executed in CI (`tests/test_recipes_execute.py`), so a
returned spec is known-good — **start from a recipe instead of composing cold** whenever
one is close. Negative recipes (`neg_*`) teach the structured refusals (see §3.4).

### Build — spec in, body_id out

**`cad_generate(spec, name="part", formats=None)`** — generate a solid FROM SCRATCH from
an ordered list of build steps `[{"op": <skill>, "args": {...}}, ...]`; first step is
usually a create skill (box/cylinder/gear/sketch_extrude…), then features (hole/pocket/
fillet…). Writes STEP (+ optional `"stl"`, `"py"` per `formats`) to the workspace, caches
the body, returns `body_id` + file paths + `resource_uris`. Runs HANG-PROOF in a warm
worker subprocess with a hard timeout (§3.6). Per-step failures are ISOLATED: check
`status` (`ok | partial | error`) and `steps[i]` — failed steps carry the
machine-actionable fields of §3.3, so you repair just the failing step.

**`cad_import(path, max_faces=4000)`** — import an existing STEP into the session as a
`body_id` so every other tool can work on it without re-parsing the file. REFUSES
oversize parts (> `max_faces` faces) with a structured redirect to
`cad_analyze(part_path=…)` / `assembly_reverse_engineer` — do not retry the import with a
bigger number as your first move; the refusal exists because a 40k-face housing will blow
the session budget.

**`cad_modify(body_id, spec, name="")`** — apply MORE build steps to an existing session
body (same spec shape as `cad_generate`) WITHOUT resending the original spec. On success
it mints a NEW `body_id` whose parent is the input — the input body is never mutated.
Honest limit: geometry round-trips through STEP, so **in-session face tags
(`tag_face` / `_pd_tags`) do not survive a modify** (§3.5). Runs in the same guarded
worker; failed steps are enriched exactly like `cad_generate`.

**`cad_undo(body_id)`** — return the PARENT `body_id` of a `cad_modify` /
`cad_repair_dfm` result. Bodies are immutable, so undo is just moving one hop up the
lineage — nothing is deleted, and you can "redo" by simply reusing the child id you
already hold. The response includes the restored body's `volume_mm3` so you can verify,
plus the full `lineage` chain. A root body (generated/imported directly) refuses with
`fm.at_root`.

**`cad_preflight(spec, body_id="")`** — validate a spec WITHOUT executing geometry: per
step, is the op known, do the args pydantic-validate, and (when `body_id` is given and
the step carries a selector) how many faces/edges the selector matches on that body.
Fast — registry lookup + schema validation only. Catch mistakes BEFORE paying for
geometry. Read `spec_ok`, not `ok` (§3.2). A 0-match selector is a WARNING, not an
invalidity: earlier spec steps may legitimately create the geometry the selector will
see at execution time.

### Measure / see

**`cad_measure(body_id|part_path, what="mass")`** — read-only measurement:
`what="mass"` (volume, centroid, inertia), `"obb"` (minimal oriented bounding box — the
true stock size, not the axis-aligned one), `"dimensions"` (auto-extracted key
dimensions). These are direct geometry queries — measured numbers, not estimates.

**`cad_preview(body_id|part_path, views=None)`** — render PNG previews from standard
views (default `iso, front, top`) into the workspace so you can SEE what you modelled.
In a headless/no-GL environment it returns an honest skip marker (`skipped_no_gl`
contract) instead of blank images — treat that as "cannot see here", not as failure.

### Analyze / cost / process (single part)

**`cad_analyze(body_id|part_path, processes=None, estimate_cost=False,
recognize_fits=False, sheet_metal=False, measure_fits=False)`** — the single-part
analysis: quality report (topology / wall / draft / blends / DFM) + opt-in cost, ISO-286
fits, sheet-metal bend table, assembly fits. Writes the HTML report next to the STEP and
returns the structured analysis. All manufacturing numbers are `grade='estimate'`.

**`cad_estimate_cost(body_id|part_path, process="cnc_3axis", material="aluminum",
lot_size=1000)`** — unit cost + cycle time for ONE process (`cnc_3axis | cnc_5axis |
injection_mold_pa | sheet_laser_brake | sheet_progressive_die | …`).
`grade='estimate'` — a transparent heuristic, not a quote.

**`cad_recommend_process(body_id|part_path, material="aluminum", lot_size=1000)`** —
the cheapest VIABLE process at a lot + material, with cost-vs-volume crossovers and
explicit `excluded` reasons (synthesises cost + DFM + sheet detection). NOTE: slower
than the other tools (multiple cost models). `grade='estimate'`.

### Repair / quote / drawing

**`cad_repair_dfm(body_id|part_path, processes=None, pull_direction=None, apply=True,
max_hausdorff_mm=None)`** — AUTO-FIX manufacturability: fillet failing internal corners
to the tool radius, add draft to sub-min-draft walls. Each fix is kept ONLY if the DFM
verdict strictly improves within a Hausdorff-guarded geometry change — else reverted, so
**worst case == input**. Thin wall / undercut / sink are SUGGEST-only (never auto-fixed).
When the body changes and `apply=True`, a NEW `body_id` is minted (parent = input) with
its STEP. `grade='estimate'` — a heuristic repair, not a guarantee.

**`cad_dfm_workflow(body_id|part_path, processes=None, pull_direction=None,
material="aluminum", lot_size=1000, repair=True)`** — the whole
make-it-manufacturable-and-quote-it pipeline in ONE call: repair_dfm, then
recommend_process **on the resulting body** (so the price reflects the repaired
geometry; `priced_body` tells you which one was priced). `repair=False` = analyse +
quote the input as-is. `grade='estimate'` throughout.

**`cad_quote_package(body_id|part_path, material="aluminum", lot_sizes=None)`** —
one-call RFQ bundle: a zip with the STEP, per-lot cost estimates (default lots 1/100/
1000, `grade='estimate'` labeled INSIDE the manifest, not just in the API reply), the
process recommendation with exclusion reasons, a quality summary, and a DXF
cross-section. This is what you hand a human/supplier.

**`cad_drawing(body_id|part_path, part_name="", include_section=True)`** — a
third-angle ENGINEERING DRAWING SHEET: front/top/right + iso HLR views with hidden
lines, optional section, title block, dimension table, hole table — self-contained HTML
plus a layered VISIBLE/HIDDEN DXF per view. Labeled **DRAFT FOR REVIEW**
(`grade='draft'`): v1 uses anchored callouts + tables, no automatic leader placement.
Never present its output as a released drawing.

### Variants / compare / parametric re-execute

**`cad_compare(part_a|body_a, part_b|body_b)`** — rev-A vs rev-B: classification
(identical / scaled / feature-changed / different), similarity score, scale factor,
Hausdorff distance, per-feature diff. Each side is a STEP path OR a session body_id.
`grade='estimate'`.

**`cad_variants(body_id|part_path, n_variants=4)`** — identify the key driving
dimensions of a part, then produce n scaled/varied family members with their parameter
tables. `grade='estimate'`.

**`cad_cheapest_variant(body_id|part_path, process="cnc_3axis", material="aluminum",
lot_size=1000)`** — search the variant space for the CHEAPEST *genuinely-viable*
variant. STRICT viability gate — a marginal candidate is never crowned (standing project
ruling); "no winner" is a legitimate answer. Slow (multiple cost evaluations).
`grade='estimate'`.

**`cad_reexecute(plan_path|plan, parameter_overrides=None)`** — re-execute a saved
parametric plan (schema v2) with overrides like `{"wall": 2.2}`; reports volume/bbox
deltas vs the baseline **plus any selector drift** — the honest equivalent of a
SolidWorks rebuild error, surfaced instead of silently resolving to a different face.

### Assembly / export

**`cad_analyze_assembly(assembly_path, per_component_timeout_s=None)`** — a multi-body
STEP assembly in one call: split into components, dedup identical parts by signature (50
bolts = 1 analysis), per-class light analysis, pairwise interference/clearance matrix,
standard-part recognition. Two hard honesty rules baked in: clearance/interference is
**static pose only** (mates are tags, not a solver), and recognized standard parts get
CATALOG lines — never machined-cost estimates. Returns the versioned AssemblyReportV1.

**`cad_export(body_id|part_path, formats=None, name="")`** — re-export an existing body
to STEP / STL / editable `.py` WITHOUT regenerating. Use when you need another format of
a body you already have; do not re-run `cad_generate` for that.

---

## 2. Recommended loops

### Loop 1 — from-scratch modelling (recipe → preflight → generate → self-correct)

Step 1: look for a proven starting point instead of composing cold.

```json
cad_find_recipe {"query": "bent round pipe with a 90 degree elbow"}
→ {"ok": true, "n": 3, "recipes": [{
     "name": "sketch_sweep_bent_pipe",
     "intent_en": "Sweep a round section along a bent path — straight run, tangent
                   90-degree elbow arc, straight riser: a bent pipe / bent round tube",
     "intent_kr": "둥근 관을 구부려 만든 파이프 — … 구부러진 둥근 튜브를 만들어줘",
     "spec": [{"op": "sketch_sweep", "args": {
        "section": {"kind": "circle", "diameter_mm": 6},
        "path": {"kind": "path", "plane": "XY", "segments": [
          {"kind": "line", "start": [0,0],  "end": [20,0]},
          {"kind": "arc",  "start": [20,0], "end": [28,8], "radius": 8, "ccw": true},
          {"kind": "line", "start": [28,8], "end": [28,28]}]}}}],
     "expected": {"is_solid": true, "volume_mm3": [1456.553, 1516.005],
       "notes": "THE G1 LAW: the path must be tangent-continuous — a line->arc joint is
                 legal only when the arc entry tangent matches the line direction."}
  }, "..."]}
```

Adapt the numbers (diameter, leg lengths) — keep the idiom (tangent-continuous path).
The `expected.notes` are there to stop you re-discovering the trap.

Step 2: preflight the adapted spec (free, fast, no geometry).

```json
cad_preflight {"spec": [
  {"op": "box", "args": {"length_mm": 30, "width_mm": 30, "height_mm": 10}},
  {"op": "counterbore_hole", "args": {
     "face_selector": {"kind": "faces_by_normal", "direction": [0,0,1], "tol_deg": 5},
     "thread_spec": "M3", "fit": "medium", "depth_mm": 6}}]}
→ {"ok": true, "spec_ok": true, "n_steps": 2, "steps": [
    {"op": "box", "known": true, "args_valid": true, "arg_errors": [],
     "selector_match_count": null, "warnings": []},
    {"op": "counterbore_hole", "known": true, "args_valid": true, "arg_errors": [],
     "selector_match_count": null, "warnings": []}]}
```

(`selector_match_count` is `null` here because no `body_id` was given — the box does not
exist yet. Pass a `body_id` when preflighting a `cad_modify` spec against a live body.)

Step 3: generate.

```json
cad_generate {"spec": [ …same 2 steps… ], "name": "m3_seat_plate"}
→ {"ok": true, "status": "ok", "body_id": "body_3fa8c21d", "is_solid": true,
   "volume_mm3": 8847.4, "bbox_mm": [30.0, 30.0, 10.0], "n_steps": 2, "n_ok": 2,
   "steps": [{"op": "box", "status": "pass"}, {"op": "counterbore_hole", "status": "pass"}],
   "files": {"step": ".../m3_seat_plate.step"},
   "resource_uris": ["file:///.../m3_seat_plate.step"]}
```

Step 4 — when a step FAILS, do not retry blind. The failed entry is enriched with
machine-actionable fields (§3.3). Example: a pocket whose selector matched nothing:

```json
{"op": "extrude_pocket", "status": "error",
 "error": "ValueError: face_selector matched 0 faces",
 "args": {"face_selector": {"kind": "face_named", "name": "lid_top"}, "depth_mm": 3},
 "likely_cause": "selector_zero_match",
 "suggested_fix": "The selector resolved to 0 faces/edges on the current body — use
                   suggest_selector_from_phrase or selector_preview to find a selector
                   that actually matches, then re-run the step.",
 "related_skills": ["suggest_selector_from_phrase", "selector_preview",
                    "explain_skill_failure"],
 "selector_match_count": 0,
 "selector_suggestions": [{"selector": {"kind": "faces_by_normal",
                           "direction": [0,0,1], "tol_deg": 5}, "matches": 1}]}
```

The repair move is mechanical: take `selector_suggestions[0].selector` (or follow
`related_skills`), fix ONLY the failing step, and — because `status` was `"partial"` and
a solid WAS written — apply the fix with `cad_modify` on the returned `body_id` instead
of regenerating from scratch. `likely_cause` is a closed five-bucket classification
(`unknown_op | args_invalid | selector_zero_match | fm_refusal:<fm.token> |
occt_failure`); `occt_failure` is the honest catch-all. The raw `error` string is NEVER
masked by enrichment — trust it over the hint when they disagree.

Step 5: iterate with `cad_modify` / `cad_undo`, then look at it.

```json
cad_modify {"body_id": "body_3fa8c21d", "spec": [
  {"op": "fillet_edges_by_predicate", "args": {
     "selector": {"kind": "axis_aligned_edges", "axis": "Z"}, "radius_mm": 2}}]}
→ {"ok": true, "body_id": "body_91b02e77", "parent_body_id": "body_3fa8c21d",
   "is_solid": true, "volume_mm3": 8809.1, "...": "..."}

cad_undo {"body_id": "body_91b02e77"}
→ {"ok": true, "body_id": "body_3fa8c21d", "volume_mm3": 8847.4,
   "op_note": "generate:m3_seat_plate",
   "lineage": ["body_3fa8c21d"]}

cad_preview {"body_id": "body_91b02e77"}
→ {"ok": true, "images": {"iso": ".../previews/iso.png", "...": "..."},
   "resource_uris": ["file:///.../previews/iso.png", "..."]}
```

Verify volumes after modify/undo — the numbers are cheap and they catch wrong-body bugs
on YOUR side (stale `body_id` reuse) immediately.

### Loop 2 — analysis / RFQ on an existing part

```json
cad_import  {"path": "D:/parts/bracket_rev_c.step"}
→ {"ok": true, "body_id": "body_a1e40b9f", "n_faces": 412, "...": "..."}

cad_measure {"body_id": "body_a1e40b9f", "what": "obb"}      // true stock size
cad_analyze {"body_id": "body_a1e40b9f", "estimate_cost": true}
→ {"ok": true, "report_html_path": ".../bracket_rev_c.report.html",
   "feature_counts": {"holes": 14, "pockets": 3, "...": "..."},
   "cost_estimate": {"...": "..."}, "grade": "estimate"}
```

Then either the one-call orchestration or the explicit chain:

```json
cad_dfm_workflow {"body_id": "body_a1e40b9f", "material": "aluminum", "lot_size": 500}
→ {"ok": true, "repaired_body_id": "body_c77d0512", "priced_body": "repaired",
   "repair": {"body_changed": true, "before": {"...": "..."}, "after": {"...": "..."},
              "fixes_applied": ["..."], "suggestions": ["..."],
              "total_hausdorff_mm": 0.31},
   "quote": {"recommendation": {"...": "..."}, "ranking": ["..."],
             "crossovers": ["..."], "confidence_note": "..."},
   "grade": "estimate"}
```

Use `cad_repair_dfm` directly when you want control (custom `max_hausdorff_mm`,
`apply=false` for a dry verdict). Remember: only fillet/draft are auto-fixed; thin
wall / undercut / sink come back as `suggestions` for YOU to act on via `cad_modify`.
Finish with the deliverables:

```json
cad_quote_package {"body_id": "body_c77d0512", "lot_sizes": [1, 100, 1000]}
→ {"ok": true, "zip_path": ".../quote_bracket_rev_c/quote.zip", "manifest": {"...": "..."}}

cad_drawing {"body_id": "body_c77d0512", "part_name": "bracket_rev_c"}
→ {"ok": true, "grade": "draft", "sheet": {"written": {"html": "...", "dxf_front": "..."}}}
```

For a multi-body STEP, skip `cad_import` (it will refuse oversize input anyway) and go
straight to `cad_analyze_assembly(assembly_path=…)`.

### Loop 3 — variants / compare / parametric re-execute

```json
cad_variants {"body_id": "body_a1e40b9f", "n_variants": 4}
→ {"ok": true, "variants": [{"params": {"...": "..."}, "step": "..."}, "..."],
   "grade": "estimate"}

cad_cheapest_variant {"body_id": "body_a1e40b9f", "process": "cnc_3axis",
                      "lot_size": 1000}
→ cheapest GENUINELY-viable member, or an honest "no viable variant" — never a
  marginal winner.

cad_compare {"part_a": "D:/parts/bracket_rev_b.step", "body_b": "body_a1e40b9f"}
→ {"ok": true, "classification": "feature_changed", "similarity": 0.94,
   "hausdorff_mm": 1.8, "feature_diff": {"...": "..."}, "grade": "estimate"}

cad_reexecute {"plan_path": "D:/plans/bracket.plan.yaml",
               "parameter_overrides": {"wall": 2.2}}
→ {"ok": true, "volume_delta_mm3": "...", "bbox_delta_mm": "...",
   "selector_drift": []}        // non-empty drift == honest rebuild-error warning
```

---

## 3. Contracts (read once, rely on always)

### 3.1 body_id lineage and undo semantics — bodies are IMMUTABLE

Every tool that changes geometry (`cad_generate`, `cad_modify`, `cad_repair_dfm`,
`cad_dfm_workflow` with repair) mints a NEW `body_<8hex>` id and records
`parent_id + op_note`. Nothing ever mutates an existing body. Consequences:

- `cad_undo(body_id)` = "give me the parent id". It destroys nothing; the child id you
  had remains valid, so redo = reuse it. Undo at a root refuses with `fm.at_root`.
- Ids never expire within a session. Eviction from the live LRU (§3.6) drops only the
  in-memory object; the record + STEP snapshot persist and `get` transparently
  re-imports.
- An id the store never minted refuses with `fm.unknown_body_id` — that means YOUR
  bookkeeping is wrong (typo, or an id from a previous session), not a server bug.

### 3.2 `ok` vs `status` vs `spec_ok` — three different questions

- On most tools, `ok` answers "did the TOOL run without an internal fault".
- On `cad_generate`/`cad_modify`, `status` answers "did the GEOMETRY build":
  `ok` (all steps built) | `partial` (a solid IS written and analysable — `body_id` is
  minted — but some steps failed; fix just those with `cad_modify`) | `error` (no solid;
  `body_id` is null). `is_solid` is the honest gate: no solid, no body_id, no files —
  a shell/compound result is never silently passed off as a solid.
- On `cad_preflight`, `ok` = "preflight itself ran"; **`spec_ok`** = the spec verdict
  (all ops known + all args valid). A spec_ok=false response is still `ok: true`.
  Do not send a spec to `cad_generate` while `spec_ok` is false.

### 3.3 Machine-actionable failures — the self-correction inputs

Failed steps in `cad_generate`/`cad_modify` responses gain: `likely_cause` (closed
buckets: `unknown_op`, `args_invalid`, `selector_zero_match`, `fm_refusal:<fm.token>`,
`occt_failure` as the catch-all), `suggested_fix` (one actionable sentence),
`related_skills` (registered skill names to run next), `selector_match_count` (only when
the step's args carry a selector and a body exists; `null` = the resolver could not run —
an honest "don't know", notably for `edges_convex_only`/`edges_concave_only`), and
`selector_suggestions` (only on a 0-match). Enrichment appends, never replaces: the raw
`error` string is byte-identical to what the executor produced. `likely_cause` is a
hint, not an authority.

### 3.4 `fm.*` refusal tokens — refusals are data, not exceptions

House rule: a skill that cannot honestly do the job raises `ValueError` with a stable
`fm.<token>` string. These land in `error` and are classified as
`likely_cause: "fm_refusal:fm.<token>"`. Treat each token as a machine-actionable
signal with a known repair, e.g.:

- `fm.sweep_tangent_discontinuity` — sweep path has a kinked (>30°) joint; insert a
  tangent fillet arc at the corner (the `neg_sweep_kinked_path_refused` recipe pins
  exactly this: MakePipeShell would SILENTLY drop volume at the kink, so we refuse).
- `fm.timeout` — the plan exceeded the worker budget (§3.6); simplify the step or raise
  the env budget, don't just resend.
- `fm.unknown_body_id`, `fm.at_root` — session bookkeeping (§3.1).
- `fm.snapshot_missing`, `fm.step_parse_failed`, `fm.step_write_failed` — the STEP
  snapshot durability layer failed; the store refuses rather than half-registering.
- `fm.args_invalid`, `fm.bad_args` — your input shape is wrong; fix the call.

Never "work around" a refusal by disabling the check — refusals encode geometry/honesty
constraints the project deliberately enforces. Negative recipes exist so you can learn
the constraint from the corpus instead of by trial.

### 3.5 Tags do not survive STEP round-trips

`cad_modify`, LRU re-import, and repair all round-trip geometry through STEP. STEP
carries geometry only: **in-session face tags (`tag_face`), build123d component names,
labels and colors attached in-memory are LOST** across these boundaries (volume/topology
are preserved to STEP precision). The store marks such bodies with a sticky
`reimported: true`. Practical rule: a `tagged` selector is only reliable within the SAME
spec that created the tag; across `cad_modify` calls, select geometrically
(`faces_by_normal`, `faces_by_area`, `face_named`, `axis_aligned_edges`, …).

### 3.6 Hang-proofing, timeouts, and the live-body LRU

Plans run in a warm worker subprocess with a hard timeout — one stuck OCCT builder
cannot block the stdio server. `PHONE_DESIGNER_SKILL_TIMEOUT_S` (default 120 s; `0` =
inline, no worker — also the automatic fallback if the worker cannot spawn) resolves the
budget; on timeout you get `{ok: false, error: "fm.timeout: op exceeded <N>s"}` and the
worker is killed + lazily respawned — the session and all body_ids survive. Only the
STEP crosses the pipe; the cached body is re-imported from it (hence §3.5).
The session keeps at most `PHONE_DESIGNER_MCP_MAX_LIVE` (default 32) live bodies, LRU on
last touch; evicted bodies transparently re-import from their snapshot on next use — you
never need to manage this, only to remember the tag loss it implies.

### 3.7 Grade labels — what a number is worth

- **`grade='estimate'`** — every cost, process recommendation, DFM repair, compare,
  variant result. A transparent heuristic model, NOT a quote or a guarantee; the label
  is embedded inside artifacts (quote-package manifest) too. Repeat the label when you
  relay these numbers to a human.
- **`grade='draft'`** — `cad_drawing` sheets: DRAFT FOR REVIEW, anchored callouts +
  tables, no automatic leader placement (v1). Not a released drawing.
- **Measured** — direct geometry queries (`cad_measure`, `volume_mm3`/`bbox_mm` on
  generate results, recipe `expected` invariants — which are recorded by EXECUTING the
  recipe, never guessed). Exact to modelling/STEP precision.

Other honesty markers you will meet and must preserve when summarising:
`static_pose_only` (assembly interference), `skipped_no_gl` (preview on headless),
catalog-not-machined pricing for recognized standard parts, `selector_drift` on
re-execute, and the strict viability gate on `cad_cheapest_variant`.

---

## 4. Appendix — three real recipes from `recipes/`

These are committed, CI-executed corpus entries (58 files today; schema in
`recipes/README.md`). Volume ranges are MEASURED ±2%, never guessed — the anti-rot pin
(`tests/test_recipes_execute.py`) re-executes every one.

**`counterbore_m3_center`** — the fastener-catalog idiom. `thread_spec: "M3"` pulls
bore/counterbore diameters from the metric catalog; `depth_mm` is the shaft depth BELOW
the counterbore floor:

```yaml
intent_kr: 윗면 중앙에 M3 카운터보어 구멍(육각 소켓 머리 자리)을 가공해줘
spec:
- op: box
  args: {length_mm: 30, width_mm: 30, height_mm: 10}
- op: counterbore_hole
  args:
    face_selector: {kind: faces_by_normal, direction: [0.0, 0.0, 1.0], tol_deg: 5.0}
    thread_spec: M3
    fit: medium
    depth_mm: 6
expected: {is_solid: true, volume_mm3: [8670.726, 9024.634]}
```

**`gear_external_involute`** — one-step parametric machine element; pitch diameter =
`module_mm * n_teeth` = 36 mm, `bore_diameter_mm: 0` = no bore:

```yaml
intent_en: "Create an external involute spur gear: module 1.5, 24 teeth, face width 8,
            with a 5 mm center bore"
spec:
- op: gear_external_involute
  args: {module_mm: 1.5, n_teeth: 24, width_mm: 8, bore_diameter_mm: 5}
expected: {is_solid: true, volume_mm3: [7673.758, 7986.972]}
```

**`neg_sweep_kinked_path_refused`** — a NEGATIVE recipe: the spec is EXPECTED to fail
with a pinned `fm.*` token. The tiny placeholder box exists only so the failing spec
still returns a body (`generate_from_spec` has a `body_present` post-condition):

```yaml
intent_kr: 부정 예제 — 90도로 꺾인(킹크) 경로 스윕은 fm.sweep_tangent_discontinuity 로
           거절된다. 모서리에 접선 연속 원호를 넣어야 한다
spec:
- op: box
  args: {length_mm: 5, width_mm: 5, height_mm: 2}
- op: sketch_sweep
  args:
    section: {kind: circle, diameter_mm: 6}
    path:
      kind: path
      plane: XY
      segments:
      - {kind: line, start: [0, 0],  end: [20, 0]}
      - {kind: line, start: [20, 0], end: [20, 20]}   # 90° kink — refused
expected:
  ok: false
  failing_op: sketch_sweep
  error_contains: fm.sweep_tangent_discontinuity
```

The positive counterpart (`sketch_sweep_bent_pipe`, shown in Loop 1) replaces the kink
with a tangent `radius: 8` arc — that pair, refusal plus repair, is the pattern this
whole guide wants you to internalize: **preflight, generate, read the structured
failure, apply the pinned idiom, verify the measured numbers.**
