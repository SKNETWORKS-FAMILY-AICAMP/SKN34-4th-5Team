"""기존 코스 LLM/검증기를 V2 실행·checkpoint에 연결한다. 범용 Agent는 코스를 재작성하지 않는다."""
from copy import deepcopy

from langchain_core.messages import AIMessage, HumanMessage

from .state import STATE_VERSION


def course_snapshot(value):
    return deepcopy(value) if isinstance(value, dict) and value.get("version") == STATE_VERSION else None


def previous_course_state(messages, turns):
    """완료된 질문 순서로 선택한다. 늦게 끝난 옛 요청이나 삭제한 턴은 기억을 덮지 못한다."""
    answer_ids = {m.id for m in messages if isinstance(m, AIMessage)}
    for message in reversed(messages):
        if not isinstance(message, HumanMessage):
            continue
        turn = turns.get(message.id) or {}
        if turn.get("status") == "completed" and turn.get("answer_id") in answer_ids:
            snapshot = course_snapshot(turn.get("course_state"))
            if snapshot is not None:
                return snapshot
    return None


def routing_context(context, runtime):
    context = dict(context or {})
    context.pop("course_pending", None)
    previous = course_snapshot((runtime or {}).get("state")) or {}
    if previous.get("pending") or previous.get("selected_game"):
        context["course_pending"] = True
    return context or None


def run_course(question, history, context, runtime):
    """기존 코스 chain을 그대로 실행하고, 검증된 공개 문장만 custom 스트림에 싣는다."""
    from langgraph.config import get_stream_writer
    from llm.v2.agent.course_chain import course_chain

    runtime = deepcopy(runtime or {"state": None, "profile_team": None})
    writer = get_stream_writer()
    chunks = []
    for chunk in course_chain.stream({"question": question, "chat_history": history,
                                      "context": context, "course_runtime": runtime}):
        chunks.append(chunk)
        writer({"course_delta": chunk})
    from uuid import uuid4
    from llm.v2.agent.chat_ui import present_planning_questions, public_ui

    state = course_snapshot(runtime.get("next_state"))
    previous = course_snapshot(runtime.get("state"))
    questions = []
    if state and state.get("pending") == "choice":
        choices = [f"{i}안: {game['date']} {game['time'][:5]} {game['away_name']} vs {game['home_name']}"
                   for i, game in enumerate(state.get("candidates", []), 1)]
        if len(choices) == 1:
            choices.append("조건을 바꿀게요")
        questions = [{"question": "어느 경기 기준으로 코스를 짤까요?", "choices": choices}]
    elif state and state.get("pending") in ("changed", "expired"):
        choices = (["기존 조건을 유지할게요", "바뀐 일정에 맞게 조건을 수정할게요"]
                   if state["pending"] == "changed" else ["이전 코스를 수정할게요", "같은 조건의 다음 경기로 짜 주세요"])
        questions = [{"question": state["pending_question"], "choices": choices}]
    payload = (public_ui({"offer_writer": previous is None, "questions": questions})
               or {"offer_writer": previous is None, "questions": []})
    messages = []
    if payload and (payload["offer_writer"] or payload["questions"]):
        messages.append(present_planning_questions.invoke({"type": "tool_call", "id": str(uuid4()),
            "name": present_planning_questions.name, "args": payload}))
    return {"messages": [*messages, AIMessage("".join(chunks))],
            "course_state": state, "jump_to": "end"}
