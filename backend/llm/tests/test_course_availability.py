from contextlib import ExitStack
from copy import deepcopy
from datetime import date
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from llm.v1.rag.course import agent, availability as hours, editing, evidence_memory, grounding, timeline
from .test_course_route_ranking import ANCHOR, ORIGIN, NEAR, FAR, CAFE, PARK, provider


DAY = "2026-10-06"  # Tuesday, Asia/Seoul
SHOP = {"placeId": "hours:shop", "name": "가상 식당", "address": "서울 송파구 올림픽로 10", "category": "FOOD"}


def document(text, **changes):
    return {"body_read": True, "status": "read", "title": "가상 식당 - 잠실 음식점", "url": "https://www.diningcode.com/profile.php?rid=hours",
            "body_text": "가상 식당\n서울특별시 송파구 올림픽로 10\n" + text, **changes}


class HoursRulesTests(SimpleTestCase):
    def test_date_tabs_are_not_promoted_to_recurring_weekly_hours(self):
        info = hours.parse("오늘(월)\n영업시간: 11:30 - 19:30\n10월 6일(화) 10월 7일(수)\n영업시간: 11:30 - 19:30",
                           observed_on=date(2026, 10, 5))
        self.assertTrue(hours._verdict(info, date(2026, 10, 6), 1380, 50)[0])
        self.assertEqual(hours._verdict(info, date(2026, 10, 13), 1380, 50)[0], "")
        closed = hours.parse("영업시간\n10월 6일(화)\n휴무", observed_on=date(2026, 10, 5))
        self.assertEqual(hours._verdict(closed, date(2026, 10, 6), 1080, 50)[0], "휴무일")
        self.assertEqual(hours._verdict(closed, date(2026, 10, 13), 1080, 50)[0], "")
        special = hours.parse("영업시간\n매주 화요일 휴무\n10월 6일(화)\n11:00 - 22:00", observed_on=date(2026, 10, 5))
        self.assertEqual(hours._verdict(special, date(2026, 10, 6), 1080, 50)[0], "")
        self.assertEqual(hours._verdict(special, date(2026, 10, 6), None, 0)[0], "")
        self.assertEqual(hours._verdict(special, date(2026, 10, 13), 1080, 50)[0], "휴무일")

    def verdict(self, text, at="18:00", stay=50, day=DAY, **page_changes):
        with hours.session():
            hours.observe(SHOP, document(text, **page_changes))
            return hours.check(SHOP, day, {"time": at, "stayMin": stay})

    def test_unknown_empty_ambiguous_and_unread_pages_keep_candidate(self):
        for text in ("메뉴정보\n자장면", "영업시간\n업체 문의", "영업시간\n시간 변동 가능", "영업시간\n11:00 ~ 마감 시", "영업시간\n매월 둘째 화요일 휴무"):
            self.assertEqual(self.verdict(text), "")
        self.assertEqual(self.verdict("폐업", body_read=False), "")

    def test_closed_before_open_after_close_and_exact_boundary(self):
        text = "영업시간\n매일 11:00 - 22:00"
        self.assertEqual(self.verdict(text, "11:00"), "")
        self.assertEqual(self.verdict(text, "21:10"), "")
        for at in ("10:59", "21:11", "22:00"):
            self.assertIn("영업시간 밖", self.verdict(text, at))

    def test_korean_clocks_and_am_pm(self):
        for text in ("매일 오전 11:00 - 오후 10:00", "매일 11시 ~ 22시"):
            self.assertEqual(self.verdict("영업시간\n" + text, "20:00"), "")
            self.assertIn("영업시간 밖", self.verdict("영업시간\n" + text, "22:00"))

    def test_weekday_weekend_and_weekday_range(self):
        text = "영업시간\n평일 11:00 - 18:00\n주말 11:00 - 23:00"
        self.assertIn("영업시간 밖", self.verdict(text))
        self.assertEqual(self.verdict(text, day="2026-10-10"), "")
        self.assertEqual(self.verdict("영업시간\n월~금 11:00 - 22:00"), "")

    def test_regular_day_off_and_separate_heading(self):
        for text in ("영업시간\n매일 11:00 - 22:00\n매주 화요일 정기휴무", "휴무일\n매주 화요일", "매주 화요일 휴무"):
            self.assertEqual(self.verdict(text), "휴무일")
            self.assertEqual(self.verdict(text, day="2026-10-07"), "")

    def test_dated_exception_and_special_open_day(self):
        self.assertEqual(self.verdict("휴무일\n2026-10-06 휴무"), "휴무일")
        self.assertEqual(self.verdict("휴무일\n2026-10-07 휴무"), "")
        self.assertEqual(self.verdict("영업시간\n매주 화요일 휴무\n2026-10-06 11:00 - 22:00"), "")

    def test_break_and_split_sessions_reject_overlap(self):
        text = "영업시간\n매일 11:00 - 22:00\n브레이크타임 15:00 - 17:00"
        self.assertEqual(self.verdict(text, "14:10"), "")
        self.assertEqual(self.verdict(text, "14:11"), "브레이크타임")
        self.assertEqual(self.verdict(text, "17:00"), "")
        self.assertIn("영업시간 밖", self.verdict("영업시간\n매일 11:00 - 15:00\n17:00 - 22:00", "14:30"))

    def test_last_order_inline_and_separate(self):
        for suffix in ("\n라스트오더 21:00", " (21:00 라스트오더)"):
            self.assertEqual(self.verdict("영업시간\n매일 11:00 - 22:00" + suffix, "21:00", 20), "주문 마감 이후")

    def test_overnight_and_previous_day_last_order(self):
        text = "영업시간\n매일 18:00 - 02:00\n라스트오더 01:30"
        self.assertEqual(self.verdict(text, "익일 00:30", 40), "")
        self.assertEqual(self.verdict(text, "익일 01:30", 20), "주문 마감 이후")
        self.assertIn("영업시간 밖", self.verdict(text, "익일 01:40", 30))

    def test_unknown_today_is_not_inferred_from_yesterdays_hours(self):
        self.assertEqual(self.verdict("영업시간\n월요일 18:00 - 02:00", "18:00"), "")
        self.assertEqual(self.verdict("영업시간\n월요일 18:00 - 02:00", "00:30"), "")

    def test_day_offset_uses_actual_calendar_day(self):
        with hours.session():
            hours.observe(SHOP, document("휴무일\n매주 수요일 휴무"))
            self.assertEqual(hours.check(SHOP, DAY, {"time": "익일 00:10", "stayMin": 30}), "휴무일")
            self.assertEqual(hours.check(SHOP, DAY, {"time": "18:00", "stayMin": 30}), "")

    def test_today_closed_badge_does_not_prove_future_closure(self):
        for text in ("영업시간\n현재 영업 종료", "영업시간\n오늘 휴무", "영업시간\n임시휴업"):
            self.assertEqual(self.verdict(text), "")

    def test_facility_hours_are_not_business_hours(self):
        for text in ("수영장 운영시간 06:00 - 10:00", "수영장\n운영시간\n06:00 - 10:00", "조식 이용시간\n07:00 - 10:00"):
            self.assertEqual(self.verdict(text), "")
        self.assertEqual(self.verdict("주차장\n운영시간\n08:00 - 10:00\n매장 영업시간\n매일 11:00 - 22:00"), "")

    def test_permanent_closure_and_marked_title(self):
        self.assertEqual(self.verdict("폐업"), "폐업")
        self.assertEqual(self.verdict("영업 상태: 영구 영업 종료"), "폐업")
        self.assertEqual(self.verdict("매장 정보", title="가상 식당 (폐업) - 잠실 음식점"), "폐업")

    def test_historical_review_other_place_and_negation_are_not_closure(self):
        for text in ("폐업이 아닙니다", "방문자 리뷰\n폐업\n영업시간\n매주 화요일 휴무", "예전에 옆 식당이 폐업했어요"):
            self.assertEqual(self.verdict(text), "")
        self.assertEqual(self.verdict("폐업", title="다른 식당 - 잠실"), "")
        self.assertEqual(self.verdict("폐업", body_text="서울 송파구 올림픽로 100\n폐업"), "")

    def test_disagreeing_sources_remain_uncertain(self):
        with hours.session():
            hours.observe(SHOP, document("폐업"))
            hours.observe(SHOP, document("영업시간\n매일 11:00 - 22:00", url="https://polle.com/place/fixture"))
            self.assertEqual(hours.check(SHOP, DAY, {"time": "18:00", "stayMin": 50}), "")

    def test_twenty_four_hours_and_missing_visit_date(self):
        self.assertEqual(self.verdict("24시간 영업", "23:30", 50), "")
        self.assertEqual(self.verdict("영업시간\n매일 11:00 - 12:00", day=None), "")
        self.assertEqual(self.verdict("폐업", day=None), "폐업")

    def test_request_scope_does_not_leak_to_next_course(self):
        self.assertEqual(self.verdict("폐업"), "폐업")
        self.assertEqual(hours.check(SHOP, DAY), "")


