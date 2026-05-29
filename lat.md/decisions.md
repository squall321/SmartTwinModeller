# decisions — Pre-flight + Architecture Decision Records

ADR 식으로 누적. 결정 후 변경되면 새 항목으로 (이전 항목 deprecated 표기).

---

## PF-1 — Persistent Naming (OCCT history map)

**상태**: Accepted (Rev 4)
**시점**: Phase 0
**결정**: history map propagation 을 단순 string 이 아닌 `HistoryRule` enum (4종) 으로 표현.
70% skill 에서 동작, 나머지 30% 는 fallback chain (tagged → face_named → position) + 사용자 경고.

**대안**:
- 100% propagation 시도 → OCCT 의 known limitation, 박사과정 연구 수준
- propagation 없이 모두 position-based selector → cross-platform / 변경 robust 성 결정성 약함

**상세 spec**: [[persistent-naming]].

---

## PF-2 — Plan Determinism

**상태**: Accepted (Rev 4)
**시점**: Phase 0
**결정**: 결정성 default = `strict` (same-machine). cross-platform 은 best-effort.
selector 매칭 결과를 `(matched_count, topology_signature)` 로 plan 에 freeze.

**대안**:
- strict cross-platform → OCCT 의 known limitation 으로 깨질 가능성 높음
- freeze 없이 매번 selector 재실행 → mismatch 검출 불가

**상세 spec**: [[plan-determinism]].

---

## PF-3 — Parasolid → STEP 변환 + CAD 정밀도

**상태**: Accepted (Rev 5)
**시점**: Phase 0 (회사 컴 1회)
**결정**:
- 변환 = SpaceClaim manual 1회. 자동화는 v0.2 (SpaceClaim Python automation 또는 OpenCascade Parasolid reader 통합)
- 변환 옵션: AP242, "Save Assembly Structure" ON, "Include Names" ON
- 정밀도 = face/edge 직접 측정 (mesh 의 ±0.5mm 가 아닌 CAD ±0.01mm)

**대안**:
- Parasolid SDK 라이선스 — 유료, 사내 채택 어려움
- FreeCAD 의 OpenCascade Parasolid reader — LGPL, 정확성 검증 필요
- CadExchanger CLI — 유료

