# 검증·분석·재생성 역량 확보 플랜 (2026-06)

> **진행 상태 (2026-06-11):** Wave 1 완료 (V1 ✅ 2dfbb34, P1 ✅ 8a4790f,
> V3 ✅ 70662dc) · Wave 2 완료 (V2 ✅ corpus-regress+baselines,
> V4 ✅ geometry_deviation, P3 ✅ validate_rebuilt_body,
> A1 ✅ topology_health, A2 ✅ pass-24 — Ventilator 0.381→0.9091,
> root corpus **55/55 ≥ 0.5** 달성, P4 ✅ 데모 v2 gated 전 셀 PASS) ·
> **Wave 3 완료** (V5 ✅ executor provenance+strict_cuts,
> V6 ✅ RE 코어 46 테스트+ratchet — detect_shell_holes 버그 발굴·수정,
> V7=A4 ✅ ground-truth 사이드카 42파일/111치수,
> A5 ✅ inspect_draft_angles, A6 ✅ measure_thread_pitch+pass-25,
> A7 ✅ read_step_pmi — export 측이 빈 GDT 를 쓰는 결함 발견,
> A8 ✅ gdt_position DRF+MMC, P5 ✅ scale-aware planner,
> P6/P7/P8 ✅ normalize+constraints+strict,
> A3 ⚠️ classify_edge_blends 스킬 완성 — 단 카탈로그 연결은 OPT-IN:
> 컷 잔여면이 regen 에서 fillet 으로 오분류 (Ventilator 31→54),
> default-on 은 self-match 하드 제약 위반. 잔여: cut-residue 필터 후
> default-on, A9/A10=P9, export PMI 수정, KR600/RC_Buggy 타임아웃).

9-agent 병렬 감사(스킬 인벤토리 / 테스트 인프라 / RE 파이프라인 / 파라메트릭
변형 / 코퍼스 / executor 신뢰층 + 3개 설계 에이전트) 결과를 종합한 실행
플랜. 목표 3개:

1. **Track V — 자동화된 모든 부분의 검증**: 헤드라인 수치(35/55 PERFECT,
   box 0.83/0.46/0.82/0.69)가 재현 가능하고 회귀가 자동으로 잡히는 상태.
2. **Track A — 퀄리티 있는 CAD 분석 스킬 확보**: 산업 분석가가 쓸 수 있는
   측정 등급(measured-grade) 분석 커버리지.
3. **Track P — 다른 치수 재생성(파라메트릭 변형) 완성**: 변형된 치수로
   **유효한 바디**가 재생성되고 그것이 해석적으로 검증되는 상태.

## 직접 검증된 P0 버그 (감사 주장 → 코드로 재확인 완료)

| # | 버그 | 증거 | 영향 |
|---|------|------|------|
| 1 | `extrude_pocket_world`, `assembly_reverse_engineer`, `base_step_kind_chooser`, `body_orientation_check` 4개 스킬이 `export_manifest.py` 미등록 | grep 0건; executor 는 manifest 만 import (`executor.py:53-55`) | 프로덕션 CLI 경로에서 box-mode pocket 전부 unknown-skill 드롭. 지금까지의 box 점수는 ad-hoc 스크립트의 `pkgutil.walk_packages` 전체 임포트 덕분에 측정된 것 |
| 2 | `vary_feature_catalog` whitelist 에 `entry_origin`/`entry_depth_mm` 누락 (pass-23 신규 필드) | `vary_feature_catalog.py:48-82` vs `plan_from_feature_catalog.py:359-361` | scale≠1.0 box-mode 변형에서 **모든 hole 이 스케일 안 된 위치에 emit → zero-delta SKIP → 민슬라브**. 감사 스모크: linkrods 1.5× = 구멍 없는 슬라브 (vol 3.887× vs 기대 3.375×) |
| 3 | CI `fidelity.yml:60` 이 존재하지 않는 `tests/skills/test_manifest_drift.py` 호출 + CASES fixture 전부 gitignored | `ls` 실패 확인; gh run 최근 5회 전부 failure | 유일한 자동 게이트가 모든 push 에서 적색 = 게이트 부재 |
| 4 | zero-delta SKIP 이 실 컷 손실을 PASS 로 위장 | `executor.py:165-182`, error_count 미증가 | 변형/재생성 실패가 invisible — 위 #2 가 조용히 통과한 이유 |
| 5 | 헤드라인 지표의 순환성 | `feature_fidelity_diff` 는 같은 detector 의 출력끼리 비교 | detector 사각지대는 양쪽에서 동일하게 누락 → 점수에 안 보임. 기하학적 ground truth 부재 |

