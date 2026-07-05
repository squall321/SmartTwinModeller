<!-- Viewer hardening + feature backlog, 2026-07. 4-agent adversarial review + synth. -->

All claims verified against source. The critical chain is confirmed: (1) `cad_modify` does `_STORE.get(body_id)` at line 522 → raises `fm.unknown_body_id` for a viewer stem; (2) `cad_get_selection` returns the stash verbatim with the stem as `body_id`; (3) coordinate frame bug is real (GLB in metres/Y-up, pick_face compares against mm/Z-up centroids); (4) `.mcp.json` has no `PHONE_DESIGNER_MCP_WORKSPACE`; (5) all the reuse skills (split_body, cross_section, measure, extract_feature_catalog, edge_polyline_extract) exist.

One important cross-cutting realization: findings #1 (coordinate frame) and the four "nearest-centroid" findings share ONE root fix — the browser already knows the exact hit primitive (`hits[0].object`) and GLB emits one primitive per OCCT face in `_all_faces` order. Switching to **face-primitive-index picking** simultaneously kills the coordinate-frame bug, the centroid-mispick bug, the curved/concentric ambiguity, the tol divergence, AND removes the per-click STEP re-import cost. That is the single highest-leverage fix. I have everything I need to synthesize.

---

# SmartTwinModeller 웹 뷰어 — 첫 실사용 전 우선순위 백로그

**핵심 통찰 (전체를 관통하는 dedup):** 리뷰어 4명이 서로 다른 렌즈로 보고한 **5개의 "pick이 엉뚱한 면을 고른다" 버그**(coordinate-frame, nearest-centroid, curved/concentric, tol divergence, elongated-face)는 **단일 근본 원인**이다. 브라우저는 이미 정확한 primitive(`hits[0].object`)를 알고 있고, GLB는 `_all_faces()` 순서대로 **OCCT face 1개당 primitive 1개**를 emit한다 (코드에서 확인: gltf_export는 단일 `AddShape`, three.js GLTFLoader가 primitive마다 child Mesh 생성). **face-primitive-index로 pick을 바꾸는 단 하나의 수정(F1)이 이 5개 버그를 동시에 죽이고, per-click STEP re-import 비용까지 제거한다.** 이것이 다른 무엇보다 먼저다.

---

## 1. MUST-FIX — 첫 세션이 깨지거나 사람을 오도하는 것

### F1. Pick을 centroid가 아니라 face-primitive-index로 (5개 critical 버그를 1개 수정으로) — effort **S**
현재 pick loop는 **end-to-end로 완전히 깨져 있다.** 원인 두 겹이 곱해진다:
- **좌표계 (gltf_export.py:172-175 확인):** `SetLengthUnit_s(doc,0.001)`가 mm→m로 스케일하고 `shape.Moved(-90° about +X)`가 Y-up으로 회전 → GLB node quaternion `[-0.707,0,0,0.707]`, vertex는 metres. 브라우저는 이 world point(metres, Y-up)를 `index.html:172`에서 그대로 POST하는데, `pick_face`는 `_face_center()`의 **OCCT mm/Z-up centroid**와 비교(viewer_server.py:61-67). 1000배 스케일 + 90° 회전 차이로 매칭이 쓰레기.
- **centroid 매칭 자체:** 좌표계를 고쳐도, 큰/elongated/concentric 면에서 클릭점은 centroid 근처가 아니므로 이웃 면이 이긴다. hollow tube의 내벽/외벽은 centroid가 같아(0,0,0) tie-break이 pick_face와 resolve_faces에서 갈려 **리포트한 면과 selector가 재해석하는 면이 다르다** (self-consistent하게 틀린 면이 lock-in됨 — 최악).

**Fix:** `index.html`에서 `loadBody`가 traverse 순서로 face mesh를 `faceMeshes[]` 배열에 수집 → pick 시 `faceIdx = faceMeshes.indexOf(hits[0].object)` → `POST {body_id, face_idx}`. 서버는 `pick_face`에 by-index 경로 추가: `faces[face_idx]` 직접 사용, centroid 탐색 skip. point는 fallback으로만 유지. **좌표계 변환도 centroid 탐색도 불필요해진다.** 순서 매핑을 신뢰하기 전에 probe 1회로 검증 (7 faces = 7 primitives 리포트에서 확인됨).
**Pin proof:** `pick_face`에 (40×20×10 box, top face를 face_idx로) 넣어 idx가 정확히 top을 반환. 회귀 테스트로 진짜 GLB-space point 경로를 태우는 케이스 추가 (기존 `test_pick_face_resolves_and_stashes`는 OCCT-mm point를 직접 먹여 false confidence를 줌 — 이 gap을 메움).

