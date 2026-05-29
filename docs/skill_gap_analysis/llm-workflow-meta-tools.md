# LLM Workflow Meta-Tools — Skill Gap Analysis

**Domain**: Meta-skills the LLM uses to *operate* the skill library efficiently —
dry-run / preview / search / chain / introspect / explain. These are the
**control-plane** skills that turn the existing ~150 OCCT skills into something
an LLM can wield without thrashing.

**Author**: deep-analysis agent (LLM Workflow Meta-Tools)
**Date**: 2026-05-29
**Library state at analysis time**: ~150 skills across `create / compose /
inspect / modify_* / assembly / io`. Manifest at
[`d:/SmartTwinModeller/manifest.json`](../../manifest.json).

---

## 1. Survey — what's already there

Grep'ing the existing registry (`export_manifest.py`, manifest.json) shows the
control-plane is currently almost empty.

### Already present (LLM-meta-tool-adjacent)

| Skill | Category | What it gives the LLM |
|---|---|---|
| `selector_preview` | inspect | **Dry-run a selector only.** Returns matched entity list + center/area/length/surface_type. Swallows exceptions into `extras.error` so LLM can read & retry. The *only* true LLM control-plane tool today. |
| `find_features` | inspect | Heuristic feature inventory (hole / pocket / fillet / chamfer). Boss + rib **explicitly punted** (`notes: "not implemented"`). LLM hint only — not ground truth. |
| `inspect_geometry` | inspect | Bbox + volume + face/edge count. Static body summary. |
| `measure` | inspect | Point-to-point / edge length / face area. Manual probe. |
| `audit.py` (module, not skill) | tooling | Runs *every* skill against a standard body to build a history catalog. **Not exposed to LLM.** |
| `_post_conditions.py` (framework) | core | Declarative `volume_decreased / face_count_changed / body_present` checks. Raises `PostConditionError`. The LLM only sees the error message — no structured "why" or "suggest fix". |
| `_history.py / EntityHistoryMap / SelectorFreeze` | core | Per-step freeze: `(matched_count, topology_signature)`. Determinism backbone. Diff reporting documented in [`plan-determinism.md`](../../lat.md/plan-determinism.md) but **no skill exposes plan-vs-plan diff** to the LLM. |
| `PlanExecutor` (`plan/executor.py`) | core | Sequential apply + freeze check + cache `step_results`. **No `--dry-run` mode, no `--up-to-step` predict, no rollback simulator.** |

### What is NOT there (confirmed by Grep on `src/`)

Zero matches in `src/` for any of: `dry_run`, `what_if`, `suggest_selector`,
`autochain`, `skill_search`, `replay_diff`, `nl_to_selector`,
`explain_failure`, `schema_introspect`. The LLM workflow surface is a
**known greenfield**.

### Implication

The library has invested heavily in *executing* skills correctly (history
propagation, freeze, post-conditions). It has invested almost nothing in
*helping the LLM choose* which skill to run, *preview* what will happen, or
*recover* when something goes wrong. From the cost model in
[`llm.md#비용`](../../lat.md/llm.md) — 20 steps × 5 backtracks per session at
$5 budget — every avoided retry directly converts to LLM spend savings. This
domain is high-leverage.

---

## 2. Top Missing — prioritized

Each entry: `WHAT (concrete skill name + 1-line behavior + Pydantic args)`,
`WHY (real workflow / cost lever)`, `STANDARD (if applicable)`, `PRIORITY`,
`OCCT difficulty`.

### 2.1 `dry_run_skill` — P0, moderate

**What.** Apply a single skill in a *sandboxed copy* of the body and return
predicted Δ(volume_mm³, face_count, edge_count, bbox) + freeze that *would*
be recorded, **without mutating the executor state**.

```python
class Args(BaseModel):
    skill_name: str
    args: dict[str, Any]
    summary_only: bool = True   # if False, return serialized OCCT BREP blob
```

Returns extras:
```
{ "predicted": {"volume_delta_mm3": ..., "face_count_delta": ...,
                "bbox_delta_mm": [...], "freeze": {...}},
  "post_condition_results": [{"kind":"volume_decreased","passed":true,...}],
  "would_fail": false, "warnings": [...] }
```

