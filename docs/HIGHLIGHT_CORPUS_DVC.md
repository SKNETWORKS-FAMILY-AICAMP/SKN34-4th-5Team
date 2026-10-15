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

## Private Google Drive remote — operator setup

Remote/auth configuration is local to each operator, not supplied by Git. Obtain the
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
in an ignored private location with owner-only permissions. Each operator authenticates their
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

자동 init은 `dvc[gdrive]==3.67.1`의 실제 `Repo.pull(..., remote='highlight-drive')`로
두 trusted Git 포인터를 받는다. gdown·공개 ZIP·파일 URL/SHA 환경변수·fallback은 제거했다.
이미지 빌드에서 `OpenSSL`과 `pydrive2.fs` import를 확인하며, resolver가 오래된
pyOpenSSL과 새 cryptography를 조합하지 않도록 `pyOpenSSL==24.2.1`을 지정한다.

### 인증 파일은 어디에 두는가

기본 배치는 **Compose 프로젝트 루트** 기준이다(실제 내용은 Git에 넣지 않는다):

```text
project-root/
├── docker-compose.yml
├── docker-compose.override.yml
├── .dvc/
│   └── config.local          # highlight-drive URL, 선택 OAuth client ID/secret
└── .secrets/
    └── drive-user.json       # 이미 승인된 사용자 OAuth access/refresh token
```

`config.local`에는 위 수동 설정의 `highlight-drive` remote와 `gdrive://FOLDER_ID`가 필요하다.
`drive-user.json`은 PyDrive2/oauth2client가 저장한 **사용자 토큰**이며 Google Cloud에서
다운로드한 `installed`/`web` OAuth **클라이언트 JSON이 아니다**. 최초 OAuth는 운영자가
별도 로컬 환경에서 완료한다. init은 누락·잘못된 토큰/remote를 거절하고 새 브라우저
인증을 시작하지 않는다. 만료 access token은 기존 refresh token으로만 갱신한다.

```sh
chmod 700 .secrets
chmod 600 .dvc/config.local .secrets/drive-user.json
# 기본 경로가 아닌 경우에만 .env에 실제 절대 호스트 파일 경로를 설정:
# VECTOR_DVC_CONFIG=/absolute/private/config.local
# VECTOR_DRIVE_USER=/absolute/private/drive-user.json
```

두 파일은 각각 컨테이너 `/run/secrets/dvc_config`, `/run/secrets/drive_user`에
**읽기 전용 runtime bind**로 들어간다(`create_host_path: false`). 실제 비밀은 이미지의
COPY/ARG/ENV에 넣지 않으며 Git·Docker context 제외를 유지한다. `.env.example`에는
경로만 있다. 다른 체크아웃/배포에는 ignored 파일이 Git으로 전달되지 않으므로 시작 전에
승인된 보안 경로로 직접 provision해야 한다. 토큰을 채팅/로그/공유 저장소에 붙이지 않는다.

현재 host config의 `gdrive_user_credentials_file`이 예전 `~/.hermes/runtimes/` 절대 경로여도
컨테이너는 이를 따르지 않고 mounted token의 private 임시 **쓰기 가능한 복사본**으로
덮어쓴다. 호스트 config/token은 수정하지 않는다. 수동 host DVC 명령을 쓸 때는 별도로
그 호스트에서 실제 존재하는 올바른 token 경로를 설정해야 한다. 선택 remote의 URL 및
client ID/secret만 전달하므로 호스트 cache 경로/다른 remote/service account는 전파하지 않는다.

```sh
# Local default (실제 기동은 운영자가 승인한 시점에만)
docker compose up -d --build --wait --wait-timeout 4500 backend
# Explicit dev/prod callers must include the overlay:
docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.override.yml up -d --wait --wait-timeout 4500 backend
docker compose -f docker-compose.prod.yml -f docker-compose.override.yml up -d --wait --wait-timeout 4500 backend
```

`vector_cache`는 별도 no-SCM DVC workspace/cache에 정확한 두 이미지 포인터의 절대 경로를
pull하며 copy cache를 사용한다. DVC가 cache 검증·재사용과 부족한 object의 Drive 전송을
담당한다. 성공·실패·child timeout 모두 부모 소유 임시 인증 디렉터리만 정리하고
cache/work/runtime은 유지한다. child에는 QDRANT_API_KEY를 전달하지 않는다.
`vector_runtime`은 immutable source와 다른 쓰기 가능한 볼륨이다.
cache/workspace/runtime 합계 약 7GB 이상과 Qdrant 복원 여유를 확보한다.
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
404-only missing/redirect 금지, 선택 Drive remote·mounted token override·원본 인증 불변·누락/손상 인증의 network 전 거부·절대 포인터/copy cache·timeout 임시 파일 정리,
전송/복원 실패, secret-safe CLI 실패, deployment build/save/dependency를 검사한다.
이 offline 검사는 실제 Drive 전송/hosted CI/서버 배포의 증거가 아니다. 별도 tiny manifest empty-cache 전송 검증도 전체 2.3GB 코퍼스/운영 복원 검증을 대체하지 않는다.

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