## Track V — 자동화 검증 (우선 실행)

### V1. 스킬 레지스트리 정합 (P0, S)
- `export_manifest.py` 에 4개 고아 스킬 등록.
- `tests/skills/test_manifest_drift.py` 신규 (CI 가 이미 호출하는 파일):
  AST/pkgutil 로 `@skill` 정의 전수 스캔 → SkillRegistry 등록 집합과 일치
  assert + planner 가 emit 하는 모든 skill name 문자열이 registry 에서
  resolve 되는지 assert.
- **검증**: 현재 HEAD 에서 red → manifest 수정 후 green. box-mode
  corpus 점수가 CLI 경로에서도 동일하게 나오는지 재측정.

### V2. corpus-regress 정식 하니스 (P0, L)
- `run_logs/_tmp/run_root_corpus_re.py`(untracked) 로직을
  `src/phone_designer/corpus/regress.py` + `phone-designer corpus-regress`
  CLI 로 승격. `--mode preserve_brep|box|both`, `--subset`,
  `--update-baseline`, 파일별 5분 watchdog, subprocess 격리.
- baseline JSON 커밋 (`corpus/baselines/`): 파일별 match_ratio,
  per_kind, volume_delta_pct + **회귀 시 exit 1**.
- 23-pass 동안 매번 손으로 짠 인라인 sweep 을 이것으로 대체.
- **검증**: d121f14 에서 audit-v6 수치 재현 (35/55 PERFECT, box 4종
  0.833/0.464/0.823/0.690). 과거 revert 된 Pass-22 패치를 스크래치
  브랜치에 적용하면 하니스가 red 가 되는지 확인 (회귀 감지 능력 증명).

### V3. CI 복구 (P0, M)
- fixture 생성 스텝 추가 (`scripts/build_fidelity_fixtures.py` 추적 커밋,
  `fixtures/generated/` 출력), CASES 경로 재지정.
- `skill_audit.yml` 의 untracked 스크립트 참조를 `skills/audit.py` 기반으로 교체.
- **검증**: fresh checkout 의 GitHub runner 에서 fidelity.yml green.

### V4. geometry_deviation 스킬 — 순환성 타파 (P0, M)
- 신규 `inspect/geometry_deviation.py`: 원본 대비 volume/surface-area
  delta % + BRepMesh 테셀레이션 양방향 point-to-triangle 거리
  (Hausdorff/RMS). catalog 비교가 아닌 **기하 자체의 ground truth**.
- corpus-regress 레코드에 `hausdorff_mm` 컬럼 추가.
- **검증**: 동일 바디 → ~0; 100mm 박스 한 면 0.5mm 오프셋 → 0.45–0.55;
  preserve_brep self-match 전 코퍼스에서 ~0 확인.

### V5. executor 신뢰층 보강 (P1, M)
- per-step provenance: `_spec.py:160-172` 가 이미 계산하고 버리는
  pre/post volume/face_count 를 Step 에 저장, `ExecutionResult.to_report_json()`.
- `skipped_steps` 목록화 + `strict_cuts` 모드 (변형 플랜 기본 ON):
  cut step 의 zero-delta SKIP → FAIL.
- volume 측정 실패 메시지가 SKIP 으로 새는 regex fallback 수정
  (`executor.py:287-288`).
- **검증**: 의도적 out-of-body cut 이 PASS 가 아닌 skipped_cuts=1 로
  보고; preserve_brep 코퍼스 35/55 무변동 (flag off 기본).

