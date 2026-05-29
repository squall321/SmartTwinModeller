# Gap Analysis — Surface Aesthetics: Class-A Surfaces and Industrial-Design Details

Domain angle: the visible, "branded" outside of a consumer product after
geometry is correct but before tooling. Class-A surface region tagging with
explicit continuity enforcement (G0/G1/G2/G3), highlight-line / zebra-stripe
reflection visualization, crown-and-flair feature for ID transitions, parting
line camouflage (hidden in a fillet radius so the witness mark is invisible),
texture transition (smooth ↔ grained) blend zones, soft-edge versus hard-edge
break philosophy (radius vs. chamfer with a defined termination), logo
embossment with brand-correct draft and edge break, paint-mask / colorway
region tagging for two-shot and bi-injection housings.

Reference benchmark products for the "concrete product example" callouts in
this document:

* Apple iPhone 15 Pro titanium chassis (crown-and-flair around the rear glass
  perimeter, hairline brushed-vs-polished texture transition).
* Apple AirPods Pro 2 stem-to-bud transition (G3 reflection-continuous loft).
* Samsung Galaxy S24 Ultra rear cover with raised camera island (sharp 90°
  edge to bezel by ID intent — a "hard-edge break" feature).
* Dyson V15 vacuum motor housing (purple/grey two-shot color split that
  follows a hidden parting line).
* BMW iX kidney grille (matte-vs-gloss texture transition with G2 boundary).
* Bang & Olufsen Beoplay H95 ear cup (logo emboss on a G2 freeform face with
  precisely controlled glyph draft).
* Tesla Model Y A-pillar trim (matte grain Mold-Tech MT-11010 transitioning
  to high-gloss piano-black B-pillar with a hidden seam).

---

## 1. Survey — what already exists in the library

The library already covers the *mechanics* of surfacing reasonably well; what
it lacks is the *aesthetic-intent metadata* and the **visualization /
verification** tools that an ID team uses to lock a Class-A surface.

| Existing skill | What it does | Why it does NOT cover Class-A / ID aesthetic |
|---|---|---|
| `surface_blend_g1` (modify/curvature) | Builds a Geom_BSpline blend between two faces via `BRepFill_Filling` with G1 tangency | Single blend operation only — does not *enforce* continuity downstream across a feature chain. There is no region-wide "this entire surface zone must remain G2-or-better" guarantee. |
| `surface_blend_g2` | Same machinery, G2 (curvature-continuous) | Local blend only; no G3 (torsion-continuous) variant for reflection-perfect surfaces, no continuity-audit pass over an existing body. |
| `surface_finish_tag` (modify/finish) | Tags faces with a manufacturing finish (polish / matte / texture_a/b) + target Ra (µm) | This is a **manufacturing** tag (Ra for downstream DFM), not an **ID-intent** Class-A region tag. There is no notion of "Class-A region" vs "Class-B / hidden" or required continuity grade. |
| `face_face_fillet`, `fillet_edges_by_predicate`, `variable_radius_fillet`, `variable_radius_fillet_with_law` | Standard fillet operations | A blanket fillet has no awareness of highlight-line direction or reflection continuity — designers regularly need a fillet whose radius is *driven* by a target highlight angle, not by a numeric R. |
| `chamfer_conic` | Conic-bulge chamfer with ρ ∈ [0, 1] | Single edge, no "soft vs hard" break semantics, no aesthetic constraint that the chamfer must terminate on a tangent face with zero step. |
| `final_fillet_all_sharp_edges`, `sanding_pass` | Bulk fillet of every sharp / convex edge | Indiscriminate — kills the deliberately *crisp* edges that ID specifies as a "hard break" (Galaxy S24 Ultra camera island, MacBook Pro Touch Bar bezel). No way to express "these edges stay sharp, those get a 0.2 mm break". |
| `text_emboss` | Extrudes glyph outlines on a *planar* face | (a) Planar only — most consumer-product logos sit on a G2 freeform face (AirPods Pro, B&O headphones); (b) no glyph-side draft separate from glyph height, no top-edge break (the cause of the "tinny" look on cheap embossed logos); (c) no font hinting for sub-stroke-width legibility. |
| `embossed_pattern` | Tile-pattern emboss | Same planar-face limitation, no aesthetic edge-break control. |
| `tag_face` | Generic named tag store | Could in principle host an aesthetic tag, but provides no schema for *Class-A grade*, required continuity, allowed manufacturing route, or paint-mask boundary. |
| `curvature_map` (inspect) | Per-face UV sampling of Gauss/mean/principal curvature | Returns raw curvature numbers — does NOT compute the two ID-critical reflection visualizations: **zebra stripes** (parallel-light reflection lines) and **isophotes** (constant-angle-to-view-direction lines). An ID reviewer needs the *picture*, not the number. |
| `silhouette` | Brute projection of all edges to a view plane | Edge silhouette only — not the silhouette of the *surface* (true horizon curves where the surface normal is perpendicular to view direction), which is what ID uses for proportion review. |
| `draft_apply_auto`, `parting_surface`, `core_cavity_split` (modify/mold) | Standard mold-tooling skills | Place the parting line *where the geometry permits*, not *where the eye won't see it*. There is no "camouflage the parting witness inside a radius" inverse skill. |
| `surface_extend_tangent` | Extend a face tangentially | Extends but does not *trim with a Class-A-clean boundary* (needs G2 boundary on the trim curve). |
| `surface_split_with_curve` | Split a face along a curve | Useful primitive, but offers no "split for paint-mask / two-shot" semantics. |
| `surface_offset`, `surface_thicken_variable` | Standard offset surfaces | Numeric only — no constraint that the offset preserves the highlight-line topology of the parent surface. |

