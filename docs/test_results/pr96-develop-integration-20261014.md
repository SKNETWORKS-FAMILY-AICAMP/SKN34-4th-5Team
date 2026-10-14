# PR96 최신 develop 통합 검증

## 범위와 충돌 처리

- 통합 전 A: `215fc5d30e29a68aa24649511edf1fe5385345eb` (PR96 V2 이전 및 탐색 동작 복구).
- 팀 기준: `fb4b3918dbe144b859941a939e9d83a19c51d350` (develop).
- B 실행 소스 tree: `f59252e0670749cdcee32506772f54304696fdda`. 이후 이 검증 기록만 추가했다.
- 충돌 파일은 `backend/llm/v2/evaluation/EVALUATION_CRITERIA.md` 하나다. 코스/V2/메뉴/후기 기준과 팀의 커뮤니티 자동 검사 기준을 모두 보존했다. 기존 정책·정답·합격 기준을 수정하지 않았다.
- `backend/llm/v2/course`, `backend/llm/v2/agent`, `backend/llm/v2/middleware`, `backend/llm/tools`는 A와 동일하다. v2 이전을 되돌리거나 새 모델 호출을 추가하지 않았다.
- 팀의 PWA·게시글 검사·배포 설정을 유지했다. 실제 `.env`, 사용자 대화, 운영 캐시, 발표용 임시 파일은 커밋 대상에 추가하지 않았다.

## 동일 계약 A/B

기존 `tonkatsu_contract.py`의 54개 입력·정답·harness를 그대로 사용했다. 사례 SHA256은 `1f11273965502898c2845c73b993a59a0a14c257b5660cf56d226ab42175ab63`이다.

| 검사 | A | B |
| --- | --- | --- |
| DeepEval exact contract | 54/54 | 54/54 |
| Ragas exact contract replay | 54/54 | 54/54 |
| 사례별 실제 관측값 비교 | 54개 전부 동일 | 차이 0 |

DeepEval 4.2.8, Ragas 0.4.3, Python 3.12를 사용했다. 모델 SDK 관련 환경은 langchain 1.4.2/core 1.6.5/openai 1.6.6, OpenAI SDK 3.3.0이다. CI처럼 DeepEval 환경의 langchain-community는 0.4.2, Ragas 환경은 0.4.1로 분리했다. A/B는 각각 같은 환경에서 실행했다.

Docker 네트워크를 끊고 기존 offline-exec의 socket 차단을 유지했다. 제공자·LLM judge 호출은 0이며 운영 장소 캐시·운영 DB를 사용하지 않았다. 실제 전체 채팅 지연/비용/제공자 의미 품질은 미측정이다.

## 실행 및 CI 연결

`deploy-dev.yml`의 develop PR/Push → `evaluation-regression` 재사용 job이 아래 검사를 실행한다. 표의 결과는 로컬 격리 환경에서 해당 명령을 실행한 결과이며 GitHub 원격 CI 결과와 구분한다.

| 명령 또는 대상 | 연결 workflow/job | 결과 |
| --- | --- | --- |
| `python -I .github/evaluation_ci.py free --output <new-output>` | evaluation-regression / deterministic | 전체 exit 0; 첫 코스 runner 109개 통과, DeepEval 54/54 포함 |
| `python -I .github/evaluation_ci.py offline-exec backend/llm/v2/evaluation/tonkatsu_contract.py score --input <export> --output <result>` | evaluation-regression / highlight-deterministic | A/B 각각 Ragas 54/54 |
| `python -I .github/test_evaluation_ci.py` | evaluation-regression / deterministic | 11개 통과 |
| `python -I .github/test_evaluation_ab.py` | evaluation-regression / deterministic | 13개 통과 |
| `python -I .github/test_evaluation_luna.py LunaExportIntegrationTest LunaSDKTest` | evaluation-regression / deterministic 및 highlight-deterministic | 두 CI 대상 합계 6개 통과 |
| `python -I .github/evaluation_ci.py highlight-free --output <new-output>` | evaluation-regression / highlight-deterministic | exit 0, 정상/실패 scorer 계약 포함 |
| `python backend/manage.py test community community.moderation_probe llm.tests.test_domain_tools.DomainToolsTest accounts.test.test_point_integration --noinput --verbosity=2` | community-moderation / django-integration | 162개 통과, 일회용 PostgreSQL |
| `test_community_moderation.py`, `test_moderation_native.py`, `test_moderation_fallback.py` | community-moderation / provider-contracts | 각각 DeepEval/Ragas 10/10, 7/7, 6/6; 제공자 호출 0 |
| `npm test` | community-moderation의 프론트 4파일 및 deterministic의 프론트 2파일을 포함한 전체 자동 발견 | 751개 통과, 기존 skip 2개, 실패 0 |
| `next typegen` 후 `tsc --noEmit` | 추가 로컬 타입 검사 | exit 0 |

