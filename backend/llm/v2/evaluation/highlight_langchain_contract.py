"""Five fixed synthetic migration contracts, scored by installed offline frameworks."""
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
    'test_candidate_batch_is_real_runnable_and_ordered',
    'test_embedding_text_and_joint_preserve_unspecified_task',
    'test_invalid_metadata_and_image_limit_zero_transport',
    'test_malformed_and_inaccessible_are_not_negative',
    'test_real_structured_model_serialization_callbacks',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise ValueError('Use fresh contract evidence path')
    output.mkdir(parents=True)
    from llm.tests import test_highlight_langchain as contract
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
            self.observed[test._testMethodName] = 'error'
        def addSuccess(self, test):
            super().addSuccess(test)
            self.observed[test._testMethodName] = 'pass'
        def addFailure(self, test, err):
            super().addFailure(test, err)
            self.observed[test._testMethodName] = 'fail'
        def addError(self, test, err):
            super().addError(test, err)
            self.observed[test._testMethodName] = 'error'
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.observed = {}

    suite = unittest.TestSuite(contract.LangChainMigrationTests(name) for name in CASES)
    result = unittest.TextTestRunner(verbosity=2, resultclass=Observations).run(suite)
    rows = []
    metric, exact = Metric(), ExactMatch()
    for name in CASES:
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
    (output / 'observations.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    raise SystemExit(not result.wasSuccessful())


if __name__ == '__main__':
    main()
