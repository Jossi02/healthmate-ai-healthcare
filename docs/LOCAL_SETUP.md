# HealthMate 로컬 실행 가이드

이 문서는 기본 브랜치의 프론트엔드 프로토타입과 `test/all`의 통합 코드를 구분해 설명합니다.

## 1. 보안 주의

- 실제 API 키, 비밀번호, JWT 서명키를 저장소에 커밋하지 마세요.
- 아래 값은 모두 예시 placeholder입니다. 자신의 로컬 값으로 교체해야 합니다.
- 공개 이력에 포함되었던 `develop/backend-api/.env`의 값은 복사하거나 재사용하지 마세요.
- `.env.example`은 변수 이름만 제공하는 템플릿이고, `.env`와 `.env.local`은 로컬 전용 파일입니다.

## 2. 사전 요구사항

- Git
- Next.js 16 실행에 호환되는 Node.js와 npm
- 통합 실행 시 Python 3.11(AI Dockerfile 기준)
- 통합 실행 시 Supabase 프로젝트와 Gemini API 접근 권한

Next.js 의존성은 Node.js 20.9 이상을 요구하지만 저장소에는 `.nvmrc`, `engines` 등 프로젝트 공통 로컬 Node.js 버전 고정 설정이 없습니다. 백엔드 Dockerfile은 Node.js 24를 사용합니다.

## 3. `main` 프론트엔드 프로토타입

```bash
git clone https://github.com/Jossi02/healthmate-ai-healthcare.git
cd healthmate-ai-healthcare/develop/frontend-ui
npm ci
npm run dev
```

브라우저에서 `http://localhost:3000`을 엽니다.

```bash
npm run lint
npm run build
```

`main`에는 백엔드와 AI 서버가 없습니다. 화면 프로토타입은 확인할 수 있지만 일부 채팅·추천 데이터는 목업이고 서버 의존 기능은 완전하게 동작하지 않습니다.

호환되는 외부 백엔드를 별도로 사용할 경우 `develop/frontend-ui/.env.local`에 다음 중 하나를 설정할 수 있습니다. 둘 다 있으면 `NEXT_PUBLIC_BACKEND_URL`이 우선합니다.

```dotenv
NEXT_PUBLIC_BACKEND_URL=http://localhost:8080
# NEXT_PUBLIC_API_URL=http://localhost:8080
```

## 4. `test/all` 통합 코드 준비

새로 복제한 저장소의 루트에서 브랜치를 전환합니다.

```bash
git clone https://github.com/Jossi02/healthmate-ai-healthcare.git
cd healthmate-ai-healthcare
git switch test/all
```

통합 실행은 다음 세 서비스를 각각 별도 터미널에서 실행합니다.

1. Backend / WAS: `develop/backend-api`
2. AI Server: `develop/ai-model/v2`
3. Frontend: `develop/frontend-ui`

저장소 루트에는 세 서비스를 한 번에 실행하는 통합 스크립트나 Compose 파일이 없습니다.

## 5. Supabase 준비

실제 인증, 프로필, 채팅, 플랜 저장에는 Supabase 프로젝트와 호환 스키마가 필요합니다.

마이그레이션 파일은 다음 위치에 있습니다.

