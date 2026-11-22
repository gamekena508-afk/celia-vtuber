"""Régua do Jev (2026-10-06): intérprete de contexto, ficha curta, Juiz fora.

Cada teste cobre um item do pedido do Pai. Os testes de agente usam um LLM
falso que só grava o que recebeu; nenhum modelo real é chamado.
"""
from __future__ import annotations

import asyncio
import re
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from celia.core import jev as jev_core
from celia.core.jev import FICHA_LIMIT, ITEM_LIMIT, Jev, interpret

BLENDER = (
    "Muito engraçado. Célia, por que tu tá com essa cara triste, minha filha? "
    "Tô fazendo teu modelo aqui."
)
T0 = 10_000.0


def _blender_session() -> Jev:
    state = Jev()
    for ago, text in (
        (900, "Foi demais, Célia pato."),
        (260, "Sim, assumir blender e exportar Célia web VR sem tocar na Célia VR3."),
        (200, "VRM é o formato certo. É o padrão aberto de Avatar 3D para Vtuber, com os ossos."),
        (77, "Inscreve no canal, gente. Agora vai rolar um timelapse da montagem do modelo da Célia, do começo ao fim."),
    ):
        state.observe_user(text, speaker="Criador", owner=True, now=T0 - ago)
    state.observe_celia(
        "[sad] Ah, parece que você está animado com o timelapse, mas eu não estou me sentindo muito bem agora.",
        now=T0 - 70,
    )
    state.observe_user(BLENDER, speaker="Criador", owner=True, now=T0)
    return state


def _section(ficha, name: str) -> list[str]:
    for key, items in ficha.sections.items():
        if key.startswith(name):
            return items
    return []


@pytest.fixture(autouse=True)
def _clean_global_buffers():
    from celia.core.vision_cache import vision_cache

    jev_core.tool_facts.clear()
    vision_cache.clear()
    yield
    jev_core.tool_facts.clear()
    vision_cache.clear()


# --- critério de sucesso -------------------------------------------------------

def test_blender_case_is_understood_without_writing_the_reply():
    ficha = _blender_session().build(
        BLENDER, speaker="Criador", owner=True, profile="roteiro",
        emotion={"emotion": "CONFUSED"}, now=T0,
    )
    text = ficha.text
    assert ficha.reading.intent == "provocacao"
    assert ficha.reading.direct
    assert "construção do modelo/avatar 3D da Célia" in text
    assert "Criador disse que está fazendo o modelo da Célia." in _section(ficha, "FATOS")
    inferences = " ".join(_section(ficha, "INFERÊNCIAS"))
    assert "provavelmente está fazendo o modelo da Célia" in inferences
    assert "brincadeira pedindo reação" in inferences
    assert jev_core.EXPECTED_BEHAVIOR["reagir_provocacao"] in text
    assert "não precisa se fazer de perdida nem devolver a pergunta" in text
    assert "falando direto com a Célia" in text


# --- 1, 2: presente vence passado ---------------------------------------------

def test_current_context_beats_old_history():
    state = Jev()
    state.observe_user("Bora jogar xadrez, quero ver essa partida de damas", speaker="Criador", owner=True, now=T0 - 14 * 60)
    state.observe_user("Abri o Blender pra mexer no VRM", speaker="Criador", owner=True, now=T0 - 90)
    state.observe_user("Tô ajustando os ossos do modelo", speaker="Criador", owner=True, now=T0 - 30)
    state.observe_user(BLENDER, speaker="Criador", owner=True, now=T0)
    ficha = state.build(BLENDER, speaker="Criador", owner=True, now=T0)
    assert "avatar 3D" in ficha.text
    assert "xadrez" not in ficha.text and "damas" not in ficha.text


def test_past_is_never_presented_as_present():
    state = Jev()
    state.observe_user("Ontem eu terminei o rig do modelo", speaker="Criador", owner=True, now=T0 - 5 * 60)
    state.observe_user(BLENDER, speaker="Criador", owner=True, now=T0)
    ficha = state.build(BLENDER, speaker="Criador", owner=True, now=T0)
    past = _section(ficha, "CONTEXTO RELEVANTE")
    assert past and "há 5 min" in past[0]
    assert "PASSADO: há 5 min" in ficha.text
    assert not any("rig" in item for item in _section(ficha, "EVENTOS RECENTES"))


# --- 3, 4: fato, inferência e incerteza ----------------------------------------

