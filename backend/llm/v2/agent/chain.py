"""V2 그래프 = 메인 Agent 하나. JEV 는 메인 Agent 의 JevGuidelineMiddleware(run_jev=True)가 invocation 당 한 번 부르고,
하위 Agent 는 ask_* 도구로 노출돼 다른 도구처럼 capability 로 노출·차단된다. checkpointer 없음."""
from functools import cache

from ..middleware.dynamic_tools import CAPABILITY_TOOLS
from . import sub_agents
from .common import MAIN_MODEL_CALL_BUDGET, build_agent

MAIN_RULES = """역할: KBO 야구 직관 안내 메인 에이전트.
허용된 도구만 필요한 만큼 호출해 답한다. 구장 목록은 get_stadiums, 구장 ID가 필요한 도구는 get_stadium 으로 먼저 확인한다.
구장 주변 식당·카페는 search_places(method=category, category=FD6/CE7)로 찾는다.
질문의 구장이 모호하면 어느 구장인지 되묻는다. 인사·감사·잡담에는 도구 없이 짧게 답한다.
고정 도구로 안 되는 집계만 get_baseball_schema → execute_baseball_select 순서로 조회한다.
경기 전후 코스·하루 일정 조율은 하위 에이전트에게 하위 작업을 구체적으로 맡긴다.
- ask_baseball: 경기 시각·구장·구장 안 정보
- ask_travel_research: 조건에 맞는 맛집·카페·관광·실내활동 후보
- ask_place_data: 기존 공개 코스, 특정 장소 확인
구장이 정해지지 않았으면 ask_baseball 결과로 구장을 확인한 뒤 장소를 조사한다. 부족한 정보가 있으면 필요한 하위
에이전트만 다시 부른다. 후보 사이 이동 시간은 get_directions 로 확인한다.
새 직관 코스를 통째로 짜 달라는 요청은 plan_course 에 사용자 요청을 그대로 넘긴다(기존 공개 코스 검색은 ask_place_data).
get_directions 가 실패하면 한 번까지만 다시 부르고, 그래도 실패하면 이동 시간을 미확인으로 밝히고 그대로 답한다.
받은 결과만으로 시간 순서의 계획을 만들고 경기 시작 전에 구장에 도착하게 짠다. 확인 안 된 시각·영업시간은 단정하지
않고, 조회 실패나 결과 충돌은 그대로 밝힌다."""

TOOLS = tuple(dict.fromkeys(n for names in CAPABILITY_TOOLS.values() for n in names if n not in sub_agents.SPECIALISTS))


def build_graph(model, tools_by_name):
    tools = [*(tools_by_name[n] for n in TOOLS), *sub_agents.build(model, tools_by_name)]
    return build_agent(model, tools, MAIN_RULES, CAPABILITY_TOOLS, budget=MAIN_MODEL_CALL_BUDGET, run_jev=True)


V2_PLAN_COURSE_DESCRIPTION = "직관 코스(경기 전 → 구장 → 경기 후, 요청 시 숙소)를 짠다. 결과 텍스트로 사용자에게 코스를 직접 설명한다."


@cache
def get_graph():
    from llm.tools import create_default_tools
    from llm.tools.knowledge import create_knowledge_tools
    from .common import llm
    from llm.tools.assistant import build_specialized_tools
    tools_by_name = {t.name: t for t in (*create_default_tools(), *create_knowledge_tools())}
    for t in build_specialized_tools():  # 이름이 겹치면 기존 default/knowledge 구현·스키마 유지
        if t.name == "plan_course":  # V2엔 지도/코스 카드 소비자가 없으므로 사본 설명만 교체(V1 전역 정의 불변)
            t = t.model_copy(update={"description": V2_PLAN_COURSE_DESCRIPTION})
        tools_by_name.setdefault(t.name, t)
    return build_graph(llm(), tools_by_name)
