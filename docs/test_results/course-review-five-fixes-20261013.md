# 코스 리뷰 5건 수정 · 오프라인 A/B

## 범위와 상태

- 작업 기준: `feat/course-planner-stability`, HEAD `4437abff5f09ff45fc842b76be1fb44cbcf5674f`, 시작 상태 clean. 코디네이터가 확인한 팀 develop `372b6b0560e4e3d29f2a0f6881c1336da1c39d41` 정보 사용. 이 worktree는 upstream ref가 없고 origin이 팀 저장소인 예외 상태이며 remote/ref/history 변경 및 merge는 하지 않았다.
- AGENTS, 팀 GitHub 규칙, 코스 성능 지침, 평가 기준을 읽고 평가 목적/기대/범위/pass-fail을 먼저 기록했다. Graphify AST `/Users/yunseongho/.hermes/cache/scratch/course-review-fix-graph/graphify-out/graph.json`의 관계를 현재 소스 caller 검색과 대조했다.
- 수정 전 고정 계약 작성 → A Django/DeepEval/Ragas 실제 실행 → 최소 production 수정 → B 동일 계약/CI/경계 실행 순서를 따랐다. 기존 17개 계약은 변경하지 않고 9개를 추가했다. 신규 계약은 독립 검토 대상이며 자동 golden 승격하지 않았다.
- 원시 로그/JSON/해시: `/Users/yunseongho/.hermes/cache/scratch/course-review-fix-results-ctx9b78/`. repo에 생성된 scratch와 `.deepeval`은 본 작업 파일만 확인 후 이 절대 경로로 이동했다.

## 수정 내용

1. `visit_roles`: 보존 역할 언급은 변경 허용이 아니다. 번호 뒤 실제 상호가 붙은 `2번 STATION은 그대로`도 보호한다. 실제 번호/상호/술집 변경과 기존 selected/internal/cafe 경계는 유지한다.
2. BAR 방문 의도를 `Selector.search → editing.candidates → venue_policy.search_candidates → place_quality.verify_candidates`에 업종과 별도로 전달한다. 수정 대상은 기존 `timeline.kind_of(target)`을 재사용하고 실제 경로별 `_kakao_step`도 kind를 전달한다. FOOD 상호 혼합 검색에서 실제 술집만 기존 평점 수집기가 조사한다. 일반 식당 혼합 결과에는 추가 IO가 없다. 기존 업종/반경/예산/병렬/취소 경계는 변경하지 않았다.
3. `canonical`은 road 주소가 없으면 Kakao `address_name`을 재사용한다. 지번 술집도 평점 조사하되 다른 지점 근거는 여전히 거절한다.
4. FREE 첫 runner에 `test_course_bar_ratings`, `test_course_quality_research`, `test_course_recommendation_policy`, `test_course_visit_requests`를 포함하고 wiring 기대값을 함께 갱신했다.
5. 모호한 변경의 보존 안내는 실제 보호 대상에 따라 `술집은`, `구장 내부 카페는`, `구장 내부 먹거리는`으로 구분한다.

## 고정 A/B

Python 3.12, DeepEval 4.2.8 deterministic BaseMetric exact, Ragas 0.4.3 non-LLM SingleTurnMetric exact. 모델/judge 호출 없음. 네트워크는 offline-exec와 계약 entrypoint에서 차단했다. 양쪽 framework 지표는 같은 exact 계약을 각각 검증하며 일반 의미 품질 지표로 해석하지 않는다.

- 동일 CASES SHA256: `94b1d252d6fe0746793d7c0e1c385aa00db1a5ce06f3d01f4b92296ddd222676`
- 동일 harness SHA256: `4aef40f85ba2dca3a8b700fd7f4184344ac9b1da5cc13df2f5857a3bce0e16de`
- A: Django `TonkatsuReplacementTests` 11 tests, 7 subcase failures, exit 1. DeepEval/Ragas 각각 **19/26**, exit 1.
- B: 같은 26케이스 DeepEval/Ragas 각각 **26/26**, exit 0. 전체 course FREE runner **69 tests**, exit 0.
- A 실패 IDs: `preserve-bar-role`, `preserve-number-name`, `bar-clarification-role`, `cafe-clarification-role`, `bar-brand-mixed`, `bar-jibun-only`, `bar-other-branch`.
- 추가 정상 반례: `explicit-bar-name`, `food-brand-mixed-no-io`는 A/B 모두 통과. 기존 실제 번호/술집/내부 수정, 일반 카페 구분, 메뉴 후보 계약도 통과했다.
- apply 케이스는 실제 `editing.answer` 호출 후 실제 검색 대상으로 전달된 visitId를 관측한다. ratings 케이스는 실제 `Selector.search`, editing, venue/quality/bar_ratings를 통과하고 고정 공급자 결과/평점 profile 및 수집 횟수를 관측한다. IO 기대: BAR 혼합 및 지번 1회, 일반 FOOD 혼합 0회, 다른 지점은 조사 1회 후 허용 0개. interiores mock은 호출되면 실패한다.