def test_fact_never_becomes_inference_and_inference_never_becomes_fact():
    ficha = _blender_session().build(BLENDER, speaker="Criador", owner=True, now=T0)
    facts = _section(ficha, "FATOS")
    inferences = _section(ficha, "INFERÊNCIAS")
    assert facts and inferences
    assert not any("provavelmente" in item for item in facts)
    assert all(
        "provavelmente" in item or "comentando" in item for item in inferences
    )
    assert "(prováveis, não confirmadas)" in ficha.text


def test_uncertainty_stays_marked():
    ficha = _blender_session().build(
        BLENDER, speaker="Criador", owner=True, asr_uncertain=True, now=T0,
    )
    uncertain = " ".join(_section(ficha, "INCERTEZAS"))
    assert "não sabemos exatamente o que aparece" in uncertain.lower()
    assert "transcrição" in uncertain
    assert "INCERTO você não sabe e não deve inventar" in jev_core.CONTEXT_HEADER


def test_repetition_is_a_fact_not_a_guess():
    first = Jev().build("Bora, Célia.", speaker="Criador", owner=True, repetition=(1, False), now=T0)
    assert "primeira vez que ele diz isso" in first.text
    again = Jev().build("Bora, Célia.", speaker="Criador", owner=True, repetition=(3, True), now=T0)
    assert "apareceu 3 vezes" in again.text and "primeira vez" not in again.text


def test_celia_quote_has_no_live2d_tag():
    state = Jev()
    state.observe_celia("[sad] Tô me sentindo esquisita hoje.", now=T0 - 20)
    state.observe_user("E aí?", speaker="Criador", owner=True, now=T0)
    ficha = state.build("E aí?", speaker="Criador", owner=True, now=T0)
    assert "«Tô me sentindo esquisita hoje.»" in ficha.text
    assert "[sad]" not in ficha.text


def test_unstarted_script_beat_is_not_shown():
    idle = Jev().build("Bora.", speaker="Criador", owner=True, profile="gravacao", script={"current": "not_started"}, now=T0)
    assert "not_started" not in idle.text and "beat do roteiro" not in idle.text
    live = Jev().build("Bora.", speaker="Criador", owner=True, profile="gravacao", script={"current": "Beat 2: mostrar o rig"}, now=T0)
    assert "Objetivo atual (beat do roteiro): Beat 2: mostrar o rig." in live.text


# --- 5: visão com idade ---------------------------------------------------------

def test_vision_stays_available_with_age_and_goes_stale():
    note = SimpleNamespace(app="Blender", summary="viewport com o modelo da Célia em pose T")
    fresh = Jev().build("Célia, tá vendo o modelo?", speaker="Criador", owner=True, vision=(note, 12_000), now=T0)
    assert "Análise da tela há 12s (Blender)" in fresh.text
    stale = Jev().build("Célia, tá vendo o modelo?", speaker="Criador", owner=True, vision=(note, 300_000), now=T0)
    assert "DESATUALIZADA" in stale.text and "há 5 min" in stale.text
    assert "pose T" not in stale.text


def test_vision_tool_prose_never_refreshes_frame_cache():
    from celia.core.vision_cache import vision_cache

    original = vision_cache.put("screen", "Current frame", analyzed_at=time.monotonic() - 20)
    jev_core.observe_tool_result("vision_describe_visual", "Old Minecraft response")
    note, age_ms = vision_cache.get()
    assert note is original and age_ms >= 20_000
    assert jev_core.tool_facts.recent()[-1].summary == "Old Minecraft response"
    ficha = Jev().build("E agora?", speaker="Criador", owner=True,
                        vision=(note, age_ms), tools=jev_core.tool_facts.recent())
    assert "Old Minecraft response" not in ficha.text


# --- 6, 7: limite duro e itens inteiros ----------------------------------------

def _stuffed() -> tuple[Jev, list]:
    state = Jev()
    for i in range(40):
        state.observe_user(
            f"Falando do modelo {i}: ajustei os ossos, a textura e o blendshape do rosto número {i}",
            speaker="Criador", owner=True, now=T0 - 600 + i * 10,
        )
        state.observe_celia(f"[happy] Comentário {i} sobre o modelo e a textura", now=T0 - 595 + i * 10)
    for i in range(6):
        jev_core.tool_facts.observe(f"ferramenta_{i}", "resultado muito longo " * 40, now=T0 - 5)
    return state, jev_core.tool_facts.recent(now=T0)


