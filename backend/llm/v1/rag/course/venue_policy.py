"""모든 코스 검색에 적용하는 구장 내부 장소 정책. 대화 취향/캐시와 독립적이다."""
import re
from contextlib import contextmanager
from contextvars import ContextVar

from travel.stadium_scope import classify_stadium_point, reviewed_zones

_PERMISSION = ContextVar("course_internal_venues", default=(None, frozenset()))
_INSIDE = re.compile(r"(?:야구장|경기장|구장)\s*(?:내부|안쪽|내(?:에서|에|의|\s|$)|안(?:에서|에|의|\s|$)|매점)|입장\s*후.*(?:매점|카페|식당|편의점)")
_NEGATIVE = re.compile(r"말고|제외|빼(?:줘|고|주세요)|싫|아니라|추천하지|원하지|안\s*(?:가|갈|먹|마시|들르)")
_CATEGORY = {"FOOD_OUT": "FOOD", "FOOD_IN": "FOOD", "FD6": "FOOD", "BAR": "FOOD", "CE7": "CAFE",
             "CS2": "CONVENIENCE", "AD5": "STAY", "AT4": "SPOT"}


@contextmanager
def request_policy(question, code, requests=()):
    allowed = set()
    for item in requests or ():
        expression = item.get("expression", "").strip()
        if not expression or expression not in question or not _INSIDE.search(expression):
            continue
        # 모델이 부정 표현 직전까지만 인용해 허용으로 뒤집지 못하게 한다.
        start = question.index(expression)
        clause = re.split(r"[.!?\n,]", question[start:])[0]
        if _NEGATIVE.search(clause):
            continue
        allowed.add(_CATEGORY.get(item.get("category"), item.get("category")))
    token = _PERMISSION.set((code, frozenset(allowed)))
    try:
        yield
    finally:
        _PERMISSION.reset(token)


def filter_candidates(places, category=None):
    """공급자 태그와 현재 구장 경계를 함께 검사한다. 오래된 RAG/캐시도 예외가 아니다.

    Kakao 원본과 정규화된 후보를 모두 받아 원본 형태를 보존한다. 실제 경기장
    목적지는 이 후보 목록과 별도로 삽입하므로 제외하지 않는다.
    """
    places = list(places)
    if not places:
        return []
    zones = reviewed_zones()  # 읽기 실패 시 차단을 건너뛰지 않고 오류를 전파한다.
    code, allowed = _PERMISSION.get()
    result = []
    for place in places:
        try:
            lat, lng = place.get("lat", place.get("y")), place.get("lng", place.get("x"))
            if isinstance(lat, bool) or isinstance(lng, bool):
                continue
            point = {"lat": float(lat), "lng": float(lng)}
            if not -90 <= point["lat"] <= 90 or not -180 <= point["lng"] <= 180:
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        area = classify_stadium_point(point, zones)
        tagged = place.get("stadiumArea") or {}
        if area["scope"] in ("unknown", "excluded_complex") or tagged.get("scope") == "excluded_complex":
            continue
        internal = (area["scope"] == "internal" or tagged.get("scope") == "internal"
                    or place.get("scope") == "internal" or place.get("category") == "FOOD_IN")
        kind = category or place.get("category") or place.get("category_group_code")
        if internal and not (code and code == (area.get("stadium") or tagged.get("stadium") or place.get("stadium"))
                             and _CATEGORY.get(kind, kind) in allowed):
            continue
        result.append(place)
    return result


def search_candidates(invoke, args, category):
    """첫 페이지가 내부 매장뿐이어도 다음 페이지의 외부 후보를 확인한다."""
    found = {}
    for page in range(1, 4):
        payload = invoke("course", "search_places", {**args, "page": page})
        if not isinstance(payload, dict):
            break
        for place in filter_candidates(payload.get("places", []), category):
            key = place.get("id") or (place.get("place_name"), place.get("y"), place.get("x"))
            found.setdefault(key, place)
        if len(found) >= 15 or not payload.get("hasNextPage"):
            break
    return list(found.values())
