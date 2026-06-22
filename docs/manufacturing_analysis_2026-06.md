# 제조성 분석 (Manufacturing RE) 능력 — 정리 (2026-06)

측정된 형상을 **설계자/견적자가 실제로 쓰는 숫자**(원가·공차·끼워맞춤·판금
전개 + **공정 선정**)로 변환하는 inspect 능력군. 전부 `analyze_part` front-door + CLI 플래그로
통합되고, **전 detector가 161-파일 corpus로 검증**되었으며, anti-fake-accuracy
원칙대로 **유효성 밖이면 정직하게 flag**(grade / reliability) 한다.

> **상태 (2026-06-21):** 5 능력 전부 등록·통합·corpus 검증·HTML 렌더링 완료.
> 381 스킬. 전체 스위트 1900 passed (headless). 합성 fixture가 아니라 실제
> corpus에서 검증된 상태.

---

## 1. `estimate_cost` — 정량 단가 + 사이클타임 (grade: estimate)

측정 부피·피처수·벽두께·blank를 **$/개 + 사이클타임**으로. 공정군:

| process | 모델 | 비고 |
|---|---|---|
| `cnc_3axis` / `cnc_5axis` | 소재 + (셋업 + 러프[stock/MRR] + Σ피처분) × rate; 5축 ×2.5 | 기존 |
| `injection_mold_pa` | 소재 + cycle(벽두께) × rate + 툴링/lot | 기존 |
| `sheet_laser_brake` (기본 판금) | 소재(blank×t×scrap) + 레이저컷(윤곽/speed(t)) + 핸들링 + 프레스브레이크(n_forms×8s) | **신규** |
| `sheet_turret_brake` | 컷을 hit수(홀+윤곽 nibble)로, 브레이크 동일 | **신규** |
| `sheet_progressive_die` (스탬핑) | 소재 + 스탬프머신(0.5s/stroke) + 진행다이 툴링(base$8k+$4k/station)/lot | **신규** |

- **레이저 속도**: `10000·(1mm/t)^0.8` mm/min, [500,18000] clamp — 실제는 1/t보다
  *느리게* 감소(설계 패널의 adversarial 검증이 선형 1/t 오류를 잡아 거듭제곱으로 수정).
- **cut-perimeter 4-tier fallback**: 전개길이×폭 사각형 → 면적/폭 → √면적 정사각 →
  bbox-면. 선택된 tier를 항상 assumptions에 기록. 윤곽 **하한**(홀은 pierce만, 미반영).
- **reliability**: `ok | degenerate_geometry | below_scale | above_scale`(부피 검증)
  + 판금 전용 `not_sheet_metal`(추정 무효 — CNC/사출로) ·
  `brake_infeasible_forming`(<15mm 몸체에 굽힘 6+ → 핸드브레이크는 허구, 스탬핑으로).
- **crossover**: laser_brake가 스탬핑 분기점 lot을 `drivers.stamping_crossover_lot`로 제시.

**검증 (USB-A 실드, t0.3, 굽힘12):** CNC $49.90(비현실) → laser+brake $4.49(brake-
infeasible flag) / 스탬핑 $1.71@100k, crossover ~37k. 평판 와셔 $0.36(브레이크 없음).
비-판금 → `not_sheet_metal`.

CLI: `analyze … --estimate-cost --cost-process sheet_laser_brake --cost-material stainless`

---

## 2. `recognize_fits` — ISO 286 공차 + 표준 끼워맞춤 (grade: estimate)

원통 피처(보어·보스)에 ISO 286 공차밴드 + 역할별 표준 끼워맞춤 추천.
- 공차 **크기는 ISO 286 표준 참조값**(IT등급표 + g/f/h/k/n/p/s 기본편차 하드코딩,
  반대쪽 경계는 IT에서 *유도* → 셀당 표값 1개 → 정확값). 끼워맞춤 **선택은 휴리스틱**.
- 두 모드: 추천(role→H7/g6 등) · **인식**(`mating_diameter_mm`→실측 틈새 + 최근접 표준).
- corpus 검증: 161파일 0 이상치(최대 Ø46.3mm), 수정 불필요.

CLI: `analyze … --recognize-fits --fit-role clearance|transition|press`

---

## 3. `measure_assembly_fit` — 두 솔리드 간 실측 끼워맞춤 (grade: measured)

어셈블리(솔리드 ≥2)에서 동축 보어↔축(cylindrical) 또는 동평면 슬롯↔키(prismatic)를
매칭해 **실제 틈새 측정**. clearance + fit_type이 형상에서 측정되므로 `measured`.
- 보어/축: 반경 point-inside 프로브. 슬롯/키: **외향 법선 방향**(벽은 안쪽, 키는 바깥).
- corpus 검증이 잡은 것 + 수정: dedup(솔리드쌍+Ø+축, as1_pe_203 14→7) · 불가능
  억지끼움(>5% 명목) 거부 · **CAD는 명목치 모델링**(틈새 0)→ clr≈0은 `nominal`-coincident로
  정직하게 flag(transition 단정 안 함). fit_type 집계 + max_fits cap.

CLI: `analyze … --measure-fits`

---

## 4. `detect_sheet_metal` — 판금 인식 + 굽힘테이블 + 전개도 (grade: estimate)