def test_ficha_never_exceeds_limit_and_drops_whole_items():
    state, tools = _stuffed()
    ficha = state.build(
        BLENDER, speaker="Criador", owner=True, profile="roteiro",
        script={"current": "Beat 3: mostrar o rig"}, emotion={"emotion": "HAPPY"},
        asr_uncertain=True, tools=tools, now=T0,
    )
    assert len(ficha.text) <= FICHA_LIMIT
    assert ficha.dropped > 0
    rendered_items = {item for items in ficha.sections.values() for item in items}
    for line in ficha.text.splitlines():
        if line.startswith("- "):
            body = re.sub(r"^- (PASSADO|INCERTO): ", "- ", line)[2:]
            assert body in rendered_items, line
            assert len(body) <= ITEM_LIMIT
    assert "O USUÁRIO DISSE AGORA" in ficha.text
    assert "RESPOSTA ESPERADA" in ficha.text


def test_long_items_are_never_cut_mid_sentence():
    jev_core.tool_facts.observe("pesquisa_web", "palavra " * 200, now=T0 - 3)
    ficha = Jev().build("Achou?", speaker="Criador", owner=True, tools=jev_core.tool_facts.recent(now=T0), now=T0)
    tool_lines = [line for line in ficha.text.splitlines() if "pesquisa_web" in line]
    assert tool_lines and tool_lines[0].endswith("[resumo]")
    assert all(len(line) <= ITEM_LIMIT + 2 for line in tool_lines)


def test_jev_is_fast():
    state, tools = _stuffed()
    started = time.perf_counter()
    for _ in range(20):
        state.build(BLENDER, speaker="Criador", owner=True, tools=tools, now=T0)
    assert (time.perf_counter() - started) / 20 < 0.02


# --- 10, 11, 12: emoção é dado; Jev não escreve fala ----------------------------

EMOTION_PROMPT = """--- ESTADO EMOCIONAL ATUAL (Célia) ---
Emocao: CONFUSED
Intensidade: 2/3
Estilo de resposta: opinativa, direta, tentando conectar as ideias sem ficar interrogando
TAMANHO: 20-70 palavras
Inicios possiveis: Olha, Mano, Cara, Entendi
- Chame o usuario de Criador
- Tags permitidas: [neutral], [happy], [confused]
- Tag preferida nesta resposta: [confused]
--- FIM DO ESTADO EMOCIONAL ---"""


def test_emotion_block_becomes_data_not_an_order():
    data = jev_core.parse_emotion_block(EMOTION_PROMPT + "\n\nInput: oi")
    assert data == {
        "emotion": "CONFUSED",
        "intensity": "2/3",
        "allowed_tags": "[neutral], [happy], [confused]",
        "size": "20-70 palavras",
    }
    form = jev_core.render_reply_form(data)
    assert "preferida" not in form and "Inicios" not in form and "Olha" not in form
    assert jev_core.strip_emotion_block(EMOTION_PROMPT + "\n\nInput: oi Célia") == "oi Célia"


def test_jev_never_writes_the_final_reply():
    for text in (BLENDER, "Célia?", "Abre o YouTube", "Qual a capital da Austrália?"):
        ficha = Jev().build(text, speaker="Criador", owner=True, now=T0)
        expected = _section(ficha, "RESPOSTA ESPERADA")
        assert expected and expected[0] in jev_core.EXPECTED_BEHAVIOR.values()
        assert not re.search(r"^\s*(Célia|\[\w+\])\s*:", ficha.text, flags=re.MULTILINE)
    for gloss in jev_core.EXPECTED_BEHAVIOR.values():
        assert "«" not in gloss and '"' not in gloss


def test_intents_of_the_recorded_failures():
    assert interpret("Célia?").intent == "chamado"
    assert interpret("Célia, tá me ouvindo? Fala comigo.").intent == "chamado"
    assert interpret("Opa Célia, e aí tá tudo bem?").intent == "cumprimento"
    assert interpret("Caraca, Célia tá fraca, hein?").expected == "reagir_provocacao"
    assert interpret("Inscreve no canal, gente.").intent == "audiencia"
    assert interpret("Não, Célia, eu quis dizer o modelo 3D").intent == "correcao"
    assert interpret("Abre o YouTube pra mim, Célia").tool_hint == "ferramenta:youtube"
    assert interpret(BLENDER).tool_hint == "conversa"


# --- agente ponta a ponta --------------------------------------------------------

