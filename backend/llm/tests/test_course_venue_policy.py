import json
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from travel.collected_places import CatalogueUnavailable
from travel.stadium_facilities import _root
from travel.stadium_scope import classify_stadium_point
from llm.v1.rag.course import agent, editing, memory, slots, venue_policy
from llm.tests.test_course_editing import plan


class CourseVenuePolicyTests(SimpleTestCase):
    def setUp(self):
        self.locations = json.loads((_root() / "stadium_locations.json").read_text(encoding="utf-8"))["stadiums"]
        center = self.locations["SAJIK"]
        self.anchor = {"lat": center["lat"], "lng": center["lng"], "category": "STADIUM", "name": "사직야구장"}
        self.inside = {**self.anchor, "category": "CAFE", "name": "아무 카페", "placeId": "inside", "detail": "음식점 > 카페",
                       "address": "주소 없음", "distance": 10, "dist": .1}
        self.outside = {**self.inside, "lng": center["lng"] + .008, "placeId": "outside", "name": "동네 사직야구장점", "distance": 750}
        self.assertEqual(classify_stadium_point(self.outside)["scope"], "external")

    def raw(self, place):
        return {"id": place["placeId"], "place_name": place["name"], "category_name": place["detail"],
                "road_address_name": place["address"], "y": str(place["lat"]), "x": str(place["lng"])}

    def test_internal_centers_of_all_stadiums_and_all_visit_categories_are_excluded_by_default(self):
        for code, center in self.locations.items():
            for category in ("FOOD_OUT", "CAFE", "WALK", "SPOT", "CONVENIENCE", "INDOOR", "STAY"):
                with self.subTest(code=code, category=category):
                    self.assertEqual(venue_policy.filter_candidates([{**self.inside, **{k: center[k] for k in ("lat", "lng")}, "category": category}]), [])

    def test_unknown_names_missing_tags_and_bad_addresses_do_not_bypass_geometry(self):
        candidates = [self.inside, self.outside]
        before = deepcopy(candidates)
        self.assertEqual(venue_policy.filter_candidates(candidates), [self.outside])
        self.assertEqual(candidates, before)
        # 구장 이름을 포함하는 실제 외부 지점은 과도하게 제외하지 않는다.
        picked = agent.pick([{**self.outside, "category": "FOOD_OUT"}], 3, slots.parse("식사"), 2500)
        self.assertEqual([p["placeId"] for p in picked], ["outside"])

    def test_provider_tag_and_legacy_rag_internal_scope_are_not_lost(self):
        for extra in ({"stadiumArea": {"scope": "internal", "stadium": "SAJIK"}}, {"scope": "internal"}, {"category": "FOOD_IN"}):
            self.assertEqual(venue_policy.filter_candidates([{**self.outside, **extra}]), [])

    def test_explicit_request_is_limited_to_this_stadium_category_and_turn(self):
        question = "구장 안에서 커피 마실 카페를 넣어줘"
        request = [{"category": "CAFE", "expression": question}]
        other = {**self.inside, "category": "FOOD_OUT"}
        other_stadium = {**self.inside, **{k: self.locations["JAMSIL"][k] for k in ("lat", "lng")}}
        with venue_policy.request_policy(question, "SAJIK", request):
            self.assertEqual(venue_policy.filter_candidates([self.inside, other, other_stadium]), [self.inside])
        self.assertEqual(venue_policy.filter_candidates([self.inside]), [])

    def test_nearby_negative_and_quoted_old_permission_never_enable_internal_candidates(self):
        for question, expression in (("구장 가기 전에 카페", "구장 가기 전에 카페"),
                ("구장 근처 카페", "구장 근처 카페"), ("구장 안 카페는 빼줘", "구장 안 카페"),
                ("구장 내부 카페 말고 밖에서", "구장 내부 카페"), ("새 코스 짜줘", "구장 안 카페")):
            with self.subTest(question=question), venue_policy.request_policy(question, "SAJIK", [{"category": "CAFE", "expression": expression}]):
                self.assertEqual(venue_policy.filter_candidates([self.inside]), [])

    def test_explicit_inside_particles_are_allowed_but_stadium_guidance_is_not(self):
        for question in ("구장 안에 있는 카페", "야구장 내에 있는 카페", "경기장 안쪽 카페"):
            with self.subTest(question=question), venue_policy.request_policy(question, "SAJIK", [{"category": "CAFE", "expression": question}]):
                self.assertEqual(venue_policy.filter_candidates([self.inside]), [self.inside])
        with venue_policy.request_policy("구장 안내하고 카페 추천해줘", "SAJIK", [{"category": "CAFE", "expression": "구장 안내하고 카페 추천해줘"}]):
            self.assertEqual(venue_policy.filter_candidates([self.inside]), [])

    def test_complex_exclusion_and_missing_geometry_still_apply_to_opt_in(self):
        with venue_policy.request_policy("구장 안 카페", "SAJIK", [{"category": "CAFE", "expression": "구장 안 카페"}]):
            excluded = {**self.inside, "lat": 35.1900, "lng": 129.0583}
            self.assertEqual(venue_policy.filter_candidates([excluded]), [])
            with patch("travel.stadium_scope._root", return_value=Path("/missing-venue-policy-test")):
                with self.assertRaises(CatalogueUnavailable):
                    venue_policy.filter_candidates([self.inside])

    def test_request_permission_is_isolated_across_workers(self):
        def run(allow):
            question = "구장 안 카페"
            with venue_policy.request_policy(question, "SAJIK", [{"category": "CAFE", "expression": question}] if allow else []):
                return bool(venue_policy.filter_candidates([self.inside]))
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(run, [True, False, True, False])), [True, False, True, False])

    def test_step_search_edit_search_and_rag_ranking_all_apply_same_default(self):
        payload = {"places": [self.raw(self.inside), self.raw(self.outside)]}
        with patch.object(agent, "invoke_domain_tool", return_value=payload):
            fresh = agent._kakao_step("CAFE", self.anchor, 2500, self.anchor, slots.parse("카페"))
            edit = editing.candidates(self.outside, self.anchor, {"query": "카페"}, [])
        cached = agent.pick([self.inside, self.outside], 3, slots.parse("카페"), 2500)
        for candidates in (fresh, edit, cached):
            self.assertEqual([p["placeId"] for p in candidates], ["outside"])

    def test_origin_and_drawn_corridor_do_not_reintroduce_internal_pool_or_fresh_results(self):
        for segments in (None, [(self.outside, self.anchor)]):
            with self.subTest(corridor=bool(segments)), patch.object(agent, "_kakao_step", return_value=[self.inside]):
                result = agent.build_origin_course(self.outside, self.anchor, [self.inside], slots.parse("경기 전 카페만"), False, segments)
            self.assertIsNone(result)

    def test_internal_only_first_page_does_not_hide_external_replacement_on_next_page(self):
        def search(domain, name, args):
            return {"places": [self.raw(self.inside if args["page"] == 1 else self.outside)], "hasNextPage": args["page"] == 1}
        with patch.object(agent, "invoke_domain_tool", side_effect=search):
            for candidates in (agent._kakao_step("CAFE", self.anchor, 2500, self.anchor, slots.parse("카페")),
                               editing.candidates(self.outside, self.anchor, {"query": "카페"}, [])):
                self.assertEqual([p["placeId"] for p in candidates], ["outside"])

    def test_new_generation_receives_opt_in_but_does_not_store_it_as_preference(self):
        question = "구장 안에서 카페 들른 뒤 경기를 볼 코스 짜줘"
        requested = [{"category": "CAFE", "expression": question}]
        def generate(*args, **kwargs):
            self.assertEqual(venue_policy.filter_candidates([self.inside]), [self.inside])
            return {"places": []}
        with patch.object(editing, "interpret", return_value=plan("new", [], internal_venue_requests=requested)), \
                patch.object(agent, "_answer", side_effect=generate):
            result = agent.answer(question, hint_stadium="SAJIK", course_memory=memory.empty(), course_request="NEW")
        self.assertEqual(result["courseMemory"], memory.empty())
        self.assertEqual(venue_policy.filter_candidates([self.inside]), [])

    def test_partial_edit_receives_only_current_category_permission(self):
        question = "카페만 구장 안 카페로 바꿔줘"
        current = {"stadiumCode": "SAJIK", "places": [{**self.outside, "visitId": "cafe"}]}
        parsed = plan("replace", ["cafe"], internal_venue_requests=[{"category": "CAFE", "expression": question}])
        def edit(*args, **kwargs):
            self.assertEqual(venue_policy.filter_candidates([self.inside, {**self.inside, "category": "FOOD"}]), [self.inside])
            return {"places": [], "answer": "edit reached"}
        with patch.object(editing, "interpret", return_value=parsed), patch.object(editing, "answer", side_effect=edit), \
                patch.object(agent, "_answer") as generate:
            result = agent.answer(question, hint_stadium="SAJIK", current_course=current, course_request="EDIT")
        self.assertEqual(result["answer"], "edit reached")
        generate.assert_not_called()
        self.assertEqual(venue_policy.filter_candidates([self.inside]), [])
