"""Network-free relocation checks; run with the isolated highlight environment."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.update(DEEPEVAL_TELEMETRY_OPT_OUT="YES", DEEPEVAL_DISABLE_DOTENV="1",
                  DEEPEVAL_DISABLE_LEGACY_KEYFILE="1", PYTHON_DOTENV_DISABLED="1",
                  RAGAS_DO_NOT_TRACK="true", LANGSMITH_TRACING="false")


class RelocationTest(unittest.TestCase):
    def test_legacy_imports_data_and_cli_help(self):
        from llm import highlight_deepeval as old_deep, highlight_ragas as old_ragas
        from llm.v2.evaluation import highlight_deepeval, highlight_ragas
        self.assertIs(old_deep.CorpusMetric, highlight_deepeval.CorpusMetric)
        self.assertIs(old_ragas.CorpusRagasMetric, highlight_ragas.CorpusRagasMetric)
        self.assertEqual((ROOT / "rag_test/golden").resolve(),
                         ROOT / "backend/llm/v2/evaluation/golden")
        self.assertEqual((ROOT / "backend/llm/tests/highlight_deepeval_sample.json").resolve(),
                         ROOT / "backend/llm/v2/evaluation/highlight_deepeval_sample.json")
        for script in ("backend/llm/v2/evaluation/highlight_deepeval.py",
                       "backend/llm/v2/evaluation/highlight_ragas.py",
                       "backend/llm/v2/evaluation/eval_ragas.py",
                       "backend/llm/highlight_deepeval.py", "backend/llm/highlight_ragas.py",
                       "rag_test/eval_ragas.py"):
            result = subprocess.run([sys.executable, "-I", str(ROOT / script), "--help"],
                                    cwd=tempfile.gettempdir(), capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("usage:", result.stdout)

    def test_real_framework_evaluation_without_network(self):
        # Empty completed negative is a deterministic valid abstention, not a provider output.
        with patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")):
            from llm.v2.evaluation.highlight_deepeval import (
                CorpusMetric, LLMTestCase, evaluate, AsyncConfig, CacheConfig, DisplayConfig)
            from llm.v2.evaluation.highlight_ragas import (
                CorpusRagasMetric, EvaluationDataset, SingleTurnSample, RunConfig,
                evaluate as ragas_evaluate)
            label = json.dumps({"references": []})
            result = json.dumps({"complete": True, "no_match": True, "retrieval_only": [],
                                 "decisions": [], "matches": [], "candidate_rows": {}})
            deep = evaluate([LLMTestCase(input="negative", expected_output=label, actual_output=result)],
                            metrics=[CorpusMetric("no_match")],
                            async_config=AsyncConfig(run_async=False),
                            cache_config=CacheConfig(write_cache=False, use_cache=False),
                            display_config=DisplayConfig(show_indicator=False, print_results=False,
                                                         inspect_after_run=False))
            self.assertEqual(deep.test_results[0].metrics_data[0].score, 1.0)
            ragas = ragas_evaluate(EvaluationDataset(samples=[SingleTurnSample(response=result, reference=label)]),
                                  metrics=[CorpusRagasMetric("no_match")],
                                  run_config=RunConfig(max_workers=1, max_retries=0),
                                  raise_exceptions=True, show_progress=False)
            self.assertEqual(ragas.scores, [{"no_match": 1.0}])


if __name__ == "__main__":
    unittest.main()
