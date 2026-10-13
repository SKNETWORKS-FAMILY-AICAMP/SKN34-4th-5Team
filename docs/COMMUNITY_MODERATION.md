# 커뮤니티 게시 검사 — 로컬 테스트 안내

## 현재 자동/수동 선택 (2026-10-13)

기본값은 OpenAI 기본 검사(`openai`)다. 관리자 DB 설정에 명시적으로 저장한 수동 선택은 유지한다. `COMMUNITY_MODERATION_MODE` 환경변수는 제거했고 읽지 않는다. 0014 migration은 모델 기본값만 변경하며 기존 운영자의 선택을 덮어쓰지 않는다. 기존 수동 작업 전체 재검사는 사용자 요청에 따라 현재 버전의 수동 스냅샷을 대상으로 별도 실행하고, 이전 작업/검토 이력 및 관리자 숨김을 보존한다.

관리자 → 게시 검사 → 검사 방식에서 `자동 · OpenAI 기본 검사` 또는 `수동 · 모든 글 운영진 검토`를 선택하고 `방식 저장`을 누른다. 설정은 `ModerationSettings` 단일 행에 저장되어 백엔드/워커가 공유하고 재시작 후 유지된다. API는 관리자 전용 `GET/POST /api/v1/community/admin/moderation/settings/`다. migration `0013_moderationsettings`가 필요하다. DB 설정이 없으면 환경변수와 무관하게 항상 `openai`다. 기존 `live` 작업은 저장된 스냅샷만 처리한다.

- 자동: 본문 전체를 한 번에 `omni-moderation-latest`로 검사한다. API의 `flagged=false`면 승인, `true`면 사유를 기록하고 운영진 검토로 보낸다. 정치 임베딩/임계값/별도 문맥 모델/harassment 예외/URL 단독 보류는 사용하지 않는다. 일반 정치글·광고·거래·사기 등을 별도 커뮤니티 정책으로 차단하는 기능은 현재 자동 모드에 없다.
- 수동: 모든 새 글·댓글·수정본을 운영진 검토로 보낸다.
- 모드 변경은 새 접수/수정본에만 적용한다. 기존 대기/완료/수동 검토 스냅샷을 자동 전환하거나 공개하지 않는다. 실패 작업을 명시적으로 재시도하면 현재 모드로 새 작업을 만든다.
- 자동은 서버 `OPENAI_API_KEY`만 필요하다. 키가 없으면 선택/저장을 막는다. 키 존재는 API 유효성 보증이 아니다. DB 선택이 없으면 키가 없어도 기본 자동 작업을 접수한다. 키 누락·timeout·API 오류·결과 누락/오형은 최초 실패에 완료 작업의 open `manual_review`로 넘기고 공개와 운영진 사후 검토를 병행한다. `moderation_status=review`와 현재 `approved_revision`을 함께 유지하므로 제공자 승인으로 표시하지 않는다. 독립 `image_review`나 다른 미해제/확정 사유는 공개를 막고 숨김·비공개 부모 글도 그대로 유지한다. `automatic_check_failed` 코드와 안전한 fallback 근거만 저장하며 원시 예외/키를 저장하지 않는다. 전역 모드나 관리자 DB 선택을 변경하거나 자동 승인하지 않는다. DB/저장 오류는 이 검사 실패 경로로 처리하지 않는다. 기존 `live` 스냅샷의 오류 재시도/실패 계약은 유지한다.
- **자동 검사는 텍스트만 연결되어 있다. 첨부 이미지는 `image_review`로 운영진 확인을 유지한다.** 링크 대상에 접속하거나 검사하지 않는다. 실제 API 결과는 기록하되 모델 분류 정확도를 보장하지 않는다.
- 아래의 정치/문맥 `live` 설명과 기존 평가 기록은 **이전 스냅샷 호환용**이다. 새 관리자 자동 모드에서는 이 경로를 사용하지 않는다.

검증: `test_moderation_mode.py`, 프론트 `community-moderation.test.mjs`, 분리 평가 `test_moderation_native.py`를 `.github/workflows/community-moderation.yml`에 연결한다. 공식 규격: https://developers.openai.com/api/docs/guides/moderation

현재 브랜치 `feat/21-member-activity`에 적용. 새 글·초안 게시·댓글·수정본은 DB에 저장하되 `pending`으로 비공개다. DB 작업 큐를 별도 워커가 즉시 처리한다. 일정 간격으로 공개된 글을 사후 차단하는 방식이 아니다. 워커의 2초 대기는 신규 큐 작업 조회 간격이다.