- [`develop/backend-api/supabase/migrations`](https://github.com/Jossi02/healthmate-ai-healthcare/tree/test/all/develop/backend-api/supabase/migrations)

저장소에는 로컬 Supabase 설정 파일과 확정된 초기화 명령이 없으므로, 마이그레이션을 적용할 정확한 절차는 사용 중인 Supabase 환경에 맞춰 결정해야 합니다. 스키마가 적용되지 않은 상태에서는 Backend readiness와 데이터 기능이 정상 동작하지 않을 수 있습니다.

## 6. Backend / WAS

`develop/backend-api/.env.example`을 참고해 로컬 `.env`를 만듭니다. 과거 공개 이력의 `.env`는 신뢰하거나 재사용하지 말고 모든 자격 증명을 새 로컬 값으로 설정하세요.

```dotenv
PORT=8080
NODE_ENV=development
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
FASTAPI_URL=http://localhost:8000
AI_REQUEST_TIMEOUT=90000
INTERNAL_API_KEY=replace-with-a-shared-local-key
JWT_SECRET=replace-with-a-long-random-local-secret
JWT_EXPIRES_IN=7d
REQUIRE_IDEMPOTENCY_TABLE=false
LOG_LEVEL=info
```

| 변수 | 용도 |
| --- | --- |
| `SUPABASE_URL` | Supabase 프로젝트 URL |
| `SUPABASE_SERVICE_ROLE_KEY` | 서버 전용 Supabase 접근 키 |
| `FASTAPI_URL` | AI Server 기본 주소 |
| `INTERNAL_API_KEY` | Backend와 AI Server 간 내부 요청 인증. 두 서비스에 같은 값을 설정 |
| `JWT_SECRET` | 사용자 JWT 서명키. 개발 fallback을 사용하지 말고 반드시 별도 설정 |
| `AI_REQUEST_TIMEOUT` | Backend에서 AI 요청을 기다리는 시간 |
| `REQUIRE_IDEMPOTENCY_TABLE` | idempotency 테이블 강제 사용 여부 |

현재 CORS 코드는 모든 origin을 허용하며 예제에 있던 `CLIENT_URL`을 허용 목록으로 사용하지 않습니다. 운영 배포 전에는 코드 수준의 CORS 제한이 별도로 필요합니다.

```bash
cd develop/backend-api
npm ci
npm run dev
```

## 7. AI Server

`develop/ai-model/v2/.env.example`을 참고해 로컬 `.env`를 만듭니다.

```dotenv
GEMINI_API_KEY=your-gemini-api-key
GEMINI_MODEL_NAME=your-supported-gemini-model
ROUTER_MODEL_NAME=your-supported-gemini-model
WAS_BASE_URL=http://localhost:8080
WAS_TIMEOUT=10.0
INTERNAL_API_KEY=replace-with-the-same-shared-local-key
ENABLE_RAG_MEMORY=false
CHECKPOINT_DB_PATH=data/checkpoints.sqlite
APP_ENV=development
LOG_LEVEL=INFO
```

`GEMINI_API_KEY`와 `WAS_BASE_URL`은 AI v2 설정에서 필수입니다. 통합 인증을 위해 `INTERNAL_API_KEY`도 Backend와 같은 값으로 설정해야 합니다. 모델 이름은 사용하는 Gemini 계정/API에서 실제 제공되는 값을 선택하세요.

`ROUTER_API_KEY`는 선택 사항이며 생략하면 `GEMINI_API_KEY`를 사용합니다.

```dotenv
# ROUTER_API_KEY=your-router-api-key
```

대화 요약·보관 주기를 조정하는 선택 변수는 `SUMMARY_TURN_INTERVAL`, `MAX_MESSAGES`, `CHECKPOINT_TTL_HOURS`입니다. 기본값은 AI v2의 `.env.example`과 `app/core/config.py`에서 확인할 수 있습니다.

Pinecone RAG는 기본 비활성입니다. 다시 활성화할 때만 다음 값을 준비합니다.

```dotenv
ENABLE_RAG_MEMORY=true
PINECONE_API_KEY=your-pinecone-api-key
PINECONE_INDEX_NAME=your-pinecone-index-name
```

LangSmith 추적도 선택 사항입니다. 관련 설정 이름은 다음과 같습니다.

- `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_ENDPOINT`, `LANGCHAIN_PROJECT`
- `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_ENDPOINT`, `LANGSMITH_PROJECT`
- `LANGSMITH_QUALITY_ENABLED`, `LANGSMITH_SEND_FULL_TEXT`
- `LANGSMITH_MAX_CHILD_RUNS`, `LANGSMITH_CHILD_EVENT_SAMPLE_RATE`

```bash
cd develop/ai-model/v2
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

AI v2는 하한 버전 중심의 `requirements.txt`를 사용하고 Python lockfile이 없으므로 완전히 동일한 의존성 환경을 보장하지는 않습니다.

## 8. Frontend

`develop/frontend-ui/.env.local`을 만듭니다.

```dotenv
NEXT_PUBLIC_BACKEND_URL=http://localhost:8080
```

```bash
cd develop/frontend-ui
npm ci
npm run dev
```

`NEXT_PUBLIC_API_URL`도 대체 변수로 지원하지만 `NEXT_PUBLIC_BACKEND_URL`이 우선합니다.

## 9. 서비스 접근과 상태 확인

| 서비스 | 주소 |
| --- | --- |
| Frontend | `http://localhost:3000` |
| Backend health | `http://localhost:8080/api/health` |
| Backend readiness | `http://localhost:8080/api/readiness` |
| AI health | `http://localhost:8000/health` |

Backend readiness는 Supabase 연결과 스키마 상태의 영향을 받습니다. 외부 서비스 키와 데이터베이스가 준비되지 않으면 통합 기능은 동작하지 않습니다.

## 10. Docker와 배포 설정

Backend와 AI v2에는 각각 별도의 Compose 파일이 있습니다. 두 파일은 하나의 로컬 네트워크로 통합되어 있지 않으므로, 컨테이너를 따로 실행할 때는 서비스 주소를 Docker 네트워크나 호스트 주소에 맞춰 조정해야 합니다. 컨테이너 내부의 `localhost`는 다른 컨테이너를 가리키지 않습니다.

`test/all`의 [GCP 2-VM 배포 문서](https://github.com/Jossi02/healthmate-ai-healthcare/blob/test/all/develop/deploy/gcp-two-vm/README.md)는 배포 참고 자료이며, 현재 라이브 서비스의 존재나 정상 상태를 보장하지 않습니다.

## 11. 이번 문서 작업의 검증 범위

위 명령, 경로, 환경변수 이름은 저장소 설정을 정적으로 대조했습니다. 이번 작업에서는 다음을 다시 실행하지 않았습니다.

- 의존성 설치
- lint와 build
- 세 서비스 기동
- 단위·통합·E2E 테스트
- Supabase, Gemini, Pinecone, LangSmith 연결

따라서 현재 통과 상태는 **확인 불가**입니다.
