# 날씨 리뷰 수정 오프라인 검증 (2026-10-13)

## 범위와 실행 순서

기준을 EVALUATION_CRITERIA.md에 먼저 기록한 뒤 weather_contract.py의 입력/기대값을 동결하고, 수정 전 HEAD d7c160e의 실제 StructuredTool.invoke로 A를 실행했다. A의 오류/실패를 보존한 다음 weather.py만 수정하고 동일 harness/사례로 B를 실행했다. 실제 서비스 호출은 고정 fixture로 대체했고 socket connect/connect_ex/create_connection을 차단했다. AST 추출/대체 도구가 아니라 실제 production tool/schema/common wrapper를 실행했다.

수정은 None 반환 보존과 오늘 23시 cap의 정직한 근거 표기뿐이다. ExternalDomainToolAdapterTest 정확한 클래스와 11-case production export/DeepEval entrypoint를 FREE에 추가했다. 별도 highlight-free에는 실제 DeepEval/Ragas 채점기의 합성 정상/오류 대조를 추가했다. DB-heavy DomainToolsTest는 선택하지 않았다.

## 동결 설정과 근거

- A/B 입력: KST 2026-10-13, JAMSIL, 고정 기상 응답, 명시 호출 인자까지 완전 일치 검사
- DeepEval 4.2.8 custom BaseMetric `weather_contract`, strict exact 1.0, sync, cache read/write false, judge 없음
- Ragas 0.4.3 custom SingleTurnMetric `weather_contract`, response/reference exact, max_workers=1/max_retries=0, raise_exceptions=true, LLM/embedding 없음
- production export/DeepEval: scratch/jev-eval-venv (Python 3.12.12); export의 동일 실제 결과를 scratch/highlight-deepeval/venv (Python 3.12.12)에서 Ragas 채점
- cases SHA256: `41893ca05d1b8ff05972322e13e8191857ad1445846f5a0ddc7ae35c945abdaf`
- A/B 동일 harness SHA256: `4e350bbf61365e865b65dda27f75ef9113f6f37b400a4a134b83752a0d92220c`
- A weather SHA256: `c71183154cbbc0687345f41e29237245d58de9c40a62c3118f06a2dc25eb9e9c`
- B weather SHA256: `bcc21d646f3fb7f76077e8a1867a1a940123cf6baf276e90fbdfc852ed09be67`

| 고정 사례 | A DeepEval / Ragas | B DeepEval / Ragas |
| --- | --- | --- |
| omitted-none | 0 / 0 (AttributeError) | 1 / 1 |
| null-none | 0 / 0 (AttributeError) | 1 / 1 |
| explicit-none | 1 / 1 | 1 / 1 |
| explicit-result | 1 / 1 | 1 / 1 |
| today-next | 1 / 1 | 1 / 1 |
| today-mismatch | 1 / 1 | 1 / 1 |
| future-mismatch | 1 / 1 | 1 / 1 |
| last-23:00 | 0 / 0 (허위 다음 정시) | 1 / 1 |
| last-23:30 | 0 / 0 (허위 다음 정시) | 1 / 1 |
| last-23:59 | 0 / 0 (허위 다음 정시) | 1 / 1 |
| invalid-time | 1 / 1 (서비스 호출 0) | 1 / 1 |

각 framework A 6/11, B 11/11이며 서로 다른 지표의 우열 비교가 아니다. A export/score 종료 1은 보존된 제품 계약 실패이고 채점 시스템 오류가 아니다. B 둘 다 종료 0.

## 실제 검증 명령과 결과

저장소 루트에서 해당 isolated Python으로 실행했다. 모든 output은 새 scratch 경로이며 dotenv discovery/telemetry/tracing을 비활성화했다.

```sh
"$PY" -I .github/evaluation_ci.py offline-exec backend/llm/v2/evaluation/weather_contract.py export --output "$SCRATCH/A-export.json"
"$HIGHLIGHT_PY" -I backend/llm/v2/evaluation/weather_contract.py score --input "$SCRATCH/A-export.json" --output "$SCRATCH/A-score.json"
# production 수정 후 동일 명령으로 B-export.json/B-score.json 생성
"$PY" -I .github/evaluation_ci.py free --output "$SCRATCH/free"
"$HIGHLIGHT_PY" -I .github/evaluation_ci.py highlight-free --output "$SCRATCH/highlight-free"
"$PY" -I .github/test_evaluation_ci.py
```

- 실제 free lane 종료 0: seed 13/boundary 26, 기존 테스트 19+4+12+101+7 및 adapter 5개 모두 성공, weather production 계약 11/11
- 실제 highlight-free 종료 0: 기존 1+2개 테스트 성공, weather 합성 채점기 정상 1/오류 0을 두 framework에서 확인
- CI wiring 10개 성공: exact class/export argv 및 highlight scorer-check 포함 확인
- disposable Docker backend manifest 환경 focused suite 24개 성공: adapter 5, ToolErrorContractTest 1, DomainAllowlistTest 18; network none/read-only/tmpfs, 기존 스택 변경 없음
- git diff --check 성공; HEAD/브랜치 변경·stage·commit·push·merge 없음

## 증거 경계

