# plan-determinism — PF-2 spec

> **목표 (정직화, Rev 4)**: 같은 머신 동일 OCCT 빌드에서 결정성 보장.
> cross-platform 결정성은 best-effort. 한계 README 명시.

상위 결정: [[decisions#PF-2]].

## SelectorFreeze 구조

각 plan step 에 자동 저장:

```python
class SelectorFreeze(BaseModel):
    matched_count: int
    sort_key: str = "lexicographic_bbox_center"  # 정렬 방식
    topology_signature: str                        # 16-hex sha256 prefix
```

YAML 직렬화:
```yaml
selector_freeze:
  matched_count: 4
  sort_key: lexicographic_bbox_center
  topology_signature: "9f8a2c1e3b6d4f70"
```

## topology_signature 계산식

```python
def topology_signature(matched_entities: list[Entity]) -> str:
    items = []
    for e in matched_entities:
        items.append((
            e.kind,                              # "face" | "edge" | "vertex"
            round_tuple(e.bbox.center, ndigits=3),
            round(e.measure(), ndigits=3),       # 면적 or 길이
            e.convexity,                         # "convex" | "concave" | "mixed" | "n/a"
        ))
    items.sort()
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()[:16]
```

mismatch 판단 = `(matched_count, topology_signature)` 튜플 비교.

## 실행 모드

| 모드 | 정책 | 사용 |
|---|---|---|
| **`strict`** | mismatch = 에러 + diff 리포트 | same-machine, default |
| **`loose`** | mismatch = 경고 + 새 매칭 사용 | cross-platform, CI |

UI 의 Plan executor 옵션에 모드 선택. CLI 는 `--mode strict|loose`.

## diff 리포트 (strict 에서 mismatch 시)

```jsonl
{
  "level":"ERROR",
  "phase":"freeze_check",
  "step_id":"s4",
  "expected": {"count":4, "signature":"9f8a2c1e"},
  "actual":   {"count":3, "signature":"7d2e9a13"},
  "diff": {
    "lost_entities": [{"bbox_center":[12.3,-5.1,0.4], "measure":15.7}],
    "extra_entities": [],
    "modified": [{"old":..., "new":...}]
  },
  "hint":"upstream skill 의 selector 매칭이 한 entity 적게 잡힘. PF-1 fallback chain 발동 가능성."
}
```

## PoC 범위 (Phase 0)

`tests/poc/determinism.py`:

```python
def test_same_machine_5x_identical():
    plan = load_plan("plans/simple_watch_outer.yaml")
    results = [execute(plan) for _ in range(5)]
    # 모든 step 의 freeze 가 동일
    for r in results[1:]:
        assert r.frozen_signatures == results[0].frozen_signatures
    # 결과 body 의 face count + volume + bbox 동일
    for r in results[1:]:
        assert r.body.face_count == results[0].body.face_count
        assert abs(r.body.volume - results[0].body.volume) < 1e-6
```

## 확장 검증 (Phase 1 끝)

Windows + Linux (또는 동일 OS 의 다른 OCCT 빌드) 에서 freeze 일치율 측정.

- baseline: same-machine 5회 실행, 100% 일치
- cross-platform: 동일 plan 실행, freeze 일치율 X%

**Go/No-Go**:
- X ≥ 70%: strict 가 cross-platform 에서도 의미 있음
- X < 70%: cross-platform default = loose, README 에 한계 명시

## 부동소수 톨러런스

전역 ε = 1e-6 mm (build123d / OCCT 의 표준 톨러런스).

bbox 중심 비교: `abs(a - b) < ε` 또는 `round(a, 3) == round(b, 3)`.

## CI 회귀

`tests/regression/determinism/baseline_signatures.json` 에 baseline commit. CI 가 변동 감지 → fail.

cross-platform 회귀: 가능하면 Windows + Linux 양쪽 CI 에서 동일 plan 실행 후 freeze 비교.
양쪽 CI 어려우면 nightly 1회만이라도.

## LLM Planner 와의 상호작용

[[llm#planner-mode]] 의 agentic loop 가 새 step 을 propose 할 때:

1. Executor 가 step 실행 → SelectorFreeze 자동 생성
2. Plan 에 freeze 와 함께 append
3. 후속 turn 에서 LLM 이 plan 을 다시 봄 → freeze 가 보임 → "이 selector 가 N개 matched 되었음" 정보로 활용

freeze 가 LLM 의 자체 일관성 검증에 도움. mismatch 시 LLM 에 명시적 피드백:

```text
Step s4 freeze mismatch:
  expected 4 edges, got 3.
  most likely cause: upstream selector resolved differently.
  Either fix the selector or accept the new matching (loose mode).
```
