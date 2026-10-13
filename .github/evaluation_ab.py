"""Trusted PR A/B control plane. No candidate harness, shell inputs or quality approval."""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EVAL = Path('backend/llm/v2/evaluation')
spec = importlib.util.spec_from_file_location('ci', HERE / 'evaluation_ci.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)
luna_spec = importlib.util.spec_from_file_location('luna', HERE / 'evaluation_luna.py')
luna = importlib.util.module_from_spec(luna_spec)
luna_spec.loader.exec_module(luna)
MODES = ('jev', 'supervisor', 'ragas', 'highlight')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def tree_hash(root):
    return digest({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts})


def quality_state(paths):
    # Unknown backend/dependency changes are relevant; only clearly unrelated changes exempt.
    protected = [p for p in paths if p.startswith(('backend/llm/v2/evaluation/', '.github/evaluation', '.github/test_evaluation', '.github/workflows/evaluation'))]
    relevant = protected or [p for p in paths if not p.startswith(('frontend/', 'docs/', 'graphify-out/')) and p not in ('README.md', '.gitignore')]
    return {'quality': 'required/not-run' if relevant else 'exempt/non-LLM',
            'harness_review_required': bool(protected)}


def validate_pr(pr, repository, number, base, head):
    if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)
            or type(number) is not int or number <= 0
            or any(not re.fullmatch(r'[0-9a-f]{40}', sha) for sha in (base, head))
            or pr.get('number') != number or pr.get('state') != 'open'
            or pr.get('base', {}).get('repo', {}).get('full_name') != repository
            or pr.get('base', {}).get('ref') not in ('develop', 'main')
            or pr.get('base', {}).get('sha') != base or pr.get('head', {}).get('sha') != head
            or not pr.get('head', {}).get('repo')
            or not re.fullmatch(r'feat/[a-z0-9-]+', pr.get('head', {}).get('ref', ''))):
        raise ValueError('PR identity/ref changed or invalid')


