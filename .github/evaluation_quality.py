"""Trusted human quality decision; consumes executed workflow evidence, never PR code."""
import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('ab', HERE / 'evaluation_ab.py')
ab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ab)


def required_modes(paths):
    # ponytail: conservative all-mode scope; narrow only after adopted path-to-mode policy.
    return set(ab.MODES) if ab.quality_state(paths)['quality'] == 'required/not-run' else set()


def decision_state(decision, required, evidence, protected=False, protected_approved=False):
    if decision == 'approve' and protected and not protected_approved:
        raise ValueError('Independent protected baseline/harness review approval required')
    if decision == 'reject':
        return 'failure'
    if decision == 'not-applicable':
        if required or evidence:
            raise ValueError('Relevant changes cannot be exempted')
        return 'success'
    if decision != 'approve' or not required or set(evidence) != required:
        raise ValueError('Approval requires every classified mode')
    return 'success'


def validate_evidence(run, jobs, report, mode, repository, trusted_sha, base, head, pr):
    if (run.get('event') != 'workflow_dispatch' or run.get('status') != 'completed'
            or run.get('conclusion') != 'success' or run.get('head_branch') not in ('develop', 'main')
            or run.get('head_sha') != trusted_sha or run.get('repository', {}).get('full_name') != repository
            or run.get('path') != '.github/workflows/evaluation-pr-ab.yml'
            or run.get('display_title') != f'Provider A/B pr={pr} mode={mode} base={base} head={head}'
            or not all(any(j.get('name') == name and j.get('conclusion') == 'success' for j in jobs) for name in ('authorize', 'evaluate', 'publish'))
            or report.get('execution') != 'complete/comparable' or report.get('quality') != 'needs-review'):
        raise ValueError('Not successful trusted executed A/B evidence')
    identity = report.get('identity', {})
    if (identity.get('mode') != mode or identity.get('base') != base or identity.get('head') != head
            or any(not re.fullmatch('[0-9a-f]{64}', identity.get(k, '')) for k in ('dataset_hash', 'harness_hash'))
            or identity.get('models') != {'jev': 'jev-1.13', 'supervisor': 'gpt-6-luna', 'ragas_judge': 'gpt-6-luna', 'ragas_generation': 'gpt-6-luna', 'embedding': 'text-embedding-3-small', 'highlight': 'gemini-3.8-flash'}
            or not isinstance(identity.get('settings'), dict)
            or (mode in ('ragas', 'highlight') and not re.fullmatch('[0-9a-f]{64}', identity.get('corpus_hash', '')))
            or (mode in ('jev', 'supervisor') and identity.get('corpus_hash') != 'independently-seeded-contexts')):
        raise ValueError('Stale/incomplete evidence identity')
    selected = identity['settings'].get('ids')
    cases = report.get('cases')
    settings = identity['settings']
    if (settings.get('dataset') not in ('seed', 'boundary', 'first-action') or settings.get('ragas_k') != 5
            or settings.get('ragas_mode') != 'vector' or settings.get('judge_sampling') != 'omitted' or settings.get('judge_reasoning') != 'medium'
            or (mode == 'ragas' and settings.get('ragas_requests') != ab.luna.SETTINGS)
            or (mode == 'highlight' and not identity.get('actual_model_versions'))
            or (mode == 'supervisor' and identity.get('observed_supervisor_config', {}).get('model') != 'gpt-6-luna')):
        raise ValueError('Frozen evaluator settings mismatch')
    if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected):
        raise ValueError('Missing frozen case IDs')
    aa, bb = [], []
    for row in ab.rows_by_id(cases, selected).values():
        if any(not re.fullmatch('[0-9a-f]{64}', row.get(k, '')) for k in ('input_hash', 'label_hash')):
            raise ValueError('Missing frozen input/label identity')
        for target, key in ((aa, 'A'), (bb, 'B')):
            target.append({'id': row['id'], 'input_hash': row['input_hash'], 'label_hash': row['label_hash'], 'scores': row[key], 'errors': {}})
    reconstructed = ab.compare({'identity': identity, 'rows': aa}, {'identity': identity, 'rows': bb}, selected, identity)
    if report.get('count') != reconstructed['count'] or report.get('metrics') != reconstructed['metrics']:
        raise ValueError('Incomplete counts/metrics')
    for original, rebuilt in zip(cases, reconstructed['cases']):
        if original.get('delta') != rebuilt['delta']:
            raise ValueError('Mismatched A/B delta')
    return identity


