"""Server-side visit roles, independent of the public map's broad categories."""
import re


def category(place):
    value = place.get('category')
    return 'FOOD' if value in {'FOOD_IN', 'FOOD_OUT'} else value


def visit_kind(place):
    from .timeline import BAR_WORDS
    detail = str(place.get('detail') or '')
    if (str(place.get('placeId') or '').startswith('stadium-facility:')
            or place.get('source') == 'MYSEATCHECK' or place.get('category') == 'FOOD_IN'
            or (place.get('stadiumArea') or {}).get('scope') == 'internal'
            or '구장 내부 먹거리' in detail):
        return 'INTERNAL_' + str(category(place))
    if place.get('category') == 'BAR' or any(word in detail for word in (*BAR_WORDS, '주점')):
        return 'BAR'
    return category(place)


def explicit_target(question, place, current):
    """A name/number/selected-place reference can intentionally cross roles."""
    references = [re.escape(str(place.get('name') or ''))]
    if str(place.get('label') or '').isdecimal():
        references.append(r'(?<!\d)' + re.escape(place['label']) + r'\s*번(?!\d)')
    if (current.get('selectedPlace') or {}).get('visitId') == place.get('visitId'):
        references.extend([r'선택한\s*(?:장소|매장|곳)', r'이\s*(?:곳|가게|매장)', r'여기'])
    for reference in references:
        if not reference:
            continue
        for match in re.finditer(reference, question):
            tail = question[match.end():]
            if not re.match(r'\s*(?:' + re.escape(str(place.get('name') or '')) + r'\s*)?(?:은|는|을|를)?\s*(?:그대로|유지|건드리지|바꾸지)', tail):
                return True
    return False


def protect_target(question, current, plan):
    """Do not let a model replace a snack/bar for a generic meal/cafe request.

    No menu guessing: source role is determined before candidate search. A sole
    eligible external visit is unambiguous; zero/multiple visits need clarification.
    Explicit internal/name/number references keep the existing edit mechanism.
    """
    if plan.get('operation') != 'replace' or len(plan.get('targets', [])) != 1:
        return plan
    target = next((p for p in current.get('places', []) if p['visitId'] in plan['targets']), None)
    if not target or visit_kind(target) not in {'INTERNAL_FOOD', 'INTERNAL_CAFE', 'BAR'}:
        return plan
    if explicit_target(question, target, current):
        return plan
    from .venue_policy import _INSIDE
    for mention in _INSIDE.finditer(question):
        tail = question[mention.end():]
        if not re.match(r'\s*(?:먹거리|간식|식당|카페|커피|매장)?\s*(?:은|는|을|를)?\s*(?:그대로|유지|건드리지|바꾸지)', tail):
            return plan
    if visit_kind(target) == 'BAR' and any(
            not re.match(r'\s*(?:은|는|을|를)?\s*(?:그대로|유지|건드리지|바꾸지)', question[m.end():])
            for m in re.finditer(r'술집|주점|호프|이자카야', question)):
        return plan
    source = category(target)
    words = r'식당|음식점|식사' if source == 'FOOD' else r'카페|커피'
    if not re.search(words, question):
        # A bare menu can be shorthand for an external meal; never assign it to
        # an internal visit solely because all map food pins share FOOD.
        if source != 'FOOD':
            return plan
    eligible = [p for p in current.get('places', []) if visit_kind(p) == source]
    phase = re.search(r'(?:경기|야구|직관)\s*(전|후)', question)
    if phase:
        eligible = [p for p in eligible if p.get('phase') == ('BEFORE' if phase[1] == '전' else 'AFTER')]
    if len(eligible) == 1:
        return {**plan, 'targets':[eligible[0]['visitId']]}
    label = '일반 식당' if source == 'FOOD' else '구장 밖 카페'
    preserved = {'BAR':'술집은', 'INTERNAL_CAFE':'구장 내부 카페는',
                 'INTERNAL_FOOD':'구장 내부 먹거리는'}[visit_kind(target)]
    return {**plan, 'operation':'clarify', 'targets':[],
            'clarification':f'바꿀 {label}을 하나로 확인하지 못했어요. {preserved} 유지했어요. 변경할 장소 이름이나 번호를 알려 주세요.'}
