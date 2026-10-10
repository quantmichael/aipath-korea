# Agent Collect PHASE 10A — Cloud Run Jobs container foundation

This document prepares a **shadow** Agent-only job. It does not authorize deployment or execution. Frontend and FastAPI stay on Vercel; Service DB, Agent DB and private `agent-raw` storage stay on Supabase. Only the existing one-shot Agent Automation moves from the Mac to Cloud Run Jobs. There is no HTTP server, daemon or Scheduler in this image.

## Target and execution contract

| Item | Proposed value |
| --- | --- |
| Project | `aipath-511213` (`237498080689`) |
| Region | `asia-northeast3` |
| Job | `aipath-agent-shadow` |
| Registry / image | `aipath-agent` / `aipath-agent-shadow` |
| Tasks / parallelism / retries | `1` / `1` / `0` |
| Task timeout | `900s` initial cap; review measured runtime before changing |
| CPU / memory | `1` vCPU / `1Gi` initial proposal |
| Execution | `python -m agent_collect.shadow_job` container entrypoint; calls the same `AutomationRuntime.run_once()` as `python -m agent_collect automation-run --once` exactly once |

The 900-second cap is below the 3600-second global Automation lease and the 1800-second Collection lease. It does **not** prove that all possible due workloads finish within 900 seconds: up to five sequential HTTP Collection jobs and other workers may exceed it. No lease heartbeat exists. Observe duration and lease recovery before production scheduling. A timeout can leave a claimed worker item until its lease expires; inspect DB state before manual retry. Cloud Run Jobs exit after the process exits and do not need a web health endpoint. [Cloud Run Jobs configuration](https://docs.cloud.google.com/run/docs/create-jobs).

## Image and dependencies

`Dockerfile.agent` uses official `python:3.13.2-slim-bookworm`, copies only `agent_collect/` and `requirements.agent.txt`, and runs as an unprivileged user. `.dockerignore` allowlists those files and explicitly excludes secrets, `.env*`, Git, tests, caches, local raw content and logs. `defaults.toml` is copied unchanged. Build and inspect the context before any push:

```sh
docker build -f Dockerfile.agent -t aipath-agent-shadow:phase10a .
docker run --rm --entrypoint python aipath-agent-shadow:phase10a -c 'import agent_collect, agent_collect.runtime; print("import-ok")'
docker run --rm --entrypoint python aipath-agent-shadow:phase10a -m agent_collect --help
docker run --rm aipath-agent-shadow:phase10a  # expected startup rejection, exit 2; no DB call
```

`requirements.agent.txt` pins direct Agent dependencies `supabase` and `openai`; `httpx` and `pydantic` are transitive, Tavily uses the standard-library `urllib`, HTML parsing uses `html.parser`, and settings use `tomllib`. Google auth Python packages are unnecessary because Cloud Run injects Secret Manager values into environment variables. Backend-only `fastapi`, `uvicorn`, `python-dotenv` are excluded. The selected [Supabase 2.32.0](https://pypi.org/project/supabase/2.32.0/) and [OpenAI 2.54.0](https://pypi.org/project/openai/2.54.0/) releases declare Python ranges that include 3.13; this is package metadata evidence, **not** an image build result. The **transitive dependency tree and base-image digest are not locked**, so byte-for-byte build reproducibility is not yet established; pin a reviewed lock and image digest before release. Local Docker build is pending because Docker is unavailable in this workspace.

## PHASE 10A.5 — Cloud Shell + Cloud Build readiness (no build executed)

The Git remote is `https://github.com/quantmichael/aipath-korea.git`. At this review, the Agent source tree, most Agent docs and tests, and container files are **untracked** on local `main`; cloning GitHub would therefore omit them until they are deliberately reviewed, committed and pushed. Do not use `git add -A`: it would pick up unrelated `.codex-finalizer/` and `.codex-ppt-build/` artifacts. Stage only the reviewed paths in the inventory below. Check each selected file before commit. Frontend `api/`, legacy `collect/`, `public/` and Vercel configuration are outside this staging set.

| Git staging inventory | Paths |
| --- | --- |
| Root build and safety files | `.gitignore`, `.dockerignore`, `.gcloudignore`, `Dockerfile.agent`, `requirements.agent.txt`, `cloudbuild.agent.yaml`, `codex_tasks.md` |
| Agent package | `agent_collect/` including Python modules, `config/defaults.toml`, migrations 001–008 and README; Git ignore rules still exclude caches/secrets |
| Agent tests | `tests/test_agent_collect_*.py` (17 current files, including build-context and shadow-job tests) |
| Agent documentation | `docs/AGENT_COLLECT_ARCHITECTURE.md`, `AGENT_DATABASE.md`, `AUTOMATION_RUNTIME.md`, `CLOUD_RUN_DEPLOYMENT.md`, `COLLECTION_ENGINE.md`, `COLLECTION_SCHEDULING.md`, `DATA_CONTRACT.md`, `DISCOVERY_ENGINE.md`, `DISCOVERY_RUNTIME.md`, `DISCOVERY_SCHEDULING.md`, `EVIDENCE_GROUNDING.md`, `EXISTING_SYSTEM_ANALYSIS.md`, `EXTRACTION_BENCHMARK.md`, `EXTRACTION_ENGINE.md`, `NORMALIZATION_ENGINE.md`, `PROCESSING_WORKER.md`, `PRODUCTION_CHECKLIST.md`, `PRODUCTION_OPERATIONS.md`, `PUBLISH_WORKER.md`, `QUALITY_GATE.md`, `RAW_STORAGE.md`, `RUNTIME_INTEGRATION.md`, `SERVICE_PUBLISH.md`, `SOURCE_APPROVAL.md` (all under `docs/`) |

The local tracked `.env.example` uses placeholders for its credential fields and numeric examples for two limits; no credential-shaped value was found by the offline pattern scan. `.env` is ignored and not tracked. This scan is a preflight aid, not a substitute for reviewing staged content and Cloud Shell's actual upload list.

There are **two** upload boundaries. `.gcloudignore` controls which local files `gcloud builds submit` uploads to Cloud Build's source staging bucket; `.dockerignore` controls which uploaded files `docker build` sees. Both allowlist the Agent image inputs and exclude `.env*`, credentials, keys, raw data, logs, test artifacts and the Agent migrations. `.gitignore` protects local credentials from routine Git staging, but `git add -f` could still override it. `agent_collect/config/defaults.toml` and the Python modules must remain included. Actual gcloud upload membership must be checked in Cloud Shell with `gcloud meta list-files-for-upload` before submitting; the local static tests use Git's ignore parser and do not replace that check. [gcloudignore syntax and inspection](https://docs.cloud.google.com/sdk/gcloud/reference/topic/gcloudignore).

From **Google Cloud Shell**, after the reviewed Agent commit is available on GitHub, the operator can run the following commands. They are prepared here only; PHASE 10A.5 does **not** execute them. Cloud Shell supplies `gcloud`; Cloud Build supplies the Docker builder, so no Docker/gcloud installation on the Mac is needed. Replace `<REVIEWED_COMMIT_SHA>` with the reviewed pushed commit. Do not build from an uncommitted or dirty checkout.

```sh
git clone https://github.com/quantmichael/aipath-korea.git
cd aipath-korea
REVIEWED_COMMIT_SHA='PASTE_REVIEWED_40_HEX_SHA'
test "${#REVIEWED_COMMIT_SHA}" -eq 40
git checkout --detach "$REVIEWED_COMMIT_SHA"
test -z "$(git status --porcelain)"
git ls-files --error-unmatch .dockerignore .gcloudignore Dockerfile.agent \
  requirements.agent.txt cloudbuild.agent.yaml \
  agent_collect/config/defaults.toml agent_collect/shadow_job.py
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo 'STOP: .env is tracked' >&2
  exit 1
fi

# Read-only checks before any build or repository creation.
gcloud projects describe aipath-511213 --format='value(projectNumber)'
gcloud services list --enabled --project=aipath-511213
gcloud artifacts repositories describe aipath-agent \
  --location=asia-northeast3 --project=aipath-511213
gcloud meta list-files-for-upload

# Only after reviewing the upload list and confirming Registry/IAM/API state:
TAG="$(git rev-parse --verify HEAD)"
test "${#TAG}" -eq 40
gcloud builds submit . \
  --project=aipath-511213 \
  --region=asia-northeast3 \
  --config=cloudbuild.agent.yaml \
  --substitutions="_IMAGE_TAG=${TAG}"
```

`cloudbuild.agent.yaml` uses the Cloud Build Docker builder with `-f Dockerfile.agent`, and its `images:` entry pushes `asia-northeast3-docker.pkg.dev/aipath-511213/aipath-agent/aipath-agent-shadow:${TAG}` to Artifact Registry. It contains no run/deploy step, Secret reference or runtime environment variable. Both Cloud Build and Artifact Registry list `asia-northeast3` as supported regions. [Build container images](https://docs.cloud.google.com/build/docs/building/build-containers), [Cloud Build locations](https://docs.cloud.google.com/build/docs/locations), [Artifact Registry locations](https://docs.cloud.google.com/artifact-registry/docs/repositories/repo-locations).

The `aipath-agent` Docker repository must already exist before build; if `describe` reports missing, create it as a separate reviewed PHASE 10B action using the command below. Likewise verify `cloudbuild.googleapis.com` and `artifactregistry.googleapis.com` are enabled and identify the **actual** Cloud Build service account for this project; default identities vary by project. It must have permission to push to this repository (for a user-managed build identity, grant `roles/artifactregistry.writer` on the repository) and write build logs. No Cloud Run runtime service account or Secret Manager access is needed for this build-only operation. [Cloud Build identity](https://docs.cloud.google.com/build/docs/cloud-build-service-account), [Artifact Registry access](https://docs.cloud.google.com/artifact-registry/docs/access-control).

```sh
# Prepared for later only if the repository is absent; NOT run in PHASE 10A.5.
gcloud artifacts repositories create aipath-agent \
  --repository-format=docker --location=asia-northeast3 \
  --project=aipath-511213
```

Cloud Build source submission can create a source-staging Cloud Storage bucket and retains uploaded source according to its lifecycle; even a build-only step has resource and cost effects. The monthly billing alert is not a hard cap. No Cloud Run Job, Scheduler, Secret, Service DB write or Provider call is part of PHASE 10A.5. A successful future image build still does not validate container startup, live Agent settings or Cloud Run deployment.

## Production settings and shadow safety

The entrypoint validates **explicit** values before `AgentRuntime.connect()` or any DB/network access. Missing flags fail closed even though `defaults.toml` currently defaults them to false. It requires `APP_ENV=production`, `AGENT_RAW_STORAGE_PROVIDER=supabase`, enabled Discovery/LLM/Automation/Discovery Worker/Collection Worker/Processing Worker, and both `AGENT_PUBLISH_ENABLED=false` and `AGENT_PUBLISH_WORKER_ENABLED=false`. It also checks Agent Supabase, Tavily and OpenAI credential presence without printing values. `AGENT_LLM_MODEL=gpt-5.4-nano` is the Shadow setting. The resolved production OpenAI budget defaults remain 5/day and 100/month. Runtime budget guards remain in place.

Environment overrides are read by `agent_collect/config/settings.py`: `APP_ENV` chooses the budget profile; each `AGENT_*` setting overrides the corresponding `defaults.toml` policy/feature. The entrypoint also rejects a changed resolved extraction model or production OpenAI budget limits. The actual credential paths are `AGENT_SUPABASE_URL` / `AGENT_SUPABASE_SERVICE_KEY` with legacy fallback `SUPABASE_URL` / `SUPABASE_SECRET_KEY` (`agent_collect/runtime.py`), `AGENT_TAVILY_API_KEY` (`agent_collect/runtime.py`) and `OPENAI_API_KEY` (`agent_collect/runtime.py`). The sample below uses Agent-prefixed Supabase variables to avoid ambiguity. Do not include a Service publish setting or Cloud Run override that enables either publish flag.

| Container variable | Proposed Secret Manager secret ID | Purpose |
| --- | --- | --- |
| `AGENT_SUPABASE_URL` | `aipath-agent-supabase-url` | Agent/Service Supabase project URL |
| `AGENT_SUPABASE_SERVICE_KEY` | `aipath-agent-supabase-service-key` | Server-side Supabase key; never expose to browser |
| `AGENT_TAVILY_API_KEY` | `aipath-agent-tavily-key` | Guarded Discovery provider |
| `OPENAI_API_KEY` | `aipath-agent-openai-key` | Guarded Processing provider |

These are proposed **names**, not existing resources. Create and review secret versions separately in PHASE 10B; never put values in Docker args, Git, shell history or docs. Pin specific secret version numbers for rollout when available, instead of blindly rotating `latest`. [Cloud Run Job secret injection](https://docs.cloud.google.com/run/docs/configuring/jobs/secrets).

The service identity should be a dedicated `aipath-agent-shadow-sa@aipath-511213.iam.gserviceaccount.com`. Grant `roles/secretmanager.secretAccessor` on **only these four secrets**, not project-wide. The deployer needs permission to attach the account; the runtime identity does not need Owner, Editor, Billing Admin, IAM Admin or a downloaded JSON key. Cloud Run captures stdout/stderr into Cloud Logging; no extra runtime `roles/logging.logWriter` is needed for that path. Use the managed service identity, not `GOOGLE_APPLICATION_CREDENTIALS`. The image has no Google Cloud client calls. [Service identity](https://docs.cloud.google.com/run/docs/configuring/jobs/service-identity), [Secret access](https://docs.cloud.google.com/secret-manager/docs/manage-access-to-secrets).

## Prepared deployment commands — **do not run in PHASE 10A**

First verify Cloud SDK/Docker, account, project number, API state, region support, Artifact Registry existence, billing/quotas and secret/IAM readiness. The local environment for this phase has no `gcloud`, so enabled APIs and existing resources are **unverified**. APIs needed for the documented local-build flow are Cloud Run (`run.googleapis.com`), Artifact Registry (`artifactregistry.googleapis.com`), Secret Manager (`secretmanager.googleapis.com`) and IAM (`iam.googleapis.com`); Cloud Build (`cloudbuild.googleapis.com`) is needed only if building with Cloud Build. API enablement is a separate change.

```sh
# Read-only checks after gcloud is installed/authenticated:
gcloud projects describe aipath-511213 --format='value(projectNumber)'
gcloud services list --enabled --project=aipath-511213
gcloud artifacts repositories describe aipath-agent --location=asia-northeast3 --project=aipath-511213

# Resource creation: PHASE 10B only, after approval and existence checks.
gcloud artifacts repositories create aipath-agent --repository-format=docker --location=asia-northeast3 --project=aipath-511213
gcloud iam service-accounts create aipath-agent-shadow-sa --project=aipath-511213

# Create the four Secret Manager secrets and their versions through a controlled
# operator process; this document intentionally contains no value-import command.
for secret in aipath-agent-supabase-url aipath-agent-supabase-service-key aipath-agent-tavily-key aipath-agent-openai-key; do
  gcloud secrets add-iam-policy-binding "$secret" --project=aipath-511213 \
    --member='serviceAccount:aipath-agent-shadow-sa@aipath-511213.iam.gserviceaccount.com' \
    --role='roles/secretmanager.secretAccessor'
done

# First commit reviewed Agent/container changes, then use that commit SHA.
TAG="$(git rev-parse --verify HEAD)"
IMAGE="asia-northeast3-docker.pkg.dev/aipath-511213/aipath-agent/aipath-agent-shadow:${TAG}"
docker build -f Dockerfile.agent -t "$IMAGE" .
gcloud auth configure-docker asia-northeast3-docker.pkg.dev
docker push "$IMAGE"

# Creation alone does not execute the Job. Keep --execute-now absent.
gcloud run jobs create aipath-agent-shadow \
  --project=aipath-511213 --region=asia-northeast3 --image="$IMAGE" \
  --service-account=aipath-agent-shadow-sa@aipath-511213.iam.gserviceaccount.com \
  --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=900s \
  --cpu=1 --memory=1Gi \
  --set-env-vars='APP_ENV=production,AGENT_RAW_STORAGE_PROVIDER=supabase,AGENT_DISCOVERY_ENABLED=true,AGENT_LLM_ENABLED=true,AGENT_LLM_MODEL=gpt-5.4-nano,AGENT_PUBLISH_ENABLED=false,AGENT_AUTOMATION_ENABLED=true,AGENT_DISCOVERY_WORKER_ENABLED=true,AGENT_COLLECTION_WORKER_ENABLED=true,AGENT_PROCESSING_WORKER_ENABLED=true,AGENT_PUBLISH_WORKER_ENABLED=false' \
  --set-secrets='AGENT_SUPABASE_URL=aipath-agent-supabase-url:1,AGENT_SUPABASE_SERVICE_KEY=aipath-agent-supabase-service-key:1,AGENT_TAVILY_API_KEY=aipath-agent-tavily-key:1,OPENAI_API_KEY=aipath-agent-openai-key:1'

# PHASE 10B only, after live preflight and read-only Service DB baseline:
gcloud run jobs execute aipath-agent-shadow --project=aipath-511213 --region=asia-northeast3 --wait
gcloud run jobs logs read aipath-agent-shadow --project=aipath-511213 --region=asia-northeast3
```

Replace `:1` with approved, existing secret version numbers and verify all command flags against the installed gcloud version. Do not use `--execute-now` when creating/updating. Artifact Registry URI syntax, Job flags and execution/log commands follow [Artifact Registry repositories](https://docs.cloud.google.com/artifact-registry/docs/repositories/create-repos), [Cloud Run Jobs create](https://docs.cloud.google.com/run/docs/create-jobs), [execution](https://docs.cloud.google.com/run/docs/execute/jobs) and [Job logs](https://docs.cloud.google.com/sdk/gcloud/reference/run/jobs/logs).

## Logs, exit codes and rollback

The entrypoint emits JSON lines for `shadow_startup_validated`, each `shadow_worker_summary` and `shadow_automation_summary`. Records include automation run ID, worker ID, UTC start/finish, status, due/claimed/processed and failure counts where the Worker provides them, and publish-off status. Secrets, raw content and exception messages are omitted. Existing Agent DB audit summaries remain authoritative for detailed provenance. A startup guard failure emits `shadow_startup_rejected` before DB access.

Exit 0 means Automation `COMPLETED` with zero `failure_count`/`failed_count` across returned Worker summaries. Exit 1 means `PARTIAL`, `LOCKED`, `DISABLED`, any Worker exception or any failed item count. Exit 2 means startup rejection or uncaught runtime/transport failure. Budget-blocked items are reported but are not counted as Worker failures. The existing manual CLI differs: `automation-run --once` returns 0 if `summary.errors` is empty, even when a Worker summary contains failures. The container-specific entrypoint closes that monitoring gap without changing the manual CLI or Automation DB behavior.

Rollback before first execution: remove or leave the Job unexecuted; no Agent DB state has changed. After execution: stop manual invocations, keep Scheduler absent, set `AGENT_AUTOMATION_ENABLED=false` for subsequent attempts or remove the Job. Existing in-flight work is not interrupted by a flag update; inspect global/worker leases and Agent audit before retry. Never repair by changing due times, enabling Publish or editing Service rows. Restore a previously reviewed image by immutable digest/tag only after verifying shadow flags and secret versions again.

## Cost safety and readiness

The existing ₩5,000 monthly billing alert is a notification, **not** a hard cap. One task, parallelism one, zero retries, 900-second timeout and manual execution constrain initial exposure. Tavily/OpenAI budget guards remain unchanged; due jobs and HTTP Collection can still incur external charges. Scheduler remains disabled. Before PHASE 10B, confirm current prices/quotas, regional availability, API state, Secret Manager versions/IAM, image build and offline container smoke, real preflight + dry run, Service DB full baseline/snapshot strategy, Agent lease state, log alerting and rollback operator. Natural Publish Gate results may be queued but Service Publish remains off. No Service Publish E2E has been validated.
