# recipes/ — executed-and-pinned few-shot spec corpus (track 2-5)

Each `*.yaml` here is ONE recipe: a small, **actually executed** example of a
`generate_from_spec` spec for an idiom that bites a cold LLM (composite arc
profiles, the `sketch_revolve` Z-lock, the sweep G1 tangent law, face-selector
pocket/boss/fillet idioms, boolean/transform verbs, ...). They are served to
MCP clients through `find_recipe` (`src/phone_designer/mcp_support/_recipes.py`).

## Schema

```yaml
name: sketch_sweep_bent_pipe        # MUST equal the file stem
intent_en: "..."                    # natural-language intent, English
intent_kr: "..."                    # natural-language intent, Korean
tags: [sweep, pipe, bent, ...]      # search keywords
spec:                               # runs via generate_from_spec, verbatim
- op: sketch_sweep
  args: {...}
expected:                           # MEASURED by executing the recipe
  is_solid: true
  volume_mm3: [min, max]            # actual volume ±2%
  bbox_mm: [[dx,dy,dz]_min, [dx,dy,dz]_max]   # optional, extents ±1% +0.05mm
  notes: "..."
```

NEGATIVE recipes (names prefixed `neg_`) teach a **structured refusal** instead
— their spec is EXPECTED to fail on one step with a pinned `fm.*` token:

```yaml
expected:
  ok: false
  failing_op: sketch_sweep
  error_contains: fm.sweep_tangent_discontinuity
  notes: "..."
```

(Their spec carries a tiny placeholder `box` first step so the failing spec
still returns a body — `generate_from_spec` has a `body_present`
post-condition.)

## The anti-rot pin

`tests/test_recipes_execute.py` re-executes EVERY file here through
`GenerateFromSpec` and asserts the expected invariants. **When adding a recipe:
author the spec, EXECUTE it, and record the ACTUAL measured volume as the
range (±2%) — never guess numbers.** A recipe whose numbers were not produced
by execution is exactly the fake-accuracy failure mode this corpus exists to
prevent.
