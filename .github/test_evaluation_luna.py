"""Real SDK interfaces + synthetic HTTP only; not provider support evidence."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.update(RAGAS_DO_NOT_TRACK='true', LANGSMITH_TRACING='false',
                  LANGCHAIN_TRACING='false', LANGCHAIN_TRACING_V2='false')
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('luna', HERE / 'evaluation_luna.py')
luna = importlib.util.module_from_spec(spec)
spec.loader.exec_module(luna)


class LunaSDKTest(unittest.TestCase):
    def test_workflow_runs_both_dependency_environments(self):
        workflow = (HERE / 'workflows/evaluation-regression.yml').read_text()
        backend, highlight = workflow.split('  highlight-deterministic:', 1)
        self.assertIn('python -I .github/test_evaluation_luna.py LunaExportIntegrationTest', backend)
        self.assertIn('python -I .github/test_evaluation_luna.py LunaSDKTest', highlight)

    def test_generation_responses_request(self):
        import httpx
        from langchain_openai import ChatOpenAI
        requests = []
        def transport(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'id':'resp_fixture','object':'response','created_at':1,'status':'completed',
                'model':'gpt-6-luna','output':[{'id':'rs_fixture','type':'reasoning',
                'summary':[{'type':'summary_text','text':'Synthetic reasoning metadata'}], 'encrypted_content':'synthetic'},
                {'id':'msg_fixture','type':'message','status':'completed','role':'assistant',
                'content':[{'type':'output_text','text':'Synthetic answer','annotations':[]}]}],
                'usage':{'input_tokens':1,'output_tokens':2,'total_tokens':3}})
        model = luna.generation_factory(ChatOpenAI)(model=luna.MODEL, timeout=30, max_tokens=1200, api_key='synthetic',
            http_client=httpx.Client(transport=httpx.MockTransport(transport)))
        response = model.invoke('Synthetic query')
        self.assertEqual(response.content, 'Synthetic answer')
        self.assertEqual(response.usage_metadata['total_tokens'], 3)
        self.assertEqual(response.response_metadata['model_name'], luna.MODEL)
        self.assertEqual(response.additional_kwargs['reasoning'][0]['summary'][0]['text'], 'Synthetic reasoning metadata')
        self.assertEqual(response.additional_kwargs['reasoning'][0]['encrypted_content'], 'synthetic')
        body = requests[0]
        self.assertEqual(body['model'], luna.MODEL)
        self.assertEqual(body['reasoning']['effort'], 'medium')
        self.assertEqual(body['max_output_tokens'], 1200)
        self.assertNotIn('temperature', body)
        self.assertNotIn('top_p', body)

    def test_raw_output_validation_before_lossy_conversion(self):
        text = {'type': 'output_text', 'text': 'Synthetic answer', 'annotations': []}
        for bad in [{'type': 'unknown_fixture'}, {'type': 'refusal', 'refusal': 'No'},
                    {'type': 'output_text', 'text': 3, 'annotations': []},
                    {'type': 'output_text', 'text': 'OK'}, None]:
            with self.subTest(content=bad), self.assertRaises(ValueError):
                luna.validate_generation_output({'status': 'completed', 'output': [
                    {'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': [text, bad]}]})
        for bad in [{'type': 'unknown_fixture'}, {'type': 'reasoning', 'summary': [{}]},
                    {'type': 'reasoning', 'summary': [], 'encrypted_content': 3}, None]:
            with self.subTest(output=bad), self.assertRaises(ValueError):
                luna.validate_generation_output({'status': 'completed', 'output': [bad]})

    def test_generation_text_validation_and_metadata(self):
        from langchain_core.messages import AIMessage
        for content, expected in [(' Plain text ', ' Plain text '),
                                  ([{'type': 'text', 'text': 'First '}, {'type': 'text', 'text': 'second'}], 'First second')]:
            message = AIMessage(content=content, response_metadata={'fixture': True},
                                usage_metadata={'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3})
            result = luna.generation_text(message)
            self.assertEqual(result.content, expected)
            self.assertEqual(result.response_metadata, message.response_metadata)
            self.assertEqual(result.usage_metadata, message.usage_metadata)
            self.assertEqual(message.content, content)
        for content in ['', '  ', [], [{'type': 'refusal', 'refusal': 'No'}],
                        [{'type': 'text'}], [{'type': 'text', 'text': 3}],
                        [{'type': 'image_url', 'image_url': 'synthetic'}],
                        [{'type': 'text', 'text': 'OK'}, {'type': 'refusal', 'refusal': 'No'}], 3]:
            with self.subTest(content=content), self.assertRaises(ValueError):
                luna.generation_text(AIMessage(content='').model_copy(update={'content': content}))
        with self.assertRaises(ValueError):
            luna.generation_text(AIMessage(content='OK', additional_kwargs={'refusal': 'No'}))

    def test_judge_real_ragas_instructor_structured_request(self):
        import httpx
        from openai import AsyncOpenAI
        from pydantic import BaseModel
        from ragas.llms import llm_factory
        class Verdict(BaseModel):
            score: float
        requests = []
        def transport(request):
            body=json.loads(request.content)
            requests.append(body)
            return httpx.Response(200,json={'id':'chatcmpl_fixture','object':'chat.completion','created':1,'model':luna.MODEL,
                'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'{"score":0.5}'}}],
                'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})
        async def run():
            async with AsyncOpenAI(api_key='synthetic',max_retries=0,http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport))) as client:
                judge=luna.judge_factory(llm_factory)(luna.MODEL,client=client,temperature=0,max_tokens=1800,max_retries=1)
                self.assertEqual((await judge.agenerate('Synthetic judgment',Verdict)).score,0.5)
        asyncio.run(run())
        self.assertEqual(len(requests),1)
        body=requests[0]
        self.assertEqual(body['model'],luna.MODEL)
        self.assertEqual(body['reasoning_effort'],'medium')
        self.assertEqual(body['max_completion_tokens'],1800)
        self.assertEqual(body['response_format'], {'type': 'json_object'})
        self.assertIn('score', json.dumps(body['messages']))
        for key in ('temperature','top_p','max_tokens'):
            self.assertNotIn(key,body)


class LunaExportIntegrationTest(unittest.TestCase):
    def test_actual_adapter_django_jsonl_synthetic_only(self):
        import httpx
        from langchain_openai import ChatOpenAI
        os.environ.update(PYTHON_DOTENV_DISABLED='1', DJANGO_SETTINGS_MODULE='config.settings',
                          OPENAI_API_KEY='synthetic-fixture-only', OPENROUTER_API_KEY='synthetic-fixture-only',
                          LANGSMITH_TRACING='false', LANGCHAIN_TRACING='false', LANGCHAIN_TRACING_V2='false')
        sys.path.insert(0, str(HERE.parent / 'backend'))
        from django.conf import settings
        settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
        settings.DATABASE_ROUTERS = []
        import django
        django.setup()
        docs = [{'doc_id': 'synthetic-doc', 'category': 'RULE', 'content': 'Synthetic context, not provider evidence'}]
        cases = [('multi', [{'type': 'output_text', 'text': 'First ', 'annotations': []},
                            {'type': 'output_text', 'text': 'second', 'annotations': []}], 'First second'),
                 ('empty', [], None), ('whitespace', [{'type': 'output_text', 'text': ' ', 'annotations': []}], None),
                 ('refusal', [{'type': 'refusal', 'refusal': 'No'}], None),
                 ('malformed', [{'type': 'output_text', 'annotations': []}], None),
                 ('nontext', [{'type': 'unknown_fixture', 'data': 'not text'}], None),
                 ('mixed_unknown', [{'type': 'output_text', 'text': 'Synthetic answer', 'annotations': []},
                                    {'type': 'unknown_fixture', 'data': 'not text'}], None),
                 ('mixed_refusal', [{'type': 'output_text', 'text': 'Synthetic answer', 'annotations': []},
                                    {'type': 'refusal', 'refusal': 'No'}], None)]
        def denied(*args, **kwargs):
            raise RuntimeError('synthetic regression: network forbidden')
        scratch = Path(os.environ.get('RUNNER_TEMP') or os.environ.get('TMPDIR') or HERE.parent / 'scratch')
        scratch.mkdir(parents=True, exist_ok=True)
        with patch.object(socket.socket, 'connect', denied), patch.object(socket.socket, 'connect_ex', denied), \
             patch.object(socket, 'create_connection', denied), tempfile.TemporaryDirectory(prefix='luna-synthetic-', dir=scratch) as tmp:
            golden = Path(tmp) / 'golden.jsonl'
            golden.write_text(json.dumps({'id': 'r16', 'question': 'Synthetic question',
                'reference': 'Independent synthetic reference', 'reference_doc_ids': ['synthetic-doc']}) + '\n')
            for name, content, expected in cases:
                with self.subTest(fixture=name):
                    requests = []
                    def transport(request):
                        requests.append(json.loads(request.content))
                        return httpx.Response(200, json={'id': 'resp_fixture', 'object': 'response', 'created_at': 1,
                            'status': 'completed', 'model': luna.MODEL,
                            'output': [{'id': 'msg_fixture', 'type': 'message', 'status': 'completed',
                                        'role': 'assistant', 'content': content}],
                            'usage': {'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3}})
                    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
                        def actual_sdk(*args, **kwargs):
                            return ChatOpenAI(*args, **kwargs, api_key='synthetic-fixture-only', http_client=client)
                        target = Path(tmp) / f'{name}.jsonl'
                        argv = ['evaluation_luna.py', 'export', 'export_ragas', '--golden', str(golden),
                                '--ids', 'r16', '--limit', '1', '--k', '5', '--mode', 'vector',
                                '--generation-model', luna.MODEL, '--output', str(target)]
                        code = 0
                        with patch.object(sys, 'argv', argv), \
                             patch('langchain_openai.ChatOpenAI', side_effect=actual_sdk), \
                             patch('langchain_openai.OpenAIEmbeddings'), \
                             patch('llm.tools.knowledge.search_kbo_rows', return_value=docs):
                            try:
                                luna.main()
                            except SystemExit as exc:
                                code = exc.code
                    rows = [json.loads(line) for line in target.read_text().splitlines()]
                    self.assertEqual(len(rows), 1)
                    row = rows[0]
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(requests[0]['model'], luna.MODEL)
                    self.assertEqual(row['retrieved_doc_ids'], ['synthetic-doc'])
                    self.assertEqual(row['response'], expected)
                    if expected is None:
                        self.assertNotEqual(code, 0)
                        self.assertTrue(row['errors'])
                    else:
                        self.assertEqual(code, 0)
                        self.assertEqual(row['errors'], {})
                        self.assertEqual(row['usage']['generation'], {'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3})


if __name__ == '__main__':
    # Optional separate dependency environments: generation backend / judge Ragas.
    def denied(*args, **kwargs):
        raise RuntimeError('network forbidden')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = denied
    unittest.main(verbosity=2)
