# HealthMate 로컬 실행 가이드

이 문서는 현재 `main`에 함께 보존된 Frontend, Backend / WAS, AI Server를 로컬에서 준비하는 방법을 설명합니다. 저장소 루트에는 세 서비스를 한 번에 실행하는 script나 통합 Compose가 없습니다.

## 1. 저장소 복제

기본 브랜치인 `main`을 복제합니다.

```bash
git clone https://github.com/Jossi02/healthmate-ai-healthcare.git
cd healthmate-ai-healthcare
```

현재 `main`에는 다음 경로가 있어야 합니다.

- `develop/frontend-ui`
- `develop/backend-api`
- `develop/ai-model/v2`
- `develop/deploy`

## 2. 보안 원칙

- 실제 API key, password, JWT signing key를 저장소에 commit하지 마세요.
- 각 component의 `.env.example`은 변수 이름과 placeholder만 제공하는 template입니다.
- 공개 Git history에 존재했던 과거 credential을 복사하거나 재사용하지 마세요.
- `.env`, `.env.local`은 로컬 전용이며 root `.gitignore`에서 제외됩니다.
- `NEXT_PUBLIC_*` 변수는 browser에 노출되므로 secret을 넣으면 안 됩니다.
- Backend의 `JWT_SECRET`과 `INTERNAL_API_KEY`는 시작 시 필수이며 공백·example/placeholder 값을 거부합니다. Production에서는 각각 최소 32자여야 합니다.
- Backend와 AI Server에는 같은 `INTERNAL_API_KEY`를 설정합니다. Backend→AI 요청은 `x-api-key`로 인증하고, browser→Backend 요청은 `Authorization: Bearer <JWT>`를 사용합니다. CORS credentials는 허용하지 않습니다.

## 3. 사전 요구사항

- Git
- Node.js 20.9 이상과 npm. Backend Dockerfile 기준 환경은 Node.js 24입니다.
- Python 3.11(AI Dockerfile 기준)
- 실제 저장 기능을 사용할 경우 Supabase project와 compatible schema
- AI 응답을 생성할 경우 사용 가능한 Gemini API access

AI v2는 version lower bound 중심의 `requirements.txt`를 사용하고 Python lockfile이 없습니다. Integration CI와 Dockerfile은 Python 3.11에서 clean resolve/install과 `pip check`를 수행하지만 서로 다른 시점의 dependency identity까지 고정하지는 않습니다. Windows의 standard-library `zoneinfo`에 필요한 IANA database는 direct `tzdata` requirement로 포함합니다. 한 platform의 `pip freeze`를 lock으로 오인하지 않기 위해 constraints는 추가하지 않았습니다.

## 4. Supabase requirements

인증, profile, chat, plan 저장에는 Supabase/PostgreSQL schema가 필요합니다. 추적된 migration source는 다음 경로에 있습니다.

- [`develop/backend-api/supabase/migrations`](../develop/backend-api/supabase/migrations)

Supabase `.temp`는 CLI local state이며 source가 아닙니다. 현재 snapshot에서는 source에서 제거되고 ignore됩니다.

저장소에는 모든 Supabase 환경에 공통으로 적용할 수 있는 확정된 root initialization command가 없습니다. Migration 적용 방식은 사용하는 Supabase project와 CLI/workflow에 맞춰 결정해야 합니다. Schema가 준비되지 않으면 Backend readiness와 데이터 기능이 실패할 수 있습니다.

필요한 server-side 설정 이름은 `develop/backend-api/.env.example`에서 확인합니다. `SUPABASE_SERVICE_ROLE_KEY`는 Backend local environment에만 두고 Frontend나 `NEXT_PUBLIC_*` 변수에 노출하지 마세요.

## 5. Backend / WAS

Backend template을 복사해 local `.env`를 만들고 placeholder를 자신의 local 값으로 교체합니다.

```bash
cd develop/backend-api
cp .env.example .env
npm ci
npm run dev
```

PowerShell에서는 `Copy-Item .env.example .env`를 사용할 수 있습니다.

주요 변수:

