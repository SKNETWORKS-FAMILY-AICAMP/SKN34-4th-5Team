import json

from django.http import StreamingHttpResponse

"""SSE(Server-Sent Events) 스트리밍 HTTP 응답 관련 로직.

service.chat 은 (event, data) 튜플만 만들고, 이 모듈이 그걸 text/event-stream
StreamingHttpResponse 로 감싸는 HTTP 전송 계층 일을 담당한다.
"""


def _frame(event, data):
    """SSE 와이어 포맷 한 프레임: event: <name>\ndata: <json>\n\n"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def event_stream_response(events):
    """(event, data) 튜플 이터러블을 SSE StreamingHttpResponse 로 감싼다."""
    def generate():
        for event, data in events:
            yield _frame(event, data)

    response = StreamingHttpResponse(generate(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
