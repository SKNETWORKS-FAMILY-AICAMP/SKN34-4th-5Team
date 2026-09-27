"""travel 체인: 구장 주변 맛집 / 카페 / 숙박 / 관광 / 산책 / 공원 / 실내 놀거리 / 편의점."""
from .common import build_domain_chain
from llm.v1.rag.venue.prompts import CONTENT_RULES  # v1 내용 규칙 재사용

RULES = """구장 주변 장소 담당이다. 좌표는 get_stadium으로 확인한 뒤 search_places·search_tourism으로 찾고,
<context>와 search_documents_tool 결과도 근거로 쓴다. 도구 결과에 없는 장소·영업시간은 만들지 않는다.

""" + CONTENT_RULES

CATEGORIES = ("FOOD_OUT", "CAFE", "SPOT")

TOOLS = (
    "get_stadium", "search_places", "search_tourism", "get_directions", "search_documents_tool",
)

travel_chain = build_domain_chain(RULES, CATEGORIES, TOOLS)
