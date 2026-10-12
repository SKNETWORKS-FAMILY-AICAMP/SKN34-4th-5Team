# 평가 CI 실행 안내

## 무료 필수 게이트

`evaluation-regression.yml`을 dev/prod 배포 워크플로가 재사용합니다. `develop` PR/Push, `main` Push에서 배포는 기존 Django check/Next build와 **두 평가 job 모두 성공**해야 가능합니다. `main` PR은 평가 워크플로가 직접 실행합니다. 경로 필터가 없으므로 테스트·데이터 변경도 항상 실행됩니다.

Python 3.12의 별도 job을 사용하며 운영 requirements는 변경하지 않습니다.

| 환경 | 실제 실행 대상 |
| --- | --- |
| DeepEval/backend | seed offline 13개, reviewed boundary offline 26개, test_evaluation 전체, attachment_shared 전체(소유 SQLite runner), test_remaining 전체 및 60-case validate, 기존 attachment offline runner 기본 5개 모듈 전체, relocated test_eval_ragas mock 회귀 |
| highlight isolated | relocated test_highlight_deepeval(DeepEval/Ragas scoring), test_relocation(실제 두 framework offline evaluate 및 legacy CLI/import/data 호환) |
| CI wiring | `.github/test_evaluation_ci.py`: ID cap/중복/주입 거부, argv 실행, 실행 오류와 품질 불일치 구분, trusted-ref 수동 gate와 배포 needs 확인 |

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
| ragas | ids=r16,r25,v04 중 명시선택, limit=1..3, k=5/rerank/gpt-4.1-mini 고정 | OPENAI_API_KEY, OPENROUTER_API_KEY, EVAL_QDRANT_URL 및 실제 dense/sparse corpus |
| ragas-score | saved_input=기존 export JSONL 절대 경로, limit=1..3 | OPENAI_API_KEY; 검색·생성 재호출 없이 유료 judge/embedding |
| highlight | 입력 sample 고정10개, 후보3개/문항, 기존 pressure-error 중단 | GOOGLE_API_KEY, EVAL_CORPUS |
| highlight-replay | saved_input=기존 실제 raw-rows.json 절대 경로, 최대10개 unique ID | corpus/credential/새 provider 호출 불필요, Ragas deterministic replay |

ID/표본 상한은 invocation 기준이며 반복 실행 총예산은 승인자가 통제합니다. Ragas 지표는 문장/문맥당 다수 judge 호출이므로 문항 수가 API 호출 횟수와 같지 않습니다. 첫-action ID는 `first_action.json`을 확인합니다. seed/boundary ID는 해당 frozen JSON을 확인합니다. UI input은 env로 전달하고 Python argv list로 실행하므로 shell 코드로 확장되지 않습니다.

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
