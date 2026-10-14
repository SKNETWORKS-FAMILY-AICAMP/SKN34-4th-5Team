# 자동/수동 선택과 OpenAI 기본 검사 검증

2026-10-13, feat/21-member-activity. 기존 미커밋 충돌 복구 변경 위에 추가했다.

## 변경

- 관리자 설정 GET/POST API와 자동/수동 선택 UI. singleton DB 저장 및 변경 관리자/시각 기록. 키가 없으면 자동 저장 거절, 일반회원/익명 접근 금지.
- 새 자동 접수는 `openai` 스냅샷. 전체 본문에 omni-moderation-latest를 한 번 호출하고 flagged를 그대로 반영한다. embeddings/Responses/정치 threshold를 사용하지 않는다. 이미지 미검사 사유는 유지한다.
- 기존 수동 및 이전 live 작업 스냅샷은 보존. DB 저장 모드가 없는 경우 환경변수 초기값을 사용하되 live는 새 기본 자동 모드로 매핑한다.
- migration 0013_moderationsettings 로컬 적용. 현재 모드는 manual, automaticAvailable=true. 기존 게시물을 재검사하거나 자동 공개하지 않았다.

## 검증 결과

- CI django-integration 명령: `python manage.py test community community.moderation_probe llm.tests.test_domain_tools.DomainToolsTest accounts.test.test_point_integration --noinput --verbosity=1` — 151개 통과, 격리 PostgreSQL 테스트 DB 사용.
- 전체 `npm test` — 752개 중 750 통과, 기존 2 skip, 실패 0. CI의 community-moderation.test.mjs가 새 UI 회귀를 자동 발견한다. 초기 검토카드 테스트가 새 설정 컴포넌트를 잘못 선택하던 문제는 job prop으로 대상을 특정하여 수정하고 전체 재실행했다.
- Next typegen / 전체 TypeScript 검사 통과. 모델 변경 누락 검사 통과.
- provider-contracts에 `python -I backend/llm/v2/evaluation/test_moderation_native.py` 추가. 기존 격리 `/tmp/community-evaluation`에서 실제 DeepEval/Ragas 실행.
- 신규 고정 7개 계약의 구현 전 A: 각 프레임워크 1/7(새 openai 모드 미지원). 구현 후 B: 각 7/7. 같은 입력/기대값/fixture/metric/cache-off 조건, provider_calls=0. 이는 분류 품질이 아닌 API 결과 분기 계약이다.
- 기존 test_community_moderation.py: DeepEval/Ragas 각 10/10 유지.
- 실제 OpenAI smoke: 개인 게시물이 아닌 정상 야구 예문 1건, omni-moderation-latest, flagged=false, findings=[] 확인. 이 1건은 전반적 분류 정확도나 정치/욕설 품질 평가가 아니다.
- 실제 관리자 계정의 설정 API GET 200, 키 내용 미노출, 워커 API 키 존재 확인. backend/worker/frontend 재시작 완료.
- 브라우저 자동화 도구는 커널 경로 오류로 초기화되지 않아 시각 검증은 미실행. UI 이벤트 회귀·타입 검사와 HTTP 확인을 사용했다.
- 재시작 뒤 Next 개발 생성 타입 2개에 중복 꼬리가 남아 최종 tsc가 실패했다. 프론트를 중지한 상태에서 `.next/dev/types/routes.d.ts`, `validator.ts`만 제거하고 재시작하여 재생성했다. 관리자 화면 HTTP 200 확인.

공식 응답 규격: https://developers.openai.com/api/docs/guides/moderation

## 후속: 자동 기본값 및 기존 수동 전체 재검사

- 사용자 요청으로 settings/.env.example/모델 기본값을 openai로 변경. 0014 migration 적용. 명시적 수동 선택은 여전히 가능하다.
- 현재 버전의 수동 작업 5건(이미 운영진이 해제한 1건 포함)을 잠금/트랜잭션 안에서 기존 enqueue_locked로 재접수했다. 이전 job 7/8/9/14/15는 기록으로 보존하고 새 job 16/17/18/19/20을 만들었다. 현재 버전이 이미 자동 작업인 대상은 제외한다.
- 실제 omni-moderation-latest 처리 완료: 4건 approved, 1건 harm 사유로 review, 실패/대기 0. 현재 버전의 수동 작업 잔여 0. 기존 관리자 숨김 상태는 유지했다. 재검사 대상 댓글은 0건이었다.
- `python manage.py test community.test_moderation_mode community.test_moderation accounts.test.test_point_integration --noinput`: 40개 통과. 새 기본값/명시적 수동 우선/재접수 후 이력·숨김·최초 공개시각 유지 회귀 포함. 기존 community-moderation.yml django-integration의 community 자동 발견 범위에 포함된다.
- 백엔드/워커 재시작 완료. 원격 운영 서버에는 적용하지 않았다.
