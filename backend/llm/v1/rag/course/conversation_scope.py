"""보이는 대화는 보존하고 모델이 참고할 코스 대화의 시작점만 분리한다."""
from copy import deepcopy

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def scoped_messages(messages, turns):
    start, human_index, completed = 0, 0, False
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage):
            human_index = index
            turn = turns.get(message.id, {})
            completed = turn.get("status") == "completed" and not turn.get("answer_deleted")
        elif completed:
            reset = (isinstance(message, AIMessage) and message.response_metadata.get("course_history_reset"))
            if isinstance(message, ToolMessage) and message.name == "plan_course" and message.status != "error":
                reset = reset or (message.artifact or {}).get("course_history_reset")
            if reset:
                start = human_index
    return messages[start:]


def new_context(context):
    """이번 화면의 구장·출발지·선택 경로는 보존하되 기존 코스 자체는 새 생성에 넣지 않는다."""
    result = deepcopy(context or {})
    current = result.pop("currentCourse", None) or {}
    if "writerState" in current:
        result["origin"] = deepcopy(current["writerState"].get("origin"))
    return result
