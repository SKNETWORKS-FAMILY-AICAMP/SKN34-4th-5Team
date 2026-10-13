"""CI-only Luna request settings around unchanged canonical evaluation runners."""
import runpy
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'gpt-6-luna'
SETTINGS = {'generation_api': 'responses', 'generation_reasoning': 'medium', 'generation_max_output_tokens': 1200,
            'judge_api': 'chat.completions/instructor-json', 'judge_reasoning': 'medium',
            'judge_max_completion_tokens': 1800, 'sampling': 'omitted', 'sdk_retries': 0, 'instructor_attempts': 1}


def validate_generation_output(response):
    output = response.get('output')
    if response.get('status') != 'completed' or response.get('error') or not isinstance(output, list) or not output:
        raise ValueError('CI generation requires completed output')
    for item in output:
        if not isinstance(item, dict):
            raise ValueError('Malformed CI generation output')
        if item.get('type') == 'reasoning':
            summary = item.get('summary')
            if not isinstance(summary, list) or any(
                not isinstance(block, dict) or block.get('type') != 'summary_text'
                or not isinstance(block.get('text'), str) for block in summary
            ) or ('encrypted_content' in item and item['encrypted_content'] is not None
                  and not isinstance(item['encrypted_content'], str)):
                raise ValueError('Malformed CI reasoning metadata')
        elif item.get('type') == 'message':
            content = item.get('content')
            if item.get('role') != 'assistant' or item.get('status') != 'completed' or not isinstance(content, list) or any(
                not isinstance(block, dict) or block.get('type') != 'output_text'
                or not isinstance(block.get('text'), str) or not isinstance(block.get('annotations'), list)
                for block in content
            ):
                raise ValueError('CI generation requires valid output_text only')
        else:
            raise ValueError('Unknown CI generation output')


def generation_text(message):
    content = message.content
    if message.additional_kwargs.get('refusal') or not isinstance(content, (str, list)):
        raise ValueError('CI generation requires non-refusal text')
    if isinstance(content, list) and any(
        not isinstance(block, str) and (
            not isinstance(block, dict) or block.get('type') not in ('text', 'reasoning')
            or (block.get('type') == 'text' and not isinstance(block.get('text'), str))
        ) for block in content
    ):
        raise ValueError('CI generation requires valid text blocks')
    text = str(message.text)
    if not text.strip():
        raise ValueError('CI generation requires nonempty text')
    reasoning = [block for block in content if isinstance(block, dict) and block.get('type') == 'reasoning'] if isinstance(content, list) else []
    extra = dict(message.additional_kwargs)
    if reasoning:
        extra['reasoning'] = reasoning
    return message.model_copy(update={'content': text, 'additional_kwargs': extra})


def generation_factory(original):
    def create(*args, **kwargs):
        if kwargs.get('model') != MODEL:
            raise ValueError('CI generation requires Luna; fallback prohibited')
        kwargs.pop('temperature', None)
        kwargs.pop('top_p', None)
        kwargs.update(use_responses_api=True, reasoning_effort='medium', max_retries=0)
        from langchain_core.runnables import RunnableLambda
        model = original(*args, **kwargs)
        responses = model.root_client.responses.with_raw_response
        create_response = responses.create
        def checked_response(*args, **params):
            raw = create_response(*args, **params)
            # Validate wire content before the SDK/LangChain can discard unknown blocks.
            validate_generation_output(raw.http_response.json())
            return raw
        responses.create = checked_response
        return model | RunnableLambda(generation_text)
    return create


def judge_factory(original):
    def create(model, **kwargs):
        if model != MODEL:
            raise ValueError('CI judge requires Luna; fallback prohibited')
        # Ragas defaults include temperature/top_p/max_tokens; remove after factory merge.
        client = kwargs['client']
        create_completion = client.chat.completions.create
        async def completion(*args, **params):
            for key in ('temperature', 'top_p', 'max_tokens'):
                params.pop(key, None)
            return await create_completion(*args, **params)
        client.chat.completions.create = completion
        llm = original(model, **kwargs)
        for key in ('temperature', 'top_p', 'max_tokens'):
            llm.model_args.pop(key, None)
        # The SDK-boundary wrapper above also removes Instructor-injected temperature.
        llm.model_args.update(reasoning_effort='medium', max_completion_tokens=1800, max_retries=1)
        return llm
    return create


def main():
    mode, *args = sys.argv[1:]
    sys.path.insert(0, str(ROOT / 'backend'))
    if mode == 'export':
        import langchain_openai
        sys.argv = ['backend/manage.py', *args]
        with patch.object(langchain_openai, 'ChatOpenAI', generation_factory(langchain_openai.ChatOpenAI)):
            runpy.run_path(str(ROOT / 'backend/manage.py'), run_name='__main__')
    elif mode == 'judge':
        import ragas.llms
        sys.argv = ['eval_ragas.py', *args]
        with patch.object(ragas.llms, 'llm_factory', judge_factory(ragas.llms.llm_factory)):
            runpy.run_path(str(ROOT / 'backend/llm/v2/evaluation/eval_ragas.py'), run_name='__main__')
    else:
        raise ValueError('Only canonical export/judge supported')


if __name__ == '__main__':
    main()
