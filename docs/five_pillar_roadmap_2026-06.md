# 5-기둥 통합 로드맵 (2026-06) — RE·대형·리포트·변형·비교

5-에이전트 병렬 설계(각 기둥 실제 코드 read-only) + 1 종합. 목표:
**리버스 엔지니어링 최대화(자유곡면 포함) · 대형 어셈블리 처리 ·
분석 후 리포트 · 주요 치수 변형 생성 · 유사 두 구조 상호 분석** 을
하나의 단계화된 마스터 플랜으로.

> **진행 상태 (2026-06-13):**
> - **Phase 0 완료** (5 커밋, 전부 byte-identical 검증): geometry_deviation
>   rigid+Hausdorff 게이트(즉시 as1_pe_203 의 숨은 200mm 클램프 오차 적발) ·
>   plan_out_path · 검출기 출력 재사용(3-4× 속도, Ventilator 173→156s) ·
>   component 시그니처 · section-curve 복원 헬퍼. corpus-regress 양 모드 0 회귀.
> - **Phase 1 완료** (5 커밋, 12 신규 스킬, corpus 양 모드 0 회귀):
>   FREEFORM `extract_outer_silhouette_profile`+`extrude_profile_world`
>   (opt-in, A/B revert-guard — **11752 box 0.047→0.179, hausdorff 569→489**) ·
>   COMPARE `register_bodies`+`feature_change_classify` (rmsd 0.0mm 복원) ·
>   REPORT `dfm_verdict`+`emit_quality_report` (measured/estimate 등급 HTML) ·
>   VARIANTS `identify_key_dimensions` · PERF assembly 인스턴스 dedup+pool.
> - **Phase 2 완료** (6 커밋, 3 신규 스킬, corpus 양 모드 0 회귀):
>   COMPARE **`compare_parts`** 매크로 — 두 STEP → registration + 변경분류
>   + Hausdorff 히트맵 + 질량델타 + PMI diff + 유사도/family 분류 (헤드라인
>   산출물; A-vs-A identical, 1.5×-scaled→scaled-family scale≈1.5) ·
>   VARIANTS `recover_design_relations`+`apply_variant_drivers`
>   (housing_length 1.5×→1.497, counterbore 전파) ·
>   FREEFORM 진짜 loft 단면 복원 (circle fallback byte-identical) ·
>   REPORT 헤드리스 단면 PNG (render_views opt-in, GL작동) ·
>   PERF boss/rib face-pair fast-path (deep-equal).
> - **Phase 3 완료** (6 커밋, 1 신규 스킬, corpus 양 모드 0 회귀):
>   HARNESS `corpus-regress --workers`(기본 1=serial byte-identical;
>   N>1 은 speedup, timeout-경계 파일은 경합으로 거짓 timeout 가능 —
>   정직 caveat 코드 명시) + **`compare-regress` 비교 회귀 게이트**
>   (3쌍 identical/variant/unrelated, sign-flip self-proof) ·
>   VARIANTS `generate_variant_family`(linkrods 4변형 monotone) +
>   normalize 5번째 관계-일관성 pass · REPORT 프로덕션 HTML +
>   golden-snapshot 게이트 · FREEFORM 전용 corpus 레인 box_freeform.json
>   (swept_channel 320× win, rounded_plate 9.3× tighter but guard 가
>   match_ratio 로 revert — guard-metric 수정 Phase 4 큐).
> - **다음: Phase 4 (어려운 프런티어, XL)** — fit_bspline_surface_patch
>   (open-shell 편차 리포트), 진짜 sweep path+profile, registration ICP 강화,
>   SOLID-aware 분할+캐시(KR600/RC_Buggy), revert-guard 를 hausdorff 기준으로,
>   PDF 스파이크. 각자 정직한 fallback.

설계 원칙 (전 기둥 공통, 23-pass + Wave1~4 의 교훈 계승):
1. corpus-regress 하니스로 게이트 — 인라인 sweep 금지.
2. preserve_brep 35/55 PERFECT 는 하드 제약 — 모든 변경의 바닥.
3. identity 필드 불변 + sibling 필드 추가 (pass-22 revert / pass-23 성공).
4. additive pass-style — classify_*/plan_* 재작성 금지.
5. **fake-accuracy 금지**: feature_fidelity_diff 의 0.15 tol 은 기하적으로
   틀린 복원도 통과시킨다 → **geometry_deviation(Hausdorff) 게이트 필수**.

## 기둥별 현재 갭 (코드 근거)

