# Modelling from Claude Code — the SEE-WHILE-YOU-BUILD loop

Audience: **Claude Code** (the Anthropic CLI agent) driving this CAD system, and the
human wiring it up. The whole point of this guide is one capability the other clients
don't have: **Claude Code's `Read` tool renders a PNG inline**, so you can *look at what
you just modelled*, notice it's wrong, and fix it — a closed generate → render → SEE →
modify loop, entirely from the terminal.

This works **HEADLESS**. The renderer behind it
(`src/phone_designer/skills/inspect/_render_headless.py`) is a GL-free numpy z-buffer
rasterizer of the OCCT triangulation — **zero GL, zero Qt, zero display**, deterministic
(same body + same view ⇒ byte-identical PNG). So the loop runs in any Claude Code
environment: CI runners, a bare WSL box, an SSH session — anywhere Python + OCCT install,
with no GPU and `PHONE_DESIGNER_UI_HEADLESS=1` set. It is a **clean shaded solid, not a
photo**: a flat steel-blue part on a light-grey background, good enough to read "is the
hole where I meant it" — see the honest notes in §5.

> The full tool/spec contract lives in `docs/MCP_CLIENT_GUIDE.md` (specs, `fm.*`
> refusals, `body_id` lineage, grade labels). This guide is *only* the visual loop on top
> of it. Read that one for anything about composing the spec itself.

---

## 1. The core loop

```
   ┌──────────────────────────────────────────────────────────────┐
   │  1. GENERATE / MODIFY a body          (cad_generate / cad_modify,   │
   │                                         or GenerateFromSpec)         │
   │  2. RENDER it to PNGs in a known dir   (render_views_to_pngs →       │
   │                                         iso / front / top)           │
   │  3. READ the PNG  ← you SEE it         (Claude Code Read tool)       │
   │  4. Judge: right?  → done.  wrong? →   go to 2 after a cad_modify    │
   └──────────────────────────────────────────────────────────────┘
```

The load-bearing step is **3**: you call your own `Read` tool on the PNG path and the
image comes back inline in the conversation. You are now *looking* at the part. A hole in
the wrong corner, a fillet that didn't take, a pocket on the wrong face — all of these are
obvious in the right view and nearly invisible in the volume number alone.

**Two ways to render**, pick by what's connected:

| You have…                     | Render with…                                    | Headless? |
|-------------------------------|-------------------------------------------------|-----------|
| the MCP server connected      | `cad_preview` (§2)                              | see note ↓ |
| just the venv / a shell       | `render_views_to_pngs` via `python -c` (§3)     | **always** |

> **Headless honesty (important).** The MCP `cad_preview` tool currently routes through
> the older GL-based `build_views`, so **on a no-GL box it returns an honest
> `skipped_no_gl` marker and no images** — that is "cannot see here", not a failure. The
> **GL-free** `render_views_to_pngs` path in §3 is the one that *always* produces real
> PNGs headless. So: on a machine with GL, `cad_preview` is the one-call convenience; on a
> headless box, use the `python -c` one-liner. Both feed the same `Read`-the-PNG step.

The renderer's public API (do not rewrite it — it's built and verified):

```python
from phone_designer.skills.inspect._render_headless import (
    render_view,            # (shape, view='iso', size=640) -> (uint8 HxWx3, info)
    render_views_to_pngs,   # (shape, out_dir, views=('iso','front','top'),
)                           #  size=640, stem='preview') -> {images, skipped, renderer, note}
```

Views: `iso`, `front`, `back`, `right`, `left`, `top`, `bottom`. `render_views_to_pngs`
writes `<out_dir>/<stem>_<view>.png` and **never fakes an image** — a view it can't
produce comes back as `None` with a reason in `note`, never a black PNG.

---

## 2. Walkthrough A — via the MCP server (`cad_generate` → `cad_preview` → SEE → `cad_modify`)

Use this when the `phone-designer-cad` MCP server is connected AND you're on a box with
GL. Every tool returns strict JSON; check `status`/`is_solid` per the client guide.

**Step 1 — generate a body from a spec.** (A 30×30×8 plate — no hole yet.)

```jsonc
// tool: cad_generate
{ "spec": [
    { "op": "box", "args": { "length_mm": 30, "width_mm": 30, "height_mm": 8 } }
  ],
  "name": "seat_plate" }
// → { "ok": true, "status": "ok", "body_id": "body_3fa8c21d",
//     "is_solid": true, "volume_mm3": 7200.0, "bbox_mm": [30,30,8],
//     "files": { "step": ".../seat_plate.step" },
//     "resource_uris": ["file:///.../seat_plate.step"] }
```

Keep the `body_id` — everything below hangs off it.

**Step 2 — preview it.** `cad_preview` renders into `<workspace>/previews/` and returns
the PNG paths + `resource_uris`.

