import asyncio
from types import SimpleNamespace

import pytest
from openai import NOT_GIVEN

from celia.agent.stateless_llm.openai_compatible_llm import AsyncLLM


@pytest.mark.parametrize("tools", [None, NOT_GIVEN, []])
def test_optional_tools_do_not_break_plain_text_stream(tools):
    requests = []

    async def stream():
        yield SimpleNamespace(choices=None, usage=None)
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="pronta", tool_calls=None))],
            usage=None,
        )

    async def create(**kwargs):
        requests.append(kwargs)
        class Stream:
            def __aiter__(self):
                return stream()

            async def close(self):
                pass

        return Stream()

    llm = AsyncLLM(model="test", base_url="http://example.invalid/v1")
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    async def run():
        return [part async for part in llm.chat_completion(
            [{"role": "user", "content": "oi"}], tools=tools,
        )]

    assert asyncio.run(run()) == ["pronta"]
    assert len(requests) == 1
    assert requests[0].get("tools") is not None
