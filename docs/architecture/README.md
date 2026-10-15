# 아키텍처 · 흐름 다이어그램

[archify](https://github.com/tt-a1i/archify)로 만든 인터랙티브 다이어그램입니다. 원본은 `*.archify.json`, 결과는 같은 이름의 `.html`입니다.

| 다이어그램 | 종류 | 인터랙티브 | 움직이는 그림 (README용) | 확대용 (SVG) |
| --- | --- | --- | --- | --- |
| **4차 시스템 아키텍처** | architecture | [HTML](system_architecture_4th.html) | [PNG](../images/system_architecture_4th.png) | — |
| 시스템 아키텍처 (3차) | architecture | [HTML](system_architecture.html) | [GIF](../images/system_architecture.gif) | [SVG](https://raw.githubusercontent.com/SKNETWORKS-FAMILY-AICAMP/SKN34-3rd-5Team/develop/docs/images/system_architecture.svg) |
| 챗봇 RAG · 에이전트 흐름 | workflow | [HTML](chat_pipeline.html) | [GIF](../images/chat_pipeline.gif) | [SVG](https://raw.githubusercontent.com/SKNETWORKS-FAMILY-AICAMP/SKN34-3rd-5Team/develop/docs/images/chat_pipeline.svg) |
| 직관 코스 추천 순서 | sequence | [HTML](course_sequence.html) | [GIF](../images/course_sequence.gif) | [SVG](https://raw.githubusercontent.com/SKNETWORKS-FAMILY-AICAMP/SKN34-3rd-5Team/develop/docs/images/course_sequence.svg) |
| 데이터 수집 · 전처리 · 인덱싱 | dataflow | [HTML](data_pipeline.html) | [GIF](../images/data_pipeline.gif) | [SVG](https://raw.githubusercontent.com/SKNETWORKS-FAMILY-AICAMP/SKN34-3rd-5Team/develop/docs/images/data_pipeline.svg) |
| 협업 · CI/CD 배포 | workflow | [HTML](deploy_cicd.html) | [GIF](../images/deploy_cicd.gif) | [SVG](https://raw.githubusercontent.com/SKNETWORKS-FAMILY-AICAMP/SKN34-3rd-5Team/develop/docs/images/deploy_cicd.svg) |
| 화면 흐름 (UX Flow) | workflow | [HTML](ux_flow.html) | [GIF](../images/ux_flow.gif) | [SVG](https://raw.githubusercontent.com/SKNETWORKS-FAMILY-AICAMP/SKN34-3rd-5Team/develop/docs/images/ux_flow.svg) |

- README 의 흐름도 그림을 누르면 SVG 원본(가로 2000px)이 열립니다. 벡터라서 Ctrl + 휠로 더 키워도 선명합니다.
- GitHub 는 레포 안의 HTML 을 실행하지 않습니다. HTML 은 내려받아 브라우저로 열면 **Present**(발표 화면) · **Play story**(단계별 설명) · 노드 클릭 · 검색이 됩니다.
- 뷰어 오른쪽 위 **Export** 메뉴에서 PNG · SVG · 영상(WebM)을 바로 내려받을 수 있습니다.
- 4차 시스템 아키텍처는 `develop` 2026-10-15(`b81f3f4`) 코드 기준입니다. 그 외 다이어그램은 3차 기준이라 4차 구조(LangGraph · Qdrant · 하위 에이전트)와 다릅니다.
