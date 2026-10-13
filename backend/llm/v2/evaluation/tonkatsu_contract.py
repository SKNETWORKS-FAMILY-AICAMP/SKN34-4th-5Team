"""Offline meal replacement contracts; fixed evidence, no provider/semantic judge."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
MEAL = dict(visitId='meal', label='1', placeId='meal', name='기존 짜장면 식당',
            category='FOOD', detail='음식점 > 중식 > 중국요리', phase='BEFORE', lat=37.510, lng=127.081)
SNACK = {**MEAL, 'visitId':'snack', 'label':'2', 'placeId':'stadium-facility:snack',
         'name':'STATION', 'detail':'구장 내부 먹거리 · 크림새우'}
BAR = {**MEAL, 'visitId':'bar', 'label':'4', 'placeId':'bar', 'name':'기존 술집',
       'detail':'음식점 > 술집 > 호프,요리주점', 'phase':'AFTER'}
GAME = {**MEAL, 'visitId':'game', 'label':'3', 'placeId':'game', 'name':'잠실야구장', 'category':'STADIUM', 'phase':'GAME'}
CURRENT = dict(stadiumCode='JAMSIL', places=[MEAL,SNACK,GAME,BAR], game={'date':'2026-10-16','time':'17:00'})
SPECIALTY = {**MEAL, 'placeId':'cutlet', 'name':'시험 돈카츠 전문점', 'detail':'음식점 > 일식 > 돈까스,우동'}
OTHER = {**MEAL, 'placeId':'soup', 'name':'시험 국밥집', 'detail':'음식점 > 한식 > 국밥'}
QUESTIONS = [
    '식당이 좀 별로다. 돈카츠 가게로 수정해줘',
    '경기 전 자장면 식당만 돈카츠 가게로 바꿔줘. 나머지 일정은 유지해줘',
    '식당을 돈가스로 바꿔줘. 나머지는 그대로 둬',
]
CASES = [
    *[{'id':f'replace-{i}', 'kind':'interpret', 'question':q, 'expected':['meal']} for i,q in enumerate(QUESTIONS)],
    {'id':'only-internal-bar', 'kind':'interpret', 'question':QUESTIONS[0], 'without_meal':True, 'expected':None},
    {'id':'parking-preserved', 'kind':'interpret', 'question':'돈카츠로 바꿔줘. 주차도 가능해야 해', 'expected':None},
    {'id':'two-meals-ambiguous', 'kind':'interpret', 'question':QUESTIONS[0], 'two_meals':True, 'expected':None},
    {'id':'preserve-phrase-selection', 'kind':'choose', 'question':QUESTIONS[1], 'expected':'cutlet'},
    {'id':'menu-alias-selection', 'kind':'choose', 'question':'식당을 돈가스로 바꿔줘', 'alias':True, 'expected':'cutlet'},
    {'id':'station-not-cutlet', 'kind':'choose', 'question':'돈카츠로 바꿔줘', 'no_specialty':True, 'expected':None},
    {'id':'steak-protect-internal', 'kind':'apply', 'question':'식당을 스테이크 가게로 바꿔줘', 'target':'snack', 'expected':'meal'},
    {'id':'sushi-protect-internal', 'kind':'apply', 'question':'식당을 초밥집으로 바꿔줘', 'target':'snack', 'expected':'meal'},
    {'id':'meal-protect-bar', 'kind':'apply', 'question':'식당을 다른 곳으로 바꿔줘', 'target':'bar', 'expected':'meal'},
    {'id':'explicit-internal', 'kind':'apply', 'question':'구장 안 간식을 다른 매장으로 바꿔줘', 'target':'snack', 'expected':'snack'},
    {'id':'explicit-number', 'kind':'apply', 'question':'2번을 다른 식당으로 바꿔줘', 'target':'snack', 'expected':'snack'},
    {'id':'explicit-bar', 'kind':'apply', 'question':'술집을 다른 곳으로 바꿔줘', 'target':'bar', 'expected':'bar'},
    {'id':'preserve-bar-role', 'kind':'apply', 'question':'술집은 그대로 두고 식당을 돈카츠로 바꿔줘', 'target':'bar', 'expected':'meal'},
    {'id':'preserve-number-name', 'kind':'apply', 'question':'2번 STATION은 그대로 두고 식당을 돈카츠로 바꿔줘', 'target':'snack', 'expected':'meal'},
    {'id':'explicit-bar-name', 'kind':'apply', 'question':'기존 술집을 다른 곳으로 바꿔줘', 'target':'bar', 'expected':'bar'},
    {'id':'bar-clarification-role', 'kind':'clarify', 'target':'bar', 'expected':'술집은 유지했어요'},
    {'id':'cafe-clarification-role', 'kind':'clarify', 'target':'snack', 'expected':'구장 내부 카페는 유지했어요'},
    {'id':'bar-brand-mixed', 'kind':'ratings', 'expected':{'ids':['9101'], 'reads':1}},
    {'id':'food-brand-mixed-no-io', 'kind':'ratings', 'food':True, 'expected':{'ids':['9102'], 'reads':0}},
    {'id':'bar-jibun-only', 'kind':'ratings', 'jibun':True, 'expected':{'ids':['9101'], 'reads':1}},
    {'id':'bar-other-branch', 'kind':'ratings', 'jibun':True, 'other_branch':True, 'expected':{'ids':[], 'reads':1}},
    {'id':'no-external-meal', 'kind':'apply', 'question':'식당을 초밥집으로 바꿔줘', 'target':'snack', 'without_meal':True, 'expected':None},
    {'id':'ambiguous-external-meals', 'kind':'apply', 'question':'식당을 초밥집으로 바꿔줘', 'target':'snack', 'two_meals':True, 'expected':None},
]


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True)


def observe(case):
    from llm.v1.rag.course import literal_edits, editing, evidence_memory, recommendation_policy
    current = deepcopy(CURRENT)
    if case.get('without_meal'): current['places'].remove(current['places'][0])
    if case.get('two_meals'): current['places'].append({**MEAL,'visitId':'meal2'})
    if case['kind']=='clarify':
        from llm.v1.rag.course import visit_roles
        target = deepcopy(BAR if case['target']=='bar' else SNACK)
        if case['target']=='snack': target.update(category='CAFE')
        plan = visit_roles.protect_target('식당을 바꿔줘' if case['target']=='bar' else '카페를 바꿔줘',
            {'places':[target]}, {'operation':'replace','targets':[target['visitId']]})
        return next((s.strip() for s in plan.get('clarification','').split('.') if '유지했어요' in s), '')
    if case['kind']=='ratings':
        from django.core.cache.backends.locmem import LocMemCache
        from llm.v1.rag.course import agent, bar_ratings, place_quality, quality_research, visit_requests
        cache = LocMemCache(case['id'], {})
        cache.clear()
        raw = {'id':'9101','place_name':'시험브랜드','x':127.081,'y':37.510,
               'category_group_code':'FD6','category_name':'음식점 > 술집 > 호프,요리주점',
               'address_name':'서울 송파구 잠실동 1'}
        if not case.get('jibun'): raw['road_address_name']='서울 송파구 시험로 1'
        food = {**raw,'id':'9102','category_name':'음식점 > 한식','place_name':'시험브랜드 식당'}
        visit = {'kind':'FOOD' if case.get('food') else 'BAR','phase':'AFTER','id':'AFTER:0',
                 'expression':'시험브랜드 식당' if case.get('food') else '시험브랜드 술집',
                 'query':'시험브랜드','conditions':[],'internal':False}
        selector = visit_requests.Selector([visit], {}, 'JAMSIL')
        def profile(item, url, reader, **kwargs):
            return {'place':item,'url':url,'rating':4.,'ratingCount':1}
        real_save = bar_ratings.save
        def save(item, proof):
            real_save({**item,'name':'다른 지점'} if case.get('other_branch') else item, proof)
        with patch.object(place_quality,'verdict_cache',return_value=cache), \
                patch.object(place_quality,'catalogue',return_value={}), \
                patch('travel.course_place_warmer.remember'), \
                patch.object(agent,'invoke_domain_tool',return_value={'places':[raw] if case.get('jibun') else [raw,food]}), \
                patch.object(editing.availability,'check',return_value=''), \
                patch.object(quality_research,'discover',return_value=['https://www.diningcode.com/profile.php?rid=Test123456']) as reads, \
                patch.object(quality_research,'profile',side_effect=profile), \
                patch.object(quality_research,'interiors',side_effect=AssertionError('No model IO')), \
                patch.object(bar_ratings,'save',side_effect=save), \
                recommendation_policy.request('일반 코스'), place_quality.request_scope('JAMSIL'):
            result = selector.search(MEAL)
        return {'ids':[p['placeId'] for p in result],'reads':reads.call_count}
    if case['kind']=='interpret':
        plan=literal_edits.interpret(case['question'],current)
        return plan['targets'] if plan else None
    if case['kind']=='apply':
        plan=dict(operation='replace', targets=[case['target']], reference='', position='after', query='식당',
                  conditions=[], category='', clarification='', preferences=[], forget_conditions=[])
        current.update(travelMode='walk',legModes={})
        with patch.object(editing, 'candidates',return_value=[]) as candidates, \
                patch('llm.v1.rag.course.agent.requested_date', return_value=None), \
                patch('llm.v1.rag.course.agent.stadium_anchor', return_value=GAME), \
                patch.object(editing.place_quality, 'missing_notice', return_value=''):
            editing.answer(case['question'], [], current, 'JAMSIL', None, parsed=plan)
        return candidates.call_args.args[0]['visitId'] if candidates.called else None
    plan={'query':'돈가스' if case.get('alias') else '돈카츠',
          'conditions':['돈가스' if case.get('alias') else '돈카츠'], 'course_only':True}
    # Returned provider candidates contain an unrelated nearer restaurant.
    # Keep real venue policy; the internal fixture has no current permission.
    with patch.object(recommendation_policy,'balanced',return_value=True), \
            patch.object(editing.availability,'check',return_value=None), \
            patch.object(evidence_memory,'enrich',return_value=[]):
        result=editing.choose([OTHER,SNACK]+([] if case.get('no_specialty') else [SPECIALTY]),plan,case['question'])
    return result.get('placeId') if result else None


def score(rows, framework):
    if framework=='deep':
        from deepeval import evaluate
        from deepeval.metrics import BaseMetric
        from deepeval.test_case import LLMTestCase
        from deepeval.evaluate import AsyncConfig, CacheConfig, DisplayConfig
        class Contract(BaseMetric):
            threshold=1.0
            async_mode=False
            strict_mode=True
            evaluation_model='deterministic contract; no judge'
            @property
            def __name__(self): return 'meal_replacement_contract'
            def measure(self,c,*args,**kwargs):
                self.score=float(c.actual_output==c.expected_output)
                self.success=self.score==1
                self.reason='Exact target/candidate identity, not semantic quality'
                return self.score
            async def a_measure(self,c,*args,**kwargs): return self.measure(c)
            def is_successful(self): return self.success
        result=evaluate([LLMTestCase(input=r['id'],actual_output=encoded(r['actual']),expected_output=encoded(r['expected'])) for r in rows],
            metrics=[Contract()],async_config=AsyncConfig(run_async=False),cache_config=CacheConfig(write_cache=False,use_cache=False),
            display_config=DisplayConfig(show_indicator=False,print_results=False,inspect_after_run=False))
        return [r.metrics_data[0].score for r in result.test_results]
    from ragas import EvaluationDataset, RunConfig, SingleTurnSample, evaluate
    from ragas.metrics.base import MetricType, SingleTurnMetric
    class Contract(SingleTurnMetric):
        def __init__(self):
            super().__init__(name='meal_replacement_contract',_required_columns={MetricType.SINGLE_TURN:{'response','reference'}})
        def init(self,run_config): pass
        async def _single_turn_ascore(self,sample,callbacks): return float(sample.response==sample.reference)
    result=evaluate(EvaluationDataset(samples=[SingleTurnSample(response=encoded(r['actual']),reference=encoded(r['expected'])) for r in rows]),
        metrics=[Contract()],run_config=RunConfig(max_workers=1,max_retries=0),raise_exceptions=True,show_progress=False)
    return [r['meal_replacement_contract'] for r in result.scores]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('export','score','scorer-check'))
    parser.add_argument('--input',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    os.environ.update(PYTHON_DOTENV_DISABLED='1',DEEPEVAL_TELEMETRY_OPT_OUT='YES',DEEPEVAL_DISABLE_DOTENV='1',
                      RAGAS_DO_NOT_TRACK='true',LANGSMITH_TRACING='false',WEB_RESEARCH_ENABLED='false')
    def denied(*a,**kw): raise RuntimeError('Network forbidden in meal contract')
    socket.socket.connect=socket.socket.connect_ex=socket.create_connection=denied
    if args.mode=='scorer-check':
        rows=[{'id':'good','actual':'cutlet','expected':'cutlet'},{'id':'bad','actual':'station','expected':'cutlet'}]
        assert score(rows,'deep')==score(rows,'ragas')==[1.0,0.0]
        return
    digest=hashlib.sha256(encoded(CASES).encode()).hexdigest()
    if args.mode=='export':
        sys.path.insert(0,str(ROOT/'backend'))
        os.environ['DJANGO_SETTINGS_MODULE']='config.settings'
        import django
        django.setup()
        rows=[{'id':c['id'],'expected':c['expected'],'actual':observe(c)} for c in CASES]
        report={'scope':__doc__,'cases_sha256':digest,'results':rows}
        framework='deep'
    else:
        report=json.loads(args.input.read_text(encoding='utf-8'))
        rows=report['results']
        if report.get('cases_sha256')!=digest or [(r['id'],r['expected']) for r in rows]!=[(c['id'],c['expected']) for c in CASES]:
            raise ValueError('Frozen cases mismatch')
        framework='ragas'
    values=score(rows,framework)
    report[framework+'_scores']=values
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(encoded({'framework':framework,'cases':len(rows),'passed':sum(v==1 for v in values)}))
    raise SystemExit(any(v!=1 for v in values))


if __name__=='__main__': main()
