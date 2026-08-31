# HealthMate 구현 상세 노트

이 문서는 포트폴리오용 `README.md`에서 덜어낸 구현 감사 내용을 보존합니다. 논문의 제안·평가와 저장소 코드의 구현 상태를 같은 것으로 간주하지 않으며, 다음 기준으로 구분합니다.

- **연구 배경, 제안 구조, 평가 결과, 연구 한계:** 최종 논문
- **구현 기능, API, 기술 스택, 실행 설정:** 현재 브랜치의 코드와 설정 파일
- **개인 역할과 수상 이력:** 프로젝트 참여 기록과 본인 확인

## 1. 브랜치와 provenance

| 브랜치 | 확인되는 범위 | 현재 관계 |
| --- | --- | --- |
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

따라서 AI v2만 실행한 환경에서 legacy route가 그대로 동작한다고 단정할 수 없습니다. 현재 Frontend의 식단 영양 정보 흐름에도 legacy meal-record 계약과 수동 입력 경로가 함께 남은 흔적이 있습니다. 이 계약 정리는 문서 범위를 넘으므로 Phase 2B blocker로 남깁니다.

## 6. Security history와 현재 주의점

과거 일부 브랜치에는 Backend runtime `.env`, 배포 archive, 테스트용 hardcoded API key가 추적된 이력이 있었습니다. 기존 감사와 프로젝트 기록에 따르면 확인된 Supabase service-role credential, 데이터베이스 비밀번호, Backend–AI internal key, 외부 서비스 key는 교체·폐기됐고 current branch tip에서는 관련 runtime 파일과 credential-bearing archive를 제거했습니다.

폐기된 credential이 포함된 과거 commit과 blob은 Fork와 upstream history에 남아 있습니다. Fork만 rewrite하면 upstream의 같은 공개 history는 유지되는 반면 다수 descendant SHA와 기존 clone이 바뀝니다. 이 비용과 제한된 효과 때문에 이 candidate에서도 history rewrite를 수행하지 않았습니다. 전체 팀과 upstream 관리자가 함께 결정할 때만 별도 최신 감사와 협업 절차로 다시 검토해야 합니다.

현재 코드에는 다음 promotion blocker가 남아 있습니다. Phase 2A에서는 behavior를 수정하지 않았습니다.

- 일부 legacy AI/API route가 인증 없이 caller 제공 `user_id`를 신뢰하는 경계
- `INTERNAL_API_KEY`가 비어 있을 때 internal auth가 fail-open할 수 있는 경로
- debug·trace·log·관리 통계 endpoint의 인증과 민감 데이터 보존 경계
- credentials를 허용하는 wildcard CORS와 readiness 응답의 DB 오류 정보
- non-root container user 미설정과 배포 SSH host-key 신뢰 방식
- 추적된 migration에서 명시적 RLS policy가 확인되지 않아 별도 검증이 필요한 tenant 경계

실제 secret 값은 문서나 예제 파일에 기록하지 않습니다. 원본 팀 저장소에는 Fork current-tip 정리가 자동 반영되지 않습니다.

## 7. Dependency reproducibility

- Frontend와 Backend에는 lockfile이 있어 `npm ci`를 사용할 수 있습니다.
- AI v2 `requirements.txt`는 하한 버전(`>=`) 중심이며 Python lockfile이 없습니다.
- Backend Dockerfile은 Node.js 24, AI Dockerfile은 Python 3.11을 사용합니다.
- 저장소 공통 Node.js version pin과 세 서비스를 한 번에 실행하는 root script/Compose는 없습니다.

따라서 AI 의존성 집합과 로컬 runtime은 설치 시점·환경에 따라 달라질 수 있습니다. 검증은 AI Dockerfile과 같은 Python 3.11 환경에서 다시 수행해야 완전한 재현성을 주장할 수 있습니다.

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

`develop/ai-model/v2/docs/quality`의 `quality_requirements.yaml`, `risk_catalog.md`, `standard_mapping.md`는 A입니다. 같은 디렉터리의 report 29개(약 4.12 MB)는 관련 script로 재생성되는 C이지만 연구·품질 evidence 가치가 있어 Phase 2A에서 삭제하지 않았습니다. 일부 report에는 stale absolute path나 현재 없는 fixture 참조가 있어 현재 검증 결과의 source of truth로 간주하지 않습니다.

AI v2 root의 `ruff_result.txt`, `simulation_results.json`, `simulation_memory_results.json`, `simulation_output.txt`도 생성된 evidence인 C로 분류해 유지했습니다. Backend의 `git_log.txt`는 Git metadata로 재생성 가능하지만 당시 문맥 보존 의도를 확정할 수 없어 E로 분류해 유지했습니다.

`scratch_pw/tests`의 두 Playwright spec은 이름은 scratch이지만 단순 cache/build output은 아닙니다. 하나는 mock API 기반 Frontend UI 회귀 검사이고, 하나는 외부 Backend/Supabase를 사용하는 experimental probe입니다. 연결된 root package/config가 없어 E로 분류해 유지했습니다. 정식 test 위치로 이동할지, 외부 probe에 production 실행 차단과 계정 cleanup을 추가할지는 Phase 2B에서 결정합니다.

## 10. Validation과 알려진 한계

Phase 2A의 목표는 문서와 명백한 repository hygiene 변경이 application behavior를 바꾸지 않았는지 확인하는 것입니다. 다음 검사는 통과했습니다.

| 영역 | 결과 |
| --- | --- |
| Frontend | `npm ci`, lint(오류 0·경고 2), production build, display contract 7/7 |
| Backend | `npm ci`, internal contracts 21/21, JavaScript 구문 33/33 |
| AI static/smoke | Python 구문 100/100, Pinecone metadata 44/44, intent 57/57, routing edge 12/12, fast plan flow 110/110, quality evaluation 2/2 |

AI 검사는 모든 외부 credential을 비우고 tracing을 끈 별도 복사본에서 실행했습니다. 실제 Supabase·Gemini·Pinecone·LangSmith 또는 production service request는 보내지 않았습니다. 사용 가능한 bundled Python 3.12와 설치 시점의 unlocked dependency를 사용했으므로 Dockerfile의 Python 3.11 재현 검증은 남아 있습니다.

기존 감사에서 알려진 다음 실패는 Phase 2A에서 code 또는 expectation을 바꾸지 않고 blocker로 유지합니다.

- `scripts/test_plan_quality_guards.py`: source와 test import drift
- `scripts/test_demo_mixed_was_edge_cases.py`: workout 삭제·식단 변경 조건 실패
- `scripts/test_chat_e2e.py`: create 응답의 workout plan assertion 실패

외부 Supabase·Gemini·Pinecone·LangSmith 연결, 실제 deployment, migration 적용 상태, 의료·임상 안전성은 검증하지 않습니다.

## 11. License와 재사용

이 저장소에는 `LICENSE` 파일이 없습니다. 팀 프로젝트·Fork provenance와 권리 상태를 임의로 바꾸지 않기 위해 candidate에서 새 license를 추가하지 않았습니다. 명시적 허가 없이 재사용·배포 조건을 추정하면 안 됩니다.

로컬 준비 절차는 [LOCAL_SETUP.md](LOCAL_SETUP.md)를 참고하세요.
