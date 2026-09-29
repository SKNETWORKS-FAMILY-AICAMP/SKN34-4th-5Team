"""공용 state + 전문/단일 Agent 공통 조립: create_agent + JEV 가이드라인/동적 도구 미들웨어."""
import os
from functools import cache
from typing import NotRequired, TypedDict

from langchain.agents.middleware import AgentState
from langchain_core.messages import AIMessage
from langgraph.graph import MessagesState

from ..middleware.dynamic_tools import DynamicToolMiddleware
from ..middleware.jev_guidelines import JevGuidelineMiddleware


# 상위 그래프와 create_agent 공용 state. 인증/세션/저장 필드는 두지 않는다.
class Decision(TypedDict):
    allowed: bool  # JEV guard PASS 여부
    complexity: str  # "SIMPLE" | "COMPLEX"
    capabilities: list[str]  # Simple 도구 노출용 capability 이름


class ChainState(MessagesState):
    decision: NotRequired[Decision]
    context: NotRequired[dict | None]  # 선택: {"stadium", "intent", "origin": {"lat", "lng"}}


class V2AgentState(AgentState):
    decision: NotRequired[Decision]
    context: NotRequired[dict | None]


RECURSION_LIMIT = int(os.getenv("AGENT_RECURSION_LIMIT", "12"))


@cache
def llm():
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=os.getenv("LLM_MODEL") or "gpt-5.6-luna", temperature=0, timeout=25,
        max_retries=0, reasoning_effort="medium", use_responses_api=True,
    )


def build_agent(model, tools, rules, capability_tools=None):
    """create_agent 를 JEV 가이드라인 + 동적 도구 노출 미들웨어와 함께 조립한다.

    capability_tools 가 None 이면 role_tools(주어진 tools) 그대로 노출한다(구성 시점에 고정된
    전문 Agent). capability_tools 를 주면 decision.capabilities 와의 교집합만 노출한다(Simple)."""
    from langchain.agents import create_agent
    return create_agent(
        model=model, tools=tools, state_schema=V2AgentState,
        middleware=[
            JevGuidelineMiddleware(rules),
            DynamicToolMiddleware([t.name for t in tools], capability_tools),
        ],
    )


def invoke_agent(agent, state):
    return agent.invoke(state, {"recursion_limit": RECURSION_LIMIT})


def final_text(result) -> str:
    """에이전트 결과에서 도구 호출이 없는 마지막 AI 답변만 꺼낸다."""
    for msg in reversed(result.get("messages") or []):
        if isinstance(msg, AIMessage) and not msg.tool_calls:
            return msg.content
    return ""
