# reference — CAD/Mesh 처리 + reverse engineering

두 경로:
1. **CAD pipeline** (1순위, Rev 5) — Parasolid/STEP → 부품 자동 추출 + topology feature 인식
2. **Mesh pipeline** (Phase 9 일반화) — `.glb/.fbx` segmentation + ≥ 1mm feature 측정

## 자산 위치

```
fixtures/
├── simple_watch.step                 # build123d 합성, 부품 5개 어셈블리
├── simple_watch_housing_only.step
└── simple_watch_components/*.step

reference/galaxy_watch/                # 회사 컴 only (gitignore)
├── original.x_t                       # Parasolid OEM
├── converted.step                     # SpaceClaim 변환 결과 (AP242)
└── extracted/<part>.step              # 자동 분리

iphone/iphone_12_teardown.glb          # Phase 9 mesh 검증
```

## CAD pipeline

### 구현 위치 (Phase 3 v1 완료)

| Module | 역할 | 상태 |
|---|---|---|
| [[../src/phone_designer/reference/step_reader.py]] | XDE 어셈블리 + 부품 네이밍 + naming → category 분류 | ✓ |
| [[../src/phone_designer/reference/topology_analyzer.py]] | face surface type → FeatureCatalog (fillet/hole/chamfer) | ✓ v1 |
| [[../src/phone_designer/reference/feature_to_plan.py]] | FeatureCatalog → Plan 초안 (disc/slab + corner_r + hole) | ✓ v1 |

CLI: `phone-designer reproduce --reference <step> --out <step> [--part <name>] [--plan-out <yaml>]`

검증: [[../tests/reference/]] — 18/18 PASS (Phase 3 v1)
- step_reader: XDE 5 부품 추출 + 네이밍 보존 + naming → category
- topology_analyzer: Box(6 plane) + Box+hole(cylinder 검출) + disc bottom fillet(toroidal 검출)
- feature_to_plan: Box→rounded_slab, disc→disc_with_dome, bbox/corner_r 자동, plan executor 정상 실행

