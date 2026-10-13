# 평가 CI 실행 안내

## 무료 필수 게이트

2026-10-13 사용자 정정으로 자동 제공자 오류는 공개와 수동 검토를 병행한다. fallback 6건은 동일 synthetic 입력과 threshold=1로 공개 revision 여부까지 채점하며 이미지 오류는 비공개다. Django mode 회귀는 공개 post/comment, 사후검토 목록, confirm/dismiss, 숨김·비공개 부모·다중 이미지 사유, 최초 포인트/댓글수, stale/storage를 검증한다. 이는 기존 비공개 오류 계약을 대체하는 새 사용자 정책이며 실제 제공자 승인/품질을 뜻하지 않는다.

커뮤니티 검사는 `evaluation-regression.yml`이 `community-moderation.yml`을 재사용한다. disposable PostgreSQL의 `community` 전체 및 `community.moderation_probe`, `llm.tests.test_domain_tools.DomainToolsTest`, `accounts.test.test_point_integration`을 `python backend/manage.py test community community.moderation_probe llm.tests.test_domain_tools.DomainToolsTest accounts.test.test_point_integration --noinput --verbosity=2`로 실행한다. `django-integration` job의 frontend 명령은 `node --test tests/community-api.test.mjs tests/community-moderation.test.mjs tests/admin-dashboard.test.mjs tests/auth-bridge.test.mjs`이며, `provider-contracts` 분리 환경에서 `python -I backend/llm/v2/evaluation/test_community_moderation.py`(legacy live 10건), `python -I backend/llm/v2/evaluation/test_moderation_native.py`(native adapter 7건), `python -I backend/llm/v2/evaluation/test_moderation_fallback.py`(실제 worker의 synthetic 실패/DB 모드 계약 6건)를 실행한다. 모두 실제 DeepEval·Ragas exact 지표이며 각 프레임워크의 실행 개수/점수를 assert한다. fallback은 in-memory SQLite 트랜잭션과 합성 ORM 경계만 사용하며 운영 DB/실제 제공자에 연결하지 않는다. `community.test_moderation_mode` 신규 회귀는 기존 community 자동 발견 명령에 포함된다. 이 결과는 실제 모델의 정치·욕설 분류 품질을 승인하지 않는다. 로컬 HTTP A/B 관찰은 `community.moderation_probe`, 채점은 `community_moderation_ab.py`로 재현한다. 자세한 범위는 [커뮤니티 검사 안내](COMMUNITY_MODERATION.md)를 참고한다.

`evaluation-regression.yml`을 dev/prod 배포 워크플로가 재사용합니다. `develop` PR/Push, `main` Push에서 배포는 기존 Django check/Next build와 **두 평가 job 모두 성공**해야 가능합니다. `main` PR은 평가 워크플로가 직접 실행합니다. 경로 필터가 없으므로 테스트·데이터 변경도 항상 실행됩니다.

Python 3.12의 별도 job을 사용하며 운영 requirements는 변경하지 않습니다.

2026-10-13 내부·외부 방문 구분 회귀: `deterministic`의 FREE 목록에 `run_attachment_offline.py llm.tests.test_course_tonkatsu llm.tests.test_course_literal_edits llm.tests.test_course_audit_repairs.CourseSelectionRepairTest`(27개)와 `tonkatsu_contract.py export`(17개 실제 함수 계약/DeepEval)를 추가했습니다. `course-contract-export` artifact를 후속 `highlight-deterministic`에서 내려받아 같은 결과와 고정 정답을 `tonkatsu_contract.py score`로 Ragas 채점합니다. 후속 job은 deterministic 성공에 의존합니다. `scorer-check`는 별도로 정상/오류 점수 대조를 검사합니다. 모두 기존 offline-exec의 네트워크 차단 안에서 실행하며 실제 제공자 품질 점수가 아닙니다.