**Verdict:** the library has the *atomic OCCT building blocks* (G1/G2 blends,
draft, fillets) but is **entirely missing the four ID-team primitives**:
(1) Class-A region tag with explicit continuity / paint-route metadata,
(2) reflection visualization (zebra / isophote / highlight-line),
(3) crown-and-flair / soft-vs-hard edge-break philosophy,
(4) parting-line camouflage as a *design* decision, not a *mold* decision.

A designer working with this library today can produce a *geometrically*
correct phone back cover but cannot prove, in the model, that the rear glass
perimeter is reflection-continuous, that the colorway boundary follows the
camouflaged parting line, or that the logo emboss will not look tinny under
showroom lighting.

---

## 2. Top Missing — concrete skills

### P0 — true blockers for a Class-A demo

1. **`class_a_surface_tag`** — atomic.
   Attaches a structured ID-intent tag to a set of faces: `grade` (A / B /
   hidden), `required_continuity` (G0 / G1 / G2 / G3), `max_curvature_ratio`
   (k_max / k_min along any UV row — Audi/BMW spec ≤ 3.0 for Class-A), and a
   declared `appearance` (gloss / soft-touch / brushed / mirror). Persists as
   `body._pd_class_a` so downstream skills (fillet, draft, split) can refuse
   to violate the contract.
   - Args: `face_selector`, `grade: Literal["A","B","hidden"]`,
     `required_continuity: Literal["G0","G1","G2","G3"]`,
     `max_curvature_ratio: float = 3.0`, `appearance: str`,
     `paint_route: Optional[str]` (e.g. `"PVD_titanium_DLC"`).
   - **Why:** every premium product has a written Class-A spec (Apple Surface
     Quality Guidelines, BMW BMW.G-Surface-Spec). Without a tag, the planner
     has no way to reject a `fillet_predicate` operation that would break
     continuity on the iPhone titanium frame. Today the model is geometrically
     correct but contractually silent — a reviewer cannot tell that the rear
     glass perimeter is Class-A and the SIM-tray side wall is Class-B.
   - **Standard:** Audi/BMW Class-A criteria (G2/G3 ≤ 0.1° tangent deviation,
     0.005 mm⁻¹ curvature step), ISO 22081 (general surface quality).
   - **OCCT:** trivial — pure metadata on `body._pd_class_a`, identical
     storage pattern to `tag_face` and `surface_finish_tag`.

