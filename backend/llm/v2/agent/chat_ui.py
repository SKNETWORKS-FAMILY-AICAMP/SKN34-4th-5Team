"""Explicit public UI tool; never render model HTML or navigate to a model URL."""
import re

from langchain_core.tools import tool
from pydantic import BaseModel, Field


class PlanningQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=160)
    choices: list[str] = Field(min_length=2, max_length=4)


def public_ui(value):
    if not isinstance(value, dict) or not isinstance(value.get("offer_writer"), bool):
        return None
    questions = value.get("questions")
    if not isinstance(questions, list) or len(questions) > 4:
        return None
    cleaned = []
    for item in questions:
        if not isinstance(item, dict):
            return None
        question, choices = item.get("question"), item.get("choices")
        if not isinstance(question, str) or not question.strip() or len(question) > 160:
            return None
        if not isinstance(choices, list) or not 2 <= len(choices) <= 4:
            return None
        if any(not isinstance(c, str) or not c.strip() or len(c) > 80 for c in choices) or len(set(choices)) != len(choices):
            return None
        cleaned.append({"question": question, "choices": list(choices)})
    return {"offer_writer": value["offer_writer"], "questions": cleaned}


@tool(response_format="content_and_artifact")
def present_planning_questions(offer_writer: bool, questions: list[PlanningQuestion]):
    """Ask 1-4 essential questions only when the stadium is unknown or the user requests choices. With a known stadium, do not require a date, game or time: call plan_course first. Never ask known information."""
    payload = public_ui({"offer_writer": offer_writer, "questions": [q.model_dump() for q in questions]})
    if payload is None:
        raise ValueError("Invalid public planning questions")
    return "계획 조건을 선택하거나 채팅으로 답할 수 있어요.", payload


VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
MAX_VIDEOS = 5


def public_videos(value):
    """select_highlight_scenes artifact → 화면 영상 카드. 서버가 DB 에서 만든 videoId·시작 시각만 형식 검사해 통과시킨다."""
    if not isinstance(value, dict) or not isinstance(value.get("videos"), list):
        return []
    videos = []
    for raw in value["videos"][:MAX_VIDEOS]:
        if not isinstance(raw, dict):
            continue
        video_id, start = raw.get("videoId"), raw.get("start")
        if not isinstance(video_id, str) or not VIDEO_ID.match(video_id):
            continue
        if isinstance(start, bool) or not isinstance(start, int) or not 0 <= start <= 6 * 3600:
            continue
        label = raw.get("label") if isinstance(raw.get("label"), str) else ""
        game_date = raw.get("gameDate") if isinstance(raw.get("gameDate"), str) else None
        videos.append({"videoId": video_id, "start": start, "label": label.strip()[:120],
                       **({"gameDate": game_date[:10]} if game_date else {})})
    return videos


def highlight_videos(messages):
    """한 턴의 메시지(메인 + ask_highlight 하위 대화)에서 마지막으로 선택된 하이라이트 영상 카드를 꺼낸다."""
    from langchain_core.messages import ToolMessage
    from llm.serializer.message import _artifact_messages
    found = []
    for message in messages:
        if not isinstance(message, ToolMessage) or message.status == "error":
            continue
        if message.name == "select_highlight_scenes":
            found = public_videos(message.artifact) or found
        elif message.name == "ask_highlight":
            found = highlight_videos(_artifact_messages(message.artifact)) or found
    return found