| 환경 | 실제 실행 대상 |
| --- | --- |
| DeepEval/backend | seed offline 13개, reviewed boundary offline 26개, test_evaluation 전체, attachment_shared 전체(소유 SQLite runner), test_remaining 전체 및 60-case validate, 기존 attachment offline runner 기본 5개 모듈 전체, relocated test_eval_ragas mock 회귀, 정확한 `llm.tests.test_domain_tools.ExternalDomainToolAdapterTest`(DB-heavy DomainToolsTest 제외), `weather_contract.py export` 실제 도구 11개 고정 계약/DeepEval, `llm.tests.test_chat_eval` 전체(같은 SQLite runner, 모의 stream·결정적 채점·명령 저장 회귀) |
| highlight Django/frontend (`deterministic`) | `llm.tests.test_highlights` 전체 28개(실제 Django TestCase/ORM/migrations), `frontend/tests/youtube-highlight.test.mjs` 전체 3개(Node test runner) |
| highlight isolated | relocated test_highlight_deepeval(DeepEval/Ragas scoring), test_relocation(실제 두 framework offline evaluate 및 legacy CLI/import/data 호환), `weather_contract.py scorer-check` 실제 DeepEval/Ragas 정상·오류 채점기 대조(합성, production 증거 아님) |
| CI wiring | `.github/test_evaluation_ci.py`: ID cap/중복/주입 거부, argv 실행, 실행 오류와 품질 불일치 구분, trusted-ref 수동 gate와 배포 needs 확인 |

`deterministic` job은 기존 evaluation requirements/`offline-exec` launcher를 재사용하여 `python -I .github/evaluation_ci.py offline-exec backend/manage.py test llm.tests.test_highlights --settings=llm.tests.highlight_settings --noinput --verbosity=2`를 실행합니다. 전용 `pgvector/pgvector:pg18` service의 loopback DB에만 연결하며 Django가 `test_highlight_ci`를 생성·삭제합니다. 독립 settings는 운영 settings/dotenv를 import하지 않고 DB host/user/name을 고정하며, 로컬 disposable service 포트만 `HIGHLIGHT_TEST_DB_PORT`로 바꿀 수 있습니다. 전체 migration graph에 필요한 앱과 PostgreSQL/vector extension을 유지하므로 migration 생략·AST 대체가 아닙니다. Python socket 차단은 제공자 네트워크를 막고 psycopg/libpq의 전용 DB 연결만 사용합니다. 이는 하이라이트 회귀 증거이며 전체 운영 settings/제공자 품질 검증은 아닙니다.

같은 job의 Node 22에서 `frontend` 작업 디렉터리의 `npm ci` 후 `node --test tests/youtube-highlight.test.mjs`를 실행합니다. 기존 `.github/test_evaluation_ci.py` unittest가 YAML을 읽어 두 명령, 무조건 실행/fail-closed, disposable DB service와 dev/prod reusable workflow 연결을 검사하며 이 guard 자체도 같은 job에서 실행합니다. 두 테스트는 develop PR/Push·main PR/Push 및 배포 전 기존 reusable 필수 게이트에 포함됩니다.

무료 회귀 job은 먼저 `TIKTOKEN_CACHE_DIR=$RUNNER_TEMP/evaluation-tiktoken`에서 `tiktoken.get_encoding("cl100k_base")`를 실행해 공개 tokenizer asset을 내려받고 tiktoken의 지정 SHA-256으로 검증합니다. 이는 모델/API 평가 호출이 아닌 설치 사전준비이며, 다운로드·hash 검증 실패는 job 실패입니다. 이후 동일 cache를 사용하여 네트워크 차단을 유지합니다. 빈 HOME/cache에서도 이 준비 단계를 먼저 수행해야 합니다.

무료 실행은 Python socket connect를 거부하는 subprocess에서 수행합니다. Mock/scorer/스크립트 회귀이지 실제 제공자 품질이나 전체 챗봇 E2E 증거가 아닙니다. 새 테스트가 자동 발견되지 않으면 `.github/evaluation_ci.py` 실행 목록과 이 표를 함께 갱신합니다.

