# HealthMate 구현 상세 노트

이 문서는 포트폴리오용 `README.md`에서 덜어낸 구현 감사 내용을 보존합니다. 논문의 제안·평가와 저장소 코드의 구현 상태를 같은 것으로 간주하지 않으며, 다음 기준으로 구분합니다.

- **연구 배경, 제안 구조, 평가 결과, 연구 한계:** 최종 논문
- **구현 기능, API, 기술 스택, 실행 설정:** 현재 브랜치의 코드와 설정 파일
- **개인 역할과 수상 이력:** 프로젝트 참여 기록과 본인 확인

## 1. 브랜치와 provenance

| 브랜치 | 확인되는 범위 | 현재 관계 |
| --- | --- | --- |
| `portfolio/integration-runtime` | Phase 2C-1 runtime trace·log privacy, bounded retention, checkpoint TTL 검증 | `portfolio/integration-quality@8c5fe5cf66d1322dc85703dffb3aa722e9358c5a`에서 직접 시작한 runtime 후보 |
| `portfolio/integration-quality` | Phase 2B-2 AI 품질 계약, tenant ownership, dependency·artifact 검증 | `portfolio/integration-security@35757ea60cc1e6d2647c527112d0e83be07d9f7b`에서 직접 시작한 품질 후보 |
| `portfolio/integration-security` | Phase 2B-1 fail-closed auth, public/debug surface, CORS/readiness/rate-limit 경계 | `portfolio/integration-candidate@3ab3c4f8fe3b728bdf9aa7d7c69902bad23f91d0`에서 직접 시작한 보안 후보 |
| `portfolio/integration-candidate` | Frontend, Express Backend, Supabase migrations, AI v1/v2, GCP 배포 설정 | `test/all@5714945eab8379a875d8c536414b13fb2db0f47e`에서 직접 시작한 통합 후보. 완성본 아님 |
| `main` | Next.js 프런트엔드 프로토타입과 포트폴리오·설계 문서 | 기존 portfolio landing/default branch. 기준 tip `3acaf4a79c3cf2c690a116d35ab838e948e4f612` |
| `test/all` | 가장 넓은 통합 staging snapshot | candidate의 direct ancestor이며 그대로 유지됨 |
| `develop` | 로그인·회원가입·`PlanContext`가 추가된 프런트엔드 | 별도 개발 이력 |
| `ai-model`, `ai-model-langgraph2` | AI v1 및 LangGraph v2 개발 과정 | 레거시·구조 변경 이력 |

Candidate는 `main`을 merge, rebase, squash 또는 cherry-pick하지 않았습니다. `test/all`의 통합 ancestry를 그대로 두고, `main` 문서의 사실관계만 현재 트리에 맞게 새 문서로 작성했습니다. 기존 브랜치는 원래 커밋과 작성자 provenance를 계속 보존합니다.

이 브랜치는 통합 범위를 명확히 검토하기 위한 후보입니다. default branch 변경, release 선언 또는 운영 준비 완료를 의미하지 않습니다.

## 2. 개인 역할과 팀 역할

HealthMate는 2026년 대학 심화캡스톤 팀 프로젝트이며 한국정보기술학회 논문 경진대회 은상은 팀 연구 성과입니다. 저장소 소유자는 논문 집필 담당 및 제1저자입니다.

저장소 소유자의 확인된 기여 범위는 다음과 같습니다.

- LangGraph 멀티에이전트 구조 공동 설계
- FastAPI AI Server 구현 참여
- Gemini API 연동
- 프롬프트 설계 공동 수행
- Pinecone/RAG 초기 구현 후 팀원에게 인계
- 평가 시나리오, 테스트 방향, 결과 분석
- 관련 연구와 논문 집필

Frontend·Backend·AI 전체 시스템 또는 전체 AI 구조를 단독 구현한 것으로 해석하면 안 됩니다. 전체 AI 구조의 초기 설계는 다른 팀원이 주도했고 이후 구현은 공동 작업으로 진행됐습니다.

## 3. 논문과 현재 코드의 차이