### F2. Viewer stem ↔ MCP body_id 네임스페이스 단절 — '클릭한 면 필렛' 왕복 자체가 불가능 — effort **M**
`cad_generate`는 `body_<8hex>`를 mint(_body_store.py:164)하고 STEP은 `<name>.step`로 씀(mcp_server.py:193). 뷰어는 STEP **stem**을 body_id로 리스트/stash(viewer_server.py:104-110,79)하고 `cad_get_selection`이 그 stem을 반환(822). 그런데 `cad_modify`는 `_STORE.get(body_id)`(mcp_server.py:522)가 stem을 모르니 **`fm.unknown_body_id`**. 문서화된 loop 'pick→get_selection→modify(반환된 selector)'가 **완료 불가**.
**Fix (한 네임스페이스를 authoritative로):** 가장 단순 — `cad_generate`/`cad_export`가 `<body_id>.step`로 쓰게 해서 stem이 곧 body_id가 되게 함. 또는 `cad_get_selection`에 stem→live BodyStore id 역인덱스 추가. selector(centroid 기반)는 body-agnostic이라 durable하므로, 반환 dict에 `current_body_id`(워크스페이스 최신 stem)를 같이 실어 Claude가 그걸 modify 타깃으로 쓰게 하면 F2+workflow staleness가 함께 풀린다.
**Pin proof:** `cad_generate(name='mypart')` → 뷰어 pick → `cad_get_selection` → 반환 selector+body_id로 `cad_modify` 호출이 `ok=True`.

### F3. 워크스페이스 커플링 3중 버그 (환경변수 미고정 + import-time 캡처 + 글롭 오염) — effort **S**
첫 실사용에서 **뷰어와 MCP가 다른 temp dir을 볼 확률이 매우 높다** → 첫 pick 이전에 조용히 깨짐("body 없음" 또는 엉뚱한 파트).
- `.mcp.json`(확인)에 `PHONE_DESIGNER_MCP_WORKSPACE` 없음 → `mcp_server.py:44-45`가 launch마다 새 `mkdtemp(pd_mcp_)`.
- 그 값이 **import-time에 `_WORKSPACE`로 캡처**(44-45)되어 나중에 env 세팅해도 무효 (footgun).
- 뷰어 `_workspace()` 글롭 `pd_mcp_*`가 BodyStore의 **`pd_mcp_bodies_*`** snapshot dir(_body_store.py:55 확인)까지 매칭 → 라이브 세션 중 생성되어 더 newest → 뷰어가 snapshot dir에 바인딩, selection 파일이 `cad_get_selection`이 못 읽는 곳에 떨어짐.

**Fix:** (a) `.mcp.json` env에 `"PHONE_DESIGNER_MCP_WORKSPACE": "${workspaceFolder}/.pd_workspace"` 고정. (b) self-healing: MCP가 startup에 자기 `_WORKSPACE` 경로를 `gettempdir()/pd_mcp_current.txt` pointer에 쓰고, 뷰어 `_workspace()`가 그걸 먼저 읽음. (c) 글롭을 `[p for p in tmp.glob('pd_mcp_*') if not p.name.startswith('pd_mcp_bodies_')]`로 좁히고 후보>1이면 경고.
**Pin proof:** MCP 켜고 뷰어 켠 뒤 `/api/bodies`의 `workspace`가 MCP `_WORKSPACE`와 동일 경로.

