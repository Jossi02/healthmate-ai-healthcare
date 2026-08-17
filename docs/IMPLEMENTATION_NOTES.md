# HealthMate 구현 상세 노트

이 문서는 포트폴리오용 `README.md`에서 덜어낸 구현 감사 내용을 보존합니다. 논문의 제안·평가와 저장소 코드의 구현 상태를 같은 것으로 간주하지 않으며, 다음 기준으로 구분했습니다.

- **연구 배경, 제안 구조, 평가 결과, 연구 한계:** 최종 논문
- **구현 기능, API, 기술 스택, 실행 설정:** 저장소의 코드와 설정 파일
- **개인 역할과 수상 이력:** 프로젝트 참여 기록과 본인 확인

## 1. 브랜치별 구현 범위

| 브랜치 | 확인되는 범위 | 해석 |
| --- | --- | --- |
| [`main`](https://github.com/Jossi02/healthmate-ai-healthcare/tree/main) | Next.js 프론트엔드 프로토타입, 초기 설계 문서 | 기본 브랜치. 백엔드·AI 소스는 없음 |
| [`develop`](https://github.com/Jossi02/healthmate-ai-healthcare/tree/develop) | 로그인·회원가입·`PlanContext`가 추가된 프론트엔드 | `main` 이후의 프론트엔드 작업 |
| [`test/all`](https://github.com/Jossi02/healthmate-ai-healthcare/tree/test/all) | 프론트엔드, Express 백엔드, Supabase 마이그레이션, FastAPI/LangGraph AI v2, 배포 설정 | 가장 넓은 통합 코드가 보존되어 있으나 `main`에 병합되지 않은 별도 브랜치 |
| [`ai-model`](https://github.com/Jossi02/healthmate-ai-healthcare/tree/ai-model) | AI v1 중심 구현 | 레거시 엔드포인트와 실험 이력 |
| [`ai-model-langgraph2`](https://github.com/Jossi02/healthmate-ai-healthcare/tree/ai-model-langgraph2) | LangGraph AI v2 개발 과정 | AI 구조 변경 이력 |

따라서 `main`만 복제해 실행하면 프론트엔드 프로토타입 범위만 재현할 수 있습니다. README의 통합 기능 설명은 `test/all` 코드가 근거이며, 해당 코드가 기본 브랜치에 포함되어 있다는 뜻은 아닙니다.

## 2. 초기 설계 문서와 실제 구현의 구분

`main/docs`의 `architecture.md`, `DATABASE_schema.md`, `DataFormat_*`, `sequence_*` 문서는 초기 협업을 위한 설계·계약 자료입니다. `main`에는 이 문서가 전제하는 백엔드와 AI 구현이 없으므로, 문서만으로 기능 완성을 판단할 수 없습니다.

후기 구현을 확인할 때는 `test/all`의 다음 자료와 실제 소스를 함께 봐야 합니다.

- [AI v2 소스와 문서](https://github.com/Jossi02/healthmate-ai-healthcare/tree/test/all/develop/ai-model/v2)
- [Express 백엔드](https://github.com/Jossi02/healthmate-ai-healthcare/tree/test/all/develop/backend-api)
- [Supabase 마이그레이션](https://github.com/Jossi02/healthmate-ai-healthcare/tree/test/all/develop/backend-api/supabase/migrations)
- [통합 프론트엔드](https://github.com/Jossi02/healthmate-ai-healthcare/tree/test/all/develop/frontend-ui)

초기 문서와 후기 코드에는 모드 수, 엔드포인트 이름, 응답 래핑 방식 등이 서로 다른 흔적이 있습니다. 문서와 코드가 충돌하는 경우 이 노트에서는 실행 경로에 연결된 후기 코드를 현재 구현의 기준으로 삼았습니다.

## 3. 논문 아키텍처와 현재 코드의 차이

| 주제 | 논문에서 설명한 범위 | 저장소에서 확인되는 상태 |
| --- | --- | --- |
| 배포 | Vercel 프론트엔드와 Oracle Cloud 기반 서버 | `test/all`의 최신 통합 배포 설정은 Vercel과 GCP 2-VM을 대상으로 함 |
| AI 그래프 | 의도 분류, 도메인 전문가, 검색 라우터, 답변 평가를 포함한 멀티에이전트 | 현재 builder의 빠른 경로는 `preprocess → fast_router → fast_target_resource → fast_profile_constraints → fast_generate → fast_validate → fast_finalize` |
| 영구 기억 | Pinecone 의미 검색을 이용한 장기 기억 | 관련 클라이언트·노드·평가 자산은 남아 있으나 `ENABLE_RAG_MEMORY=false`가 기본값이고 현재 fast graph에는 검색 노드가 연결되지 않음 |
| 스트리밍 | SSE 기반 응답 스트리밍 | 현재 프론트엔드는 JSON 응답을 받은 뒤 `simulateStreamingResponse`로 글자를 순차 표시함 |
| 성향 모델 | MBTI와 DISC를 결합한 프로파일 | MBTI와 `selected_ai_persona` 저장·사용은 확인되지만 명시적인 DISC 입력·처리 경로는 확인되지 않음 |
| 모델 | 논문 평가에 Gemini 2.5 Flash 사용 | AI v2는 모델명을 환경변수로 설정함. 저장소 예시 모델의 실제 계정별 사용 가능 여부는 코드만으로 확인할 수 없음 |

이 차이는 논문의 연구 결과가 무효라는 의미가 아니라, 논문 시점의 전체 시스템과 Fork에 보존된 후기 코드 스냅샷이 동일한 배포본이 아님을 뜻합니다.

## 4. 현재 코드에서 확인되는 기능 범위

### Frontend

- 온보딩과 건강 프로필 입력
- 로그인·회원가입과 인증 토큰 사용(`test/all`)
- 홈 추천, 수분·운동·식단 UI
- 캘린더형 플랜 조회·수정·체크·삭제
- AI 채팅, 대화 기록, 피드백 UI
- AI 페르소나 선택과 프로필 반영

`main`의 채팅과 추천에는 더미 응답·고정 데이터가 포함됩니다. 서버 연동이 필요한 일부 요청 경로는 존재하지만 `main` 자체에는 이를 처리할 백엔드가 없습니다.

### Backend

`test/all`의 Express 서버에서 다음 범위를 확인했습니다.

- 회원가입·로그인과 JWT 인증
- Supabase 기반 사용자·건강 프로필 저장
- 채팅 세션·메시지·피드백 저장 및 조회
- 운동·식단 플랜과 캘린더 데이터 처리
- FastAPI 채팅·홈 추천 중계
- AI 서버가 WAS에 플랜을 기록하기 위한 내부 API

### AI Server

`test/all/develop/ai-model/v2`에서 다음 범위를 확인했습니다.

- FastAPI `/chat` 및 홈 추천 경로
- LangGraph 상태 그래프
- 사용자 프로필 제약 추출과 계획 검증
- Gemini 기반 생성과 라우팅
- SQLite checkpointer
- 선택적 Pinecone·LangSmith 연동 코드
- 시나리오·품질 평가 스크립트와 관측 UI

평가 스크립트와 결과 파일이 존재한다는 사실은 확인했지만, 이번 문서 작업에서 이를 다시 실행해 통과 여부를 검증하지는 않았습니다.

## 5. 레거시 API와 AI v2 계약

백엔드의 일부 레거시 AI 라우트는 AI v1의 다음 엔드포인트를 호출합니다.

- `/process-meal`
- `/recommend`
- `/user-instruction`

AI v2의 활성 통합 경로는 `/chat`과 `/home/recommendations...` 계열입니다. 따라서 레거시 라우트가 AI v2만 실행한 환경에서도 그대로 동작한다고 볼 수 없습니다. 현재 프론트엔드의 식단 영양 정보 입력도 일부는 수동 입력 흐름을 사용합니다.

## 6. 기술부채와 보안상 주의점

- 백엔드 CORS 설정은 현재 모든 origin을 허용합니다. 환경변수 예시에 있는 `CLIENT_URL`이 실제 허용 목록으로 사용되지는 않습니다.
- JWT 서명키에 개발용 fallback이 남아 있습니다. 환경변수 주입이 누락되어도 서버가 fallback 값으로 실행될 수 있으므로, 실제 운영 환경에서는 중앙 설정 검증과 startup fail-fast 방식으로 보강하는 것이 바람직합니다.
- 내부 API 인증은 `INTERNAL_API_KEY`가 설정되지 않았을 때 보호 수준이 약해질 수 있으므로 통합 환경에서는 Backend와 AI Server 양쪽에 동일한 강한 값을 명시적으로 설정해야 합니다.
- 과거 `test/all`, `test-1`, `codex/persona-chatbot-avatars` 브랜치에서 Backend `.env`가 Git에 추적된 이력이 확인되었습니다. 현재 Fork의 해당 branch tip에서는 파일 제거와 ignore/example 정리를 완료했으며, 확인된 Supabase service-role credential, 데이터베이스 비밀번호, Backend–AI internal API key는 모두 교체·폐기했습니다.
- 과거 배포용 `v2-deploy.tar.gz`와 `develop/ai-model/v2/v2-deploy.tar.gz`에는 환경파일과 외부 서비스 credential이 포함된 이력이 있었습니다. 해당 archive는 생성 산출물로 판단해 영향받는 Fork branch tip에서 제거했고 정확한 경로의 ignore 규칙을 적용했습니다.
- `ai-model-langgraph2`에 남아 있던 테스트용 hardcoded Google API key도 제거했으며, 현재는 `GEMINI_API_KEY`와 `GEMINI_MODEL_NAME` 환경변수를 사용하는 방식으로 변경했습니다.
- Google/Gemini, Pinecone, LangSmith를 포함해 이번 정리 과정에서 확인된 credential은 모두 교체·폐기했습니다.
- Fork의 17개 current branch tip을 텍스트·binary blob과 추적 archive 내부까지 검사한 결과, 정리 완료 시점 기준 고신뢰 실제 credential finding은 0건이었습니다.
- 폐기된 credential이 포함된 과거 commit과 blob은 Fork와 upstream의 Git history에 남아 있습니다. 다만 credential이 모두 폐기됐고 Fork current tip이 정리된 상태이며, Fork만 history rewrite하더라도 upstream의 동일 공개 history는 유지됩니다. 약 134개의 Fork descendant commit SHA 변경과 기존 clone 재동기화 비용을 고려해 **Fork 단독 history rewrite는 수행하지 않기로 결정했습니다.**
- 향후 upstream 관리자와 전체 팀이 공동 history rewrite에 동의한다면 별도의 최신 감사와 협업 절차를 거쳐 다시 검토할 수 있습니다.
- 원본 팀 저장소 `WinLike-dev/capstone_2team`에는 Fork에서 수행한 current-tip 정리가 자동 반영되지 않습니다. Upstream 정리는 저장소 관리자가 일반 fast-forward commit으로 별도 적용할 수 있도록 handoff 절차를 마련했습니다.
- 일부 배포·smoke·브라우저 테스트 스크립트에는 synthetic 테스트 계정용 고정 비밀번호가 남아 있습니다. 현재 분석에서는 실제 사용자 계정 credential이 아닌 테스트 fixture로 분류했지만, 향후에는 실행별 무작위 비밀번호 생성, production endpoint 실행 차단, 테스트 계정 cleanup을 추가할 수 있습니다.
- 저장소에 `LICENSE` 파일이 없어 재사용·배포 조건이 명시되어 있지 않습니다.
- 여러 개발·실험 브랜치가 함께 남아 있어 처음 저장소를 보는 사람이 현재 기준 구현을 파악할 때 추가 설명이 필요합니다.

실제 비밀값은 이 문서와 예제 파일에 기록하지 않습니다.

## 7. 의존성 재현성

- 프론트엔드와 백엔드는 lockfile이 있어 `npm ci`를 사용할 수 있습니다.
- AI v2의 `requirements.txt`는 하한 버전(`>=`) 중심이며 Python lockfile이 없습니다. 따라서 서로 다른 시점에 설치한 환경의 정확한 의존성 집합이 달라질 수 있습니다.
- 저장소에는 프로젝트 공통 Node.js 버전 고정 파일이 없습니다. 백엔드 Dockerfile은 Node.js 24, AI Dockerfile은 Python 3.11을 사용합니다.
- 루트에서 세 서비스를 한 번에 실행하는 Compose 또는 통합 스크립트는 없습니다.

## 8. 배포 환경 차이

논문은 Oracle Cloud를 기준으로 기술되었지만, `test/all`에는 [GCP 2-VM 배포 설정](https://github.com/Jossi02/healthmate-ai-healthcare/tree/test/all/develop/deploy/gcp-two-vm)이 있습니다. 두 서비스의 Compose 파일은 각각 분리되어 있으며, 로컬에서 단순히 실행할 경우 컨테이너 내부의 `localhost`가 다른 서비스 컨테이너를 가리키지 않는다는 점에 주의해야 합니다.

배포 파일과 GitHub Actions가 존재한다는 사실만 확인했으며, 현재 공개 URL의 가동 상태나 실제 배포 성공 여부를 입증하는 자료로 사용하지 않았습니다.

## 9. 현재 검증되지 않은 부분

다음 항목은 코드만으로 결론을 내릴 수 없거나 이번 문서 작업에서 실행 검증하지 않았습니다.

- 현재 운영 중인 외부 서비스와 배포 URL: **확인 불가**
- Supabase 프로젝트의 실제 스키마·마이그레이션 적용 상태: **확인 불가**
- Gemini·Pinecone·LangSmith 계정과 현재 모델 사용 가능 상태: **확인 불가**
- 전체 의존성 설치, lint, build, 단위·통합·E2E 테스트의 현재 통과 여부: **확인 불가**
- 논문 평가에 사용된 정확한 배포 스냅샷과 현재 `test/all` 코드의 완전한 동일성: **확인 불가**
- DISC 4유형의 현재 런타임 처리 구현: **확인 불가**
- 문서가 참조하는 일부 별도 실험 결과 파일의 보존 여부: 저장소에서 확인되지 않은 항목이 있음

현재 Fork의 보안 정리는 current branch tip을 기준으로 완료했지만, 원본 팀 저장소의 current tip과 양쪽 저장소의 과거 Git history까지 변경한 것은 아닙니다.

자세한 로컬 준비 절차는 [LOCAL_SETUP.md](LOCAL_SETUP.md)를 참고하세요.
