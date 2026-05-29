# llm — Claude API 통합

## 두 모드

### Editor mode

사용자: *"카메라 island 둘레만 R3 필렛"*

흐름:
1. UI Chat panel → POST plan + manifest + 사용자 메시지
2. LLM Tool call: `propose_step(skill="fillet_edges_by_predicate", args={selector:..., radius_mm:3.0})`
3. Plan executor incremental 실행 (앞 step 캐시 사용)
4. 결과 viewport 갱신 + 채팅 응답

[[src/phone_designer/planner/editor.py]].

### Planner mode (agentic)

사용자: *"부품 배치로 알루미늄 unibody housing 만들어줘"*

Agentic loop:
```
state = (empty_plan, component_arrangement, budget)
for turn in range(MAX_TURNS):
    tool_call = llm.propose_next_step(state)
    if tool_call == DONE: break
    result = executor.apply(tool_call)
    dfm = dfm_validator.check(result, budget)
    state = state.with_step(tool_call, result, dfm)
    if dfm.has_violations() and turn > N_RETRIES:
        # 백트랙
        state = state.remove_last_step()
```

한도: 20 step, 5 백트랙, $5/세션 ([[#비용]]).

[[src/phone_designer/planner/housing_synth_llm.py]].

## 도구 (manifest 자동 생성)

| Tool | 입력 | 출력 |
|---|---|---|
| `inspect_state` | (none) | plan markdown + bbox + component 배치 |
| `propose_step` | skill, args | step 추가 결과 + freeze + history |
| `replace_step` | step_id, new_skill, new_args | 변경 결과 |
| `remove_step` | step_id | plan 갱신 |
| `validate` | (none) | DFM Report |

LLM 은 manifest 외 skill 호출 불가 + pydantic validation 통과 필수. [[#safety]].

## 컨텍스트 + caching

### Caching breakpoint 배치

Anthropic prompt caching 의 cache_control 4개 한도 활용:

```
[messages]
  ├─ [system 1] STABLE CORE   ─── breakpoint A
  │    - 시스템 instructions
  │    - manifest 의 atomic skill 정의
  │    - selector 정의
  │    - process registry
  ├─ [system 2] STABLE EXTENDED ─── breakpoint B
  │    - 일반 component 카탈로그 요약
  │    - macro skill 정의
  ├─ [system 3] DYNAMIC         ─── (no cache)
  │    - 현재 plan (markdown 직렬화)
  │    - 현재 ComponentArrangement
  │    - 직전 step 결과 / DFM 요약
  └─ [messages]
       - 직전 5 turn
       - 사용자 메시지
```

**원칙**:
- breakpoint A 는 atomic skill 의 schema 안정 후 도입 (Phase 5 이후)
- 개발 초기 (Phase 4 까지, skill 추가 빈도 ↑) 는 caching off
- Manifest 의 atomic 변경 = cache 깨짐 비용 ↑ → atomic API 동결 우선시

### 비용

| 항목 | 가격 (Claude Opus 4.7 가정) |
|---|---|
| input | $15 / M token |
| output | $75 / M token |

per-step 추정:
- input ≈ 7K (manifest stable + plan + DFM)
- output ≈ 500
- 비용 = $0.14 / step (caching off)
- caching hit 70% → input 1/4 → $0.04 / step

세션 비용 (20 step + 5 백트랙 = 25 호출):
- caching off: $3.5
- caching on: $1.0

세션 한도: **$5** (안전 마진).

Phase 7 dry-run (0.5주) 으로 실제 cache hit 율 측정 + 한도 재조정.

### CI 정책

| 환경 | LLM 호출 |
|---|---|
| CI (commit 마다) | **mock + golden response**. tests/llm/golden_responses/ 의 fixture 비교 |
| Nightly | real LLM, 12 시나리오, 비용 monitor |
| 릴리즈 직전 | real LLM 전체 회귀, 수동 트리거 |

## Safety

- LLM 출력의 skill name **반드시 manifest 에 존재**, 없으면 거부 + 재시도
- 인자 **pydantic Args model validation** 통과 필수
- 한 turn 최대 N=20 skill step
- `import_step` 의 path = whitelist (fixture/, catalogs/, reference/extracted/)
- LLM 이 `exec()`/임의 코드 생성 불가 (tool schema 가 vocabulary)
- mismatch 시 [[plan-determinism#diff-리포트]] 를 LLM 에 피드백 → 자체 수정

## API key 관리

- 첫 실행 시 Settings → API key 입력
- 저장: OS keyring (Windows Credential Manager / macOS Keychain / Linux Secret Service)
- `keyring` 라이브러리 사용
- 환경변수 `ANTHROPIC_API_KEY` 도 fallback
- 평문 파일 저장 금지

[[src/phone_designer/llm/keyring_storage.py]]:

```python
import keyring

SERVICE = "phone-designer"
USER = "anthropic-api-key"

def save_key(key: str):
    keyring.set_password(SERVICE, USER, key)

def load_key() -> str | None:
    return keyring.get_password(SERVICE, USER) or os.environ.get("ANTHROPIC_API_KEY")
```

## Offline mode

LLM API 비가용 (인터넷 단절 / API key 없음 / 사내망 격리) 시:

| 모드 | 동작 |
|---|---|
| Reproduction | **OK** — LLM 없이 STEP/glb 파이프라인 + 자동 plan |
| Composition (rule-based v0) | **OK** — [[components#합성-v0]] |
| Composition (LLM v1) | **disabled** + UI tooltip |
| Editor (직접 plan 편집) | **OK** — UI 의 plan 편집기 |
| Editor (자연어) | **disabled** + UI tooltip |
| Auto-synthesize 버튼 | **disabled** |
| DFM 검증 | **OK** — LLM 무관 |

UI 의 Chat panel 과 Auto-synthesize 가 disabled 상태로 graceful degrade.

## 12 시나리오 LLM 회귀 (Phase 7)

미리 정의된 자연어 명령 12개 회귀:

1. "두께를 1mm 줄여줘"
2. "디스플레이 베젤 1.5mm"
3. "카메라 plateau +0.5mm" (워치: "크라운 plinth +0.5mm")
4. "여기 면에 보스 4개" (face picking)
5. "측면 안테나 슬릿"
6. "포켓 깊이 다항식, 중앙 최심"
7. "USB-C → Lightning" (워치: "lug 폭 +2mm")
8. "스피커 그릴 16홀"
9. "방금 변경 되돌려"
10. "전체 리셋"
11. "이 부품 배치로 housing 합성"
12. "이 형상의 DFM 위반 모두 보고"

각 시나리오 → tool call + freeze 검증 + 결과 비교. golden response fixture 로 CI 검증.
