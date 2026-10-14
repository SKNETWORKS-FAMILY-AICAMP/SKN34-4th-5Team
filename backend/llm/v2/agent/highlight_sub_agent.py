"""하이라이트 전문 Agent: 장면 RAG(Qdrant 의미 검색) + 장면표(DB 조건 검색)를 맞춰 보고 질문과 맞는 장면만 고른다.

고른 장면의 영상 카드(videoId·시작 시각)는 select_highlight_scenes 결과로 서버가 만든다. 모델은 URL 을 쓰지 않는다."""
from .common import build_agent

HIGHLIGHT_RULES = """역할: KBO 하이라이트 장면 전문 에이전트. 받은 작업에 맞는 장면을 찾아 검증하고 고른다.
1. 선수·팀·상대 팀·날짜·결과(홈런·안타·호수비 등)를 말했으면 search_highlight_scenes 로 조건 검색한다.
2. 장면을 말로 묘사했거나(역전, 끝내기, 다이빙 캐치, 3점 홈런 등) 조건 검색이 비거나 애매하면 search_highlight_rag 로 의미 검색한다.
   두 도구는 한 번에 함께 불러도 된다. 같은 도구를 같은 조건으로 다시 부르지 않는다.
   '최근·요즘·지난 경기·마지막'처럼 날짜 없이 최근을 물으면 search_highlight_scenes 는 최신 경기부터 돌려주고,
   search_highlight_rag 는 recent_first=true 로 부른다. 고를 때도 game_date 가 가장 최근인 장면을 먼저 고른다.
3. 검증: 후보마다 날짜·대진(matchup)·타자·결과(event)·타점(rbi)·이닝이 작업과 맞는지 본다. 두 도구에 같은 scene_id 가 있으면 더 믿을 만하다.
   작업에 선수 이름이 있으면 batter 나 description 에 그 이름이 있는 장면만 남긴다. 결과를 말했으면 event 가 같은 장면만 남긴다.
   '3점 홈런'처럼 타점을 말했으면 rbi 가 같은 장면을 우선한다. 날짜를 말했는데 그날 장면이 없으면 relaxed·requested_date 를 보고
   가장 가까운 경기를 고르고 날짜가 다르다고 적는다.
4. 맞는 장면이 있으면 select_highlight_scenes(scene_ids=[...], note=불확실한 점)를 한 번만 부른다. 검색 결과에 있던 scene_id 만 쓴다.
   날짜를 특정하지 않은 질문은 가장 최근 장면 하나, 여러 개·전부를 원하면 최대 5개를 고른다.
5. 맞는 장면이 없으면 select_highlight_scenes 를 부르지 않고, 아직 분석된 영상에 없다고 analyzed_videos·latest_game_date 와 함께 돌려준다.
최종 답은 사실만 짧게: 고른 장면마다 날짜·대진·이닝·타자·결과(타점)·한 줄 설명, pitcher 가 있으면 상대 투수도 적는다.
불확실한 점은 해당될 때만: 날짜를 바로잡았을 때, 작업이 투수를 물었는데 pitcher 가 비어 있을 때(투수를 묻지 않았으면 투수 얘기를 꺼내지 않는다).
URL·영상 ID·재생 시각은 만들거나 적지 않는다(영상 카드는 서버가 붙인다)."""

TOOLS = ("search_highlight_scenes", "search_highlight_rag", "select_highlight_scenes")


STORED_RETRY_RULES = """검증 오류 후 저장된 멀티모달 DB에서 한 번만 재조회한다.
search_stored_highlight_rag를 정확히 한 번 호출하고 원래 질문을 보존한 짧은 검색어만 바꾼다.
웹 검색·새 URL·영상 ID·파일 경로·재귀 위임은 금지한다. 저장 결과는 원래 질문과 이미지로 다시 검증되며 검증되지 않은 카드는 반환하지 않는다."""