| 변수 | 용도 |
| --- | --- |
| `PORT` | Backend port. 예제 기본값은 8080 |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Backend 전용 Supabase access key |
| `FASTAPI_URL` | AI Server base URL |
| `INTERNAL_API_KEY` | Backend와 AI Server 사이의 shared local key |
| `JWT_SECRET` | 사용자 JWT signing key |
| `CORS_ALLOWED_ORIGINS` | 정확히 허용할 origin의 쉼표 구분 allowlist |
| `TRUST_PROXY_HOPS` | 신뢰할 reverse proxy hop 수. 직접 실행은 0 |
| `AI_REQUEST_TIMEOUT` | AI request timeout |
| `AUTH_RATE_LIMIT_WINDOW_MS` | signup/login rate-limit window (ms) |
| `AUTH_RATE_LIMIT_MAX` | 위 두 인증 endpoint의 window당 최대 요청 수 |
| `REQUIRE_IDEMPOTENCY_TABLE` | idempotency table 강제 여부 |

`INTERNAL_API_KEY`와 `JWT_SECRET`은 비워 두거나 `.env.example`의 placeholder를 그대로 사용하면 Backend가 시작되지 않습니다. Development/test에서는 짧더라도 명시적인 non-placeholder 값을 deterministic local test에 사용할 수 있지만, 값 자체는 모든 환경에서 필수입니다. Production에서는 두 secret이 32자 미만이어도 시작되지 않습니다. `CORS_ALLOWED_ORIGINS`는 정확한 origin을 쉼표로 구분해 입력하고 `*`는 사용할 수 없습니다. 값을 생략하면 development/local에서만 `http://localhost:3000`과 `http://127.0.0.1:3000`을 사용하며 production에서는 명시값이 필요합니다. Rate limit은 signup/login에만 적용됩니다. `TRUST_PROXY_HOPS`는 직접 실행 시 0으로 두고, 신뢰하는 단일 Caddy/Nginx 뒤에서만 1로 설정합니다.

검사 명령:

```bash
npm run test:contracts
npm run test:security
npm run test:tenant
npm run test:logging
npm audit --omit=dev
```

위 보안 검사는 외부 service 호출 없이 설정·인증 계약을 확인합니다. `/api/v1/ai` legacy 경로는 현재 callsite가 없고 AI v2 계약과 달라 정적 `410`을 반환합니다. `/api/v1/admin`은 role source가 아직 없어 정적 `404`를 반환합니다.

Backend file log는 `logs/error.log`와 `logs/combined.log` 각각 5 MiB × 5 files로 제한됩니다. Morgan은 `method path status response-time`만 기록하며 path에서 query를 제거하고 Authorization/Cookie header를 포함하지 않습니다. 공통 logger는 credential을 `[REDACTED]`로 치환하고 Axios/Error의 raw request·response object를 직렬화하지 않습니다.

## 6. AI Server

새 terminal에서 AI v2 환경을 준비합니다.

```bash
cd develop/ai-model/v2
python -m venv .venv
```

환경 활성화:

```bash
# macOS/Linux
source .venv/bin/activate

# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

Dependency와 local environment template을 준비한 뒤 server를 실행합니다.

```bash
python -m pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app.main:app --reload --port 8000
```

PowerShell에서는 `Copy-Item .env.example .env`를 사용할 수 있습니다.

주요 변수:

| 변수 | 용도 |
| --- | --- |
| `GEMINI_API_KEY` | Gemini API access key |
| `GEMINI_MODEL_NAME` | 계정에서 지원되는 generation model |
| `ROUTER_MODEL_NAME` | routing model |
| `WAS_BASE_URL` | Backend base URL |
| `INTERNAL_API_KEY` | Backend와 같은 shared local key |
| `CHECKPOINT_DB_PATH` | local SQLite checkpoint path |
| `CHECKPOINT_TTL_HOURS` | checkpoint activity TTL. 기본 72시간 |
| `ENABLE_RAG_MEMORY` | 선택적 Pinecone RAG 활성화 여부. 기본 false |
| `APP_ENV` | AI 실행 환경. 예제 기본값은 `development` |
| `ENABLE_DEBUG_ROUTES` | debug/observability route 명시적 opt-in. 기본 false |
| `TRACE_RETENTION_MINUTES` | in-memory trace/log TTL. 기본 60분 |

`INTERNAL_API_KEY`는 Backend와 동일한 non-blank/non-placeholder 값을 사용해야 하며 production에서는 최소 32자여야 합니다. Development/test에서는 짧은 명시적 값이 허용되지만 placeholder는 허용되지 않습니다. `ENABLE_DEBUG_ROUTES`는 기본 false이고 `APP_ENV`가 `development` 또는 `local`일 때만 true로 설정할 수 있습니다. Production에서는 debug/observability route가 mount되지 않고 TraceStore도 summary mode를 강제합니다. Summary mode에는 raw 대화·profile·plan·WAS body가 없으며 `TRACE_RETENTION_MINUTES` 기본값은 60분입니다. Development/local debug trace도 nested credential을 `[REDACTED]`로 치환합니다. Debug HTML에는 service key를 넣지 않으므로 기존 browser submit은 보호된 `/chat` 인증을 우회하지 않습니다. `ROUTER_API_KEY`는 선택 사항이며 비어 있으면 Gemini key를 사용하는 코드 경로가 있습니다. 사용하는 account/API가 예제 model name을 실제 지원하는지 별도로 확인해야 합니다.

AI 보안 경계 검사:

```bash
python scripts/test_security_boundaries.py
```

외부 credential 없이 실행 가능한 AI 회귀 검사:

```bash
python scripts/test_pinecone_metadata_lint.py
python scripts/test_intent_matrix.py
python scripts/test_demo_routing_edge_cases.py
python scripts/test_fast_plan_flow_suite.py
python scripts/test_quality_evaluation.py
python scripts/test_plan_quality_guards.py
python scripts/test_demo_mixed_was_edge_cases.py
python scripts/test_chat_e2e.py
python scripts/test_security_boundaries.py
python scripts/test_trace_privacy.py
python scripts/test_checkpoint_retention.py
```

실행 전 실제 Gemini·Pinecone·LangSmith credential을 설정하지 말고 `ENABLE_RAG_MEMORY=false`, tracing 비활성 상태를 확인하세요. 일부 quality script는 report 파일을 갱신하므로 결과를 commit하기 전 diff와 provenance를 검토해야 합니다.

## 7. Frontend

새 terminal에서 Frontend template을 복사하고 Backend URL을 확인합니다.

```bash
cd develop/frontend-ui
cp .env.example .env.local
npm ci
npm run dev
```

PowerShell에서는 `Copy-Item .env.example .env.local`을 사용할 수 있습니다.

`NEXT_PUBLIC_BACKEND_URL`이 browser에서 접근 가능한 Backend 주소를 가리켜야 합니다. 호환 alias인 `NEXT_PUBLIC_API_URL`도 지원하지만 둘 다 있으면 `NEXT_PUBLIC_BACKEND_URL`이 우선합니다.

검사 명령:

```bash
npm run lint
npm run build
npm run test:display-contract
npx playwright install chromium
npm start -- --hostname 127.0.0.1 --port 3100
# 별도 terminal
npm run smoke:home-recommendation-ux -- --url http://127.0.0.1:3100
```

## 8. 실행 순서와 상태 확인

권장 순서는 다음과 같습니다.

1. 필요한 Supabase project/schema 준비
2. Backend / WAS 시작
3. AI Server 시작
4. Frontend 시작

| 서비스 | 기본 local 주소 |
| --- | --- |
| Frontend | `http://localhost:3000` |
| Backend health | `http://localhost:8080/api/health` |
| Backend readiness | `http://localhost:8080/api/readiness` |
| AI health | `http://localhost:8000/health` |

Backend readiness는 Supabase 연결과 schema 상태의 영향을 받습니다. 응답에는 coarse/redacted 상태와 제한된 warning·blocking code만 포함되며 provider/DB의 원문 오류나 secret을 노출하지 않습니다. 외부 service와 database가 준비되지 않으면 통합 기능은 정상 동작하지 않습니다.

