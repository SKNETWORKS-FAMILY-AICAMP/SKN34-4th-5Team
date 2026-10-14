# Highlight corpus backup / restore

This is **non-LLM storage tooling**: no model/retrieval change, ingestion, embedding,
transcription, provider calls, paid quality evaluation or A/B harness. DeepEval/Ragas
quality evaluation is exempt for this storage-only change; deterministic filesystem
and SQLite regression remains required. This corpus is the Specialist's **local
Qdrant directory**, not the document-store Qdrant HTTP service or PostgreSQL.

## Local tooling and backup

From the checkout, install operator dependencies outside the app environment:

```sh
uv venv --python 3.12 "$HOME/.hermes/runtimes/highlight-dvc-tools"
uv pip install --python "$HOME/.hermes/runtimes/highlight-dvc-tools/bin/python" -r scripts/requirements-data.txt
export PATH="$HOME/.hermes/runtimes/highlight-dvc-tools/bin:$PATH"
export DVC_NO_ANALYTICS=1
```

Only run the venv command if that environment does not already exist. The CLI itself
uses Python stdlib; DVC/Drive packages are not backend requirements.

```sh
python -I scripts/highlight_corpus.py backup /absolute/completed/corpus data/artifacts/highlight-corpus
python -I scripts/highlight_corpus.py verify data/artifacts/highlight-corpus
dvc add data/artifacts/highlight-corpus
```

`backup` refuses existing output (including symlinks), overlapping paths, missing
media, incomplete completion metadata, or a changing source. Use a quiescent source;
stop its writers through your normal separately approved process, not this tool.
It never opens Qdrant or changes source locks. All non-transient files are copied;
`web.lock`, `qdrant/.lock` and SQLite WAL/SHM/journal sidecars are omitted. Every SQLite
file is read with `mode=ro`, integrity-checked, online-backed-up, and compared using
table counts and a logical dump digest. Destination SQLite uses DELETE journal mode
so the backup is self-contained, including committed WAL contents. SQLite SHM lock
bytes are excluded from source-change detection because read-only SQLite readers
update them; main DB/WAL checksums and file stat changes still detect writes.

Only incidental regular files named `.DS_Store` are excluded from corpus inventories
and mutation checks; their creation/change must not block restore. Symlinks with that
name still fail, and any `.DS_Store` explicitly listed in an older trusted manifest
is copied and checksum/stat-checked for compatibility. Other hidden/unexpected files
and all expected media/vector/SQLite changes remain checked.

The SHA-256 manifest checks exact corpus inventory, sizes, contents and SQLite logical
integrity. It is not a signature: trust the Git/DVC revision that supplies it.
Copying is staged on the destination filesystem and published by an exclusive native
rename (macOS/Linux); failures clean only the tool's stage and leave unrelated paths
untouched. Ensure space for the backup plus DVC's separate copy cache and a later
runtime restore (roughly three corpus-sized copies). No archives or hardlinks.

The source-backed local backup measured on 2026-10-14 has **922 segments**, **922
transcripts**, **922 stored points**, separate index mode and recorded dimension 768.
It contains 1,865 corpus files / 2,258,050,714 bytes plus its manifest. The Qdrant
metadata names `highlight_multimodal_experiment_v1` and video/audio/text 768-dimensional
vectors; storage counts were measured using read-only SQLite, **not a Qdrant query**.
Original source and private Compose/DeepEval files were unchanged. DVC tracks the
folder incrementally; only `.dvc` metadata and ignore rules go in Git, never media,
vectors, `.dvc/cache`, `.dvc/config.local` or OAuth tokens. Do not run the app against
the DVC artifact or cache: app locks/writes would invalidate backup hashes.

## Private Google Drive remote — pending operator setup