def MultimodalAgent(retry_model=None):
    """Retrieve → actual Gemini model → deterministic validation/card projection."""
    import fcntl
    import math
    from pathlib import Path
    from typing import Annotated, TypedDict
    from django.conf import settings
    from langchain_core.messages import AnyMessage, AIMessage, HumanMessage, SystemMessage, ToolMessage
    from langchain_core.runnables import RunnableConfig
    from langchain_core.runnables.config import merge_configs
    from langchain_core.tools import tool
    from llm.service.chat_runs import check_cancelled
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import add_messages
    from llm.highlight_multimodal import Google, Specialist, VerificationAdmissionError, image_parts, verify_retrieval

    class State(TypedDict, total=False):
        messages: Annotated[list[AnyMessage], add_messages]
        highlight_images: list
        highlight_original_text: str
        retrieval: dict
        result: dict
        retry_allowed: bool

    def retrieve(query, images):
        check_cancelled()
        root = Path(settings.HIGHLIGHT_MULTIMODAL_CORPUS)
        # ponytail: serialize local Qdrant retrieval; use a server if throughput matters.
        with (root / "web.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            check_cancelled()
            specialist = Specialist(root, Google())
            try:
                return specialist.retrieve(query, candidate_limit=3, images=images)
            finally:
                specialist.close()

    def tools(inputs: State):
        images = inputs.get("highlight_images") or []
        query = inputs.get("highlight_original_text") or inputs["messages"][-1].text
        try:
            image_parts(images, embedding=False)
            return {"retrieval": retrieve(query, images)}
        except VerificationAdmissionError:
            raise
        except (OSError, RuntimeError, ValueError):
            reason = "하이라이트 이미지 검색은 최대 6개까지 지원합니다" if len(images) > 6 else "highlight retrieval unavailable"
            return {"result": {"complete": False, "no_match": None, "matches": [],
                    "decisions": [{"provider_error": reason}]}}

    def model(state: State, config: RunnableConfig):
        if "result" in state:
            return {}
        # No corpus client/lock survives into countTokens, quota or Gemini network calls.
        result = verify_retrieval(Google(), state["retrieval"],
                images=state.get("highlight_images") or [], config=config)
        return {"result": result, "retry_allowed": not result["complete"]}

    def retry(state: State, config: RunnableConfig):
        if not state.get("retry_allowed"):
            return {}
        check_cancelled()
        first = state["result"]
        first_failure = [{k: d[k] for k in ("provider_error", "rejected") if k in d}
                         for d in first["decisions"]]
        executed = 0
        reason = "stored retry not selected"
        result = first
        images = state.get("highlight_images") or []

        @tool
        def search_stored_highlight_rag(query: str):
            """Search only the existing stored multimodal corpus with a revised query; max three unique originals."""
            nonlocal executed
            check_cancelled()
            if executed:
                raise ValueError("Stored retry exhausted")
            executed += 1
            return retrieve(query, images)

        try:
            if retry_model is not None:
                content = [{"type": "text", "text": state["retrieval"]["query"]}, *images]
                response = retry_model.bind_tools([search_stored_highlight_rag]).invoke(
                    [SystemMessage(STORED_RETRY_RULES), HumanMessage(content)],
                    config=merge_configs(config, {"tags": ["nostream"]}))
                check_cancelled()
                calls = response.tool_calls
                if response.invalid_tool_calls or len(calls) != 1 or calls[0]["name"] != search_stored_highlight_rag.name:
                    raise ValueError("Expected one stored retry call")
                args = calls[0]["args"]
                if (set(args) != {"query"} or not isinstance(args["query"], str)
                        or not 0 < len(args["query"].strip()) <= 500):
                    raise ValueError("Invalid stored retry arguments")
                retrieval = search_stored_highlight_rag.invoke(args, config=config)
                check_cancelled()
                # Revised query is retrieval-only; Gemini consumes stored tool results with authoritative inputs.
                retrieval = {**retrieval, "query": state["retrieval"]["query"]}
                result = verify_retrieval(Google(), retrieval, images=images, config=config)
                reason = "verified stored retry absence" if result["complete"] else "stored retry verification incomplete"
        except Exception as exc:
            from llm.service.chat_runs import Stopped
            from llm.service.usage import InsufficientCredits, WalletBusy, UnsafeOutputLimit
            if isinstance(exc, (Stopped, VerificationAdmissionError, InsufficientCredits, WalletBusy, UnsafeOutputLimit)):
                raise
            if getattr(exc, "status_code", None) in (401, 402, 403):
                raise VerificationAdmissionError("Retry provider authorization/billing denied") from None
            reason = "stored retry unavailable or invalid"
            result = {**first, "complete": False, "no_match": None, "matches": []}
        return {"result": {**result, "status": "success" if result["complete"] and result["matches"] else "block",
                "first_failure": first_failure, "retry_executed": executed,
                "block_reason": reason if not (result["complete"] and result["matches"]) else None}}

    def validate(state: State):
        from llm.service.chat_runs import check_cancelled
        check_cancelled()
        result = state["result"]
        result.setdefault("status", "success" if result["complete"] else "block")
        result.setdefault("retry_executed", 0)
        failures = [d.get("provider_error") or d.get("rejected") for d in result["decisions"]
                    if d.get("provider_error") or d.get("rejected")]
        if not result["complete"]:
            text = "[조회 실패] 멀티모달 검증 미완료: " + "; ".join(failures) + ". 장면 없음으로 판단할 수 없습니다."
            selection = ToolMessage(text, name="select_highlight_scenes", tool_call_id="multimodal-selection",
                        status="error", additional_kwargs={"highlight_status": "block"},
                        artifact={"videos": [], "status": "block", "multimodal": result})
        else:
            videos = [{"videoId": m["video_id"], "start": math.floor(m["playback_seconds"]),
                       "label": m["evidence"][:120]} for m in result["matches"]]
            text = "\n".join(m["evidence"] for m in result["matches"]) or "샘플링된 멀티모달 후보에서 요청 장면을 확인하지 못했습니다(전체 경기 검색 아님)."
            selection = ToolMessage(text, name="select_highlight_scenes", tool_call_id="multimodal-selection",
                        status="error" if result["status"] == "block" else "success",
                        additional_kwargs={"highlight_status": result["status"]},
                        artifact={"videos": videos, "status": result["status"], "multimodal": result})
        return {"messages": [selection, AIMessage(text)]}

    graph = StateGraph(State)
    graph.add_node("tools", tools)
    graph.add_node("model", model)
    graph.add_node("retry", retry)
    graph.add_node("validate", validate)
    graph.add_edge(START, "tools")
    graph.add_edge("tools", "model")
    graph.add_edge("model", "retry")
    graph.add_edge("retry", "validate")
    graph.add_edge("validate", END)
    return graph.compile()


def build(model, tools_by_name):
    from django.conf import settings
    if getattr(settings, "HIGHLIGHT_MULTIMODAL_CORPUS", ""):
        return MultimodalAgent(model)
    return build_agent(model, [tools_by_name[n] for n in TOOLS], HIGHLIGHT_RULES)
