# 챗봇 예약 워커 운영

`docker compose up -d backend chat-worker`로 시작합니다. 운영 설정은 `docker compose -f docker-compose.prod.yml up -d backend chat-worker`입니다. API의 `/api/v1/healthz/`가 성공하고 migration/checkpoint 초기화가 끝난 뒤 워커가 시작합니다. 워커 heartbeat와 `docker compose logs chat-worker`를 확인합니다. 새 요청은 PostgreSQL에 예약되므로 브라우저 종료로 실행이 취소되지 않습니다.

복구는 **단일 호스트의 영속 `chat_queue_state` 볼륨**만 지원합니다. `docker compose restart chat-worker`는 같은 scope와 lock inode를 유지합니다. 워커가 자식 provider 프로세스에 상속한 flock을 얻은 뒤에만 중단 요청을 정산합니다. lease 만료나 오래된 시간은 종료 증명이 아닙니다. 실행 중 볼륨·scope·lock 파일을 삭제하거나 교체하지 않습니다. NFS/다중 호스트 복구는 지원하지 않습니다.

강제 종료된 실행은 재호출하지 않고 실패 처리한 뒤 다음 예약을 실행합니다. 최종 답변 checkpoint 저장이 먼저 끝난 경우 취소/복구도 완료 상태와 저장된 답변을 유지합니다. 저장 이전 취소 또는 대화 기록 수정·삭제는 실행을 fence합니다. 알려진 provider 토큰은 보존하며 미확인 호출은 추정하지 않습니다.

이전 버전 또는 다른 scope의 `reserved` 원장은 자동 해제하지 않습니다. 예약 화면의 종료 확인 안내가 보이면 원래 provider 작업의 종료를 운영자가 확인한 뒤 기존 `settle_abandoned` 절차로 처리해야 합니다. 모든 과거 예약이 자동 복구되는 것은 아닙니다.

세션 삭제 시 예약 입력/context/도구·첨부 참조와 이벤트를 즉시 지웁니다. 실행 중에는 opaque 요청·attempt·charge 식별자만 정산까지 남기고 이후 고아 요청을 삭제합니다. 완료 이벤트는 최대 512개/요청이며 워커가 24시간 이후 삭제합니다. 삭제가 아닌 정상 대화의 요청 입력은 세션 기록에 속하며 유지됩니다. 클라이언트는 만료된 이벤트 대신 기록을 다시 읽습니다.

재시도 intent는 해당 탭의 sessionStorage에 사용자 identity별 payload fingerprint·UUID 맵으로 저장합니다. 새로고침만으로 자동 재전송하지 않습니다. 진행 중 admission의 응답이 유실되면 같은 UUID로 최대 3회 확인하며, 이후에는 불확실성 오류를 표시합니다. 조기 중단은 admission 확인 후 같은 UUID를 취소합니다. 명시적 동일 입력 재시도도 UUID를 재사용하고 승인 시 해당 항목만, identity 전환 시 전체를 제거합니다. JWT/인증 정보는 여기에 저장하지 않습니다.

실행 중 질문에 이어 예약하고 지도 세대가 바뀌지 않았을 때만 continuation을 표시합니다. 워커는 수정되지 않은 accepted context를 최신 완료 코스에 이어 적용합니다. 독립적인 지도 변경이나 예약 context 수정은 명시적 입력을 유지합니다.