### F4. Path traversal — `/model/{id}.glb`와 `/pick`의 body_id `../`로 임의 .step read + arbitrary .glb write — effort **S**
`/model` 핸들러(viewer_server.py:154-159)와 `/pick`(41,46-48)이 body_id를 `ws/f'{body_id}.step'`에 컨테인먼트 체크 없이 넘김. static branch만 가드(162). `../../secret`로 워크스페이스 탈출 → 임의 STEP import·serve + 타깃 옆에 `.glb` 임의 write. 게다가 127.0.0.1 바인딩이지만 `Access-Control-Allow-Origin:*`(139)라 브라우저의 아무 웹페이지나 `/pick` POST 가능 (CSRF/DNS-rebinding으로 .step 탐침).
**Fix:** `_glb_for`/`pick_face` 진입에서 body_id에 `/`,`\`,`..` 있으면 거부 (또는 `ws.resolve() in step.resolve().parents` assert). CORS wildcard 제거→same-origin, Origin/Host allowlist.
**Pin proof:** `_glb_for(ws,'../../PWNED_probe')`가 404/거부.

### F5. 서버 single-threaded + no `allow_reuse_address` — effort **S**
`socketserver.TCPServer`(196). 첫 `/model/*.glb`가 **동기 GltfExport**(tessellate+write)를 도는 동안 `/api/bodies`·다른 GLB·`/pick`이 전부 블록. `pick_face`는 클릭마다 STEP re-import(58, ~0.10s/76면). auto-refresh poll(아래 W1) + GLB build + pick이 직렬화되면 UI가 멈춘 것처럼 보임. Ctrl-C 재시작은 TIME_WAIT로 'address in use'.
**Fix:** `ThreadingHTTPServer` + `allow_reuse_address=True` + `daemon_threads=True`. (F1이 들어가면 pick의 re-import 비용도 사라지지만, GLB build 블로킹은 threading으로 별도 해결.) 서버 클래스만 스왑, ~2줄.
**Pin proof:** GLB build 중 `/api/bodies`가 즉시 응답.

### F6. GLB 캐시 stale — stem 재사용 시 `mtime >=` 가드가 옛 GLB serve — effort **S**
"body immutable" 가정이 거짓. `cad_generate(name=X)`/`cad_export(name=X)`가 `<X>.step`를 새 지오메트리로 덮어씀. 덮어쓴 mtime == 캐시 GLB mtime(coarse granularity)이면 `>=`(121)가 **stale GLB serve**. 동시 `/model` 2개가 같은 `<id>.glb`로 build race.
**Fix:** content 기반 invalidate — STEP size/hash 비교, strict `>`+size, 또는 GLB 파일명에 STEP hash 임베드. temp path+atomic rename+per-body lock으로 race 제거.
**Pin proof:** part.step를 다른 지오메트리로 mtime 동일하게 덮고 `/model` 요청 시 새 GLB.

### F7 (low). 입력 검증 — `/pick` 잘못된 point가 500(400 아님), NaN이 조용히 '성공' — effort **S**
`float(point[0..2])`(61) 무검증. 짧은/비숫자 point는 500(client 에러인데). **NaN point는 raise 안 하고 face_idx 0 반환+stash → `cad_get_selection` 오염.** `index.html:175`는 `s.ok`만 보고 `distance_mm` 무시 → 15mm 빗나간 pick도 '면 선택됨'으로 자신있게 표시. (F1이 index-pick으로 가면 distance 이슈 대부분 소멸하지만, 입력 검증/NaN 거부는 여전히 필요.)
**Fix:** point를 3-element finite numeric로 검증, 잘못되면 400+정직한 에러, NaN/inf 거부.

---

## 2. HIGH-VALUE 기능 — 코어 loop를 여는 것 (사용자가 **할 수 있는 일**을 가장 크게 넓히는 순)

### V1 (최우선). Edge pick → edge fillet/chamfer — effort **M**
**왜 1등:** fillet/chamfer는 **edge 연산**이고, CAD 뷰어에서 클릭하는 #1 이유다. 현재 GLB엔 edge 지오메트리가 0개(probe: 18 OCCT edges → 0 LINES primitive), resolver에 `edges_near_point`도 없음(edges_on_face/by_length/by_position만). 그래서 최선이 '면 클릭→그 면의 **모든** edge 필렛'인데 slab top이면 둘레 전체가 라운드됨 — 대개 틀림. 하나의 edge를 클릭으로 지목 불가 = 헤드라인 데모의 표현력 공백.
**재사용 skill:** `edge_polyline_extract`(`_sample_edge`로 edge→polyline 샘플), resolver `edges_on_face`, `fillet_predicate`/`chamfer_predicate`(selector_kinds=['edges'] 소비), 가시성은 `hlr_view` VISIBLE set.
**Thin glue:** `EdgesNearPointSelector`를 `_selectors.py`에 추가 + `resolve_edges` clause(~30줄, faces_near_point 미러). `POST /pick_edge`: STEP 로드→`_all_edges` iterate→각 edge를 `_sample_edge`로 폴리라인화→ray/point에 최근접 edge 반환+`edges_near_point` selector stash. 뷰어: 가시 edge를 three.js `LineSegments`로 오버레이(hlr_view VISIBLE만)하고 굵은 threshold로 raycast. `fillet_predicate`/`chamfer_predicate`/`cad_get_selection`에 selector_kind 등록. **gltf_export 변경 없음 (byte-identical 유지).**
**주의:** F1의 face-primitive-index가 먼저 들어가야 edge 오버레이가 올바른 면 기준으로 그려진다.

### V2. SECTION / cut-plane 뷰 (내부 보기) — effort **M**
enclosure/pocket 파트는 즉시 단면으로 벽두께·내부 피처를 보고 싶어함. 현재 닫힌 solid만 orbit. **지오메트리 코드 불필요**, thin bridge만.
**재사용 skill:** `split_body`(BRepAlgoAPI_Splitter, `keep='positive'/'negative'`로 centroid side 한쪽 solid 반환 — export하면 채워진 내부가 보임), `cross_section`(교차 폴리라인 outline).
**Thin glue:** `GET /section?body_id&nx&ny&nz&d`: STEP 로드→`split_body(keep='negative')`→GltfExport half→GLB 스트림(plane hash로 캐시). 뷰어: 축 3버튼+슬라이더가 `d` 구동, 변경 시 표시 GLB를 `/section`으로 스왑. cross_section 폴리라인을 밝은 LineSegments로 cut plane에 오버레이. **body_id는 mutate 금지 — section은 transient view artifact지 lineage edit 아님.** ~40줄 서버 + 슬라이더.

### V3. Auto-refresh (cad_modify 후 자동 반영) — effort **S**
loop의 핵심 '내가 편집→사용자가 봄'인데, `index.html`은 페이지 로드와 수동 '새로고침'(47,188)에서만 `/api/bodies` fetch. poll/SSE/WS 전무. WEB_VIEWER_PLAN.md V2에 'WS /session'이 명세됐으나 미구현. → 매 modify마다 stale 모델, 수동 새로고침+새 body 클릭 = loop에서 가장 큰 수작업.
**재사용:** `_list_bodies`가 이미 newest-first 정렬, `index.html:139`가 이미 `d.bodies[0]` auto-show.
**Thin glue:** 의존성 0 — `setInterval(loadBodies, ~1500)`, 단 최신 stem이 안 바뀌면 카메라/선택 리셋 안 하고 genuinely 새 stem일 때만 reframe. ~8줄, 서버 변경 0. (진짜 push 원하면 `GET /events` SSE로 워크스페이스 파일 mtime tail — 나중에.) **F5(threading)가 선행돼야 poll이 GLB build를 블록 안 함.**

### V-보조. 선택된 FACE 하이라이트 (mispick을 눈으로 잡게) — effort **S**
현재 on-mesh 피드백은 빨간 마커 sphere+텍스트 뱃지뿐, body 전체가 단일 steel-blue. **서버가 내가 클릭한 것과 다른 면을 골랐는지 볼 방법이 없다.** GLB의 per-face primitive가 바로 이걸 위해 있는데 `index.html:115-118`이 **하나의 공유 material**을 전부에 적용해 per-face 색칠을 막음.
**Thin glue:** traverse에서 `o.material = mat.clone()` + `faceMeshes[]`에 push. pick 시 이전 하이라이트 복원 후 hit mesh를 accent(emissive orange). **F1의 face_idx picking이 들어가면 공짜로 딸려온다.** ~15줄. — F1과 함께 묶어서 반드시.

---

## 3. NICE-TO-HAVE

- **MEASURE (두 점/면 → 거리)** — effort **S**. 거의 이미 배선됨: 뷰어가 매 클릭 3D hit point(`index.html:158`)를 가짐. **재사용:** `measure` skill(point/face_center/edge_midpoint/bbox_center, distance&angle — probe로 point-point 24.0mm, face-point 10.0mm 확인). Glue: 'Measure' 토글, 첫 클릭 point 저장, 둘째 클릭에 `POST /measure {a,b}`→`value_mm`, dashed line+label. 주의: `measure.py`의 declared selector_kinds에 `faces_near_point` 누락(resolver는 수용) — preflight 일관성 위해 추가.
- **Feature overlay (검출된 hole/pocket/boss 하이라이트)** — effort **M**. **재사용:** `extract_feature_catalog`(각 피처에 `face_indices` + centroid). GLB primitive 순서 = `_all_faces` 순서라 face_indices가 primitive에 직접 매핑. `GET /scene?body_id`로 카탈로그 반환→사이드 패널, 클릭 시 해당 primitive 하이라이트+centroid를 faces_near_point로 stash. "파란 덩어리 orbit하며 찍기" → "당신의 hole 4개·pocket 2개, 하나 고르세요". mispick도 de-risk.
- **카메라 프리셋 + 단위/치수 칩** — effort **M**. Reset/Front/Top/Iso 버튼(`frameObject`의 center/r 재사용), 'mm' 칩, bbox 치수 readout(이미 만든 Box3), 뱃지를 surface_type+dominant normal('top plane, +Z')로 기술. 전부 client-side, 데이터 이미 보유.
- **Drag-vs-click 임계값** — effort **S**. 고정 5px+damping이 느린 정밀 클릭을 orbit으로 먹고 피드백 0. ~8-10px+시간창(<250ms), DPR 정규화, 'orbit으로 소비됨' 큐.
- **One-command 런치** — effort **S**. `pyproject.toml [project.scripts]`에 `phone-designer-viewer` 추가 + `serve()`에서 `webbrowser.open`(--open 플래그). **함정:** 현재 `phone-designer viewer`(cli.py:280)는 **옛 PyVista 데스크톱 뷰어** — 잘못된 것 실행. 데스크톱을 `viewer-desktop`으로, 웹을 `viewer`로 alias. VS Code Simple Browser 경로도 문서화.
- **`cad_generate`/`cad_modify` 결과에 `viewer_url` 힌트** — effort **S**. 순수 문자열 추가, 지오메트리 무관. 뷰어가 GLB를 lazy-build하므로 glb export 불필요, 하지만 '최신 body로 자동 등장' 힌트가 필요.
- **`faces_near_point`를 SelectorKind Literal에 추가** — effort **S**. 구현/등록됐으나 Literal(_selectors.py:32-48)에 없음 → `typing.get_args(SelectorKind)`를 도는 schema/preflight/planner에 **보이지 않음**. 지금은 각 Pydantic class가 자체 Literal을 들고 있어 우회되지만, preflight가 master Literal을 gate하는 순간 pick이 조용히 'unknown selector'. edges_convex/concave_only도 이미 누락 — 리스트가 drift 중. (edge selector V1 추가 시 `edges_near_point`도 함께.)
- **Stale body_id 신호 (`current_body_id`/`is_current`)** — effort **S**. F2 fix에 흡수 권장.
- **Multi-pick 누적 ('이 3면 필렛')** — effort **M**. `faces_near_point`에 `n` 필드 이미 있음(_selectors.py:118). Shift+click append, stash를 selection SET으로, `cad_get_selection`이 OR/n selector 반환.
- **Multi-body/assembly 표시** — effort **L**. 현재 `loadBody`가 이전 body 제거(108)+`bodies[0]`만 auto-load = 단일 파트 인스펙터. V3 스코프, V2 blocker 아님. F1-F4 착지 후로 미룸.
- **VS Code Simple Browser 문서화** — effort **S**. F5(threading)에 얹어 doc 한 줄.

---

## 4. BUILD ORDER — 다음 배치 (먼저 할 5개)

이 순서인 이유: **loop를 깨는 것 → loop를 완성하는 것 → loop를 넓히는 것.** F1/F2/F3가 없으면 뷰어는 클릭해도 틀린 면을 주거나(F1), 그 면을 modify로 못 넘기거나(F2), 애초에 다른 워크스페이스를 본다(F3) — **셋 중 하나라도 빠지면 pick loop가 0% 동작.** F5는 auto-refresh/section이 뒤에서 서버를 블록하지 않게 하는 선결 인프라라 V1/V2/V3 앞에 둔다.

| 순번 | 항목 | 왜 이 순서 | Pin-able proof |
|---|---|---|---|
| **1** | **F1 face-index pick + V-보조 하이라이트** (묶음, S) | 5개 critical pick 버그를 한 번에 죽이고, 하이라이트가 딸려와 사용자가 mispick을 눈으로 확인. 다른 모든 pick/edge/feature 작업의 토대. | 40×20×10 box top-face 클릭 → `face_idx`=top(정확), 하이라이트가 top에 뜸. 진짜 GLB-space 경로를 태우는 회귀 테스트 green. |
| **2** | **F2 네임스페이스 브리지 + current_body_id** (M) | pick이 정확해져도 selector가 modify로 안 넘어가면 loop 미완. F1 직후 왕복을 완성. | `cad_generate`→pick→`cad_get_selection`→그 selector+body_id로 `cad_modify` = `ok=True`, fillet이 클릭한 면에 착지. |
| **3** | **F3 워크스페이스 커플링 (env 고정+pointer+글롭)** (S) | 위 둘이 맞아도 두 프로세스가 다른 dir을 보면 무의미. 첫 세션 silent-break 방지. | MCP+뷰어 기동 후 `/api/bodies`의 `workspace` == MCP `_WORKSPACE`. bodies_ dir이 newest여도 뷰어가 real ws 선택. |
| **4** | **F4 path traversal + CORS + F5 threading/reuse_addr** (묶음, S) | 첫 실사용 전 보안 하한선(F4) + poll/section이 블록 안 되게 하는 인프라(F5). 둘 다 서버 파일 국소 수정이라 함께. | `_glb_for(ws,'../../x')` 거부; GLB build 중 `/api/bodies` 즉시 응답; Ctrl-C 재시작 즉시 재바인딩. |
| **5** | **V3 auto-refresh + V-보조 잔여** (S) | F5가 threading을 깔았으니 이제 안전하게 poll. loop의 최대 수작업 제거 — '편집→자동으로 봄' 완성. | `cad_modify` 후 ~1.5s 내 뷰어가 새 body로 자동 전환, orbit 중이면 뷰 안 뺏김. |
| **6** | **V1 edge pick → edge fillet** (M) | loop가 안정되고 자동화된 뒤, 사용자가 **할 수 있는 일**을 가장 크게 넓히는 기능. F1의 face-index가 선행돼야 edge 오버레이가 정확. | box edge 클릭 → `edges_near_point` selector stash → `cad_modify` fillet이 **그 하나의 edge**만 3mm 라운드. |

**추가 저비용 끼워넣기 (아무 배치에나):** F6(GLB stale, S)와 F7(입력검증/NaN, S)는 F4/F5와 같은 파일을 건드리므로 4번에 함께 넣으면 marginal cost가 거의 0. `faces_near_point`를 SelectorKind Literal에 추가(S)는 V1에서 `edges_near_point` 넣을 때 한 줄 더.

**정직한 effort 총평:** 1~5번은 전부 S(하나는 M) — **핵심 loop 전체를 동작+안전+자동화하는 데 대략 M 규모 하루 작업**이면 도달 가능(각 항목이 파일 1-2개 국소 수정, 지오메트리 코드 신규 없음). 진짜 신규 지오메트리 브리지는 V1(edge)/V2(section)뿐이고 둘 다 **기존 skill 재사용**(edge_polyline_extract / split_body+cross_section)이라 M. L짜리(multi-body)는 V3 스코프로 정당하게 미룸.

**관련 파일 (절대경로):**
- `D:\SmartTwinModeller\src\phone_designer\viewer_server.py` (F1,F3,F4,F5,F6,F7 + V2/V3 엔드포인트)
- `D:\SmartTwinModeller\src\phone_designer\viewer_static\index.html` (F1,V3,V-보조 + V1 edge 오버레이)
- `D:\SmartTwinModeller\src\phone_designer\mcp_server.py` (F2 line 522 `_STORE.get`, line 806 `cad_get_selection`; line 44-45 import-time `_WORKSPACE`)
- `D:\SmartTwinModeller\src\phone_designer\skills\io\gltf_export.py` (line 172-175 좌표계 baking — F1의 근본; 변경 불필요)
- `D:\SmartTwinModeller\src\phone_designer\skills\_selectors.py` (line 32-48 Literal 누락; V1의 EdgesNearPointSelector)
- `D:\SmartTwinModeller\src\phone_designer\mcp_support\_body_store.py` (line 55 `pd_mcp_bodies_` 글롭 오염 — F3)
- `D:\SmartTwinModeller\.mcp.json` (F3: `PHONE_DESIGNER_MCP_WORKSPACE` 추가)
- 재사용 skill: `skills\transform\split_body.py`(V2), `skills\inspect\cross_section.py`(V2), `skills\inspect\edge_polyline_extract.py`(V1), `skills\inspect\measure.py`(measure), `skills\reverse_engineer\extract_feature_catalog.py`(feature overlay)