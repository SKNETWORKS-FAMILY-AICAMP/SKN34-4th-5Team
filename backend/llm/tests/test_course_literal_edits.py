from copy import deepcopy
from unittest.mock import patch

from django.test import SimpleTestCase

from llm.tests.test_course_editing import CURRENT, PLACES
from llm.v1.rag.course import agent, editing, literal_edits, memory
from llm.v1.rag.nearby import lodging


QUESTION = "난 카페 무조건 메가커피 가야해. 메가커피로 바꿔줘 그리고 산책 말고 숙박으로 일정 수정해줘"


class LiteralEditTests(SimpleTestCase):
    def test_complete_compound_request_needs_no_model_and_preserves_preferences(self):
        before = deepcopy(CURRENT)
        with patch.object(agent, 'llm') as llm:
            plan = editing.interpret(QUESTION, CURRENT, [])
        llm.assert_not_called()
        self.assertEqual(CURRENT, before)
        self.assertTrue(plan['_literal_edit'])
        self.assertEqual(plan['operation'], 'batch')
        self.assertEqual([(a['targets'], a['category']) for a in plan['actions']], [(['cafe'], ''), (['park'], 'STAY')])
        self.assertEqual(plan['preferences'], [{'scope': 'CAFE', 'text': '메가MGC커피 브랜드'}])
        self.assertEqual(plan['actions'][0]['conditions'], ['메가MGC커피 브랜드'])
        self.assertEqual(plan['actions'][1]['conditions'], [])

    def test_other_literal_brands_menus_and_category_changes_are_supported(self):
        for text, target, category in (
            ('카페는 컴포즈커피로 변경해주세요', 'cafe', ''),
            ('식당이 좀 별로다. 돈카츠 가게로 수정해줘', 'food', ''),
            ('식당을 중식으로 바꿔줘', 'food', ''),
            ('카페를 산책으로 바꿔줘', 'cafe', 'WALK'),
            ('산책 대신 숙소로 바꿔주세요', 'park', 'STAY'),
        ):
            with self.subTest(text=text):
                plan = literal_edits.interpret(text, CURRENT)
                self.assertEqual((plan['targets'], plan['category']), ([target], category))
                self.assertEqual(plan['preferences'], [])

    def test_unknown_modifiers_negations_dependencies_and_extra_questions_fall_back(self):
        for text in (
            '메가커피 말고 다른 카페로 바꿔줘', '가능하면 메가커피로 바꿔줘',
            '스타벅스로 바꿔줘', '메가커피 잠실점으로 바꿔줘',
            '메가커피로 바꿔줘 그리고 주차도 가능해야 해',
            '메가커피로 바꿔줘. 왜 추천했는지도 설명해줘',
            '산책 말고 깔끔한 숙박으로 바꿔줘', '산책 말고 호텔로 바꿔줘',
            '메가커피로 바꿔줘 그리고 컴포즈커피로 바꿔줘',
            '난 카페 무조건 메가커피 가야해. 컴포즈커피로 바꿔줘',
            '카페 전에 메가커피로 바꿔줘', '구장 내 메가커피로 바꿔줘',
            '16일로 바꾸고 메가커피로 바꿔줘',
            '식당이 좀 별로다. 메가커피로 바꿔줘',
            '메가커피로 바꿔줘. 식당이 좀 별로다',
            '산책 카페로 바꿔줘', '돈카츠 카페로 바꿔줘', '메가커피 식당으로 바꿔줘',
        ):
            with self.subTest(text=text):
                self.assertIsNone(literal_edits.interpret(text, CURRENT))

    def test_missing_ambiguous_and_conflicting_saved_targets_use_semantic_path(self):
        for places in ([], [*PLACES, {**PLACES[1], 'visitId': 'cafe2'}]):
            self.assertIsNone(literal_edits.interpret('메가커피로 바꿔줘', {**CURRENT, 'places': places}))
        for condition in ('컴포즈커피 브랜드', '카페는 스타벅스만', '조용한 카페'):
            saved = {'conditions': [{'scope': 'CAFE', 'text': condition}]}
            self.assertIsNone(literal_edits.interpret('메가커피로 바꿔줘', CURRENT, saved))

    def test_unsupported_request_still_reaches_model_with_the_full_original_text(self):
        question = '메가커피로 바꿔줘 그리고 주차도 가능해야 해'
        interpreted = literal_edits.interpret('메가커피로 바꿔줘', CURRENT)
        interpreted.pop('_literal_edit')
        interpreted['conditions'].append('주차 가능')
        with patch.object(agent, 'llm') as llm:
            invoke = llm.return_value.with_structured_output.return_value.invoke
            invoke.return_value = interpreted
            result = editing.interpret(question, CURRENT, [])
        invoke.assert_called_once()
        self.assertIn(question, invoke.call_args.args[0][1].content)
        self.assertIn('주차 가능', result['conditions'])
        self.assertNotIn('_literal_edit', result)

    def test_literal_brand_uses_actual_catalog_without_a_second_model(self):
        cafe = {**PLACES[1], 'name': '메가MGC커피 삼성중앙점', 'detail': '음식점 > 카페 > 커피전문점', 'placeId': 'new'}
        other = {**cafe, 'name': '다른커피', 'placeId': 'other'}
        plan = literal_edits.interpret('메가커피로 바꿔줘', CURRENT)
        with patch.object(agent, 'llm') as llm, patch.object(editing.availability, 'check', return_value=None):
            self.assertEqual(editing.choose([other, cafe], plan, '메가커피로 바꿔줘')['placeId'], 'new')
            self.assertIsNone(editing.choose([other], plan, '메가커피로 바꿔줘'))
        llm.assert_not_called()

    def test_plain_lodging_skips_only_condition_interpretation_never_source_verification(self):
        stay = {**PLACES[3], 'category': 'STAY'}
        plan = literal_edits.interpret('산책 말고 숙박으로 바꿔줘', CURRENT)
        with patch.object(lodging, 'verify', return_value={'items': []}) as verify:
            self.assertIsNone(editing.choose([stay], plan, '산책 말고 숙박으로 바꿔줘'))
        self.assertEqual(verify.call_args.kwargs, {'fixed_requirements': []})
        with patch.object(lodging, 'verify', return_value={'items': []}) as verify:
            editing.choose([stay], {**plan, 'conditions': ['숙소 주차 가능']}, '산책 말고 숙박으로 바꿔줘')
        self.assertNotIn('fixed_requirements', verify.call_args.kwargs)

    def test_batch_failure_remains_atomic_and_keeps_input_state(self):
        saved = {**memory.empty(), 'current': deepcopy(CURRENT)}
        before = deepcopy(saved)
        cafe = {**PLACES[1], 'placeId': 'new', 'name': '메가MGC커피 새 지점'}
        with patch.object(agent, 'requested_date', return_value=None), \
                patch.object(agent, 'stadium_anchor', return_value=PLACES[2]), \
                patch.object(editing, 'candidates', return_value=[cafe]), \
                patch.object(editing, 'choose', side_effect=[cafe, None]) as choose:
            result = editing.answer(QUESTION, [], CURRENT, 'JAMSIL', None, saved)
        self.assertEqual(result['places'], [])
        self.assertIn('전체 변경을 취소', result['answer'])
        self.assertEqual(saved, before)
        self.assertTrue(all(call.args[1]['_literal_edit'] for call in choose.call_args_list))

    def test_completed_and_locked_visits_still_block_replacement(self):
        for completed in (True, False):
            current = deepcopy(CURRENT)
            saved = memory.empty()
            if completed:
                current['places'][1]['completed'] = True
            else:
                saved['locked'] = [memory.identity(current['places'][1])]
            with patch.object(agent, 'requested_date', return_value=None), patch.object(editing, 'candidates') as search:
                result = editing.answer('메가커피로 바꿔줘', [], current, 'JAMSIL', None, saved)
            search.assert_not_called()
            self.assertEqual(result['places'], [])
