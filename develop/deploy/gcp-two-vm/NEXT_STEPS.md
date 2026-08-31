# Remaining deployment steps

The repository contains hardened source configuration and non-deploy CI. Canonicalization did not perform a GCP deployment.

## 1. Confirm the canonical revision

Deploy only an explicitly reviewed commit from canonical `main`. Do not use a historical or intermediate branch as an automatic production trigger.

## 2. Prepare the network outside the repository

- Backend: public 80/443, public 8080 closed
- AI: tcp:8000 reachable only from the Backend VM/private network
- SSH: IAP, a private/self-hosted runner, or tightly scoped reviewed source rules
- no recommendation of public `0.0.0.0/0:22`

Record the AI VM's actual private interface address in the deployment environment as `AI_BIND_ADDRESS`; do not commit it.

## 3. Prepare trusted SSH configuration

Verify both VM host-key fingerprints through an independent trusted channel such as the cloud console or an established administrative connection. Store the resulting known-host entries in `GCP_SSH_KNOWN_HOSTS`.

Repository secrets:

- `GCP_SSH_PRIVATE_KEY`
- `GCP_SSH_KNOWN_HOSTS`
- `GCP_BACKEND_HOST`
- `GCP_AI_HOST`
- `GCP_BACKEND_ENV`
- `GCP_AI_ENV`

Repository variable:

- `GCP_SSH_USER`

Do not discover and trust host keys inside the deployment run.

## 4. Prepare service environments

Use `backend/env.backend.example` and `ai/env.ai.example` as field lists. Replace all placeholders outside Git and ensure:

- shared `INTERNAL_API_KEY` is strong and identical
- Backend `FASTAPI_URL` targets the AI private address
- AI `AI_BIND_ADDRESS` is the AI VM private interface
- AI `WAS_BASE_URL` is the Backend HTTPS origin
- production debug and tracing exports remain disabled unless separately approved

## 5. Require green non-deploy CI

The `Integration CI` workflow must pass its Frontend, Backend, AI Python 3.11, and Containers jobs. It uses fixtures only and does not deploy.

## 6. Dispatch deployment manually

Run `Deploy GCP Two VM` with `workflow_dispatch` only after the promoted revision, trusted network path, variables, and secrets are reviewed. The workflow deploys Backend and AI in parallel; it does not mutate firewall, DNS, or other GCP resources.

## 7. Verify without destructive probes

After deployment, run:

```bash
python develop/deploy/gcp-two-vm/scripts/smoke_deployment.py \
  --base-url https://<backend-domain>
```

This checks TLS-verified Backend health and readiness only. Perform any authenticated application test with a separately managed test account and cleanup plan; no such production test is automated here.