| 주제 | 논문에서 설명한 범위 | 현재 브랜치에서 확인되는 상태 |
| --- | --- | --- |
| 배포 | Vercel Frontend와 Oracle Cloud 기반 서버 | 보존된 최신 통합 배포 설정은 Vercel과 GCP 2-VM 대상 |
| AI 그래프 | 의도 분류, 도메인 전문가, 검색 라우터, 답변 평가를 포함한 멀티에이전트 | 빠른 경로는 `preprocess → fast_router → fast_target_resource → fast_profile_constraints → fast_generate → fast_validate → fast_finalize` |
| 영구 기억 | Pinecone 의미 검색 기반 장기 기억 | 클라이언트·노드·평가 자산은 남아 있으나 RAG가 기본 비활성이고 fast graph 기본 경로에 검색 노드가 연결되지 않음 |
| 스트리밍 | SSE 기반 응답 스트리밍 | Frontend가 JSON 응답 후 `simulateStreamingResponse`로 표시 |
| 성향 모델 | MBTI와 DISC 결합 | MBTI와 `selected_ai_persona` 사용은 확인되지만 명시적인 DISC 입력·처리 경로는 확인되지 않음 |
| 모델 | 논문 평가에서 Gemini 2.5 Flash | AI v2는 모델명을 환경변수로 선택하므로 논문 시점과 동일성을 보장하지 않음 |

이 차이는 연구 결과가 무효라는 뜻이 아니라 논문 시점의 전체 시스템과 Fork에 보존된 후기 snapshot이 완전히 동일한 배포본이 아니라는 뜻입니다.

## 4. 현재 코드 범위

### Frontend

- 온보딩과 건강 프로필 입력
- 회원가입·로그인과 인증 토큰 사용
- 홈 추천, 수분·운동·식단 UI
- 캘린더형 플랜 조회·수정·체크·삭제
- AI 채팅, 대화 기록, 피드백 UI
- AI persona 선택과 프로필 반영

### Backend / WAS

- 회원가입·로그인과 JWT 인증
- Supabase 기반 사용자·건강 프로필 저장
- 채팅 session·message·feedback 저장 및 조회
- 운동·식단 플랜과 캘린더 데이터 처리
- FastAPI 채팅·홈 추천 중계
- AI Server가 WAS에 플랜을 기록하기 위한 internal API

### AI Server

- FastAPI `/chat` 및 홈 추천 경로
- LangGraph state graph
- 사용자 profile constraint 추출과 plan validation
- Gemini 기반 생성과 routing
- SQLite checkpointer
- 선택적 Pinecone·LangSmith 연동 코드
- 시나리오·품질 평가 스크립트와 관측 UI

## 5. Legacy API와 AI v2 계약

Backend의 일부 legacy AI route는 upstream에서 `/process-meal`, `/recommend`, `/user-instruction`을 호출하도록 작성돼 있습니다. AI v1 source에는 `/process-meal`, `/recommend`, `/ai-chat`이 확인되지만 `/user-instruction`은 확인되지 않으며, AI v2의 활성 통합 경로는 `/chat`과 `/home/recommendations...` 계열입니다.

따라서 AI v2만 실행한 환경에서 legacy route가 그대로 동작한다고 단정할 수 없습니다. 현재 Frontend에 해당 legacy route를 호출하는 callsite가 없고, 활성 Backend의 `/api/v1/ai`는 계약 혼선을 피하기 위해 정적 `410 Gone`을 반환합니다. 기존 controller source는 이력으로 남아 있지만 active app 경로에서는 도달하지 않습니다. 현재 `/api/v1/admin`도 role source가 없으므로 정적 `404 Not Found`를 반환합니다. 식단 영양 정보의 legacy meal-record 계약과 수동 입력 흔적은 별도 정리 대상으로 남깁니다.

## 6. Security history와 현재 주의점

과거 일부 브랜치에는 Backend runtime `.env`, 배포 archive, 테스트용 hardcoded API key가 추적된 이력이 있었습니다. 기존 감사와 프로젝트 기록에 따르면 확인된 Supabase service-role credential, 데이터베이스 비밀번호, Backend–AI internal key, 외부 서비스 key는 교체·폐기됐고 current branch tip에서는 관련 runtime 파일과 credential-bearing archive를 제거했습니다.

폐기된 credential이 포함된 과거 commit과 blob은 Fork와 upstream history에 남아 있습니다. Fork만 rewrite하면 upstream의 같은 공개 history는 유지되는 반면 다수 descendant SHA와 기존 clone이 바뀝니다. 이 비용과 제한된 효과 때문에 이 candidate에서도 history rewrite를 수행하지 않았습니다. 전체 팀과 upstream 관리자가 함께 결정할 때만 별도 최신 감사와 협업 절차로 다시 검토해야 합니다.