### V6. RE 코어 테스트 공백 (P1, M)
- 무테스트 12개 스킬 중 핵심: `feature_fidelity_diff`(헤드라인 지표인데
  단위 테스트 0개), `base_step_kind_chooser`, `body_orientation_check`,
  `detect_shell_holes`, `assembly_reverse_engineer`.
- coverage ratchet (정의 348 = 참조 348 또는 명시적 축소 allowlist).
- **검증**: synthetic catalog pair 로 1.0 / dropped / drift 경계 고정.

### V7. 치수 ground-truth 사이드카 (P1, M)
- `corpus/oem/ground_truth/*.json`: revolved/ 패스너 40개 + kicad 20개의
  공칭 치수 (608ZZ=8/22/7, ISO4032_M3 A/F 5.5, C_0402=1.0×0.5 — 파일명과
  `catalogs/standards/*.yaml` 에 이미 있는 값).
- `tests/corpus/test_ground_truth_dims.py` (`requires_oem` 마커 — 휴면
  conftest auto-skip 최초 가동): classify_holes 측정값 vs 공칭 비교.
- **검증**: bore 추출을 5% 비틀면 10개 이상 assert 가 red (mutation check).

### V8. 단일 verify 엔트리포인트 (P1, S)
- `scripts/verify.ps1` + `phone-designer verify`: fast pytest →
  FIDELITY_STRICT → catalog validate → corpus-regress, 단일 exit code.
- 죽은 pytest 마커 4종 가동 (slow/requires_oem 태깅).

## Track A — CAD 분석 스킬 확보

현 상태 (감사): 352 스킬 중 hole/pocket/wall-thickness/curvature/질량
특성/간섭/GD&T 핏팅은 **실측급**. 빈 곳: BREP 위상 건강성, fillet/chamfer,
draft 메트롤로지, thread pitch, PMI 읽기.

### A1. topology_health (P0, M)
- `BRepCheck_Analyzer` + `BOPAlgo_CheckerSI`(opt) + `ShapeAnalysis_FreeBounds`
  + non-manifold/sliver 센서스. **repo 전체에 BRepCheck 가 0건**이었음.
- corpus-regress 에 `regen_valid` 컬럼으로 연결 (V2 와 결합).

### A2. classify_holes Ø1mm 소형 홀 강화 — pass 24 (P0, M)
- Ventilator 8/14 미검출의 원인 (절대 mm 톨러런스가 소형 홀에서 과대)
  진단 + `min_diameter_mm` Args 화. preserve_brep 0.381 → 개선 기대.
- **검증**: V2 하니스로 Ventilator 점수 개선 + 타 54파일 무회귀.

### A3. classify_edge_blends — fillet/chamfer 검출 (P0, L)
- 엣지 기반: cylinder/torus/일정반경 bspline 면이 양쪽 이웃과 G1 연속이면
  fillet (radius=실측), 비스듬한 평면 스트립+양쪽 sharp 엣지면 chamfer.
- `extract_feature_catalog` 에 `edge_blends` 키 추가 →
  `feature_fidelity_diff` 가 fillet 누락을 **처음으로 감점 가능**.
- **검증**: build123d 로 만든 기지 radius fillet → 2% 내 실측; 코퍼스
  preserve_brep 무회귀 sweep.

### A4. 치수 메트롤로지 ground-truth 하니스 (P0, M) — V7 과 동일 항목 (공유)

### A5. inspect_draft_angles (P1, M)
- 면당 5×5 UV 그리드 normal 샘플 → pull_direction 대비 min/mean draft,
  ok/below_min/undercut 분류. 현재는 면당 1점 이항 판정뿐.

### A6. measure_thread_pitch (P1, M)
- 나선 unwrap (θ,z) 최소제곱 → pitch/handedness/신뢰도.
  classify_holes 의 표준 매칭을 (직경,피치) 쌍 스코어로 업그레이드 —
  M3×0.5 vs M3×0.35 구분 가능.
- **검증**: revolved/ ISO 패스너 10개에서 공표 피치 5% 내.

### A7. read_step_pmi — AP242 GD&T 읽기 (P1, L)
- 현재 PMI 는 **쓰기 전용**. `STEPCAFControl_Reader` + `XCAFDoc_DimTolTool`
  로 치수/데이텀/공차 읽기 (export 측 코드가 XCAF 플러밍 검증 완료).