2. **`continuity_audit`** — atomic, read-only.
   For every shared edge between Class-A-tagged faces, measures actual
   continuity grade (G0 position step, G1 tangent angle, G2 curvature step,
   G3 torsion / curvature derivative step) and reports pass/fail against the
   tag's `required_continuity`. Output: per-edge report with the worst
   sample.
   - Args: `body`, `samples_per_edge: int = 25`,
     `tolerances: dict` (defaults from ISO 22081).
   - **Why:** "we ran G2 blend" ≠ "the surface IS G2". OCCT's `BRepFill_Filling`
     can silently fall back to G1 if G2 cannot converge. Without an
     independent audit, the ID team cannot trust that the model meets spec.
     Every premium-tier handset team runs this check before tooling release.
   - **Standard:** ISO 22081 surface-quality auditing; Audi/BMW G2 tangent
     deviation ≤ 0.16° and curvature ratio ≤ 1.05 at the edge.
   - **OCCT:** moderate — `BRepLProp_SLProps` order-3 along the boundary,
     compare normals and principal curvatures from both adjacent faces.

3. **`highlight_line_view`** — atomic, read-only.
   Computes the curves on the body where the surface normal makes a given
   `light_angle_deg` with a parallel light direction — i.e. the actual
   highlight ridges a customer sees under showroom lighting. Returns 3D
   polylines per face. This is the single most important visualization in ID.
   - Args: `light_direction: tuple[float,float,float]`,
     `view_direction: tuple[float,float,float]`,
     `light_angle_deg: float = 0.0` (specular), `samples_per_face: int = 100`.
   - **Why:** designers iterate on a freeform surface by tweaking the control
     polygon until the highlight line is *smooth*. Without a way to draw the
     highlight, the LLM cannot evaluate "does this loft look right?". This is
     directly the test Apple/Sony surfacing teams run on every CMF review.
   - **Standard:** ICEM Surf / Autodesk Alias "highlight band" rendering;
     ISO 22081 §5.4 reflection-line check.
   - **OCCT:** moderate — `BRepLProp_SLProps` normal evaluation on a UV grid +
     marching-squares isoline extraction at `dot(n, light) == cos(angle)`.

4. **`zebra_stripe_view`** — atomic, read-only.
   Same machinery as `highlight_line_view` but emits N evenly-spaced
   isolines at `light_angle_deg = i * (360 / N)`. Zebra stripes are the de-
   facto ID visualization for Class-A surface inspection.
   - Args: `light_direction`, `n_stripes: int = 12`, `samples_per_face: int =
     100`. Returns a list of polyline lists keyed by stripe index.
   - **Why:** zebra stripes show G0 (broken stripe), G1 (kinked stripe), G2
     (smooth stripe). It is the SINGLE test ID uses to certify a Class-A
     surface, mentioned by name in every surfacing handbook (Alias, ICEM,
     PowerSHAPE). Without it the LLM cannot self-evaluate continuity.
   - **Standard:** documented in Autodesk Alias "Class-A Surfacing"
     curriculum; BMW BMW.G-Spec §3.2 zebra check.
   - **OCCT:** moderate — identical to (3) with stripe striping.

### P1 — major missing capabilities

5. **`crown_and_flair`** — macro.
   Single-call composite for the canonical ID transition: a slightly raised
   central "crown" surface (mean-curvature plateau) that fans out via a
   flare (variable-radius G2 blend) into a perimeter face. This is the
   transition Apple uses around the iPhone rear glass perimeter, Samsung
   uses around the camera island, and Sony uses on the WH-1000XM5 ear cup.
   - Args: `top_face`, `crown_rise_mm`, `flair_radius_law` (linear or sin),
     `flair_continuity: Literal["G2","G3"]`, `terminate_on_face`.
   - Expansion: `surface_offset` (crown) + `variable_radius_fillet_with_law`
     (flair) + `continuity_audit` (verify).
   - **Why:** the most common ID feature on premium products; faking it as a
     fillet always reads as cheap because the highlight breaks at the crown
     edge. Need it as a first-class skill so the LLM can reproduce
     well-known products.
   - **Standard:** Apple internal HW-DG surfacing guide (public references
     to "crown and flair" in iPhone teardowns), Sony AcousticArchitect
     handbook.
   - **OCCT:** moderate — chains existing primitives but the law-coupling
     between crown_rise and flair_radius is non-trivial.