Phase 2A는 application behavior를 수정하지 않은 역사적 hygiene 단계였습니다. Phase 2B-1 candidate에는 다음과 같은 보안 경계를 적용하는 범위가 명시돼 있습니다.

- Backend startup은 `JWT_SECRET`과 `INTERNAL_API_KEY`의 blank/example/placeholder 값을 모든 환경에서 거부하며, production에서는 각각 최소 32자를 요구합니다. Development/test에서는 짧은 명시적 non-placeholder 값을 사용할 수 있습니다. JWT signing과 verification은 중앙 `JWT_SECRET`을 사용하고 HS256으로 제한합니다.
- Browser 인증은 `Authorization: Bearer <JWT>`이고, Backend와 AI Server는 같은 `INTERNAL_API_KEY`를 사용합니다. Backend에서 AI로 나가는 호출에는 `x-api-key`가 항상 포함됩니다. Internal auth는 key 누락·불일치를 허용하지 않습니다.
- CORS는 정확한 쉼표 구분 allowlist를 사용하고 credentials는 false입니다. Development/local에는 localhost 기본값이 있고 production에서는 `CORS_ALLOWED_ORIGINS`가 필요하며 wildcard는 금지됩니다.
- AI debug/observability route는 기본 false이며 development/local에서 명시적으로 켠 경우에만 mount됩니다. Readiness는 coarse/redacted 상태만 반환합니다. Auth rate limit은 `AUTH_RATE_LIMIT_WINDOW_MS`와 `AUTH_RATE_LIMIT_MAX` 설정으로 signup/login에만 적용되고, reverse proxy는 명시한 `TRUST_PROXY_HOPS`만 신뢰합니다.

Phase 2B-2 tenant data flow 감사에서는 Frontend에 Supabase client/direct DB 경로가 없고 Backend만 service role을 사용하는 것을 다시 확인했습니다. Public API는 JWT의 `req.user.user_id`와 parent ownership filter를 경계로 사용합니다. 확정 finding은 2건이었습니다.

- **High:** AI checkpoint와 process/DB lock이 caller가 정한 raw `session_id`만 사용해 같은 값을 아는 다른 사용자가 state를 공유할 수 있었습니다. Public session 계약은 유지하고 내부 thread key를 `user_id + session_id`의 SHA-256 값으로 바꿨습니다. 보안 경계를 다시 열 수 있는 raw-key fallback은 추가하지 않았으므로 upgrade 전에 생성된 active proposal·pending write checkpoint는 자동 재개되지 않습니다.
- **Medium:** feedback가 client가 보낸 message snapshot을 그대로 저장해 session/message ownership과 내용 무결성을 보장하지 못했습니다. 저장된 assistant message를 인증 사용자와 session으로 다시 조회하고 바로 앞 user message·저장 intent만 사용하도록 수정했습니다.

Backend API 8개와 AI checkpoint A/B 격리 1개, 총 9개 외부 서비스 없는 tenant 회귀 시나리오가 통과했습니다. 현재 구조에서는 RLS 부재 자체를 구현 결함으로 분류하지 않습니다. Browser/direct Supabase access가 없고 service role은 RLS를 우회하므로 실제 경계는 application auth·ownership입니다. 향후 browser 또는 user-context DB 접근을 추가할 때 RLS를 별도 blocker로 올려야 합니다.

Phase 2B-2의 read-only 감사에서는 AI memory trace가 user message, user/session ID, request payload, WAS request/response와 profile·plan snapshot을 최대 trace 120개·global log 1,200개·trace별 120개까지 보관하면서 일반 TTL/redaction이 없고, Backend Winston과 Morgan도 file bound·query 제거가 없음을 확인했습니다.

Phase 2C-1은 이 경계를 다음처럼 좁혔습니다.

- TraceStore는 기본 summary mode이며 raw identifier, message, request/response, profile·plan snapshot, WAS body와 상세 event/log content를 보존하지 않습니다. 보존 field는 trace ID·kind·status·timestamp, duration, intent/domain 등 allowlist state, quality grade/count와 WAS method/status/count입니다. `TRACE_RETENTION_MINUTES` 기본값 60분을 access-time deterministic cleanup에 적용하면서 trace 120개, global log 1,200개, trace별 log 120개의 기존 count cap도 유지합니다.
- 상세 trace는 `APP_ENV`가 development/local이고 `ENABLE_DEBUG_ROUTES=true`인 경우에만 활성화됩니다. Production은 설정 실수로 상세 mode가 켜지지 않습니다. Debug에서도 Authorization, Cookie, password/hash, token, API key, JWT, secret, service-role 계열을 대소문자·snake/camel 변형과 nested dict/list까지 `[REDACTED]`로 치환합니다.
- Winston file transport는 `error.log`와 `combined.log` 각각 5 MiB, 5 files, tailable로 제한합니다. 공통 logger는 credential-bearing string/object/Error를 sanitize하고 Error의 arbitrary request/response property는 직렬화하지 않습니다. Morgan format은 `:method :safe-path :status :response-time ms`이며 query와 header를 남기지 않습니다. Chat/home gateway와 공통 handler의 client response에는 raw upstream payload, provider detail, stack 또는 5xx internal message가 없습니다.

