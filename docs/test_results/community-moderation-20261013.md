# 커뮤니티 검사 통합 기록 — 2026-10-13

작업 브랜치: `feat/21-member-activity`, 기준 HEAD 및 origin 브랜치: `37c17124b587978bf7b81ae959e78bb34f6002e3`. 소스 변경은 미커밋 로컬 상태. 최신 upstream 조회는 했으나 현재 작업 기준을 바꾸는 병합은 하지 않았다.

## 실행 결과

| 검사 | 결과 |
| --- | --- |
| Django `test community accounts.test.test_point_integration --noinput` | 125개 통과. PostgreSQL 테스트 DB 생성·삭제 |
| 최종 댓글 수 조회 최적화 후 영향 경로 재검증 | `community.test_moderation community.tests.CommunityPostApiTests` 22개 통과 |
| 프런트엔드 전체 `npm test` | 총 739, 통과 737, 기존 skip 2, 실패 0 |
| `npm run build` | 성공. 타입 검사 포함. `/community/submissions` 정적 경로 생성 |
| 변경 프런트엔드 ESLint | 오류 0. 인증 Blob 이미지를 위한 일반 img 경고 1 |
| Django `check`, `makemigrations --check --dry-run` | 정상, 미생성 변경 없음 |
| 실제 localhost HTTP + 실행 워커 | 제출 202 → 익명 상세 404 → 운영진 해제 200 → 익명 상세 200 → 수정 202 → 익명 상세 404 → 이전 판정 요청 409 |
| 새 페이지 HTTP | Nginx를 통해 `/community/write`, `/community/submissions`, `/admin?tab=moderation` 각각 200 |
| DeepEval 4.2.8 / Ragas 0.4.3 | synthetic provider 8건씩 통과. 실제 모델 호출 0 |
| OpenAPI 생성 | 생성 성공. 기존 챗봇 serializer 미정의 등 38 errors / 5 warnings 출력. strict 계약 검사 통과로 간주하지 않음 |

로컬 실행 중인 백엔드에 `migrate`를 적용했다. community 0011 외에도 이미 브랜치에 있었으나 이 DB에 미적용이던 accounts 0008, fantasy 0001~0005, llm 0014 두 갈래와 0015 merge가 적용됐다. 기존 공개 community 데이터는 승인 버전 0으로 보존한다. 검사 워커 컨테이너를 추가했고 새 라우트 인식을 위해 frontend를 재시작했다.

임시 HTTP 시험 사용자와 게시글·작업·검토·포인트는 삭제했다. 첫 시험의 사용자 삭제가 포인트 원장 PROTECT 제약으로 실패하여, 해당 시험의 정확한 사용자 ID/이름만 확인하고 원장을 먼저 정리했다. 명령의 정리 절차를 수정한 뒤 재실행해 성공했다. 기존 회원·게시글은 이 정리 대상에 포함하지 않았다. 시퀀스 번호는 되돌리지 않았다.

## 동일 입력 A/B

`community/moderation_probe.py`의 같은 HTTP 입력과 기대값을 사용했다. A는 `git archive HEAD backend` 원본을 컨테이너 `/tmp/moderation-baseline`에 분리하여 실행했고, DB도 `test_moderation_baseline`으로 분리했다. B는 수정 작업 트리와 별도 `test_mydb`에서 실행했다. 데이터는 같은 읽기 전용 `/data`를 사용했다. 스냅샷/후보 소스 차이 외에 기대값은 변경하지 않았다.

| 고정 관찰 | A 원본 | B 후보 |
| --- | --- | --- |
| 미검토 글 공개 목록 | 노출 | 비노출 |
| 미검토 글 공개 상세 | 200 | 404 |
| 미검토 글 댓글 경로 | 200 | 404 |
| 최초 제출 포인트 | 즉시 지급 | 승인까지 미지급 |

실제 두 로그를 `community_moderation_ab.py`로 채점한 결과, 사전에 정한 비공개 대기 계약에서 DeepEval A 0/4 → B 4/4, Ragas A 0/4 → B 4/4였다. 두 프레임워크 점수 간 우열을 비교하지 않는다. A의 0/4는 과거 기능의 일반적인 결함률이 아니라 **이번에 새로 요구한 비공개 대기 정책**에 대한 불일치다. 전체 작업은 먼저 기준 문서를 기록했으나, 원본 스냅샷의 동결 A/B 관찰 실행은 구현 이후에 수행했다.

synthetic provider 8건은 감탄형 wtf, 거래, 야구 광고, 정치 임계값 분기, 태그가 있어도 사기 검토, 기본 유해성, 문맥 모욕, URL 검토다. 임베딩 벡터와 모델 응답을 고정한 연결 검사이며 언어 모델 분류 정확도는 측정하지 않았다. 실제 모델/임계값/기준 corpus 품질은 `not-run / needs-review`다.

## 발견 후 수정한 항목과 제한

- 기존 테스트의 즉시 공개/201/즉시 포인트 전제를 202/검사 후 공개에 맞춰 변경했다. 공개 조회용 ORM fixture에는 승인 상태를 명시했다.
- 기존 `community-category.test.mjs`의 중첩 merge conflict 표시와 중복 helper를 제거하되 추가되어 있던 UI 테스트는 유지했다.
- frontend 컨테이너만으로 전체 테스트를 실행했을 때 backend/data/nginx 경로가 없어 발생한 ENOENT는, 해당 형제 디렉터리를 읽기 전용 마운트한 독립 컨테이너에서 재검증했다.
- 호스트 node_modules에는 일부 기존 markdown 의존성이 누락되어 실제 frontend 컨테이너 의존성으로 타입 검사·전체 테스트·빌드를 검증했다. package lock은 변경하지 않았다.
- 브라우저 자동화 도구 초기화 오류로 스크린샷 기반 시각 검사는 하지 못했다. HTTP, React 처리 테스트, TypeScript, 프로덕션 빌드는 수행했다.
- 기본 수동 모드에서는 모든 새 제출이 운영진 검토로 이동한다. 실제 OpenAI 자동 분류를 켜거나 정확도 검증을 완료했다고 보고하지 않는다.

사용법·파일별 병합 접점·정책 목록: [커뮤니티 검사 안내](../COMMUNITY_MODERATION.md).
