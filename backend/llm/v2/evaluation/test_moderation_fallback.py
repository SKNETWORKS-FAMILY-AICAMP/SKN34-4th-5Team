"""Actual worker routing with synthetic failures; no provider, production DB, or semantic judge."""
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

os.environ.update(DEEPEVAL_TELEMETRY_OPT_OUT='YES', DEEPEVAL_DISABLE_DOTENV='1',
                  DEEPEVAL_DISABLE_LEGACY_KEYFILE='1', RAGAS_DO_NOT_TRACK='true',
                  PYTHON_DOTENV_DISABLED='1', LANGSMITH_TRACING='false')
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from django.conf import settings
settings.configure(INSTALLED_APPS=['django.contrib.auth', 'django.contrib.contenttypes', 'accounts', 'community'],
                   AUTH_USER_MODEL='accounts.CustomUser', DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
                   COMMUNITY_POLICY_VERSION='test-v1', COMMUNITY_MODERATION_MAX_ATTEMPTS=3,
                   COMMUNITY_MODERATION_MODE='manual')
import django
django.setup()
from community.models import CommunityPost
from community.moderation_config import current_mode
from services.community_moderation import finish
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_community_moderation import (
    evaluate, LLMTestCase, RoutingMetric, AsyncConfig, CacheConfig, DisplayConfig,
    ragas_evaluate, EvaluationDataset, SingleTurnSample, RoutingRagasMetric, RunConfig,
)

# Frozen before source changes. New contract, separate from legacy native adapter expectations.
CASES = [('missing-key', RuntimeError('synthetic secret'), []),
         ('timeout', TimeoutError('synthetic secret'), []),
         ('malformed', ValueError('synthetic secret'), []),
         ('image-error', TimeoutError('synthetic secret'), ['fixture'])]
outputs = []
with patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden')):
    for name, error, images in CASES:
        obj = CommunityPost(post_number=1, content_revision=1, moderation_status='pending', approved_revision=None)
        obj.save = MagicMock()
        job = NS(pk=1, state='running', lease_token='fixture', lease_until=None, revision=1, attempts=1,
                 payload={'mode': 'openai', 'policy_version': 'test-v1', 'image_ids': images},
                 findings=[], evidence={}, error_code='', save=MagicMock())
        # Publication side effects are covered by the disposable-DB Django suite.
        with patch('services.community_moderation.locked_target', return_value=obj), \
                patch('community.moderation_service.publish_locked'), \
                patch.object(CommunityPost, 'moderation_jobs') as previous, \
                patch('services.community_moderation.ModerationJob.objects.select_for_update') as manager:
            previous.filter.return_value.exclude.return_value = []
            manager.return_value.filter.return_value.first.return_value = job
            finish(NS(pk=1, lease_token='fixture'), error=error)
        actual = f'{job.state}/{obj.moderation_status}/' + ','.join(sorted(f['code'] for f in job.findings))
        actual += '/public=' + str(obj.approved_revision == obj.content_revision)
        expected = 'done/review/' + ('image_review,manual_review/public=False' if images else 'manual_review/public=True')
        outputs.append((name, actual, expected))
        assert 'synthetic secret' not in json.dumps([job.error_code, job.evidence, job.findings])
    with patch('community.moderation_config.ModerationSettings.objects.filter') as manager:
        manager.return_value.values_list.return_value.first.return_value = None
        with patch.dict(os.environ, {'COMMUNITY_MODERATION_MODE': 'manual'}):
            outputs.append(('default-ignores-env', current_mode(), 'openai'))
        manager.return_value.values_list.return_value.first.return_value = 'manual'
        outputs.append(('db-manual', current_mode(), 'manual'))
    deep = evaluate([LLMTestCase(input=n, actual_output=a, expected_output=e) for n, a, e in outputs],
                    metrics=[RoutingMetric()], async_config=AsyncConfig(run_async=False),
                    cache_config=CacheConfig(write_cache=False, use_cache=False),
                    display_config=DisplayConfig(show_indicator=False, print_results=False, inspect_after_run=False))
    ragas = ragas_evaluate(EvaluationDataset(samples=[SingleTurnSample(user_input=n, response=a, reference=e) for n, a, e in outputs]),
                          metrics=[RoutingRagasMetric()], run_config=RunConfig(max_workers=1, max_retries=0),
                          raise_exceptions=True, show_progress=False)
print(json.dumps({'outputs': outputs, 'deepeval_passed': sum(r.metrics_data[0].score == 1 for r in deep.test_results),
                  'ragas_passed': sum(r['moderation_routing'] == 1 for r in ragas.scores),
                  'provider_calls': 0, 'semantic_quality': 'not-measured'}))
if '--baseline' not in sys.argv:
    assert len(deep.test_results) == len(ragas.scores) == len(outputs)
    assert all(a == e for _, a, e in outputs)
    assert all(r.metrics_data[0].score == 1 for r in deep.test_results)
    assert all(r['moderation_routing'] == 1 for r in ragas.scores)
