# PR96 V2 기능 보존 검증 (2026-10-14)

## 변경과 범위

- A: `e96d3f0c39d8739127f01cb6c18ae3c1d3f3ec7a`를 새 scratch archive로 생성한 실제 V1 모듈. 시작 HEAD: `6cd0aaeac47b60b8cbd7eab7e0701d9a3eefdc02`, `feat/course-planner-stability`.
- V2 이전을 유지하고 `editing.candidates`의 비전문 메뉴 적중 후 업종 fallback, `profile_features.collect`의 중식 조사 우선순위를 A와 같게 복구했다. 조사 우선순위는 메뉴 판매 근거가 아니다.
- 5커밋의 rename/import-normalized diff를 검토했다. 현재 후보의 변경된 production Python 77개 중 71개는 절대/상대 import를 정규화한 AST가 A와 일치한다. 나머지는 네 agent 공통 팩토리, 평가 계약 추가, persona의 문서 예제 import다. `ast-comparison.json`, `normalized.diff`에 근거를 보존한다.
- 공통 모델 팩토리는 유지한다. 기본/빈/override 환경값에서 실제 constructor의 model, reasoning, timeout, retry, Responses API, 출력 상한 및 agent별 캐시/low 복제 뒤 medium 보존 관측값이 A/B 일치했다. V2 medium 모델의 객체 identity 공유 자체는 A와 다르지만 현재 호출자는 모델 설정을 직접 수정하지 않으며 low는 복제본이다. 런타임 환경변수 변경·임의 공유 객체 mutation은 평가하지 않았다.
- simple 자장면의 명시적 비판매 우회는 A에도 있던 별도 문제이므로 수정하지 않았다. 프롬프트·정책·운영 requirements·V1 호환 계층은 추가하지 않았다. stage/commit/push/merge/rebase 및 운영 DB/cache 변경 없음.

## 동결 A/B 및 실제 프레임워크

증거 경로 `S=/Users/yunseongho/.hermes/cache/scratch/pr96-v2-preservation`.

- DeepEval Python `D=/Users/yunseongho/.hermes/cache/scratch/pr86-ci/venv/bin/python`: Python 3.12.12, DeepEval 4.2.8.
- Ragas Python `R=/Users/yunseongho/.hermes/cache/scratch/highlight-deepeval/venv/bin/python`: Python 3.12.12, Ragas 0.4.3, DeepEval 4.2.8.
- `frozen_contract.py` SHA256: `41894972b2ff98a5c7860b1e39db3f50b24254aca47ab9fa0e327068b67f49c4`.
- `run_contract.py` SHA256: `e03bd126c9a3856ac3c39c8ab6b73cd8d481903ad5c1698bcde95cc837c2fd7c`.
- A는 harness의 네 이동 모듈 import만 V1으로 remap하고 ROOT를 archive로 지정한다. B는 동일 파일/입력/정답/metric/SDK를 사용한다. 최종 frozen 파일은 production 복구 **전** A3/HEAD3에서 먼저 실행했다.
- 초기 시험 A/HEAD의 실패도 보존했다. 첫 provisional fixture가 상속한 `기존 짜장면 식당` 이름은 일반 후보에도 메뉴 힌트를 주었고, SDK timeout은 float인데 기대 JSON은 int였다. 이름만 중립화하고 25초의 동일 기대값을 SDK float 표현 `25.0`으로 교정한 뒤 최종 동결했다. `initial-fixture.py`, `fixture2.py`, `A-deep.*`, `HEAD-deep.*`, `A2-deep.*` 보존; 기존 51개 정답 및 exact 1.0 threshold 변경 없음. 새 계약/fixture 교정은 독립 검토 대상이다.

| 실행 | 실제 결과 | exit |
| --- | --- | --- |
| A3 DeepEval | 54/54 | 0 |
| 복구 전 HEAD3 DeepEval | 52/54, fallback/중식 조사 순서 두 건 불일치 | 1 |
| B DeepEval | 54/54 | 0 |
| A3 Ragas | 54/54 | 0 |
| B Ragas | 54/54 | 0 |
| A3/B 전체 observations 비교 | 54개 반환/검색 IO/조사 순서/모델 설정 delta 0 | 0 |
| 실제 CI export의 downstream Ragas score | 54/54 | 0 |
| 기본/빈/override-model 설정 probe | 각 A/B exit 0, JSON byte comparison 0, 3쌍 일치 | 0 |

모두 **synthetic/offline 계약**이며 provider·LLM judge 호출 0이다. DeepEval과 Ragas는 각자의 deterministic exact-contract 지표를 실제 실행했고 서로 다른 지표의 품질 우열을 주장하지 않는다.

### 재현 명령

아래 명령은 정확한 fix workspace에서 실행한다. 모든 평가 호출에 `DEEPEVAL_DISABLE_DOTENV=1 DEEPEVAL_DISABLE_LEGACY_KEYFILE=1 PYTHON_DOTENV_DISABLED=1`을 전달하고 기존 offline-exec가 socket connect를 차단한다. contract 자체는 telemetry/tracing opt-out 및 socket 차단을 적용한다.