class RepairTests(SimpleTestCase):
    def test_closed_then_bad_hours_then_unknown_candidate_and_recalculate(self):
        original = {**SHOP, "key": "food", "phase": "BEFORE"}
        short = {**SHOP, "placeId": "short", "name": "짧은영업식당"}
        unknown = {**SHOP, "placeId": "unknown", "name": "정보없는식당"}
        stadium = {"key": "stadium", "placeId": "stadium", "category": "STADIUM", "name": "구장", "phase": "GAME"}
        calculate = Mock(side_effect=lambda rows: {"tl": {"rows": [{"time": "18:00", "stayMin": 50} for p in rows]}})
        def candidates(target, i, rows, rejected):
            return [short, unknown]
        with hours.session():
            hours.observe(SHOP, document("폐업"))
            hours.observe(short, document("영업시간\n매일 11:00 - 18:10", title="짧은영업식당"))
            result, _, changes = hours.repair([original, stadium], calculate, candidates, DAY)
        self.assertEqual([p["name"] for p in result], [unknown["name"], "구장"])
        self.assertEqual(calculate.call_count, 3)
        self.assertEqual(len(changes), 2)
        self.assertEqual(original["name"], SHOP["name"])

    def test_no_alternative_omits_closed_visit_and_keeps_other_stops(self):
        with hours.session():
            hours.observe(SHOP, document("폐업"))
            kept = {**SHOP, "name": "다른카페", "placeId": "other", "category": "CAFE"}
            result, _, changes = hours.repair([SHOP, kept], lambda rows: {"tl": {"rows": [{} for p in rows]}}, lambda *a: [], DAY)
        self.assertEqual(result, [kept])
        self.assertIn("대체 후보를 찾지 못해", changes[0])

    def test_unknown_does_not_search_or_remove_and_completed_is_preserved(self):
        search = Mock()
        calculate = lambda rows: {"tl": {"rows": [{} for p in rows]}}
        with hours.session():
            self.assertEqual(hours.repair([SHOP], calculate, search, DAY)[0], [SHOP])
            hours.observe(SHOP, document("폐업"))
            done = {**SHOP, "completed": True}
            self.assertEqual(hours.repair([done], calculate, search, DAY)[0], [done])
        search.assert_not_called()

    def test_grounding_collects_hours_even_when_requested_menu_is_not_found(self):
        from .test_course_grounding import claim
        f = claim(place_id=SHOP["placeId"])
        with evidence_memory.request_budget():
            budget = evidence_memory._BUDGET.get()
            budget["deadline"] = grounding.time.monotonic() + 30
            with patch.object(grounding, "PublicReader") as reader:
                reader.return_value.read.return_value = document("폐업\n메뉴정보\n우동")
                self.assertEqual(grounding.verify([f], {f.url}, budget), [])
            self.assertEqual(hours.check(SHOP, DAY), "폐업")

    def test_lodging_reference_page_is_checked_without_extra_page_or_model_calls(self):
        from llm.v1.rag.nearby import lodging
        hotel = {"id": "hotel", "name": "가상호텔", "address": SHOP["address"]}
        url = "https://nol.yanolja.com/stay/domestic/fixture"
        page = document("폐업", title="가상호텔 호텔/리조트 예약 | NOL", url=url)
        model = Mock()
        model.with_structured_output.return_value.invoke.return_value = {"requirements": [], "properties": []}
        with hours.session(), patch("travel.public_page_reader.PublicReader") as reader, patch.object(agent, "llm", return_value=model):
            reader.return_value.read.return_value = page
            lodging._read_details([hotel], [url], [], {"deadline": grounding.time.monotonic() + 90})
            self.assertEqual(hours.check({**hotel, "placeId": "hotel"}, DAY), "폐업")
        reader.return_value.read.assert_called_once()
        model.with_structured_output.return_value.invoke.assert_called_once()

    def test_menu_evidence_on_closed_first_batch_continues_to_next_candidate(self):
        req = evidence_memory.Requirement(term="돈까스", attribute="menu", intent="required", group="food")
        candidates = [{**SHOP, "placeId": str(i), "name": f"가상식당{i}"} for i in range(5)]
        def search(active, requested):
            for p in active:
                if p["placeId"] != "4":
                    hours.observe(p, document("폐업", title=p["name"]))
            return [], set(), 1
        rows = [{"attribute": "menu", "term": "돈까스", "polarity": "positive", "source": {"url": "https://example.com/menu"}}]
        with self.settings(COURSE_WEB_VERIFICATION_ENABLED=True), evidence_memory.request_budget(), \
                patch.object(evidence_memory, "requirements", return_value=[req]), \
                patch.object(evidence_memory, "read", return_value=[]), \
                patch.object(evidence_memory, "ensure_place", return_value=object()), \
                patch.object(evidence_memory, "cooling_terms", return_value=set()), \
                patch.object(evidence_memory, "store", return_value=0), \
                patch.object(evidence_memory, "checked_rows", return_value=rows), \
                patch.object(evidence_memory.PlaceEnrichmentAttempt.objects, "create"), \
                patch.object(evidence_memory, "search", side_effect=search) as lookup:
            result = evidence_memory.enrich(candidates, ["돈까스"])
        self.assertEqual([p["placeId"] for p in result], ["4"])
        self.assertEqual(lookup.call_count, 2)


