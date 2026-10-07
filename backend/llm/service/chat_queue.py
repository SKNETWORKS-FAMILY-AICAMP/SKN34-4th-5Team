"""PostgreSQL admission and execution fences. Provider work never runs inside a transaction."""
import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound

from llm.models import ChatRequest, ChatRequestEvent, ChatSession, UsageCharge
from llm.service import usage

LIVE = ("queued", "running")
TERMINAL = ("completed", "failed", "cancelled")
EVENT_LIMIT = 512


class Conflict(APIException):
    status_code = 409
    default_detail = "예약 상태가 바뀌었어요. 다시 확인해 주세요."


def dto(row):
    return {"id": str(row.id), "session_id": str(row.session_id), "order": row.order,
            "status": row.status, "revision": row.revision, "content": row.payload["content"],
            "context": row.payload.get("context"), "attachment_ids": row.payload.get("attachment_ids", []),
            "tool_group_ids": row.payload.get("tool_group_ids", []), "version": row.payload["version"],
            "error": row.error, "cancel_requested": row.cancel_requested,
            "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat()}


def enqueue(session, request_id, payload):
    # Wallet lock also orders admission across this owner's sessions.
    with transaction.atomic():
        usage._locked_wallet(usage._owner(session))
        ChatSession.objects.select_for_update().get(pk=session.pk)
        old = ChatRequest.objects.filter(id=request_id).first()
        if old:
            if old.session_id != session.id:
                raise NotFound()
            if old.accepted_payload != payload:
                raise Conflict("같은 요청 번호에 다른 내용을 보낼 수 없습니다.")
            return old
        if ChatRequest.objects.filter(session=session, status="queued").count() >= 2:
            raise Conflict("대기 질문은 2개까지 예약할 수 있어요.")
        return ChatRequest.objects.create(id=request_id, session=session, payload=payload, accepted_payload=payload)


def change(session, request_id, *, payload=None, revision=None):
    with transaction.atomic():
        ChatSession.objects.select_for_update().get(pk=session.pk)
        row = ChatRequest.objects.select_for_update().filter(session=session, id=request_id).first()
        if row is None:
            raise NotFound()
        if payload is not None:
            if row.status != "queued" or row.revision != revision:
                raise Conflict()
            row.payload = payload
            row.revision += 1
        elif row.status == "queued":
            row.status = "cancelled"
        elif row.status == "running":
            from llm.service.chat_thread import ChatThread
            _, turns = ChatThread(session.id).state()
            turn = turns.get(str(row.human_id), {})
            if not (turn.get("status") == "completed" and turn.get("answer_id") == str(row.answer_id)):
                row.cancel_requested = True
        row.save()
        return row


def invalidate_waiting(session_id):
    """Called under the session write lock when history changes."""
    ChatRequest.objects.filter(session_id=session_id, status="queued").update(status="cancelled", error="대화 기록이 변경되어 예약을 취소했어요.", updated_at=timezone.now())
    ChatRequest.objects.filter(session_id=session_id, status="running").update(cancel_requested=True)


def fence(row_id, attempt, *, lock=False, allow_cancel=False):
    rows = ChatRequest.objects.select_for_update() if lock else ChatRequest.objects
    rows = rows.filter(id=row_id, attempt=attempt, status="running")
    if not allow_cancel:
        rows = rows.filter(cancel_requested=False, lease_expires_at__gt=timezone.now())
    row = rows.first()
    if row is None:
        from llm.service.chat_runs import Stopped
        raise Stopped()
    return row


def append(row_id, attempt, event, data):
    if event == "heartbeat":
        return
    from llm.serializer.message import public_frame
    frame = public_frame(event, data, False)
    if frame is None:
        return
    event, data = frame
    if event == "tool":
        data = {key: value for key, value in data.items() if key not in ("detail", "args", "result", "title")}
    with transaction.atomic():
        row = fence(row_id, attempt, lock=True)
        ChatRequestEvent.objects.create(request=row, event=event, data=data)
        cutoff = list(row.events.order_by("-id").values_list("id", flat=True)[EVENT_LIMIT:EVENT_LIMIT + 1])
        if cutoff:
            row.events.filter(id__lte=cutoff[0]).delete()


def cleanup():
    ChatRequest.objects.filter(session__isnull=True).exclude(status="running").delete()
    ChatRequestEvent.objects.filter(request__status__in=TERMINAL, created_at__lt=timezone.now() - timedelta(days=1)).delete()


def claim(scope):
    ChatRequest.objects.filter(session__isnull=True, status="queued").update(status="cancelled")
    for candidate in ChatRequest.objects.filter(status="queued", session__isnull=False).select_related("session").order_by("order").iterator(chunk_size=32):
        with transaction.atomic():
            # Admission creates the wallet before publishing the queued row.
            owner = usage._owner(candidate.session)
            if not usage.UsageWallet.objects.select_for_update(skip_locked=True).filter(**owner).exists():
                continue
            wallet = usage._locked_wallet(usage._owner(candidate.session))
            # Same-owner FIFO, including cross-session requests.
            sessions = ChatSession.objects.filter(**usage._owner(candidate.session))
            first = ChatRequest.objects.filter(session__in=sessions, status__in=LIVE).order_by("order").first()
            if first is None or first.id != candidate.id or first.status != "queued":
                continue
            if wallet.charges.filter(status=UsageCharge.RESERVED).exists():
                ChatRequest.objects.filter(pk=candidate.pk, status="queued").update(error="이전 작업의 종료 확인이 필요해요. 운영자에게 문의해 주세요.")
                continue  # legacy/foreign reservation requires proof, never age-clear
            if not ChatSession.objects.select_for_update(skip_locked=True).filter(pk=candidate.session_id).exists():
                continue
            row = ChatRequest.objects.select_for_update().get(pk=candidate.pk)
            if row.status != "queued":
                continue
            try:
                charge = usage.reserve(candidate.session, queued=True)
            except usage.InsufficientCredits:
                row.status, row.error = "failed", usage.EXHAUSTED_MESSAGE
                row.save()
                continue
            row.error = ""
            row.status, row.attempt, row.charge = "running", uuid.uuid4(), charge
            row.worker, row.heartbeat_at = {"scope": scope}, timezone.now()
            row.lease_expires_at = timezone.now() + timedelta(seconds=30)
            row.save()
            return row
    return None


def execute(request_id, attempt):
    """Child process consumes the existing V1/V2 pipeline, not an HTTP observer."""
    from llm.service.chat_thread import ChatThread
    from llm.service import chat_v1, chat_v2
    row = fence(request_id, attempt)
    thread = ChatThread(row.session_id)
    thread.queue_request, thread.queue_attempt = str(row.id), attempt
    thread.answer_id = str(row.answer_id)
    charge = row.charge
    charge.queue_request, charge.queue_attempt = str(row.id), attempt
    payload = row.payload
    from llm.service.attachments import resolve
    resolve(row.session_id, payload.get("attachment_ids", []))
    options = {key: payload[key] for key in ("attachment_ids", "tool_group_ids") if key in payload}
    begin = thread.ask(payload["content"], options, human_id=str(row.human_id))
    if payload["version"] == "v1":
        frames = chat_v1._stream_turn(thread, *begin, charge)
    else:
        context = payload.get("context")
        if payload.get("continuation") and context == row.accepted_payload.get("context"):
            # An edited/independent map keeps its explicit context; only accepted continuation rebases.
            from llm.v1.rag.course.memory import restore
            current = restore(begin[0], begin[1]).get("current")
            if current:
                context = {**(context or {}), "currentCourse": current}
        frames = chat_v2._stream_turn(thread, *begin, context, charge)
    try:
        next(frames)
        for event, data in frames:
            append(request_id, attempt, event, data)
    finally:
        frames.close()


def reconcile(request_id, attempt, *, interrupted=False):
    """Only the supervisor holding the request flock may call this, after child join/death proof."""
    from llm.service.chat_thread import ChatThread
    from llm.enum import TurnStatus
    row = ChatRequest.objects.filter(id=request_id, attempt=attempt, status="running").first()
    if row is None:
        return
    thread = ChatThread(row.session_id) if row.session_id else None
    messages, turns = thread.state() if thread else ([], {})
    turn = turns.get(str(row.human_id), {})
    completed = turn.get("status") == TurnStatus.COMPLETED and turn.get("answer_id") == str(row.answer_id)
    if turn.get("status") == TurnStatus.PENDING:
        thread.queue_request, thread.queue_attempt, thread.queue_allow_cancel = str(row.id), attempt, True
        thread.update([], {str(row.human_id): {"status": "cancelled" if row.cancel_requested else "failed", "answer_id": None}})
    # Persisted known totals are preserved; interrupted missing callbacks stay unknown.
    if row.charge_id is not None:
        usage.settle(row.charge, unknown=interrupted and not completed and not row.worker.get("meter_finished", False), queue_attempt=attempt)
    with transaction.atomic():
        if row.session_id:
            ChatSession.objects.select_for_update().filter(pk=row.session_id).exists()
        row = ChatRequest.objects.select_for_update().filter(id=request_id, attempt=attempt, status="running").first()
        if row is None:
            return
        if row.session_id is None:
            row.delete()
            return
        messages, turns = ChatThread(row.session_id).state()
        turn = turns.get(str(row.human_id), {})
        completed = turn.get("status") == TurnStatus.COMPLETED and turn.get("answer_id") == str(row.answer_id)
        row.status = "completed" if completed else "cancelled" if row.cancel_requested else "failed"
        row.error = "" if row.status == "completed" else "답변 생성이 중단되었어요." if interrupted else "답변을 완료하지 못했어요."
        row.save()
        if not row.events.filter(event__in=("done", "error", "stopped")).exists():
            if row.status == "completed":
                from llm.serializer.message import wire_done, public_frame
                event, data = public_frame("done", wire_done(messages, turns, thread.wire), False)
            else:
                event, data = ("stopped", {}) if row.status == "cancelled" else ("error", {"detail": row.error})
            ChatRequestEvent.objects.create(request=row, event=event, data=data)