**상세**: [[reference#parasolid-워크플로]].

---

## PF-4 — UI 프레임워크: PySide6

**상태**: Accepted (Rev 4, Rev 6 재확인)
**시점**: Phase 0
**결정**: PySide6 (Qt for Python, LGPL).
**기각**: PyQt6 (GPL/상용 듀얼 라이선스, 사내 외 배포 시 문제).

**대안 비교**:
| 프레임워크 | 라이선스 | API 호환 | 비고 |
|---|---|---|---|
| PySide6 | LGPL | Qt 표준 | 채택 |
| PyQt6 | GPL/상용 | Qt 표준 | 기각 (배포 라이선스) |
| Tkinter | stdlib | 안 됨 | 시각화 한계 |
| Dear PyGui | MIT | 비표준 | 생태계 작음 |

---

## PF-5 — Atomic vs Macro Skill 분류

**상태**: Accepted (Rev 4)
**시점**: Phase 0
**결정**:
- **Atomic**: OCCT 호출 1회 + selector 1회
- **Macro**: 2개 이상 atomic 시퀀스의 명명. manifest 에 `expansion: [...]`
- 모든 skill 의 SkillSpec 에 `level` 필드 필수
- Planner 정책: 외피/composition → macro, edit/agentic → atomic

**상세**: [[concepts#atomic-vs-macro]], [[skills#atomic-vs-macro]].

---

## PF-6 — Secondary Reference (iPhone glb)

**상태**: Accepted (Rev 5)
**시점**: Phase 0 (집 컴)
**결정**: 보유한 `iphone/iphone_12_teardown.glb` 를 mesh pipeline 일반화 검증용으로 사용.
Phase 9 의 입력. PF-3 와 별도 정밀도 측정.

**대안**:
- 다른 워치 mesh — Phase 9 에 워치 1순위 검증이 의미 약화 (이미 Galaxy Watch CAD 가 1순위)
- mesh 검증 생략 — mesh pipeline 자체가 미검증 → 폰 reference 활용 불가

**상세**: [[reference#mesh-pipeline]], [[reference#mesh-정밀도]].

---

## PF-7 — Voxel Ground-Structure Spike

**상태**: Pending Spike (Rev 4)
**시점**: Phase 5 말 1주 spike
**결정 후보**:
- voxel + greedy
- Delaunay tetrahedralization of mount points
- skeleton (medial axis of void)
- scope 축소 (ribbing 자동화 제외, 사용자 manual)

spike 측정 항목:
- 해상도 별 후보 segment 수, wall-clock, 메모리
- 결과 ribbing 의 시각 sanity
- OEM ribbing 과의 비교 가능성

**대안 비교**: [[components#합성-v0]].

---

## ADR-1 — Web UI 폐기, 데스크탑 (Rev 2)

**상태**: Accepted (Rev 2)
**결정**: 단일 사용자 데스크탑 (PyVista + Qt). Three.js / FastAPI 폐기.

**사유**:
- 큰 tessellation 의 HTTP 직렬화 비용
- viewport picking real-time
- 단일 사용자 가정
- in-process 가 단순 + 안정

---

## ADR-2 — FBX 의존성 (bpy) 폐기 (Rev 4)

**상태**: Accepted (Rev 4)
**결정**: FBX 지원 안 함. glb only.

**사유**:
- iphone teardown 에서 glb 와 fbx 가 같은 데이터
- bpy = 200MB+ 의존성, 설치 까다로움
- mesh pipeline 은 Phase 9 의 부수적, FBX 필요성 미증명

---

## ADR-3 — Reference 변경: Galaxy Watch + OEM Parasolid (Rev 5)

**상태**: Accepted (Rev 5)
**결정**: 1순위 = Galaxy Watch (Parasolid OEM CAD + 부품 네이밍). 폰은 Phase 9 의 mesh 일반화 검증.

**사유**:
- 워치 부피 1/10 → iteration 빠름
- 워치 부품 절반 → 합성 검증 단순
- OEM CAD ±0.01mm → mesh ±0.5mm 보다 정밀
- 부품 네이밍 보존 → component 자동 추출 가능
- face-level 정량 검증 가능

[[project#무엇을-만드는가]] 참조.

---

## ADR-4 — 환경 분리: 집(Dev) / 회사(Test) (Rev 6)

**상태**: Accepted (Rev 6)
**결정**:
- 집 = 개발 + 자체 시나리오 (fixture 만)
- 회사 = OEM CAD 테스트
- 메일 SMTP 로 회사 → 집 zip 번들
- 회사 컴에서 사용자 직접 디버깅 안 함
- 집 컴에서 자체 검증 최대화

**핵심 인프라**:
- [[dev-test#로깅-시스템]] JSON Lines + viewport snapshot
- [[dev-test#시나리오-러너]] 1-커맨드
- [[dev-test#자동-번들-메일]] SMTP

---

## ADR-5 — lat.md 지식 그래프 도입 (Rev 6)

**상태**: Accepted (Rev 6)
**결정**: 단일 PHONE_DESIGNER_PLAN.md (~700줄) 를 lat.md/ 의 토픽별 ~20 파일 + `[[wiki#link]]` 로 분해.

**사유**:
- 단일 파일이 700줄 넘어가서 navigation 어려움
- 회사 컴 1회 셋업 시 토픽별 참조가 효율적
- 결정 사항 (PF, ADR) 누적 가능
- LLM context 도 토픽별 참조 가능 (필요한 것만 load)

[[lat]] (index) 참조.

---

## 향후 결정 예정 (open)

[[risks#open-questions]] 의 항목들은 결정 시점이 되면 본 페이지에 추가:

| ID | 항목 | 결정 시점 |
|---|---|---|
| ADR-OPEN-1 | CAD 백엔드 보조 (build123d only vs cadquery 동시 vs OCP 직접) | Phase 1 |
| ADR-OPEN-2 | 합성 평가 v1 방식 | Phase 8 |
| ADR-OPEN-3 | DFM v0.3 정확도 향상 방식 | Phase 5 종료 |
| ADR-OPEN-4 | 공정 카탈로그 확장 시점 (sintering, MIM, ceramic) | Phase 5 이후 |
| ADR-OPEN-5 | Parasolid 변환 자동화 (v0.2) | v0.2 시점 |
| ADR-OPEN-6 | MCP 노출 여부 | Phase 8 이후 |
| ADR-OPEN-7 | 리포지토리 위치 (SmartTwinModeller sub vs 별도) | Phase 0 시작 시 |
| ADR-OPEN-8 | LLM 모델 변경 (Opus 고정 vs Sonnet fallback) | Phase 7 dry-run |
| ADR-OPEN-9 | 부품 메타데이터 보강 UX | Phase 6 |
| ADR-OPEN-10 | 두 번째 워치 (다른 사이즈/모델/Apple Watch) | Phase 9 이후 |