## 지금 테스트하는 방법

- 글쓰기: `http://127.0.0.1:8080/community/write`
- 작성자 상태·내용·수정: `http://127.0.0.1:8080/community/submissions`
- 운영진 검사·사유별 복구: `http://127.0.0.1:8080/admin?tab=moderation` (`is_staff` 계정)
- 기존 게시글·신고 관리의 제목 링크는 운영진 전용 원문 미리보기로 연결된다. 공개 URL에는 비공개 검토/숨김 글을 노출하지 않는다. 자동 검사 오류만 남은 공개 중 사후 검토는 공개 URL과 검사 목록에 함께 남는다.

수동 테스트는 관리자 DB에서 **manual**을 선택한 상태에서만 실행한다. 이 선택에서는 모든 새 제출이 `pending → review`로 이동하며, 운영진이 원문과 이미지를 확인하고 사유를 해제해야 공개된다. DB 선택이 없으면 기본값은 **openai**다. 기존 공개 데이터는 migration 0011에서 승인 버전 0으로 보존했다. 이는 과거 데이터의 모델 검사 통과를 뜻하지 않는다. 기존 글도 수정하면 새 버전으로 검사한다.

운영진은 선택한 사유를 `dismiss`(해제) 또는 `confirm`(확정)하고 근거를 남긴다. 자동 검사 오류 fallback의 open `manual_review`만 공개를 막지 않는다. 이 사유의 `confirm`은 rejected/비공개, `dismiss`는 공개를 유지한다. 다른 미해제 사유 또는 어떤 확정 사유라도 있으면 비공개다. 확정 사유도 재검토 후 해제 가능하다. 다른 운영진이 먼저 처리하거나 작성자가 수정하면 409가 나며 새로고침해야 한다. 기존 신고 관리의 `is_hidden`은 검사 복구로 해제하지 않는다.

## 서비스와 실행 명령 구조

- `backend/services/community_moderation.py`: 기존 `ModerationJob` ORM에서 처리할 작업을 가져오고, 게시글/댓글의 제출 버전 스냅샷으로 검사 요청 후 기존 모델에 결과를 저장한다. 결과 반영 시 실제 `CommunityPost`/`CommunityComment`를 잠그고 버전·lease를 검증한다.
- `backend/services/community_moderation_policy.py`: 기존 필터 정책 및 OpenAI 임베딩·Moderation·문맥 모델 요청. 정책/모델 설정은 그대로다. 외부 호출은 DB 트랜잭션 밖에서 수행한다.
- `backend/llm/commands/run_moderation_worker.py`: 서비스 호출을 반복하는 워커 실행 구현. 프로젝트의 기존 소문자 `llm` 패키지를 사용한다.
- `backend/llm/management/commands/run_moderation_worker.py`: Django가 명령을 발견하기 위한 연결 파일. `llm/commands`만으로는 `manage.py`가 명령을 자동 등록하지 않는다.
- `community/moderation_worker.py`, `community/moderation_checks.py`: 기존 import 호환용. `community/moderation_service.py`의 비공개 접수·공개·운영진 복구 트랜잭션과 ORM 모델은 유지한다.

워커를 services/llm 명령으로 옮긴 리팩터링 자체는 새 테이블이나 마이그레이션 없이 기존 큐와 필터 결과를 사용한다. 별도 접수 제한 보완은 migration `0012_moderation_admission`이 필요하다. `ModerationAdmission`은 계정 ID와 접수 시각만 저장하여 글·댓글 삭제와 무관하게 최근 1시간 접수량을 유지하며 본문·이미지·검사 사유를 보관하지 않는다. 계정 행의 PostgreSQL `FOR NO KEY UPDATE` 잠금으로 같은 계정 접수를 직렬화하되 새 글의 외래키 key-share 잠금과 교착하지 않는다. 다음 접수에서 그 계정의 만료 기록을 정리하며, migration은 남아 있는 최근 작업의 접수량만 이관하고 기존 작업·리뷰를 수정하지 않는다(이미 삭제된 작업은 복원할 수 없다). 수정 도중 본문이 바뀌는 문제를 막기 위해 LLM 입력에는 해당 버전의 저장된 스냅샷을 쓴다.