class _RecordingLLM:
    def __init__(self, candidates):
        self.candidates = list(candidates)
        self.calls = 0
        self.systems: list[str] = []
        self.messages: list[list] = []

    def chat_completion(self, messages, system, tools=None):
        del tools
        self.systems.append(system)
        self.messages.append(messages)
        text = self.candidates[min(self.calls, len(self.candidates) - 1)]
        self.calls += 1

        async def stream():
            yield {"type": "text_delta", "text": text}

        return stream()


PERSONA = "PERSONA-COMPLETA-DA-CELIA " + ("Ácida, direta, filha do criador. " * 40)


def _agent(llm, **kwargs):
    from celia.agent.agents.basic_memory_agent import BasicMemoryAgent

    agent = BasicMemoryAgent(
        llm=llm, live2d_model=None, system=PERSONA, use_mcpp=False,
        memory_book_enabled=False, **kwargs,
    )
    agent._read_game_mood = lambda *_a, **_k: None
    return agent


def _say(agent, text, *, profile="chat", metadata=None):
    from celia.conversations.conversation_utils import create_batch_input
    from celia.core.continuous_behavior import continuous_behavior

    meta = {"origin": "owner", "emotion_prompt": EMOTION_PROMPT}
    meta.update(metadata or {})
    old = continuous_behavior._profile_manager._profile
    continuous_behavior._profile_manager._profile = profile

    async def collect():
        return [item async for item in agent.chat(create_batch_input(text, images=None, from_name="Criador", metadata=meta))]

    try:
        return asyncio.run(collect())
    finally:
        continuous_behavior._profile_manager._profile = old


def _spoken(output) -> str:
    parts = []
    for item in output:
        display = getattr(item, "display_text", None)
        parts.append(getattr(display, "text", None) or str(item))
    return " ".join(parts)


def test_raw_history_does_not_return_and_persona_is_intact():
    llm = _RecordingLLM(["[happy] Tá bom."] * 40 + ["[teasing] Triste nada, é teu rig torto."])
    agent = _agent(llm)
    for i in range(12):
        _say(agent, f"frase antiga número {i} sobre xadrez")
    _say(agent, BLENDER, profile="roteiro")
    system, messages = llm.systems[-1], llm.messages[-1]
    assert len(messages) <= jev_core.RAW_HISTORY_MESSAGES + 1
    flat = " ".join(str(m.get("content")) for m in messages)
    assert "número 0 " not in flat and "número 5 " not in flat
    assert "número 0 " not in system
    assert PERSONA.strip() in system
    assert "REGRA PRINCIPAL: MÁXIMO 2-3 frases CURTAS" not in system
    assert "=== CONTEXTO DO SISTEMA ===" in system
    assert "[LIVE OPERATING CONTEXT" not in system
    assert "[SITUATION INTERPRETATION" not in system
    last = str(messages[-1]["content"])
    assert jev_core.USER_HEADER in last and BLENDER in last


def test_conversation_prompt_keeps_the_old_diet_no_keyword_modules():
    llm = _RecordingLLM(["[teasing] Engraçado é teu rig."])
    agent = _agent(llm, human_name="Criador")
    _say(agent, BLENDER, profile="roteiro")
    system = llm.systems[-1]
    assert "--- CONTEXTO DO MODULO ATIVO ---" not in system
    assert "[TOOL USAGE RULES]" not in system
    assert PERSONA.strip() in system
    total = len(system) + sum(len(str(m.get("content"))) for m in llm.messages[-1])
    # persona + verdade temporal f45e (~2,7k, inteira) + ficha (~1,6k com
    # cabeçalhos) + conversa natural (~1k) + interlocutor/foco/forma (~0,8k).
    assert total < len(PERSONA) + 7500


def test_conversation_autonomous_prompt_fits_the_diet_budget():
    # Irmão do teste acima, mas medindo o perfil novo (conversation_autonomous),
    # que é o que a série exige. Mesmo teto: não sobe o orçamento.
    llm = _RecordingLLM(["[teasing] Engraçado é teu rig."])
    agent = _agent(llm, human_name="Criador")
    _say(agent, BLENDER, profile="conversation_autonomous")
    system = llm.systems[-1]
    assert "--- CONTEXTO DO MODULO ATIVO ---" not in system
    assert "[TOOL USAGE RULES]" not in system
    assert PERSONA.strip() in system
    total = len(system) + sum(len(str(m.get("content"))) for m in llm.messages[-1])
    # Teto PRÓPRIO do perfil da série (11/11/2026): o teto da dieta (persona+7500) não cobria este perfil; medido: antes dos blocos "MINECRAFT BASICO E
    # HUMOR" + "FORMATO DO VIDEO" (1343 caracteres) ele já dava 9330 (524 acima). Este teto trava o crescimento a partir de hoje; não o suba sem medir.
    teto_serie = len(PERSONA) + 7500 + 524 + 1343 + 150
    assert total < teto_serie, f"orçamento do perfil da série estourado: total={total} teto={teto_serie} excedente={total - teto_serie}"