Checkpoint schema 감사 결과 persisted channel에서 `user_id`가 제거되고 `session_activity`에도 owner가 없어 legacy raw-key row의 tenant owner를 신뢰성 있게 판정할 수 없습니다. 따라서 raw-key fallback·automatic rekey는 금지하고 세션 연속성보다 tenant isolation을 우선합니다. Upgrade 시 activity가 없던 checkpoint/write thread에는 현재 시각을 넣어 기본 `CHECKPOINT_TTL_HOURS=72` 안에 만료시키며, 필요하면 AI Server를 중지한 뒤 backup에서 확인한 정확한 legacy `thread_id`만 `writes` → `checkpoints` → `session_activity` 순으로 transaction purge합니다. 값을 추정하거나 hash key로 복사하지 않습니다. Cleanup은 startup 직후와 이후 매시간 실행되고 `BEGIN IMMEDIATE`와 120초 live lock 제외 조건을 사용합니다. Pending durable `was_outbox`는 checkpoint cleanup과 분리해 보존합니다. Credential 없는 SQLite regression에서 73시간 row 삭제, 71시간 row 보존, live lock 해제 전후, activity 없는 legacy row의 새 만료 시계, pending outbox 보존과 1,001개 초과 expired activity의 batched deletion을 실제 확인했습니다.

다음 항목은 여전히 promotion 전 별도 검토가 필요한 deferred blocker입니다.

- 운영 중앙 로그 수집기와 관리 통계의 별도 보존·삭제 정책
- Supabase chat/profile/plan 제품 데이터 lifecycle 정책
- non-root container user 미설정과 배포 SSH host-key 신뢰 방식
- GCP/실제 deployment의 secret injection, network, TLS, firewall 및 운영 차이

Phase 2B-2 current tree secret scan에서는 tracked runtime `.env`, API key/token/private key, credential URL, environment archive, fixed real credential을 확인하지 못했습니다. 실제 secret 값은 문서나 예제 파일에 기록하지 않습니다. 원본 팀 저장소에는 Fork current-tip 정리가 자동 반영되지 않습니다.

## 7. Dependency reproducibility

- Frontend와 Backend에는 lockfile이 있어 `npm ci`를 사용할 수 있습니다.
- AI v2 `requirements.txt`는 하한 버전(`>=`) 중심이며 Python lockfile이 없습니다.
- Backend Dockerfile은 Node.js 24, AI Dockerfile은 Python 3.11을 사용합니다.
- 저장소 공통 Node.js version pin과 세 서비스를 한 번에 실행하는 root script/Compose는 없습니다.

Phase 2B-2 Backend production audit는 중간 5·높음 4(총 9)에서 시작했습니다. Direct dependency는 Axios·Express·Morgan이었고 나머지는 production transitive dependency였습니다. `npm audit fix --omit=dev`가 제안한 현재 major 범위의 lockfile 갱신만 적용한 뒤 clean `npm ci`와 전체 Backend 회귀를 통과했고 audit은 0건이 됐습니다. `--force`, major migration, `package.json` range 변경은 사용하지 않았습니다.

AI direct requirements와 실제 import, Docker의 `pip install -r requirements.txt` 경로를 감사했지만 사용 가능한 Python 3.11 runtime이 없었습니다. 제공된 Python 3.12.13에서만 credential-free 검증했으므로 이를 3.11 결과로 간주하지 않습니다. 3.12 `pip freeze`를 잘못된 기준으로 고정하지 않았고 `requirements.lock.txt`/constraints도 생성하지 않았습니다. 따라서 AI 의존성 집합은 설치 시점·환경에 따라 달라질 수 있으며 Python 3.11 clean resolve·install 검증 뒤에만 lock artifact를 추가해야 합니다.

## 8. Deployment differences

