"""Score frozen HTTP observations from baseline/candidate; no provider or judge calls."""
import argparse
import json
from pathlib import Path
import socket
from unittest.mock import patch
from test_community_moderation import (
    evaluate, LLMTestCase, RoutingMetric, AsyncConfig, CacheConfig, DisplayConfig,
    EvaluationDataset, SingleTurnSample, RoutingRagasMetric, RunConfig, ragas_evaluate,
)


def load(path):
    lines = [line.split('=', 1)[1] for line in path.read_text().splitlines() if line.startswith('MODERATION_PROBE=')]
    if len(lines) != 1:
        raise ValueError('Exactly one completed probe required')
    rows = json.loads(lines[0])
    expected = {'list': 'private', 'detail': 'private', 'comments': 'private', 'points': 'deferred'}
    if [r['id'] for r in rows] != list(expected) or any(r['expected'] != expected[r['id']] for r in rows):
        raise ValueError('Observation contract mismatch')
    return rows


def score(rows):
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden')):
        deep = evaluate([LLMTestCase(input=r['id'], actual_output=r['actual'], expected_output=r['expected']) for r in rows],
                        metrics=[RoutingMetric()], async_config=AsyncConfig(run_async=False),
                        cache_config=CacheConfig(write_cache=False, use_cache=False),
                        display_config=DisplayConfig(show_indicator=False, print_results=False, inspect_after_run=False))
        ragas = ragas_evaluate(EvaluationDataset(samples=[SingleTurnSample(user_input=r['id'], response=r['actual'], reference=r['expected']) for r in rows]),
                              metrics=[RoutingRagasMetric()], run_config=RunConfig(max_workers=1, max_retries=0), raise_exceptions=True, show_progress=False)
    return {'cases': len(rows), 'deepeval_passed': sum(r.metrics_data[0].score == 1 for r in deep.test_results),
            'ragas_passed': sum(r['moderation_routing'] == 1 for r in ragas.scores)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    args = parser.parse_args()
    report = {'A': score(load(args.before)), 'B': score(load(args.after)), 'provider_calls': 0, 'semantic_quality': 'not-measured'}
    print(json.dumps(report))
    if report['B']['deepeval_passed'] != 4 or report['B']['ragas_passed'] != 4:
        raise SystemExit(1)
