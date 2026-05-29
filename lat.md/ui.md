# ui — 데스크탑 UI (PySide6 + pyvistaqt)

## Stack

| 컴포넌트 | 라이브러리 |
|---|---|
| Application framework | PySide6 (LGPL — PF-4) |
| 3D viewport | pyvistaqt.QtInteractor (VTK/GPU) |
| Plot/visualization | PyVista (VTK 위) |
| Picking / selection | VTK 의 cell picker → [[concepts#selector|Selector]] 자동 제안 |

## Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│  File  Edit  View  Plan  Components  Process  Test  Help             │
├────────────────┬─────────────────────────────────────┬───────────────┤
│ Components     │                                     │  Plan Editor  │
│  ▸ Watch       │                                     │  ┌─────────┐  │
│   ▸ Displays   │                                     │  │ Step 1  │  │
│   ▸ Batteries  │                                     │  │ disc_w_ │  │
│   ▸ Crowns     │                                     │  │ dome    │  │
│   ▸ Coils      │      VTK Viewport (GPU)             │  └─────────┘  │
│  ▸ Extracted   │                                     │  ┌─────────┐  │
│   (OEM CAD)    │      [Pick / Measure / Slice]       │  │ Step 2  │  │
│                │                                     │  │ chamfer │  │
├────────────────┤                                     │  │ ✓freeze │  │
│ Process Budget │                                     │  └─────────┘  │
│  ☑ die_cast_al │                                     │   + Add step  │
│  ☑ cnc_3axis   │                                     │               │
│  Complexity:   │                                     │               │
│   ◉ medium     │                                     │               │
├────────────────┴─────────────────────────────────────┴───────────────┤
│ Chat (LLM)                                            │  DFM Report  │
│ > Make housing for placed components, al die-cast     │  ⚠ Wall 0.7  │
│   draft strict                                        │    at boss 3 │
│                                                       │  ⚠ Undercut  │
└──────────────────────────────────────────────────────┴───────────────┘
```

### Panels

| Panel | 목적 | 상세 |
|---|---|---|
| Component Palette | 부품 drag → viewport 배치 | catalogs/ + extracted/ 분리 탭 |
| Process Budget | 공정·복잡도·draft 설정 | [[manufacturing#manufacturingbudget]] |
| Viewport | 3D 시각화 + picking | offscreen 모드 = [[dev-test#viewport-snapshot-자동-캡처]] |
| Plan Editor | step 카드 list, drag-reorder | 각 step 의 freeze 상태 아이콘 |
| Chat | LLM 대화 | offline 시 disabled |
| DFM Report | 위반 highlight + confidence | [[manufacturing#dfm-report]] |
| Status Bar | 모드 (offline/online), Phase, 비용 | |

## Undo / Redo

### Plan-level (Ctrl+Z 의 default)

Plan 의 history stack. step 추가/제거/수정 단위로 undo.

LLM 이 만든 plan 변경도 plan-level stack 에 들어가므로 Ctrl+Z 로 되돌릴 수 있음.

```python
class PlanUndoStack:
    def push(self, plan: Plan, description: str): ...
    def undo(self) -> Plan: ...
    def redo(self) -> Plan: ...
```

### Editor-level

위젯 안에서만 (텍스트 박스, slider). Qt 의 표준 `QUndoStack` 사용.

Ctrl+Z 의 우선순위: focused widget 에 editor-level stack 있으면 그것, 없으면 plan-level.

## Picking → Selector 자동 제안

사용자가 viewport 에서 face/edge 클릭 시:
1. VTK cell picker 가 picked entity 확인
2. 그 entity 의 특성 분석:
   - 위치 (axis-aligned? top/bottom/side?)
   - 크기 (length, area)
   - tag 가 붙어있는가
3. 후보 selector 제안:

```
사용자가 top face 클릭 →
  제안 1: {kind: face_named, name: top}              # 가장 안정
  제안 2: {kind: faces_by_normal, direction: [0,0,1]}
  제안 3: {kind: faces_by_area, min: 12, max: 14}
```

LLM Chat 에 이 picked context 가 자동 첨부 → "여기에 보스 4개" 같은 자연어 가능.

[[src/phone_designer/viz/selection.py]].

## Error mapping (친절한 OCCT 에러)

OCCT 의 cryptic 에러를 사용자 친화 메시지로 변환.

```python
# src/phone_designer/ui/error_mapping.py
ERROR_MAPPING = [
    (r"BRepFillet_MakeFillet failed",
     "Fillet radius too large or edges incompatible. "
     "Try smaller radius or check that edges meet at convex/concave consistent corners."),
    (r"BRepBoolean failed",
     "Boolean operation produced no result — bodies may not intersect or share coincident faces."),
    (r"BRepFeat_MakePrism: invalid parameter",
     "Pocket depth exceeds local body thickness — adjust depth or position."),
    (r"BRepOffsetAPI_MakeOffsetShape failed",
     "Surface offset failed — likely due to small features being eaten by the offset."),
    (r"Standard_ConstructionError",
     "Geometric construction error — usually invalid arguments (zero/negative dimensions, "
     "degenerate sketch)."),
    # ~20 known patterns, 점진 확장
]

def map_error(raw: str) -> str:
    for pattern, friendly in ERROR_MAPPING:
        if re.search(pattern, raw):
            return friendly
    return None  # 없으면 raw 그대로
```

Plan editor 의 실패 step 카드:
```
┌─────────────────────────────────────┐
│ Step 7: extrude_pocket        ✗    │
│ Pocket depth exceeds local body     │
│ thickness — adjust depth or pos.    │
│ [show raw error ▼] [edit] [delete]  │
└─────────────────────────────────────┘
```

## 다국어 (i18n)

모든 UI 라벨을 Qt `tr()` 래핑.

```python
# src/phone_designer/ui/main_window.py
self.add_step_button = QPushButton(self.tr("Add Step"))
```

PO 파일 2개:
- `src/phone_designer/ui/i18n/en_US.po`
- `src/phone_designer/ui/i18n/ko_KR.po`

런타임 전환: Settings → Language → KO/EN. 재시작 없이 적용.

채팅 LLM 응답 언어는 사용자 입력 언어를 따름 (별도, LLM 가 자동 결정).

## Plan autosave + recovery

- 매 step 실행 후 `~/.phone_designer/autosave/<timestamp>.yaml` 저장
- 시작 시 마지막 autosave 자동 로드 옵션 (다이얼로그)
- 30일 후 autosave 자동 정리 (config 가능)

## Test 메뉴

UI 에서 시나리오 실행 가능 (회사 컴 핵심 기능):

```
Test ▶
  ├── Run Scenario...        # 드롭다운에서 선택, 1-click 실행
  ├── Make Bundle            # 직전 결과를 zip
  ├── Send Report by Mail    # SMTP 전송
  ├── Configure Mail...      # 자격증명 등록
  └── View Run Logs          # run_logs/ 디렉토리 열기
```

[[dev-test#시나리오-러너]] 와 1:1 대응.

## Status Bar

| 영역 | 표시 |
|---|---|
| 좌측 | 현재 Plan 이름 + step 수 |
| 중앙 | 현재 Phase (예: "Editing", "Synthesizing", "Validating DFM") |
| 우측 | LLM 모드 (Online/Offline) + 누적 비용 |

비용 표시 = 현재 세션 누적, 한도 80% 도달 시 노란색, 100% 시 빨간색 + 비활성.

## Headless mode

UI 없이 CLI 만으로 모든 핵심 동작 가능:

```powershell
python -m phone_designer generate --plan plans/simple_watch.yaml --out out.step
python -m phone_designer reproduce --reference fixtures/simple_watch.step --out auto.step
python -m phone_designer validate --plan plans/x.yaml --budget budgets/al.yaml
python -m phone_designer test --scenario <name> [--mail]
```

회사 컴은 GUI 보다 headless + 메일이 주요 워크플로.

## offscreen 캡처 (회사 컴 자동 번들용)

VTK 의 offscreen rendering 으로 PNG 자동 생성:

```python
import pyvista as pv

plotter = pv.Plotter(off_screen=True, window_size=[1920, 1080])
plotter.add_mesh(polydata)
plotter.show(screenshot="step_001_iso.png")
```

각 시나리오의 매 step 후 4-view (iso/top/side/front) 자동 캡처 → `run_logs/<ts>/screenshots/`.
