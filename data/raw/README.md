# data/raw

팀이 준 원본 파일들. 어떤 스크립트도 자동으로 덮어쓰지 않음.

- 구장먹거리,컨텐츠.xlsx
- 구장정보.xlsx
- 시트가 뭘 의미하는지.xlsx
- 재입장 규정.비공식.txt
- backlog 보충.xlsx
- kbo_schedule.csv (팀원 원본 크롤링, 9월 한 달치 — 확장판은 data/preprocessed/kbo_schedule_full.csv)
- kbo_ticket_policy.csv (yagu.today 크롤링 원본, 자연어 문장 형태 — 구조화본은 data/preprocessed/kbo_ticket_policy_structured.csv)
- team_stadium_code_map.csv (팀·구장 표준 코드 매핑표. 구장정보.xlsx 기준으로 만든 기준표라 raw로 분류 — 스크립트가 자동 생성하지 않음)
- KBO_잔여정보_보완자료_좌석도_주차_버스_20260907.xlsx (팀원 보완 조사, 2026-09-08 추가. 광주 KIA 좌석도/좌석수, 대전·대구 주차 수용면·요금, 대전 버스정류장명. 시트 3개: 최종_보완데이터/입력용_요약/검증_메모. status·evidence_type·source_url까지 컬럼으로 갖춰져 있어 별도 구조화 없이 바로 참고 가능)
- KBO_9개구장_편의시설_통합_20260907.xlsx (팀원 보완 조사, 2026-09-08 추가. 9개 구장 화장실·수유실·흡연구역·장애인화장실·임산부휴게실 205건 통합. 시트 4개: 현행_시설/보류_이력/구장별_요약/기준_설명. 기존 구장정보.xlsx의 Facilities(49건)보다 훨씬 촘촘함 — 편의시설 정보 부족 문제가 사실상 해결됨)

※ kbo_standing.csv는 여기 없음 — 크롤러가 실행할 때마다 덮어쓰는 생성물이라 data/preprocessed/에 있음.
