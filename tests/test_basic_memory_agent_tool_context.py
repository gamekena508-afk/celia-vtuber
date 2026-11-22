import asyncio

from celia.agent.agents.basic_memory_agent import BasicMemoryAgent
from celia.mcpp.types import ToolCallFunctionObject, ToolCallObject


class _FakeLLM:
    def __init__(self):
        self.calls = 0

    def chat_completion(self, _messages, _system, tools=None):
        async def stream():
            if self.calls == 0:
                self.calls += 1
                yield [
                    ToolCallObject(
                        id="call-1",
                        function=ToolCallFunctionObject(name="get_current_time", arguments="{}"),
                    )
                ]
            else:
                self.calls += 1
                yield "Resposta final."

        return stream()


class _FakeExecutor:
    def __init__(self):
        self.contexts = []

    async def execute_tools(
        self, tool_calls=None, caller_mode=None, trusted_context=None
    ):
        self.contexts.append(trusted_context)
        yield {"type": "final_tool_results", "results": [{"role": "tool", "content": "ok"}]}


def test_openai_tool_loop_forwards_trusted_context_to_executor():
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
    agent._system = "system"
    agent._mcp_prompt_string = ""
    agent.prompt_mode_flag = False
    agent._json_detector = None
    agent._tool_executor = _FakeExecutor()
    agent._add_message = lambda *_args, **_kwargs: None
    llm = _FakeLLM()
    trusted_context = object()

    async def collect():
        return [
            item
            async for item in agent._openai_tool_interaction_loop(
                [],
                [],
                active_llm=llm,
                trusted_context=trusted_context,
            )
        ]

    outputs = asyncio.run(collect())

    assert outputs == ["Resposta final."]
    assert agent._tool_executor.contexts == [trusted_context]
