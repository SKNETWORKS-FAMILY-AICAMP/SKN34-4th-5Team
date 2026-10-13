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
