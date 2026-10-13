"""Stdlib wiring/security checks; no credentials or provider calls."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('ci', HERE / 'evaluation_ci.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class WiringTest(unittest.TestCase):
    def test_bounded_ids_and_no_shell(self):
        self.assertEqual(ci.ids('greeting,new-course'), ['greeting', 'new-course'])
        for bad in ('', 'a,a', 'a,b,c,d,e,f', '$(touch /tmp/ci)', 'a;env', '../seed'):
            with self.assertRaises(ValueError):
                ci.ids(bad)
        with patch.object(ci.subprocess, 'run') as run:
            run.return_value.returncode = 0
            ci.execute(['script.py', '$(not-a-shell)'])
            self.assertNotIn('shell', run.call_args.kwargs)
            self.assertEqual(run.call_args.args[0][-1], '$(not-a-shell)')

    def test_missing_prerequisites_fail_before_paid_calls(self):
        with patch.dict(ci.os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                ci.require('OPENAI_API_KEY')
        with patch.object(ci.subprocess, 'run') as run:
            run.return_value.returncode = 0
            ci.execute(['script.py', '--output', '/new/output'], offline=True)
            command = run.call_args.args[0]
            self.assertEqual(command[-3:], ['script.py', '--output', '/new/output'])
            self.assertIn('offline-exec', command)

    def test_quality_failure_is_not_execution_success_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'result.json'
            report = {
                'part': 'jev', 'selected_count': 1, 'executed_count': 1,
                'results': [{'id': 'greeting', 'status': 'failed'}],
                'dimensions': {'execution_errors': 0, 'semantic_count': 0,
                    'guard': dict.fromkeys(('evaluated', 'held', 'exact', 'false_allow', 'false_deny'), 0),
                    'course': dict.fromkeys(('evaluated', 'held', 'exact'), 0),
                    'capability': dict.fromkeys(('evaluated', 'held', 'contract_passed', 'exact'), 0),
                    'contract': {'passed': 0}}}
            path.write_text(json.dumps(report))
            self.assertEqual(ci.jev_report(path)['failed'], 1)
            with self.assertRaises(RuntimeError):
                ci.jev_report(path, offline=True)
            for bad in ({**report, 'status': 'unexpected'}, {**report, 'executed_count': 2},
                        {**report, 'dimensions': {}}, {**report, 'selected_count': True}):
                path.write_text(json.dumps(bad))
                with self.assertRaises(RuntimeError):
                    ci.jev_report(path)
            path.write_text(json.dumps(report))
            with self.assertRaises(RuntimeError):
                ci.jev_report(path, expected_ids=['other'])
            for row in ({'status': 'execution_error'}, {'status': 'unknown'}, {'id': '', 'status': 'passed'}, {'status': 'failed', 'error_type': 'TimeoutError'}):
                path.write_text(json.dumps({**report, 'results': [row]}))
                with self.assertRaises(RuntimeError):
                    ci.jev_report(path)

    def test_django_export_help_under_isolated_launcher(self):
        # Help imports the actual command but does not search or call a provider.
        self.assertEqual(ci.execute(['backend/manage.py', 'export_ragas', '--help']), 0)
        with patch.object(ci.subprocess, 'run') as run:
            run.return_value.returncode = 0
            ci.execute(['backend/manage.py', 'export_ragas', '--help'])
            self.assertIn('django-exec', run.call_args.args[0])
            self.assertNotIn('offline-exec', run.call_args.args[0])

    def test_first_action_input_uses_canonical_isolated_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'input.json'
            self.assertEqual(ci.execute([ci.EVAL + 'remaining.py', 'supervisor-input', '--output', str(output)], offline=True), 0)
            rows = json.loads(output.read_text())
            self.assertEqual(len(rows), 12)
            self.assertTrue(all('selected_tools' in row and 'context' in row for row in rows))

    def test_only_documented_quality_exit_is_accepted(self):
        with patch.object(ci.subprocess, 'run') as run:
            for code in (0, 1):
                run.return_value.returncode = code
                self.assertEqual(ci.execute(['script.py'], accept_quality=True), code)
            for code in (2, 7, -9):
                run.return_value.returncode = code
                with self.assertRaises(RuntimeError):
                    ci.execute(['script.py'], accept_quality=True)

    def test_remaining_schema_and_canonical_ids(self):
        manifest = json.loads((ci.ROOT / ci.EVAL / 'remaining_manifest.json').read_text())
        rows = [{'id': id, 'split': split, 'contract_score': 1, 'reason': 'test fixture, not quality evidence',
                 'semantic_correctness': 'not_measured', 'dimension_scores': {}}
                for split in ('dev', 'heldout') for id in manifest[split]]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'report.json'
            def write(values):
                path.write_text(json.dumps({'scope': 'supplied structured outputs only, not production/live quality', 'results': values}))
            write(rows)
            self.assertEqual(ci.remaining_report(path)['passed'], 60)
            for values in ([{'contract_score': 1}] * 60, rows[:-1], rows[:-1] + [rows[0]],
                           [{**rows[0], 'contract_score': '1'}] + rows[1:],
                           [{**rows[0], 'split': 'unknown'}] + rows[1:]):
                write(values)
                with self.assertRaises(RuntimeError):
                    ci.remaining_report(path)

    def test_highlight_requires_complete_finite_metrics_and_count(self):
        report = {'framework': 'ragas', 'replayed_cases': 1, 'additional_provider_calls': 0,
                  'judge_calls': 0, 'api_error_cases': 0, 'inconclusive_cases': 0}
        report.update({key: {'passed': 0, 'denominator': 1, 'rate': 0.0}
                       for key in ('recall_at_3', 'final_selection', 'original_time', 'no_match', 'no_match_precision')})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'summary.json'
            path.write_text(json.dumps(report))
            self.assertEqual(ci.highlight_report(path, 1, replay=True)['final_selection']['rate'], 0)
            for bad in ({'api_error_cases': 0}, {**report, 'replayed_cases': 2},
                        {**report, 'inconclusive_cases': 1},
                        {**report, 'final_selection': {'passed': 0, 'denominator': 1, 'rate': float('nan')}}):
                path.write_text(json.dumps(bad))
                with self.assertRaises(RuntimeError):
                    ci.highlight_report(path, 1, replay=True)

    def test_tokenizer_preflight_precedes_network_denial(self):
        workflow = (HERE / 'workflows/evaluation-regression.yml').read_text()
        self.assertEqual(workflow.count('TIKTOKEN_CACHE_DIR: ${{ runner.temp }}/evaluation-tiktoken'), 2)
        self.assertLess(workflow.index('tiktoken.get_encoding("cl100k_base")'),
                        workflow.index('evaluation_ci.py free'))

    def test_full_highlight_regressions_are_in_required_workflow(self):
        import yaml

        workflow = yaml.safe_load((HERE / 'workflows/evaluation-regression.yml').read_text())
        job = workflow['jobs']['deterministic']
        self.assertNotIn('if', job)
        self.assertNotIn('continue-on-error', job)
        service = job['services']['highlight-db']
        self.assertEqual(service['image'], 'pgvector/pgvector:pg18')
        self.assertEqual(service['env'], dict.fromkeys(
            ('POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD'), 'highlight_ci'))
        self.assertIn('5432:5432', service['ports'])
        backend = 'python -I .github/evaluation_ci.py offline-exec backend/manage.py test llm.tests.test_highlights --settings=llm.tests.highlight_settings --noinput --verbosity=2'
        frontend = 'node --test tests/youtube-highlight.test.mjs'
        for command in (backend, frontend, 'python -I .github/test_evaluation_ci.py'):
            steps = [step for step in job['steps'] if step.get('run') == command]
            self.assertEqual(len(steps), 1, command)
            self.assertNotIn('if', steps[0])
            self.assertNotIn('continue-on-error', steps[0])
            if command == frontend:
                self.assertEqual(steps[0]['working-directory'], 'frontend')
        for name in ('dev', 'prod'):
            deploy = yaml.safe_load((HERE / f'workflows/deploy-{name}.yml').read_text())
            self.assertEqual(deploy['jobs']['evaluation-regression']['uses'],
                             './.github/workflows/evaluation-regression.yml')

    def test_workflow_trust_and_gates(self):
        manual = (HERE / 'workflows/evaluation-manual.yml').read_text()
        self.assertNotIn('pull_request_target', manual)
        self.assertIn('needs: authorize', manual)
        self.assertIn('environment: evaluation', manual)
        self.assertIn('persist-credentials: false', manual)
        run_blocks = [line for line in manual.splitlines() if '${{ inputs.' in line]
        self.assertTrue(all(': ${{' in line for line in run_blocks))
        for name in ('dev', 'prod'):
            workflow = (HERE / f'workflows/deploy-{name}.yml').read_text()
            self.assertIn('needs: [ci-test, evaluation-regression]', workflow)
        self.assertEqual(len(ci.FREE), 10)
        self.assertEqual(ci.FREE[-2:], [
            ['backend/llm/v2/tests/run_attachment_offline.py', 'llm.tests.test_domain_tools.ExternalDomainToolAdapterTest'],
            [ci.EVAL + 'weather_contract.py', 'export', '--output', 'weather.json']])
        self.assertIn("execute([EVAL + 'weather_contract.py', 'scorer-check'], offline=True)",
                      (HERE / 'evaluation_ci.py').read_text())
        self.assertTrue(all((ci.ROOT / command[0]).is_file() for command in ci.FREE))


if __name__ == '__main__':
    unittest.main(verbosity=2)