## 수동 실행

GitHub Actions → **Manual evaluation (bounded, paid opt-in)** → Run workflow. 신뢰된 `develop`/`main`만 허용하고 `authorize=true`가 필수입니다. 다른 ref 또는 승인 누락은 preflight에서 **실패**합니다. `evaluation` GitHub Environment에 required reviewer와 develop/main deployment branch 제한을 설정하고, 팀 저장소에서만 승인합니다. Fork에는 Secrets를 복제하지 않습니다.

| mode | 입력 및 제한 | 필수 조건 |
| --- | --- | --- |
| jev | dataset=seed/boundary, explicit ids 최대5 | OPENROUTER_API_KEY |
| supervisor | dataset=seed/first-action, explicit ids 최대5; 첫 tool 선택만, 실제 tool 미실행 | OPENAI_API_KEY, OPENROUTER_API_KEY, 운영 model call budget >1 |
| generate | contexts=1..3, 기존 runner 최대12 provider calls/180초 | OPENAI_API_KEY; **미검토 후보 생성, 품질 평가 아님** |
| remaining-score | saved_input=60개 actual 구조화 출력 JSON의 절대 경로 | 기존 `remaining.py score` 사용; 새 모델 호출 없음, semantic correctness=not_measured |
| ragas | ids=r16,r25,v04 중 명시선택, limit=1..3, k=5/vector/gpt-6-luna 고정 | OPENAI_API_KEY, OPENROUTER_API_KEY, EVAL_QDRANT_URL 및 실제 dense/sparse corpus |
| ragas-score | saved_input=기존 export JSONL 절대 경로, limit=1..3 | OPENAI_API_KEY; 검색·생성 재호출 없이 유료 judge/embedding |
| highlight | 입력 sample 고정10개, 후보3개/문항, 기존 pressure-error 중단 | GOOGLE_API_KEY, EVAL_CORPUS |
| highlight-replay | saved_input=기존 실제 raw-rows.json 절대 경로, 최대10개 unique ID | corpus/credential/새 provider 호출 불필요, Ragas deterministic replay |

ID/표본 상한은 invocation 기준이며 반복 실행 총예산은 승인자가 통제합니다. Ragas 지표는 문장/문맥당 다수 judge 호출이므로 문항 수가 API 호출 횟수와 같지 않습니다. 첫-action ID는 `first_action.json`을 확인합니다. seed/boundary ID는 해당 frozen JSON을 확인합니다. UI input은 env로 전달하고 Python argv list로 실행하므로 shell 코드로 확장되지 않습니다.

## PR-target 실제 제공자 A/B (로컬 구현, 운영 검증 대기)

유료 평가는 **사용자가 필요한 PR/릴리스와 실행 시점을 선택**합니다. 두 유료 workflow는 workflow_dispatch 전용이며 PR/Push마다 API를 자동 호출하지 않습니다. 무료 PR 회귀만 자동 필수입니다. 유료 미실행은 품질 미측정/not-run으로 정직하게 보고하되 일반 Merge를 자동 차단하지 않으며, 사용자가 해당 PR/릴리스의 품질 gate를 명시 선택한 경우에만 실제 증거와 최종 승인을 요구합니다. 전역 paid required branch protection은 설정하지 않습니다. PR/base/head/mode/IDs와 예산을 선택하고 Environment 승인 후 실행합니다.

`evaluation-pr-ab.yml`은 trusted develop/main의 workflow_dispatch만 받습니다. PR 번호·정확한 base/head SHA·mode·ID·authorize를 전달하고 **두 실행의 유료 예산**을 사전 승인합니다. `.github/evaluation_ab.py`는 열린 팀 PR/base develop 또는 main/head feat 및 snapshot SHA를 호출 전·A/B 사이·완료 시 검증합니다. fork SHA는 팀 PR에 연결된 검토된 commit이어야 합니다. 새 head/base는 재승인·재실행 대상입니다.

