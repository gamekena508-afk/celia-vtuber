"""Fluxo completo Jev -> executor (orquestração) -> Célia, offline, com fakes.

Regras fechadas em 2026-10-04:
  - Jev decide/coordena e é o único que chama ferramenta (tool call nativo);
  - Célia conversa e escreve a fala; NÃO executa ferramenta, nem por JSON dentro do texto dela;
  - o executor (ToolExecutor + autorização) é quem executa, com o contexto confiável do turno.
"""
import asyncio
import json

import pytest

from celia.agent.agents.basic_memory_agent import BasicMemoryAgent
from celia.mcpp.json_detector import StreamJSONDetector
from celia.mcpp.types import ToolCallFunctionObject, ToolCallObject

POST_ARGS = json.dumps({"mensagem": "A live começa às oito!", "marcar_todos": False})
JSON_MCP = ('{"mcp_server": "discord-actions", "tool": "discord_postar_mensagem", '
            '"arguments": "{\\"mensagem\\": \\"oi\\"}"}')
JSON_DIRETO = '{"name": "gerar_imagem", "parameters": {"prompt": "gato"}}'


class FakeJev:
    """Roteador: 1ª chamada devolve tool call nativo. Registra system e tools recebidos."""

    def __init__(self, tool_name="discord_postar_mensagem", args=POST_ARGS):
        self.tool_name, self.args, self.calls = tool_name, args, []

    def chat_completion(self, _messages, system, tools=None):
        self.calls.append((system, tools))

        async def stream():
            yield [ToolCallObject(id="c1", function=ToolCallFunctionObject(
                name=self.tool_name, arguments=self.args))]
        return stream()


class FakeCelia:
    """Persona: escreve a fala em pedaços. Registra system e tools recebidos."""

    def __init__(self, chunks):
        self.chunks, self.calls = chunks, []

    def chat_completion(self, _messages, system, tools=None):
        self.calls.append((system, tools))

        async def stream():
            for c in self.chunks:
                yield c
        return stream()


class FakeExecutor:
    """Orquestração de execução: só ela executa. Guarda o que rodou e o contexto confiável."""

    def __init__(self):
        self.executed, self.contexts = [], []

    def process_tool_from_prompt_json(self, data):
        out = []
        for item in data:
            args = item.get("arguments")
            out.append({"name": item.get("tool"), "server": item.get("mcp_server"),
                        "args": json.loads(args) if isinstance(args, str) else args, "id": "p0"})
        return out

    async def execute_tools(self, tool_calls=None, caller_mode=None, trusted_context=None):
        for call in tool_calls or []:
            name = call["name"] if isinstance(call, dict) else call.function.name
            self.executed.append((name, caller_mode))
        self.contexts.append(trusted_context)
        yield {"type": "final_tool_results", "results": [{"role": "tool", "content": "feito"}]}


def _agent(executor):
    ag = BasicMemoryAgent.__new__(BasicMemoryAgent)
    ag._system = "PERSONA COMPLETA DA CELIA"
    ag._mcp_prompt_string = ""
    ag.prompt_mode_flag = False
    ag._json_detector = StreamJSONDetector()
    ag._tool_executor = executor
    ag._add_message = lambda *_a, **_k: None
    return ag


def _run(jev, celia, executor, ctx=None):
    ag = _agent(executor)
    tools = [{"type": "function", "function": {"name": jev.tool_name, "parameters": {}}}]

    async def collect():
        return [x async for x in ag._openai_tool_interaction_loop(
            [{"role": "user", "content": "posta no discord que a live começa"}], tools,
            "PERSONA COMPLETA DA CELIA", active_llm=jev, final_response_llm=celia,
            trusted_context=ctx)]
    return asyncio.run(collect())


def _fala(outputs):
    return "".join(x for x in outputs if isinstance(x, str))


# ── o caminho feliz ──
def test_jev_executa_a_ferramenta_e_celia_so_fala():
    jev, celia, ex, ctx = FakeJev(), FakeCelia(["Postei lá, ", "pode olhar!"]), FakeExecutor(), object()
    out = _run(jev, celia, ex, ctx)
    assert ex.executed == [("discord_postar_mensagem", "OpenAI")]
    assert ex.contexts == [ctx]                       # execução recebe o contexto confiável do turno
    assert _fala(out) == "Postei lá, pode olhar!"
    assert len(jev.calls) == 1 and len(celia.calls) == 1


def test_jev_recebe_prompt_de_roteador_e_tools_celia_recebe_persona_e_nenhuma_tool():
    jev, celia, ex = FakeJev(), FakeCelia(["ok"]), FakeExecutor()
    _run(jev, celia, ex)
    jev_system, jev_tools = jev.calls[0]
    celia_system, celia_tools = celia.calls[0]
    assert "tool-selection router" in jev_system and jev_tools
    assert celia_system == "PERSONA COMPLETA DA CELIA"
    assert not celia_tools                             # Célia nunca recebe ferramentas na fala


# ── a regra: o texto da Célia não executa nada ──
@pytest.mark.parametrize("json_no_texto", [JSON_MCP, JSON_DIRETO])
def test_json_de_tool_no_texto_da_celia_nao_executa_e_nao_e_falado(json_no_texto):
    jev = FakeJev()
    celia = FakeCelia(["Beleza, já fiz! ", json_no_texto[:30], json_no_texto[30:]])
    ex = FakeExecutor()
    out = _run(jev, celia, ex)
    assert ex.executed == [("discord_postar_mensagem", "OpenAI")]   # só o do Jev
    fala = _fala(out)
    assert "mcp_server" not in fala and '"name"' not in fala and "gerar_imagem" not in fala
    assert fala.startswith("Beleza, já fiz!")          # a parte falável da Célia continua saindo


def test_json_de_tool_no_texto_da_celia_em_um_unico_pedaco_tambem_nao_executa():
    jev, ex = FakeJev(), FakeExecutor()
    _run(jev, FakeCelia([f"Claro! {JSON_MCP}"]), ex)
    assert [n for n, _ in ex.executed] == ["discord_postar_mensagem"]
    assert len(ex.executed) == 1


# ── Jev sem ferramenta, e o JSON do próprio Jev em texto (modelo sem tool call nativo) ──
class FakeJevTexto:
    """Jev em modo texto: devolve JSON de tool call como texto (não nativo)."""

    tool_name = "discord_status_bot"

    def __init__(self, texto):
        self.texto, self.calls = texto, 0

    def chat_completion(self, _messages, system, tools=None):
        self.calls += 1
        texto = self.texto

        async def stream():
            yield texto
        return stream()


def test_jev_diz_sem_ferramenta_e_a_celia_responde_sem_executar_nada():
    ex = FakeExecutor()
    out = _run(FakeJevTexto("__NO_TOOL__"), FakeCelia([f"Hmm, deixa eu pensar. {JSON_MCP}"]), ex)
    assert ex.executed == []
    assert "mcp_server" not in _fala(out) and _fala(out).startswith("Hmm, deixa eu pensar.")


def test_json_de_tool_escrito_pelo_proprio_jev_ainda_executa():
    """O bloqueio vale só para a fala da Célia: o Jev continua podendo acionar ferramenta."""
    ex = FakeExecutor()
    jev = FakeJevTexto('{"mcp_server": "discord-actions", "tool": "discord_status_bot", "arguments": "{}"}')
    _run(jev, FakeCelia(["O bot está ligado."]), ex)
    # executa uma única vez (as repetições são barradas pelo guarda de duplicata do laço)
    assert ex.executed == [("discord_status_bot", "Prompt")]
