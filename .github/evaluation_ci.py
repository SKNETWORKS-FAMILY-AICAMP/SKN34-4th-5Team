"""CI wiring only; existing evaluators own datasets, scoring and provider limits."""
import argparse
import json
import math
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EVAL = 'backend/llm/v2/evaluation/'
FREE = [
    ['backend/llm/v2/tests/run_attachment_offline.py', 'llm.tests.test_course_tonkatsu',
     'llm.tests.test_course_catalog_conditions', 'llm.tests.test_course_profile_features',
     'llm.tests.test_course_literal_edits', 'llm.tests.test_course_audit_repairs.CourseSelectionRepairTest',
     'llm.tests.test_course_bar_ratings', 'llm.tests.test_course_quality_research',
     'llm.tests.test_course_recommendation_policy', 'llm.tests.test_course_visit_requests',
     'llm.tests.test_course_candidates',
     'llm.tests.test_llm_temperature.ResponsesPayloadTest.test_v2_agents_use_the_single_v2_model_factory',
     'llm.tests.test_llm_temperature.ResponsesPayloadTest.test_luna_constructors_omit_temperature'],
    [EVAL + 'tonkatsu_contract.py', 'export', '--output', 'tonkatsu.json'],
    [EVAL + 'test_course_dataset.py', '-v'],
    [EVAL + 'run.py', 'offline', '--output', 'seed.json'],
    [EVAL + 'run.py', 'offline', '--dataset', EVAL + 'reviewed_boundary.json', '--output', 'boundary.json'],
    [EVAL + 'test_evaluation.py', '-v'],
    ['backend/llm/v2/tests/run_attachment_offline.py', 'llm.v2.evaluation.test_attachment_shared'],
    [EVAL + 'test_remaining.py', '-v'],
    [EVAL + 'remaining.py', 'validate'],
    ['backend/llm/v2/tests/run_attachment_offline.py'],
    ['backend/llm/v2/tests/run_attachment_offline.py', 'llm.v2.evaluation.test_eval_ragas'],
    ['backend/llm/v2/tests/run_attachment_offline.py', 'llm.tests.test_domain_tools.ExternalDomainToolAdapterTest'],
    [EVAL + 'weather_contract.py', 'export', '--output', 'weather.json'],
    ['backend/llm/v2/tests/run_attachment_offline.py', 'llm.tests.test_chat_eval'],
]


def execute(args, python=sys.executable, offline=False, accept_quality=False):
    command = [python, '-I']
    if offline or args[0] == 'backend/manage.py':
        command += [str(Path(__file__).resolve()), 'offline-exec' if offline else 'django-exec']
    result = subprocess.run(command + args, cwd=ROOT, check=False)
    if result.returncode not in ((0, 1) if accept_quality else (0,)):
        raise RuntimeError('Evaluation command failed')
    return result.returncode


def jev_report(path, offline=False, expected_ids=None):
    report = json.loads(path.read_text())
    rows = report.get('results', [])
    dimensions = report.get('dimensions')
    if ('status' in report or report.get('part') not in (('offline',) if offline else ('jev', 'supervisor'))
            or not isinstance(rows, list) or not rows
            or type(report.get('selected_count')) is not int
            or type(report.get('executed_count')) is not int
            or len(rows) != report['selected_count'] or len(rows) != report['executed_count']
            or any(not isinstance(r, dict) or r.get('status') not in ('passed', 'failed', 'held')
                   or 'error_type' in r or not isinstance(r.get('id'), str) or not r['id'] for r in rows)
            or len({r['id'] for r in rows}) != len(rows)
            or (expected_ids is not None and {r['id'] for r in rows} != set(expected_ids))
            or not isinstance(dimensions, dict) or type(dimensions.get('execution_errors')) is not int or dimensions['execution_errors'] != 0
            or type(dimensions.get('semantic_count')) is not int
            or not 0 <= dimensions['semantic_count'] <= len(rows)
            or any(not isinstance(dimensions.get(key), dict) for key in ('guard', 'course', 'capability', 'contract'))):
        raise RuntimeError('Incomplete evaluation or execution error')
    for key, counters in {'guard': ('evaluated', 'held', 'exact', 'false_allow', 'false_deny'),
                          'course': ('evaluated', 'held', 'exact'),
                          'capability': ('evaluated', 'held', 'contract_passed', 'exact'),
                          'contract': ('passed',)}.items():
        if any(type(dimensions[key].get(counter)) is not int or not 0 <= dimensions[key][counter] <= len(rows) for counter in counters):
            raise RuntimeError('Malformed evaluation dimensions')
    if offline and any(r['status'] != 'passed' for r in rows):
        raise RuntimeError('Deterministic regression failed')
    return {'selected': report['selected_count'], 'executed': len(rows),
            'passed': sum(r['status'] == 'passed' for r in rows),
            'failed': sum(r['status'] == 'failed' for r in rows),
            'held': sum(r['status'] == 'held' for r in rows),
            'dimensions': report['dimensions']}


