import json
import time

from django.db import close_old_connections
from django.http import Http404, StreamingHttpResponse
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import serializers

from llm.models import ChatRequest
from llm.serializer.message import ChatMessageInputSerializer
from llm.service import chat_queue
from llm.service.ownership import get_owned_session
from llm.views.message import _input_options, GuestChatThrottle, EventStreamRenderer
from rest_framework.renderers import JSONRenderer


class QueueInput(ChatMessageInputSerializer):
    request_id = serializers.UUIDField()
    continuation = serializers.BooleanField(default=False)


class QueueEdit(ChatMessageInputSerializer):
    revision = serializers.IntegerField(min_value=0)


class ChatQueueView(GenericAPIView):
    permission_classes = [AllowAny]
    throttle_classes = [GuestChatThrottle]

    def get(self, request, session_id, version):
        session = get_owned_session(request, session_id)
        return Response([chat_queue.dto(row) for row in session.requests.order_by("-order")[:100]][::-1])

    def post(self, request, session_id, version):
        session = get_owned_session(request, session_id)
        serializer = QueueInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        request_id = data.pop("request_id")
        data["version"] = version
        # Exact retries remain valid after completion even when attachments were later removed.
        if not ChatRequest.objects.filter(session=session, id=request_id).exists():
            _input_options(session, data, version)
        return Response(chat_queue.dto(chat_queue.enqueue(session, request_id, data)), status=202)


class ChatQueueDetailView(GenericAPIView):
    permission_classes = [AllowAny]

    def patch(self, request, session_id, request_id, version):
        session = get_owned_session(request, session_id)
        serializer = QueueEdit(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        revision = data.pop("revision")
        _input_options(session, data, version)
        data["version"] = version
        return Response(chat_queue.dto(chat_queue.change(session, request_id, payload=data, revision=revision)))

    def delete(self, request, session_id, request_id, version):
        session = get_owned_session(request, session_id)
        return Response(chat_queue.dto(chat_queue.change(session, request_id)))


class ChatQueueEventsView(GenericAPIView):
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer, EventStreamRenderer]

    def get(self, request, session_id, request_id, version):
        session = get_owned_session(request, session_id)
        row = ChatRequest.objects.filter(session=session, id=request_id).first()
        if row is None:
            raise Http404
        try:
            cursor = int(request.query_params.get("after", 0))
            if cursor < 0:
                raise ValueError()
        except (ValueError, TypeError):
            raise serializers.ValidationError("이벤트 번호를 확인해 주세요.")

        def stream():
            nonlocal cursor
            try:
                while True:
                    close_old_connections()
                    current = ChatRequest.objects.filter(pk=row.pk, session_id=session.id).first()
                    if current is None:
                        yield 'event: stopped\ndata: {}\n\n'
                        return
                    first = current.events.order_by("id").first()
                    if first and ((cursor and not current.events.filter(id=cursor).exists() and cursor < first.id) or (not cursor and current.events.count() >= chat_queue.EVENT_LIMIT)):
                        yield 'event: error\ndata: {"detail":"대화 기록을 다시 불러와 주세요."}\n\n'
                        return
                    for frame in current.events.filter(id__gt=cursor).order_by("id"):
                        cursor = frame.id
                        yield f'id: {cursor}\nevent: {frame.event}\ndata: {json.dumps(frame.data, ensure_ascii=False)}\n\n'
                    if current.status in chat_queue.TERMINAL:
                        if not current.events.filter(event__in=("done", "error", "stopped")).exists():
                            yield f'event: error\ndata: {json.dumps({"detail": current.error or "예약이 취소되었어요."}, ensure_ascii=False)}\n\n'
                        return
                    yield ': keep-alive\n\n'
                    time.sleep(0.5)
            finally:
                close_old_connections()  # Observation closure never cancels provider execution.

        response = StreamingHttpResponse(stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