`evaluation` Environment의 required reviewer와 trusted branch 제한은 운영자가 설정해야 하며 이 구현은 설정을 등록하거나 그 존재를 자동 보장하지 않습니다. reviewer는 **secret이 제공되는 PR production 코드를 사전에 검토**해야 합니다. trusted control·독립 정답·canonical scorer overlay는 후보의 정답 완화만 막으며 악성 production 코드의 secret 탈취를 sandbox하지 않습니다. 전용 ephemeral runner, read-only corpus, 제한된 평가 전용 키, 실행 종료 후 runner 폐기가 전제입니다. GH_TOKEN은 candidate subprocess에 전달하지 않고 상태 쓰기는 별도 hosted publish job의 statuses:write만 사용합니다. Actions token을 production 코드가 실행되는 job에 쓰기 권한으로 주지 않습니다.

A/B는 동일한 trusted 평가 requirements, canonical runner/dataset/scorer, explicit IDs와 모델을 사용합니다. JEV는 jev-1.13/독립 라벨 custom DeepEval, supervisor는 gpt-6-luna/첫 선택만, Ragas는 r16/r25/v04의 **양쪽 실제 검색·생성 export 후 동일 built-in judge gpt-6-luna**(별도 venv), highlight는 frozen10/실제 corpus/gemini-3.8-flash입니다. generate·saved-score·replay는 PR live 근거로 인정하지 않습니다. 기준 ref의 정답/평가 변경은 자동 채택하지 않고 독립 검토 후 trusted branch에 먼저 반영해야 합니다. base에 canonical evaluation 경로가 있어야 하며 legacy 이전 base는 검증 대기로 둡니다.

CI 실제 Ragas generation/judge는 사용자 지정 **gpt-6-luna**로 모두 명시 override합니다. `.github/evaluation_luna.py`는 canonical export/eval source를 그대로 실행하며 generation=Responses API/reasoning medium/max output1200, judge=Ragas Instructor chat JSON-object/reasoning medium/max completion1800, SDK retry0/Instructor attempt1로 고정합니다. Instructor가 temperature를 다시 삽입하므로 실제 OpenAI completion 경계에서 temperature/top_p/max_tokens를 제거합니다. A/B identity의 ragas_requests와 harness hash에 이 CI adapter를 포함하고 quality review에서도 같은 설정을 요구합니다. 실제 SDK와 httpx.MockTransport+socket-denied 두 테스트를 무료 highlight dependency job에서 실행합니다. 이 검증은 request/JSON parsing 호환이며 OpenAI의 실제 Luna model ID·endpoint·structured output 지원/성능은 유료 승인 실행 전 **미검증**입니다. 오류 시 실패하며 mini fallback은 없습니다. 평가 source의 기존 default/아카이브 모델은 현재 CI에서 쓰지 않으며 운영 defaults는 변경하지 않습니다.

관측 계약도 검사합니다: Ragas export metadata의 mode/k/generation/embedding/scope 및 errors dict, judge-export context/label 일치, precision/recall/faithfulness 0..1(answer relevancy만 -1..1), JEV 정확한 part·3차원 및 상태 일치, capability만 nullable held를 허용합니다. supervisor는 양쪽 production factory를 네트워크 차단 probe로 호출해 model/timeout/retries/reasoning/responses/max_tokens 관측 설정이 동일함을 확인합니다. 이 probe와 실제 invocation 사이 코드 변조/외부 제공자의 동일 alias 내부 버전 변화는 독립 보장이 아니므로 검토된 코드·동결 runner 전제와 provider alias 한계를 유지합니다.