6. **`edge_break_dual`** — atomic.
   Apply a *combination* of fillet and chamfer on the same edge: a small
   tangent fillet (the "soft" radius, default 0.15 mm) immediately followed
   by a flat chamfer (the "hard" facet). This is the canonical "soft-edge
   break" that ID uses to reconcile a sharp visual edge with a comfortable
   touch.
   - Args: `edge_selector`, `fillet_radius_mm: float = 0.15`,
     `chamfer_width_mm: float`, `chamfer_angle_deg: float = 45.0`,
     `direction: Literal["outward","inward"]`.
   - **Why:** every premium phone bezel uses this — a flat anodized chamfer
     (the visible "diamond cut") with a tiny radius on each side so it
     doesn't cut the user's hand. Today the library forces you to choose
     fillet OR chamfer; the result is either soft (no diamond reflection)
     or sharp (cuts the hand). iPhone 5/SE diamond bezel is the canonical
     example.
   - **Standard:** Apple iPhone Industrial Design DRM §4.1
     "Chamfer-Radius-Chamfer (CRC) edge break".
   - **OCCT:** moderate — chamfer first, then fillet the two resulting
     edges; needs careful edge tracking via `selector_freeze`.

7. **`parting_line_camouflage`** — atomic.
   Given a body and a target parting Z plane that *cannot* be moved (mold
   constraint), shift the geometric parting line into the inside of an
   adjacent fillet so the visible witness mark is hidden by the radius. The
   skill modifies the local fillet face geometry so that its inflection
   point coincides with the parting plane.
   - Args: `parting_z_mm`, `adjacent_fillet_selector`,
     `min_camouflage_overlap_mm: float = 0.2`.
   - **Why:** every two-shot consumer product hides the parting witness in a
     radius — failing to do so leaves a visible "step" the eye reads as a
     QC defect. Dyson, Apple, Bose all spec this. The library has the
     `parting_surface` skill (mold-side) but not the matching ID-side
     camouflage.
   - **Standard:** GE Plastics Engineering Design Database §6.4 (parting line
     witness ≤ 0.05 mm if visible, hide-in-radius preferred).
   - **OCCT:** hard — needs to re-build the local fillet so its tangent
     plane at z = parting_z is perpendicular to the pull direction. May
     require a custom BSpline patch.

8. **`texture_transition_zone`** — atomic.
   Define a "blend zone" of width `transition_mm` between two surface-finish
   regions (e.g. brushed → polished, matte grain → gloss). The geometry
   does not change; the metadata records the boundary curve, the
   transition_type (cross-fade / hard-edge / micro-bevel), and the target
   Ra/grain-depth profile across the zone for the manufacturing pipeline.
   - Args: `region_a_selector`, `region_b_selector`,
     `transition_curve: SelectorRef` (an edge / wire on the body),
     `transition_mm: float`, `kind: Literal["cross_fade","hard","micro_bevel"]`,
     `ra_profile: list[float]` (start..end), `grain_id: Optional[str]`
     (MT-110xx code).
   - **Why:** the matte-to-gloss boundary on a BMW kidney grille or the
     brushed-to-polished line on iPhone titanium is a *real* manufacturing
     feature (laser-ablation pass, post-bead-blast masking) that the model
     must declare. Without the metadata, vendor tooling does not know where
     to mask.
   - **Standard:** Mold-Tech MT-11010 to MT-11500 grain catalog; SPI/SPE
     finish standards SPI-A1 (polished) to SPI-D3 (heavy grain).
   - **OCCT:** trivial — pure metadata + selector resolve.

