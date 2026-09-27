"""baseball 체인: 경기 일정 / 결과 / 순위 / 선수 / 야구 규칙."""
from .common import build_domain_chain
from llm.v1.rag.club.prompts import CONTENT_RULES  # v1 내용 규칙 재사용

RULES = """야구 정보 담당이다. 일정·결과·순위·선수는 도구(get_games, get_standings, search_players) 결과를 정본으로 쓰고,
규칙은 <context>와 search_kbo_documents 결과만 근거로 답한다.
고정 도구로 안 되는 집계만 get_baseball_schema → execute_baseball_select 순서로 조회한다.
순위·일정은 기준일을 함께 밝히고, 순위 전망·승부 예측은 단정하지 않는다.

""" + CONTENT_RULES

CATEGORIES = ("RULE",)

TOOLS = (
    "get_games", "get_standings", "search_players", "get_weather",
    "get_baseball_schema", "execute_baseball_select", "search_kbo_documents",
)

baseball_chain = build_domain_chain(RULES, CATEGORIES, TOOLS)
