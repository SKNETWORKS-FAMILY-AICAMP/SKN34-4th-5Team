"""Real DeepEval/Ragas, synthetic provider responses. NOT semantic accuracy evidence."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch, MagicMock

os.environ.update(DEEPEVAL_TELEMETRY_OPT_OUT='YES', DEEPEVAL_DISABLE_DOTENV='1',
                  DEEPEVAL_DISABLE_LEGACY_KEYFILE='1', RAGAS_DO_NOT_TRACK='true',
                  PYTHON_DOTENV_DISABLED='1', LANGSMITH_TRACING='false')
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from django.conf import settings
if not settings.configured:
    settings.configure(COMMUNITY_POLICY_VERSION='test-v1', COMMUNITY_POLITICAL_THRESHOLD='0.8',
                       COMMUNITY_POLICY_MODEL='synthetic-provider', COMMUNITY_EMBEDDING_MODEL='synthetic-embedding')
from community.moderation_checks import check, PolicyResult
from deepeval import evaluate
from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase
from deepeval.evaluate.configs import AsyncConfig, CacheConfig, DisplayConfig
from ragas import EvaluationDataset, SingleTurnSample, RunConfig, evaluate as ragas_evaluate
from ragas.metrics.base import SingleTurnMetric, MetricType


class RoutingMetric(BaseMetric):
    threshold = 1.0
    async_mode = False
    strict_mode = True
    evaluation_model = 'deterministic; no semantic judge'

    @property
    def __name__(self):
        return 'moderation_routing'

    def measure(self, test_case, *args, **kwargs):
        self.score = float(test_case.actual_output == test_case.expected_output)
        self.success = self.score == 1
        self.reason = 'Synthetic provider output routed to expected status'
        return self.score

    async def a_measure(self, test_case, *args, **kwargs):
        return self.measure(test_case)

    def is_successful(self):
        return self.success


class RoutingRagasMetric(SingleTurnMetric):
    def __init__(self):
        super().__init__(name='moderation_routing', _required_columns={MetricType.SINGLE_TURN: {'response', 'reference'}})

    def init(self, run_config):
        pass

    async def _single_turn_ascore(self, sample, callbacks):
        return float(sample.response == sample.reference)


# Score values/flags below are fixtures, never claimed as real model judgments.
CASES = [
    ('praise', 'wtf hanwha Eagles is at another level', False, False, False, [], False, 'approved'),
    ('trade', '한화 유니폼 중고 판매합니다', False, True, False, [], False, 'approved'),
    ('untagged-trade', '한화 유니폼 중고 판매합니다', False, False, False, [], False, 'approved'),
    ('untagged-baseball-ad', '야구 배트 매장 할인 행사입니다', False, False, False, [], False, 'approved'),
    ('baseball-ad', '야구 배트 매장 광고입니다', False, False, True, [], False, 'approved'),
    ('political', '이 정당에게 투표하세요', True, False, False, [], False, 'review'),
    ('scam-even-if-tagged', '돈 보내면 수익 보장', False, True, True, ['fraud'], False, 'review'),
    ('harm', 'synthetic unsafe text', False, False, False, [], True, 'review'),
    ('context-abuse', '저 사람을 공격해', False, False, False, ['abuse'], False, 'review'),
    ('link', '야구 광고 https://example.invalid', False, False, True, [], False, 'review'),
]


class CommunityProviderContractTests(unittest.TestCase):
    def test_actual_adapter_and_frameworks_without_network(self):
        outputs = []
        with tempfile.TemporaryDirectory() as directory, patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden')):
            corpus = Path(directory) / 'synthetic.json'
            corpus.write_text(json.dumps({'reviewed': True, 'version': 'synthetic-only', 'cases': [{'id': 'fixture', 'label': 'political', 'text': 'reference fixture'}]}))
            with patch.object(settings, 'COMMUNITY_POLITICAL_CASES_PATH', str(corpus), create=True):
                for name, text, political, trade, ad, codes, harm, expected in CASES:
                    client = MagicMock()
                    client.embeddings.create.return_value.data = [NS(index=0, embedding=[1., 0.]), NS(index=1, embedding=[1., 0.] if political else [0., 1.])]
                    flags = MagicMock()
                    flags.model_dump.return_value = {'violence': harm, 'harassment': name == 'praise'}
                    scores = MagicMock()
                    scores.model_dump.return_value = {'violence': 1. if harm else 0.}
                    client.moderations.create.return_value = NS(model='synthetic-moderation', results=[NS(categories=flags, category_scores=scores, flagged=harm or name == 'praise')])
                    client.responses.parse.return_value = NS(status='completed', output_parsed=PolicyResult(findings=[{'code': code, 'quote': text, 'reason': 'synthetic fixture'} for code in codes]))
                    with patch('openai.OpenAI', return_value=client):
                        findings, _ = check({'text': text, 'is_trade': trade, 'is_advertisement': ad, 'image_ids': [], 'mode': 'live', 'policy_version': 'test-v1'})
                    actual = 'review' if findings else 'approved'
                    self.assertEqual(actual, expected, name)
                    outputs.append((name, actual, expected))
                # Missing/invalid provider output must raise; workers hold it privately.
                client.responses.parse.return_value = NS(status='incomplete', output_parsed=None)
                with patch('openai.OpenAI', return_value=client), self.assertRaises(RuntimeError):
                    check({'text': '원문', 'is_trade': False, 'is_advertisement': False, 'image_ids': [], 'mode': 'live', 'policy_version': 'test-v1'})
            deep = evaluate([LLMTestCase(input=name, actual_output=actual, expected_output=expected) for name, actual, expected in outputs],
                            metrics=[RoutingMetric()], async_config=AsyncConfig(run_async=False),
                            cache_config=CacheConfig(write_cache=False, use_cache=False),
                            display_config=DisplayConfig(show_indicator=False, print_results=False, inspect_after_run=False))
            self.assertTrue(all(r.metrics_data[0].score == 1 for r in deep.test_results))
            ragas = ragas_evaluate(EvaluationDataset(samples=[SingleTurnSample(user_input=name, response=actual, reference=expected) for name, actual, expected in outputs]),
                                  metrics=[RoutingRagasMetric()], run_config=RunConfig(max_workers=1, max_retries=0), raise_exceptions=True, show_progress=False)
            self.assertEqual(ragas.scores, [{'moderation_routing': 1.0}] * len(CASES))
            print(json.dumps({'cases': len(CASES), 'deepeval_passed': len(deep.test_results), 'ragas_passed': len(ragas.scores), 'provider_calls': 0, 'semantic_quality': 'not-measured'}))


if __name__ == '__main__':
    unittest.main()
