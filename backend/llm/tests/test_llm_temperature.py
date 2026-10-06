"""Serialize the production constructors without importing agents or calling OpenAI."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from langchain_openai import ChatOpenAI


class ResponsesPayloadTest(TestCase):
    def test_luna_constructors_omit_temperature(self):
        root = Path(__file__).resolve().parents[1]
        paths = [
            "v2/agent/common.py", "tools/knowledge.py",
            "v1/rag/venue/agent.py", "v1/rag/nearby/agent.py",
            "v1/rag/course/agent.py", "v1/rag/club/agent.py",
            "v1/rag/assistant/pipeline.py",
        ]
        for configured, expected in [(None, "gpt-6-luna"), ("", "gpt-6-luna"), ("override-model", "override-model")]:
            env = {} if configured is None else {"LLM_MODEL": configured}
            with patch.dict(os.environ, env, clear=True):
                for path in paths:
                    with self.subTest(model=expected, path=path):
                        tree = ast.parse((root / path).read_text())
                        scope = {
                            "os": os, "ChatOpenAI": ChatOpenAI,
                            "settings": SimpleNamespace(USAGE_MAX_CALL_OUTPUT_TOKENS=4000),
                        }
                        for node in tree.body:
                            if isinstance(node, ast.Assign) and any(
                                isinstance(t, ast.Name) and t.id == "LLM_MODEL" for t in node.targets
                            ):
                                scope["LLM_MODEL"] = eval(compile(ast.Expression(node.value), path, "eval"), scope)
                        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                                 and isinstance(node.func, ast.Name) and node.func.id == "ChatOpenAI"]
                        self.assertEqual(len(calls), 1)
                        calls[0].keywords.append(ast.keyword(arg="api_key", value=ast.Constant("offline-test")))
                        expression = ast.fix_missing_locations(ast.Expression(calls[0]))
                        model = eval(compile(expression, path, "eval"), scope)
                        payload = model._get_request_payload([{"role": "user", "content": "test"}])
                        self.assertNotIn("temperature", payload)
                        self.assertEqual(payload["model"], expected)
                        self.assertEqual(payload["reasoning"], {"effort": "medium"})
                        self.assertEqual(payload["max_output_tokens"], 4000)
                        self.assertEqual(model.request_timeout, 25)
                        self.assertEqual(model.max_retries, 0)
                        self.assertTrue(model.use_responses_api)
