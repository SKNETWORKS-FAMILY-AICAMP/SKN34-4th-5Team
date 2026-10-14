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

The SHA-256 manifest checks exact inventory, sizes, contents and SQLite logical
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
or cache. No reembedding, transcription, ingestion, or automatic startup pull occurs.
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

## Regression evidence boundary

`evaluation-regression.yml` → `deterministic` runs:

```sh
python -I .github/evaluation_ci.py offline-exec scripts/tests/test_highlight_corpus.py
```

Ten stdlib unittest cases cover exact backup/restore, SQLite integrity/online WAL,
existing destination and publication-race refusal, malformed/tampered manifest,
path/symlink escapes, unfinished/missing corpus, source mutation and staging cleanup,
overlap and disk checks. `.github/test_evaluation_ci.py` guards this exact command.
The exact command passed locally; hosted CI/full app suites, actual Google Drive
push/pull/auth and restored-Qdrant query verification are separate evidence, not
implied by these filesystem tests. No cloud upload was performed.
