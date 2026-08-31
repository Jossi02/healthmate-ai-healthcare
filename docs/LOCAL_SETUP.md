# HealthMate 로컬 실행 가이드

이 문서는 `portfolio/integration-candidate` 브랜치에 함께 보존된 Frontend, Backend / WAS, AI Server를 로컬에서 준비하는 방법을 설명합니다. 저장소 루트에는 세 서비스를 한 번에 실행하는 script나 통합 Compose가 없습니다.

## 1. Candidate 직접 복제

다음 명령은 복제 후 별도 branch switch 없이 candidate를 checkout합니다.

```bash
git clone --branch portfolio/integration-candidate --single-branch https://github.com/Jossi02/healthmate-ai-healthcare.git
cd healthmate-ai-healthcare
```

현재 브랜치에는 다음 경로가 있어야 합니다.

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

## 3. 사전 요구사항

- Git
- Node.js 20.9 이상과 npm. Backend Dockerfile 기준 환경은 Node.js 24입니다.
- Python 3.11(AI Dockerfile 기준)
- 실제 저장 기능을 사용할 경우 Supabase project와 compatible schema
- AI 응답을 생성할 경우 사용 가능한 Gemini API access

AI v2는 version lower bound 중심의 `requirements.txt`를 사용하고 Python lockfile이 없습니다. 서로 다른 시점의 설치가 완전히 같은 dependency set을 보장하지 않습니다.

## 4. Supabase requirements

인증, profile, chat, plan 저장에는 Supabase/PostgreSQL schema가 필요합니다. 추적된 migration source는 다음 경로에 있습니다.

- [`develop/backend-api/supabase/migrations`](../develop/backend-api/supabase/migrations)

Supabase `.temp`는 CLI local state이며 source가 아닙니다. Candidate에서 제거·ignore됐습니다.

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
| `AI_REQUEST_TIMEOUT` | AI request timeout |
| `REQUIRE_IDEMPOTENCY_TABLE` | idempotency table 강제 여부 |

`INTERNAL_API_KEY`는 AI Server와 동일한 강한 값을 사용하고, `JWT_SECRET`은 development fallback에 의존하지 마세요. 현재 CORS와 internal auth behavior에는 Phase 2B 검토 항목이 있으므로 public network에 그대로 노출하지 마세요.

검사 명령:

```bash
npm run test:contracts
```

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
| `ENABLE_RAG_MEMORY` | 선택적 Pinecone RAG 활성화 여부. 기본 false |

`ROUTER_API_KEY`는 선택 사항이며 비어 있으면 Gemini key를 사용하는 코드 경로가 있습니다. 사용하는 account/API가 예제 model name을 실제 지원하는지 별도로 확인해야 합니다.

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

Backend readiness는 Supabase 연결과 schema 상태의 영향을 받습니다. 외부 service와 database가 준비되지 않으면 통합 기능은 정상 동작하지 않습니다.

## 9. Optional external integrations

Pinecone RAG는 기본 비활성입니다. 활성화할 때만 AI `.env.example`의 다음 변수에 자신의 local 값을 설정합니다.

- `ENABLE_RAG_MEMORY`
- `PINECONE_API_KEY`
- `PINECONE_INDEX_NAME`

LangSmith tracing과 quality export도 선택 사항이며 기본 비활성입니다. 관련 변수는 `LANGCHAIN_*`, `LANGSMITH_*` prefix로 AI `.env.example`에 정리돼 있습니다. 실제 key를 문서나 commit에 넣지 마세요.

## 10. Docker와 deployment configuration

Backend와 AI v2에는 각각 별도 container/deployment 설정이 있고, [`develop/deploy/gcp-two-vm`](../develop/deploy/gcp-two-vm)에 GCP 2-VM 참고 구성이 있습니다. 하나의 root Compose로 연결된 구조가 아닙니다.

Container 내부의 `localhost`는 다른 container나 VM을 가리키지 않습니다. 환경에 맞는 service address, network, firewall, TLS, secret injection을 구성해야 합니다. 보존된 파일의 존재는 live deployment나 성공 상태를 보장하지 않습니다.

## 11. 검증 범위와 알려진 blocker

Phase 2A에서는 credential 없이 Frontend install/lint/build, Backend internal/static 검사, AI syntax와 offline smoke/static 검사를 수행해 통과 범위를 [IMPLEMENTATION_NOTES.md](IMPLEMENTATION_NOTES.md)에 기록했습니다.

다음 AI test failure는 기존 코드에 있던 Phase 2B blocker이며 이 문서·repository hygiene 단계에서는 수정하지 않습니다.

- `scripts/test_plan_quality_guards.py`
- `scripts/test_demo_mixed_was_edge_cases.py`
- `scripts/test_chat_e2e.py`

Production endpoint를 호출하는 browser probe, 실제 Supabase mutation, Gemini·Pinecone·LangSmith 요청은 local 준비만으로 자동 실행되지 않으며 Phase 2A 검증에서도 제외합니다.
