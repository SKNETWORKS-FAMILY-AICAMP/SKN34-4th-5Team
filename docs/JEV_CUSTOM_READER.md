# JEV 프로젝트 DOM reader

## 소스·빌드

`docker/jev-browser/jev`는 https://github.com/0x7067/jev-browse 의
`07c2dce0983181d6b2fee3dbdc8b892dae562656` (0.16.0)을 complete clone의 git archive로 반입한 일반 소스 트리다.
원본 LICENSE를 유지하며 nested .git/gitlink가 없다. Dockerfile은 로컬 소스를 COPY하고 locked npm ci --ignore-scripts로 빌드한다.
기존 patch-helper는 이미지 안에서만 provider request 호환 패치를 적용한다. apt/npm/pip 다운로드가 필요하므로 offline build는 아니다.

## 첨부 URL 경계

첨부 `source_text`는 자동으로 `web_body → jev_read_body`를 호출한다. Reader는 upstream CDP transport만 재사용하고
모델 agent/provider를 호출하지 않는다. main model의 `wrap_model_call` 요청에 관찰 본문을 untrusted data로 복원한다.
완전 성공 원문만 기존 extracted_text에 캐시하며 legacy 원문 캐시도 유지한다. partial/failure/generated answer는 캐시하지 않는다.
DB migration 없이 transient provenance를 전달하므로 재사용 캐시에는 최초 extractor/time/frame metadata가 남지 않는다.
캐시 provenance는 legacy-or-observed로 보수적으로 표시하고 현재 DOM임을 주장하지 않는다.

계약: schema_version=1, extractor_version=jev-dom-v1, status, requested_url/final_url/source_url, title,
body, source_kind=rendered_dom_snapshot, collected_at, frames(role/status/url/title/chars), limitations.
status는 ok/partial/blocked/busy/timeout/error/overflow/cancelled이며 cancellation은 예외로 전파한다.
본문은 렌더링 DOM 텍스트이지 원본 HTML·생성 요약이 아니다. 일부 자료 실패는 현재 성공 자료의 실패로 취급하지 않는다.
모델·캐시 진입 전에 URL/title/time/frame/limitations 타입과 개수·전체 크기를 검증한다. malformed evidence는 해당 출처의 error이며,
알려진 MCP/httpx/anyio transport 오류만 timeout/error로 정규화한다. 취소·보안 검증·저장소·프로그램 오류는 숨기지 않는다.
성공 출처의 재첨부를 요청하지 않으며 URL 인용 링크에는 정확한 source_url만, chars 위치는 링크 밖에 둔다.

## 프레임·한도·보안

article/main/블로그 본문 컨테이너를 우선하고 normal-security CDP default execution context로 nested/cross-origin iframe을 읽는다.
지도·주소 보조 자료는 별도 프레임 provenance로 유지하고 광고/소셜 본문은 제외한다.
상태와 URL 비교로 누락/실패한 article iframe은 partial로 표시한다. 프레임 역할은 DOM/container/URL heuristic이므로 모든 사이트의 의미를 보장하지 않는다.
최대 24 context, tree depth 6, scroll 6회/300ms, startup/DOM wait 및 MCP subprocess 45초,
CDP call timeout 30초, 전체 JSON 2MiB와 model-context 20,000 aggregate token 한도를 적용한다.
긴 무한 스크롤의 전부 수집은 보장하지 않는다. arbitrary-depth OOPIF frame ancestry가 root tree에 없으면 depth 판정은 제한되지만 context count/time은 항상 bounded다.

Chrome는 기존 non-root UID/node sandbox/proxy/seccomp/NET_ADMIN entrypoint 그대로 사용한다.
server finally는 CLI/Chrome process group을 kill/drain/wait하고 Tini는 이미지 ENTRYPOINT 한 번만 사용한다.
Squid의 IPv4-mapped `::ffff:0:0/96`는 실제 5.7 파서에서 `0.0.0.0/0`으로 변환되는 것이 확인되어 제거했다.
IPv4 destination ACL에 사설/loopback/link-local을 명시하며 mapped private IPv4도 해당 ACL로 차단한다.
본문/credentials는 ordinary logs에 남기지 않는다.

