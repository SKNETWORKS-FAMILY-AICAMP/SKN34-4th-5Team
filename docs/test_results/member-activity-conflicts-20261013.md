# 커뮤니티 충돌 복구 검증 (2026-10-13)

- 대상: `feat/21-member-activity`. 커밋된 충돌 표시 12개 파일을 정리했다. Git index의 unmerged 항목은 없었다.
- 모델의 ModerationAdmission 등록, 계정별 접수 제한, Boolean 정규화 멱등 재시도, 비공개 글 삭제와 관련 회귀를 유지했다. 기존 검사 정책, 비공개 처리, 검토·복구, 포인트 지급과 검사 화면 CSS를 보존했다.
- 최신 upstream/develop을 fetch하여 확인했다. 이번 작업은 잔여 충돌 복구이며 추가 병합은 수행하지 않았다. 복구한 community 모델·서비스·views는 확인한 upstream/develop과 동일하다.

## 검증

- 저장소 전체(숨김·ignore 파일 포함)에서 충돌 시작/구분/종료/base 표시 검색: 0건. Git 내부·의존성·빌드 산출물·가상환경·로그·환경변수 파일은 제외했다.
- `git diff --check`: 통과.
- `python manage.py makemigrations community --check --dry-run`: 변경 없음. 기존 `0012_moderation_admission` 적용 확인, 재시작 시 미적용 migration 없음.
- `python manage.py test community community.moderation_probe llm.tests.test_domain_tools.DomainToolsTest accounts.test.test_point_integration accounts.test.test_point_service fantasy tving.tests.test_home_schedule --noinput --verbosity=2`: 격리 PostgreSQL 테스트 DB에서 **193개 통과**.
- `npx next typegen` 이후 `npx tsc --noEmit --incremental false`: 통과.
- `npm test`: **749개 중 747개 통과, 기존 2개 skip, 실패 0개**. 프론트 컨테이너와 같은 의존성을 사용하고 backend/data/nginx/.github 및 docker-compose.prod.yml을 읽기 전용 마운트했다.
- 초기 실행의 ENOENT는 테스트 컨테이너 마운트 누락으로 해결했다. 이전 frontend_details 브랜치 전용 test_member_level/test_fallback 모듈을 포함한 최초 백엔드 실행은 import 오류였으며, 현재 브랜치에 존재하는 위 모듈로 다시 실행한 결과다.

## CI 연결

기존 `.github/workflows/community-moderation.yml`의 `django-integration` job에서 확장된 명령을 보존했다.

```sh
python backend/manage.py test community community.moderation_probe llm.tests.test_domain_tools.DomainToolsTest accounts.test.test_point_integration --noinput --verbosity=2
node --test tests/community-api.test.mjs tests/community-moderation.test.mjs tests/admin-dashboard.test.mjs tests/auth-bridge.test.mjs
```

위 대상은 각각 이번 Django 실행 및 전체 npm test에 포함되어 실행됐다. 원격 GitHub Actions를 실행한 것은 아니다. 정책/모델 변경 및 실제 LLM 제공자 품질 평가는 이번 범위에 없다.

## 재시작 확인

로컬 backend/frontend/community-moderation-worker/nginx 재시작 완료. `/login`, `/admin?tab=moderation`, `/api/v1/community/posts/` HTTP 200 확인. 운영 EC2에는 변경하지 않았다.