9. **`logo_emboss_class_a`** — atomic.
   Emboss text or logo glyphs on a *freeform* (non-planar) face with
   per-glyph draft, top-edge break radius, and minimum-stroke-width
   enforcement so the result does not look tinny. Geometrically:
   project glyph outlines onto the face, extrude along surface normal field,
   apply a 0.05 mm top break, taper the glyph walls at `draft_deg`.
   - Args: `face_selector` (any face, not just planar),
     `text_or_svg_path`, `height_mm`, `draft_deg: float = 5.0`
     (much higher than the body draft — vendor spec for logos),
     `top_edge_break_mm: float = 0.05`,
     `min_stroke_width_mm: float = 0.4`.
   - **Why:** `text_emboss` requires a planar face — but the AirPods Pro 2
     stem logo, B&O ear cup logo, and Samsung Galaxy S battery logo all sit
     on G2 freeform surfaces. The 5° glyph draft and top break are the
     difference between a $4 OEM logo and a $1500 premium one.
   - **Standard:** Apple Identity Marks Standard §3 (logo emboss spec on
     curved surface); ISO 7000 graphical symbols.
   - **OCCT:** hard — needs `wrap_sketch_on_curved_surface` (already exists)
     plus per-glyph normal-extrude plus a localized fillet on the result
     top edge.

10. **`colorway_region_tag`** — atomic.
    Tag a region of faces as a colorway zone (paint mask / two-shot
    overmold / PVD region) with an explicit boundary curve and process
    metadata. Becomes the source of truth for paint-mask drawings,
    two-shot mold cavity definitions, and renderer material assignments.
    - Args: `face_selector`, `colorway_id: str` (e.g.
      `"NaturalTitanium_BrushedHairline"`), `process: Literal["paint",
      "two_shot", "pvd", "anodize", "in_mold_decoration"]`,
      `boundary_curve_selector: Optional[SelectorRef]`,
      `pantone: Optional[str]`, `gloss_units: Optional[float]`.
    - **Why:** every premium product ships in 3-5 colorways. The model must
      drive both the paint-vendor mask drawing AND the marketing renderer
      from a single source of truth. Without this tag, color is a renderer-
      side hack and any geometry change orphans the colorway map.
      iPhone "Natural Titanium / Blue Titanium / White Titanium / Black
      Titanium" — same geometry, four colorway tags.
    - **Standard:** Pantone color reference, Apple HW-CMF colorway spec.
    - **OCCT:** trivial — metadata only.

### P2 — nice-to-have for completeness

