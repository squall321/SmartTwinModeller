<!-- Web CAD viewer plan — 2026-07. 4-agent scout (web-kernel options / our assets /
integration loop) + architect synthesis. KEYSTONE PROVEN: gltf_export emits per-face
GLB primitives (box+2holes: 8 OCCT faces = 8 primitives), so per-face picking works
WITHOUT a browser WASM kernel. Architecture: Python-OCCT tessellation server + thin
three.js front-end; Claude stays the modelling brain, user gets 3D rotate+pick. -->

The scouts' claims check out against the actual code. I have enough grounding to write the plan.

# SmartTwinModeller 웹 CAD 뷰어 — 단계별 빌드 플랜

## 0. 결론 먼저 (아키텍처 선택)

**선택: (b) Python-OCCT tessellation-server + thin three.js front-end.** WASM(opencascade.js) 반려.

**한 줄 이유:** 우리는 이미 서버에 OCCT 7.8(cadquery-ocp)과 425개 스킬·selector·body_id 세션 캐시를 매일 돌리고 있다 — 브라우저에 9MB WASM 커널을 넣으면 *같은 커널을 두 번* 유지하게 되고, 그 커널은 우리 Python 스킬을 한 개도 재사용하지 못한다. 검증된 근거: `gltf_export.py`를 box+2holes(OCCT face 8개)에 돌리면 `RWGltf_CafWriter`가 정확히 **face당 1 primitive = 8 primitive**를 뱉는다 → three.js raycaster가 hit한 primitive group을 그대로 돌려주므로 **per-face 픽킹이 이미 잠재적으로 존재**한다. WASM은 "서버 왕복 없는 오프라인/제로-레이턴시 편집"이 필요할 때만 V4+에서 재검토.

핵심 원칙 3가지 (스카우트 3렌즈 합의):
- **Claude가 모델링 브레인으로 남는다.** 뷰어는 rotate + pick + 선택 컨텍스트 표시만. fillet/chamfer 버튼을 뷰어에 넣지 않는다 (그러면 spec-composition 로직이 Claude와 뷰어로 분기됨 — 프로젝트 테제 위반).
- **transport bridge는 별도 프로세스.** stdio MCP(`mcp_server.py`)를 건드리지 않는다. Claude Code가 그 stdio 파이프를 소유하고 있어 브라우저가 공유 불가.
- **durable key는 정수 index가 아니라 centroid.** `_all_faces` TopExp index는 한 body 안에서만 안정적이고 cad_modify의 STEP 왕복에서 사라진다(`mcp_server.py:509-510` 명시). 픽을 좌표 기반 selector로 번역해야 편집이 살아남는다.

---

## 1. DATA CONTRACT — Python side가 뱉는 것

두 개의 새 산출물. **기존 `gltf_export.py`(AR/review용 GLB)는 byte-identical 유지, 절대 변경하지 않는다.** 새 경로는 sibling으로 추가.

### 1a. `tessellate_faces()` 헬퍼 (신규, ~15줄, `_render_headless.py`에서 추출)
`_render_headless._triangles`(lines 57-76)는 이미 필요한 walk를 다 한다: `BRepMesh_IncrementalMesh` → per-face `TopExp_Explorer(FACE)` → `BRep_Tool.Triangulation_s` → world verts + REVERSED winding 보정(line 69-74). 현재는 모든 face를 하나의 `verts`/`tris` soup으로 **flatten**해서 face 경계를 딱 한 줄(`base=len(verts)`)에서 버린다. face 경계는 loop 레벨에 살아있다.

추출 시그니처:
```
tessellate_faces(shape, deflection) -> list[{
    id: int,          # == _all_faces TopExp index (in-session 정수 id)
    verts: [[x,y,z]…], tris: [[a,b,c]…],
    normal: [nx,ny,nz],       # _resolvers._face_normal_at_center 재사용
    area: float,              # _resolvers._face_area
    centroid: [cx,cy,cz],     # _resolvers._face_center — DURABLE selector payload
    surface_type: str         # plane/cylinder/... (selector_preview가 이미 계산)
}]
```
순수 OCCT + numpy, GL-free, deterministic. 좌표는 **native mm / +Z-up** (gltf의 mm→m·−90°X 회전 안 함). GLB 태깅 경로와 뷰어 JSON이 이 헬퍼를 공유 → face index가 양쪽에서 동일.