논문은 Oracle Cloud를 기준으로 기술됐지만 이 브랜치에는 [`develop/deploy/gcp-two-vm`](../develop/deploy/gcp-two-vm)의 GCP 2-VM 설정이 있습니다. Backend와 AI Compose는 분리되어 있고 각 container 내부의 `localhost`는 다른 VM/container를 가리키지 않으므로 환경별 주소 설정이 필요합니다.

배포 파일과 GitHub Actions의 존재는 현재 공개 URL의 가동 상태나 실제 배포 성공을 입증하지 않습니다. 자동 배포 trigger, secret 주입, SSH host fingerprint, Docker/firewall 구성은 promotion 전에 검토해야 합니다.

## 9. Repository artifact 분류

Phase 2A에서는 다음 기준을 사용했습니다.

- **A — source/documentation:** application source, migration SQL, quality 기준 문서
- **B — reproducible test evidence:** 재실행 가능한 test fixture와 검사 자산
- **C — generated but useful evidence:** 스크립트로 생성되지만 연구·품질 추적에 유용한 report
- **D — disposable build/cache/archive:** 재생성 가능하고 실행 경로에서 요구되지 않는 build·CLI local state
- **E — unclear:** 목적이나 재현 경로가 충분히 확인되지 않은 항목

`develop/ai-model/v1/capstone-fastapi.tar.gz`는 약 96.46 MiB의 OCI image archive였습니다. Source와 Dockerfile이 남아 있고 실행 설정에서 archive를 참조하지 않아 D로 분류해 일반 commit으로 제거했으며 정확한 ignore rule을 추가했습니다.

`develop/backend-api/supabase/.temp`의 8개 파일은 Supabase CLI가 생성한 project/version/connection local state로 D에 해당해 제거했습니다. 기존 exact ignore rule은 유지했습니다. `supabase/migrations`의 SQL은 source이므로 그대로 보존했습니다.

`develop/ai-model/v2/docs/quality`의 `quality_requirements.yaml`, `risk_catalog.md`, `standard_mapping.md` 3개는 A입니다. 같은 디렉터리의 generated report 29개는 관련 script의 출력 이름과 일치하고 연구·품질 provenance 가치가 있어 보존했습니다. 이 중 JSON report 9개는 stale absolute path와 현재 없는 fixture를 참조하고, 현재 script output 5개는 추적되지 않으며 전체 report를 재생성하는 단일 명령도 없습니다. 따라서 29개는 historical/generated evidence이지 current regression의 source of truth가 아니며 대량 삭제하지 않습니다.

AI v2 root의 `ruff_result.txt`, `simulation_results.json`, `simulation_memory_results.json`, `simulation_output.txt`도 생성된 evidence인 C로 분류해 유지했습니다. Backend의 `git_log.txt`는 Git metadata로 재생성 가능하지만 당시 문맥 보존 의도를 확정할 수 없어 E로 분류해 유지했습니다.

`scratch_pw/tests/home_plan_sync_toast.spec.js`는 mock-only UI 검사였지만 정식 `develop/frontend-ui/scripts/smoke-home-recommendation-ux.cjs`가 동일한 success/already-exists/failure 계약을 이미 검증하므로 중복 spec을 제거했습니다. `approval_bottleneck.spec.js`는 고정 외부 endpoint와 실제 Backend `.env`/service role을 사용해 user·plan을 만들면서 cleanup을 보장하지 않는 experimental probe라 안전한 portfolio source로 유지하지 않았습니다. 새 Playwright dependency나 중복 package script는 추가하지 않았습니다.

## 10. Validation과 알려진 한계

아래 표는 Phase 2A 당시 문서와 명백한 repository hygiene 변경을 확인한 역사적 validation입니다. Phase 2B-1 보안 변경의 최종 통합 결과를 의미하지 않습니다.

| 영역 | 결과 |
| --- | --- |
| Frontend | `npm ci`, lint(오류 0·경고 2), production build, display contract 7/7 |
| Backend | `npm ci`, internal contracts 21/21, JavaScript 구문 33/33 |
| AI static/smoke | Python 구문 100/100, Pinecone metadata 44/44, intent 57/57, routing edge 12/12, fast plan flow 110/110, quality evaluation 2/2 |

AI 검사는 모든 외부 credential을 비우고 tracing을 끈 별도 복사본에서 실행했습니다. 실제 Supabase·Gemini·Pinecone·LangSmith 또는 production service request는 보내지 않았습니다. 사용 가능한 bundled Python 3.12와 설치 시점의 unlocked dependency를 사용했으므로 Dockerfile의 Python 3.11 재현 검증은 남아 있습니다.