| 기둥 | 현재 상태 | 핵심 갭 |
|------|----------|---------|
| 자유곡면 복원 | box 모드가 box/cyl/sph/torus/cone 프리미티브만 (`_pick_base_shape` plan:1912). bspline_faces 수집 후 폐기(extract:269), sweep path 는 조작된 3점 대각선(308-312, "~30% 부피" 자인) | 외곽 실루엣 추출+extrude, 진짜 단면 프로파일 복원, B-spline 표면 스키닝 부재. Ventilator 0.30 / 11752 0.047 |
| 대형 어셈블리 | corpus-regress 직렬, 검출기 array 루프가 ClassifyHoles/Pockets/Bosses 를 7× 재실행(710-743), assembly RE 직렬(`_plan_yaml_path` 하드코딩) | KR600(4000+면)·RC_Buggy(148부품) 900s 타임아웃. 인스턴스 중복 검출(볼트 50개 = 50회) |
| 분석 리포트 | `extract_quality_report` 가 dict 만 반환 | 사람용 산출물(HTML/PDF/단면뷰) 부재, DFM 판정 부재, measured/estimate 시각화 부재 |
| 치수 변형 | vary/normalize/constraints/validate 파이프 작동 | **어느 게 "주요" 치수인지** 식별 부재, 설계의도/관계 그래프 부재(bore↔counterbore, 패턴 pitch) |
| 두 구조 비교 | 씨앗만: `feature_fidelity_diff`(쌍맞춤), `geometry_deviation`(Hausdorff) | 둘 다 **평행이동만** 처리 → 회전 정합(registration) 부재. "무엇이 바뀌었나" 판정·치수 델타·유사도·family 분류 전부 신규 |

## 공유 인프라 (한 번 만들어 여러 기둥이 쓰는 것 — 먼저)

| 항목 | 서비스 기둥 | 먼저인 이유 |
|------|-----------|------------|
| **geometry_deviation rigid-transform** (`align='rigid'` + 4×4 변환 + Hausdorff 를 corpus-regress baseline 에 추가) | 자유곡면·비교·리포트 | 자유곡면의 anti-fake 게이트, 비교의 회전 프리미티브, 리포트의 영역별 히트맵 — **3 기둥, 1 additive 변경** (기존 none/bbox_center + 35/55 self-match 무영향) |
| **단면-커브 복원 헬퍼** (`cross_section.py` BRepAlgoAPI_Section → 닫힌 루프 → 실행가능 Sketch) | 자유곡면·리포트·비교 | 최고 fan-out: extrude/loft/sweep 베이스 프로파일 + 단면뷰 PNG + 비교 히트맵. 실행 sink(PolygonSketch/loft ThruSections)는 이미 검증됨, 복원 절반만 비어있음 |
| **plan_out_path Arg** (`PlanFromFeatureCatalog`, 기본=현재 경로 byte-identical) | 대형·변형·비교 | `_plan_yaml_path` 하드코딩이 강제하는 직렬 실행을 1 Arg 로 풀어 process-pool 병렬 가능케 |
| **컴포넌트 기하 시그니처** (반올림 부피+정렬 bbox+면수, tolerance-band) | 대형·비교 | 동일 볼트 dedup + 캐시 키 + 비교가능성 사전필터. 0.001mm 지터 대비 tolerance-band 해시 |
| **검출기 출력 재사용** (array 루프의 positions 인자) | 전 기둥 | array 루프가 검출기를 7× 재실행 → 3-4× 속도, identity 보존(positions=None 이면 byte-identical) |

## 단계 (각 단계 = 출하 가능한 증분, big-bang 아님)

### Phase 0 — 공유 기반 + 무위험 속도개선
전 항목 기본값 byte-identical → preserve_brep 35/55 + box baseline 무영향.
- geometry_deviation rigid 확장 + Hausdorff/rms/p95 를 corpus-regress 레코드·baseline 에
- plan_out_path Arg (기본 불변)
- array 루프 검출기 출력 재사용
- 컴포넌트 기하 시그니처 헬퍼
- 단면-커브 복원 헬퍼 (`_face_count_guard` 보호)

**Exit**: corpus-regress 양 모드 byte-identical; complex 9 레코드에 hausdorff 채워짐;
array 타이밍 ~1/7; identity 변환 → hausdorff ~0.

