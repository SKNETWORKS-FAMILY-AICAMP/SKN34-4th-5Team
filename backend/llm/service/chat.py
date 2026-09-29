"""채팅 유스케이스 facade — 체인 선택, 한 턴의 생성·스트리밍, 공개 API(send/list/update/delete).

대화 저장(LangGraph checkpoint, 동시 쓰기 순서, 세션 삭제 outbox)은 llm.service.chat_thread 가 맡는다.
이 모듈은 그쪽을 일방향으로 쓴다.
LLM 생성·도구 실행·SSE 전송은 잠금 없이 돌고, 저장 시점에만 ChatThread.update 의 짧은 fence 를 지난다.
"""
import asyncio
import logging
import os
import uuid
from contextlib import closing

from langchain_core.messages import AIMessage, HumanMessage

from llm.enum import ChainVersion, ChatRole, PublicChatEvent, TurnStatus
from llm.serializer.message import (
    error_payload,
    project_event,
    project_history,
    wire_done,
    wire_history,
)
from llm.service.chat_thread import ChatThread
from llm.service.ownership import get_owned_session

log = logging.getLogger(__name__)

SUPPORTED_CHAIN_VERSIONS = tuple(ChainVersion.values)  # ("v1", "v2")


def chain_version():
    """설정된 LLM 체인 버전. env LLM_CHAIN_VERSION (기본 "v2")."""
    return os.getenv("LLM_CHAIN_VERSION", ChainVersion.V2.value)


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

    v1: llm.v1.rag.pipeline.chat_chain() -- astream_events({"question","chat_history","run"}, version="v2")
    v2: llm.v2.agent.chain.chain       -- stream({"question","chat_history","context"?}) -> str

    v1/v2 가 아닌 값은 resolve_version() 이 ValueError 로 막는다 (v2 로 조용히 폴백하지 않는다).
    """
    if resolve_version(version) == ChainVersion.V1:
        from llm.v1.rag.pipeline import chat_chain
        return chat_chain()

    from llm.v2.agent.chain import chain
    return chain


def _model_history(messages, turns):
    """다음 모델 입력용 projection: 완료된 턴의 질문/최종 답변만. 원본(도구 기록)은 그대로 둔다."""
    return [
        HumanMessage(item["content"]) if item["role"] == ChatRole.USER else AIMessage(item["content"])
        for item in project_history(messages, turns) if item["status"] == TurnStatus.COMPLETED
    ]


# ── 스트리밍 ──────────────────────────────────────────────────────────────────────
def _sync_events(agen, run):
    """async astream_events 를 WSGI 동기 이터레이터로 한 이벤트씩 넘긴다.

    요청 스레드에서 전용 event loop 를 이벤트 하나만큼씩 돌린다. 파이프라인은 asyncio.to_thread
    worker 에서 계속 돌기 때문에 provider 청크가 생성 완료 전에 바로 전달된다.
    닫힐 때(정상 종료·실패·클라이언트 연결 종료) run["cancelled"] 로 worker 를 멈추고
    worker 가 끝날 때까지 기다린 뒤 loop 를 닫는다.
    """
    loop = asyncio.new_event_loop()
    try:
        while True:
            try:
                event = loop.run_until_complete(agen.__anext__())
            except StopAsyncIteration:
                return
            yield event
    finally:
        run["cancelled"] = True
        try:
            loop.run_until_complete(agen.aclose())
            # ponytail: 취소 플래그는 청크/도구 호출 사이에서만 확인된다. 진행 중인 도구 호출은
            # 자기 timeout 까지 기다린다. 더 빨라야 하면 도구 쪽에 취소 토큰을 넘긴다.
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()


def _v1_frames(chain, chain_input, run):
    """v1: 공식 이벤트 → 공개 (event, data). 기록되는 도구(대응표에 있는 run)만 내보낸다."""
    events = chain.astream_events(chain_input, version="v2")
    with closing(_sync_events(events, run)) as stream:
        for event in stream:
            if str(event.get("event", "")).startswith("on_tool_") and event.get("run_id") not in run["tool_call_ids"]:
                continue  # 저장되지 않는 도구(폴백 도메인 내부 등)는 조회 내역과 어긋나므로 내보내지 않는다
            frame = project_event(
                event, answer_run_id=run["answer_run_id"], tool_call_ids=run["tool_call_ids"],
            )
            if frame:
                yield frame


def _stream_turn(thread, prefix, turns, human, version, context):
    """(event, data) 제너레이터. _start 가 첫 yield 까지 미리 돌려 둔다(priming).

    그래서 소비 전에 close() 돼도 아래 GeneratorExit 경로가 돌아 턴을 cancelled 로 저장한다.
    프레임: delta*/tool* → done(최종 저장 성공 뒤 한 번) 또는 error.
    생성은 잠금 없이 돌고(같은 대화의 다른 요청과 병렬), 저장만 thread.update 의 fence 를 지난다.
    """
    run = {
        "answer_run_id": uuid.uuid4(),
        "tool_call_ids": {},    # 도구 run_id(str) -> tool_call_id (v1 파이프라인이 채운다)
        "messages": [],         # 실제 AI(tool_calls)/ToolMessage 기록 (v1 파이프라인이 채운다)
        "answer": None,
        "cancelled": False,
    }

    def finish(final, status):
        """이번 턴의 도구 기록·최종 답변을 덧붙이고 (messages, turns) 를 돌려준다. 세션이 없으면 None."""
        added = [*run["messages"], *([final] if final else [])]
        turn = {"status": status.value, "answer_id": final.id if final else None}  # checkpoint 에는 순수 str
        if not thread.update(added, {human.id: turn}):
            return None
        return [*prefix, human, *added], {**turns, human.id: turn}

    final = None
    try:
        yield None  # priming
        chain = get_chain(version)
        # v1/v2 모두 {"question","chat_history"} 로 보낸다. context 는 v2 전용 입력이다.
        chain_input = {"question": human.content, "chat_history": _model_history(prefix, turns)}
        if version == ChainVersion.V1:
            chain_input["run"] = run
            yield from _v1_frames(chain, chain_input, run)
            answer = run["answer"]
        else:
            if context:
                chain_input["context"] = context
            chunks = []
            for chunk in chain.stream(chain_input):
                chunks.append(chunk)
                yield PublicChatEvent.DELTA.value, {"text": chunk}
            answer = "".join(chunks)
        if not isinstance(answer, str) or not answer:
            raise ValueError("agent returned no answer")
        final = AIMessage(answer, id=str(uuid.uuid4()))
    except GeneratorExit:
        # 클라이언트 연결 종료: 이미 저장된 질문·도구 내역은 두고 턴만 cancelled. 부분 답변은 저장 안 함.
        try:
            finish(None, TurnStatus.CANCELLED)
        except Exception:
            log.exception("chat cancel save failed")
        raise
    except Exception:
        log.exception("chat generation failed")

    done = None
    try:
        saved = finish(final, TurnStatus.COMPLETED if final else TurnStatus.FAILED)
        if final is not None and saved is not None:
            done = wire_done(*saved, thread.wire)
    except Exception:
        # ponytail: 최종 저장이 실패하면 턴은 pending 으로 남는다(질문은 이미 저장됨). 다음 요청은 정상 진행.
        log.exception("chat save failed")
    yield (PublicChatEvent.DONE.value, done) if done else (PublicChatEvent.ERROR.value, error_payload())


def _start(thread, turn, version, context):
    """(prefix, turns, human) 로 priming 된 스트림을 돌려준다."""
    frames = _stream_turn(thread, *turn, version, context)
    next(frames)  # priming
    return frames


# ── 공개 유스케이스 ────────────────────────────────────────────────────────────────
def send_message(session, content, context=None, version=None):
    """사용자 메시지를 저장하고 (event, data) 튜플을 흘려보내는 제너레이터를 돌려준다.

    context: {"stadium"?, "intent"?, "origin"?} 선택 사항 (v2 만 쓴다).
    version: "v1"/"v2". 없으면 env(LLM_CHAIN_VERSION). 그 밖의 값은 ValueError (저장 전).
    HTTP/SSE 로 감싸는 일은 views 의 책임이다.
    """
    version = resolve_version(version)
    thread = ChatThread(session.id)
    return _start(thread, thread.ask(content), version, context)


def list_messages(request, session_id):
    """소유한 세션의 최신 state → 공개 대화 항목 목록 (옛 wire 필드 포함)."""
    session = get_owned_session(request, session_id)
    thread = ChatThread(session.id)
    return wire_history(*thread.state(), thread.wire)


def message_update(request, session_id, message_id, content, context=None, version=None):
    """해당 사용자 메시지 뒤를 지우고 같은 ID 로 질문을 바꾼 뒤 다시 답한다."""
    version = resolve_version(version)  # 절단 전에 검증: 잘못된 버전이면 아무것도 바꾸지 않는다
    session = get_owned_session(request, session_id)
    thread = ChatThread(session.id)
    return _start(thread, thread.edit(message_id, content), version, context)


def message_delete(request, session_id, message_id):
    """해당 사용자 메시지부터 이후 메시지를 최신 대화에서 제거한다. 제거한 메시지 수를 돌려준다."""
    session = get_owned_session(request, session_id)
    return ChatThread(session.id).delete_from(message_id)