## 자동 배포 설정

개발 `.github/workflows/deploy-dev.yml`은 base + dev Compose의 `web-research` profile을 선택하고
`backend frontend jev-browser`를 build/start한다. 기존 frontend anonymous volume 갱신과 nginx 재생성·검사를 유지한다.
운영 `.github/workflows/deploy-prod.yml`도 standalone `docker-compose.prod.yml`의 같은 profile과 reader를 명시한다.
reader는 기존 Compose 내부 네트워크에서만 `8080`을 사용하며 host port를 공개하지 않는다.
기존 NET_ADMIN/seccomp, Chromium sandbox/proxy, 256MiB shared memory와 제한 로그 설정을 유지한다.

배포 전 각 서버의 `~/dev/.env`에 **동일한 비어 있지 않은 `JEV_MCP_TOKEN`**을 설정한다.
두 서비스는 서버 `.env`의 token을 공유하며 workflow에 별도 token 사전 검증을 두지 않는다.
운영은 Compose의 `${JEV_MCP_TOKEN:?Set JEV_MCP_TOKEN}`으로 누락/빈 token을 거부하고,
reader는 잘못된 token 구성을 런타임에 거부한다. 실제 token은 저장소/GitHub workflow에 넣지 않는다. 운영 Compose에는 `env_file`이 없으므로
`JEV_MCP_URL`, `JEV_MCP_TOKEN`, `WEB_RESEARCH_ENABLED`, `RESEARCH_MODEL`, `RESEARCH_BASE_URL`, `RESEARCH_API_KEY`를 backend에 명시 전달한다.
기본 MCP URL은 `http://jev-browser:8080/mcp`, 모델은 `gpt-6-luna`, 연구 base URL은 `https://api.openai.com/v1`이며 nonempty override를 유지한다.
`RESEARCH_API_KEY`가 없으면 기존 `OPENAI_API_KEY`를 사용한다. 일반 provider의 `OPENAI_BASE_URL` 전달은 이 배포 변경의 범위가 아니다.

`WEB_RESEARCH_ENABLED=false`를 유지해도 첨부 URL 원문 reader와 강제 첨부 specialist는 동작한다.
일반 웹 조사만 별도 opt-in이며, reader 배포를 위해 `true`로 바꾸지 않는다. 로컬 reader 미사용 환경은 기존 base Compose 그대로다.
로컬 회귀 검증: `python -m unittest discover -s backend/llm/tests -p test_usage_compose.py`.
이는 Compose/workflow 구성 검증이며 원격 배포 또는 실제 provider/chat E2E 성공을 의미하지 않는다.

## 검증·후속 단계

집중 Django 테스트: `llm.tests.test_input_attachments llm.v2.tests.test_web_specialist`.
서버 offline lifecycle/malformed: `python docker/jev-browser/test_server.py` (mcp dependencies 필요).
실제 DOM fixture 회귀: `JEV_FIXTURE_BASE`, `JEV_MCP_URL`, `JEV_MCP_TOKEN`을 설정하여 `test_dom_reader.py` 실행.
통제 fixture·disposable DB harness 및 sanitized evidence는 `/Users/yunseongho/.hermes/cache/scratch/jev-custom-development`에 보존한다.

기존 search callers의 goal-based jev_browse/web_page_analysis는 여전히 존재한다.
search-query-only Luna 및 parallel execution, broader web-search redesign은 이번 단계에서 변경하지 않는다.
실제 provider HTTP/SSE chat E2E는 별도 검증자가 수행하며 standalone reader 성공을 chat E2E 성공으로 간주하지 않는다.