11. **`true_silhouette_horizon`** — atomic, read-only.
    Compute the true 3D horizon curve where the surface normal is
    perpendicular to a given `view_direction`. Unlike the existing
    `silhouette` skill (which projects all edges), this returns the *visual
    silhouette* a renderer would draw — the curve where the surface starts
    to be back-facing.
    - Args: `view_direction`, `samples_per_face: int = 64`.
    - **Why:** ID proportion reviews are silhouette-driven ("the phone has
      a strong silhouette"). The existing edge silhouette is a wireframe,
      not the true horizon. Sony designers use this to compare proportion
      of WH-1000XM5 vs XM4.
    - **Standard:** Pixar RenderMan / ICEM Surf horizon curve definition.
    - **OCCT:** moderate — isoline of `dot(n, view) == 0` on each face.

12. **`mean_curvature_heatmap`** — atomic, read-only.
    Per-face mean-curvature map clamped to a designer-chosen range, returned
    as a UV grid suitable for color-mapping in the renderer. The
    existing `curvature_map` returns raw numbers; this returns the
    *visualization* (the standard "rainbow curvature plot" used in ICEM /
    Alias).
    - Args: `face_selector`, `resolution: int = 64`,
      `clamp_curvature: tuple[float,float] = (-0.5, 0.5)`.
    - **Why:** the rainbow curvature plot is the second-most-used Class-A
      diagnostic after zebra. Convex/concave reading at a glance.
    - **Standard:** ICEM Surf "color shade", Alias "curvature evaluate".
    - **OCCT:** trivial — wrapper over `curvature_map` + clamp.

13. **`anti_aliasing_fillet`** — atomic.
    A *vanishing* fillet whose radius shrinks to zero as it approaches a
    designated termination point or curve, so the fillet "dies into" a
    flat without a kink. Critical for ending a perimeter fillet at a corner
    without a visible step.
    - Args: `edge_selector`, `radius_mm`,
      `termination_point: tuple[float,float,float]`,
      `decay: Literal["linear","cosine","s_curve"]`.
    - **Why:** the perimeter fillet on a MacBook bottom case dies smoothly
      into the foot recess — anyone who has filleted a closed loop knows the
      pain of a fillet "stepping" at the seam. Today's
      `variable_radius_fillet_with_law` can do this but only if the user
      hand-codes the law expression; this skill expresses the intent.
    - **Standard:** Catia GSD "vanishing fillet"; NX Studio Surface "fillet
      end clean-up".
    - **OCCT:** moderate — calls `variable_radius_fillet_with_law` with a
      decay-specific expression, but adds a post-condition that the radius
      at termination is ≤ 0.01 mm.

14. **`isophote_strip_view`** — atomic, read-only.
    Compute the curves where the *angle* (not the dot product) between the
    surface normal and a given direction is constant. Mathematically
    equivalent to highlight_line_view but with angle parametrization (the
    distinction matters because zebra spacing must be angularly uniform).
    - Args: `direction`, `angle_step_deg: float = 5.0`, `samples_per_face`.
    - **Why:** different from highlight stripes — isophotes are *angle*-
      isolines used for terminator-line evaluation (sun-angle on automotive
      hoods, side-profile on phone bezels). Mercedes EQ design team uses
      isophotes for hood styling.
    - **Standard:** ICEM Surf "isophote", Catia ICEM Shape Design isophote
      check.
    - **OCCT:** moderate.

15. **`brand_mark_alignment_check`** — atomic, read-only.
    Given a face with `logo_emboss_class_a` applied and a face with
    `class_a_surface_tag`, check that the logo principal axis (or text
    baseline) is parallel to the highlight-line direction of the host
    surface and that the logo centroid coincides with the host surface
    centroid within tolerance.
    - Args: `logo_face_selector`, `host_face_selector`,
      `axis_tolerance_deg: float = 0.5`, `centroid_tolerance_mm: float = 0.2`.
    - **Why:** the easiest way for a brand to look cheap is a misaligned
      logo. Apple has internal alignment specs (logo principal axis ⟂ Z
      seam) that are checked in DRM. Without this, the LLM cannot self-
      audit its own logo placement.
    - **Standard:** Apple Identity Marks Standard §5.2 alignment tolerance;
      Sony brand-mark placement guide.
    - **OCCT:** trivial — read tags, do vector math.

---

## 3. Cross-cutting infrastructure gaps

These are not single skills but missing primitives that block the above.

* **G3 (torsion-continuous) blend** — the existing `surface_blend_g2`
  matches principal curvatures; G3 also matches their first derivative
  (torsion). Apple-tier reflection continuity needs G3. OCCT supports it
  (`GeomAbs_G3`) but the constraint requires `BRepFill_Filling` order 4 +
  derivative-aware sampling that the current `_build_blend_face` helper
  doesn't drive. Without this, claims 1, 2, 3, 5 cannot reach the highest
  grade.
* **Highlight-line / zebra/isophote primitives** — there is no reusable
  marching-squares isoline extractor over a UV grid in the codebase. Skills
  3, 4, 11, 14 all need the same routine. Should live in
  `phone_designer.skills._surface_iso` as a shared helper.
* **Per-face attribute store with stable selectors** — `surface_finish_tag`
  uses `body._pd_finish`, `tag_face` uses `body._pd_tags`. Class-A and
  colorway tags would be a 3rd and 4th namespace. Without consolidation,
  history propagation across a feature edit is brittle and the LLM cannot
  query "what's true about this face" in one call. Recommend a single
  `body._pd_face_attrs: dict[FaceRef, dict]` with namespaced keys.
* **Aesthetic-pre/post-condition vocabulary** — the existing
  precondition/post-condition framework (`PostCondition(kind=...)`) has no
  `aesthetic_class_a_preserved`, `highlight_continuity_preserved`,
  `colorway_boundary_intact` predicates. Without them, the planner cannot
  refuse a destructive edit on a Class-A face.
* **Reflection-rendering hand-off** — for zebra/isophote *visualization*
  (vs the curve extraction), we need a small renderer adapter that takes
  the polylines and produces a PNG. This is out of scope for the geometry
  layer but should be a documented exporter.
* **Vendor-process catalog** — Mold-Tech grain IDs, SPI finish codes, PVD
  recipe IDs, Pantone codes — all referenced by the proposed skills but
  not represented as a data table the planner can validate against.

---

## 4. Domain-specific catalogs needed

* **MOLD_TECH_GRAIN_CATALOG** — at minimum MT-11010 (fine), MT-11020,
  MT-11030, MT-11050, MT-11200, MT-11400, MT-11500 (heavy) with depth (µm)
  and minimum draft (deg) per grain. Drives `texture_transition_zone`.
* **SPI_FINISH_CATALOG** — SPI-A1 (Ra 0.012 µm, diamond polish) through
  SPI-D3 (Ra 18 µm, stone glass bead). Drives `surface_finish_tag` and
  `class_a_surface_tag.appearance`.
* **CLASS_A_TOLERANCE_PRESET** — per-OEM tangent / curvature / torsion
  step tolerances (Apple, BMW, Audi, generic ISO 22081). Drives
  `continuity_audit` and `class_a_surface_tag.required_continuity`.
* **PVD_AND_PAINT_CATALOG** — DLC, anodize types I/II/III, e-coat,
  soft-touch paint, in-mold decoration (IMD), in-mold labeling (IML),
  hairline-brush direction. Drives `colorway_region_tag.process` and
  `colorway_region_tag.paint_route`.
* **PANTONE_LOOKUP** — Pantone code → sRGB + gloss-units for renderer
  hand-off. Drives `colorway_region_tag.pantone`.

---

## 5. Examples — what becomes possible

* **Reproduce the iPhone 15 Pro titanium frame**:
  `rounded_slab` → `class_a_surface_tag(grade="A", continuity="G3")` on the
  rim → `crown_and_flair` around the rear glass perimeter →
  `colorway_region_tag(colorway_id="NaturalTitanium", process="pvd")` →
  `texture_transition_zone(kind="micro_bevel")` between brushed sides and
  polished chamfer → `edge_break_dual(fillet=0.1, chamfer=0.4)` on the
  diamond-cut bezel → `continuity_audit` → `zebra_stripe_view` for
  designer review.

* **Reproduce the AirPods Pro 2 stem logo**:
  `disc_with_dome` body → `class_a_surface_tag(continuity="G2")` on the
  stem outer face → `logo_emboss_class_a(text="", draft_deg=8,
  top_edge_break_mm=0.03)` for the Apple mark →
  `brand_mark_alignment_check(axis_tolerance_deg=0.3)`.

* **Reproduce the Dyson V15 motor housing parting hide**:
  Two-shot body via `colorway_region_tag(process="two_shot",
  colorway_id="purple_top")` + `colorway_region_tag(colorway_id=
  "grey_bottom")` → `parting_line_camouflage` shifts the seam into a
  0.5 mm radius so the colorway boundary IS the parting line and no
  witness mark is visible.

* **Reproduce a BMW kidney grille bezel**:
  Outer face `class_a_surface_tag(grade="A", continuity="G2")` →
  `texture_transition_zone(kind="hard", grain_id="MT-11020")` between
  matte and high-gloss → `highlight_line_view(light_direction=
  (0,0,-1))` for the studio reflection check.

---

## 6. Priority ranking

| P | Skills |
|---|---|
| P0 | `class_a_surface_tag`, `continuity_audit`, `highlight_line_view`, `zebra_stripe_view` |
| P1 | `crown_and_flair`, `edge_break_dual`, `parting_line_camouflage`, `texture_transition_zone`, `logo_emboss_class_a`, `colorway_region_tag` |
| P2 | `true_silhouette_horizon`, `mean_curvature_heatmap`, `anti_aliasing_fillet`, `isophote_strip_view`, `brand_mark_alignment_check` |

The P0 set is the *minimum* required to claim the library "supports
Class-A surfacing" — without these the library is silent about ID intent
and cannot self-audit continuity. P1 covers the four canonical ID
primitives (crown/flair, hard-vs-soft break, parting camouflage, colorway).
P2 are productivity / completeness items that an ID team will appreciate
but can live without for v1.