Ragas live 평가 검색은 **vector/no-rerank**로 동결합니다. 기존 production reranker가 timeout을 fallback으로 숨기는 경로를 품질 성공으로 오인하지 않기 위한 제한이며 reranker 성능·장애 대응을 검증했다는 뜻이 아닙니다. 실제 embedding·generation·built-in judge 호출은 유지합니다. rerank 변경의 품질 승인은 이 mode 증거만으로 충분하다고 주장하지 않으며 별도 오류 관측된 rerank 평가가 필요합니다.

Ragas corpus는 dense/sparse collection 전체 point payload/vector를 양쪽 전후 scroll하여 hash 비교합니다. 운영자에게 **실행 동안 immutable snapshot endpoint**가 필요합니다(동시 변경 후 원복까지 이 hash가 검출한다고 주장하지 않음). retrieved context는 검색 개선의 출력이므로 A/B가 같아야 하는 독립 입력/정답과 구분해 각 hash를 보존합니다. export와 judge의 context/응답/독립 reference 일치는 필수입니다. highlight는 corpus 파일 tree 및 기존 lock/snapshot integrity를 사용합니다. 대형 corpus scroll 비용/시간은 전용 평가 snapshot 크기로 통제합니다.

업로드는 `ab-summary.json`의 sanitized ID/hash/model/설정·개별 점수·null·분모/sum·delta·error type만이며 private provider 출력/log/corpus는 업로드하지 않습니다. 실제 실행 결과 레코드가 정확히 양쪽 선택 ID를 채워야 하며 filename만으로 완료를 인정하지 않습니다. 오류/누락/중복/잘못된 label/context/NaN/키 누락은 실패합니다. 상세 근거는 private 실행 중 파일이고 종료 cleanup으로 삭제하므로 최종 품질 판단에서 원문이 필요하면 별도의 승인된 private evidence 보존 절차가 필요합니다.

publish job은 API로 최신 PR base/head를 마지막 확인하고 exact SHA에 mode별 `evaluation/provider-ab-execution/{jev,supervisor,ragas,highlight}`와 `evaluation/provider-quality`를 기록합니다. 실행 성공만 success이며 **품질은 pending/needs-review**입니다. 제공자 오류·cancel·stale은 execution failure/quality not-run입니다. 게시와 새 push 사이의 경쟁은 이전 SHA status가 새 SHA로 이월되지 않는 GitHub 계약으로 처리합니다. 최종 사용자 결정은 별도 **Approved PR quality decision**(`evaluation-quality-review.yml`)에서 approve/reject/not-applicable과 rationale로 명시합니다. required reviewer 환경 승인 후 PR 코드를 checkout/실행하지 않는 hosted publish가 current base/head 및 trusted 변경 파일 목록(이전 rename 경로 포함)을 다시 확인합니다. 명확한 frontend/docs/graphify-out/README/.gitignore만 exemption이고 그 외 shared settings/runtime/prompts/requirements/evaluation/harness 포함 모든 변경은 보수적으로 4 mode 전부 필수입니다. approve 입력은 `jev=run_id,supervisor=run_id,ragas=run_id,highlight=run_id` 전체이며 누락·mode 실패가 다른 성공으로 덮이지 않습니다. 선택 run은 같은 팀 저장소의 성공 completed workflow_dispatch, trusted develop/main, 현재 review와 동일 trusted commit, 정확한 workflow path/run title(PR/mode/base/head), authorize/evaluate/publish 성공 및 그 run에 묶인 유일한 non-expired artifact를 요구합니다. artifact의 complete/comparable count/IDs/독립 hash/model/설정/corpus/점수·delta·분모 계약도 재검증하며 임의 status나 사용자가 올린 파일을 증거로 받지 않습니다. trusted branch가 바뀌면 새 trusted 버전으로 전체 evidence를 다시 실행해야 합니다. reject는 failure, trusted non-LLM not-applicable은 같은 aggregate quality context의 success이며 관련 변경 exemption은 거절합니다. 새 유료 실행은 aggregate quality를 pending으로 돌려 재검토가 필요합니다. rationale은 보호된 workflow dispatch 입력에서 reviewer가 확인하며 provider summary로 공개하지 않습니다. branch protection/required check 설정은 이 작업에서 변경하지 않습니다.

