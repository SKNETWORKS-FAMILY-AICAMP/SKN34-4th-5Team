"""stadium 체인: 구장 정보 / 티켓 / 가격 / 좌석 / 반입 / 재입장 / 주차 / 시설 / 구장 내 먹거리."""
from .common import build_domain_chain
from llm.v1.rag.club.prompts import CONTENT_RULES  # v1 내용 규칙 재사용

RULES = """구장 안 정보 담당이다. 가격·좌석·예매정책·교통·시설·매점은 구장 도구 결과를, 반입·재입장·운영 규정은 <context>와
search_kbo_documents 결과만 근거로 답한다. 숫자·가격·시각은 그대로 쓰고 다른 구장 정보는 섞지 않는다.
반입물품은 KBO 전 구장 공통 규정이 기본값이고, 구단 예외가 있으면 그것을 우선한다.

""" + CONTENT_RULES

CATEGORIES = (
    "STADIUM", "PRICE", "SEAT", "TICKET_POLICY", "CARRY_IN", "REENTRY",
    "TRANSPORT", "FACILITY", "CONTENT", "FOOD_IN", "OPERATION",
)

TOOLS = (
    "get_stadium", "get_seat_zones", "get_seat_views", "get_seat_maps",
    "get_ticket_prices", "get_ticket_policies", "get_transport", "get_food_stores",
    "get_facilities", "get_stadium_contents", "search_kbo_documents",
)

stadium_chain = build_domain_chain(RULES, CATEGORIES, TOOLS)