첫 FREE runner에서 `test_course_candidates`와 `test_v2_agents_use_the_single_v2_model_factory`, `test_luna_constructors_omit_temperature`의 실제 이름과 `ok`를 확인했다. 코스 메뉴·후기·내부외부 구분·술집 평점·선택 답변 모듈도 같은 runner에 포함된다. 검사 사이 중복 사례가 있으므로 전체 합계를 품질 지표로 사용하지 않는다.

## 환경 오류와 보완

- 임시 프론트 checkout에 Next 생성 타입이 없어 첫 `tsc`에서 `PageProps` 오류가 발생했다. `next typegen` 실행 후 같은 소스의 타입 검사가 통과했다.
- 첫 Ragas/SDK 시도는 DeepEval용 langchain-community 0.4.2 환경에서 `vertexai` 모듈을 찾지 못했다. CI에 지정된 별도 0.4.1 환경으로 재실행해 통과했다. 제품 코드·테스트 정답은 변경하지 않았다.
- Windows 바인드 경로의 평가 패키지 설치가 오래 걸려 해당 임시 설치를 중단하고 Linux 임시 파일시스템에 격리 환경을 준비했다. 운영 requirements/컨테이너에는 평가 패키지를 설치하지 않았다.
- 원시 로그, 실패 시도, 명령/exit 기록과 A/B JSON은 로컬 `tmp/pr96-publish-20261014/`에 보존했다. 해당 임시 자료는 Git에 포함하지 않는다.

실제 유료 제공자 품질 gate, 전체 채팅 시간 A/B, 운영 부하 시험과 EC2 배포 결과는 이 기록의 범위 밖이다. 기존 선택 재시도 시 날짜 변경 문제 등 별도 미해결 항목을 이번 통합이 수정했다고 주장하지 않는다.

## 검증 중 추가된 develop 배포 변경

- 검증 이후 팀 develop에 `7fdd5bc`가 추가됐다. 변경은 develop 이미지 빌드/전송과 배포 timeout 두 workflow뿐이며 코스·백엔드·프론트 실행 소스는 바뀌지 않았다.
- 새 `cd-deploy` job의 첫 단계가 `docker build`인데 checkout이 없어 새 GitHub runner에 Dockerfile이 존재하지 않는 문제를 확인했다. `actions/checkout@v4`를 이미지 빌드 전에 추가하고 인증정보 보존을 비활성화했다.
- 이는 non-LLM 배포 설정 보완이다. YAML 파싱, checkout 선행 및 세 build context/Dockerfile 존재, 기존 CI 의존성 검사를 수행한다. EC2에서 실제 배포 성공을 확인한 것으로 표시하지 않는다.
- 이후 팀 `1cb1b40`에도 같은 checkout 보완과 배포 timeout 20분 변경이 올라와 함께 통합했다. checkout 단계는 한 번만 유지하고 `persist-credentials: false`를 보존했다. 추가 변경은 workflow뿐이며 코스 실행 소스에는 차이가 없다. 최종 YAML·순서·Dockerfile·CI 의존성 정적 검사를 통과했다.
