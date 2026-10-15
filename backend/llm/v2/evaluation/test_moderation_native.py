"""Fixed provider flags, not real model classification quality. Run --baseline before implementation."""
import json
import sys
from unittest.mock import MagicMock, patch
from types import SimpleNamespace as NS
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_community_moderation import (
    check, evaluate, LLMTestCase, RoutingMetric, AsyncConfig, CacheConfig, DisplayConfig,
    ragas_evaluate, EvaluationDataset, SingleTurnSample, RoutingRagasMetric, RunConfig,
)

CASES = [
    ('clean', '야구 경기 재미있다', False, False, [], 'approved'),
    ('harassment', 'synthetic harassment', True, False, [], 'review'),
    ('political', '정당에 투표하세요', False, False, [], 'approved'),
    ('url', 'https://example.invalid 야구 광고', False, False, [], 'approved'),
    ('trade', '티켓 판매합니다', False, False, [], 'approved'),
    ('image-unchecked', '첨부 사진', False, False, ['fixture'], 'review'),
    ('provider-error', '원문', False, True, [], 'error'),
]
outputs = []
for name, text, flagged, fail, images, expected in CASES:
    client = MagicMock()
    categories, scores = MagicMock(), MagicMock()
    categories.model_dump.return_value = {'harassment': flagged}
    scores.model_dump.return_value = {'harassment': float(flagged)}
    client.moderations.create.return_value = NS(model='synthetic', results=[NS(flagged=flagged, categories=categories, category_scores=scores)])
    if fail:
        client.moderations.create.side_effect = TimeoutError()
    with patch('openai.OpenAI', return_value=client):
        try:
            findings, _ = check(dict(text=text, mode='openai', policy_version='test-v1', image_ids=images))
            actual = 'review' if findings else 'approved'
        except Exception:
            actual = 'error'
    client.embeddings.create.assert_not_called()
    client.responses.parse.assert_not_called()
    outputs.append((name, actual, expected))
deep = evaluate([LLMTestCase(input=n, actual_output=a, expected_output=e) for n,a,e in outputs],
    metrics=[RoutingMetric()], async_config=AsyncConfig(run_async=False),
    cache_config=CacheConfig(write_cache=False, use_cache=False),
    display_config=DisplayConfig(show_indicator=False, print_results=False, inspect_after_run=False))
ragas = ragas_evaluate(EvaluationDataset(samples=[SingleTurnSample(user_input=n, response=a, reference=e) for n,a,e in outputs]),
    metrics=[RoutingRagasMetric()], run_config=RunConfig(max_workers=1, max_retries=0), raise_exceptions=True, show_progress=False)
print(json.dumps({'outputs': outputs, 'deepeval_passed': sum(r.metrics_data[0].score == 1 for r in deep.test_results),
    'ragas_passed': sum(r['moderation_routing'] == 1 for r in ragas.scores), 'provider_calls': 0, 'semantic_quality': 'not-measured'}))
if '--baseline' not in sys.argv:
    assert len(deep.test_results) == len(ragas.scores) == len(CASES)
    assert all(a == e for _,a,e in outputs)
    assert all(r.metrics_data[0].score == 1 for r in deep.test_results)
    assert all(r['moderation_routing'] == 1 for r in ragas.scores)