## 9. Optional external integrations

Pinecone RAG는 기본 비활성입니다. 활성화할 때만 AI `.env.example`의 다음 변수에 자신의 local 값을 설정합니다.

- `ENABLE_RAG_MEMORY`
- `PINECONE_API_KEY`
- `PINECONE_INDEX_NAME`

LangSmith tracing과 quality export도 선택 사항이며 기본 비활성입니다. 관련 변수는 `LANGCHAIN_*`, `LANGSMITH_*` prefix로 AI `.env.example`에 정리돼 있습니다. Production에서 명시적으로 export를 켜더라도 TraceStore가 제공하는 summary field만 대상으로 하며 raw 대화·profile·plan·WAS body는 존재하지 않습니다. 실제 key를 문서나 commit에 넣지 마세요.

## 10. Docker와 deployment configuration

Backend와 AI v2에는 각각 별도 container/deployment 설정이 있고, [`develop/deploy/gcp-two-vm`](../develop/deploy/gcp-two-vm)에 GCP 2-VM 참고 구성이 있습니다. 하나의 root Compose로 연결된 구조가 아닙니다. Backend runtime은 official `node` UID 1000, AI runtime은 dedicated UID 10001이며 application source는 read-only입니다. Backend log와 AI checkpoint만 `/var/lib/healthmate` host path에 씁니다.

Container 내부의 `localhost`는 다른 container나 VM을 가리키지 않습니다. AI deployment env의 `AI_BIND_ADDRESS`는 실제 private interface여야 하고 GCP firewall도 Backend/private network만 tcp:8000에 접근하게 해야 합니다. Backend 8080은 host에 publish하지 않습니다. 상세 Compose/Caddy/SSH 검증은 [deployment README](../develop/deploy/gcp-two-vm/README.md)를 따르세요.

## 11. 검증 범위와 알려진 blocker

현재 `Integration CI`는 Frontend install/lint/build/display/Playwright, Backend contracts/security/tenant/logging/syntax/audit, AI clean Python 3.11 install과 offline 회귀군, 두 Docker image build/non-root/local health, Compose config와 Caddy validation을 네 job으로 실행합니다. 상세 범위는 [IMPLEMENTATION_NOTES.md](IMPLEMENTATION_NOTES.md)에 기록합니다.

Python 3.11 clean install은 CI에서 실제 실행하지만 lock/constraints는 생성하지 않았습니다. Browser smoke는 explicit Playwright devDependency와 CI-installed Chromium을 사용하며 API route를 mock합니다. 실제 Supabase mutation, Gemini·Pinecone·LangSmith 요청, production endpoint, GCP deployment는 실행하지 않습니다.

현재 AI chat 내부 checkpoint key는 tenant-scoped hash입니다. 이전 raw `session_id` row에는 신뢰 가능한 owner가 없어 fallback·automatic rekey하지 않습니다. Checkpoint startup은 activity row가 없던 기존 checkpoint/write에 현재 시각을 한 번 기록하므로 기본 72시간 TTL 뒤 삭제됩니다. Cleanup은 startup 직후와 이후 매시간 실행되며 live session lock을 제외하고, durable pending `was_outbox`는 삭제하지 않습니다.

즉시 purge가 필요하면 AI Server를 중지하고 SQLite 파일을 backup한 뒤, 확인된 정확한 legacy raw thread ID마다 다음 maintenance transaction을 실행합니다. Owner를 추정하거나 새 hash key로 복사하지 말고 `was_outbox`와 Supabase 제품 table은 이 절차에서 수정하지 않습니다.

```sql
.parameter init
.parameter set :legacy_thread_id '확인된-정확한-legacy-thread-id'
BEGIN IMMEDIATE;
DELETE FROM writes WHERE thread_id = :legacy_thread_id;
DELETE FROM checkpoints WHERE thread_id = :legacy_thread_id;
DELETE FROM session_activity WHERE thread_id = :legacy_thread_id;
COMMIT;
VACUUM;
```
