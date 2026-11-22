import asyncio

from celia.agent.agents.basic_memory_agent import BasicMemoryAgent
from celia.agent.input_types import BatchInput, TextData, TextSource
from celia.core.continuous_behavior import continuous_behavior
from celia.mcpp.types import ToolCallFunctionObject, ToolCallObject


class _RepeatingToolLLM:
    def __init__(self):
        self.calls = 0

    def chat_completion(self, _messages, _system, tools=None):
        self.calls += 1

        async def stream():
            if tools is not None:
                yield [ToolCallObject(
                    id=f"call-{self.calls}",
                    function=ToolCallFunctionObject(
                        name="get_current_time", arguments='{"timezone":"UTC"}'
                    ),
                )]
            else:
                yield "Resposta final."

        return stream()


class _CountingExecutor:
    def __init__(self, denied=False):
        self.calls = 0
        self.denied = denied

    async def execute_tools(self, tool_calls=None, caller_mode=None, trusted_context=None):
        del caller_mode, trusted_context
        self.calls += len(tool_calls or [])
        content = (
            "Denied by MCP authorization gate: missing_trusted_context."
            if self.denied else "12:00"
        )
        yield {"type": "final_tool_results", "results": [{"role": "user", "content": content}]}


def _tool_agent(executor):
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
    agent._system = "system"
    agent._mcp_prompt_string = ""
    agent.prompt_mode_flag = False
    agent._json_detector = None
    agent._tool_executor = executor
    agent._add_message = lambda *_args, **_kwargs: None
    return agent


def _run_tool_loop(denied=False):
    llm = _RepeatingToolLLM()
    executor = _CountingExecutor(denied=denied)
    agent = _tool_agent(executor)

    async def collect():
        return [item async for item in agent._openai_tool_interaction_loop(
            [], [{"type": "function", "function": {"name": "get_current_time"}}],
            active_llm=llm,
        )]

    return llm, executor, asyncio.run(collect())


class _RecordingRouterLLM:
    def __init__(self, *, call_tool=True, text="__NO_TOOL__"):
        self.call_tool = call_tool
        self.text = text
        self.systems = []
        self.messages = []
        self.calls = 0

    def chat_completion(self, _messages, system, tools=None):
        self.calls += 1
        self.systems.append(system)
        self.messages.append(_messages)

        async def stream():
            if self.call_tool and tools is not None:
                yield [ToolCallObject(
                    id=f"router-{self.calls}",
                    function=ToolCallFunctionObject(
                        name="get_current_time", arguments='{"timezone":"UTC"}'
                    ),
                )]
            else:
                yield self.text

        return stream()


def test_dedicated_tool_router_uses_compact_system_but_final_llm_keeps_full_persona():
    executor = _CountingExecutor()
    agent = _tool_agent(executor)
    router = _RecordingRouterLLM(call_tool=True)
    final = _RecordingRouterLLM(call_tool=False, text="Resposta final da Célia.")
    full_system = "PERSONA COMPLETA " * 1200

    async def collect():
        return [item async for item in agent._openai_tool_interaction_loop(
            [{"role": "user", "content": "Que horas são?"}],
            [{"type": "function", "function": {"name": "get_current_time"}}],
            system_prompt=full_system,
            active_llm=router,
            final_response_llm=final,
        )]

    output = asyncio.run(collect())
    assert executor.calls == 1
    assert output == ["Resposta final da Célia."]
    assert router.systems
    assert len(router.systems[0]) < 2000
    assert "PERSONA COMPLETA" not in router.systems[0]
    assert final.systems == [full_system]
    assert final.messages
    assert all(isinstance(message.get("content"), str) for message in final.messages[0])
    assert all("tool_calls" not in message for message in final.messages[0])


def test_dedicated_tool_router_plain_text_never_leaks_as_celia_response():
    executor = _CountingExecutor()
    agent = _tool_agent(executor)
    router = _RecordingRouterLLM(call_tool=False, text="__NO_TOOL__")
    final = _RecordingRouterLLM(call_tool=False, text="Resposta normal da Célia.")

    async def collect():
        return [item async for item in agent._openai_tool_interaction_loop(
            [{"role": "user", "content": "Consulta algo que nenhuma tool disponível cobre."}],
            [{"type": "function", "function": {"name": "get_current_time"}}],
            system_prompt="PERSONA COMPLETA",
            active_llm=router,
            final_response_llm=final,
        )]

    output = asyncio.run(collect())
    assert executor.calls == 0
    assert output == ["Resposta normal da Célia."]
    assert "__NO_TOOL__" not in " ".join(map(str, output))


def test_identical_allowed_tool_executes_once_per_turn():
    llm, executor, output = _run_tool_loop(denied=False)
    assert executor.calls == 1
    assert llm.calls == 3
    assert output == ["Resposta final."]


def test_identical_denied_tool_executes_once_per_turn():
    llm, executor, output = _run_tool_loop(denied=True)
    assert executor.calls == 1
    assert llm.calls == 3
    assert output == ["Resposta final."]


class _CandidateLLM:
    def __init__(self, candidates):
        self.candidates = list(candidates)
        self.calls = 0

    def chat_completion(self, _messages, _system, tools=None):
        del tools
        index = min(self.calls, len(self.candidates) - 1)
        text = self.candidates[index]
        self.calls += 1

        async def stream():
            yield {"type": "text_delta", "text": text}

        return stream()


