# GCP 2-VM deployment

This directory describes the repository's deployment contract:

`Browser -> Vercel frontend -> HTTPS Caddy -> Backend -> AI private interface`

It is configuration and automation source, not proof that a live GCP deployment currently exists.

## Layout

- `backend/docker-compose.yml`: Backend and Caddy on VM 1
- `backend/Caddyfile`: HTTPS reverse proxy to `backend:8080`
- `backend/env.backend.example`: Backend deployment template
- `ai/docker-compose.yml`: AI service on VM 2
- `ai/env.ai.example`: AI deployment template
- `scripts/bootstrap_and_deploy.sh`: guarded remote bootstrap
- `scripts/smoke_deployment.py`: non-destructive HTTPS Backend smoke

## Container boundaries

The Backend uses the official Node image's `node` user (UID/GID 1000). The AI image uses the dedicated `app` UID/GID 10001. Build steps can run as root, but neither application server does.

Both application services drop all Linux capabilities, set `no-new-privileges`, and use a read-only root filesystem. Their only persistent writable paths are:

- Backend logs: `/var/lib/healthmate/backend/logs` on the host -> `/usr/src/app/logs`
- AI checkpoints: `/var/lib/healthmate/ai/data` on the host -> `/app/data`

The bootstrap creates those host directories with the matching numeric ownership. On the first hardened deployment it stops the old writer before copying Backend logs or AI `checkpoints.sqlite` plus its WAL/SHM sidecars from the former release-local paths without overwriting existing persistent files. If the old Compose/environment files needed for a safe stop are missing, migration fails closed.

Caddy keeps its official runtime shape because it must bind 80/443 and write certificate/config state to its named volumes. It receives only `BACKEND_DOMAIN`, not the Backend's full secret environment.

## Network and firewall contract

Backend VM:

- public ingress: 80/443
- public 8080: closed
- SSH: a trusted administrative path only

AI VM:

- `AI_BIND_ADDRESS` must be the VM's real private interface address
- tcp:8000 ingress must also be restricted to the Backend VM/private network by GCP firewall
- public tcp:8000 ingress is prohibited
- SSH: a trusted administrative path only

Do not put a real private address in the repository. Set `AI_BIND_ADDRESS` in the deployed `.env.ai`; Compose interpolation works because every command uses `docker compose --env-file .env.ai`. The service-level `env_file` alone does not provide Compose interpolation values. Before replacing a release, the bootstrap rejects wildcard, loopback, public, and unassigned addresses; it requires an RFC1918 address present on the AI VM.

GitHub-hosted runner addresses are not a reason to recommend `0.0.0.0/0:22`. Use IAP, a private/self-hosted runner, or tightly scoped and reviewed source rules. Firewall and GCP resources are managed outside this repository.

## Environment files

Copy the templates only on a trusted machine and replace every placeholder:

```bash
cp backend/env.backend.example backend/.env.backend
cp ai/env.ai.example ai/.env.ai
chmod 600 backend/.env.backend ai/.env.ai
```

Important cross-service values:

- Backend `FASTAPI_URL=http://<ai-private-address>:8000`
- AI `AI_BIND_ADDRESS=<ai-private-interface-address>`
- AI `WAS_BASE_URL=https://<backend-domain>`
- the same strong `INTERNAL_API_KEY` on both services
- `APP_ENV=production` and debug routes disabled

## Compose and Caddy validation

From the repository root:

```bash
docker compose \
  --env-file develop/deploy/gcp-two-vm/backend/.env.backend \
  -f develop/deploy/gcp-two-vm/backend/docker-compose.yml config --quiet

docker compose \
  --env-file develop/deploy/gcp-two-vm/ai/.env.ai \
  -f develop/deploy/gcp-two-vm/ai/docker-compose.yml config --quiet

cd develop/deploy/gcp-two-vm/backend
docker compose --env-file .env.backend run --rm --no-deps caddy \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

`caddy validate` parses configuration only. It does not request a certificate or require public DNS.

## Health and readiness

The bootstrap runs `docker compose up -d --build --remove-orphans --wait --wait-timeout 180`. Compose waits for Backend/AI healthchecks and for Caddy to be running, then returns non-zero on failure or timeout. On failure the script prints Compose status and at most 80 recent lines per service, then exits non-zero. It never treats a failed probe as success.

Backend readiness is checked inside the Backend container because port 8080 is intentionally not published on the host:

```bash
docker compose --env-file .env.backend exec -T backend \
  wget -T 20 -t 1 -qO- http://127.0.0.1:8080/api/readiness
```

## Manual deployment workflow

`.github/workflows/gcp-two-vm-deploy.yml` has `workflow_dispatch` only. A push to `test/all`, `main`, or any portfolio branch does not deploy.

Required repository secrets:

- `GCP_SSH_PRIVATE_KEY`
- `GCP_SSH_KNOWN_HOSTS`
- `GCP_BACKEND_HOST`
- `GCP_AI_HOST`
- `GCP_BACKEND_ENV`
- `GCP_AI_ENV`

Required repository variable:

- `GCP_SSH_USER`

`GCP_SSH_KNOWN_HOSTS` must contain entries verified through an independent trusted channel. The workflow does not run `ssh-keyscan`; SSH and SCP require `StrictHostKeyChecking=yes`.

The runner uses `umask 077`, mode 600 for env/key/archive files, and an always-run cleanup step. The remote bootstrap uses a private `mktemp` stage, validates staged Compose/Caddy and expected files before stopping an old writer or replacing the target, and removes both stage and `/tmp/healthmate-deploy.tar.gz` on success or failure. The deployed environment file remains mode 600 because Compose needs it for restarts.

## Safe post-deployment smoke

The old live E2E probe was removed because it disabled TLS verification, created users/data, embedded public hosts, and required production debug traces.

The replacement performs only HTTPS GET requests for Backend health and readiness:

```bash
python scripts/smoke_deployment.py --base-url https://api.example.com
```

Certificate and hostname verification are always enabled, and redirects are rejected. The script has no insecure option, credentials, signup, profile/plan mutation, AI public URL, or `/debug/*` dependency. Its offline self-check is:

```bash
python scripts/smoke_deployment.py --self-test
```

## Non-deploy CI

`.github/workflows/integration-ci.yml` runs without repository secrets:

- Frontend: clean install, lint, build, display contract, Playwright Chromium smoke with mocked routes
- Backend: clean install, contracts, security, tenant, logging, syntax, production dependency audit
- AI: clean Python 3.11 install, `pip check`, import/syntax, credential-free offline regressions
- Containers: both image builds, non-root/write-boundary checks, local-only health, Compose config, Caddy validation, deployment script self-checks

This CI never logs in to a registry and never deploys.

## Current status

No GCP VM, firewall, DNS, domain, Supabase, Gemini, Pinecone, LangSmith, or production endpoint was changed or contacted during Phase 2C-2. A real deployment and the post-deployment smoke remain explicit operator actions after final branch promotion.