def remaining_report(path):
    report = json.loads(path.read_text())
    manifest = json.loads((ROOT / EVAL / 'remaining_manifest.json').read_text())
    splits = {id: split for split in ('dev', 'heldout') for id in manifest[split]}
    rows = report.get('results')
    if (report.get('scope') != 'supplied structured outputs only, not production/live quality'
            or not isinstance(rows, list) or len(rows) != 60
            or any(not isinstance(r, dict) or set(r) != {'dimension_scores', 'id', 'split', 'contract_score', 'reason', 'semantic_correctness'}
                   or r.get('id') not in splits or r.get('split') != splits[r['id']]
                   or type(r.get('contract_score')) not in (int, float) or r['contract_score'] not in (0, 1)
                   or not isinstance(r.get('reason'), str) or r.get('semantic_correctness') != 'not_measured'
                   or not isinstance(r.get('dimension_scores'), dict)
                   or not set(r['dimension_scores']) <= {'name_selection', 'argument_schema', 'request_conditions'}
                   or any(type(v) is not bool for v in r['dimension_scores'].values()) for r in rows)
            or {r['id'] for r in rows} != set(splits)):
        raise RuntimeError('Malformed or incomplete structured-output evaluation')
    return {'cases': len(rows), 'passed': sum(r['contract_score'] == 1 for r in rows), 'semantic_correctness': 'not_measured'}


def highlight_report(path, expected_count, replay=False):
    report = json.loads(path.read_text())
    count_key = 'replayed_cases' if replay else 'attempted'
    inconclusive = 'inconclusive_cases' if replay else 'inconclusive_selection_cases'
    if (type(report.get(count_key)) is not int or report[count_key] != expected_count
            or type(report.get('api_error_cases')) is not int or report['api_error_cases'] != 0
            or type(report.get(inconclusive)) is not int or report[inconclusive] != 0
            or (replay and (report.get('framework') != 'ragas'
                or any(type(report.get(key)) is not int or report[key] != 0 for key in ('additional_provider_calls', 'judge_calls'))))
            or (not replay and (report.get('planned') != expected_count or report.get('corpus_unchanged') is not True))):
        raise RuntimeError('Highlight incomplete/provider error/integrity failure')
    for key in ('recall_at_3', 'final_selection', 'original_time', 'no_match', 'no_match_precision'):
        metric = report.get(key)
        if (not isinstance(metric, dict) or type(metric.get('passed')) is not int
                or type(metric.get('denominator')) is not int
                or not 0 <= metric['passed'] <= metric['denominator'] <= expected_count
                or 'rate' not in metric
                or (metric['denominator'] == 0 and metric['rate'] is not None)
                or (metric['denominator'] > 0 and (type(metric['rate']) not in (int, float)
                    or not math.isfinite(metric['rate']) or abs(metric['rate'] - metric['passed'] / metric['denominator']) > 1e-12))):
            raise RuntimeError('Malformed highlight metric')
    return report