```jsonc
// tool: cad_preview
{ "body_id": "body_3fa8c21d", "views": ["iso", "front", "top"] }
// GL box  → { "ok": true, "skipped": false,
//             "images": { "iso": ".../previews/iso.png",
//                         "front": ".../previews/front.png",
//                         "top": ".../previews/top.png" },
//             "resource_uris": ["file:///.../previews/iso.png", ...] }
// headless → { "ok": true, "skipped": true,
//              "images": { "iso": null, "front": null, "top": null },
//              "note": "skipped_no_gl: ..." }   ← use §3 instead
```

**Step 3 — SEE it.** Take a path from `images` and call your **`Read` tool** on it:

```
Read  .../previews/top.png      ← the image renders inline in the conversation
```

Now you are *looking* at the top view. It's a plain blue square — correct, no hole yet.

**Step 4 — modify, then re-preview.** Add the hole with `cad_modify` (mints a NEW
`body_id`; the input body is never mutated). Note `hole` takes a **WORLD** position on the
entry face and `direction: "-Z"` drills down; omitting `depth_mm` = through.

```jsonc
// tool: cad_modify
{ "body_id": "body_3fa8c21d",
  "spec": [
    { "op": "hole", "args": { "position": [0, 0, 8], "diameter_mm": 6, "direction": "-Z" } }
  ] }
// → { "ok": true, "body_id": "body_91b02e77", "parent_body_id": "body_3fa8c21d",
//     "is_solid": true, "volume_mm3": 6973.8, ... }
```

**Step 5 — re-preview the CHILD and SEE it again.**

```jsonc
// tool: cad_preview  { "body_id": "body_91b02e77", "views": ["top"] }
```
```
Read  .../previews/top.png
```

The top view now shows a **centered white disc** — the through-hole. If instead the disc
sat in a corner, or there were two, you'd catch it here by *looking*, not by parsing a
volume delta. Wrong? `cad_undo` back to the parent (`cad_undo {"body_id":"body_91b02e77"}`
→ returns `body_3fa8c21d`) and try a different `position`.

---

## 3. Walkthrough B — via the CLI / a `python -c` one-liner (works headless, no MCP)

When the MCP server isn't connected, or you're on a no-GL box, drive the engine directly:
`GenerateFromSpec` builds the body, `render_views_to_pngs` writes the PNGs. **This is the
path that always produces real images headless.** Copy-paste and adapt.

> Env: venv python is `d:/SmartTwinModeller/venv/Scripts/python.exe`; set
> `PHONE_DESIGNER_UI_HEADLESS=1`. On Windows use the Bash tool for this heredoc form (Git
> Bash), or paste the `-c` string into PowerShell.

**One-liner — build a plate with a centered through-hole and write iso/front/top PNGs:**

```bash
PHONE_DESIGNER_UI_HEADLESS=1 d:/SmartTwinModeller/venv/Scripts/python.exe -c "
from phone_designer.plan.executor import _import_all_skills; _import_all_skills()
from phone_designer.skills.create.generate_from_spec import GenerateFromSpec
from phone_designer.skills.inspect._render_headless import render_views_to_pngs
spec = [
  {'op':'box',  'args':{'length_mm':30,'width_mm':30,'height_mm':8}},
  {'op':'hole', 'args':{'position':[0,0,8],'diameter_mm':6,'direction':'-Z'}},
]
body = GenerateFromSpec().apply(None, {'spec': spec}).body
out  = 'D:/tmp/cc_preview'                       # <-- your known dir
r = render_views_to_pngs(body, out, views=('iso','front','top'))
import json; print(json.dumps(r['images']))
"
```

Prints the exact paths, e.g.:

```json
{"iso": "D:/tmp/cc_preview/preview_iso.png",
 "front": "D:/tmp/cc_preview/preview_front.png",
 "top": "D:/tmp/cc_preview/preview_top.png"}
```

**Then SEE it** — call your `Read` tool on `D:/tmp/cc_preview/preview_top.png`. The image
renders inline. That is the entire loop with no server in the picture.

**This is verified.** Running exactly this (spec above) produced three real PNGs; the
`top` view showed a single centered white disc on a blue square (the hole), and the `iso`
view showed the shaded plate with the bore — both correct. The renderer reported
`{"skipped": false, "renderer": "headless_raster", "note": "GL-free numpy z-buffer render"}`.

**Filter OCCT noise** when you run these (the engine logs Transfer/Statistics chatter to
stderr): append
```bash
2>&1 | grep -vE 'WorkSession|Transfer|Statistics|INFO|class |Deprecation|UserWarning'
```

