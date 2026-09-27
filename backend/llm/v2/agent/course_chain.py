"""course 체인: 경기 전후 코스 / 하루 일정 / 이동 동선 / 장소 조합."""
from .common import build_domain_chain

RULES = """경기 전후 코스 담당이다. 경기 시각(get_games)과 날씨(get_weather)를 먼저 확인하고,
공개 코스(search_courses, get_course)가 있으면 우선 활용한다. 새로 짤 때는 search_places·search_tourism으로 장소를 고르고
get_directions로 이동 시간을 확인해 순서대로 안내한다. 경기 시작 전에 구장에 도착하도록 짠다."""

CATEGORIES = ("FOOD_OUT", "CAFE", "SPOT", "TRANSPORT")

TOOLS = (
    "get_games", "get_weather", "get_stadium", "search_courses", "get_course",
    "search_places", "search_tourism", "get_directions",
)

course_chain = build_domain_chain(RULES, CATEGORIES, TOOLS)