### 1b. GLB per-face primitive 태깅 (신규 `gltf_export_tagged` 또는 `group_by_face` flag)
`gltf_export.py`는 현재 `shape_tool.AddShape(shape_y_up, False, True)`로 solid 전체를 하나 넣는다 (SetColor/SetShapeName/AddSubShape 호출 zero — grep 확인). RWGltf_CafWriter는 **face를 labeled sub-shape로 추가하거나 color를 주면** face당 1 primitive로 split한다. 그래서 확장은 기계적:
- AddShape 전에 `_all_faces`와 **동일 순서**로 `TopExp_Explorer(FACE)` 돌며 face당 `XCAFDoc_ColorTool.SetColor` (또는 `AddSubShape`+`SetShapeName='face_{i}'`).
- 각 primitive `extras`에 `{face_id, centroid, normal, area}` 주입.
- edge 픽킹용: face당 별도 `LINES` primitive에 `extras.edge_id` (TopExp edge 순서). **glTF는 삼각형만 저장하므로 edge는 GLB에서 완전 소실** — LINES를 명시적으로 넣어야 "이 edge에 fillet"이 가능.
- top-level `extras`: `{units:'mm', up_axis:'+Z', model_transform:<display 회전>}` → 프론트가 똑바로 보여주되 픽은 model 좌표로 리포트.

### 1c. `brep_faces` 데이터 shape 차용 (구현 아님, 계약만)
occt-import-js의 `brep_faces`(per-face triangle range first/last + color)가 검증된 계약이다. 우리가 직접 삼각형 소프에 `face_id` range를 실으면 raycast triangle index → binary-search → face_id로 primitive split 없이도 픽 가능. **b2(우리 Python이 per-face+per-edge glTF 방출)를 선호** — fillet/chamfer가 edge 픽에 크게 의존하므로.

---

## 2. SELECTION → cad_modify 매핑

**단 하나의 새 커널 primitive가 keystone이다: `faces_near_point` selector.**

현재 `_selectors.py:32-48`의 SelectorKind에는 `face_by_index`도 `face_at_point`도 **없다**. 가장 가까운 것들:
- `faces_by_normal` — 동일 평면 face를 **전부** 매치 (구멍 하나엔 틀림).
- `tagged` — cad_modify STEP 왕복에서 소실.

**유일하게 존재하는 centroid 매칭**은 `_resolvers._resolve_tagged_faces`(lines 221-259): tag를 **nearest bbox_center**로 25.0mm² 임계값 내에서 resolve. 이게 정확히 face-pick이 필요로 하는 알고리즘.

추가할 것 (~30줄 총합, 스킬은 하나도 안 건드림):
```
_selectors.py:  FacesNearPointSelector {kind:'faces_near_point', point:[x,y,z], tol_mm}
                (stability_rank ~1, edges_by_position급 — live pick엔 정확히 맞음)
_resolvers.py:  resolve_faces에 clause 추가 — _all_faces + _face_center 로
                point에 centroid 최근접 face 반환 (_resolve_tagged_faces 루프 재사용)
```
`_KIND_TO_CLASS` + fillet/chamfer/pocket의 `selector_kinds`에 등록.

**픽 → spec 파이프라인:**
```
picked triangle → 그 face의 centroid (1a JSON에서) 
  → SelectorRef {kind:'faces_near_point', point:centroid, tol_mm:1}
```
edge fillet은 조합: `{kind:'edges_on_face', face:{kind:'faces_near_point', point:[…]}}` — `edges_on_face`는 `_resolvers.py:139-152`에 이미 구현됨. 이렇게 하면 selector-taking 스킬 425개가 **스킬 변경 0으로** 전부 pickable.

**Dual-id 규약:** (1) `_all_faces` 정수 index = in-session hover/highlight용 (싸고 정확), (2) centroid = cad_modify로 보내는 durable payload. 정수 index를 **편집 selector로 노출하지 않는다** — modify 후 조용히 오타겟됨. 뷰어는 매 cad_modify 후 body_id가 바뀌므로 per-face JSON을 재-fetch.

---

## 3. TRANSPORT BRIDGE (stdio MCP → HTTP/WS)

**별도 프로세스 `viewer_server.py`** (FastAPI + WebSocket). `mcp_server.py`의 23개 cad_* 툴은 손대지 않는다.

- `mcp_server.py`는 FastMCP over stdio (`mcp.run()`, `.mcp.json`이 `python -m phone_designer.mcp_server`로 런치). grep 결과 src 전체에 fastapi/websocket/uvicorn/flask **0 hits**, .ts/.js/.html **0개**.
- 브라우저는 stdio를 못 하고 Claude Code의 MCP 프로세스도 공유 불가.

`viewer_server.py`가 import하는 것 (전부 재사용): `_session_tools`의 import_body/modify_body/measure_body/preview_body (line 4-6: "no store, no FastMCP imports" 설계), `_body_store`의 BodyStore, `gltf_export` 스킬.

