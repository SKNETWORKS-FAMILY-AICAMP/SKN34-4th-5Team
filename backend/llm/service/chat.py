import os

from django.db import transaction
from django.http import Http404

from llm.models import ChatMessage
from llm.enum import ChatRole, MessageStatus
from llm.service.ownership import get_owned_session


"""
V2 채팅 메시지 비즈니스 로직
"""

GENERIC_ERROR_MESSAGE = "답변 생성에 실패했습니다. 다시 시도해 주세요."

SUPPORTED_CHAIN_VERSIONS = ("v1", "v2")


def chain_version():
    """설정된 LLM 체인 버전. env LLM_CHAIN_VERSION (기본 "v2")."""
    return os.getenv("LLM_CHAIN_VERSION", "v2")


def resolve_version(version=None):
    """호출자가 넘긴 version(URL 등)이 있으면 그걸 쓰고, 없으면 env 로 내려간다.

    HTTP 가 아닌 기존 호출자(예: 관리 커맨드, 배치)는 그대로 env 기본값을 쓴다.
    URL 로 들어온 값은 v1/v2 가 아니면 조용히 무시하지 않고 여기서 바로 막는다.
    """
    resolved = version if version is not None else chain_version()
    if resolved not in SUPPORTED_CHAIN_VERSIONS:
        raise ValueError(f"지원하지 않는 채팅 버전입니다: {resolved!r}")
    return resolved


def get_chain(version=None):
    """체인 버전 선택. 기본값은 chain_version(), 인자로 override 가능.

    v1: llm.v1.rag.pipeline.chat_chain() -- invoke/stream({"question","chat_history"}) -> str
    v2: llm.v2.agent.chain.chain       -- invoke/stream({"question","chat_history"}) -> str

    v1/v2 가 아닌 값은 resolve_version() 이 ValueError 로 막는다 (v2 로 조용히 폴백하지 않는다).
    """
    if resolve_version(version) == "v1":
        from llm.v1.rag.pipeline import chat_chain
        return chat_chain()

    from llm.v2.agent.chain import chain
    return chain


def _build_history(session):
    """세션의 이전 COMPLETED 메시지를 LangChain Human/AIMessage 리스트로 만든다 (이번 질문 제외)."""
    from langchain_core.messages import AIMessage, HumanMessage

    history = []
    for msg in (
        ChatMessage.objects
        .filter(session=session, status=MessageStatus.COMPLETED)
        .order_by("sequence_no")
    ):
        if msg.role == ChatRole.USER:
            history.append(HumanMessage(content=msg.message))
        else:
            history.append(AIMessage(content=msg.message))
    return history


def _next_sequence_no(session):
    last_sequence = (
        ChatMessage.objects
        .filter(session=session)
        .order_by("-sequence_no")
        .values_list("sequence_no", flat=True)
        .first()
        or 0
    )
    return last_sequence + 1


def _stream_chat_events(session, content, version, history, user_message, context=None):
    """chat 이벤트((event, data) 튜플)를 만든다. 실제로 순회될 때만 실행된다 (lazy).

    프레임 순서: delta(청크마다) -> done. 실패하면 error 하나만 보낸다.

    스트리밍(chain.stream) 도중에는 DB 락을 잡지 않는다 -- 모델 생성 중 세션을 잠그면
    안 된다. 생성이 끝난 뒤 완료 저장 직전에만 짧게 select_for_update 로 user_message
    행을 잠깐 잠그고, 그 사이 message_delete/message_update 가 같은 행을 먼저 지웠으면
    (동시 삭제/수정 요청) 오래된 스트림의 답변은 저장하지 않고 done 없이 조용히
    끝낸다 (이미 새 질문으로 대체된 turn을 되살리지 않는다).
    """
    chunks = []
    try:
        chain = get_chain(version)
        # v1/v2 모두 {"question","chat_history"} 로 통일해서 보낸다. context 는 v2 전용 입력이다.
        chain_input = {"question": content, "chat_history": history}
        if version != "v1" and context:
            chain_input["context"] = context

        for chunk in chain.stream(chain_input):
            chunks.append(chunk)
            yield "delta", {"text": chunk}

        answer = "".join(chunks)
        with transaction.atomic():
            # 스트리밍이 끝난 뒤에만 짧게 잠근다 (모델 생성 중에는 절대 잠그지 않음).
            # 이 user_message 가 이미 지워졌으면(message_delete/message_update 로 동시
            # 삭제/수정됨) 오래된 스트림의 답변을 저장하지 않는다 -- 지워진 turn 되살리기 방지.
            still_pending = (
                ChatMessage.objects
                .select_for_update()
                .filter(id=user_message.id, status=MessageStatus.PENDING)
                .exists()
            )
            if not still_pending:
                return

            assistant_message = ChatMessage.objects.create(
                session=session,
                sequence_no=_next_sequence_no(session),
                role=ChatRole.ASSISTANT,
                status=MessageStatus.COMPLETED,
                message=answer,
            )
            user_message.status = MessageStatus.COMPLETED
            user_message.save(update_fields=["status", "updated_at"])

        yield "done", {
            "message_id": str(assistant_message.id),
            "assistant_message": answer,
        }
    except Exception:
        with transaction.atomic():
            # 실패 처리도 같은 행을 잠깐 잠가서 확인한다: 이미 삭제/수정으로 지워진
            # turn 이면 FAILED 로 되살리지 않고 그대로 둔다.
            still_pending = (
                ChatMessage.objects
                .select_for_update()
                .filter(id=user_message.id, status=MessageStatus.PENDING)
                .exists()
            )
            if not still_pending:
                return

            user_message.status = MessageStatus.FAILED
            user_message.save(update_fields=["status", "updated_at"])
            ChatMessage.objects.create(
                session=session,
                sequence_no=_next_sequence_no(session),
                role=ChatRole.ASSISTANT,
                status=MessageStatus.FAILED,
                message="".join(chunks),
            )
        yield "error", {"detail": GENERIC_ERROR_MESSAGE}