**Why.** In editor mode (`llm.md#editor-mode`) the LLM proposes
`fillet_edges_by_predicate radius=3.0` but doesn't know if radius 3.0 will
exceed half the shortest selected edge (the `pc.radius_less_than_half_shortest_edge`
precondition). A dry-run lets the LLM converge on a valid radius **in one
turn** instead of failing → reading PostConditionError → guessing again.
At $0.04–0.14/step (`llm.md#비용`), three retries = one full extra step
budget burned.

**Standard.** N/A — purely internal workflow.

**OCCT difficulty.** Moderate — needs `BRepBuilderAPI_Copy` of the input
shape, then runs the existing `SkillBase.apply()` on the copy. The trick is
sandboxing `_tag_propagation.propagate_tags` so it doesn't mutate the input
body's tag dict.

---

### 2.2 `suggest_selector_from_phrase` — P0, hard

**What.** Given a natural-language phrase ("the top rim", "the camera island
side wall", "all the small holes inside the cavity"), return a ranked list
of selector JSON candidates with a *preview hit count* for each.

```python
class Args(BaseModel):
    phrase: str
    kind: Literal["faces","edges","vertices"]
    max_candidates: int = 5
    body_context: bool = True  # use current body bbox/features to disambiguate
```

Returns:
```
{ "candidates": [
    {"selector": {...}, "match_count": 1, "explanation": "top face named on s1",
     "confidence": 0.91},
    {"selector": {...}, "match_count": 4, "explanation": "...",
     "confidence": 0.62}, ...
  ] }
```

Internally: rule-based phrase → selector grammar (top/bottom/side/rim/edge/face/
corner/longest/shortest/largest/smallest/inside/outside + tag lookup) +
optional embedding similarity over face tag history. Each candidate is
validated via `selector_preview` before being returned (so the LLM never sees
zero-match suggestions).

**Why.** This is the single biggest UX lever in editor mode. Today the LLM
must hand-author selector JSON trees like
`{"kind":"and","left":{"kind":"edges_on_face","face":{"kind":"face_named","name":"top"}},"right":{"kind":"axis_aligned_edges","axis":"Z"}}`
from a phrase like "top rim". Production CAD assistants (Onshape AI, NX
NextGen) all have phrase→selector as their flagship feature. The watch
crown_shaft_hole regression in [`skills.md#깨짐-catalog`](../../lat.md/skills.md)
specifically called out that the LLM authored wrong selectors in 3/12
scenarios — a phrase suggester would have caught all three.

**Standard.** N/A — vocabulary is internal, but the *grammar* should mirror
the named-feature vocabulary in [PMI ISO 16792](https://www.iso.org/standard/72448.html)
("top", "bottom", "front" face naming for product-and-manufacturing info).

**OCCT difficulty.** Hard — the rule grammar itself is moderate, but
**disambiguating** with body geometry (which face is "top" on a watch with a
dome?) requires hooking into `symmetry_axes`, `inspect_geometry` and the tag
history. Plan for a v1 that handles only canonical phrases ("top face",
"bottom rim", "all sharp edges") and a v2 that uses embedding similarity
over tag names.

---

### 2.3 `predict_post_conditions` — P0, moderate

**What.** Without applying the skill, evaluate all of the skill's declared
preconditions + post_conditions *symbolically* against current body metrics
and return a Boolean + explanation per condition.

```python
class Args(BaseModel):
    skill_name: str
    args: dict[str, Any]
```

Returns:
```
{ "preconditions": [
    {"ref":"pc.radius_less_than_half_shortest_edge",
     "passed": false,
     "reason": "shortest matched edge = 4.2 mm; requested radius = 3.0 mm > 2.1 mm",
     "suggested_fix": "use radius_mm ≤ 2.0"},
    ...],
  "post_conditions_inferable": [...]
}
```

**Why.** Today preconditions are *named refs* (`preconditions: list[str]`) in
the `SkillSpec` — they exist as documentation only. There is no runtime
evaluator. `_post_conditions.py` evaluates *after* the apply, so the LLM
only learns post-hoc. A symbolic predictor lets the LLM self-validate
*before* burning $0.04 on apply. The example from
[`llm.md#12-시나리오`](../../lat.md/llm.md) — "두께를 1mm 줄여줘" on a 1.2mm
wall is a guaranteed `volume_decreased` violation → predictable in
microseconds.

**Standard.** Aligns with NIST [Geometric & Topological Tolerance
Evaluation](https://www.nist.gov/programs-projects/digital-thread-smart-manufacturing)
declarative-validation pattern.

**OCCT difficulty.** Moderate — requires building a precondition *registry*
(parallel to the existing post-condition framework) with each ref backed by
a `(body, args) → (bool, reason)` evaluator. ~30 atomic skills × ~2
preconditions each = ~60 evaluators. Many are arithmetic (`min_wall_mm`,
`min_draft_deg`) and reusable.

---

### 2.4 `find_skill_by_intent` — P0, moderate

**What.** Semantic + keyword + tag search over the skill manifest. LLM asks
"how do I add a screw boss with a counterbore?" → returns
`boss_with_hole + hole(kind=cbore)`. Ranks by `(summary embedding cosine,
produces_features overlap, category match, recently_failed_skills demotion)`.

```python
class Args(BaseModel):
    intent: str
    category_filter: list[str] | None = None
    level_filter: Literal["atomic","macro","any"] = "any"
    max_results: int = 10
    include_args_skeleton: bool = True
```

Returns:
```
{ "matches": [
    {"name":"boss_with_hole","level":"macro","score":0.92,
     "category":"modify/boss",
     "summary":"...",
     "args_skeleton":{"position":[0,0,0],"boss_diameter_mm":4.0,...},
     "produces_features":["boss_face","hole_face"]},
    ...]}
```

**Why.** Today the LLM gets the **entire manifest** in its system prompt
(~150 skills × ~500 tokens each ≈ 75K input tokens). With ~7K tokens budgeted
per step (`llm.md#비용`), that's a 10× overrun and the cache discipline in
`llm.md#caching` only works for skills that the LLM *already knows*. A
semantic search reduces the working set to ~10 candidates per turn → enables
**dynamic skill loading** (LLM asks for skills, never sees the full manifest
unless needed). This single skill changes the cost equation.

**Standard.** N/A — pure tooling.

**OCCT difficulty.** Trivial (no OCCT). Easy implementation: bm25 over
`(name, summary, produces_features, category)` + small embedding index
(sentence-transformers MiniLM, ~80MB). Note this is a **must** for
agentic Planner mode — at 20 steps + 5 backtracks, a 70K-token manifest blows
the $5 session cap on context alone.

---

### 2.5 `propose_skill_chain` — P1, hard

**What.** Given `(start_body, target_metrics)` or `(start_body, goal_phrase)`,
return ordered candidate skill chains (length 1–5) that transform start → goal,
ranked by Σ(`cost_hint`) + sandboxed dry-run feasibility check.

```python
class Args(BaseModel):
    goal: Literal["match_metrics","match_phrase","produce_feature"]
    target_metrics: dict | None = None       # {"volume_mm3":..., "feature_count":{"hole":4}}
    target_phrase: str | None = None
    target_feature: str | None = None        # produces_features tag
    max_chain_length: int = 5
    max_candidates: int = 3
```

Returns:
```
{ "chains": [
    {"steps":[
        {"skill":"extrude_pocket","args":{...}},
        {"skill":"final_fillet_all_sharp_edges","args":{"radius_mm":0.3}}],
     "predicted_cost_hint": 0.45,
     "dry_run_feasible": true,
     "rationale": "..."}, ...]}
```

**Why.** Two concrete LLM workflows where this is the difference between
success and a 20-turn loop: (a) **DFM auto-repair** — DFM validator reports
"min_wall < 0.8mm on face f_42"; LLM needs to find a chain to fix
(`surface_offset` + `face_face_fillet`) without trial-and-error apply. (b)
**Composition** — Planner mode given ComponentArrangement currently calls
`HousingSynthRule` (rule-based), but the spec [`llm.md#planner-mode`](../../lat.md/llm.md)
explicitly envisions the LLM proposing the chain. Today the LLM has no
chain-search primitive, so it open-loops with no cost-of-search budget.

**Standard.** N/A.

**OCCT difficulty.** Hard — requires (i) a feature-graph over
`produces_features` / `preserves` (already in `SkillSpec`!), (ii) sandboxed
multi-step dry-run, (iii) cost-bounded BFS/A* search. The metadata is *already*
in the manifest — produces_features + preserves were designed for this
exact purpose ([`skills.md#manifest-구조`](../../lat.md/skills.md)). Just
nobody wired up the search yet.

---

### 2.6 `plan_diff` — P1, trivial

**What.** Structural diff between two `Plan` YAMLs (or a Plan and its
post-execution variant). Reports added/removed/modified steps,
arg-by-arg changes, freeze drift, status flips.

```python
class Args(BaseModel):
    plan_a_path: str   # whitelisted
    plan_b_path: str
    include_args_diff: bool = True
    include_freeze_diff: bool = True
```

Returns:
```
{ "added_steps": [...],
  "removed_steps": [...],
  "modified_steps": [
    {"id":"s4","arg_changes":{"radius_mm":{"old":3.0,"new":2.0}},
     "freeze_changes":{"matched_count":{"old":4,"new":3}}}],
  "summary": "1 step modified, 1 freeze drift" }
```

**Why.** `plan-determinism.md#diff-리포트` defines a diff JSON-line format
*for the freeze-mismatch case only*, but the LLM's Editor flow constantly
needs to answer "what changed since my last propose?" — replace_step /
remove_step in `llm.md#도구` produce *new plans*, and the LLM currently
must diff them in its head. For the "방금 변경 되돌려" scenario
(`llm.md#12-시나리오` #9), a structured diff lets the LLM verify the undo
worked.

**Standard.** N/A.

**OCCT difficulty.** Trivial — pure Pydantic / dict diff, no OCCT.

---

### 2.7 `tag_inventory` — P1, trivial

**What.** Enumerate every persistent tag currently attached to the body
(via `tag_face` or auto-propagated) with provenance (which step set it,
which face it's on now, current center/area/measure).

```python
class Args(BaseModel):
    pattern: str | None = None   # glob — "camera_*" etc.
    include_history_chain: bool = False
```

Returns:
```
{ "tags": [
    {"name":"camera_island_top",
     "kind":"face",
     "current_entity":{"center":[...],"area_mm2":...,"surface_type":"plane"},
     "set_by_step":"s3",
     "propagation_chain":["s3:tag_face","s4:fillet:MODIFIED_INHERIT",
                          "s7:extrude_pocket:MODIFIED_INHERIT"],
     "still_valid":true},
    ...] }
```

**Why.** Tags are the most-stable selector kind (★★★★★ in
[`skills.md#selectors`](../../lat.md/skills.md)) but the LLM has no way to
*see* them. Today it must remember every `tag_face` call across 20 turns.
For the watch crown scenarios in `audit.py` (`audit_top_tag` etc.) the LLM
inevitably loses track and re-tags or references nonexistent tags. This is
the lowest-effort, highest-value introspection tool.

**Standard.** N/A — internal taxonomy. Loosely analogous to STEP AP242
[ISO 10303-242 Persistent Identifier](https://www.iso.org/standard/66654.html)
PIDs.

**OCCT difficulty.** Trivial — `_tag_propagation.py` and `compose.tag_face`
already maintain `body._pd_tags`. Just expose it.

---

### 2.8 `explain_failure` — P1, moderate

**What.** Given a `FailureMeta` from `Plan.steps[k].failure`, produce a
structured root-cause hypothesis + suggested fixes. Internally maps
`error_type` → playbook + inspects body state + nearest passing freeze.

```python
class Args(BaseModel):
    plan_path: str
    step_id: str
    include_state_dump: bool = True
```

Returns:
```
{ "step":"s4", "skill":"fillet_edges_by_predicate",
  "raw_error":"BRepFilletAPI failed: radius too large",
  "root_cause_hypothesis":"radius 3.0 mm exceeds half the shortest matched edge (2.1 mm)",
  "likely_violated_precondition":"pc.radius_less_than_half_shortest_edge",
  "suggested_fixes":[
    {"action":"reduce_arg","arg":"radius_mm","new_value":1.5,
     "predicted_pass":true},
    {"action":"refine_selector","hint":"exclude edges shorter than 4 mm",
     "selector_patch":{...}}],
  "related_skills":["face_face_fillet","variable_radius_fillet"] }
```

**Why.** Currently `FailureMeta` carries `error_type`, `message`,
`mapped_message`, `raw_traceback` — all *strings*. The LLM gets a stacktrace
and is on its own. The `selector_preview` skill is the only one that already
swallows errors and returns structured diagnostics. Generalize that pattern
to *every* failure. In LLM-call cost terms: every avoided round-trip after a
failure saves $0.04–0.14.

**Standard.** N/A. Loosely follows the "explainable AI" failure-mode
taxonomy in [ASME Y14.41 Annex C](https://www.asme.org/codes-standards/find-codes-standards/y14-41-digital-product-definition-data-practices).

**OCCT difficulty.** Moderate — needs an error_type → playbook lookup table
+ symbolic precondition re-evaluation (overlaps with #2.3). Reuses
`failure_modes: list[str]` slot already in `SkillSpec`.

---

### 2.9 `skill_schema_introspect` — P1, trivial

**What.** Return the Pydantic JSON Schema + selector_kinds + history_rules
+ preconditions + manufacturing rules + cost_hint for one specific skill,
on demand. Dynamic counterpart to the static manifest.

```python
class Args(BaseModel):
    skill_name: str
    include_examples: bool = True   # pull from tests/skills/test_<name>.py
```

Returns: the full `SkillSpec.to_manifest_dict()` payload + ≤3 worked example
arg dicts from the test suite.

**Why.** With #2.4 (`find_skill_by_intent`) reducing the LLM's working set
to ~10 skills, we need a way to fetch *full* schemas only when the LLM
commits to using a skill. This is the **on-demand manifest** that makes
dynamic skill loading viable. Without it the LLM either gets nothing or
everything.

**Standard.** N/A. Pattern mirrors OpenAPI `/components/schemas/{name}`
dynamic resolution.

**OCCT difficulty.** Trivial — registry already exposes
`SkillSpec.to_manifest_dict()`. Add example extraction.

---

### 2.10 `preview_plan_metrics` — P1, moderate

**What.** Execute the plan in dry-run mode up to step N (or to the end)
and return per-step predicted (volume, face_count, bbox, freeze) without
committing to the executor's `step_results` cache.

```python
class Args(BaseModel):
    plan_path: str
    up_to_step: str | None = None    # default = run whole plan
    return_per_step_metrics: bool = True
```

Returns:
```
{ "steps": [
    {"id":"s1","predicted":{"volume_mm3":...,"face_count":...,
     "freeze":{...},"would_fail":false}},
    ...],
  "final_metrics": {...},
  "would_fully_pass": true,
  "first_failing_step": null }
```

**Why.** Two flows: (a) **Replan validation** — LLM proposes a new plan
variant; before committing, predict whether it will fully pass. (b)
**Backtrack planning** — `llm.md#planner-mode` allows 5 backtracks per
session; without a metrics preview the LLM uses backtracks as
trial-and-error. With preview, backtracks become deliberate. Compounds with
#2.1 (dry_run_skill).

**Standard.** N/A.

**OCCT difficulty.** Moderate — needs an `ExecutionMode.PREDICT` in
`PlanExecutor` that runs each step on a copied body and discards the result
but records metrics. Reuses #2.1 internally.

---

### 2.11 `selector_robustness_score` — P2, moderate

**What.** Given a selector + current body, score how brittle the selector
is to small upstream changes (Δ radius ±10%, Δ depth ±10%, +draft 1°)
by perturbing the body and re-running selector resolution.

```python
class Args(BaseModel):
    selector: dict
    kind: Literal["faces","edges"]
    perturbations: list[Literal["fillet_radius","extrude_depth","draft_angle","corner_radius"]] | None = None
```

Returns:
```
{ "baseline_count": 4,
  "robustness_score": 0.83,    # avg fraction-preserved across perturbations
  "by_perturbation": {
    "fillet_radius_+10%": {"count":4,"preserved":1.0},
    "extrude_depth_-10%": {"count":3,"preserved":0.75},
    ...},
  "suggested_more_stable_selector": {...} | null }
```

**Why.** The selector stability rank in `skills.md#selectors` is **subjective**
(★★★★★ for tagged, ★ for edges_by_position). A measured score per actual
selector instance is much more useful — and lets the LLM pick the
parameterization-robust option in design-space exploration (Phase 7 watch
variants). Aligns with the persistent-naming "70% propagate" Go/No-Go in
`persistent-naming#확장-검증`.

**Standard.** N/A. Methodologically borrows from sensitivity analysis in
[NAFEMS R0099](https://www.nafems.org/publications/resource_center/r0099/)
("Verification & Validation of CAE Models").

**OCCT difficulty.** Moderate — needs a small perturbation harness that
reapplies upstream steps with ±X% args. Slow (5–10 sec per selector) so
should be opt-in / cached.

---

### 2.12 `precondition_evaluate` — P2, moderate

**What.** Standalone evaluator for a single named precondition ref against
current body + args. Backbone for #2.3 and #2.8 but also useful on its own
for LLM "is this safe?" probes.

```python
class Args(BaseModel):
    precondition_ref: str         # e.g. "pc.radius_less_than_half_shortest_edge"
    skill_name: str
    skill_args: dict
```

Returns: `{"passed": bool, "reason": str, "evidence": {...}}`

**Why.** Decouples the precondition-check primitive from the "predict all
preconditions" composite (#2.3), enabling LLM to interactively narrow down
*which* precondition is the issue.

**Standard.** N/A.

**OCCT difficulty.** Moderate — same evaluator registry as #2.3, just one
ref at a time.

---

### 2.13 `replay_with_overrides` — P2, hard

**What.** Re-execute a stored plan but override one or more step arg values
on the fly, return ExecutionResult without persisting. Lets LLM say "redo
plan with s4.radius_mm = 1.5 instead of 3.0".

```python
class Args(BaseModel):
    plan_path: str
    overrides: list[dict]  # [{"step_id":"s4","arg_path":"radius_mm","value":1.5}]
    stop_on_first_failure: bool = True
```

**Why.** The "방금 변경 되돌려" / "두께를 1mm 줄여줘" / "카메라 plateau +0.5mm"
scenarios in `llm.md#12-시나리오` are all parameter tweaks. Today each one
forces the LLM through propose_step → replace_step → re-run. With overrides
it's a single tool call returning the metrics, then a confirm.

**Standard.** N/A.

**OCCT difficulty.** Hard — has to dot-walk into nested arg dicts (e.g.
`overrides.arg_path = "sketch.diameter_mm"`) and re-validate against
`Args` Pydantic model.

---

### 2.14 `body_state_summary` — P2, trivial

**What.** A *compact* body summary tuned for LLM context: tag inventory
(top 10 by importance) + bbox + key feature counts + recent step provenance
+ outstanding DFM warnings — all in ~500 tokens.

```python
class Args(BaseModel):
    max_tags: int = 10
    include_dfm_warnings: bool = True
    include_recent_steps: int = 5
```

**Why.** The current "DYNAMIC" cache section in `llm.md#caching-breakpoint-배치`
serializes the **full plan markdown + ComponentArrangement + DFM summary**
per turn. A focused 500-token summary cuts dynamic-section size by 5–10×,
extending caching effectiveness and reducing input cost.

**Standard.** N/A.

**OCCT difficulty.** Trivial — composes existing `inspect_geometry` +
`find_features` + (new #2.7) `tag_inventory`.

---

## 3. Cross-Cutting Infrastructure Gaps

These are not skills themselves but underlying capabilities that *every*
meta-tool above will need.

1. **Precondition evaluator registry.** Today `preconditions: list[str]`
   is documentation-only. Build a `pc.<ref> → (body, args) → (bool, reason, evidence)`
   registry parallel to the `PostCondition` framework in `_post_conditions.py`.
   Required for #2.3, #2.8, #2.12.

2. **Sandboxed body copy.** `BRepBuilderAPI_Copy` + tag-dict copy + history
   reset. Currently no helper exists. Required for #2.1, #2.10, #2.11, #2.13.

3. **Failure-mode → playbook table.** `failure_modes: list[str]` is also
   documentation-only. Need `fm.<ref> → diagnose(body, args, exception) → hypothesis`.
   Required for #2.8.

4. **`ExecutionMode.PREDICT`** in `PlanExecutor`. Currently STRICT / LOOSE
   only. Required for #2.10, #2.13.

5. **Selector serializer round-trip tests.** Selectors come in as JSON,
   become `SelectorBase`, get re-serialized in `selector_preview.extras.selector`.
   Several composite selectors (And/Or/Not) currently don't round-trip
   cleanly — observed in `_selector_to_dict` fallback. Required for any
   skill that returns a selector (#2.2, #2.11).

6. **Embedding index over skill summaries.** Need a build step (probably
   added to `export_manifest.py`) that produces a static embedding index.
   Required for #2.2, #2.4. ~80MB MiniLM model on-disk; fine.

7. **Feature graph over `produces_features` / `preserves`.** Already
   documented per-skill but never used. Required for #2.5 (chain search)
   and #2.8 (related-skills suggestion).

8. **Example mining from tests/.** Tests already exercise every skill with
   realistic args. A doc generator that extracts the first PASS example per
   skill (and serializes its args) gives free worked-examples for #2.9 and
   #2.4. Marginal cost ~1 day; high value.

---

## 4. Domain-Specific Catalogs Needed

Catalogs that the meta-tools should consume or produce (separately from the
core registries above).

1. **Phrase → selector grammar catalog.** YAML file
   `data/phrase_grammar.yaml` listing canonical phrases ("top face",
   "bottom rim", "camera island side wall", "all the sharp edges") + their
   selector templates + body-context disambiguation rules. Bootstrap with
   the 12 regression scenarios from `llm.md#12-시나리오`. Used by #2.2.

2. **Precondition catalog.** `data/preconditions.yaml`: one entry per
   `pc.<ref>` with description, args contract, evaluator function name.
   Audited against `SkillSpec.preconditions` in CI.

3. **Failure-mode playbook catalog.** `data/failure_modes.yaml`: one entry
   per `fm.<ref>` with diagnose template + suggested fixes + related
   skills. Audited against `SkillSpec.failure_modes` in CI.

4. **Skill-intent embedding cache.** Build artifact
   `build/skill_embeddings.npz` produced alongside `manifest.json`. Required
   for #2.4. Stable across runs (deterministic with fixed-seed embedder).

5. **Body-summary template registry.** `data/body_summary_templates.yaml`
   per body kind (watch / phone / earbud) — what feature counts matter,
   what tags are critical. Used by #2.14.

---

## 5. Worked Examples — what improves with these tools

### Example A — Editor mode: "camera plateau +0.5mm" (`llm.md#12-시나리오` #3)

**Today (no meta-tools)**:
1. LLM reads full manifest (~75K tokens).
2. LLM guesses: `propose_step(extrude_plateau, args={face_selector: face_named=top, sketch: circle d=20, height_mm=+0.5})`.
3. Apply fails — current plateau is at z=8.2, increment must be relative.
4. LLM reads FailureMeta string, retries with different args. 3–4 turns.
5. Cost: ~$0.4 (4× $0.10).

**With meta-tools**:
1. LLM calls `body_state_summary` (#2.14) → 500 tokens.
2. LLM calls `find_skill_by_intent("increase plateau height")` (#2.4) → 3 candidates including `extrude_plateau` and `surface_offset`.
3. LLM calls `tag_inventory(pattern="camera_*")` (#2.7) → finds tag `camera_plateau_top`.
4. LLM calls `dry_run_skill("extrude_plateau", args=...)` (#2.1) → predicted Δvol = 250 mm³, freeze stable.
5. LLM commits.
6. Cost: ~$0.08 (1× full apply + 3× cheap previews ≈ $0.02 each).
**5× cost reduction.**

### Example B — Planner mode: "워치 housing 합성" (`llm.md#12-시나리오` #11)

**Today**: HousingSynthRule (rule-based, deterministic, ~30 steps). LLM
is *not* invoked; spec says LLM-mode is "deferred".

**With #2.5 (propose_skill_chain) + #2.10 (preview_plan_metrics)**: LLM
can be invoked to refine the rule-based plan — e.g. "make the cavity 0.2mm
deeper and add a heat-stake boss near component X". Each refinement is
chain-searched + metrics-previewed → committed only if would_fully_pass.
Unlocks the LLM-Composition mode that the spec already envisions.

### Example C — DFM auto-repair after composition

**Today**: DFMValidator returns violations as text. LLM reads, proposes
fixes one by one, no chain coherence.

**With #2.8 (explain_failure) + #2.5 (propose_skill_chain)**: LLM gets a
structured (violation → fix chain) mapping per violation, dry-runs each
chain, picks the chain that resolves the most violations with the lowest
cost_hint sum. **This is the canonical agentic loop the spec wants.**

---

## Summary

The library has built a strong *executor* but no *navigator*. The 14 skills
above turn the existing ~150 skills into something an LLM can actually
operate within a $5/session budget. **Priority stack**: #2.1, #2.2, #2.3,
#2.4 are the P0 quartet — without them, agentic planner mode is
economically unviable at current input-token rates. #2.5–#2.10 unlock
multi-turn flows and DFM auto-repair. #2.11–#2.14 are polish.

Roughly 60% of the implementation effort is shared infrastructure (precondition
registry, sandbox copy, embedding index) that benefits every meta-tool.
Estimated effort: ~3 engineer-weeks for the P0 quartet including infrastructure;
~6 weeks total for all 14.
