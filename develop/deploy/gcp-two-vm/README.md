# GCP 2-VM deployment

This layout is for the architecture below:

- `frontend-ui` on Vercel
- `backend-api` on GCP VM 1
- `ai-model/v2` on GCP VM 2

Traffic flow:

`Browser -> Vercel frontend -> HTTPS backend domain -> backend private call -> AI private IP`

`AI -> HTTPS backend domain -> Caddy -> backend container`

For the current project-specific manual checklist, see [`NEXT_STEPS.md`](./NEXT_STEPS.md).

## Why this shape

The current codebase already matches this split:

- frontend reads `NEXT_PUBLIC_BACKEND_URL` or `NEXT_PUBLIC_API_URL`
- backend calls FastAPI through `FASTAPI_URL`
- AI calls WAS through `WAS_BASE_URL`

For browser traffic, backend must be served over HTTPS. If the frontend runs on Vercel HTTPS and backend is only plain HTTP by public IP, the browser will hit mixed-content issues.

This deployment therefore uses:

- backend VM: `backend-api` container + `Caddy` for HTTPS
- AI VM: `ai-hub-v2` container only

## Directory layout

- `backend/docker-compose.yml`: backend VM compose
- `backend/Caddyfile`: HTTPS reverse proxy for the backend VM
- `backend/env.backend.example`: backend environment template
- `backend/.env.backend`: backend deployment env
- `ai/docker-compose.yml`: AI VM compose
- `ai/env.ai.example`: AI environment template
- `ai/.env.ai`: AI deployment env

## VM plan

Recommended layout:

- VM 1 `backend-vm`
  - public static IP
  - open inbound `80`, `443`
  - app traffic ends at Caddy and proxies to backend container `8080`
- VM 2 `ai-vm`
  - no public traffic if possible
  - open inbound `8000` only from the backend VM private IP or backend network tag

Both VMs should live in the same VPC and region so `FASTAPI_URL` can use a private IP.

For this repository's current compose layout, `WAS_BASE_URL` should use the backend HTTPS domain, not the backend VM private `:8080`. The backend service is only exposed inside the backend compose network, and Caddy is the published entrypoint.

## Domain

Set a backend domain for HTTPS, for example:

- `api.example.com`
- `api.<your-domain>`

If you do not have a domain, an IP-based testing hostname such as `34-12-34-56.sslip.io` can work temporarily as long as DNS resolves to the backend VM public IP.

That hostname goes into `BACKEND_DOMAIN` and also into the Vercel frontend env:

- `NEXT_PUBLIC_BACKEND_URL=https://api.example.com`

## Backend VM setup

On the backend VM, from the repo root:

```bash
cd backend
cp env.backend.example .env.backend
docker compose up -d --build
```

Set these values in `develop/deploy/gcp-two-vm/backend/.env.backend`:

- `BACKEND_DOMAIN`
- `CORS_ALLOWED_ORIGINS=https://<your-vercel-domain>`
- `TRUST_PROXY_HOPS=1` for the single Caddy hop in this Compose topology
- `FASTAPI_URL=http://<ai-private-ip>:8000`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `INTERNAL_API_KEY`
- `JWT_SECRET`
- `REQUIRE_IDEMPOTENCY_TABLE=false` for demo fallback mode, or `true` after applying `20260531090000_add_ai_was_idempotency_keys.sql`

Backend health checks:

```bash
docker compose ps
docker compose logs -f backend
curl http://127.0.0.1:8080/api/health
curl http://127.0.0.1:8080/api/readiness
curl https://<your-backend-domain>/api/health
curl https://<your-backend-domain>/api/readiness
```

## AI VM setup

On the AI VM, from the repo root:

```bash
cd ai
cp env.ai.example .env.ai
docker compose up -d --build
```

Set these values in `develop/deploy/gcp-two-vm/ai/.env.ai`:

- `WAS_BASE_URL=https://<your-backend-domain>`
- `GEMINI_API_KEY`
- `INTERNAL_API_KEY`
- `ROUTER_API_KEY` only if it should differ from `GEMINI_API_KEY`
- `ENABLE_RAG_MEMORY=false` for the fast demo flow
- `PINECONE_API_KEY` and `PINECONE_INDEX_NAME` only when `ENABLE_RAG_MEMORY=true`

AI health checks:

```bash
docker compose ps
docker compose logs -f ai-hub-v2
curl http://127.0.0.1:8000/health
```

Private connectivity check from the backend VM:

```bash
curl http://<ai-private-ip>:8000/health
```

## Vercel setup

Set one of these in Vercel:

- `NEXT_PUBLIC_BACKEND_URL=https://<your-backend-domain>`
- or `NEXT_PUBLIC_API_URL=https://<your-backend-domain>`

The frontend code already reads either variable, so one is enough.

## Firewall rules

Backend VM:

- allow `tcp:22` from GitHub Actions runner traffic if using hosted runners
- allow `tcp:80` from the internet
- allow `tcp:443` from the internet
- close public `tcp:8080`

AI VM:

- allow `tcp:22` from GitHub Actions runner traffic if using hosted runners
- allow `tcp:8000` only from backend VM private IP, subnet, or backend network tag
- do not expose `8000` to the public internet

## Runtime contract

Use the same `INTERNAL_API_KEY` value on both VMs:

- backend sends it to FastAPI for `/internal/events/profile-updated`
- AI sends it to backend for `/api/...` internal routes

Service URLs should look like:

- backend `backend/.env.backend`: `FASTAPI_URL=http://10.0.0.5:8000`
- AI `ai/.env.ai`: `WAS_BASE_URL=https://api.example.com`

## Deploy updates

After code changes:

Backend VM:

```bash
cd develop/deploy/gcp-two-vm/backend
docker compose up -d --build
```

AI VM:

```bash
cd develop/deploy/gcp-two-vm/ai
docker compose up -d --build
```

## GitHub Actions

The repository includes [`gcp-two-vm-deploy.yml`](../../../../.github/workflows/gcp-two-vm-deploy.yml) for VM deployment over SSH.

Behavior:

- manual trigger with `workflow_dispatch`
- automatic deploy on push to `test/all`
- deploys `backend` and `ai` in parallel
- installs Docker on Ubuntu VMs if it is missing
- normalizes the remote bootstrap script to UTF-8 without BOM and LF line endings before execution

Firewall note:

- if this workflow uses GitHub-hosted runners, SSH `tcp:22` must allow GitHub runner source IPs
- for a fast capstone setup, teams often open `22` more broadly and rely on SSH key auth
- a stricter alternative is a self-hosted runner inside GCP

Required GitHub secrets:

- `GCP_SSH_PRIVATE_KEY`
- `GCP_BACKEND_HOST`
- `GCP_AI_HOST`
- `GCP_BACKEND_ENV`
- `GCP_AI_ENV`

Recommended values:

- `GCP_BACKEND_HOST=34.50.45.68`
- `GCP_AI_HOST=34.50.21.162`
- `GCP_BACKEND_ENV`: full contents of `backend/.env.backend`
- `GCP_AI_ENV`: full contents of `ai/.env.ai`

This repository is currently configured to auto-deploy from `test/all`.

## Minimum verification

1. `https://<backend-domain>/api/health` returns `200`
2. backend VM can reach `http://<ai-private-ip>:8000/health`
3. login/signup requests succeed from the Vercel frontend
4. chat request reaches backend, then AI, then returns to frontend
5. profile update and home recommendation flows still work end-to-end
