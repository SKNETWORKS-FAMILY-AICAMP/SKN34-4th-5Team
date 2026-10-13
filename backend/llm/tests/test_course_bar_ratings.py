from datetime import date, timedelta
from unittest.mock import Mock, patch

from django.core.cache.backends.locmem import LocMemCache
from django.test import SimpleTestCase, override_settings

from llm.v2.course import bar_ratings, place_quality as quality, quality_research, recommendation_policy as policy, venue_policy
from llm.tests.test_course_place_warmer import PLACE

BAR = {**PLACE, "name": "시험호프", "detail": "음식점 > 술집 > 호프,요리주점", "kind": "BAR"}
URL = "https://www.diningcode.com/profile.php?rid=Test123456"


@override_settings(COURSE_RECOMMENDATION_MODE="balanced")
class BarRatingTests(SimpleTestCase):
    def setUp(self):
        self.cache = LocMemCache(self.id(), {})
        self.cache.clear()
        for target, name, value in ((quality, "verdict_cache", self.cache), (quality, "catalogue", {})):
            self.enterContext(patch.object(target, name, return_value=value))
        self.enterContext(patch("travel.course_place_warmer.remember"))
        self.enterContext(policy.request("술집 코스"))
        self.enterContext(quality.request_scope("GWANGJU"))

    def save(self, place=BAR, **changes):
        row = {**place, "rating": 3.5, "ratingCount": 1, "reviewUrl": URL,
               "checkedAt": date.today().isoformat(), **changes}
        self.cache.set(bar_ratings.cache_key(place), {"row": row})
        return row

    def test_unknown_bars_rejected_through_raw_and_normalized_adapters(self):
        raw = {"id": BAR["placeId"], "place_name": BAR["name"], "x": BAR["lng"], "y": BAR["lat"],
               "category_name": BAR["detail"], "category_group_code": "FD6"}
        for place, category in ((BAR, "BAR"), (BAR, "FOOD"), ({**BAR, "category": "FOOD_OUT"}, "FOOD_OUT"), (raw, "FD6")):
            with self.subTest(category=category):
                self.assertIsNone(quality.filter_place({**place, "rating": 5, "ratingCount": 999}, category))
        for code in ("JAMSIL", "SAJIK", "DAEJEON"):
            with quality.request_scope(code):
                self.assertIsNone(quality.filter_place(BAR, "FOOD"))
                self.assertIsNotNone(quality.filter_place(PLACE, "FOOD"))
        for detail in ("포장마차", "맥주", "바(BAR)"):
            self.assertIsNone(quality.filter_place({**BAR, "detail": detail}, "FOOD"))

    def test_one_verified_rating_suffices_without_interior_approval(self):
        self.save()
        self.assertIsNotNone(quality.filter_place(BAR, "FOOD"))
        self.assertIsNone(quality.resolve(BAR), "Rating-only evidence must not become interior approval")
        self.assertIn("평가 1명", quality.annotation(BAR))
        self.assertIn(URL, quality.annotation(BAR))
        with quality.request_scope("SAJIK"):
            self.assertIsNotNone(quality.filter_place(BAR, "FOOD"))

    def test_missing_invalid_stale_or_other_branch_ratings_do_not_pass(self):
        for changes in ({"rating": None}, {"rating": 0}, {"rating": float("nan")}, {"rating": True},
                        {"ratingCount": 0}, {"ratingCount": None}, {"ratingCount": True}, {"ratingCount": 1.5},
                        {"reviewUrl": ""}, {"reviewUrl": URL + "&fake=1"}, {"placeId": "999"},
                        {"lat": BAR["lat"] + .01}, {"name": "다른지점"},
                        {"checkedAt": (date.today() - timedelta(days=181)).isoformat()},
                        {"checkedAt": (date.today() + timedelta(days=1)).isoformat()}):
            with self.subTest(changes=changes):
                self.save(**changes)
                self.assertIsNone(quality.filter_place(BAR, "FOOD"))

    def test_food_and_cafe_behavior_unchanged_and_no_unrelated_rating_reads(self):
        cafe = {**PLACE, "detail": "음식점 > 카페", "category": "CAFE"}
        with patch.object(bar_ratings, "investigate") as investigate:
            result = quality.verify_candidates([PLACE, BAR], "FOOD", "식당", center=PLACE)
            self.assertEqual([p["placeId"] for p in result], [PLACE["placeId"]])
            self.assertTrue(result[0]["qualityUnverified"])
            self.assertIsNotNone(quality.filter_place(cafe, "CAFE"))
        investigate.assert_not_called()

    def test_new_bar_rating_is_preserved_without_model_or_interior_call(self):
        proof = {"place": BAR, "url": URL, "rating": 4.0, "ratingCount": 1}
        with patch.object(quality_research, "discover", return_value=[URL]) as discover, \
                patch.object(quality_research, "profile", return_value=proof) as profile, \
                patch.object(quality_research, "interiors", side_effect=AssertionError("No interior model")):
            result = quality.verify_candidates([BAR], "FOOD", "술집", center=BAR)
            self.assertEqual(len(result), 1)
            self.assertEqual(bar_ratings.resolve(BAR)["ratingCount"], 1)
            again = quality.verify_candidates([BAR], "FOOD", "술집", center=BAR)
            self.assertEqual(result, again)
        discover.assert_called_once()
        self.assertEqual(profile.call_args.kwargs, {"min_ratings": 1})

    def test_failed_reads_stay_excluded_and_are_not_repeated(self):
        with patch.object(quality_research, "discover", return_value=[]) as discover:
            for _ in range(2):
                self.assertEqual(quality.verify_candidates([BAR], "FOOD", "술집", center=BAR), [])
        discover.assert_called_once()

    def test_broad_seeds_and_local_probes_do_not_do_new_io(self):
        with patch.object(quality_research, "discover") as discover:
            with quality.research_policy(False):
                self.assertEqual(quality.verify_candidates([BAR], "FOOD", "술집", center=BAR), [])
            with quality.local_probe():
                self.assertEqual(quality.verify_candidates([BAR], "FOOD", "술집", center=BAR), [])
        discover.assert_not_called()

    def test_remote_rating_does_not_block_closer_new_branch_or_bypass_radius(self):
        far = {**BAR, "placeId": "9002", "lat": BAR["lat"] + .014}
        self.save(far)
        proof = {"place": BAR, "url": URL, "rating": 4., "ratingCount": 2}
        with patch.object(quality_research, "discover", return_value=[URL]), \
                patch.object(quality_research, "profile", return_value=proof) as profile:
            rows = quality.verify_candidates([far, BAR], "FOOD", "술집", center=BAR)
        self.assertEqual([p["placeId"] for p in rows], [BAR["placeId"], far["placeId"]])
        profile.assert_called_once()
        raw = {"id": far["placeId"], "place_name": far["name"], "x": far["lng"], "y": far["lat"], "category_name": far["detail"]}
        with patch.object(quality, "search_candidates", return_value=[raw]):
            rows = venue_policy.search_candidates(Mock(), {"latitude": BAR["lat"], "longitude": BAR["lng"], "radius": 300}, "BAR", discovered=[])
        self.assertEqual(rows, [])

    def test_known_rejection_and_user_exclusions_survive_verified_ratings(self):
        self.save()
        with patch.object(quality, "resolve", return_value={"status": "rejected"}):
            self.assertIsNone(quality.filter_place(BAR, "FOOD"))
        for identifier in ("18774818", "119189575"):
            blocked = {**BAR, "placeId": identifier}
            self.save(blocked)
            self.assertIsNone(quality.filter_place(blocked, "FOOD"))

    def test_rating_budget_is_shared_and_bounded_across_searches(self):
        bars = [{**BAR, "placeId": str(9100 + i)} for i in range(10)]
        with patch.object(quality_research, "discover", return_value=[]) as discover:
            self.assertEqual(quality.verify_candidates(bars, "BAR", "술집", center=BAR), [])
            self.assertEqual(quality.verify_candidates(bars, "BAR", "술집", center=BAR), [])
        self.assertEqual(discover.call_count, bar_ratings.MAX_CANDIDATES)

    def test_closed_reviewed_bar_does_not_suppress_new_candidate_check(self):
        closed = {**BAR, "placeId": "9002"}
        self.save(closed)
        proof = {"place": BAR, "url": URL, "rating": 4., "ratingCount": 1}
        with patch("llm.v2.course.availability.check", side_effect=lambda p, day: "휴무" if p["placeId"] == "9002" else ""), \
                patch.object(quality_research, "discover", return_value=[URL]), \
                patch.object(quality_research, "profile", return_value=proof) as profile:
            quality.verify_candidates([closed, BAR], "BAR", "술집", center=BAR)
        profile.assert_called_once()
        self.assertIsNotNone(bar_ratings.resolve(BAR))