**No remote URL, OAuth or upload has been configured or verified yet.** Obtain the
actual folder ID from the user's private Google Drive folder URL. Share that folder
explicitly with only the accounts that need access (not public/link-only sharing).
Official reference: [DVC Google Drive storage](https://dvc.org/doc/user-guide/data-management/remote-storage/google-drive).
Commands below are templates; replace placeholders only after the folder is supplied:

```sh
dvc remote add --local -d highlight-drive 'gdrive://FOLDER_ID'
dvc remote modify --local highlight-drive gdrive_user_credentials_file '/absolute/private/location/drive-user.json'
# If the default DVC OAuth app is blocked, use your own Desktop OAuth client:
dvc remote modify --local highlight-drive gdrive_client_id 'YOUR_DESKTOP_CLIENT_ID'
dvc remote modify --local highlight-drive gdrive_client_secret 'YOUR_DESKTOP_CLIENT_SECRET'
# First push authenticates interactively in your local browser:
dvc push -r highlight-drive data/artifacts/highlight-corpus.dvc
```

For a personal OAuth client: create a Google Cloud project, enable **Google Drive
API**, configure consent/test users, and create a **Desktop app OAuth** client. No
GCS bucket, billing or paid bucket is required. Enter credentials privately; avoid
shell history/log capture and never paste tokens/passwords into chat. Configure
client details and token cache only in local DVC config; user credentials belong
outside the checkout with owner-only permissions. Each operator authenticates their
own account; never commit/share user authorization caches. Remote configuration is
local and must be provisioned again on another deployment. Do not enable auth
implicitly in CI or on every app startup.

Deployment credentials are explicitly/manual provisioned secrets, not baked into an
image. Interactive user OAuth can be provisioned for an approved deployment account;
refresh tokens remain sensitive. A service account is **not automatically storage-
quota-compatible with personal My Drive**; do not assume sharing a personal folder
solves ownership/quota constraints. Validate the Drive account/storage arrangement
before choosing a service account or delegated Workspace setup.
Google's free 15 GB is shared by Drive/Gmail/Photos; retained DVC versions consume
space. Plan retention deliberately; this tool does not delete cloud versions/cache.

## Deployment: pull → verify → restore → start

Install the same isolated tooling and provision the real local remote/auth as above,
then from the selected trusted Git/DVC revision:

```sh
dvc pull -r highlight-drive data/artifacts/highlight-corpus.dvc
python -I scripts/highlight_corpus.py verify data/artifacts/highlight-corpus
python -I scripts/highlight_corpus.py restore data/artifacts/highlight-corpus /absolute/NEW/runtime/highlight-corpus
```

The runtime destination **must not exist**. Restore validates before publication and
copies rather than linking, so mutable runtime data cannot affect the DVC artifact
or cache. No reembedding, transcription or ingestion occurs. The manual command remains available; the Docker init below now automates startup pull.
To roll back, deliberately select a prior trusted `.dvc` revision through your normal
Git workflow, pull that revision, restore to another **new** runtime location and
switch the mount after verification. Never overwrite an existing runtime or reuse
an old store by merging files; retain the previous runtime until rollback is no
longer needed. This tool does not checkout revisions or restart services.

For a host-run backend set `HIGHLIGHT_MULTIMODAL_CORPUS` to that new runtime path.
For Compose, create a **separate operator override**, without editing base Compose:

```yaml
services:
  backend:
    environment:
      HIGHLIGHT_MULTIMODAL_CORPUS: /highlight-multimodal
    volumes:
      - /absolute/NEW/runtime/highlight-corpus:/highlight-multimodal
```

Mount writable (local Qdrant locking needs it). Only after restore/verification,
start the backend with your existing project/profile and this override, e.g.
`docker compose -f docker-compose.yml -f /absolute/highlight-override.yml --profile local up -d backend`.
Choose the deployment's actual project name/options; this is an explicit operator
step, not an instruction to restart current stacks. Normal provider credentials are
still required for subsequent chat queries, but **restore itself calls no provider**.

## Docker 자동 준비 (문서 + highlight, 2026-10-15)

`docker-compose.override.yml`은 기본 local Compose에서 자동 선택되고 dev/prod 배포
workflow도 명시적으로 선택한다. backend는 `vector-init`의 성공 종료를 기다린다.
CI는 `dev-vector-init:latest`를 별도 빌드·이미지 묶음에 포함하며 이미지에는 Python/DVC,
검증 코드, **두 Git 포인터만** 들어간다. backend에는 DVC 패키지를 추가하지 않는다.

자동 init은 기존 공개 DVC 데이터의 정확한 두 포인터 closure만 담은 **단일 ZIP**을
`gdown==6.4.2`의 file download로 읽는다. 기존 공개 Drive 폴더에 데이터만 추가했으며
원본 1,870개 object와 공유 권한은 변경하지 않았다. 기본 파일 URL과 trusted SHA256은
이미지·overlay·`.env.example`에서 일치하므로 새 secret/env 배치가 필요 없다.
선택 override는 `VECTOR_GDRIVE_URL`에 Google Drive **파일** URL 또는 ID,
`VECTOR_GDRIVE_SHA256`에 해당 ZIP의 검토된 SHA256을 함께 지정한다.
**기존 folder URL override는 삭제해 기본값을 쓰거나 파일 URL/SHA 쌍으로 이관해야 한다.**
폴더 fallback이나 재시도는 없고 쿠키는 사용하지 않으며 connect/read timeout은 30/120초이다.
단일 파일은 요청 수를 줄이지만 Google의 자동 요청 차단 해제를 보장하지 않는다.

init은 `.dvc/config.local`·`.secrets/drive-user.json`을 읽거나 마운트하지 않는다.
수동 private backup/push의 DVC OAuth 절차와 `scripts/requirements-data.txt`는 그대로 유지하며
해당 로컬 인증 파일은 계속 Git·Docker build context에서 제외한다. init은 공유 권한이나
원본 인증 파일을 변경하지 않는다. 누락·손상·접근 불가 파일은 실패로 처리한다.

```sh
# Local default (실제 기동은 운영자가 승인한 시점에만)
docker compose up -d --build --wait --wait-timeout 4500 backend
# Explicit dev/prod callers must include the overlay:
docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.override.yml up -d --wait --wait-timeout 4500 backend
docker compose -f docker-compose.prod.yml -f docker-compose.override.yml up -d --wait --wait-timeout 4500 backend
```

`vector_cache`는 별도 no-SCM DVC workspace/cache에 정확한 두 이미지 포인터의 절대 경로를
pull하며 copy cache를 사용한다. 먼저 빈 임시 local remote로 DVC pull을 시도해 완전한
로컬 cache는 다운로드 없이 재사용한다. cache가 부족하면 task-owned 임시 디렉터리에
ZIP 한 개를 내려받아 **전체 SHA256을 먼저 검증**한다. member는 중복·경로 이탈·symlink·
특수 파일 없이 `files/md5/<hex2>/<hex30>[.dir]`의 일반 파일만 허용하고 개별 MD5도 확인한다.
새 빈 local remote로만 풀고 원래 artifact 경로 복원과 cache 확인은 DVC가 담당한다.
성공·실패·child timeout 모두 임시 전송 파일만 정리하고 cache/work/runtime은 유지한다.
`vector_runtime`은 immutable source와 다른 쓰기 가능한 볼륨이다.
유지할 디스크는 cache/workspace/runtime 합계 약 7GB 이상, 임시 ZIP과 압축 해제 object 약 4.6GB 및 Qdrant 복원 여유를 확보한다.
첫 2.3GB pull은 3600초, 각 Qdrant HTTP는 300초로 제한하고 배포 wait 4500초/SSH 90분을
사용한다. 두 native flock은 동시 init을 비파괴 거절한다. 다운로드/검증/복원 실패는
backend 시작을 차단하며 재임베딩·유료 모델 호출은 없다.

문서는 Qdrant 1.19.2, text-embedding-3-small/1536/Cosine와 snapshot SHA/크기를 검증하고
비어 있는 **새 컬렉션을 독점 생성**하여 업로드한다. 복원 성공 뒤 collection metadata의
snapshot provenance를 기록한다. 재실행은 provenance·모델·차원·비영 건수를 확인한다:
건수가 같다는 이유만으로 기존 외부 컬렉션을 채택하지 않으며 정상 app 추가 건수는 허용한다.
Highlight는 기존 `transfer(..., restore=True)`로 새 디렉터리에 복사하고 receipt를 남긴다.
재실행은 receipt/원본 manifest, 완료/media/layout/dimension/저장 행수만 확인한다. 쓰기 가능한
runtime 전체 checksum이나 QdrantClient 재개방을 하지 않으므로 정상 runtime 갱신/lock을 보존한다.
backend의 effective 경로는 `/vector-runtime/highlight-corpus`이며 기존 개인 Compose/mount는
수정하지 않는다. 문서는 **dense만** 복원하고 sparse index/retrieval 정책은 변경하지 않는다.

기존 populated collection/corpus, 불완전한 복원, 다른 포인터로의 자동 갱신은 거절한다.
실패 중 생성된 pending collection/runtime은 삭제하지 않는다. 운영자는 원인을 확인하고
별도 승인 아래 새 collection·새 Compose project/runtime으로 복원·검증·전환한다.
기존 자료의 자동 adoption/overwrite/delete/refresh는 없다. `down -v`는 데이터를 지우므로
사용하지 않는다. 원래 수동 restore와 이전 revision rollback 절차는 그대로 유효하다.

새 회귀의 실제 CI 연결: `evaluation-regression.yml` → `deterministic` →
`python -I .github/evaluation_ci.py offline-exec scripts/tests/test_vector_startup.py`.
first/reuse, 손상/누락, foreign/empty/incomplete, symlink/overlap/version,
404-only missing/redirect 금지, 공개 파일 URL 경계·SHA256/개별 MD5·unsafe ZIP 거절·단일 cookie 없는 전송·local DVC 연결·cache 재사용·timeout 임시 파일 정리,
전송/복원 실패, secret-safe CLI 실패, deployment build/save/dependency를 검사한다.
이 offline 검사나 held-artifact smoke는 새 Drive 전송/hosted CI/서버 배포의 증거가 아니다.

## Regression evidence boundary

`evaluation-regression.yml` → `deterministic` runs:

```sh
python -I .github/evaluation_ci.py offline-exec scripts/tests/test_highlight_corpus.py
```

Eleven stdlib unittest cases cover exact backup/restore, SQLite integrity/online WAL,
existing destination and publication-race refusal, malformed/tampered manifest,
path/symlink escapes, unfinished/missing corpus, source mutation and staging cleanup,
overlap and disk checks. `.github/test_evaluation_ci.py` guards this exact command.
The exact command passed locally; hosted CI/full app suites, actual Google Drive
push/pull/auth and restored-Qdrant query verification are separate evidence, not
implied by these filesystem tests. No cloud upload was performed.
