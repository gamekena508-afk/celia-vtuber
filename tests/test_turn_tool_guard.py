"""Ferramenta por fragmento ambíguo (tentativa de gravação de 2026-10-21): o guard só restringe, nunca concede."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from celia.core import turn_tool_guard as g  # noqa: E402
from celia.core.ingress_security import TrustedContext  # noqa: E402
from celia.mcpp.tool_executor import ToolExecutor  # noqa: E402


@pytest.fixture(autouse=True)
def _sem_turno():
    g.clear_turn()
    yield
    g.clear_turn()


def _turno(texto, **kw):
    return g.begin_turn(texto, utterance_id=kw.pop("utterance_id", "u1"), **kw)


# --- os três disparos REAIS da tentativa ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("ferramenta,args,fala,motivo", [
    ("minecraft_parar", {}, "Zera Minecraft.", "not_an_explicit_stop"),
    ("minecraft_objetivo", {"objetivo": "fazer uma série de Minecraft"}, "Eu vou fazer uma série. Calma, calma. Tô perdido.", "not_addressed_to_her"),
    ("search", {"query": "tio situação tio notícia", "max_results": "5"}, "Tá calada, Célia, o que que tá acontecendo com o tio?",
     "query_not_from_what_he_said"),
])
def test_the_three_real_tool_firings_are_blocked(ferramenta, args, fala, motivo):
    _turno(fala)
    assert g.check(ferramenta, args) == motivo


# --- o que TEM que passar --------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("fala", ["Célia, para tudo agora", "Fica parada.", "Para.", "Pare", "Célia, para", "Para agora"])
def test_explicit_stop_commands_pass_immediately(fala):
    _turno(fala)
    assert g.check("minecraft_parar", {}) is None


@pytest.mark.parametrize("ferramenta,args,fala", [
    ("minecraft_acao", {"tipo": "mine"}, "Célia, pega madeira pra nossa casa"),
    ("minecraft_acao", {"tipo": "mine"}, "Pega madeira pra nossa casa"),
    ("search", {"query": "receita de bolo"}, "Célia, pesquisa receita de bolo"),
    ("search", {"query": "receita de bolo simples"}, "Célia, pesquisa receita de bolo"),
])
def test_clear_orders_still_run(ferramenta, args, fala):
    _turno(fala)
    assert g.check(ferramenta, args) is None


# --- fragmento --------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("fala,fragmento", [("Célia, vai pra...", False), ("Hum.", False), ("Ahhh.", False), ("Célia, pega madeira", True), ("pega", False)])
def test_a_fragment_never_runs_a_body_tool(fala, fragmento):
    _turno(fala, fragment=fragmento)
    assert g.check("minecraft_acao", {"tipo": "mine"}) == "fragment_not_an_order"


def test_other_side_effect_tools_do_not_run_on_a_fragment_but_read_only_tools_are_untouched():
    _turno("Tá se fazendo a meu...")
    assert g.check("criar_enquete", {}) == "fragment_not_an_order"
    assert g.check("get_current_time", {}) is None, "ferramenta de leitura não é o alvo do guard"


def test_guard_is_inactive_without_turn_info():
    assert g.check("minecraft_parar", {}) is None and g.check("search", {"query": "x y z"}) is None


# --- uma ordem não executa duas vezes ---------------------------------------------------------------------------------------------------------------
def test_the_same_order_in_the_same_utterance_runs_once_but_a_new_utterance_may_repeat_it():
    _turno("Célia, pula", utterance_id="u7")
    assert g.check("minecraft_acao", {"tipo": "jump", "duration_ms": 300}) is None
    assert g.check("minecraft_acao", {"tipo": "jump", "duration_ms": 300}) == "duplicate_order_same_utterance"
    _turno("Célia, pula", utterance_id="u8")          # fala nova: pode repetir
    assert g.check("minecraft_acao", {"tipo": "jump", "duration_ms": 300}) is None


# --- integração no ToolExecutor: o guard nega DEPOIS da autorização real e nunca concede ---------------------------------------------------------------
class _Tool:
    def __init__(self, nome):
        from celia.mcpp.types import FormattedTool
        self.tool = FormattedTool({}, "test-server")
        self.nome = nome

    def get_tool(self, nome):
        return self.tool if nome == self.nome else None


class _Client:
    def __init__(self):
        self.calls = []

    async def call_tool(self, **kwargs):
        self.calls.append(kwargs)
        return {"metadata": {}, "content_items": [{"type": "text", "text": "ok"}]}


def _dono():
    return TrustedContext(trusted_source="local_owner", trusted_user_id="criador", trusted_session_id="s", request_id="r", authenticated=True,
                          authorization_scope=("owner.full", "minecraft.write"), policy_mode="observe")


async def _rodar(executor, chamada):
    return [u async for u in executor.execute_tools([chamada], "Prompt", trusted_context=_dono())]


@pytest.mark.asyncio
async def test_executor_denies_an_authorized_stop_that_came_from_a_fragment_and_runs_a_real_one():
    cliente = _Client()
    ex = ToolExecutor(cliente, _Tool("minecraft_parar"))
    _turno("Zera Minecraft.")
    negado = await _rodar(ex, {"id": "a", "name": "minecraft_parar", "input": {}})
    assert any(u.get("status") == "denied" for u in negado) and cliente.calls == []

    _turno("Fica parada.", utterance_id="u2")
    await _rodar(ex, {"id": "b", "name": "minecraft_parar", "input": {}})
    assert len(cliente.calls) == 1, "a parada urgente tem que passar"


@pytest.mark.asyncio
async def test_guard_never_grants_what_authorization_denies():
    cliente = _Client()
    ex = ToolExecutor(cliente, _Tool("execute_command"))
    _turno("Célia, roda um comando aí")
    resposta = await _rodar(ex, {"id": "c", "name": "execute_command", "input": {"command": "echo x"}})
    assert any(u.get("status") == "denied" for u in resposta) and cliente.calls == []


# --- o agente liga o guard só em turno de voz e limpa nos demais ---------------------------------------------------------------------------------
def test_agent_arms_the_guard_for_voice_turns_and_clears_it_for_typed_turns(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from celia.agent.agents.basic_memory_agent import BasicMemoryAgent
    from celia.conversations.conversation_utils import create_batch_input

    visto = []
    monkeypatch.setattr(g, "begin_turn", lambda texto, **kw: visto.append(("voz", texto, kw)))
    monkeypatch.setattr(g, "clear_turn", lambda: visto.append(("limpo",)))

    class LLM:
        def chat_completion(self, messages, system, tools=None):
            async def s():
                yield {"type": "text_delta", "text": "[neutral] ok"}
            return s()

    agent = BasicMemoryAgent(llm=LLM(), live2d_model=None, system="P", use_mcpp=False, memory_book_enabled=False, human_name="Criador")
    agent._read_game_mood = lambda *_a, **_k: None

    async def rodar(texto, meta):
        return [x async for x in agent.chat(create_batch_input(texto, images=None, from_name="Criador", metadata=meta))]
    emo = "--- ESTADO EMOCIONAL ATUAL (Célia) ---\nEmocao: NEUTRAL\nIntensidade: 2/3\nTAMANHO: 15-60 palavras\n- Tag preferida nesta resposta: [neutral]\n"
    asyncio.run(rodar("Zera Minecraft.", {"origin": "owner", "emotion_prompt": emo, "utterance_id": "u9",
                                         "asr_refinement": {"acoustic": {"turn_gate": "released_after_continuation_window"}}}))
    asyncio.run(rodar("Oi", {"origin": "owner", "emotion_prompt": emo}))
    assert visto[0][0] == "voz" and visto[0][2]["fragment"] is True and visto[0][2]["utterance_id"] == "u9"
    assert ("limpo",) in visto
