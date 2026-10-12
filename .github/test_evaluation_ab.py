"""Synthetic orchestration fixtures only: never real provider quality evidence."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('ab', HERE / 'evaluation_ab.py')
ab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ab)
quality_spec = importlib.util.spec_from_file_location('quality', HERE / 'evaluation_quality.py')
quality = importlib.util.module_from_spec(quality_spec)
quality_spec.loader.exec_module(quality)


class ABTest(unittest.TestCase):
    def setUp(self):
        self.identity = {'synthetic': True, 'model': 'fixture', 'corpus_hash': 'frozen'}
        self.row = {'id': 'r16', 'input_hash': 'input+context', 'label_hash': 'independent',
                    'scores': {'score': 0.5, 'held': None}, 'errors': {}}
        self.a = {'identity': self.identity, 'rows': [self.row]}
        self.b = copy.deepcopy(self.a)

    def test_exact_frozen_AB_null_and_quality_review(self):
        self.b['rows'][0]['scores']['score'] = 0.75
        result = ab.compare(self.a, self.b, ['r16'], self.identity)
        self.assertEqual(result['quality'], 'needs-review')
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['cases'][0]['delta'], {'score': 0.25, 'held': None})
        self.assertEqual(result['metrics']['held']['A_denominator'], 0)

    def test_errors_missing_duplicate_ids_input_context_labels_settings(self):
        variants = [dict(identity={'model': 'candidate-relaxed'}, rows=[self.row]),
                    dict(identity=self.identity, rows=[]),
                    dict(identity=self.identity, rows=[self.row, self.row])]
        for changes in ({'id': 'other'}, {'input_hash': 'other-context'}, {'label_hash': 'relaxed'},
                        {'errors': {'provider': 'TimeoutError'}}, {'scores': {}},
                        {'scores': {'score': float('nan'), 'held': None}},
                        {'scores': {'score': True, 'held': None}}):
            variants.append({'identity': self.identity, 'rows': [{**self.row, **changes}]})
        for b in variants:
            with self.subTest(b=b), self.assertRaises(ValueError):
                ab.compare(self.a, b, ['r16'], self.identity)

    def test_missing_approval_ref_keys_before_calls(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(ab.subprocess, 'run') as run:
            for mode in ab.MODES:
                with self.assertRaises(ValueError): ab.preflight(mode, 'true', 'refs/heads/develop')
            for mode, approval, ref in [('generate', 'true', 'refs/heads/main'), ('jev', 'false', 'refs/heads/main'),
                                         ('jev', 'true', 'refs/pull/82/head'), ('jev', 'true', 'refs/heads/feat/evil')]:
                with self.assertRaises(ValueError): ab.preflight(mode, approval, ref)
            run.assert_not_called()

    def test_current_pinned_PR_stale_and_injection_refs(self):
        p = {'number': 82, 'state': 'open', 'base': {'repo': {'full_name': 'team/repo'}, 'ref': 'develop', 'sha': 'a'*40},
             'head': {'repo': {'full_name': 'fork/repo'}, 'ref': 'feat/example', 'sha': 'b'*40}}
        ab.validate_pr(p, 'team/repo', 82, 'a'*40, 'b'*40)
        for key, value in [('number', 83), ('state', 'closed')]:
            with self.assertRaises(ValueError): ab.validate_pr({**p, key: value}, 'team/repo', 82, 'a'*40, 'b'*40)
        for section, key, value in [('head', 'sha', 'c'*40), ('base', 'sha', 'c'*40),
                                     ('head', 'ref', 'feat/a;env'), ('base', 'ref', 'feat/main'), ('head', 'repo', None)]:
            q = copy.deepcopy(p)
            q[section][key] = value
            with self.assertRaises(ValueError): ab.validate_pr(q, 'team/repo', 82, 'a'*40, 'b'*40)
        for repo, head in [('team/repo;env', 'b'*40), ('team/repo', '$(touch /tmp/x)'), ('team/repo', '--help')]:
            with self.assertRaises(ValueError): ab.validate_pr(p, repo, 82, 'a'*40, head)

    def test_free_quality_state_not_mock_pass_and_fail_closed_harness(self):
        self.assertEqual(ab.quality_state(['frontend/page.tsx'])['quality'], 'exempt/non-LLM')
        self.assertEqual(ab.quality_state(['backend/llm/v2/agent/main.py'])['quality'], 'required/not-run')
        for path in ('backend/llm/v2/evaluation/seed.json', 'backend/llm/v2/evaluation/EVALUATION_CRITERIA.md', '.github/evaluation_ab.py'):
            self.assertTrue(ab.quality_state([path])['harness_review_required'])

    def test_ragas_actual_export_judge_contract_mismatches(self):
        label = {'id': 'r16', 'question': 'Synthetic question', 'reference': 'Independent synthetic answer', 'reference_doc_ids': ['doc']}
        row = {'id': 'r16', 'user_input': label['question'], 'reference': label['reference'], 'reference_doc_ids': ['doc'],
               'response': 'Synthetic response', 'retrieved_contexts': ['Synthetic corpus context'], 'retrieved_doc_ids': ['doc'],
               'errors': {}, 'metadata': {'mode': 'vector', 'k': 5, 'generation_model': 'gpt-6-luna', 'embedding_model': 'text-embedding-3-small', 'scope': 'retrieval_single_generation'}, 'scores': dict.fromkeys(('context_precision', 'context_recall', 'faithfulness', 'answer_relevancy'), 0.5)}
        report = {'errors': {}, 'evaluation_model': 'gpt-6-luna', 'ragas_version': '0.4.3', 'results': [row]}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / 'export.jsonl').write_text(json.dumps(row)+'\n')
            (out / 'scores.json').write_text(json.dumps(report))
            self.assertEqual(len(ab.normalize('ragas', out, ['r16'], {'r16': label})), 1)
            for changes in ({'reference': 'relaxed'}, {'retrieved_contexts': ['other']}, {'id': 'v04'},
                            {'response': ''}, {'scores': {}}, {'scores': dict.fromkeys(row['scores'])}):
                (out / 'scores.json').write_text(json.dumps({**report, 'results': [{**row, **changes}]}))
                with self.assertRaises(ValueError): ab.normalize('ragas', out, ['r16'], {'r16': label})
            (out / 'scores.json').write_text(json.dumps(report))
            for key, value in [('generation_model', 'mock'), ('embedding_model', 'mock'), ('mode', 'dense'), ('k', 1), ('scope', 'replay')]:
                (out / 'export.jsonl').write_text(json.dumps({**row, 'metadata': {**row['metadata'], key: value}})+'\n')
                with self.assertRaises(ValueError): ab.normalize('ragas', out, ['r16'], {'r16': label})
            for removed in ('metadata', 'errors'):
                (out / 'export.jsonl').write_text(json.dumps({k:v for k,v in row.items() if k != removed})+'\n')
                with self.assertRaises(ValueError): ab.normalize('ragas', out, ['r16'], {'r16': label})
            (out / 'export.jsonl').write_text(json.dumps(row)+'\n')
            (out / 'scores.json').write_text(json.dumps({**report, 'results': [{**row, 'scores': {**row['scores'], 'context_precision': -0.1}}]}))
            with self.assertRaises(ValueError): ab.normalize('ragas', out, ['r16'], {'r16': label})

    def test_jev_part_exact_dimensions_and_held_status(self):
        label = {'guard': 'PASS', 'capabilities': [], 'course_request': 'NONE'}
        row = {'id': 'greeting', 'status': 'passed', 'expected': label, 'dimension_scores': {'guard': True, 'course': True, 'capability': True}}
        report = {'part': 'jev', 'results': [row]}
        with tempfile.TemporaryDirectory() as tmp, patch.object(ab.ci, 'jev_report'):
            out = Path(tmp)
            (out/'results.json').write_text(json.dumps(report))
            self.assertEqual(len(ab.normalize('jev', out, ['greeting'], {'greeting': label})), 1)
            for candidate in ({**report, 'part': 'supervisor'}, {**report, 'results': [{**row, 'dimension_scores': {'arbitrary': True}}]},
                              {**report, 'results': [{**row, 'status': 'failed'}]}, {**report, 'results': [{**row, 'dimension_scores': {'guard': None, 'course': True, 'capability': True}}]}):
                (out/'results.json').write_text(json.dumps(candidate))
                with self.assertRaises(ValueError): ab.normalize('jev', out, ['greeting'], {'greeting': label})

    def test_trusted_overlay_argv_and_no_token_in_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'synthetic-candidate'
            (root / ab.EVAL).mkdir(parents=True)
            (root / '.github').mkdir()
            (root / 'backend/llm/management/commands').mkdir(parents=True)
            out = Path(tmp) / 'output'
            with patch.object(ab.subprocess, 'run') as run, patch.dict(os.environ, {'GH_TOKEN': 'synthetic-private-control-token'}):
                ab.run_side(root, 'jev', out)
                self.assertNotIn('GH_TOKEN', run.call_args.kwargs['env'])
                self.assertNotIn('shell', run.call_args.kwargs)
                self.assertEqual(run.call_args.args[0][-3:], ['jev', '--output', str(out)])
                self.assertEqual((root / ab.EVAL / 'seed.json').read_bytes(), (ab.ROOT / ab.EVAL / 'seed.json').read_bytes())

    def test_full_cli_synthetic_transport_and_stale_final_head(self):
        p = {'number': 82, 'state': 'open', 'base': {'repo': {'full_name': 'team/repo'}, 'ref': 'develop', 'sha': 'a'*40},
             'head': {'repo': {'full_name': 'fork/repo'}, 'ref': 'feat/example', 'sha': 'b'*40}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for side in ('a', 'b'):
                (root / side / ab.EVAL).mkdir(parents=True)
                ab.shutil.copytree(ab.ROOT / ab.EVAL, root / side / ab.EVAL, dirs_exist_ok=True)
            def launch(candidate, mode, out):
                out.mkdir()
            def normalized(*args):
                return [{**self.row, 'id': 'greeting'}]
            for stale in (False, True):
                out = root / ('stale' if stale else 'success')
                q = copy.deepcopy(p)
                if stale: q['head']['sha'] = 'c'*40
                argv = ['evaluation_ab.py', 'jev', '--pr', '82', '--base', 'a'*40, '--head', 'b'*40,
                        '--a', str(root/'a'), '--b', str(root/'b'), '--output', str(out)]
                with patch.dict(os.environ, {'AUTHORIZED': 'true', 'GITHUB_REF': 'refs/heads/develop',
                         'GITHUB_REPOSITORY': 'team/repo', 'OPENROUTER_API_KEY': 'synthetic', 'EVAL_IDS': 'greeting'}), \
                     patch.object(ab.sys, 'argv', argv), patch.object(ab, 'api', side_effect=[p,p,p,q]), \
                     patch.object(ab.subprocess, 'check_output', side_effect=['a'*40, 'b'*40]), \
                     patch.object(ab, 'run_side', side_effect=launch), patch.object(ab, 'normalize', side_effect=normalized):
                    if stale:
                        with self.assertRaises(RuntimeError): ab.main()
                    else:
                        ab.main()
                report = json.loads((out/'ab-summary.json').read_text())
                self.assertEqual(report['execution'], 'failed' if stale else 'complete/comparable')
                self.assertEqual(report['quality'], 'required/not-run' if stale else 'needs-review')

    def test_multimode_approve_reject_exemption_missing_failure(self):
        required = set(ab.MODES)
        self.assertEqual(quality.decision_state('approve', required, dict.fromkeys(required)), 'success')
        self.assertEqual(quality.decision_state('reject', required, {}), 'failure')
        self.assertEqual(quality.decision_state('not-applicable', set(), {}), 'success')
        for decision, modes, evidence in [('approve', required, {'jev': {}}), ('approve', set(), {}),
                                          ('not-applicable', required, {}), ('approve', required, {})]:
            with self.assertRaises(ValueError): quality.decision_state(decision, modes, evidence)
        for path in ('backend/config/settings.py', 'shared/runtime.py', 'prompts/main.txt', 'requirements.txt', '.github/evaluation_quality.py'):
            self.assertEqual(quality.required_modes([path]), required)
        self.assertEqual(quality.required_modes(['frontend/page.tsx', 'docs/readme.md']), set())

    def test_protected_review_actionable_without_free_failure(self):
        required = quality.required_modes(['.github/evaluation_quality.py'])
        evidence = dict.fromkeys(required)
        with self.assertRaises(ValueError):
            quality.decision_state('approve', required, evidence, protected=True)
        self.assertEqual(quality.decision_state('approve', required, evidence, True, True), 'success')
        with self.assertRaises(ValueError):
            quality.decision_state('approve', required, {'jev': {}}, True, True)
        self.assertEqual(quality.decision_state('reject', required, {}, True), 'failure')
        regression = (HERE / 'workflows/evaluation-regression.yml').read_text()
        self.assertNotIn('raise SystemExit', regression.split('  deterministic:')[0])
        review = (HERE / 'workflows/evaluation-quality-review.yml').read_text()
        self.assertIn('protected_review:', review)
        self.assertIn('PROTECTED_REVIEW: ${{ inputs.protected_review }}', review)
        self.assertIn('environment: evaluation', review)

    def test_trusted_artifact_identity_old_missing_failed_model_evidence(self):
        identity = {'mode': 'jev', 'base': 'a'*40, 'head': 'b'*40, 'dataset_hash': 'c'*64, 'harness_hash': 'd'*64,
                    'corpus_hash': 'independently-seeded-contexts',
                    'models': {'jev': 'jev-1.13', 'supervisor': 'gpt-6-luna', 'ragas_judge': 'gpt-6-luna',
                               'ragas_generation': 'gpt-6-luna', 'embedding': 'text-embedding-3-small', 'highlight': 'gemini-3.8-flash'},
                    'settings': {'dataset': 'seed', 'ids': ['greeting'], 'ragas_k': 5, 'ragas_mode': 'vector', 'judge_sampling': 'omitted', 'judge_reasoning': 'medium'}}
        row = {**self.row, 'id': 'greeting', 'input_hash': 'e'*64, 'label_hash': 'f'*64}
        report = ab.compare({'identity': identity, 'rows': [row]}, {'identity': identity, 'rows': [row]}, ['greeting'], identity)
        run = {'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'success', 'head_branch': 'develop',
               'head_sha': '1'*40, 'repository': {'full_name': 'team/repo'}, 'path': '.github/workflows/evaluation-pr-ab.yml',
               'display_title': 'Provider A/B pr=82 mode=jev base='+'a'*40+' head='+'b'*40}
        jobs = [{'name': n, 'conclusion': 'success'} for n in ('authorize','evaluate','publish')]
        quality.validate_evidence(run, jobs, report, 'jev', 'team/repo', '1'*40, 'a'*40, 'b'*40, 82)
        for change in ({'conclusion': 'failure'}, {'head_sha': '2'*40}, {'head_branch': 'feat/evil'},
                       {'event': 'pull_request'}, {'path': 'evil.yml'}, {'display_title': 'spoofed'}):
            with self.assertRaises(ValueError): quality.validate_evidence({**run, **change}, jobs, report, 'jev', 'team/repo', '1'*40, 'a'*40, 'b'*40, 82)
        for change in ({'count': 2}, {'cases': []}, {'execution': 'failed'}, {'identity': {**identity, 'head': '0'*40}},
                       {'identity': {**identity, 'models': {'jev': 'mock'}}}, {'identity': {**identity, 'dataset_hash': ''}}):
            with self.assertRaises(ValueError): quality.validate_evidence(run, jobs, {**report, **change}, 'jev', 'team/repo', '1'*40, 'a'*40, 'b'*40, 82)
        with self.assertRaises(ValueError): quality.validate_evidence(run, jobs[:-1], report, 'jev', 'team/repo', '1'*40, 'a'*40, 'b'*40, 82)

    def test_workflow_trusted_secret_and_exact_sha_publication_boundary(self):
        workflow = (HERE / 'workflows/evaluation-pr-ab.yml').read_text()
        self.assertNotIn('pull_request_target', workflow)
        self.assertIn('environment: evaluation', workflow)
        self.assertIn('needs: authorize', workflow)
        self.assertIn('statuses: write', workflow)
        self.assertIn("'evaluation/provider-quality','pending'", workflow)
        self.assertNotIn('secrets.', workflow.split('  publish:')[1])
        self.assertTrue(all(': ${{' in line for line in workflow.splitlines() if '${{ inputs.' in line and not line.startswith('run-name:')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