- **검증**: 자체 export→read 라운드트립 + NIST MBE CTC 모델 답안지 대조.
- 벤더 STEP 의 PMI 는 **유일하게 권위 있는 치수 ground truth** — V7 과 시너지.

### A8. gdt_position DRF + 축 기울기 평가 (P1, S)
- datum reference frame 변환 + 깊이 양단 평가 + 고아 상태인
  `mmc_lmc_modifier` 보너스 연결. 실제 도면 콜아웃 재현 가능해짐.

### A9. extract_quality_report 집계 스킬 (P2, M)
- topology_health + wall_thickness + draft + edge_blends + 질량특성 +
  GD&T 를 한 번에 — 분석가용 단일 리포트. `result_grade:
  measured|estimate` 메타데이터로 추정치 스킬 가시화.

### A10. classify_pockets footprint_kind (P2, M)
- circular/rect/slot/freeform 분류 + planner 가 정사각 프록시 대신 실제
  풋프린트 사용 → box-mode volume drift 의 주범 공략.

## Track P — 파라메트릭 재생성 완성

현 상태: `vary_feature_catalog`/`plan_from_scaled_catalog` 파이프 존재,
bbox 는 정확히 스케일됨. 그러나 **features 가 전부 소실된 민슬라브**가
PASS 로 나오는 상태 (P0 버그 #2+#4 의 합작).

### P1. vary whitelist 보수 + 스키마 커플링 계약 테스트 (P0, S)
- `entry_origin`, `plane_origin`, `centroid` → `_VECTOR_DIM_FIELDS`;
  `entry_depth_mm` → `_SCALAR_DIM_FIELDS`. (`plane_normal`, `axis_dir` 는
  스케일 금지 — 방향 벡터.)
- **계약 테스트**: 실제 catalog 의 모든 중첩 키를 걷어
  `(_mm$|origin|center|position|bbox|centroid)` 매치 키가 whitelist 또는
  명시적 DIMENSIONLESS 예외집합에 있는지 assert — **pass-23 류의 신규
  detector 필드가 다시는 조용히 누락되지 않게** CI 화.
- **검증**: linkrods 1.5× → 부피비 3.375±2%, 재검출 홀 직경 1.5×±8%.

### P2. 고아 스킬 등록 (P0, S) — V1 과 동일 항목 (공유; 변형 pocket 경로 unblock)

### P3. validate_rebuilt_body 스킬 (P0, M)
- 변형 후 검증: (1) **feature 실현 센서스** — catalog 의 각 hole/pocket
  기대 void 위치를 `BRepClass3d_SolidClassifier` 로 probe, 물질이면
  '컷 소실' 보고; (2) BRepCheck IsValid + self-intersection;
  (3) bbox 축별 비율 vs 기대 0.5% 내; (4) min-wall (옵션).
- `plan_from_scaled_catalog` 생성 플랜의 마지막 스텝으로 자동 연결.
- **검증**: P1 수정을 monkeypatch 로 되돌리면 features_lost 2/2 보고,
  수정 상태에선 0 — 알려진 버그를 영구 핀.

### P4. 변형 데모 하니스 v2 — 해석적 채점 (P0, M)
- `tools/parametric_regen_demo.py` + `tests/test_parametric_regeneration.py`:
  N 파일 × scale {0.5, 1.0, 1.5, 2.0} × per-feature 편집 매트릭스.
- 채점은 **해석적** (catalog 순환 배제): bbox 비율 == s ±0.5%; 재검출
  직경 == s×원본 ±8%; per-feature 편집은 해석적 ΔV (포켓 풋프린트×추가
  깊이) 15% 내 — **baseline 과 bit-identical 이면 자동 FAIL** (기존
  데모의 per_feat 무변화 artifact 를 정확히 잡는 규칙).
- `fixtures/make_simple_watch.py` 의 authored 상수(HOUSING_OD=44.0 등)를
  CI-safe ground truth 로 활용.
- **검증**: P1+P2 후 simple_watch+linkrods 전 셀 green; 전체 매트릭스는
  P5–P7 후 green. 셀별 표를 `docs/parametric_regeneration_demo_v2.md` 로.

### P5. scale-aware planner (P1, M)
- `dimension_scale` Arg: 200mm 깊이 클램프 ×s, `_MIN_EMITTED_CUT_MM3`
  ×s³, 마운팅 패드 20mm 캡 ×s. 0.1× 축소 시 소형 홀 증발 / 10× 확대 시
  깊이 절단 방지.
- `_pick_base_shape` 가 **스케일 안 된 라이브 바디**에서 cyl/sphere 반경을
  측정하는 버그 수정 (revolved/ 패스너 36개는 현재 영원히 재스케일 불가)
  + 1813/1848 행의 bbox 튜플 언패킹 잠복 버그 수정.
- **검증**: 5개 파일 0.25×/1×/4× 에서 step 수·종류 동일, depth == s×기준.

### P6. normalize_varied_catalog — 종속 필드 재계산 (P1, M)
- 스케일된 직경으로 thread/standard 재매칭 (M3 hole 2× → M6 또는 None,
  절대 M3 잔존 금지); 패턴 positions 를 spacing/pitch_radius 에서 해석적
  재계산; counterbore 불변식 (cb_d > through_d) 검사; mirror pair 를
  스케일된 plane_origin 과 재대조.
- **검증**: 단위 테스트 4종 + 순수 uniform scale 에선 warning 0.

### P7. 제약 보존 모드 (P1, M)
- `constraints: {preserve_wall_thickness, min_wall_mm, containment:
  warn|clamp|fail}` — '하우징은 키우고 벽두께는 유지' 가능하게.
  catalog 공간에서 선검사 (envelope ⊂ scaled bbox, 최소벽), 위반 시
  정책별 처리. 기하 최종 진실은 P3 가 담당.

### P8. 오버라이드 무음 무시 제거 (P1, S)
- dotted key 오타 (`diamter_mm`) → `unresolved_overrides` 보고 +
  `strict` 모드에서 예외. Args 에 `extra='forbid'` (2개 파일만).

### P9. pocket entry anchor + 실제 풋프린트 (P2, L) — A10 과 결합
- pass-23 의 sibling-field 패턴을 pocket 에 적용 (`entry_origin` 추가,
  floor-centroid `axis_origin` 불변 유지 — pass-18 revert 의 교훈을
  우회하는 설계). per-edit ΔV 채점 15%→5% 강화 목표.

## 실행 순서 (의존성 기준)

```
Wave 1 (신뢰 기반, 전부 P0):
  V1=P2 (manifest+drift test, S) ─┐
  P1 (vary whitelist, S)          ├─→ 즉시 효과, 상호 독립
  V3 (CI 복구, M)                 ┘
Wave 2 (측정 기반):
  V2 (corpus-regress, L)  ← V1 후
  V4 (geometry_deviation, M)
  P3 (validate_rebuilt_body, M) ← P1 후
  P4 (변형 데모 v2, M) ← P1+P2+P3 후
  A1 (topology_health, M), A2 (Ø1mm pass-24, M)
Wave 3 (역량 확장):
  V5, V6, V7=A4, V8
  A3 (edge_blends), A5 (draft), A6 (thread), A7 (PMI read), A8 (DRF)
  P5 (scale-aware), P6 (normalize), P7 (constraints), P8 (strict)
Wave 4 (장기):
  A9 (quality report), A10=P9 (pocket footprint)
```

## 불변 원칙 (23-pass 의 교훈 계승)

1. **identity 필드는 불변, sibling 필드 추가** (pass-22 revert / pass-23
   성공 패턴). pocket 개선도 동일 패턴 강제.
2. **모드별 와이어링**: preserve_brep self-match 1.0 은 모든 변경의
   하드 게이트 — V2 하니스가 이를 자동 강제.
3. **additive pass-style 변경**: classify_holes/pockets 재작성 금지.
4. **모든 신규 스킬**: @skill + pydantic Args + PostCondition +
   export_manifest 등록 + V1 drift 테스트가 등록 누락을 CI 에서 차단.