def api(path):
    token = os.environ.get('GH_TOKEN')
    ci.require('GH_TOKEN')
    request = urllib.request.Request('https://api.github.com/' + path,
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def preflight(mode, authorized, ref):
    if authorized != 'true' or ref not in ('refs/heads/develop', 'refs/heads/main') or mode not in MODES:
        raise ValueError('Trusted ref and explicit authorization required')
    ci.require(*{'jev': ('OPENROUTER_API_KEY',), 'supervisor': ('OPENAI_API_KEY', 'OPENROUTER_API_KEY'),
                 'ragas': ('OPENAI_API_KEY', 'OPENROUTER_API_KEY', 'QDRANT_URL', 'RAGAS_PYTHON'),
                 'highlight': ('GOOGLE_API_KEY', 'EVAL_CORPUS')}[mode])


def qdrant_hash():
    """Hash all indexed points before/after both runs; endpoint must be read-only/frozen."""
    result = {}
    for collection in ('kbo_documents_openai', 'kbo_documents_sparse'):
        offset, points, seen = None, {}, set()
        while True:
            body = {'limit': 256, 'with_payload': True, 'with_vector': True}
            if offset is not None:
                body['offset'] = offset
            request = urllib.request.Request(os.environ['QDRANT_URL'].rstrip('/') + '/collections/' + collection + '/points/scroll',
                data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', 'api-key': os.environ.get('QDRANT_API_KEY', '')})
            with urllib.request.urlopen(request, timeout=30) as response:
                page = json.load(response)['result']
            for point in page['points']:
                key = str(point['id'])
                if key in points:
                    raise ValueError('Duplicate corpus point')
                points[key] = digest(point)
            offset = page.get('next_page_offset')
            if offset is None:
                break
            marker = digest(offset)
            if marker in seen:
                raise ValueError('Nonterminating corpus scroll')
            seen.add(marker)
        if not points:
            raise ValueError('Empty RAG corpus')
        result[collection] = digest(points)
    return digest(result)


def rows_by_id(rows, selected):
    if (not isinstance(rows, list) or len(rows) != len(selected)
            or any(not isinstance(row, dict) or row.get('id') not in selected for row in rows)
            or len({row['id'] for row in rows}) != len(selected)):
        raise ValueError('Incomplete/mismatched executed IDs')
    return {row['id']: row for row in rows}


def compare(a, b, selected, metadata):
    if a['identity'] != b['identity'] or a['identity'] != metadata:
        raise ValueError('A/B frozen settings mismatch')
    aa, bb = rows_by_id(a['rows'], selected), rows_by_id(b['rows'], selected)
    output = []
    for id in selected:
        x, y = aa[id], bb[id]
        if x['input_hash'] != y['input_hash'] or x['label_hash'] != y['label_hash']:
            raise ValueError('A/B input/context/label mismatch')
        if x['errors'] or y['errors'] or set(x['scores']) != set(y['scores']) or not x['scores']:
            raise ValueError('Provider error or mismatched metrics')
        for row in (x, y):
            if any(v is not None and (type(v) not in (int, float) or not math.isfinite(v) or not -1 <= v <= 1) for v in row['scores'].values()):
                raise ValueError('Invalid metric score')
        output.append({'id': id, 'input_hash': x['input_hash'], 'label_hash': x['label_hash'],
                       'A': x['scores'], 'B': y['scores'],
                       'context_hashes': {'A': x.get('context_hash'), 'B': y.get('context_hash')},
                       'delta': {k: None if x['scores'][k] is None or y['scores'][k] is None else y['scores'][k] - x['scores'][k] for k in x['scores']}})
    metrics = {k: {'A_denominator': sum(r['A'][k] is not None for r in output),
                   'B_denominator': sum(r['B'][k] is not None for r in output),
                   'A_sum': sum(r['A'][k] for r in output if r['A'][k] is not None),
                   'B_sum': sum(r['B'][k] for r in output if r['B'][k] is not None)} for k in output[0]['A']}
    return {'execution': 'complete/comparable', 'quality': 'needs-review', 'count': len(output),
            'identity': metadata, 'cases': output, 'metrics': metrics}


def normalize(mode, out, selected, labels):
    if mode in ('jev', 'supervisor'):
        ci.jev_report(out / 'results.json', expected_ids=selected)
        report = json.loads((out / 'results.json').read_text())
        if report.get('part') != mode:
            raise ValueError('Wrong actual evaluator part')
        rows = report['results']
        normalized = []
        for id, row in rows_by_id(rows, selected).items():
            expected = ({k: labels[id][k] for k in ci_run_label_keys()} if mode == 'jev' else labels[id]['selected_tools'])
            if row.get('expected') != expected:
                raise ValueError('Independent label mismatch')
            scores = row.get('dimension_scores')
            keys = {'guard', 'course', 'capability'} if mode == 'jev' else {'name_selection', 'argument_schema', 'request_conditions'}
            held = mode == 'jev' and scores is not None and scores.get('capability') is None
            expected_status = 'held' if held and all(scores.get(k) is True for k in ('guard', 'course')) else ('passed' if scores and all(v is True for v in scores.values()) else 'failed')
            if (not isinstance(scores, dict) or set(scores) != keys or row.get('status') != expected_status
                    or any(type(v) is not bool and not (mode == 'jev' and k == 'capability' and v is None) for k, v in scores.items())):
                raise ValueError('Missing executed dimension scores')
            normalized.append({'id': id, 'input_hash': digest(labels[id]), 'label_hash': digest(expected),
                               'scores': {k: None if v is None else int(v) for k, v in scores.items()}, 'errors': row.get('error_type')})
        return normalized
    if mode == 'highlight':
        ci.highlight_report(out / 'highlight/summary.json', len(selected))
        rows = rows_by_id(json.loads((out / 'highlight/raw-rows.json').read_text()), selected)
        return [{'id': id, 'input_hash': digest(row['query']), 'label_hash': digest(row['label']),
                 'scores': row['scores'], 'errors': {'provider_error_count': len(row['api_errors'])} if row['api_errors'] else {}} for id, row in rows.items()
                if row['label'] == labels[id] and row['query'] == labels[id]['query']]
    report = json.loads((out / 'scores.json').read_text())
    if type(report.get('errors')) is not dict or report['errors'] or report.get('evaluation_model') != 'gpt-6-luna' or report.get('ragas_version') != '0.4.3':
        raise ValueError('Ragas judge failure/version mismatch')
    exports = rows_by_id([json.loads(line) for line in (out / 'export.jsonl').read_text().splitlines()], selected)
    scored = rows_by_id(report.get('results'), selected)
    normalized = []
    for id in selected:
        row, export, label = scored[id], exports[id], labels[id]
        if (row.get('user_input') != label['question'] or row.get('reference') != label['reference']
                or row.get('reference_doc_ids') != label['reference_doc_ids']
                or any(row.get(k) != export.get(k) for k in ('user_input', 'reference', 'reference_doc_ids', 'response', 'retrieved_contexts', 'retrieved_doc_ids'))
                or type(export.get('errors')) is not dict or export['errors'] or type(row.get('errors')) is not dict
                or not row.get('response') or not row.get('retrieved_contexts')
                or any(export.get('metadata', {}).get(k) != v for k, v in {'mode': 'vector', 'k': 5, 'generation_model': 'gpt-6-luna', 'embedding_model': 'text-embedding-3-small', 'scope': 'retrieval_single_generation'}.items())
                or set(row.get('scores', {})) != {'context_precision', 'context_recall', 'faithfulness', 'answer_relevancy'}
                or any(type(v) not in (int, float) or not math.isfinite(v) or not (-1 if k == 'answer_relevancy' else 0) <= v <= 1 for k, v in row['scores'].items())):
            raise ValueError('Ragas incomplete/label/export mismatch')
        # Retrieved contexts are outputs of the system under test; preserve their hashes,
        # not require equal retrieval for a retrieval-improvement PR.
        normalized.append({'id': id, 'input_hash': digest(label['question']), 'label_hash': digest(label),
                           'context_hash': digest(row['retrieved_contexts']), 'scores': row['scores'], 'errors': row.get('errors')})
    return normalized


def ci_run_label_keys():
    return ('guard', 'capabilities', 'course_request')


def run_side(root, mode, out):
    # Overlay trusted control, datasets and scorers; production imports still come from A/B.
    shutil.rmtree(root / EVAL)
    shutil.copytree(ROOT / EVAL, root / EVAL, ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(HERE / 'evaluation_ci.py', root / '.github/evaluation_ci.py')
    shutil.copy2(HERE / 'evaluation_luna.py', root / '.github/evaluation_luna.py')
    # Django's legacy shim imports the now-trusted canonical export command.
    shutil.copy2(ROOT / 'backend/llm/management/commands/export_ragas.py', root / 'backend/llm/management/commands/export_ragas.py')
    keys = ('PATH', 'HOME', 'LANG', 'TMPDIR', 'TIKTOKEN_CACHE_DIR', 'EVAL_DATASET', 'EVAL_IDS', 'EVAL_LIMIT',
            'LLM_MODEL', 'RAGAS_PYTHON', 'QDRANT_URL', 'QDRANT_API_KEY', 'EVAL_CORPUS',
            'OPENAI_API_KEY', 'OPENROUTER_API_KEY', 'GOOGLE_API_KEY')
    env = {k: os.environ[k] for k in keys if k in os.environ}
    env.update(DEEPEVAL_TELEMETRY_OPT_OUT='1', DEEPEVAL_DISABLE_DOTENV='1', DEEPEVAL_DISABLE_LEGACY_KEYFILE='1',
               PYTHON_DOTENV_DISABLED='1', RAGAS_DO_NOT_TRACK='true', LANGSMITH_TRACING='false', LANGCHAIN_TRACING_V2='false')
    if mode == 'supervisor':
        probe = "import sys,os,json,socket; sys.path.insert(0,sys.argv[1]); os.environ['DJANGO_SETTINGS_MODULE']='config.settings'; socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('network denied')); import django; django.setup(); from llm.v2.agent.common import llm; m=llm(); print(json.dumps({'model':m.model_name,'timeout':m.request_timeout,'retries':m.max_retries,'reasoning':m.reasoning_effort,'responses':m.use_responses_api,'max_tokens':m.max_tokens}))"
        observed = json.loads(subprocess.check_output([sys.executable, '-I', '-c', probe, str(root / 'backend')], cwd=root, env=env, text=True, timeout=60))
        if any(observed.get(k) != v for k, v in {'model': 'gpt-6-luna', 'timeout': 25, 'retries': 0, 'reasoning': 'medium', 'responses': True}.items()) or type(observed.get('max_tokens')) is not int:
            raise ValueError('Observed supervisor config changed')
        (out.parent / (out.name + '-config.json')).write_text(json.dumps(observed))
    with (out.parent / (out.name + '-private.log')).open('x') as log:
        subprocess.run([sys.executable, '-I', str(root / '.github/evaluation_ci.py'), mode, '--output', str(out)],
                       cwd=root, env=env, stdout=log, stderr=log, check=True, timeout=1500)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=MODES)
    parser.add_argument('--pr', type=int, required=True)
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', required=True)
    parser.add_argument('--a', type=Path, required=True)
    parser.add_argument('--b', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    summary = {'execution': 'not-run', 'quality': 'required/not-run'}
    try:
        preflight(args.mode, os.environ.get('AUTHORIZED'), os.environ.get('GITHUB_REF'))
        repository = os.environ['GITHUB_REPOSITORY']
        # Validate shape before interpolating an API path or invoking any code.
        validate_pr({'number': args.pr, 'state': 'open', 'base': {'repo': {'full_name': repository}, 'ref': 'develop', 'sha': args.base},
                     'head': {'sha': args.head, 'repo': True, 'ref': 'feat/validated'}}, repository, args.pr, args.base, args.head)
        target = f'repos/{repository}/pulls/{args.pr}'
        validate_pr(api(target), repository, args.pr, args.base, args.head)
        for root, sha in ((args.a, args.base), (args.b, args.head)):
            actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
            if actual != sha or root.resolve() == ROOT:
                raise ValueError('Snapshot SHA/root mismatch')
        dataset = os.environ.get('EVAL_DATASET', 'seed')
        if dataset not in ('seed', 'boundary', 'first-action'):
            raise ValueError('Unknown dataset')
        if args.mode == 'highlight':
            data = json.loads((ROOT / EVAL / 'highlight_deepeval_sample.json').read_text())['cases']
            selected = [r['id'] for r in data]
            corpus = tree_hash(Path(os.environ['EVAL_CORPUS']))
        elif args.mode == 'ragas':
            selected = ci.ids(os.environ.get('EVAL_IDS', ''), 3)
            if not set(selected) <= {'r16', 'r25', 'v04'}:
                raise ValueError('Only reviewed Ragas IDs')
            data = [json.loads(line) for name in ('rule', 'venue') for line in (ROOT / EVAL / f'golden/golden_{name}.jsonl').read_text().splitlines() if line.strip()]
            corpus = qdrant_hash()
        else:
            selected = ci.ids(os.environ.get('EVAL_IDS', ''))
            if dataset == 'first-action':
                # existing reviewed first action source, not generated labels
                ci.execute([str(EVAL / 'remaining.py'), 'supervisor-input', '--output', str(out / 'frozen-input.json')], offline=True)
                data = json.loads((out / 'frozen-input.json').read_text())
            else:
                data = json.loads((ROOT / EVAL / ('reviewed_boundary.json' if dataset == 'boundary' else 'seed.json')).read_text())
            corpus = 'independently-seeded-contexts'
        labels = {r['id']: r for r in data if r['id'] in selected}
        if set(labels) != set(selected):
            raise ValueError('Selected labels absent')
        os.environ['LLM_MODEL'] = 'gpt-6-luna'
        os.environ['EVAL_LIMIT'] = str(len(selected)) if args.mode == 'ragas' else '3'
        identity = {'base': args.base, 'head': args.head, 'mode': args.mode, 'dataset_hash': digest(labels),
                    'harness_hash': digest({'evaluation': tree_hash(ROOT / EVAL), 'control': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (HERE / 'evaluation_ci.py', HERE / 'evaluation_ab.py', HERE / 'evaluation_luna.py')}}), 'corpus_hash': corpus,
                    'models': {'jev': 'jev-1.13', 'supervisor': 'gpt-6-luna', 'ragas_judge': 'gpt-6-luna',
                               'ragas_generation': 'gpt-6-luna', 'embedding': 'text-embedding-3-small', 'highlight': 'gemini-3.8-flash'},
                    'settings': {'dataset': dataset, 'ids': selected, 'ragas_k': 5, 'ragas_mode': 'vector', 'judge_sampling': 'omitted', 'judge_reasoning': 'medium', 'ragas_requests': luna.SETTINGS}}
        frozen_harness = tree_hash(ROOT / EVAL)
        sides = []
        for name, root in (('A', args.a), ('B', args.b)):
            validate_pr(api(target), repository, args.pr, args.base, args.head)
            run_side(root.resolve(), args.mode, out / name)
            if tree_hash(ROOT / EVAL) != frozen_harness or tree_hash(root / EVAL) != frozen_harness:
                raise ValueError('Trusted evaluation harness mutated')
            sides.append({'identity': identity, 'rows': normalize(args.mode, out / name, selected, labels)})
            summary['executed_evidence'] = [{'side': side, 'rows': [{'id': row['id'], 'input_hash': row['input_hash'], 'label_hash': row['label_hash'],
                'scores': {k: v if v is None or type(v) in (int, float) and math.isfinite(v) else None for k, v in row['scores'].items()},
                'error_count': len(row['errors']) if isinstance(row['errors'], dict) else int(bool(row['errors']))} for row in evidence['rows']]}
                for side, evidence in zip(('A', 'B'), sides)]
            if args.mode == 'highlight' and tree_hash(Path(os.environ['EVAL_CORPUS'])) != corpus:
                raise ValueError('Corpus changed')
            if args.mode == 'ragas' and qdrant_hash() != corpus:
                raise ValueError('Corpus changed')
        if args.mode == 'supervisor':
            configs = [json.loads((out / (side + '-config.json')).read_text()) for side in ('A', 'B')]
            if configs[0] != configs[1]:
                raise ValueError('Observed supervisor config mismatch')
            identity['observed_supervisor_config'] = configs[0]
        if args.mode == 'highlight':
            versions = [json.loads((out / side / 'highlight/summary.json').read_text()).get('model_versions') for side in ('A', 'B')]
            if not versions[0] or versions[0] != versions[1]:
                raise ValueError('Actual highlight model versions missing/mismatched')
            identity['actual_model_versions'] = versions[0]
        summary = compare(*sides, selected, identity)
        validate_pr(api(target), repository, args.pr, args.base, args.head)
    except Exception as exc:
        summary.update(execution='failed', quality='required/not-run', error_type=type(exc).__name__)
        raise RuntimeError('A/B execution failed; sanitized evidence records error type') from None
    finally:
        (out / 'ab-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