def artifact_report(repository, run_id):
    artifacts = ab.api(f'repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100')
    matches = [a for a in artifacts['artifacts'] if a['name'] == 'provider-ab-evidence' and not a['expired']]
    if len(matches) != 1 or artifacts['total_count'] > 100:
        raise ValueError('Missing/ambiguous executed artifact')
    url = f'https://api.github.com/repos/{repository}/actions/artifacts/{matches[0]["id"]}/zip'
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN']})
    with urllib.request.urlopen(req, timeout=30) as response:
        data = response.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError('Evidence archive too large')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.namelist() != ['ab-summary.json'] or archive.getinfo('ab-summary.json').file_size > 2_000_000:
            raise ValueError('Unexpected artifact contents')
        return json.loads(archive.read('ab-summary.json'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pr', type=int, required=True)
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', required=True)
    parser.add_argument('--decision', choices=('approve', 'reject', 'not-applicable'), required=True)
    parser.add_argument('--runs', default='', help='mode=run_id comma list; every required mode')
    parser.add_argument('--reason', required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_REF') not in ('refs/heads/develop', 'refs/heads/main') or not args.reason.strip():
        raise ValueError('Trusted explicit reasoned decision required')
    repo = os.environ['GITHUB_REPOSITORY']
    target = f'repos/{repo}/pulls/{args.pr}'
    ab.validate_pr(ab.api(target), repo, args.pr, args.base, args.head)
    paths = []
    for page in range(1, 32):
        files = ab.api(target + f'/files?per_page=100&page={page}')
        paths.extend(f['filename'] for f in files)
        paths.extend(f['previous_filename'] for f in files if 'previous_filename' in f)
        if len(files) < 100:
            break
    else:
        raise ValueError('PR file classification truncated')
    required = required_modes(paths)
    runs = {}
    for item in args.runs.split(',') if args.runs else []:
        if not re.fullmatch(r'(jev|supervisor|ragas|highlight)=[1-9][0-9]*', item):
            raise ValueError('Invalid mode/run ID')
        mode, run_id = item.split('=')
        if mode in runs:
            raise ValueError('Duplicate mode')
        runs[mode] = run_id
    evidence = {}
    if args.decision == 'approve':
        if set(runs) != required or not required:
            raise ValueError('Every classified mode must have evidence')
        for mode, run_id in runs.items():
            run = ab.api(f'repos/{repo}/actions/runs/{run_id}')
            recent = ab.api(f'repos/{repo}/actions/workflows/evaluation-pr-ab.yml/runs?event=workflow_dispatch&head_sha={os.environ["GITHUB_SHA"]}&per_page=100')
            if recent['total_count'] > 100:
                raise ValueError('Run history truncated; require bounded fresh review')
            matching = [r for r in recent['workflow_runs'] if r.get('display_title') == run.get('display_title')]
            if not matching or max(r['id'] for r in matching) != int(run_id):
                raise ValueError('Newer mode execution supersedes selected evidence')
            jobs = ab.api(f'repos/{repo}/actions/runs/{run_id}/jobs?per_page=100')
            if jobs['total_count'] > 100:
                raise ValueError('Truncated job evidence')
            evidence[mode] = validate_evidence(run, jobs['jobs'], artifact_report(repo, run_id), mode, repo,
                                              os.environ['GITHUB_SHA'], args.base, args.head, args.pr)
    elif runs:
        raise ValueError('Reject/exemption does not accept evidence run overrides')
    protected = ab.quality_state(paths)['harness_review_required']
    state = decision_state(args.decision, required, evidence, protected,
                           os.environ.get('PROTECTED_REVIEW') == 'true')
    # No arbitrary statuses are accepted as evidence; validate current PR again immediately before write.
    ab.validate_pr(ab.api(target), repo, args.pr, args.base, args.head)
    description = {'approve': 'User approved all classified live A/B evidence', 'reject': 'User rejected quality',
                   'not-applicable': 'Trusted classification: non-LLM exemption'}[args.decision]
    data = json.dumps({'state': state, 'context': 'evaluation/provider-quality', 'description': description,
                      'target_url': os.environ['GITHUB_SERVER_URL']+'/'+repo+'/actions/runs/'+os.environ['GITHUB_RUN_ID']}).encode()
    request = urllib.request.Request('https://api.github.com/repos/'+repo+'/statuses/'+args.head, data=data,
        headers={'Authorization': 'Bearer '+os.environ['GH_TOKEN'], 'Content-Type': 'application/json'})
    urllib.request.urlopen(request, timeout=30).close()
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
        stream.write(f'Quality decision: {args.decision}; required modes: {",".join(sorted(required))}; protected review required: {protected}; pinned base/head: {args.base}/{args.head}\n')


if __name__ == '__main__':
    main()