## 명령 · CI 연결 · 실제 결과

아래 `$DEEP`는 `/Users/yunseongho/.hermes/cache/scratch/jev-eval-venv/bin/python`, `$RAGAS`는 `/Users/yunseongho/.hermes/cache/scratch/pr78-ragas/venv/bin/python`, `$BOTH`는 `/Users/yunseongho/.hermes/cache/scratch/highlight-deepeval/venv/bin/python`이다. 모든 명령은 이 worktree 루트에서 실행했다. 결과 파일명은 로그 이동 전 repo scratch 경로를 사용한 역사적 명령이며 결과는 위 절대 scratch에 보존되어 있다.

```sh
# A: production 변경 전
$DEEP -I .github/evaluation_ci.py offline-exec backend/llm/v2/tests/run_attachment_offline.py llm.tests.test_course_tonkatsu
$DEEP -I .github/evaluation_ci.py offline-exec backend/llm/v2/evaluation/tonkatsu_contract.py export --output scratch/course-review-fix/A-deep.json
$RAGAS -I .github/evaluation_ci.py offline-exec backend/llm/v2/evaluation/tonkatsu_contract.py score --input scratch/course-review-fix/A-deep.json --output scratch/course-review-fix/A-ragas.json
# B: 같은 export/scorer
$DEEP -I .github/evaluation_ci.py offline-exec backend/llm/v2/evaluation/tonkatsu_contract.py export --output scratch/course-review-fix/B-deep.json
$RAGAS -I .github/evaluation_ci.py offline-exec backend/llm/v2/evaluation/tonkatsu_contract.py score --input scratch/course-review-fix/B-deep.json --output scratch/course-review-fix/B-ragas.json
# 실제 FREE 명령: 14 commands 전체 성공
$DEEP -I .github/evaluation_ci.py free --output scratch/course-review-fix/B-free
# 실제 첫 FREE runner: 69 tests 성공
$DEEP -I .github/evaluation_ci.py offline-exec backend/llm/v2/tests/run_attachment_offline.py llm.tests.test_course_tonkatsu llm.tests.test_course_literal_edits llm.tests.test_course_audit_repairs.CourseSelectionRepairTest llm.tests.test_course_bar_ratings llm.tests.test_course_quality_research llm.tests.test_course_recommendation_policy llm.tests.test_course_visit_requests
# wiring: 11 tests 성공
$DEEP -I .github/test_evaluation_ci.py
# 나머지 핵심 코스 경계: 93 tests 성공
$DEEP -I .github/evaluation_ci.py offline-exec backend/llm/v2/tests/run_attachment_offline.py llm.tests.test_course_editing llm.tests.test_course_creation llm.tests.test_course_parallel llm.tests.test_course_preparation llm.tests.test_course_candidate_sources llm.tests.test_course_quality_proximity llm.tests.test_course_route_ranking
# 취소/숙박/응답 경계: 35 tests 성공
$DEEP -I .github/evaluation_ci.py offline-exec backend/llm/v2/tests/run_attachment_offline.py llm.tests.test_vector_store_cancellation llm.tests.test_lodging_chrome llm.v2.tests.test_course_answer
# 두 framework scorer positive/negative 검사 포함: exit 0
$BOTH -I .github/evaluation_ci.py highlight-free --output scratch/course-review-fix/B-highlight-free-combined
```

