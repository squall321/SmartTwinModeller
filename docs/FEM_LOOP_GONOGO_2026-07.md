# FEM 루프 (roadmap 3-2) go/no-go — 판정: **NO-GO (v1)**

*2026-07-04. 판정자: 스파이크 프로브 (설치·벤치마크 없이 근거 확정).*

로드맵 3-2("gmsh tet mesh → CalculiX → 리포트")의 go/no-go 조건은:
> Go: 1주 스파이크에서 (a) Windows+CI ccx 바이너리 배포가 skip-if-absent 패턴으로
> 깨끗이 풀리고 (b) cantilever/plate 해석 벤치마크가 tolerance band 안이면 Go.
> 의존성 무게가 lean-policy를 깨면 No-Go(문서화된 외부 workflow로 대체).

## 프로브 결과 (2026-07-04)

| 확인 | 결과 |
|---|---|
| `import gmsh` (Python tet mesher) | **미설치** (ImportError) |
| `ccx` / CalculiX 솔버 바이너리 | **PATH·Program Files에 없음** |
| pyproject optional-deps에 FEM 그룹 | 없음 |

두 무거운 외부 컴포넌트가 모두 부재하여, 이 환경에서는 go 조건 (b)의 **벤치마크
검증 자체가 불가능**하다. 설치·검증 없이 솔버 연동 코드를 짜는 것은 "vibes로 개선
주장 금지" 하우스 룰 위반이므로 착수하지 않는다.

## 판정 근거: NO-GO — 그러나 기능 공백이 아님

FEM 루프를 **닫을 필요 자체가 낮다**. 이미 있는 것:

1. **`export_abaqus_inp_v2`** (fem_cae, 기존) — Abaqus/**CalculiX INP 덱**을 뽑는
   정문. 사용자 CAE팀은 실제로 **Abaqus를 쓴다** → 이 시스템의 FEM impact는 로드맵
   자체가 "sanity-check tier에 캡"이라 명시했고, INP 익스포트가 이미 그 tier를 채운다.
2. **`modal_frequency_estimate`** (기존) — **솔버 없는 해석적 modal 추정**. sanity
   교차검증용 lightweight 경로가 이미 존재.
3. `modal_analysis_setup` / `boundary_condition_tag` / `contact_pair` /
   `load_case_compose` / `export_mesh_for_fem` — 솔버에 넘길 셋업을 전부 태깅.

즉 이 시스템의 역할은 **"해석 준비 + 정문 익스포트"**이고, 실제 solve는 사용자의
Abaqus(또는 외부 CalculiX)가 담당하는 워크플로가 이미 완결돼 있다. 무게 있는
solver-in-the-loop을 내장하는 것은 lean-policy를 깨면서 얻는 것이 적다.

## 대체 (문서화된 외부 workflow)

```
[SmartTwin] analyze/generate → body_id/STEP
   → export_abaqus_inp_v2  (surface mesh + BC/load/material tags → .inp deck)
   → [외부] Abaqus 또는 CalculiX(ccx)로 solve
   → [외부] .frd/.odb 후처리
[SmartTwin] modal_frequency_estimate 로 1차 sanity 교차검증 (솔버 결과 vs 해석 추정)
```

## 재검토 트리거 (이후 Go로 전환할 조건)

아래가 **모두** 성립하면 3-2를 재론한다:
1. `gmsh`(BSD) + `ccx`(GPL) 바이너리를 CI+로컬에 skip-if-absent로 깨끗이 배포 가능함을
   실증 (optional-dep 그룹 `[fem]` + 바이너리 캐시);
2. cantilever/plate 벤치마크 2종이 해석해 대비 tolerance band 안 (예: 처짐 ±5%,
   1차 고유진동수 ±10%) — **자기채점 금지, 해석해 대비만**;
3. 사용자로부터 "in-house solve가 실제로 필요하다"는 요구가 있을 때 (현재는 Abaqus로
   충분하므로 pull이 없음).

이 셋 중 하나라도 미충족이면 verification-only(INP 익스포트 + 해석 추정)로 동결 유지.
