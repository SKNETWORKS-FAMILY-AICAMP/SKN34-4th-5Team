from unittest.mock import patch
from django.test import SimpleTestCase
from llm.v1.rag.course import catalog_conditions as catalog, evidence_memory, editing, agent


class CatalogConditionsTests(SimpleTestCase):
    def test_incomplete_probe_does_not_cache_failure_for_normal_condition_search(self):
        from llm.v1.rag.course import place_quality
        row = {"placeId": "1", "name": "식당", "lat": 37.51, "lng": 127.08, "category": "FOOD_OUT"}
        state = {"conditions": [{"scope": "FOOD", "text": "돈까스"}]}
        cache = {}
        with patch.object(editing, "choose", side_effect=[[], [row]]) as choose:
            with place_quality.local_probe():
                self.assertEqual(agent.remembered_candidates([row], state, cache), [])
            self.assertEqual(agent.remembered_candidates([row], state, cache), [row])
            self.assertEqual(choose.call_count, 2)

    def test_only_complete_positive_catalog_sentences_are_parsed(self):
        self.assertEqual(catalog.parse(["구장 밖 양식 식당에서 식사하고", "카페에 갔다가"]), ["양식", "카페"])
        self.assertEqual(catalog.parse(["메가MGC커피 브랜드", "경기 전 일식 식당"]), ["메가MGC커피", "일식"])
        for text in ("양식 말고", "양식 또는 일식", "가능하면 양식", "조용한 카페", "주차 가능한 카페",
                     "카페에서 아츄 먹고", "중식집에서 자장면", "매콤한 자장면 식당", "양식 카페",
                     "카페는 컴포즈만 제외", "메가MGC커피 잠실점", "매장 깨끗한 양식"):
            with self.subTest(text=text):
                self.assertIsNone(catalog.parse([text]))

    def test_cross_category_activities_keep_semantic_menu_and_service_evidence(self):
        for text in ("한식 식당에서 커피 한잔", "카페에서 식사하고", "메가커피에서 식사하기"):
            with self.subTest(text=text), patch.object(agent, "llm") as llm:
                self.assertIsNone(catalog.parse([text]))
                self.assertIsNone(catalog.choose([], {"conditions": [text]}, text, verified=True))
                llm.return_value.with_structured_output.return_value.invoke.return_value = {"items": [
                    {"term": "커피" if "커피 한잔" in text else "식사", "attribute": "menu", "intent": "required", "group": "1"}]}
                self.assertEqual(evidence_memory.requirements([text])[0].attribute, "menu")
                llm.assert_called_once()
        self.assertEqual(catalog.parse(["카페에서 커피 한잔", "한식 식당에서 식사하고"]), ["카페", "한식"])

    def test_catalog_proof_is_not_inferred_from_shop_name_or_address(self):
        self.assertTrue(catalog.matches({"detail": "음식점 > 양식"}, "양식"))
        self.assertFalse(catalog.matches({"name": "양식집", "detail": "음식점 > 한식"}, "양식"))
        cafe = {"detail": "음식점 > 카페 > 커피전문점"}
        self.assertTrue(catalog.matches({**cafe, "name": "메가MGC커피 종합운동장점"}, "메가MGC커피"))
        for name in ("메가커피맛집", "메가커피가 아닌 카페", "컴포즈커피 잠실점"):
            self.assertFalse(catalog.matches({**cafe, "name": name}, "메가MGC커피"))
        self.assertFalse(catalog.matches({"name": "메가MGC커피 잠실점", "detail": "음식점 > 한식"}, "메가MGC커피"))

    def test_known_conditions_skip_models_but_ambiguous_ones_fall_back(self):
        with patch.object(agent, "llm") as llm:
            result = evidence_memory.requirements(["구장 밖 양식 식당에서 식사하고"])
            self.assertEqual([(r.term, r.attribute) for r in result], [("양식", "catalog")])
            llm.assert_not_called()
            llm.return_value.with_structured_output.return_value.invoke.return_value = {"items": [
                {"term": "자장면", "attribute": "menu", "intent": "required", "group": "1"}]}
            self.assertEqual(evidence_memory.requirements(["매콤한 자장면 식당"])[0].attribute, "menu")
            llm.assert_called_once()

    def test_fast_selection_preserves_input_order_and_keeps_other_filters(self):
        rows = [{"name": "첫 식당", "placeId": "1", "category": "FOOD_OUT", "detail": "음식점 > 양식"},
                {"name": "다른 식당", "placeId": "2", "category": "FOOD_OUT", "detail": "음식점 > 일식"}]
        rows = [{**p, "lat": 37.511, "lng": 127.085} for p in rows]
        with patch.object(agent, "llm") as llm:
            result = editing.choose(rows, {"query": "", "conditions": ["양식"], "all_matches": True}, "양식")
        self.assertEqual([p["placeId"] for p in result], ["1"])
        llm.assert_not_called()

    def test_lossy_summary_cannot_override_original_edit_brand(self):
        self.assertIsNone(catalog.choose([], {"query": "카페", "conditions": ["카페"]}, "컴포즈커피로 바꿔줘"))

    def test_transport_instruction_does_not_trigger_repeated_venue_checks(self):
        conditions = ["모든 이동은 도보", "각 장소는 한 곳씩", "구장 밖 중식 식당에서 식사"]
        row = {"name": "중식집", "placeId": "1", "category": "FOOD_OUT",
               "detail": "음식점 > 중식 > 중국요리", "lat": 37.511, "lng": 127.085}
        with patch.object(agent, "llm") as llm:
            self.assertEqual([r.term for r in evidence_memory.requirements(conditions)], ["중식"])
            selected = editing.choose([row], {"conditions": conditions, "all_matches": True}, "; ".join(conditions))
            self.assertEqual(selected[0]["placeId"], "1")
            self.assertEqual(evidence_memory.requirements(["모든 이동은 도보"]), [])
            self.assertEqual(evidence_memory.requirements(["각 장소는 한 곳씩"]), [])
            park = {**row, "category": "WALK", "detail": "여행 > 공원"}
            self.assertEqual(editing.choose([park], {"conditions": ["모든 이동은 도보"], "all_matches": True},
                                           "모든 이동은 도보")[0]["placeId"], "1")
            llm.assert_not_called()

    def test_walking_distance_accessibility_and_menu_constraints_still_need_proof(self):
        for text in ("도보 10분 이내", "도보 이동이 편한 곳", "휠체어로 이동", "도보 이동 말고",
                     "모든 이동은 도보; 자장면", "가능하면 도보 이동"):
            self.assertIsNone(catalog.parse([text]))
        self.assertIsNone(catalog.parse(["모든 이동은 도보", "돈카츠 식당 제외"]))

    def test_exact_menu_skips_interpretation_but_never_source_verification(self):
        conditions = ["구장 밖 돈카츠 식당에서 식사하고"]
        plan = {"query": "", "conditions": conditions, "all_matches": True}
        row = {"name": "돈까스 식당", "placeId": "1", "category": "FOOD_OUT",
               "detail": "음식점 > 일식 > 돈까스", "lat": 37.511, "lng": 127.085}
        check = {"term": "돈까스", "attribute": "menu", "intent": "required", "status": "match"}
        with patch.object(agent, "llm") as llm, patch.object(evidence_memory, "enrich") as enrich:
            req = evidence_memory.requirements(conditions)
            self.assertEqual([(r.term, r.attribute) for r in req], [("돈까스", "menu")])
            self.assertIsNone(catalog.choose([row], plan, conditions[0]))
            for checks, expected in (([], []), ([{**check, "status": "unknown"}], []), ([check], ["1"])):
                enrich.return_value = [{**row, "conditionChecks": checks}]
                selected = editing.choose([row], plan, conditions[0])
                self.assertEqual([p["placeId"] for p in selected], expected)
            self.assertEqual(enrich.call_count, 3)
            llm.assert_not_called()
