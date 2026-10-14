"""Fixed synthetic migration/retrieval contracts, scored by installed offline frameworks."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import unittest

os.environ['DEEPEVAL_DISABLE_DOTENV'] = '1'
os.environ['DEEPEVAL_DISABLE_LEGACY_KEYFILE'] = '1'
os.environ['DEEPEVAL_TELEMETRY_OPT_OUT'] = 'YES'
os.environ['RAGAS_DO_NOT_TRACK'] = 'true'
os.environ['LANGCHAIN_TRACING_V2'] = 'false'

CASES = (
    'test_retrieval_has_zero_generation_calls',
    'test_three_candidates_one_real_gemini_generation',
    'test_group_exact_coverage_and_fail_closed_boundaries',
    'test_video_order_beats_cross_modal_agreement_and_preserves_images',
    'test_video_duplicates_unknown_overlap_touching_and_limits',
    'test_video_empty_negative_and_verifier_errors',
    'test_candidate_batch_is_real_runnable_and_ordered',
    'test_embedding_text_and_joint_preserve_unspecified_task',
    'test_invalid_metadata_and_image_limit_zero_transport',
    'test_malformed_and_inaccessible_are_not_negative',
    'test_real_structured_model_serialization_callbacks',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--pipeline', action='store_true', help='Video-level pipeline contracts, not segment accuracy')
    parser.add_argument('--source', help='Immutable pre-edit source for offline A')
    parser.add_argument('--retry', action='store_true', help='Compiled stored-search retry contracts')
    parser.add_argument('--graph-source', help='Immutable retry graph source for offline A')
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise ValueError('Use fresh contract evidence path')
    output.mkdir(parents=True)
    if args.source:
        os.environ['HIGHLIGHT_CONTRACT_SOURCE'] = str(Path(args.source).resolve(strict=True))
    if args.graph_source:
        os.environ['HIGHLIGHT_GRAPH_SOURCE'] = str(Path(args.graph_source).resolve(strict=True))
    if args.retry:
        from llm.tests import test_highlight_retry as contract
        test_class = contract.RetryTests
        cases = tuple(unittest.defaultTestLoader.getTestCaseNames(test_class))
    elif args.pipeline:
        from llm.tests import test_highlight_pipeline as contract
        test_class = contract.PipelineTests
        cases = tuple(unittest.defaultTestLoader.getTestCaseNames(test_class))
    else:
        from llm.tests import test_highlight_langchain as contract
        test_class, cases = contract.LangChainMigrationTests, CASES
    from deepeval.metrics import BaseMetric
    from deepeval.test_case import LLMTestCase
    from ragas.dataset_schema import SingleTurnSample
    from ragas.metrics import ExactMatch

    class Metric(BaseMetric):
        threshold = 1.
        def measure(self, test_case, **kwargs):
            self.score = float(test_case.actual_output == test_case.expected_output)
            self.success = self.score == 1.
            self.reason = 'synthetic exact execution contract'
            return self.score
        async def a_measure(self, test_case, **kwargs):
            return self.measure(test_case, **kwargs)
        def is_successful(self):
            return self.success
        @property
        def __name__(self):
            return 'HighlightLangChainExactContract'

    class Observations(unittest.TextTestResult):
        def startTest(self, test):
            super().startTest(test)
            self.observed[test._testMethodName] = 'running'
        def addSuccess(self, test):
            super().addSuccess(test)
            self.observed[test._testMethodName] = 'pass'
        def addFailure(self, test, err):
            super().addFailure(test, err)
            self.observed[test._testMethodName] = 'fail'
        def addError(self, test, err):
            super().addError(test, err)
            self.observed[test._testMethodName] = 'error'
        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            if err is not None:
                observed = 'fail' if issubclass(err[0], test.failureException) else 'error'
                if self.observed[test._testMethodName] != 'error':
                    self.observed[test._testMethodName] = observed
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.observed = {}

    suite = unittest.TestSuite(test_class(name) for name in cases)
    result = unittest.TextTestRunner(verbosity=2, resultclass=Observations).run(suite)
    rows = []
    metric, exact = Metric(), ExactMatch()
    for name in cases:
        actual = result.observed[name]
        rows.append({'id': name, 'actual': actual, 'expected': 'pass',
            'deepeval': metric.measure(LLMTestCase(input=name, actual_output=actual, expected_output='pass')),
            'ragas': asyncio.run(exact.single_turn_ascore(SingleTurnSample(response=actual, reference='pass')))})
    import importlib.metadata
    summary = {'synthetic_only': True, 'live_quality': 'not_measured', 'cases': rows,
        'deepeval_rate': sum(row['deepeval'] for row in rows) / len(rows),
        'ragas_rate': sum(row['ragas'] for row in rows) / len(rows),
        'contract_sha256': hashlib.sha256(Path(contract.__file__).read_bytes()).hexdigest(),
        'source_sha256': hashlib.sha256(Path(contract.mm.__file__).read_bytes()).hexdigest(),
        'versions': {name: importlib.metadata.version(name) for name in ('deepeval', 'ragas', 'langchain-google-genai', 'google-genai', 'langchain-core')}}
    if args.retry:
        summary['graph_sha256'] = hashlib.sha256(Path(contract.graph_source.__file__).read_bytes()).hexdigest()
    (output / 'observations.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    raise SystemExit(not result.wasSuccessful())


if __name__ == '__main__':
    main()