**커플링 = 공유 workspace (`PHONE_DESIGNER_MCP_WORKSPACE`).** MCP 툴이 STEP + body_id를 디스크에 씀 → 뷰어가 body_id의 STEP을 로드 → GLB. 공유 메모리 불필요.

엔드포인트:
- `GET /model/{body_id}.glb` — body_id의 STEP 로드 → tessellate → 스트림. **body는 immutable이므로 body_id로 cache-forever 안전.**
- `WS /session` — `{event:'body', body_id:'Y', op_note:'fillet 3mm', volume_mm3:…}` 푸시.
- `POST /pick {body_id, point:[x,y,z]}` — `faces_near_point` + `selector_preview`로 `{face_idx, centroid, normal, surface_type}` 반환 + workspace의 shared file에 "current selection" stash.

Claude 핸드오프: MCP에 `cad_get_selection()` 툴 하나 추가(~20줄) — bridge가 stash한 파일을 읽음. 총 ~150-250줄, HTTP 라우팅 + GLB 스트리머만 신규. optional dependency extra로 게이팅 → 코어 stdio MCP는 lean 유지.

---

## 4. 단계별 플랜

### V1 — Rotate + View (지금 당장 출하 가능)
사용자가 진짜 3D로 회전, 실제 CAD-capable 웹 뷰어에서 GLB를 봄.

- **Deliverables:**
  - `cad_export`에 `'glb'` 포맷 추가 (`mcp_server.py:333-363`은 현재 step/stl/py만 whitelist; trimesh+pygltflib는 pyproject:34-35에 **이미 있음**). `GltfExport().apply` 호출 — 사실상 2줄.
  - `viewer_server.py`: `GET /model/{body_id}.glb` (tessellate + 스트림).
  - static `index.html` + ~200줄 three.js: GLB 로드, OrbitControls(회전), auto-fit.
- **재사용 vs 신규:** 재사용 = gltf_export(검증됨, RWGltf), BodyStore, _session_tools import. 신규 = FastAPI GLB 엔드포인트 + html.
- **📌 Pin-able proof:** `cad_export(body_id, ['glb'])` → 나온 GLB를 뷰어 URL로 열어 마우스로 회전하는 스크린샷. (헤드리스 증명: `gltf_export.py`를 box body에 돌려 "GLB meshes:1 primitives:8" == "OCCT faces:8" 재현.)
- **정직한 노력:** 낮음. 데이터 경로가 이미 존재. 진짜 신규는 웹 transport뿐.

### V2 — Click-a-face → Claude가 fillet
사용자가 face를 클릭 → 배지가 "face@[x,y,z], plane" 표시 → 사용자가 Claude에게 "저 face 3mm fillet" → 뷰어 새 body_id로 refresh.

- **Deliverables:**
  - **`faces_near_point` selector + resolver** (§2, ~30줄). **이것이 첫 워크아이템 — keystone.**
  - `POST /pick` (faces_near_point + selector_preview로 단일 face 확인 + stash).
  - GLB per-face primitive 태깅 (`extras.face_id`+centroid, §1b) 또는 V1-path-A: 브라우저가 raycast 3D point만 리포트하고 서버가 faces_near_point로 resolve (**gltf_export를 전혀 안 건드림 — path A로 시작**).
  - three.js raycaster on click → POST /pick → 매치된 face highlight + 배지.
  - `cad_get_selection()` MCP 툴 (~20줄).
  - `WS /session` refresh: cad_modify → 새 body_id → WS ping → 뷰어가 새 GLB 로드. Undo = "parent body_id 보여줘"(cad_undo가 parent 반환, immutable lineage).
- **Flow:** 클릭 → 배지 → "fillet that edge 3mm" → Claude가 `cad_get_selection` → `cad_modify {body_id, spec:[{op:fillet_edges_by_predicate, args:{selector:{kind:edges_on_face, face:{kind:faces_near_point, point:[centroid], max_dist_mm:1}}, radius_mm:3}}]}` → 새 body_id → WS ping → refresh. Claude는 preflight/self-correct/verify-volume를 이미 아는 대로 수행. 뷰어는 cad_*를 직접 호출하지 않음.
- **재사용 vs 신규:** 재사용 = selector_preview(pick resolution), cad_modify, fillet_edges_by_predicate, _resolve_tagged_faces 최근접 루프. 신규 = selector 1종 + /pick + /session + cad_get_selection + three.js raycast.
- **📌 Pin-able proof:** 헤드리스로 `faces_near_point` + `selector_preview`가 box body에서 **정확히 face 1개**를 resolve함을 증명(무거운 corpus/pytest 불필요 — probe 하나면 왕복 증명). 그다음 뷰어에서 클릭→Claude fillet→refresh 스크린샷 3장(before/pick badge/after).
- **정직한 노력:** 중간. keystone selector가 리스크지만 30줄이고 기존 최근접-centroid 알고리즘 복사.