**Modify in the same one-liner** — since there's no session, just append the next steps to
`spec` and re-run, re-rendering to the same dir (paths are stable ⇒ you overwrite and
re-`Read` the same filename). Or start from a proven recipe: `recipes/*.yaml` each carry a
ready `spec:` block and a MEASURED `expected.volume_mm3` range — copy the `spec`, adapt the
numbers, keep the idiom. Good starters: `hole_through_center.yaml`,
`rounded_slab_case.yaml`, `counterbore_m3_center.yaml`, `gear_external_involute.yaml`.

**Single view, in-memory (no file), when you only need a quick pixel check:**

```python
from phone_designer.skills.inspect._render_headless import render_view
arr, info = render_view(body, "top", size=640)   # arr: uint8 640x640x3; info: {view,n_triangles,empty}
```
`info["empty"]` true = no mesh (bad/empty body) — an honest signal, not a crash.

---

## 4. The iterate-visually pattern — catch a wrong result by LOOKING

The discipline that makes this pay off:

1. **Preview after EVERY modify**, not just at the end. One render per feature means when
   something breaks you know exactly which step did it.
2. **Pick the view that would expose the mistake.** Hole placement → `top`. Wall
   thickness / a pocket depth → `front`. Overall proportion / a missed fillet → `iso`.
   A defect invisible in one view is glaring in another; render the pair that matters.
3. **Compare against intent, not against nothing.** "I asked for a hole *dead center* —
   is the disc centered?" The volume changed either way; only the *picture* tells you it
   moved to the right place.
4. **Cross-check the measured number too.** `volume_mm3`/`bbox_mm` (on the generate/modify
   result) or `cad_measure` are cheap and catch stale-`body_id` bugs on your side. The
   image tells you *shape*; the number tells you *magnitude*. Use both.

Classic catch: you meant to drill through the center but typed `position: [10, 0, 8]`.
Volume is nearly identical, `is_solid` is true — the spec "passed". But the `top` view
shows the disc shoved to the right edge, and you fix it in seconds because you *looked*.

---

## 5. Honest notes — what the render is and isn't

- **Shaded solid, not photoreal.** Flat steel-blue part, Lambert-shaded from the view
  direction, light-grey background. It answers "did the modelling do what I meant"; it is
  not a marketing render. No materials, textures, lighting rig, or edges/wireframe.
- **No dimensions are drawn.** These previews have no numbers, callouts, or scale on them.
  When you need a **dimensioned** view (title block, dimension table, hole table, hidden
  lines), use the drawing sheet instead: MCP `cad_drawing {"body_id": ...}`, which emits an
  HTML sheet + per-view DXF and is labeled **DRAFT FOR REVIEW** (`grade='draft'`, no
  automatic leader placement). Don't try to read a dimension off a preview PNG.
- **Deterministic.** Same body + same view ⇒ byte-identical PNG. Safe to cache; safe in
  CI; two runs won't "look different".
- **Grade labels still apply.** The preview shows *geometry* only. Any cost / process /
  DFM / variant number you surface alongside it is still `grade='estimate'` (a heuristic
  model, not a quote), and `cad_drawing` output is `grade='draft'`. Repeat those labels to
  the human — a pretty PNG does not upgrade an estimate into a quote.
- **A failure is `None` + a reason, never a fake PNG.** If a view can't be produced
  (empty/degenerate body, an OCCT meshing fault), that view is `None` and the reason is in
  `note`. `render_view` on a void body returns a plain background with `info["empty"]=True`
  and `n_triangles=0`. There is deliberately **no black/blank placeholder** to mistake for
  a real result — if you didn't get a path, you don't have a picture, and the `note` says
  why.

---

## 6. Copy-paste cheat sheet

```bash
# HEADLESS render (always works) — build a body, write iso/front/top, print paths:
PHONE_DESIGNER_UI_HEADLESS=1 d:/SmartTwinModeller/venv/Scripts/python.exe -c "
from phone_designer.plan.executor import _import_all_skills; _import_all_skills()
from phone_designer.skills.create.generate_from_spec import GenerateFromSpec
from phone_designer.skills.inspect._render_headless import render_views_to_pngs
body = GenerateFromSpec().apply(None, {'spec': [
  {'op':'box','args':{'length_mm':30,'width_mm':30,'height_mm':8}},
]}).body
import json; print(json.dumps(render_views_to_pngs(body,'D:/tmp/cc_preview')['images']))
" 2>&1 | grep -vE 'WorkSession|Transfer|Statistics|INFO|class |Deprecation|UserWarning'
```
```
Read  D:/tmp/cc_preview/preview_iso.png     # ← SEE it inline, then iterate
```

MCP equivalent (GL box): `cad_generate` → `cad_preview` → `Read` the `images.*` path →
`cad_modify` → `cad_preview` again → `Read`. Views available everywhere:
`iso · front · back · right · left · top · bottom`.
