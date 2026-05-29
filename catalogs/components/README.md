# catalogs/components/

Component 카탈로그 — 사람이 작성한 일반화 부품. `source: CATALOG`.

OEM CAD 에서 자동 추출된 부품은 `catalogs/components/extracted/<oem_name>/` 에 저장 (gitignore).

## 디렉토리

```
catalogs/components/
├── watch/
│   ├── displays/        — 디스플레이
│   ├── batteries/
│   ├── crowns/          — 회전 크라운
│   ├── sensors/         — PPG / 광학 / 모션
│   └── coils/           — 무선충전
└── phone/               — Phase 9 후
    ├── displays/
    ├── batteries/
    ├── cameras/
    └── ports/
```

## yaml 형식

`Component` Pydantic 모델 — [[../../src/phone_designer/components/model.py]].

핵심 필드:
- `name`, `category`
- `bbox` (length / width / thickness / is_circular)
- `pose` (housing-local 좌표, 사용자가 배치 시 갱신)
- `clearance` (side / back / top / thermal_zone)
- `mount_interface` (kind = screw_boss | adhesive_perimeter | snap_fit | press_fit)
- `ports` (외부 노출 — requires_housing_window / cutout + window_shape)
- `process_constraints` (옵션)

## 등록 부품 (Phase 6 초기)

| Category | Name | bbox (mm) |
|---|---|---|
| display | Galaxy Watch 44 AMOLED | D33.4 × 2.7 (원형) |
| battery | Watch Li-Po 350mAh | D28 × 4 (원형) |
| crown | Standard Crown 4mm | D4 × 4.5 (원형) |
| sensor | Heart Rate PPG Sensor | 8 × 8 × 2 (사각) |
| wireless_coil | Wireless Charging Coil 25mm | D25 × 1 (원형) |

새 부품 추가: yaml 한 파일.