```sh
S=/Users/yunseongho/.hermes/cache/scratch/pr96-v2-preservation
D=/Users/yunseongho/.hermes/cache/scratch/pr86-ci/venv/bin/python
R=/Users/yunseongho/.hermes/cache/scratch/highlight-deepeval/venv/bin/python
PRESERVATION_ROOT="$S/base" PRESERVATION_BASELINE=1 "$D" -I .github/evaluation_ci.py offline-exec "$S/run_contract.py" export --output "$S/A3-deep.json"
PRESERVATION_ROOT="$PWD" "$D" -I .github/evaluation_ci.py offline-exec "$S/run_contract.py" export --output "$S/B-deep.json"
PRESERVATION_ROOT="$PWD" "$R" -I .github/evaluation_ci.py offline-exec "$S/run_contract.py" score --input "$S/A3-deep.json" --output "$S/A3-ragas.json"
PRESERVATION_ROOT="$PWD" "$R" -I .github/evaluation_ci.py offline-exec "$S/run_contract.py" score --input "$S/B-deep.json" --output "$S/B-ragas.json"
"$D" -I .github/evaluation_ci.py free --output "$S/free"
"$R" -I .github/evaluation_ci.py highlight-free --output "$S/highlight"
"$R" -I .github/evaluation_ci.py offline-exec backend/llm/v2/evaluation/tonkatsu_contract.py score --input "$S/free/tonkatsu.json" --output "$S/ci-ragas.json"
"$D" -I .github/test_evaluation_ci.py
"$D" -I .github/evaluation_ci.py offline-exec backend/llm/v2/tests/run_attachment_offline.py llm.tests.test_course_candidate_sources llm.tests.test_course_creation llm.tests.test_course_preparation llm.tests.test_course_parallel llm.tests.test_course_literal_edits llm.tests.test_course_editing llm.tests.test_course_visit_requests llm.tests.test_course_recommendation_policy llm.tests.test_course_route_ranking llm.tests.test_course_quality_proximity llm.tests.test_vector_store_cancellation llm.tests.test_lodging_chrome llm.v2.tests.test_course_answer
```

## CI 연결과 회귀

- `.github/evaluation_ci.py`의 기존 FREE 목록에 `llm.tests.test_course_candidates`가 이미 연결되어 있어 workflow 목록 변경은 불필요하다. `evaluation-regression.yml:99`, `deterministic`의 실제 free 명령에서 `test_non_specialty_hit_preserves_remaining_spelling_and_category` 포함 **107개 모두 통과**. `free.log:103,110`.
- 같은 FREE exporter는 추가 3개 계약까지 **54/54 DeepEval**을 실행했다(`free.log:132`). 기존 artifact 전달 후 `highlight-deterministic`의 workflow 131행 score 명령으로 실제 CI export를 **54/54 Ragas** 채점했다(`ci-ragas.log`).
- 실제 `free` 전체 명령 exit 0. `highlight-free` 전체 명령 exit 0, 1+2 unittest 및 정상/오류 DeepEval/Ragas scorer-check 완료. 전체 GitHub hosted workflow/배포 job을 실행한 것은 아니다.
- 실제 wiring guard **11개 통과**, 필수 코스/취소/병렬/숙박/응답 회귀 **163개 통과**. 각각 `wiring.log`, `regressions.log`, exit 0. task-owned shared-memory SQLite runner이며 운영 DB는 사용하지 않았다.
- 추가 SDK 모듈 검사: B **3/4 통과**, A **2/3 통과**, 둘 다 `test_native_web_search_serialized_payload_and_conditional_scope`에서 `False != True`로 exit 1. offline runner가 WEB_RESEARCH_ENABLED=false를 강제하는 기존 환경 불일치를 A에서도 재현했다. 생성기·기본 모델 constructor/stream 검사 및 B의 공통 factory 검사는 통과했다. `model-tests.log`, `model-tests-A.log`; 모듈 전체 통과로 표시하지 않는다.
- `git diff --check` 통과. 프론트 source 변경 없음, 프론트 전체 테스트 미실행.

## 독립 검증 후 CI completeness 보정