- workflow `.github/workflows/evaluation-regression.yml`, job `deterministic`: `python -I .github/evaluation_ci.py free --output "$RUNNER_TEMP/evaluation-free"`. 새 CASES는 실제 `test_frozen_target_and_candidate_contracts`와 export에서 실행한다. `B-free.log`에 해당 test 이름, 네 누락 모듈의 개별 test 이름 및 `Ran 69 tests`가 있다. SQLite owned-memory runner 사용으로 운영 DB에는 접근하지 않았다.
- FREE 전체 로그의 test 실행 블록: 69 + 6 + 19 + 4 + 12 + 101 + 7 + 5 + 4 = **227 tests**. course contract 26, weather contract 11, seed 13, boundary 26도 각 entrypoint에서 실행. 중복 실행 블록이 있으므로 고유 케이스 수라고 주장하지 않는다.
- `highlight-deterministic`의 기존 Ragas job은 deterministic에서 업로드한 `tonkatsu.json`을 받아 기존 `tonkatsu_contract.py score`를 실행한다. 변경 없이 신규 26개도 같은 경로에 포함된다. 여기서는 해당 실제 score 명령을 오프라인 재현했다. 원격 GitHub workflow 자체는 실행하지 않았다.
- `test_broad_seeds_and_local_probes_do_not_do_new_io`, `test_food_and_cafe_behavior_unchanged_and_no_unrelated_rating_reads`, `test_new_bar_rating_is_preserved_without_model_or_interior_call`, `test_rating_budget_is_shared_and_bounded_across_searches`, parallel/cancellation 회귀 통과.
- 초기 highlight-free는 분리 환경에서 실패했다: Ragas-only는 `No module named deepeval`, Deep-only는 `No module named ragas`. 오류 로그를 남기고 이미 설치된 BOTH 환경으로 동일 명령 재실행하여 성공했다. 운영 requirements 수정/새 설치는 없다.

## 소스 SHA256 A → B

| 파일 | A | B |
| --- | --- | --- |
| visit_roles.py | e3f7477065b05ebd48086740a981fd335fba957b596b4b826b645e2b051885d2 | bdaeeff84737acd7bfb7cc7501b935bcc98d93cfae7813697ad7dfe69e3b421d |
| place_quality.py | daddc38e5b14f0ab5a8486626c163c4641bf82b7a6670a71ce9b51c2a34ba4e7 | ed60b2c519b279b0a73be39787b42fcfe7af341a114ad7426670d1e1496de173 |
| visit_requests.py | 6d04f0c870c4161d50b423b3b078eb574181cea9401df84857325eedb4a6bbc5 | efad5f3fad162689c87af62ab0fd4665170fe1188b11f0449b6e774ccb946e7f |
| editing.py | 4518ee9e4eb06c4610661a03807c0a10d87555e5529245bda8deeada16a6d466 | ca087151bf9765a2ba11aecd5c7220c4419da292a7b4c65f2bce054732b80985 |
| agent.py | 6fcb359c0d45de148995b9722cb90daadc039ba75ca9b8ac8c0d2d39c4338305 | 4efdd2bc8d7e52196dcf9b886b39d6bc689dec9e7aaffb8aa07139453cac122f |
| venue_policy.py | 9d002d4acdb13f6e1c670814220ddbb3f8e5feb7762805ec28c9c145148b61ad | 763cf61eacba1f808d7b500f39e0ff9cee1cc05f06a6e53221aee2512c4093d6 |

## 리뷰와 남은 검증

- 이 Claude Code 세션에서 `Skill(code-review)`를 실제 호출하고 인증 상태를 trusted CLI로만 확인한 뒤 `/Users/yunseongho/.local/bin/coderabbit review --agent --uncommitted`를 실행했다. `review_completed`, exit 0, **findings 0**, 위 10개 변경 파일 모두 reviewedFiles로 명시되어 있다. NDJSON 원시 결과는 `code-review.ndjson`에 보존했다. 완료 heartbeat를 최종 성공으로 오인하지 않았다.
- `git diff --check` 통과. commit/push/PR/deploy, 원격/branch/history 변경, nested delegation, 앱 유료 제공자 호출 없음. CodeRabbit 외부 코드 리뷰는 요청된 `/code-review` 실행이며 앱 모델 평가와 별개다.
- 실제 전체 채팅 유료 A/B 성능, 실제 공급자 페이지 가용성/모델 의미 품질, 프론트 전체 npm test/타입 검사는 미실행이다. 이번 변경은 backend 한정이고 유료 성능은 미승인/검증 대기다. 무료 IO 및 직접 함수 계약 통과를 전체 사용자 과업 완료/운영 성능 보장으로 확대하지 않는다.
- 다음 독립 verifier의 exact diff/신규 계약 검토가 남아 있다. 이 보고서는 구현 worker의 로컬 근거이며 팀 승인/merge 판단을 대체하지 않는다.
