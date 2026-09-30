"""공용 state + 전문/단일 Agent 공통 조립: create_agent + JEV 가이드라인/동적 도구 미들웨어."""
import os
from functools import cache
from typing import NotRequired, TypedDict

from langchain.agents.middleware import AgentMiddleware, AgentState, ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.messages import AIMessage, HumanMessage
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


def _budget(name, default):
    """invocation 당 model 호출 예산(포함, 1 이상 정수). 잘못된 값은 조용히 보정하지 않고 시작 시 실패한다."""
    raw = os.getenv(name, str(default))
    if not raw.strip().isdigit() or int(raw) < 1:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}")
    return int(raw)


# *_RECURSION_LIMIT 는 V1 의 LangGraph step 한도다. V2 는 읽지도 호출 수로 재해석하지도 않는다.

# 기본값은 예전 step 한도(12/25)가 허용하던 호출 수와 같다. Orchestrator 는 전문 Agent 3개 + 이동 시간 조회로 더 넉넉하게.
MODEL_CALL_BUDGET = _budget("AGENT_MODEL_CALL_BUDGET", 4)
ORCHESTRATOR_MODEL_CALL_BUDGET = _budget("ORCHESTRATOR_MODEL_CALL_BUDGET", 8)


@cache
def llm():
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=os.getenv("LLM_MODEL") or "gpt-5.6-luna", temperature=0, timeout=25,
        max_retries=0, reasoning_effort="medium", use_responses_api=True,
    )


class FinalAnswerMiddleware(AgentMiddleware):
    """invocation 당 N 번째(마지막) model 호출은 도구 없이 해서 모은 결과로 답하고 끝나게 한다.

    호출 수는 ModelCallLimitMiddleware 의 run_model_call_count(invocation 마다 0, 이전 대화 무관)를 쓴다.
    도구를 빼도 tool_calls 를 내는 모델이면 그 호출을 버려 N+1 번째 호출이 생기지 않게 한다."""

    def __init__(self, budget):
        super().__init__()
        self.budget = budget

    def wrap_model_call(self, request, handler):
        if request.state.get("run_model_call_count", 0) < self.budget - 1:
            return handler(request)
        response = handler(request.override(tools=[]))
        response.result = [AIMessage(m.content) if isinstance(m, AIMessage) and m.tool_calls else m for m in response.result]
        return response


def build_agent(model, tools, rules, capability_tools=None, budget=MODEL_CALL_BUDGET):
    """create_agent 를 JEV 가이드라인 + 동적 도구 노출 + 종료 보장 미들웨어와 함께 조립한다.

    capability_tools 가 None 이면 role_tools(주어진 tools) 그대로 노출한다(구성 시점에 고정된
    전문 Agent). capability_tools 를 주면 decision.capabilities 와의 교집합만 노출한다(Simple).
    budget: invocation 당 model 호출 수(포함). 1..N-1 은 도구 사용 가능, N 은 도구 없이 답, N+1 은 provider 호출 없이 오류.
    get_directions 는 요청당 2회까지만 실행하고(외부 429 반복 방지), 넘으면 오류 ToolMessage 로 모델이 다음으로 간다."""
    from langchain.agents import create_agent
    agent = create_agent(
        model=model, tools=tools, state_schema=V2AgentState,
        middleware=[
            JevGuidelineMiddleware(rules),
            DynamicToolMiddleware([t.name for t in tools], capability_tools),
            ModelCallLimitMiddleware(run_limit=budget, exit_behavior="error"),
            FinalAnswerMiddleware(budget),
            ToolCallLimitMiddleware(tool_name="get_directions", run_limit=2),
        ],
    )
    # 비공개 백스톱: 호출당 step 은 10 미만이라 예산보다 먼저 걸리지 않는다. 공개 설정 아님.
    return agent.with_config(recursion_limit=10 * budget + 10)


def invoke_agent(agent, state):
    return agent.invoke(state)


def final_text(result) -> str:
    """에이전트 결과에서 도구 호출이 없는 마지막 AI 답변만 꺼낸다."""
    for msg in reversed(result.get("messages") or []):
        if isinstance(msg, AIMessage) and not msg.tool_calls:
            return msg.text
    return ""