### Phase 1 — 기둥별 첫 출하 가치 (전부 Phase-0 hausdorff 게이트)
- **자유곡면**: `extract_outer_silhouette_profile` + `_pick_base_shape` base_profile 분기 → 11752 volume_delta 98.6%→<30% (hausdorff 무회귀); `accept_freeform_base()` revert-guard(나빠지면 box 복귀)
- **대형**: assembly RE 컴포넌트 인스턴스 dedup; plan_out_path 워커별 ProcessPool → RC_Buggy/KR600 900s 내(best-effort)
- **리포트**: `QualityReportV1` 스키마 + `emit_quality_report` JSON; `_dfm_verdict` 공정별 pass/marginal/fail (measured/estimate 등급 표시)
- **변형**: `identify_key_dimensions` (overall L/W/H, primary bore, key pitch, wall → 사용자가 만질 "주요 치수")
- **비교**: `register_bodies` (principal-axis seed + 모호성 해소 + Kabsch refine → 4×4 변환 + confidence); `feature_change_classify` (added/removed/moved/resized 부호델타)

**Exit**: 기둥마다 산출물 1개; 회귀 0.

### Phase 2 — 프리미티브를 완전 파이프라인으로 조립
- **자유곡면**: 진짜 loft 단면 복원(bbox-원 대체); `_classify_base_topology` 디스패처(prismatic/revolved/box)
- **비교**: `compare_parts` 매크로(import→extract→register→change_classify→deviation→mass델타→pmi_diff) + 유사도/family 분류(estimate)
- **변형**: `recover_design_relations`(mirror/counterbore/pattern 관계 그래프) + `apply_variant_drivers`(HOUSING_OD=50 → 일관 재생성)
- **리포트**: 헤드리스 단면+주석 PNG(pyvista off_screen, GL 없으면 skip-mark)
- **대형**: pockets 의 빠른 face-pair 경로를 detect_bosses/ribs 로 이식

**Exit**: compare_parts(A,A)→~1.0, compare_parts(A,1.5×A)→parametric_variant scale~1.5 (5쌍, 300s 내).

### Phase 3 — 게이트·제품화 표면·하니스 병렬화
- **대형**: corpus-regress `--workers N` 병렬(subprocess 격리, 워커별 plan env, 결정적 재정렬)
- **비교**: compare-pairs corpus 게이트 + `compare_pairs.json`(vary 라벨); sign-flip 버그 → 라벨 뒤집힘 exit 1
- **변형**: `generate_variant_family`(N 검증 변형 + fidelity-vs-1.0 표); normalize 5번째 관계-일관성 pass
- **리포트**: HTML 렌더러(jinja2, base64 PNG, measured/estimate 뱃지, DFM 칩) + `quality-report` CLI + report-snapshot baseline
- **자유곡면**: freeform 코퍼스 fixture + `box_freeform.json` (메커니즘별 정직 점수대)

**Exit**: 4 baseline 레인(preserve_brep/box/variant/compare) 그린·자가증명; HTML 리포트 렌더; --workers 8 == --workers 1 byte-identical.

### Phase 4 — 어렵고 불확실한 프런티어 (XL, 각자 정직한 fallback)
- **자유곡면**: `fit_bspline_surface_patch`(폐기된 bspline_faces 스키닝) — solidify 안 되면 **OPEN-shell 편차 리포트** + box 베이스 유지, fidelity 점수 주장 안 함; 진짜 sweep path+profile 복원
- **비교**: registration 강화(ICP, 대칭 가설 multi-start, time-boxed, 저신뢰 다운그레이드)
- **대형**: SOLID-aware 분할 + 통합 LOD 가드 + opt-in 디스크 캐시
- **변형**: standard-snap + `solve_driver_range`(해석적 envelope + rebuild 검증; freeform/loft 면 None)
- **리포트**: PDF 스파이크(reportlab vs print-HTML; weasyprint 는 승인 없이는 안 씀)

**Exit**: 각 항목이 fixture 목표 OR 정직한 fallback 달성, 어떤 baseline 에도 fake-accuracy 없음; KR600/RC_Buggy 완료 OR KNOWN-GAP.

## Critical path

geometry_deviation rigid + hausdorff-in-baseline (P0) → 단면-커브 헬퍼 (P0) →
`extract_outer_silhouette_profile` + base_profile + revert-guard (P1) →
진짜 loft/sweep 복원 + `_classify_base_topology` (P2) →
`fit_bspline_surface_patch` open-shell (P4).