def send_message(session, content, context=None, version=None):
    """사용자 메시지를 저장하고 (event, data) 튜플을 흘려보내는 제너레이터를 돌려준다.

    context: {"stadium"?, "intent"?, "origin"?} 선택 사항. 없으면 이전과 동일하게 동작한다
    (v1 은 애초에 이 인자를 쓰지 않는다).
    version: "v1"/"v2" 로 호출자(URL)가 명시하면 그걸 쓰고, 없으면 env(LLM_CHAIN_VERSION)로
    내려간다. v1/v2 가 아닌 값은 여기서 ValueError 로 막는다 (조용한 v2 폴백 없음).

    HTTP/SSE 응답으로 감싸는 일은 호출자(views)의 책임이다.
    """
    version = resolve_version(version)
    history = _build_history(session)

    user_message = ChatMessage.objects.create(
        session=session,
        sequence_no=_next_sequence_no(session),
        role=ChatRole.USER,
        status=MessageStatus.PENDING,
        message=content,
    )

    return _stream_chat_events(session, content, version, history, user_message, context)


def message_update(request, session_id, message_id, content, context=None, version=None):
    """해당 사용자 메시지부터 이후 대화를 삭제한 뒤 수정한 내용으로 다시 질문합니다."""
    version = resolve_version(version)  # 삭제 전에 검증: 잘못된 버전이면 메시지를 지우지 않는다
    session = get_owned_session(request, session_id)
    message_delete(request, session_id, message_id)
    return send_message(session, content, context, version=version)


def message_delete(request, session_id, message_id):
    """해당 사용자 메시지부터 이후 메시지를 모두 삭제합니다."""
    session = get_owned_session(request, session_id)

    with transaction.atomic():
        # select_for_update 로 target 행을 잠깐 잠가서 진행 중인 스트림의 완료 저장
        # (_stream_chat_events 의 같은 select_for_update) 과 순서를 맞춘다. 이 트랜잭션이
        # 커밋되면 스트림 쪽은 자신의 user_message 가 사라졌다고 보고 답변을 저장하지 않는다.
        try:
            target_message = ChatMessage.objects.select_for_update().get(
                id=message_id,
                session=session,
                role=ChatRole.USER,
            )
        except ChatMessage.DoesNotExist:
            # 존재하지 않는/다른 세션의/user 가 아닌 message_id -- 404 로 통일한다
            # (기존에는 여기서 예외가 그대로 올라가 500 이 됐다).
            raise Http404("message not found") from None

        deleted_count, _ = (
            ChatMessage.objects
            .filter(
                session=session,
                sequence_no__gte=target_message.sequence_no,
            )
            .delete()
        )

    return deleted_count


# ponytail: 스트리밍 중단(stop) 기능은 호출자·URL 이 아직 없어 구현하지 않았다.
# 붙일 때는 진행 중인 chain.stream 제너레이터를 세션 밖에서 취소할 수단
# (취소 토큰 또는 task handle)이 먼저 필요하다.