### V3 — Full pick / section / measure
edge 픽(fillet/chamfer THIS edge), 단면, 측정, feature 오버레이.

- **Deliverables:**
  - per-edge `LINES` primitive + `extras.edge_id` (§1b) + `edges_near_point` selector → **edge-driven 편집** (우리 스킬셋이 heavily 사용).
  - `cad_scene(body_id)` MCP 툴: `{faces:[{id,verts,tris,normal,centroid,area}], features: extract_feature_catalog, bbox: initial_bbox_mm}` 반환. 뷰어가 `feature_catalog[*].face_indices`(extract_feature_catalog.py:119-135) → primitive 매핑으로 hole/pocket/boss face를 **geometry 추가 작업 0으로** highlight.
  - SECTION = body_id에 section op → fresh GLB. MEASURE = `cad_measure`(mass/obb/dimensions, mcp_server.py:572-584 이미 존재). Ortho edge 오버레이 = `drawing_sheet` HLR visible/hidden DXF 재사용(client HLR 재실행 불필요).
  - lineage 히스토리 UI: 각 body_id의 iso PNG(cad_preview가 workspace/previews에 이미 생성)를 클릭 가능 썸네일로 → 시각적 undo/redo 공짜.
- **재사용 vs 신규:** 재사용 = extract_feature_catalog, cad_measure, cross_section/HLR, drawing_sheet, cad_preview PNG. 신규 = edges_near_point, cad_scene 툴, section→GLB 엔드포인트, feature 사이드패널 UI.
- **📌 Pin-able proof:** feature 사이드패널에서 detected hole 클릭 → 해당 face가 highlight되는 스크린샷 + section 슬라이더가 fresh GLB를 스트림하는 데모.
- **정직한 노력:** 중간-높음. CAD 인텔리전스가 전부 서버에 이미 있음 — 진짜 신규는 UI 오버레이 + edge selector.

**WASM(opencascade.js)이 V1-V3에 불필요한 이유:** 브라우저는 GLB raycast로 3D point를 얻고 highlight만 그리면 됨(thin three.js). face/edge PICK = faces_near_point + selector_preview(서버), SECTION = server section op → GLB, MEASURE = cad_measure — 전부 이미 소유한 Python OCCT에서 on-demand 제공. WASM은 Python 스킬 425개를 재사용 못 하고 geometry 로직을 브라우저에 복제. round-trip 레이턴시가 병목이 될 때만 V4+에서 재검토.

---

## 5. 단 하나의 다음 액션 (V1 시작)

**`cad_export`의 accepted formats에 `'glb'`를 추가하고 (`mcp_server.py:333-363`, `GltfExport().apply` 호출), box body 하나에 `cad_export(body_id, ['glb'])`를 돌려 GLB가 face당 1 primitive(8 faces → 8 primitives)로 나오는지 헤드리스로 확인한다.** 이 한 probe가 전체 데이터 경로(OCCT → GLB → per-face 픽 latent)를 증명하고, 그 GLB를 즉시 three.js `index.html`에 드롭해 rotate 뷰어(V1)를 세운다. selector·bridge·html 외의 모든 조각은 이미 존재한다.

---

### 참조 파일 (절대경로)
- `D:\SmartTwinModeller\src\phone_designer\skills\io\gltf_export.py` (GLB writer, 태깅 확장 지점 :169-184)
- `D:\SmartTwinModeller\src\phone_designer\skills\inspect\_render_headless.py` (`_triangles` per-face walk :57-76 → `tessellate_faces` 추출원)
- `D:\SmartTwinModeller\src\phone_designer\skills\_selectors.py` (`faces_near_point` 추가 지점 :32-48)
- `D:\SmartTwinModeller\src\phone_designer\skills\_resolvers.py` (`_resolve_tagged_faces` 최근접-centroid :221-259, `_face_center` :69-73, `edges_on_face` :139-152, `_all_faces` :45-56)
- `D:\SmartTwinModeller\src\phone_designer\mcp_server.py` (cad_export formats :333-363, cad_modify :504-546, STEP 왕복 tag 소실 :509-510/:57-59, cad_undo lineage :549-569)
- `D:\SmartTwinModeller\src\phone_designer\skills\reverse_engineer\extract_feature_catalog.py` (face_indices :119-135, initial_bbox_mm :1198-1220)
- 신규 생성 대상: `viewer_server.py`, `static/index.html`, `tessellate_faces` 헬퍼, `faces_near_point`/`edges_near_point` selector, `cad_get_selection`/`cad_scene` MCP 툴