Phase 2B-2 검증 결과는 다음과 같습니다. 모든 AI 검사는 credential·tracing·RAG를 비활성화하고 외부 service 호출 없이 disposable copy에서 실행했습니다.

| 영역 | 결과 |
| --- | --- |
| Frontend | clean `npm ci`, lint 오류 0·기존 경고 2, production build, display contract 7/7 |
| Backend | clean `npm ci`, internal contracts 21/21, security 33/33, tenant 8/8, JavaScript 구문 37/37, production audit 0 |
| AI | Python 3.12.13 구문 101/101, metadata 44/44, intent 57/57, routing 12/12, fast plan 110/110, quality 2/2, quality guards 94/94, mixed WAS edge·chat E2E·security 13/13 통과 |

Phase 2C-1 최종 검증 결과는 다음과 같습니다. AI는 credential·tracing·RAG를 끈 network-blocked disposable copy에서 실행했습니다.

| 영역 | 결과 |
| --- | --- |
| Frontend | clean `npm ci`, lint 오류 0·기존 경고 2, production build, display contract 7/7 |
| Backend | clean `npm ci`, contracts 21/21, security 33/33, tenant 8/8, logging privacy 1/1, JavaScript 구문 38/38, production audit 0 |
| AI | Python 3.12.13 구문 103/103, metadata 44/44, intent 57/57, routing 12/12, fast plan 110/110, quality 3/3, quality guards 94/94, mixed WAS edge·chat E2E·security 13/13, TraceStore privacy 3/3, checkpoint retention 1/1 통과 |

신규 runtime privacy/retention 검사는 3개 entrypoint·5개 top-level case/scenario입니다. 실제 Supabase·Gemini·Pinecone·LangSmith 또는 production endpoint는 호출하지 않았습니다.

기존 AI 실패 3건의 최종 판정과 상태는 다음과 같습니다.

- `test_plan_quality_guards.py`: 삭제된 `route_generate_self_eval`/builder route 기대는 **TEST DRIFT**였습니다. 현재 fast validation path를 검증하도록 갱신했습니다. 감사 중 발견한 malformed mixed-domain checkpoint 재사용은 별도 **CODE BUG**였고 shared state guard와 valid bundle 회귀 검사를 추가해 94/94 통과했습니다.
- `test_demo_mixed_was_edge_cases.py`: undated 요청과 맞지 않는 고정 과거 날짜는 **TEST DRIFT**, non-UUID profile fixture는 **CONTRACT MISMATCH**였습니다. 현재 KST 날짜와 valid UUID를 사용해 workout delete·diet modify write가 모두 통과했습니다.
- `test_chat_e2e.py`: 과거 slow-flow/FakeRouter exact response 기대는 **TEST DRIFT**였습니다. 현재 Frontend가 사용하는 structured proposal·intent·sync 계약으로 갱신했습니다. 삭제된 generator/validator route 전용 sparse·semantic helper와 sequential mixed-plan 기대는 **OBSOLETE / DEAD PATH**로 제거했습니다. Profile record·plan check·관련 pending/outbox endpoint helper도 현재 builder의 deterministic fast router가 노출하는 operation이 아니어서 복구 시 실제 route가 `casual`로 판정됨을 재확인하고 obsolete coverage로 제거했습니다. Current fast-flow, resilience, partial profile, tenant A/B isolation은 모두 통과했습니다.

Frontend home recommendation browser smoke는 설치된 Playwright browser executable이 없어 `NOT RUN`입니다. browser나 dependency를 자동 설치하지 않았습니다. Python 3.11 clean install·검증도 runtime 부재로 `NOT RUN`이며 lock을 만들지 않았습니다.

외부 Supabase·Gemini·Pinecone·LangSmith 연결, 실제 deployment, migration 적용 상태, 의료·임상 안전성은 검증하지 않습니다.

## 11. License와 재사용

이 저장소에는 `LICENSE` 파일이 없습니다. 팀 프로젝트·Fork provenance와 권리 상태를 임의로 바꾸지 않기 위해 candidate에서 새 license를 추가하지 않았습니다. 명시적 허가 없이 재사용·배포 조건을 추정하면 안 됩니다.

로컬 준비 절차는 [LOCAL_SETUP.md](LOCAL_SETUP.md)를 참고하세요.