상수두께 굽힘판 인식 + 두께·굽힘(반경/각도/굽힘선/보정)·헴/모서리라운드·**전개 blank**.
- 굽힘각 = 굽힘 실린더의 **호(arc) 범위**(법선 부호 모호성 없음). 전개길이 = blank면적/폭
  (단일 굽힘축). 헴 vs 모서리라운드 = 플랜지 오프셋(≈t→라운드, ≥1.5t→헴).
- **정밀도(corpus 검증):** 게이지 [0.15,6.0]mm(t=254mm 어셈블리 제거) · 굽힘선
  ≥1.5×t(IC 리드 가짜굽힘 제거) · **정직한 confidence**(성형=높음, 평판=`flat_unformed`
  + 낮음 — 평판 박판은 레이저컷/머시닝/몰딩과 기하 구분 불가). 환원불가 모호성(핀·리드프레임·
  풀리홈이 굽힘과 동일 국소기하)은 assumptions에 공개.
- 한계: 진짜 2D 전개 외곽선(컷아웃 포함) 미생성; 닫힌 헴(gap≈t).

CLI: `analyze … --sheet-metal`

---

## 5. `emit_parametric_script` — 편집가능 build123d 스크립트 (RE)

복원 결과를 다시 편집 가능한 build123d `.py`로(명명 파라미터 + 관계기반 피처 히스토리:
linear/circular 패턴 루프, mirror, revolve base). `verify=True`는 스크립트를 실행해
geometry_deviation **HAUSDORFF**로 채점(match_ratio 아님). CLI: `script <part> -o model.py`.

---

## 6. `recommend_process` — 비용순위·실현성-게이트 공정 선정 (grade: estimate, 정점)

부품 + 생산량 + 재질 → **가장 싸고 실현가능한 공정**을 비용-vs-생산량 crossover와 함께 추천.
위 3개(estimate_cost·dfm_verdict·detect_sheet_metal)를 **종합**하는 상위 능력. 다중-에이전트
설계 패널(실제 catalog/소스 read)이 설계·적대적 검증.

- **실현성 우선:** 후보를 하나의 상태로 융합 (viable < viable_marginal < unproven <
  not_applicable < infeasible). **infeasible는 절대 추천 안 함.**
- 정직한 게이트 (패널 + corpus 검증으로 확립):
  - **성형 판금**은 SHEET 공정 추천 — CNC/사출은 not_applicable(굽힌 실드를 솔리드에서
    절삭/몰딩 불가; 초기 버그 $124 CNC를 수정).
  - dfm **임계 미달**(min-wall 0.3<0.4)은 *marginal 경고*지 불가 아님(실제 0.3mm 실드 존재);
    사출 언더컷(사이드액션 없이)·brake-infeasible·진성 degenerate만 hard-infeasible.
  - estimate_cost 과부하 flag를 부피로 구분(degenerate는 부피≤0만; below_scale→marginal).
  - **die_cast_al / 3d_printing**은 dfm 스펙은 있으나 비용모델 없음 → **절대 가격 안 매김**
    (estimate_cost가 조용히 CNC 가격 매김), unpriced advisory로만.
- **성능:** 모든 estimate_cost 모델이 정확히 `unit(L)=base+T/L`(상각항 1개) → 전체 생산량
  사다리 + crossover lot을 공정당 2호출로 **해석적(closed-form, 정확)** 계산; not_applicable
  후보는 비용호출 없이 게이트. (테스트 55분 → 26초.)
- **검증:** USB-A 실드 → sheet_turret_brake@1k → sheet_progressive_die@500k(crossover
  ~36.7k); 평판 → 스탬핑 최저, winners 생산량별 turret→laser→stamping; 솔리드블록 → 판금
  not_applicable. grade 'estimate'를 macro + 모든 ranking row에 assert.

출력: `process_recommendation` {recommendation, ranking[], excluded[], cost_matrix,
winner_by_lot, crossovers[], advisories_unpriced[], overall_flag, confidence_note, …}.
(주의: 복잡 판금부품은 estimate_cost 내부 재검출로 느릴 수 있음 — cost_hint=3.0.)

## front-door 통합 (`analyze_part`)

전부 OFF 기본의 선택 stage(7–11)로 통합 — 기본 리포트 golden은 byte-identical.
켜면 구조화 출력 + **HTML "Manufacturing analysis" 섹션**(원가·공차·판금·어셈블리끼워맞춤,
각 grade/reliability 표시)에 렌더링.

```
analyze part.step -o report.html \
  --estimate-cost --cost-process sheet_laser_brake --cost-material stainless \
  --recognize-fits --fit-role clearance \
  --sheet-metal --measure-fits --emit-script
```

---

## 정직성 원칙 (anti-fake-accuracy)

1. **grade**: `measured`(실측: assembly fit) vs `estimate`(휴리스틱/가정: cost·fits·sheet).
2. **reliability flag**: 유효성 밖(비-판금/degenerate/scale/brake-infeasible)이면 숫자를
   내되 **정직하게 flag**, 조용히 확신하지 않음.
3. **corpus 검증 루프**: 전체 corpus에 돌려 → 원칙적 오탐 수정 → 환원불가 모호성은
   confidence/assumptions로 노출 → 대표 케이스 회귀 가드로 고정. brittle 규칙으로
   억지로 정밀도 올리지 않음.
4. **재구성 win은 HAUSDORFF로만 주장**(match_ratio 아님).

각 능력의 detailed 한계는 해당 스킬 docstring + 출력 `assumptions[]`에 명시.