최종 답변·live KMA·실제 LLM/provider 품질은 미측정이며 새 유료 호출은 0이다. 새로운 일반 품질 threshold/유료 gate/운영 의존성을 추가하지 않았다. 이미 알려진 travel.weather_test_urls 경로 불일치 실패는 수정/재평가 범위 밖이며 이 결과로 해결됐다고 주장하지 않는다. 새 기준·harness의 독립 검토와 운영 제공자 품질 평가는 별도다.

전체 원시 A/B 및 실행 로그: `/Users/yunseongho/.hermes/cache/scratch/weather-fix-d7c160e/`의 A-export.json/A-score.json/B-export.json/B-score.json, 각 .log, free.log/highlight-free.log/wiring.log/docker-adapter.log 및 각 CI summary. 합성 latest-run 캐시는 framework-last-run.json으로 scratch에 옮겼다.

## replay 독립 정답 신뢰 경계 후속 수정 (2026-10-13)

기준을 먼저 추가하고 실제 CLI로 expected=actual 변조 A가 수정 전 Ragas 11/11·종료 0인 결함을 재현했다. 작은 validated_rows helper로 동결 CASES의 수·순서·ID·expected 전체 및 actual 존재를 채점 전에 확인한다. 해시만 같은 변조는 거절하며 점수 누락/비유한 값도 보고서 기록 전 실패한다. production weather.py와 CASES는 변경하지 않았다.

- 수정 후 같은 변조 A: 종료 1, Frozen cases mismatch, 채점/출력 파일 생성 전 거절
- 기존 실제 A export replay: Ragas 6/11·종료 1; 기존 B export replay: Ragas 11/11·종료 0 (새 production 생성 근거가 아닌 replay)
- 새 실제 B production export: DeepEval 4.2.8 11/11·종료 0; 같은 출력 Ragas 0.4.3 11/11·종료 0
- 실제 highlight-free 종료 0: 기존 framework 대조와 canonical 정상/expected 변조/hash/ID/누락/중복/순서/형식/expected 누락 회귀 포함; CI wiring 10개 성공
- CASES SHA256 `41893ca05d1b8ff05972322e13e8191857ad1445846f5a0ddc7ae35c945abdaf` 유지; production SHA256 `bcc21d646f3fb7f76077e8a1867a1a940123cf6baf276e90fbdfc852ed09be67` 유지
- 새 scorer/harness SHA256 `e2859ee94002118599835f8e25d9fe128bbfe37f210a08e83630695edf5c32b5`; replay는 기존 producing harness SHA를 보존하고 scorer_sha256를 별도로 기록

원시 로그/JSON과 후속 보고서는 동일 scratch의 `scorer-repair/`에 보존했다. 전체 free 및 Docker 24개는 production 해시 일치에 따라 위 기존 결과를 승계했으며 이번에 재실행하지 않았다. 네트워크 차단·dotenv/tracing 비활성·DeepEval read-only 환경이며 유료 호출 0; 독립 최종 검토와 제공자 품질 평가는 여전히 별도다.

## 원격 afcfaaa 통합 — 승인 계약 v2 (2026-10-13)

위 결과는 역사적 v1 계약으로 보존한다. 사용자 승인에 따라 precipitation 명시 및 생략/null 무예보 안내 dict를 유지하는 v2 기준을 먼저 기록하고 독립 기대값을 동결했다. 실제 pinned afcfaaa archive/backend·공개 fixture와 동일 신규 harness로 A를 실행한 다음 `git merge --no-commit --no-ff afcfaaae1e8e01314ad9eba2f7ca523d24b536ca`로 통합했다. weather.py 충돌은 원격 강수/무예보 계약과 로컬23h 마지막 정시를 모두 보존했고 자동 병합 테스트의 이전 None/23h 기대값만 승인 계약에 맞췄다.

- v2 cases SHA256: `ef65f881b9df891cbd78f10e387dd0a15d3c299fecc5a365a83c59bde4ee915c`
- 동일 A/B harness SHA256: `6127c1c28206d6f747a1293cb49ec0f6b6daf45364c387288b8d48d6ed7288a3`
- 동일 11개 입력·서비스 fixture(precipitation 사전 주입 없음)·완전 일치 지표·DeepEval4.2.8/Ragas0.4.3·Python3.12.12·설정 유지
- A 두 framework 각각8/11, 종료1: last-23:00/23:30/23:59의 허위 다음 정시만 실패; B 각각11/11, 종료0
- 실제 free/highlight-free 모두 종료0, CI wiring10/10, pinned Docker adapter24/24 재실행 성공
- highlight-free의 정상1/오류0 합성 대조 및 canonical expected/hash/ID/행 변조 거절 유지
- HEAD48be37e 유지, MERGE_HEAD afcfaaa, unmerged entries 없음, 양쪽 diff check 성공; commit/push 없음

명령은 위 실제 검증 명령과 동일하며 신규 output만 `/Users/yunseongho/.hermes/cache/scratch/weather-push-integration/`으로 변경했다. A-source의 신규 harness도 동일 launcher로 실행했고 score는 같은 frozen canonical harness로 실행했다. 원시 A-export/A-score/B-export/B-score JSON·각 로그, free/highlight-free/wiring/docker-adapter 로그 및 manifest/implementation-report를 이 scratch에 보존한다. 네트워크 차단·dotenv/telemetry/tracing 비활성·read-only 평가, 유료 호출0; 최종 답변·live 제공자·hosted Actions·배포 미측정 및 독립 검토 대기는 유지한다.