서버 스케줄러에서 미검사 전체를 수집하고 일회 실행할 명령: `python manage.py filter_unchecked_content` (`backend`에서 실행). 구현은 `llm/commands/filter_unchecked_content.py`와 `services.community_moderation.enqueue_unchecked`에 있다. 현재 내용 버전의 검사 작업이 없는 게시글·댓글을 찾아 비공개 큐에 넣은 뒤 기존 `run_moderation_worker --once`를 호출한다. 과거 승인 버전 0 데이터와 Django 기본 관리자에서 생성하여 작업이 누락된 데이터도 포함한다. 이미 완료·검토·거절된 검사 및 대기·실행 중 작업은 중복 생성하지 않는다. 실패 작업은 기존 명시적 재시도 절차를 유지하며, 아직 재시도 시간이 안 된 작업·다른 워커가 처리 중인 작업은 남은 수를 출력한다. 기존 숨김은 유지한다. 이 명령의 미검사 기준은 현재 버전의 작업 유무이므로, 버전을 올리지 않고 DB나 기본 관리자에서 이미 검사한 본문을 직접 덮어쓰는 경로는 별도로 연결해야 한다.

명령 작성만 요청받았으므로 실제 게시글 전체에 실행하거나 서버 예약을 등록하지 않았다. 기존 상시 워커도 중지하지 않았다. 예약 실행만 사용할 때는 예약 등록 확인 후 상시 워커를 중지해야 한다. 관리자 DB의 manual 선택에서는 운영진 검토로 보낸다. openai 선택 또는 DB 선택이 없으면 기본 Moderation API를 호출한다. live 호출은 기존 스냅샷에만 남아 있다.

`backend` 디렉터리에서 `python manage.py run_moderation_worker`로 계속 실행하거나 `python manage.py run_moderation_worker --once`로 현재 처리 가능한 작업을 소진한 뒤 종료한다. Docker의 기존 실행 명령은 바뀌지 않는다.

구조 변경 검증(2026-10-13): 정책 파일 이동 전후 SHA-256 `232E0CCAE13B0AD1FA420F5670CE04A4EBD21B8C63B20164D84EC9CDD8811D42` 동일. 같은 합성 10건으로 DeepEval/Ragas A·B 모두 10/10(유료 호출 0, 의미 정확도 미측정). Django `community accounts.test.test_point_integration` 126건 통과. 새 명령에서 게시글·댓글을 함께 처리하는 테스트는 기존 CI 실행 대상 `community.test_moderation`에 포함했다. 최초 통합 실행은 명령의 연결 정리가 APITestCase 트랜잭션을 닫아 9건 오류가 발생했으며, 테스트에서만 연결 정리를 mock한 뒤 재실행해 통과했다. 실행 코드의 연결 정리는 유지한다. 실제 HTTP+재시작 워커 점검도 pending 404 → 복구 200 → 수정 404, 오래된 복구 409로 통과했다. 기존 OpenAPI serializer 경고/오류 메시지는 남아 있으며 스키마 엄격 검증 통과를 뜻하지 않는다.

## 로컬 실행 명령

프로젝트 루트에서 실행:

```powershell
docker exec skn34-4th-local-backend-1 python manage.py migrate --noinput
docker compose -p skn34-4th-local -f docker-compose.moderation-local.yml up -d --no-build
docker exec skn34-4th-local-backend-1 python manage.py check_moderation_flow
```

마지막 명령은 localhost Django HTTP API와 실행 중인 워커를 시험한다. 일회용 계정·글·검토·포인트를 생성한 뒤 해당 시험 데이터만 삭제한다. 기존 계정은 변경하지 않는다. `manual`에서만 실행한다. 포스트 번호 시퀀스는 되돌리지 않는다.

전체 Compose에는 `community-moderation-worker` 서비스를 추가했다. 현재 컴퓨터의 기존 Compose 전체 파일은 Qdrant 필수 환경변수가 비어 있어 단독 워커 파일로 연결했다. 실행 중인 다른 컨테이너를 orphan으로 표시하더라도 **`--remove-orphans`를 사용하지 않는다**. 워커와 백엔드는 같은 DB·정책 환경변수를 써야 한다. 현재 로컬 DB 이름은 `mydb`; 다르면 `LOCAL_DB_NAME`을 맞춘다. 배포 시 먼저 마이그레이션한 뒤 워커를 시작한다.