class AvailabilityPipelineTests(SimpleTestCase):
    def fixture(self):
        def complete(p):
            return {"placeUrl": "https://example.com/place", "address": SHOP["address"], "distance": 500,
                    "doc_id": "", **p}
        near, far, cafe, park = [complete(p) for p in (NEAR, FAR, CAFE, PARK)]
        stadium = complete({**ANCHOR, "name": "잠실야구장", "key": "STADIUM", "placeId": None, "category": "STADIUM"})
        game = {"date": DAY, "time": "18:30", "home": "LG", "away": "삼성", "status": "scheduled"}
        return near, far, cafe, park, stadium, game

    def test_new_course_replaces_closed_meal_preserves_cafe_park_origin_and_payload(self):
        near, far, cafe, park, stadium, game = self.fixture()
        invoke = provider()
        def model(*a):
            hours.observe(near, document("폐업", title=near["name"]))
            return '{"course": []}', 0
        mocks = {"load_schedule": ({}, 1), "find_game": (game, False, [game]), "embed_many": ([.1], [.2]),
                 "stadium_anchor": stadium, "_live_candidates": ([near, far, cafe, park], {}), "search_places": []}
        with ExitStack() as stack:
            for name, value in mocks.items():
                stack.enter_context(patch.object(agent, name, return_value=value))
            stack.enter_context(patch.object(agent, "call_llm", side_effect=model))
            stack.enter_context(patch.object(agent, "invoke_domain_tool", invoke))
            stack.enter_context(patch.object(agent.kakao, "nearby", return_value=[]))
            stack.enter_context(patch.object(agent, "_kakao_step", side_effect=lambda kind, *a: {
                "FOOD": [near, far], "CAFE": [cafe], "WALK": [park]}[kind]))
            result = agent.answer("경기 전 식사하고 카페 갔다가 경기 후 산책만", hint_stadium="JAMSIL", origin=ORIGIN)
        names = [far["name"], cafe["name"], stadium["name"], park["name"]]
        self.assertEqual([p["name"] for p in result["places"]], names)
        self.assertEqual([p["name"] for p in result["coursePayload"]["stops"]], names)
        self.assertEqual(result["origin"]["lat"], ORIGIN["lat"])
        self.assertIn("폐업", result["answer"])
        self.assertNotIn(near["name"], names)
        self.assertTrue(all(p.get("time") for p in result["places"]))

    def test_edit_rebuild_rechecks_closing_time_and_preserves_other_visit_ids(self):
        near, far, cafe, park, stadium, game = self.fixture()
        places = [{**p, "category": "FOOD" if p["category"] == "FOOD_OUT" else p["category"], "visitId": str(i)}
                  for i, p in enumerate((near, cafe, stadium, park))]
        current = {"places": deepcopy(places), "travelMode": "walk", "legModes": {}, "game": game, "stadiumCode": "JAMSIL"}
        with hours.session(), patch.object(agent, "load_schedule", return_value=({}, 1)), \
                patch.object(agent, "find_game", return_value=(game, False, [game])), \
                patch.object(agent, "stadium_anchor", return_value=stadium), \
                patch.object(agent, "invoke_domain_tool", provider()), \
                patch.object(editing, "candidates", return_value=[{**far, "category": "FOOD"}]) as search, \
                patch.object(editing, "choose", side_effect=lambda options, *a: options):
            hours.observe(near, document("영업시간\n매일 11:00 - 12:00", title=near["name"]))
            result = editing.rebuild(places, current, "JAMSIL", ORIGIN, "순서 바꿔줘", [])
        self.assertEqual([p["name"] for p in result["places"]], [far["name"], cafe["name"], stadium["name"], park["name"]])
        self.assertEqual([p["visitId"] for p in result["places"][1:]], ["1", "2", "3"])
        self.assertEqual(result["origin"], ORIGIN)
        search.assert_called_once()
        self.assertIn("영업시간 밖", result["answer"])