자유곡면이 가장 길다 — 각 단계가 hausdorff 게이트가 있어야만 신뢰가능
(feature_fidelity_diff 0.15-tol 은 fake 가능). 단면-커브 헬퍼가 최고 fan-out
노드. register_bodies 는 병렬 임계 서브경로(feature_change_classify →
compare_parts → compare 게이트를 게이트하며, 그 회전 필요가
geometry_deviation rigid 를 P0 로 끌어올림).

## 권장 시작 (지금 만들 1-2개)

1. **geometry_deviation `align='rigid'`/`transform_4x4` + corpus-regress 레코드·baseline 에 hausdorff/rms/p95.** 먼저인 이유: 이후 모든 자유곡면/변형/비교 변경이 통과해야 하는 **필수 anti-fake 게이트**(0.15 tol 이 기하적으로 틀린 복원을 win 으로 처리하는 걸 막음), register_bodies 가 필요로 하는 회전 프리미티브, 리포트의 영역별 히트맵 소스 — **3 기둥, 1 additive backward-compatible 변경**이라 즉시 그린 출하.
2. **단면-커브 복원 헬퍼** (병렬): 실행 sink(PolygonSketch/CompositeSketch/loft ThruSections)가 이미 검증됨, 갭은 catalog→wire 절반뿐.

## 정직한 caveat (될 수도, 부분만 될 수도)

- **B-spline 자유곡면 → watertight solid 는 XL, OCCT 7.8/OCP 에서 신뢰성 있게 안 될 수 있음.** 정직한 산출물은 OPEN-shell 편차 리포트 + box 를 빌드가능 베이스로 유지. 유기 부품(Ventilator 0.30, 11752 0.047)은 volume_delta/hausdorff 는 개선되나 프리즘급 fidelity 엔 도달 못 함.
- **100+ 부품/4000+ 면 (KR600, RC_Buggy)은 dedup+병렬+캐시 후에도 타임아웃 가능.** dedup 은 중복 인스턴스에만 유효 — 148개가 전부 다르면 풀코스트. 모든 신규 baseline 레인에 KNOWN-GAP 으로 기록.
- **근대칭(QFN)·관성축 축퇴(원통/판) 부품 회전 정합이 어렵다** — principal-axis 고유벡터 부호/순열/대칭 모호성. 잘못 seating 하면 비교 diff 전체가 자신있게 틀림. axis_ambiguous + feature-fit fallback + registration_confidence 로 완화하나 XL 항목이 완전히 닫지 못할 수 있음.
- **feature_fidelity_diff 의 loft/sweep/revolve 매처는 느슨**(단일 primary-dim + centroid, tol 0.15) → 기하적으로 틀린 프로파일에도 match_ratio 가 오를 수 있음. **자유곡면 점수 주장 전 hausdorff 게이트 필수.**
- **DFM 판정 정확도는 내부 단면 측정 불균일에 상한**(벽두께 레이마치가 자유곡면서 놓침, draft 가 top/bottom skip). 판정은 measured|estimate 등급을 상속·표시, estimate 를 자신있는 pass/fail 로 격상 금지.
- **헤드리스 3D 렌더는 CI 리스크**: pyvista off_screen 은 로컬 OK, VTK 는 OpenGL 컨텍스트 필요 — Mesa/llvmpipe 없는 러너는 실패. JSON+3D없는 HTML 경로는 무의존성 유지, GL 없으면 렌더 테스트 skip-mark.
- **PDF 미해결**: weasyprint 미설치(Windows Cairo/Pango 부담), reportlab 저충실. 정직한 결론은 print-최적화 자가포함 HTML; weasyprint 는 명시 승인 없이 안 씀.
- **모든 병렬/캐시/dedup 변경은 OCCT sub-mm 드리프트 위험**(0.001mm 핀 지터): 반올림 기하를 tolerance-band 해시, plan_out_path 기본 byte-identical, standalone array 경로와 동일 axis_origin 필드 전달 — 아니면 immutable-identity/sibling 계약과 35/55 baseline 이 조용히 깨짐.

## 신규 스킬 총괄 (이 로드맵이 추가하는 것)

`extract_outer_silhouette_profile`, `extrude_profile_world`, `classify_base_topology`,
`fit_bspline_surface_patch` (자유곡면) · `emit_quality_report`, `dfm_verdict` (리포트) ·
`identify_key_dimensions`, `recover_design_relations`, `apply_variant_drivers`,
`generate_variant_family`, `solve_driver_range` (변형) ·
`register_bodies`, `feature_change_classify`, `compare_parts` (비교).
대형 기둥은 신규 스킬 0 — 전부 기존 검출기/하니스/실행기 인프라 개선.