무료 PR job의 `provider-quality-required` summary는 relevant backend/dependency 변경을 보수적으로 required/not-run, 명확한 non-LLM 변경을 exempt로 표시합니다. evaluation criteria/cases/harness 변경은 protected review required로 보고하되 변경 사실만으로 무료 job을 영구 실패시키지 않습니다. quality decision의 approve에는 `protected_review=true`가 추가로 필수이며, Environment의 독립 required reviewer가 exact base/head의 기준·케이스·harness diff와 frozen trusted baseline을 확인하고 reason을 승인해야 합니다. 이 확인은 전체 실제 A/B 증거를 대체하지 않으며 누락 시 aggregate success를 게시하지 않습니다. 후보 코드가 자기 기준을 승인하는 경로는 없습니다. 새 trusted workflow 도입은 먼저 독립 검토로 trusted develop/main에 반영하는 bootstrap이 필요하며, 아직 반영되지 않은 현재 PR이 이 로컬 구현으로 승인됐다고 주장하지 않습니다. 무료 job 성공은 provider quality 승인 check가 아닙니다. 기존 deploy reusable 무료 게이트는 그대로 유지합니다.

`.github/test_evaluation_ab.py`는 synthetic-only 13개 오프라인 제어/비교/보안 테스트이며 무료 CI에서 명시 실행합니다. 실제 provider entrypoint help/SDK no-network 검사는 기존 평가 환경을 재사용하며 유료 A/B는 승인 전 미실행입니다.

## 실제 인프라 사전조건

수동 job은 `[self-hosted, linux, evaluation]` label의 **전용 ephemeral runner**가 필요합니다. PR job을 이 runner에 보내지 않으며 운영 서비스 호스트를 runner로 사용하지 않습니다. 해당 label runner 미등록 시 GitHub는 job을 queue에 보관하므로, 이는 성공/skip 증거가 아닙니다. 플랫폼 timeout/cancel 상태도 확인합니다.

`evaluation` Environment Secrets: 필요한 mode에만 OPENAI_API_KEY/OPENROUTER_API_KEY/GOOGLE_API_KEY/QDRANT_API_KEY를 공급합니다. Variables:

- `EVAL_QDRANT_URL`: 검색 가능한 Qdrant endpoint. 기존 `kbo_documents_openai` 및 `kbo_documents_sparse` collection과 metadata/embedding 계약을 그대로 사용합니다. 실제 read-only corpus 접근권한이 필요하며 Postgres RAG_DB 연결을 대체로 요구하지 않습니다.
- `EVAL_CORPUS`: highlight corpus 절대 경로. `web.lock`, segments.json, 기존 Qdrant vector files, SQLite transcripts, 실제 MP4/WAV 원문을 갖춘 frozen sample corpus가 필요합니다. runner가 lock을 얻고 새 snapshot을 읽으며 원문 hash 불변을 검증합니다. 원문 수집·재임베딩을 CI에서 만들지 않습니다.
- `saved_input`: trusted runner에 사전 제공한 파일. runner temp/workspace cleanup에 의존하지 않는 별도 read-only 경로를 사용합니다. export 입력에 errors가 있거나 필요한 파일/Secret/ID가 없으면 실패합니다. URL 다운로드나 임의 shell 명령 input은 지원하지 않습니다.

DeepEval/backend 환경은 기존 evaluation requirements(community 0.4.2)를 사용합니다. Ragas built-in judge는 relocated requirements-ragas의 별도 venv, highlight는 `.github/requirements-highlight.txt`(Ragas 0.4.3/community 0.4.1/DeepEval 4.2.8) 환경입니다. community 0.4.2는 Ragas 0.4.3의 vertexai import와 충돌하므로 두 환경을 섞지 않습니다.