## 이전 live 스냅샷의 호환 전제 (새 접수 전환 절차 아님)

`.env.example`에 설정 항목을 추가했다. 실제 `.env`나 키는 수정·기록하지 않았다.

1. OpenAI API 키 및 사용할 문맥 판정 모델(`COMMUNITY_POLICY_MODEL`)을 준비한다. Responses의 structured output을 지원하는 모델이어야 한다.
2. `community/data/political_reference_cases.json`의 **후보**를 독립 검토한다. 한국어·영어 및 야구 경계 사례를 확충하고, 별도 정답 데이터로 오탐/미탐을 측정한다. 예제 10건은 완전한 정치 표현 목록이 아니다.
3. 코사인 유사도 임계값을 보정해 `COMMUNITY_POLITICAL_THRESHOLD`를 정한다. 범위는 -1~1이며 확률이 아니다. 승인한 기준 파일만 `reviewed: true`로 바꾼다. 아무 수치도 자동 확정하지 않았다.
4. 이 전제들은 이미 저장된 `live` 스냅샷 호환용이며 새 접수를 live로 설정하는 방법이 아니다. 현재 자동/수동 선택은 관리자 DB에서만 한다. 기존 manual/live 작업은 스냅샷을 유지한다.

live: 1,000자 구간(150자 겹침)과 정치 기준 사례를 임베딩 → 최고 코사인 유사도가 임계값 이상이면 `political` 사유 생성 → `omni-moderation-latest` 유해성 분류 → 문맥 모델의 구조화된 정책 판정. 거래/광고 태그로 다른 검사를 건너뛰지 않는다. 일반 harassment는 문맥 판정으로 감탄·인용과 직접 모욕을 구분하고, 위협 등 다른 유해 카테고리는 운영진 검토한다. 모델 출력의 인용문이 실제 원문에 없거나 응답이 불완전하면 오류로 남긴다.

원문 URL을 서버가 직접 방문하지 않는다. 악성 링크 조회 제공자와 OCR/이미지 정책 모델은 미연결이므로 URL·이미지 첨부는 별도 운영진 검토 사유다. 운영진 화면은 인증된 이미지 API를 통해 첨부물을 보여준다. 이미지 URL 직접 접근도 부모 게시글 공개 상태를 검사한다.