- 독립 verifier가 V2 migration에서 추가/변경한 factory·constructor 두 테스트가 FREE/default workflow에서 빠졌음을 확인했다. 위 107개 결과와 모델 설정 계약은 이 두 테스트의 CI 실행을 대체하지 않는다.
- `.github/evaluation_ci.py`의 기존 첫 offline course runner에 `llm.tests.test_llm_temperature.ResponsesPayloadTest.test_v2_agents_use_the_single_v2_model_factory`와 `llm.tests.test_llm_temperature.ResponsesPayloadTest.test_luna_constructors_omit_temperature`만 추가했다. `.github/test_evaluation_ci.py`의 기존 목록 assertion도 두 selector를 요구한다. runner·workflow·dependency·테스트 정답·runtime source는 변경하지 않았다.
- 새 증거 `C=/Users/yunseongho/.hermes/cache/scratch/pr96-v2-ci-fix`: `$D -I .github/evaluation_ci.py free --output "$C/free"` 전체 exit 0; `free.log:108-110`에 두 이름과 `ok`, 첫 runner **109/109**(`free.log:113`), DeepEval export **54/54**를 확인했다. `evaluation-regression.yml:99`의 `deterministic` 실제 명령에 연결되며 전체 hosted workflow를 실행한 것은 아니다.
- 강화된 guard를 correction 전 실행한 `wiring-before.log`는 **11개 중 1개 실패/exit 1**로 누락을 검출했다. correction 후 `$D -I .github/test_evaluation_ci.py`는 **11/11/exit 0**이며 같은 workflow `deterministic` 96행에 연결된다. `$R -I .github/evaluation_ci.py offline-exec backend/llm/v2/evaluation/tonkatsu_contract.py score --input "$C/free/tonkatsu.json" --output "$C/ci-ragas.json"`도 **54/54/exit 0**다.
- 기존 verifier의 source manifest와 backend 파일 **149개 SHA256 전부 동일**(`source-parity.json`)로 기존 163개 runtime 회귀/A-B 근거를 보존했다(이번 correction에서 163개를 재실행한 것은 아님). `run_checks.py`에 credential 제거, dotenv/telemetry/tracing 비활성, 기존 tokenizer cache 경로를 기록했고 모든 free/score는 기존 socket-denied launcher를 사용했다. native web-search의 기존 A/B 실패와 paid/performance not-run 경계는 그대로다.

## 같은 세션 /code-review

`Skill(code-review)`를 실제 호출하고 설치된 `/Users/yunseongho/.local/bin/coderabbit` 0.9.0의 인증 성공 후 다음 명령을 실행했다:

```sh
/Users/yunseongho/.local/bin/coderabbit review --agent --uncommitted --dir /Users/yunseongho/orca/workspaces/SKN34-4th-5Team/fix
```

실제 NDJSON은 `code-review.ndjson`: `complete/status=review_completed`, `outcome=completed`, findings **0**, exit **0**. 당시 tracked 변경 6개(코스 두 파일/후보 테스트/criteria/계약/CI 안내)를 검토했다. 이 결과는 미실행·review_skipped가 아니며 이후 추가한 이 보고서는 해당 검토 대상에 없었다. 리뷰에 보낼 diff를 먼저 확인했으며 실제 credential은 포함하지 않았다.

### CI correction worker의 같은 세션 review

별도 correction 세션에서 `Skill(code-review)`를 호출했고 trusted CodeRabbit 0.9.0 인증 확인 후 `review --agent --uncommitted --include-untracked --dir /Users/yunseongho/orca/workspaces/SKN34-4th-5Team/fix`를 실행했다. 첫 결과는 `review_completed/outcome=completed`, 9파일 검토, exit 0이며 minor 1건(재현 shell block의 S/D/R assignment 누락)이었다. 문서에 이미 명시된 세 경로를 block 시작에 추가했고 runtime/CI/정답 변경은 없다. `C/code-review-first.ndjson`에 실제 결과를 보존하며 수정 후 재검토 결과는 scratch 최종 보고서에 기록한다.

## 변경 파일 SHA256

| 파일 | SHA256 |
| --- | --- |
| `backend/llm/tests/test_course_candidates.py` | `bcb93775c79cbf3b7ec682b5ecae4d2d47de784d19ed4a0444716ff31c631a8c` |
| `backend/llm/v2/course/editing.py` | `fbb011b2d1f6699fc8b2c44c077cdb0c6db4f9f195a9a64f2f6b683071c52fa9` |
| `backend/llm/v2/course/profile_features.py` | `772a06802a496458e75e735090b8021b1f847e86eaec5fc0e2c29239153bb60f` |
| `backend/llm/v2/evaluation/EVALUATION_CRITERIA.md` | `685d896557ba9166e0a62f66314fc11029f60d9be6bc0dfab3c7893921496580` |
| `backend/llm/v2/evaluation/tonkatsu_contract.py` | `41894972b2ff98a5c7860b1e39db3f50b24254aca47ab9fa0e327068b67f49c4` |
| `docs/EVALUATION_CI.md` | `3ea0db890ef61cf1623b686a420727d2966456bebec65b4a048a321fc5d52b0f` |

보고서 자체의 hash는 자기 참조를 피하고 scratch의 `files-sha256.json`에 포함한다.

## 남은 검증 경계

외부/유료 평가 및 실제 전체 채팅 성능 A/B는 미승인 **not-run**이다. 품질/속도/운영 부하/제공자 대응 승인을 주장하지 않는다. 코디네이터/독립 verifier 확인과 커밋 판단은 별도이며 이 worker는 실행하지 않았다. 코디네이터가 기록한 develop SHA는 `8fe80536395b4114053bfce1bc5b2a06421dcd35`이고 이 workspace에는 upstream/develop ref가 없으므로 이번 좁은 보존 작업에서 remote/history를 변경하거나 통합하지 않았다.
