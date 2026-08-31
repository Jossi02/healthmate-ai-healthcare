# Agent Context

## 1. Project Summary

This repository is a health coaching monorepo with four active areas:

- `develop/frontend-ui`: Next.js 16 + React 19 user-facing app
- `develop/backend-api`: Express + Supabase WAS layer
- `develop/ai-model/v2`: FastAPI + LangGraph AI runtime
- `develop/deploy/gcp-two-vm`: deployment files for GCP backend VM and GCP AI VM

Ignore `develop/ai-model/v1` unless someone explicitly asks for legacy behavior.

## 2. Runtime Architecture

The current production shape is:

- frontend on Vercel
- backend on GCP VM 1
- AI on GCP VM 2
- Supabase as the main database

Main traffic flow:

`Browser -> Vercel frontend -> backend-api -> ai-model/v2`

AI also calls the backend through internal routes when it needs profile and plan data.

## 3. Current Deployment Facts

Current repository contract:

- deployment workflow: manual `workflow_dispatch` only
- automatic deployment from `test/all` or portfolio branches: disabled
- SSH trust: reviewed `GCP_SSH_KNOWN_HOSTS` plus `StrictHostKeyChecking=yes`
- SSH username: repository variable `GCP_SSH_USER`
- Backend public ingress: 80/443 only; 8080 stays private to Compose
- AI ingress: `AI_BIND_ADDRESS` private interface plus GCP firewall limited to the Backend/private network

Historical public addresses and a past successful deployment are not evidence for the current candidate. Phase 2C-2 did not contact or mutate GCP, DNS, Supabase, external AI providers, or any production endpoint.

## 4. Source Of Truth Rules

These rules matter more than local convenience:

- The browser must call `backend-api`, not `ai-model` directly.
- `backend-api` owns `session_id` generation for chat.
- Chat session format is `user_id:YYYY-MM-DD` and uses `Asia/Seoul`.
- Profile, workout plan, meal plan, and feedback persistence belong to backend + Supabase.
- `selected_ai_persona` is read-only from the AI side.
- AI may read persona from profile data, but must not change it from natural language.
- Frontend local storage is allowed only for UI-level cache and auth/session helpers, not as the final source of truth for plans.

## 5. Important File Map

### Frontend

- `develop/frontend-ui/app/page.tsx`
  - home screen and recommendation UI
- `develop/frontend-ui/app/chat/page.tsx`
  - chat UI and feedback buttons
- `develop/frontend-ui/app/profile/page.tsx`
  - profile screen and logout behavior
- `develop/frontend-ui/components/BottomNav.tsx`
  - bottom tab visibility rules
- `develop/frontend-ui/app/context/PlanContext.tsx`
  - plan/profile fetch and mutation logic

### Backend

- `develop/backend-api/src/server.js`
  - server bootstrap
- `develop/backend-api/src/app.js`
  - route mount points
- `develop/backend-api/src/controllers/chatController.js`
  - chat gateway and feedback save logic
- `develop/backend-api/src/routes/chat.js`
  - chat routes
- `develop/backend-api/src/config/db.js`
  - Supabase client
- `develop/backend-api/supabase/migrations/`
  - schema changes

### AI

- `develop/ai-model/v2/app/main.py`
  - FastAPI entry
- `develop/ai-model/v2/app/routers/chat.py`
  - chat endpoint
- `develop/ai-model/v2/app/routers/home.py`
  - home recommendation endpoint
- `develop/ai-model/v2/app/graph/`
  - LangGraph flow

## 6. Recent Changes Already Applied

### Frontend hardening

These were already fixed:

- null-safety issues in `app/page.tsx`
- unsafe recommendation header rendering in `app/recommend/page.tsx`
- bottom nav showing on auth screens
- profile save showing success locally even if backend save failed
- logout not clearing chat session storage

### Chat feedback feature

Explicit thumbs feedback was added.

Current behavior:

- feedback is stored only when the user clicks `like` or `dislike`
- `like` saves immediately
- `dislike` requires at least one reason or a comment
- feedback is attached to an assistant response snapshot, not to all chat turns automatically

### Migration cleanup

Supabase migration filenames were normalized so versions are unique.

Important migration files now include:

- `20260320203401_remote_baseline.sql`
- `20260412021518_align_v2_contract.sql`
- `20260412072732_add_exercise_item_metrics.sql`
- `20260413034000_add_chat_feedback.sql`

## 7. Current Chat Feedback Contract

Frontend chat flow:

- `POST /api/v1/chat`
- backend forwards to FastAPI `/chat`
- frontend creates a local `clientMessageId` for each assistant reply
- feedback later uses that `clientMessageId`

Backend feedback route:

- `POST /api/v1/chat/feedback`

The backend saves feedback into `public.chat_feedback` in Supabase.

## 8. Current Operational Cautions

### Do not treat old root docs as source of truth

The old root `docs/` files were removed on purpose. The current truth is in:

- service code
- service-level `PROJECT_CONTEXT.codex.md` files
- current deployment files
- current migrations

### Local shell encoding can mislead you

Some Korean strings render as broken text in local PowerShell output. That does not always mean the source file is corrupted. Verify by checking the actual file contents in an editor before rewriting user-facing strings.

### Local Next.js runs can fail inside the sandbox

In this environment, local `next dev` and `next build` can fail with `spawn EPERM` inside the sandbox. Outside the sandbox, build was confirmed to work. Do not misdiagnose this as an app code failure without checking the environment.

### Backend chat needed a proxy fix

The backend chat gateway currently uses:

- `const axios = axiosModule.default || axiosModule`
- `proxy: false`

This was necessary because the local environment had proxy variables pointing to an invalid local proxy. Do not remove that change casually.

### Ignore local artifacts

Do not commit or document these as project state:

- `node_modules/`
- `logs/`
- `.temp/` tool artifacts
- temporary UI test logs
- `__pycache__/`

## 9. Safe Editing Rules

Before changing behavior, check these first:

- frontend API usage should still point to backend, never directly to AI
- workout taxonomy stays:
  - `upper_body`
  - `lower_body`
  - `cardio`
  - `stretching`
- meal taxonomy stays:
  - `breakfast`
  - `lunch`
  - `dinner`
- date format stays `YYYY-MM-DD`
- session id stays KST daily format

If you change contracts, update all three layers:

- frontend
- backend
- AI

and update migration or docs accordingly.

## 10. Recommended Read Order For A New Agent

1. `docs/AGENT_CONTEXT.md`
2. `docs/CHAT_FEEDBACK_FEATURE.md`
3. `develop/frontend-ui/docs/PROJECT_CONTEXT.codex.md`
4. `develop/backend-api/docs/PROJECT_CONTEXT.codex.md`
5. `develop/ai-model/docs/PROJECT_CONTEXT.codex.md`
6. `develop/ai-model/v2/docs/was_api_contract.md`
7. `develop/deploy/gcp-two-vm/README.md`