def test_emotion_does_not_force_the_interpretation_in_the_prompt():
    llm = _RecordingLLM(["[neutral] Oi."])
    _say(_agent(llm), BLENDER, profile="roteiro")
    sent = llm.systems[-1] + " ".join(str(m.get("content")) for m in llm.messages[-1])
    assert "Tag preferida" not in sent
    assert "Inicios possiveis" not in sent
    assert "Emocao: CONFUSED" not in sent
    assert "dado, não ordem" in sent
    assert "Tags permitidas: [neutral], [happy], [confused]" in sent


@pytest.mark.parametrize("profile", ["roteiro", "gravacao", "cohost"])
def test_judge_never_blocks_or_regenerates(monkeypatch, profile):
    llm = _RecordingLLM(["[sad] Tu ta repetindo isso.", "SEGUNDA GERACAO NAO DEVE EXISTIR"])
    output = _say(_agent(llm), "me ajuda aqui", profile=profile, metadata={"scene_profile": "gameplay"})
    assert llm.calls == 1
    assert "repetindo" in _spoken(output)
    assert "SEGUNDA" not in _spoken(output)


def test_output_judge_is_gone():
    """O Juiz de saída foi removido (2026-10-08): não importa, não existe no agente."""
    import importlib.util

    import celia.agent.agents.basic_memory_agent as module

    assert importlib.util.find_spec("celia.core.output_judge") is None
    assert not hasattr(module, "output_judge")
    assert not hasattr(module.BasicMemoryAgent, "_schedule_judge_observation")


def test_no_three_attempts_and_focus_guard_only_observes():
    llm = _RecordingLLM(["[happy] Vai nessa, so nao reclama da partida."] * 4)
    agent = _agent(llm)
    _say(agent, "como tu ganhou a partida?", profile="roteiro")
    calls_before = llm.calls
    _say(agent, "e como tu ganhou a partida mesmo?", profile="roteiro", metadata={"scene_profile": "gameplay"})
    assert llm.calls - calls_before == 1


def test_celia_owns_her_line_and_normal_flow_works():
    llm = _RecordingLLM(["[teasing] Triste? É o teu rig que tá torto, pai."])
    output = _say(_agent(llm), BLENDER, profile="roteiro")
    assert llm.calls == 1
    assert "É o teu rig que tá torto" in _spoken(output)


def test_conversational_turn_skips_tool_route_but_never_grants_tools(monkeypatch):
    import celia.agent.agents.basic_memory_agent as module

    decisions = []

    class _Recorder:
        def emit(self, name, _component, _status, payload=None, **_kwargs):
            if name == "tool_route_decision":
                decisions.append(dict(payload or {}))

    monkeypatch.setattr(module, "observability", _Recorder())
    monkeypatch.setattr(module, "needs_tool", lambda _text: "system")
    main = _RecordingLLM(["[happy] Tô aqui."])
    _say(_agent(main), "Célia?")
    assert decisions and decisions[-1]["has_tool"] is False
    assert decisions[-1]["jev_hint"] == "conversa"
    assert main.calls == 1
    # Pedido de ação: o Jev só sinaliza; não executa nem autoriza nada.
    reading = interpret("Abre o navegador, Célia")
    assert reading.tool_hint.startswith("ferramenta:")
    assert not any(hasattr(jev_core.Jev, name) for name in ("execute", "run_tool", "authorize"))
    decisions.clear()
    _say(_agent(_RecordingLLM(["[neutral] Ok."])), "Abre o navegador, Célia")
    assert decisions and decisions[-1]["has_tool"] is True


def test_tool_observation_never_changes_the_result():
    payload = {"ok": True, "items": [1, 2]}
    assert jev_core.observe_tool_result("listar_jogos", payload) is None
    assert jev_core.tool_facts.recent()[-1].name == "listar_jogos"
    assert payload == {"ok": True, "items": [1, 2]}