## 결과·오류·보안

새 디렉터리/파일만 생성하고 기존 evidence/labels를 덮어쓰지 않습니다. 무료 계약 실패는 CI 실패입니다. 수동 평가의 **실행 오류/부분 실행/blocked/원문 integrity 실패**는 job 실패이며, 품질 불일치는 실제 metric/count로 기록합니다. 독립 benchmark 승인이 없으므로 새로운 품질 threshold를 만들지 않습니다. JEV held 차원을 성공으로 환산하지 않습니다. 기존 deterministic scorer의 metric 정의는 그대로 유지합니다.

CI wrapper는 품질 불일치를 허용하는 JEV/remaining scorer에서도 문서화된 exit 1만 허용하고, 다른 nonzero/signal exit는 실패합니다. 보고서의 상태 enum·고유 ID·선택/실행 count·dimension 구조를 검증하며 remaining score는 canonical manifest의 60개 ID/split/score schema를 요구합니다. Highlight replay는 입력 count와 일치하는 `replayed_cases`, 모든 metric의 유한 rate/분모, 명시적 오류·inconclusive·추가 호출 count를 요구합니다. 필드 누락/NaN/모르는 상태를 성공으로 해석하지 않으며 이는 품질 threshold 추가가 아닌 출력 계약 검증입니다. Ragas export의 `python -I` 실행은 CI-owned `django-exec` launcher가 backend import 경로를 명시적으로 준비하며, 실제 paid export의 네트워크는 차단하지 않습니다. 무료 회귀의 `offline-exec` 네트워크 차단은 그대로 유지하고, launcher는 실제 `export_ragas --help`로 제공자 호출 없이 검증합니다.

Artifact는 `ci-summary.json`만 업로드(7일 보관)합니다. key/environment/raw provider text/원문 media/corpus copy/전체 답변/candidate는 업로드하지 않습니다. generate는 count와 candidate/unreviewed 상태만 공개하고 정답으로 승격하지 않습니다. 실행 중 private 상세 결과는 temp에 존재하지만 수동 job의 마지막 always cleanup으로 삭제합니다. 상세 원문 evidence가 필요한 별도 승인 작업은 기존 CLI로 별도 실행합니다.

Token permissions=contents:read, checkout persist-credentials=false, pull_request_target 미사용, DeepEval/Ragas telemetry 및 tracing/dotenv discovery 비활성입니다. 필요한 Secrets가 없는 실행을 green skip으로 처리하지 않습니다.

## 로컬 검증

기존 Python 3.12 isolated venv로 실제 CI 명령을 실행할 수 있습니다. output은 매번 새 경로를 선택합니다.

```sh
export TIKTOKEN_CACHE_DIR=/new/scratch/tokenizer-cache
"$PY" -I -c 'import tiktoken; tiktoken.get_encoding("cl100k_base")'
DEEPEVAL_DISABLE_DOTENV=1 DEEPEVAL_DISABLE_LEGACY_KEYFILE=1 PYTHON_DOTENV_DISABLED=1 "$PY" -I .github/evaluation_ci.py free --output /new/scratch/free
DEEPEVAL_DISABLE_DOTENV=1 DEEPEVAL_DISABLE_LEGACY_KEYFILE=1 "$HIGHLIGHT_PY" -I .github/evaluation_ci.py highlight-free --output /new/scratch/highlight
"$PY" -I .github/test_evaluation_ci.py
actionlint -shellcheck= .github/workflows/*.yml
```

실제 paid modes는 로컬 검증에서 실행하지 않습니다. 기존 frozen 실제 출력 replay만 network-denied 환경으로 검증했습니다. 제공자/실제 runner/Environment 승인 설정/GitHub에서의 배포 gate 실행은 별도 운영 검증입니다.
