# Qdrant document-store handoff

Scope: existing PostgreSQL pgvector documents → Qdrant only. OpenAI `text-embedding-3-small`, 1536 dimensions, cosine distance and metadata/provenance remain unchanged. No Gemini, YouTube import, multimodal schema or rank fusion. PostgreSQL models/migrations/tables/data remain intact as rollback input. This is implementation evidence, not final independent acceptance.

## Operational cutover (operator approval required)

1. Back up PostgreSQL and stop/pause crawler vector writes during the copy/cutover window. Do not remove any existing tables or volumes.
2. Install `backend/requirements.txt`. In the operator-owned environment set a generated `QDRANT_API_KEY`, `QDRANT_URL=http://qdrant:6333`, `QDRANT_DOCUMENT_COLLECTION=kbo_documents_openai`, and `EMBEDDING_MODEL=text-embedding-3-small`. Do not put secrets in version control. Base and prod Compose include persistent authenticated Qdrant with **no host ports**. Local/dev overlays inherit base. No separate chat-worker service exists in these files; any externally managed worker must receive those same four variables.
3. Start only the approved Qdrant service using the deployment's existing Compose file set/project name. Run migration from a backend one-off process with the new dependency/configuration; do not expose the new application to traffic until copy/check succeeds.
4. Validate and copy existing embeddings without paid reembedding:
   ```sh
   python manage.py migrate_vectors_to_qdrant --dry-run --batch-size 256
   python manage.py migrate_vectors_to_qdrant --batch-size 256
   python manage.py check_index
   python manage.py rag_check
   ```
   Before any target write (including collection creation), migration preflights the full source's exact deterministic point UUIDs, even with `--after-id`, rejecting duplicate identities including `pg:<pk>` fallback collisions and number/string collisions such as `1` and `"1"`. Missing/null/empty-string `doc_id` uses `pg:<pk>`; other IDs must be nonblank strings or finite numbers (not booleans, arrays or objects). Dry-run uses the same identity contract. Preflight keeps one identity per source row in memory; pause source writers to keep the preflight/copy source stable. Copy uses primary-key keyset paging and validates finite/nonzero 1536 vectors per batch; a later vector/storage failure can leave earlier target batches copied. A completed page prints `after-id`; rerun from the beginning idempotently or resume with `--after-id N`. Source rows are never modified/deleted.
5. Compare source/target unique IDs, counts and sample provenance, then switch/restart backend and any external chat worker together. `rag_check` is read-only by default; `--invoke` makes paid embedding/LLM calls and was not run here. Run approved smoke queries after cutover.
6. Retain PostgreSQL backup/source and old application version for rollback. Switching the old application back requires coordinated writer/crawler rollback; new Qdrant-only writes are not mirrored to PostgreSQL. Back up `qdrant_data` using Qdrant snapshot/storage procedures before production rollout.

`build_index` now upserts Qdrant only and never clears SQL or the collection. It still generates paid embeddings when its matching checkpoint is unavailable; use the migration command for existing vectors. Removed/renamed source documents remain until an operator explicitly reconciles obsolete IDs; there is no delete-all default. Checkpoint fingerprints include model/dimension/content, but preexisting advisory A1 remains unfixed: interrupted fingerprint invalidation can relabel a stale `.npy` checkpoint before replacement. Do not treat fingerprint matching alone as checkpoint safety assurance after an interrupted rebuild; review/quarantine affected checkpoints separately before reuse. Incompatible collection metadata/dimension/cosine settings fail explicitly instead of mixing spaces. Storage errors propagate and tool boundaries retain explicit ToolException behavior, but the preexisting assistant initial-retrieval handler logs ordinary retrieval failures and continues with zero documents; not every caller fails closed.

## 문서 벡터 DVC 백업 (2026-10-15)

기존 `build_index`로 현재 CSV/docs 3,437청크를 `text-embedding-3-small` / 1,536차원으로 한 번 임베딩·적재하고, Qdrant 1.19.2의 **문서 컬렉션 네이티브 스냅샷**을 별도 `data/artifacts/document-vectors.dvc`로 관리한다. 하이라이트 벡터/코퍼스와 독립된 산출물이며 하이라이트 재임베딩은 하지 않는다. 스냅샷과 `manifest.json`은 DVC 데이터에만 포함하고 Git에는 포인터만 넣는다.

승인된 운영자 절차:

