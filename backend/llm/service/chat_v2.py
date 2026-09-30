"""V2 채팅 턴: top-level 그래프(checkpointer 없음)의 공개 답변 청크·도구 진행을 SSE 로 흘리고 대화 state 에 저장한다."""
import logging
from contextlib import closing

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from llm.enum import ChatRole, PublicChatEvent, PublicToolStatus, TurnStatus
from llm.serializer.message import _public_tool, project_history
from llm.service import chat_runs
from llm.service.chat_thread import ChatThread

log = logging.getLogger(__name__)


def _model_history(messages, turns):
    """다음 모델 입력용 projection: 완료된 턴의 질문/최종 답변만."""
    return [
        HumanMessage(item["content"]) if item["role"] == ChatRole.USER else AIMessage(item["content"])
        for item in project_history(messages, turns) if item["status"] == TurnStatus.COMPLETED
    ]


_ANSWER_NODES = ("simple_agent", "orchestrator")


def _frames(graph_input, run):
    """top-level 그래프를 checkpointer 없이 돌려 공개 (event, data) 만 흘린다. 대화 state 쓰기는 finish 만 한다.

    - delta: 답변 Agent(simple_agent/orchestrator) 자신의 model 노드 텍스트 청크만 바로 흘린다. 분류기(jev_router)와
      ask_* 안쪽 전문 Agent 는 namespace 가 달라 제외된다. tool_call 청크가 나온 model 호출은 그 뒤 텍스트를 흘리지 않는다.
    - 저장 답변 run["answer"] = top-level 최종 update 의 도구 호출 없는 AI 답(마지막 model 호출)만. 도구 없는 호출이
      한 청크도 안 흘렸으면(JEV 거절·비스트리밍 답) 그 답을 한 번에 흘린다.
    - tool: 답변 Agent 의 실제 도구 호출 running → ToolMessage 로 completed/failed. 인자·결과는 싣지 않는다.
      호출/결과 원본은 run["messages"] 에 모아 V1 처럼 턴에 저장한다(project_history 의 tools).
    ponytail: 한 model 호출에서 도구 호출보다 먼저 온 머리말 텍스트는 이미 delta 로 나가 화면엔 남는다(저장·done 에는
    안 들어감). 새 이벤트 없이 지울 방법이 없어서다. 문제되면 호출 단위로 버퍼링하거나 reset 이벤트를 둔다.
    """
    from llm.v2.agent.chain import get_graph
    stream = get_graph().stream(graph_input, stream_mode=["messages", "updates"], subgraphs=True)
    texts, tool_calls = {}, set()  # model 호출(chunk.id)별 흘린 텍스트 / tool_call 청크가 나온 호출
    with closing(stream):
        for ns, mode, data in stream:
            agent = len(ns) == 1 and ns[0].split(":")[0] in _ANSWER_NODES
            if mode == "messages":
                chunk, meta = data
                if not (agent and meta.get("langgraph_node") == "model" and isinstance(chunk, AIMessageChunk)):
                    continue
                if chunk.tool_call_chunks:
                    tool_calls.add(chunk.id)
                elif chunk.text and chunk.id not in tool_calls:
                    texts[chunk.id] = texts.get(chunk.id, "") + chunk.text
                    yield PublicChatEvent.DELTA.value, {"text": chunk.text}
                continue
            for update in data.values():
                for message in (update or {}).get("messages") or [] if isinstance(update, dict) else ():
                    if not ns and isinstance(message, AIMessage) and not message.tool_calls:
                        run["answer"] = str(message.text)
                        if not any(text for key, text in texts.items() if key not in tool_calls):
                            yield PublicChatEvent.DELTA.value, {"text": run["answer"]}  # 청크 없이 끝난 답: 한 번에
                    elif agent and isinstance(message, AIMessage) and message.tool_calls:
                        run["messages"].append(message)
                        for call in message.tool_calls:
                            yield PublicChatEvent.TOOL.value, _public_tool(call["id"], call["name"], PublicToolStatus.RUNNING.value)
                    elif agent and isinstance(message, ToolMessage):
                        run["messages"].append(message)
                        status = PublicToolStatus.FAILED if message.status == "error" else PublicToolStatus.COMPLETED
                        yield PublicChatEvent.TOOL.value, _public_tool(message.tool_call_id, message.name, status.value)


def _stream_turn(thread, prefix, turns, human, context):
    """프레임: tool*/delta* → done | stopped | error (chat_runs.stream_turn). 저장 답변 = 마지막 도구 없는 model 호출의 답."""
    run = {"answer": "", "messages": []}
    graph_input = {"messages": [*_model_history(prefix, turns), human]}
    if context:
        graph_input["context"] = context
    return chat_runs.stream_turn(thread, prefix, turns, human, lambda: _frames(graph_input, run), run, label="v2")


def _start(thread, turn, context):
    frames = _stream_turn(thread, *turn, context)
    next(frames)  # priming
    return frames


def send_message(session, content, context=None):
    """V2 사용자 메시지를 저장하고 (event, data) 튜플을 흘려보내는 제너레이터를 돌려준다."""
    thread = ChatThread(session.id)
    return _start(thread, thread.ask(content), context)


def message_update(session, message_id, content, context=None):
    """V2: 해당 사용자 메시지 뒤를 지우고 같은 ID 로 질문을 바꾼 뒤 다시 답한다."""
    thread = ChatThread(session.id)
    return _start(thread, thread.edit(message_id, content), context)