v2 (P1 backlog): pocket/plateau cluster, chamfer 각도, hole depth, polynomial pocket. [[backlog#Phase-3]] 참조.

### Parasolid 워크플로

PF-3 결정 (회사 컴 1회):

1. SpaceClaim 에서 `.x_t` 열기
2. File → Save As → STEP
3. 옵션:
   - Schema: **AP242** (XDE 어셈블리 + 네이밍 보존)
   - "Save Assembly Structure" ON
   - "Include Names" ON
4. 저장 → `reference/galaxy_watch/converted.step`

자동화는 v0.2 (SpaceClaim Python automation 또는 OpenCascade Parasolid reader 통합).

### STEP 어셈블리 분석

```python
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TDocStd import TDocStd_Document
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.TDF import TDF_LabelSequence
from OCP.TDataStd import TDataStd_Name

def read_xde_step(path: str) -> list[tuple[str, TopoDS_Shape, gp_Trsf]]:
    reader = STEPCAFControl_Reader()
    doc = TDocStd_Document(TCollection_ExtendedString("doc"))
    reader.ReadFile(path)
    reader.Transfer(doc)

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    labels = TDF_LabelSequence()
    shape_tool.GetFreeShapes(labels)

    parts = []
    for i in range(1, labels.Length() + 1):
        label = labels.Value(i)
        shape = shape_tool.GetShape_s(label)
        name = TDataStd_Name.Get_s(label).ToCString() if TDataStd_Name.IsSet_s(label) else f"part_{i}"
        loc = shape.Location()
        parts.append((name, shape, loc.Transformation()))

    return parts
```

[[src/phone_designer/reference/step_reader.py]] 의 핵심.

### 부품 분류

네이밍 → component category 매핑:

```python
NAMING_RULES = {
    r".*[Dd]isplay.*|.*[Pp]anel.*|.*OLED.*|.*LCD.*":   "display",
    r".*[Bb]atter.*":                                   "battery",
    r".*[Mm]ain.*[Bb]oard.*|.*PCB.*|.*[Ss]o[Cc].*":   "mainboard",
    r".*[Cc]rown.*":                                    "crown",
    r".*[Bb]utton.*|.*[Kk]ey.*":                       "button",
    r".*[Hh]ousing.*|.*[Cc]ase.*|.*[Ff]rame.*":        "housing",
    r".*[Cc]oil.*|.*[Cc]harg.*":                       "wireless_coil",
    r".*[Vv]ibr.*|.*[Hh]aptic.*":                      "haptic",
    r".*[Aa]ntenna.*":                                  "antenna",
    r".*[Ss]peaker.*":                                  "speaker",
    r".*[Mm]ic.*":                                      "mic",
    r".*[Ss]ensor.*|.*[Hh]eart.*|.*PPG.*":             "sensor",
    r".*[Ll]ug.*|.*[Ss]trap.*":                        "lug",
}
```

매칭 실패 = `unknown` → UI 에서 사용자 수동 분류.

### Topology 분석 → FeatureCatalog

```python
class TopologyAnalyzer:
    def analyze(self, shape: TopoDS_Shape) -> FeatureCatalog:
        # 1. face 순회 → surface type 분류
        for face in faces(shape):
            stype = BRepAdaptor_Surface(face).GetType()
            # GeomAbs_Plane, _Cylinder, _Cone, _Sphere, _Torus,
            # _BezierSurface, _BSplineSurface

        # 2. Feature 인식 알고리즘
        # Toroidal face + 인접 face 가 평면/원통 → fillet 후보 (R = minor radius)
        # Conical face + 인접 평면 → chamfer 후보 (width = 빗변, angle = 반각)
        # 깊이 있는 face 그룹 + 평면 base → pocket
        # 돌출 face 그룹 + 평면 base → plateau (camera bump / 크라운 plinth)
        # 원기둥 hole face → hole (D = radius * 2, depth = height)

        return FeatureCatalog(
            fillets=[FilletFeature(face=f, radius=r, edge_count=...), ...],
            chamfers=[...],
            pockets=[...],
            plateaus=[...],
            holes=[...],
        )
```

[[src/phone_designer/reference/topology_analyzer.py]].

### Feature → Plan (reverse engineer)

```python
def feature_to_plan(catalog: FeatureCatalog, housing_bbox: BoundingBox) -> Plan:
    plan = Plan(schema_version=1, plan_name="auto_from_reference")

    # 1. base
    if housing_bbox.is_circular():
        plan.add(disc_with_dome,
                 diameter_mm=housing_bbox.diameter,
                 height_mm=housing_bbox.height,
                 dome_rise_mm=detected_dome_rise(catalog),
                 corner_r_mm=detected_outer_r(catalog))
    else:
        plan.add(rounded_slab, ...)

    # 2. fillet (큰 R 부터)
    for f in sorted(catalog.fillets, key=lambda x: -x.radius):
        plan.add(fillet_edges_by_predicate,
                 selector=infer_selector(f.face),
                 radius_mm=f.radius)

    # 3. chamfer, pocket, plateau, hole, ...
    ...
    return plan
```

[[src/phone_designer/reference/feature_to_plan.py]].

생성된 plan 은 사용자 검토 후 [[concepts#plan|Plan Executor]] 로 실행. 결과를 reference 와
[[dev-test#face-level-회귀]] 로 비교.

### 검증 metric

| Metric | 임계값 (fixture) | 임계값 (OEM) |
|---|---|---|
| face count diff | ±15% | ±10% |
| edge count diff | ±15% | ±10% |
| volume diff | ±5% | ±2% |
| bbox diff | ±1.0mm | ±0.5mm |
| feature 위치 | ±0.5mm | ±0.3mm |

## Mesh pipeline (Phase 9, iPhone)

`.glb` 처리 — Rev 4 의 §5 mesh 파이프라인 유지. **CAD 경로의 보조**.

### 파이프라인

```
.glb (pygltflib + trimesh)
  ▼
mesh 통합 + 단위 환산
  ▼
PCA 정렬 (+Z=두께, +X=길이)
  ▼
표면 segmentation (face normal 클러스터링)
  ▼
특징 추출 (≥ 1mm feature 만)
  ▼
MeasurementReport
  ▼
plan 초안 (sub-mm 항목은 placeholder)
  ▼
사용자가 UI 에서 pick + 측정 도구로 sub-mm 보정
```

### 자동 측정 대상 (≥ 1mm feature, PF-3 mesh 측정 결과 반영)

- bbox 길이/너비/두께
- 외곽 코너 R (top-edge polyline 원호 fit)
- top/bottom chamfer 폭 (edge angle 히스토그램, ≥ 0.5mm)
- 카메라 bump silhouette + 높이 (≥ 1mm)
- 카메라 lens 위치/직경 (≥ 3mm)
- 측면 포트 cutout (≥ 4mm)

### 자동 측정 제외 (사용자 보정)

- 디스플레이 step-down 깊이 (0.4mm)
- 안테나 슬릿 폭 (0.5mm)
- 마이크/스피커 핀홀 (≤ 1mm)
- 표면 마감 디테일

### mesh 정밀도 (PF-6)

`docs/iphone_mesh_precision.md` 의 측정 결과 commit. 측정 가능 범위 임계값 임시 ≥ 1mm,
실측 후 갱신.

## 공통 인터페이스

CAD 와 mesh 경로 모두 `ReverseEngineerPlanner` 인터페이스로 통일:

```python
class ReverseEngineerPlanner(Protocol):
    def plan_from_reference(self, ref_path: Path) -> Plan: ...
```

구현체 2개:
- `CadReverseEngineer` (Rev 5, 1순위)
- `MeshReverseEngineer` (Rev 4, Phase 9)

LLM Editor / Planner 는 어느 경로에서 왔는지 무관하게 plan 을 받는다.