1. 대상 backend의 Compose 작업 디렉터리와 `DB_HOST=db`, 실제 `embedding_model()`, 컬렉션 상태를 확인한다. 현재 원본은 3,437개 고유 ID, 토큰 추정 651,173개다. 대상이 비어 있지 않거나 모델/차원이 다르면 중단한다. 새 유료 실행에는 별도 승인이 필요하며, 검증 불가능한 다른 체크아웃의 캐시는 재사용하지 않는다.
2. 기존 실행 backend에서 `python manage.py build_index`를 실행한다. 위 체크포인트 주의사항을 적용하고 완료 로그·종료 코드를 보존한다. PostgreSQL 원본 행은 변경하지 않는다.
3. 같은 backend의 `llm.vector_store.client()`로 `create_snapshot(collection_name(), wait=True)`를 호출하고, 내부 `QDRANT_URL`의 `/collections/{collection}/snapshots/{name}`을 HTTP GET으로 다운로드한다. API 키는 메모리의 요청 헤더에만 넣는다. `data/artifacts/document-vectors/`에 스냅샷과 모델·차원·건수·서버 버전·SHA256·바이트 수·소스 fingerprint/revision을 기록한 manifest를 저장한다. 서버나 앱을 재시작하지 않는다.
4. 기존 격리 DVC 환경에서 다음을 실행한다(개인 Drive remote의 로컬 인증을 그대로 사용; 인증 파일/키는 커밋하지 않음):
   ```sh
   DVC_NO_ANALYTICS=1 /Users/yunseongho/.hermes/runtimes/highlight-dvc-tools/bin/python -I -m dvc add data/artifacts/document-vectors
   DVC_NO_ANALYTICS=1 /Users/yunseongho/.hermes/runtimes/highlight-dvc-tools/bin/python -I -m dvc push -r highlight-drive data/artifacts/document-vectors.dvc
   ```
   새 체크아웃에서는 동일 포인터와 별도 로컬 인증을 준비한 뒤 `dvc pull -r highlight-drive data/artifacts/document-vectors.dvc`를 실행한다. SHA256/바이트 수를 manifest와 대조한다. Push 성공은 독립 Pull/복원 성공의 증거가 아니다.
5. **복원은 별도 승인된 격리 Qdrant에서만 검증한다. 기존 대상 컬렉션이 있으면 거부하고 운영 컬렉션을 덮어쓰지 않는다.** 서버 버전 호환성을 먼저 확인한다. 스냅샷을 서버의 `POST /collections/{새로운_검증_컬렉션}/snapshots/upload?wait=true`에 multipart `snapshot`으로 업로드한다. 인증 헤더는 메모리에서만 처리한다. 복원 후 3,437건·모델 metadata·1,536/cosine·유한한 비영 벡터·원본 ID/content/payload 및 저장 벡터 기반 검색을 확인한다(유료 쿼리 임베딩 불필요). 운영 전환/기존 대상 복원은 대상과 충돌 처리에 대한 별도 명시 승인이 필요하다.

이 산출물은 dense 문서 컬렉션만 백업한다. 파생 sparse 색인, 하이라이트 로컬 SQLite 코퍼스, PostgreSQL 및 서비스 설정은 포함하지 않는다. 독립 empty-cache Pull/격리 복원은 후속 검증 대상이며, 앱 로직/테스트 변경이 없어 새 CI job이나 DeepEval/Ragas 유료 평가는 추가하지 않는다.

## Active path inventory

- `v2/club/retrieval.search`: used by club agents, assistant initial retrieval and shared `tools/knowledge.search_kbo_rows`; preserves `(rows, elapsed_ms)` and row keys/distance, including nullable `category`, `status`, `evidence_type`. Scalar/list nested metadata uses the top-level `updated_at` fallback without changing stored metadata.
- `tools/knowledge.vector_search` and keyword fallback: venue/place retrieval used by v1/v2 tools; returns content and complete metadata with match type/distance.
- `v2/course/agent.search_places`, `stadium_anchor`, `course/transport.fetch_rows`, and `club/structured._rows`: Qdrant search/scroll now covers non-vector document enumeration as well as vector queries.
- `build_index`, `check_index`, `rag_check`, `langsmith_eval.number_grounded`: load/read/check paths now use Qdrant.
- `crawling/common/cron_tving._vector_upsert`, reused by `cron_ticket`: Qdrant deterministic upserts, bounded existing-point fetch, unchanged-content vector reuse and provenance preservation. Ticket cleanup no longer deletes SQL vector rows.
- SQL `Document`/`DocumentChunk` models and initial pgvector migration are retained; only the copy command reads them. `rag_test/` remains legacy offline PG experimentation, not application retrieval; it was not converted or run against live services. `tools/place_rag` uses separate relational place knowledge and is unaffected.

Exact substring/keyword compatibility uses bounded-page scans rather than speculative text-search dependencies. Memory remains bounded to top-k/batch candidates; add an indexed text strategy only if real corpus latency warrants it. Scroll enumeration returns all matching documents, not a first-page truncation.

## Reproducible checks (disposable services only)

Use Python 3.14 and pinned requirements. Create distinct disposable Qdrant v1.19.2 and pgvector/pgvector:pg18 containers; bind test ports to loopback only. Set `DB_HOST=127.0.0.1`, the disposable mapped `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, and a disposable `QDRANT_TEST_URL` (never your application URL). Migrate that fresh database first:

```sh
python backend/manage.py check
python backend/manage.py migrate --noinput
PYTHONPATH=backend DJANGO_SETTINGS_MODULE=config.settings OPENAI_API_KEY=isolated-no-api \
  QDRANT_TEST_POSTGRES=1 python -m unittest llm.tests.test_qdrant_store -v
PYTHONPATH=backend OPENAI_API_KEY=isolated-no-api python backend/manage.py test \
  llm.tests.test_tool_packages llm.tests.test_domain_tools llm.tests.test_rag_domain_bindings \
  llm.tests.test_course_candidates llm.tests.test_course_grounding \
  llm.tests.test_v1_tool_migration llm.v2.tests.test_migrated_tools --noinput
```

Provider outputs are mocked/guarded; real model quality is untested and no paid provider API calls were authorized. Qdrant/PostgreSQL storage/query/copy evidence uses real isolated servers, not mocked storage. The integration tests delete only their randomly named test collections and explicitly created disposable PG rows.
