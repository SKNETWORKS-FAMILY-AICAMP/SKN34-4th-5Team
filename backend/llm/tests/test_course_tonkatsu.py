"""Menu replacement must preserve snack/bar visits and all explicit constraints."""
from copy import deepcopy
import json
from unittest.mock import patch
from django.test import SimpleTestCase
from llm.v1.rag.course import agent, editing, literal_edits, catalog_conditions as catalog
from llm.v1.rag.course import memory, edit_context, visit_roles
from llm.v2.evaluation.tonkatsu_contract import CASES, observe, CURRENT, MEAL, SNACK, SPECIALTY, OTHER, QUESTIONS


class TonkatsuReplacementTests(SimpleTestCase):
    def test_frozen_target_and_candidate_contracts(self):
        for case in CASES:
            with self.subTest(id=case['id']):
                self.assertEqual(observe(case), case['expected'])

    def test_literal_replacement_preserves_every_other_visit(self):
        for question in QUESTIONS:
            current = deepcopy(CURRENT)
            with self.subTest(question=question), patch.object(agent, 'llm') as model:
                plan = editing.interpret(question, current, [])
                result = editing.mutate(current['places'], plan, SPECIALTY)
            model.assert_not_called()
            self.assertEqual(result[0]['placeId'], 'cutlet')
            self.assertEqual(result[0]['visitId'], 'meal')
            self.assertEqual(result[1:], CURRENT['places'][1:])
            self.assertEqual(current, CURRENT)

    def test_ambiguous_meals_offer_only_meals_not_snacks_or_bars(self):
        current = {**CURRENT, 'places': [*CURRENT['places'], {**MEAL, 'visitId':'meal2'}]}
        pending = literal_edits.pending_selection(QUESTIONS[0], current, {})
        self.assertEqual(pending['targets'], ['meal','meal2'])
        self.assertIsNone(literal_edits.pending_selection(QUESTIONS[0], CURRENT, {}))

    def test_phase_qualification_selects_the_requested_meal(self):
        current = {**CURRENT, 'places': [*CURRENT['places'], {**MEAL, 'visitId':'after-meal', 'phase':'AFTER'}]}
        plan = literal_edits.interpret('경기 후 식당만 돈카츠로 바꿔줘. 나머지는 그대로', current)
        self.assertEqual(plan['targets'], ['after-meal'])
        self.assertIsNone(literal_edits.interpret('경기 전 돈까스 식당만 돈카츠로 바꿔줘', current))

    def test_internal_cafe_keeps_its_cafe_edit_identity(self):
        cafe = {**SNACK, 'visitId':'cafe', 'category':'CAFE'}
        self.assertEqual(literal_edits.visit_kind(cafe), 'INTERNAL_CAFE')
        current = {**CURRENT, 'places':[cafe]}
        self.assertIsNone(literal_edits.interpret('카페를 메가커피로 바꿔줘', current))
        external = {**MEAL,'category':'CAFE','visitId':'external-cafe','detail':'음식점 > 카페'}
        current['places'].append(external)
        plan = literal_edits.interpret('카페를 메가커피로 바꿔줘', current)
        self.assertEqual(plan['targets'], ['external-cafe'])
        wrong = {**plan, 'targets':['cafe']}
        self.assertEqual(visit_roles.protect_target('카페를 다른 곳으로 바꿔줘',current,wrong)['targets'], ['external-cafe'])

    def test_roles_survive_public_map_roundtrip_without_trusting_client_role(self):
        source = {**CURRENT,'travelMode':'walk','legModes':{}}
        public = edit_context.validate(source)
        self.assertNotIn('detail', public['places'][1])
        restored = memory.restore_place_details(public, {'current':source})
        self.assertEqual([visit_roles.visit_kind(p) for p in restored['places']], ['FOOD','INTERNAL_FOOD','STADIUM','BAR'])

    def test_mentioning_an_internal_visit_to_preserve_it_is_not_permission_to_edit(self):
        wrong = {'operation':'replace','targets':['snack']}
        for question in ('구장 간식은 그대로 두고 식당을 초밥집으로 바꿔줘',
                         '구장 내 식당은 그대로 두고 식당을 스테이크로 바꿔줘',
                         'STATION은 유지하고 식당만 다른 곳으로 바꿔줘'):
            self.assertEqual(visit_roles.protect_target(question, CURRENT, wrong)['targets'], ['meal'])

    def test_preservation_never_erases_new_conditions_or_detailed_requests(self):
        for suffix in ('나머지는 유지해줘 그리고 주차도 가능해야 해', '알레르기 없는 곳이어야 해',
                       '나머지 일정은 바꿔줘', '나머지 일정은 삭제해줘', '왜 추천했는지도 설명해줘'):
            question = '식당을 돈카츠로 바꿔줘. ' + suffix
            with self.subTest(suffix=suffix):
                self.assertIsNone(literal_edits.interpret(question, CURRENT))
                self.assertFalse(catalog.simple_edit_matches(question, ['돈까스']))

    def test_semantic_fallback_gets_actual_visit_roles_and_full_question(self):
        question = '주차 가능한 돈카츠로 식당을 바꿔줘'
        plan = literal_edits.interpret(QUESTIONS[0], CURRENT)
        plan.pop('_literal_edit')
        with patch.object(agent, 'llm') as model:
            invoke = model.return_value.with_structured_output.return_value.invoke
            invoke.return_value = plan
            editing.interpret(question, CURRENT, [])
        payload = json.loads(invoke.call_args.args[0][1].content)
        self.assertEqual(payload['question'], question)
        self.assertEqual([p['visitKind'] for p in payload['current']['places']], ['FOOD','INTERNAL_FOOD','STADIUM','BAR'])
        self.assertIn('크림새우', payload['current']['places'][1]['detail'])

    def test_search_does_not_stop_at_unrelated_keyword_hits(self):
        def raw(place):
            return {'id':place['placeId'],'place_name':place['name'],'y':place['lat'],'x':place['lng'],
                    'category_name':place['detail'],'category_group_code':'FD6'}
        def search(invoke, args, category, **kwargs):
            return [raw(OTHER)] if args['query']=='돈카츠' else [raw(SPECIALTY)]
        with patch.object(editing.venue_policy, 'search_candidates', side_effect=search) as searched:
            options = editing.candidates(MEAL, MEAL, {'query':'돈카츠','conditions':['돈카츠']}, [])
        self.assertEqual([c.args[1]['query'] for c in searched.call_args_list], ['돈카츠','돈까스'])
        self.assertIn('cutlet', [p['placeId'] for p in options])

    def test_keyword_alone_and_internal_catalogue_never_prove_tonkatsu(self):
        for place in (SNACK, {**OTHER,'_search_query':'돈카츠'}, {**SNACK,'name':'돈카츠라고 잘못 저장된 내부 매장'}):
            self.assertFalse(catalog.menu_hint(place, '돈까스'))
        self.assertTrue(catalog.menu_hint(SPECIALTY, '돈까스'))