def _run_chat(candidates, scene="chat"):
    llm = _CandidateLLM(candidates)
    agent = BasicMemoryAgent(
        llm=llm,
        live2d_model=None,
        system="Celia",
        use_mcpp=False,
        memory_book_enabled=False,
    )
    old_profile = continuous_behavior._profile_manager._profile
    continuous_behavior._profile_manager._profile = "roteiro"

    async def collect():
        return [item async for item in agent.chat(BatchInput(
            texts=[TextData(TextSource.INPUT, "me ajuda")],
            metadata={"scene_profile": scene},
        ))]

    try:
        output = asyncio.run(collect())
    finally:
        continuous_behavior._profile_manager._profile = old_profile
    return llm, output


def test_judge_no_longer_regenerates_the_reply():
    # Jev (2026-10-06): o Juiz saiu do caminho crítico. Uma geração só.
    llm, output = _run_chat([
        "Tu ta repetindo isso.",
        "SEGUNDA GERACAO NAO DEVE ACONTECER",
    ])
    assert llm.calls == 1
    spoken = " ".join(map(str, output))
    assert "SEGUNDA" not in spoken


def test_no_judge_retry_budget_in_any_scene():
    llm, output = _run_chat([
        "Vai nessa, so nao reclama.",
        "Vai nessa, so nao reclama.",
        "Vai nessa, so nao reclama.",
        "QUARTA GERACAO NAO DEVE ACONTECER",
    ], scene="gameplay")
    assert llm.calls == 1
    assert output


def test_model_roles_keep_70b_primary_v5_fallback_only_and_v5_out_of_brain_router():
    from pathlib import Path
    import yaml
    from celia.core.brain_router import BrainRouter

    config = yaml.safe_load(Path("conf.yaml").read_text(encoding="utf-8"))
    llm = config["character_config"]["agent_config"]["llm_configs"]["openai_compatible_llm"]
    assert llm["model"] == "cf/@cf/meta/llama-3.1-70b-instruct-fp8-fast"
    assert llm["local_fallback_base_url"] == "http://127.0.0.1:18080/v1"
    assert llm["local_fallback_model"] == "celia-v5"
    assert not llm.get("fallback_model")
    providers = {p.name for p in BrainRouter().providers()}
    assert "local_v5" not in providers
    assert {"codex_luna", "grok45", "gemini_9router"}.issubset(providers)


def test_static_facts_do_not_force_web_tools():
    from celia.agent.bridge_client import needs_tool
    assert needs_tool("qual é a capital do Brasil?") is None
    assert needs_tool("quem foi Albert Einstein?") is None
    assert needs_tool("o que é Kubernetes?") is None


def test_fresh_or_explicit_research_still_uses_search_tool():
    from celia.agent.bridge_client import needs_tool
    assert needs_tool("pesquisa pra mim a capital da Austrália") == "search"
    assert needs_tool("qual é o presidente atual do Brasil?") == "search"
    assert needs_tool("me dá uma notícia recente sobre IA") == "search"
    assert needs_tool("qual a cotação do dólar?") == "search"


def test_tool_intent_regression_estado_atual_horario_noticia_e_servico():
    from celia.agent.bridge_client import needs_tool

    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
    cases = [
        ("consulta teu estado atual", "system"),
        ("qual o horário atual?", "time"),
        ("qual a notícia atual sobre IA?", "search"),
        ("abre o status atual do serviço", "system"),
    ]
    for text, expected in cases:
        primary = needs_tool(text)
        secondary = agent._detect_intents(text)
        assert primary == expected, (text, primary, secondary)
        assert expected in secondary, (text, primary, secondary)
        if expected != "search":
            assert "search" not in secondary, (text, primary, secondary)


def test_read_only_system_request_never_offers_write_or_execute_tools():
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
    names = [
        "get_current_time", "status_servico", "status_minecraft_bot",
        "execute_command", "create_file", "run_python", "open_terminal", "open_browser",
    ]
    tools = [{"type": "function", "function": {"name": name}} for name in names]
    filtered = agent._filter_tools_for_turn(
        tools,
        "Use somente leitura para consultar o status atual do serviço; não altere nada.",
        primary_intent="system",
        secondary_intents={"system"},
    )
    kept = {agent._tool_name(tool) for tool in filtered}
    assert kept == {"get_current_time", "status_servico", "status_minecraft_bot"}


def test_public_external_tool_schema_allows_search_time_images_polls_only():
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
    agent._enquete_tool_names = {"criar_enquete", "registrar_voto", "fechar_enquete"}
    names = [
        "get_current_time", "search", "fetch_content", "ler_memoria", "salvar_memoria",
        "execute_command", "gerar_imagem", "editar_imagem",
        "criar_ou_editar_imagem", "criar_enquete",
    ]
    tools = [{"type": "function", "function": {"name": name}} for name in names]
    filtered = agent._filter_tools_for_turn(tools, "gera uma imagem e cria enquete", origin="chat")
    assert {agent._tool_name(tool) for tool in filtered} == {
        "get_current_time", "search", "gerar_imagem", "editar_imagem",
        "criar_ou_editar_imagem", "criar_enquete",
    }
