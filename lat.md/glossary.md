# glossary — 용어 사전

알파벳 (한글 → 영어 우선 정렬).

## 한글 용어

| 용어 | 의미 | 상세 |
|---|---|---|
| 결정성 | 같은 plan + 같은 입력 → 같은 출력. Plan freeze 메커니즘으로 검증. | [[plan-determinism]] |
| 공정 budget | 사용자가 허용하는 공정 + 복잡도 + draft 엄격성 | [[manufacturing#manufacturingbudget]] |
| 기구물 | 폰/워치 housing 의 내부 구조물 (보스, 리브, 스냅, 안테나 슬릿 등) | [[components]] |
| 부품 (Component) | 디스플레이/배터리 등 폰을 구성하는 entity. parametric 모델 + 인터페이스 메타데이터. | [[concepts#component]] |
| 시나리오 | reproducible test definition. 1-커맨드 실행, 자동 로그/번들/메일. | [[dev-test#시나리오-러너]] |
| 시한 폭탄 | 사전 검증 안 하면 후폭풍 큰 가정 (A: OCCT history, B: cross-platform, C: caching, D: 네이밍, E: voxel) | [[risks#risk-register]] |
| 외피 | housing 의 외곽 (사용자가 보는 표면) | — |
| 추출 (extraction) | OEM CAD 어셈블리에서 부품을 자동으로 분리 + catalog 등록 | [[reference#step-어셈블리-분석]] |
| 합성 (synthesis) | 부품 배치 → housing 자동 생성. v0 rule-based, v1 LLM agentic. | [[components#합성-v0]] |

## 영어 / 약어

| 용어 | 의미 | 상세 |
|---|---|---|
| ADR | Architecture Decision Record. 본 프로젝트는 lat.md 의 [[decisions]] 에 누적. | [[decisions]] |
| Atomic skill | OCCT 호출 1회 + selector 1회 수준의 단일 변환 | [[concepts#atomic-vs-macro]] |
| build123d | Python CAD library, OCCT(OCP) wrapper. 본 프로젝트의 1차 CAD kernel adapter. | https://build123d.readthedocs.io |
| CAD | Computer-Aided Design. 본 프로젝트의 reference 입력 (Parasolid → STEP). | [[reference#cad-pipeline]] |
| DFM | Design for Manufacturing. v0 = wall thickness ray-march + draft + undercut. | [[manufacturing#dfm-v0]] |
| EntityHistoryMap | skill 의 apply() 가 반환하는 (원본 entity → 결과 entity) 매핑 | [[persistent-naming]] |
| FeatureCatalog | TopologyAnalyzer 가 STEP 에서 추출한 fillet/chamfer/pocket/plateau/hole 목록 | [[reference#topology-분석-→-featurecatalog]] |
| Freeze (selector_freeze) | plan step 의 selector 매칭 결과를 동결한 메타데이터. 재실행 시 mismatch 검출. | [[plan-determinism#selectorfreeze-구조]] |
| GLB | glTF binary. iPhone 12 teardown 의 reference 형식 (Phase 9 mesh 검증) | [[reference#mesh-pipeline]] |
| Housing | 폰/워치의 외피 + 내부 구조물 전체 | — |
| HistoryRule | history map propagation 의 enum (MODIFIED_INHERIT / SPLIT_BRANCH / CONSUMED / GENERATED_NEW) | [[persistent-naming#historyrule-enum]] |
| lat.md | 본 지식 그래프 시스템. https://github.com/1st1/lat.md | [[lat]] |
| LLM | Large Language Model. 본 프로젝트는 Claude Opus 4.7. | [[llm]] |
| Macro skill | 2개 이상 atomic skill 시퀀스의 명명. manifest 에 `expansion` 명시. | [[concepts#atomic-vs-macro]] |
| Manifest | 모든 skill / selector / component / process 메타데이터의 머신 리더블 catalog. LLM tool schema + DFM 규칙의 single source. | [[concepts#manifest]] |
| OCCT | Open CASCADE Technology. CAD kernel. build123d 가 OCP 로 wrapping. | https://dev.opencascade.org |
| OCP | OpenCascade Python bindings (cadquery-ocp). build123d 의 backend. | https://github.com/CadQuery/OCP |
| OEM CAD | Original Equipment Manufacturer 의 CAD (Galaxy Watch Parasolid). 회사 컴 only. | [[reference#cad-pipeline]] |
| Parasolid | Siemens 의 CAD kernel 형식 (.x_t, .x_b). 회사 컴의 OEM Galaxy Watch CAD 형식. | [[reference#parasolid-워크플로]] |
| PF (Pre-flight) | Phase 0 이전 결정/검증 항목. 본 프로젝트는 PF-1 ~ PF-7. | [[decisions]] |
| Plan | skill 호출의 순서화된 시퀀스. YAML 직렬화. schema_version 관리. | [[concepts#plan]] |
| PoC | Proof of Concept. PF-1 / PF-2 에서 작성. | [[persistent-naming#poc-범위-phase-0]] |
| pyvistaqt | PySide6 + PyVista 통합. QtInteractor 가 viewport. | https://qtdocs.pyvista.org |
| Reproduction mode | reference CAD/mesh 를 우리 skill 라이브러리로 재현 | [[project#무엇을-만드는가]] |
| Selector | face/edge/vertex 부분집합을 선언적으로 지정하는 predicate | [[concepts#selector]] |
| Skill | 입력 Part + 인자 + Selector → 출력 Part 의 순수 함수 + 메타데이터 | [[concepts#skill]] |
| SkillSpec | Skill 의 메타데이터 데이터클래스 (level, args, history_rules, manufacturing, ...) | [[concepts#skillspec-메타데이터-스키마]] |
| STEP | Standard for the Exchange of Product Data (ISO 10303). CAD 교환 표준. AP242 사용. | — |
| STEPCAFControl | OCCT 의 XDE STEP reader/writer. 어셈블리 + 네이밍 보존. | [[reference#step-어셈블리-분석]] |
| Tag | Entity 에 부여된 의미 라벨. history map 으로 propagated. selector 의 가장 안정한 종류. | [[persistent-naming]] |
| Topology | CAD 의 위상 구조 (face, edge, vertex 의 연결). build123d / OCP 의 핵심. | — |
| trimesh | Python mesh library. iPhone glb 처리. | https://trimesh.org |
| VTK | Visualization Toolkit. PyVista 의 backend. GPU 가속 3D 시각화. | https://vtk.org |
| Wiki link | lat.md 의 `[[file#section]]` 또는 `[[file]]` 형식 내부 링크 | [[lat#latmd-규약-본-프로젝트]] |
| XDE | eXtended Data Exchange. STEP 의 어셈블리 + 네이밍 + 색상 보존 확장. | [[reference#step-어셈블리-분석]] |

## 좌표·단위 규약

- 단위: mm (모든 길이), deg (모든 각도)
- 좌표계: [[components#housing-local-좌표계|housing-local]]
- 부동소수 톨러런스: ε = 1e-6 mm

## 환경 변수

[[setup#환경-변수]] 표 참조.

## CLI 커맨드

```powershell
phone-designer test --scenario <name> [--mail]
phone-designer generate --plan <plan.yaml> --out <out.step>
phone-designer reproduce --reference <ref.step|glb> --out <out.step>
phone-designer validate --plan <plan.yaml> --budget <budget.yaml>
phone-designer config mail
phone-designer config api-key
```

세부 옵션은 `phone-designer <cmd> --help`.