참고: [Moderations](https://developers.openai.com/api/docs/guides/moderation), [omni-moderation-latest](https://developers.openai.com/api/docs/models/omni-moderation-latest). 기본 moderation에는 정치 게시물 차단 정책이 없으므로 임베딩과 커뮤니티 문맥 정책을 별도로 적용한다.

## 정책 사유

| 사유 | 처리 |
| --- | --- |
| political | 정치 유사도 기준 이상 → 비공개 검토 |
| harm | 기본 moderation 유해 카테고리 → 검토 |
| abuse / brigading | 특정인 모욕·공격 동원 검토. 감탄·야구 전술 비판은 허용 |
| privacy / impersonation / fraud | 타인 개인정보·인증정보, 사칭, 복합 사기 정황 검토. 가격·선입금 한 단어로 단정하지 않음 |
| gambling | 사설 도박 가입·입금·환전 유도 검토. 서비스 승부 예측/팬 예상은 허용 |
| allegation / unauthorized_distribution | 중대한 미확인 사생활 의혹, 무단 유출/배포 검토 |
| off_topic_ad / spam | 야구 무관 광고·가입 유도, 본문 반복 도배 검토 |
| 거래·광고 허용 | 티켓·굿즈 거래 및 야구 관련 광고·협찬 자체는 허용. 작성자 체크나 표시 누락은 검토 사유가 아니며 본문 문맥으로 판정. 독립적인 사기·개인정보 등 위반은 계속 검토 |
| context_review | 문맥만으로 결론을 못 내린 경우 검토 |
| external_link_review / image_review | 링크 목적지·이미지를 사람이 확인 |
| manual_review | 자동 모델 미사용 상태의 수동 검토 |

고정 금칙어 전수 목록으로 구현하지 않았다. 변형·혼합 언어·문맥 때문에 ‘모든 정치/욕설 100% 탐지’는 보장하지 않는다. 한영 예제와 실제 커뮤니티 표본을 별도 평가해야 한다. 반복 계정·다계정 동원 탐지, URL 평판, OCR은 후속 범위다. 현재 쓰기 제한은 계정당 시간당 30회(글·댓글·수정 합산)이며 실패 요청은 DB 변경을 롤백한다.

## 파일과 기존 작업 접점

| 파일군 | 반영 내용 / 병합 시 주의점 |
| --- | --- |
| `community/models.py`, `moderation_models.py`, `migrations/0011*` | 상태·버전·최초 공개 시각·거래/광고 태그, 작업 큐·검토 감사기록. 다른 0011 마이그레이션과 번호/의존성 충돌 가능 |
| `moderation_service.py`, `moderation_worker.py`, `moderation_checks.py`, management commands | 버전·lease token 검증, 재시도, 사유별 운영진 복구, API 어댑터. provider 호출 중 DB 잠금 없음 |
| `views.py`, `publishing.py`, `interactions.py`, `draft*`, `serializers.py`, `urls.py` | 새 제출 202, 기존 idempotent 재전송 200, 수정 시 재검사. 초안 이미지 이동·요청 중복 방지와 함께 검토 |
| `visibility.py`, `activity.py`, `images.py`, `llm/tools/community.py` | 공개 목록/상세/활동/댓글/투표/신고/이미지/챗봇 검색 차단. 회원 활동 브랜치와 직접 겹침 |
| `admin_views.py`, `moderation_views.py` | 운영진·작성자 전용 읽기 및 검토. 공개 상세와 운영진 원문 열람을 분리 |
| `accounts/test/test_point_integration.py` 및 공개 처리 서비스 | 최초 승인 공개 시 1회 지급. 기존 포인트 서비스 자체는 변경하지 않음 |
| Frontend editor/API/내 글/admin panels + `community-moderation.tsx`, submissions page | 거래·광고 체크박스 없음. 비공개 상태 안내, 작성자/운영진 JWT 조회·복구. 기존 태그 필드는 API/DB 호환용으로 유지하며 문맥 모델에 전달하지 않음 |
| `contracts/openapi.yaml`, `frontend/lib/api/schema.d.ts` | 전체 생성 파일이라 기존 계약 드리프트/번역 변경도 포함. 병합 후 양쪽을 재생성할 것 |
| settings, env example, Compose, CI/evaluation/tests | 관리자 DB 선택/openai 기본값, 실행 워커, 네트워크 없는 테스트. 운영 evaluation 의존성 추가 없음 |

`frontend/tests/community-category.test.mjs`에는 브랜치 원본의 중첩 merge conflict 표시가 남아 있어 기존 JSX 테스트 내용을 유지하며 표시와 중복 helper를 정리했다. 실제 서비스 코스·챗봇 생성 모델과 성능 설정은 변경하지 않았다.

## 검증 범위

`python manage.py test community accounts.test.test_point_integration --noinput`은 PostgreSQL에서 실행한다. `community.test_moderation`은 pending 공개 누출, 최초 공개 포인트, 수정/중복 제출, 사유별 복구, 별도 숨김 유지, 운영진 권한, 오류/재시도, lease 교체와 이전 결과 폐기, 챗봇 조회를 검사한다.

`backend/llm/v2/evaluation/test_community_moderation.py`는 분리된 DeepEval/Ragas 환경에서 synthetic provider 결과 10건의 실제 adapter 흐름을 평가한다. 네트워크 연결을 차단하며 **실제 모델 정확도는 미측정**이다. `.github/workflows/community-moderation.yml`에 관련 테스트를 연결했다.

2026-10-13 체크박스 제거 검증: 표시 없는 거래·야구 광고 2건을 포함한 동일 10건으로 A/B 실행, DeepEval·Ragas 모두 A 10/10, B 10/10. 합성 제공자 adapter 회귀이며 문맥 모델 품질 개선 점수가 아니다. Django `community.test_moderation` 14건 통과. Chrome에서 새 글/수정 화면 모두 체크박스 0개 확인. 로컬 frontend와 검사 worker 재시작으로 반영했다. 실제 모델 호출은 없으며 현재 manual 모드의 운영진 검토 흐름은 유지된다.

전체 API 스키마 생성에는 기존 챗봇의 serializer 미지정 경고/오류가 남아 있다. 새 커뮤니티 endpoint의 생성은 확인했으나 `contracts:check --fail-on-warn` 전체 통과로 보고하지 않는다. 실제 유료 provider A/B 평가는 미실행이며 품질 판정은 `needs-review`다.