def require(*names):
    if any(not os.environ.get(name) for name in names):
        raise ValueError('Required evaluation configuration missing: ' + ', '.join(names))


def ids(value, maximum=5):
    values = value.split(',')
    if not values or len(values) > maximum or len(set(values)) != len(values) or any(not v or not all(c.isalnum() or c in '-_' for c in v) for v in values):
        raise ValueError('Select unique explicit IDs within the sample cap')
    return values


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ('offline-exec', 'django-exec'):
        rest = sys.argv[2:]
        if sys.argv[1] == 'offline-exec':
            def denied(*args, **kwargs):
                raise RuntimeError('CI offline regression attempted network access')
            socket.socket.connect = denied
            socket.socket.connect_ex = denied
            socket.create_connection = denied
        elif not rest or rest[0] != 'backend/manage.py':
            raise ValueError('Django launcher only supports backend/manage.py')
        sys.argv = rest
        sys.path.insert(0, str(ROOT / 'backend'))
        sys.path.insert(0, str(Path(rest[0]).resolve().parent))
        runpy.run_path(rest[0], run_name='__main__')
        return
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['free', 'highlight-free', 'jev', 'supervisor', 'generate', 'remaining-score', 'ragas', 'ragas-score', 'highlight', 'highlight-replay'])
    parser.add_argument('--output', type=Path)
    options, rest = parser.parse_known_args()
    if rest or options.output is None:
        parser.error('Specify only --output with a new output directory')
    out = options.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    summary = {'mode': options.mode}
    try:
        if options.mode == 'free':
            for original in FREE:
                command = original.copy()
                if '--output' in command:
                    command[-1] = str(out / command[-1])
                execute(command, offline=True)
            summary.update(seed=jev_report(out / 'seed.json', True), boundary=jev_report(out / 'boundary.json', True))
        elif options.mode == 'highlight-free':
            execute([EVAL + 'test_highlight_deepeval.py', '-v'], offline=True)
            execute([EVAL + 'test_relocation.py', '-v'], offline=True)
            execute([EVAL + 'weather_contract.py', 'scorer-check'], offline=True)
            execute([EVAL + 'tonkatsu_contract.py', 'scorer-check'], offline=True)
        elif options.mode in ('jev', 'supervisor'):
            require('OPENROUTER_API_KEY')
            if options.mode == 'supervisor':
                require('OPENAI_API_KEY')
            selected = ids(os.environ.get('EVAL_IDS', ''))
            dataset = os.environ.get('EVAL_DATASET', 'seed')
            if dataset not in ('seed', 'boundary', 'first-action') or (options.mode == 'supervisor' and dataset == 'boundary'):
                raise ValueError('Dataset incompatible with evaluation mode')
            command = [EVAL + 'run.py', options.mode, '--ids', ','.join(selected), '--output', str(out / 'results.json')]
            if dataset == 'boundary':
                command += ['--dataset', EVAL + 'reviewed_boundary.json']
            elif dataset == 'first-action':
                if options.mode != 'supervisor':
                    raise ValueError('first-action requires supervisor')
                execute([EVAL + 'remaining.py', 'supervisor-input', '--output', str(out / 'input.json')], offline=True)
                command += ['--dataset', str(out / 'input.json')]
            code = execute(command, accept_quality=True)
            result = jev_report(out / 'results.json', expected_ids=selected)
            if code != int(bool(result['failed'] or result['held'])):
                raise RuntimeError('Evaluator exit does not match quality report')
            summary.update(result)
        elif options.mode == 'generate':
            require('OPENAI_API_KEY')
            count = int(os.environ.get('EVAL_CONTEXTS', '1'))
            if not 1 <= count <= 3:
                raise ValueError('contexts must be 1..3')
            execute([EVAL + 'run.py', 'generate', '--contexts', str(count), '--output', str(out / 'unreviewed.json')])
            summary.update(contexts=count, review_status='candidate/unreviewed', quality_evaluation=False)
        elif options.mode == 'remaining-score':
            require('EVAL_SAVED_INPUT')
            code = execute([EVAL + 'remaining.py', 'score', '--actual', os.environ['EVAL_SAVED_INPUT'], '--output', str(out / 'remaining.json')], offline=True, accept_quality=True)
            result = remaining_report(out / 'remaining.json')
            if code != int(result['passed'] != result['cases']):
                raise RuntimeError('Scorer exit does not match quality report')
            summary.update(result)
        elif options.mode in ('ragas', 'ragas-score'):
            require('OPENAI_API_KEY', 'RAGAS_PYTHON')
            limit = int(os.environ.get('EVAL_LIMIT', '3'))
            if not 1 <= limit <= 3:
                raise ValueError('Ragas sample cap is 3')
            source = os.environ.get('EVAL_SAVED_INPUT', '')
            if options.mode == 'ragas':
                require('OPENROUTER_API_KEY', 'QDRANT_URL')
                selected = ids(os.environ.get('EVAL_IDS', ''), 3)
                if not set(selected) <= {'r16', 'r25', 'v04'} or len(selected) > limit:
                    raise ValueError('Only source-reviewed Ragas IDs r16,r25,v04 within limit')
                source = str(out / 'export.jsonl')
                golden = out / 'golden.jsonl'
                golden.write_text(''.join((ROOT / (EVAL + 'golden/golden_' + name + '.jsonl')).read_text() for name in ('rule', 'venue')))
                execute(['.github/evaluation_luna.py', 'export', 'export_ragas', '--golden', str(golden), '--ids', ','.join(selected), '--limit', str(limit), '--k', '5', '--mode', 'vector', '--generation-model', 'gpt-6-luna', '--output', source])
            if not source or not Path(source).is_file():
                raise ValueError('Saved Ragas export is required')
            execute(['.github/evaluation_luna.py', 'judge', '--input', source, '--limit', str(limit), '--evaluation-model', 'gpt-6-luna', '--output', str(out / 'scores.json')], python=os.environ['RAGAS_PYTHON'])
            summary.update(json.loads((out / 'scores.json').read_text())['summary'])
            adapter = runpy.run_path(str(ROOT / '.github/evaluation_luna.py'))
            summary.update(generation_model='gpt-6-luna' if options.mode == 'ragas' else 'saved-export/not-generated',
                           judge_model='gpt-6-luna', request_settings=adapter['SETTINGS'])
        else:
            if options.mode == 'highlight':
                require('GOOGLE_API_KEY', 'EVAL_CORPUS')
                execute([EVAL + 'highlight_deepeval.py', '--dataset', EVAL + 'highlight_deepeval_sample.json', '--corpus', os.environ['EVAL_CORPUS'], '--output', str(out / 'highlight'), '--run-provider'])
            else:
                require('EVAL_SAVED_INPUT')
                source = Path(os.environ['EVAL_SAVED_INPUT'])
                rows = json.loads(source.read_text())
                if (not isinstance(rows, list) or not 1 <= len(rows) <= 10
                        or any(not isinstance(r, dict) or not isinstance(r.get('id'), str) or not r['id']
                               or not isinstance(r.get('label'), dict) or not isinstance(r.get('result'), dict)
                               or not isinstance(r.get('query'), str) or not isinstance(r.get('api_errors'), list) for r in rows)
                        or len({r['id'] for r in rows}) != len(rows)):
                    raise ValueError('Replay requires 1..10 unique saved cases')
                execute([EVAL + 'highlight_ragas.py', '--raw-rows', str(source), '--output', str(out / 'highlight')], offline=True)
            expected_count = len(rows) if options.mode == 'highlight-replay' else len(json.loads((ROOT / EVAL / 'highlight_deepeval_sample.json').read_text())['cases'])
            summary.update(highlight_report(out / 'highlight/summary.json', expected_count, replay=options.mode == 'highlight-replay'))
    except Exception as exc:
        # No exception strings, provider text, paths or environment in uploaded artifacts.
        summary['execution_error'] = type(exc).__name__
        raise
    finally:
        (out / 'ci-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
