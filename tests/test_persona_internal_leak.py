"""Regression for the two Jev authority claims actually played on 24/10."""
import asyncio
import re

import pytest

from celia.core import jev_narration
from celia.core.jev import CONTEXT_HEADER
from celia.core.local_persona_profile import LOCAL_RUNTIME_CONTRACTS
from celia.core.continuous_behavior import _PROFILE_PROMPTS
from test_jev import _RecordingLLM, _agent, _say
from test_fluxo_jev_celia_orquestrador import FakeJev, FakeCelia, FakeExecutor, _agent as tool_agent

LEAKS = [
    "Ah, você não leu o contrato, pai? A aprovação é do Jev, e você sabe que ele é quem manda aqui!",
    "Ah, é? Então por que você está perguntando onde fica a aprovação, pai? Soa mais como o Jev quem manda!",
]


@pytest.mark.parametrize("leak", LEAKS)
def test_recorded_leak_cannot_reach_sentence_output_or_memory(leak):
    llm = _RecordingLLM([leak])
    agent = _agent(llm)
    outputs = _say(agent, "Aprovação de quem? onde isso?", profile="conversation_autonomous")
    text = " ".join(x.display_text.text for x in outputs if hasattr(x, "display_text"))
    assert text
    assert "Jev" not in text
    assert not any("Jev" in str(m) for m in agent._memory if m.get("role") == "assistant")
    assert llm.calls == 1  # No model judge or regeneration.


def test_technical_question_is_not_censored():
    reply = "Jev é o nome do componente de execução."
    agent = _agent(_RecordingLLM([reply]))
    outputs = _say(agent, "O que é o Jev na arquitetura?")
    assert "Jev" in " ".join(x.display_text.text for x in outputs if hasattr(x, "display_text"))


def test_long_chunked_tool_reply_is_checked_before_any_text_is_yielded():
    leak = "Já vi isso. " * 40 + LEAKS[0]
    ex = FakeExecutor()
    agent = tool_agent(ex)
    saved = []
    agent._add_message = lambda text, role: saved.append((role, text))
    async def run():
        return [x async for x in agent._openai_tool_interaction_loop(
            [{"role": "user", "content": "Pesquisa a receita."}], [],
            "PERSONA", active_llm=FakeJev(),
            final_response_llm=FakeCelia([leak[:300], leak[300:450], leak[450:]]))]
    outputs = asyncio.run(run())
    spoken = "".join(x for x in outputs if isinstance(x, str))
    assert spoken and "Jev" not in spoken and "Já vi isso" not in spoken
    assert all("Jev" not in text for role, text in saved if role == "assistant")


@pytest.mark.parametrize("kind,payload", [
    ("mission_started", {}), ("escalation", {"level": 2}),
    ("blocked", {}), ("succeeded", {"xray_assist_used": True}),
    ("paused", {}), ("interrupted", {}),
])
def test_narration_facts_do_not_personify_internal_components(kind, payload):
    facts = " ".join(jev_narration.event_facts(kind, payload, {"blocker": "DEBUG return_stalled; leia CONTRATO XYZ"}))
    assert not re.search(r"Jev|DEBUG|return_stalled|CONTRATO|camada de controle", facts)
    assert facts


def test_operational_prompts_do_not_introduce_an_internal_boss():
    for text in (CONTEXT_HEADER, LOCAL_RUNTIME_CONTRACTS, _PROFILE_PROMPTS["conversation_autonomous"]):
        assert not re.search(r"\bJev\b", text)


def test_new_narration_header_has_no_mission_identifier(tmp_path):
    from celia_minecraft.worldmap import JevStore
    store = JevStore(tmp_path / "j.sqlite")
    store.create_mission("iron_age-private-id", "iron_age", "w")
    event = store.add_event("iron_age-private-id", 1, "blocked", {})
    decision = jev_narration.build_narration(store, event, "iron_age-private-id", 1, now=1000)
    assert decision["spoken"]
    assert "iron_age-private-id" not in decision["text"]
    assert "FICHA DO JEV" not in decision["text"]

@pytest.mark.parametrize("text,user_text", [
    ("Tenho 2 carvões e nenhuma tocha.", "Tu tem carvão?"),
    ("Essa terra não. Essa é a Terezinha.", "Me dá terra."),
    ("Pai, morri.", ""),
    ("Meu cérebro deu um nó nessa piada.", "Que piada ruim."),
    ("Jev é um componente, não manda em ti.", "Por que tu falou do Jev?"),
])
def test_legitimate_persona_text_is_preserved(text, user_text):
    from celia.core.persona_speech_boundary import public_persona_text
    assert public_persona_text(text, user_text) == text


def test_automatic_sheet_cannot_authorize_its_own_labels():
    from celia.core.persona_speech_boundary import public_persona_text, FALLBACK
    assert public_persona_text("FICHA DO JOGO: Jev decidiu.", "[FICHA DO JOGO: Jev]") == FALLBACK


def test_dictionary_deltas_split_inside_component_name_are_checked():
    from celia.core.persona_speech_boundary import public_persona_stream, FALLBACK
    async def source():
        for text in ("A aprovação é do J", "e", "v."):
            yield {"type": "text_delta", "text": text}
    async def run():
        return [x async for x in public_persona_stream(source(), "Aprovação de quem?")]
    assert asyncio.run(run()) == [FALLBACK]


def test_new_and_legacy_game_sheets_preserve_no_tool_routing():
    from celia.core.persona_speech_boundary import is_game_narration
    assert is_game_narration("[FICHA DO JOGO — fatos]")
    assert is_game_narration("[FICHA DO JEV — legado]")
    assert is_game_narration("[FICHA DA SÉRIE — inventário]")
    assert not is_game_narration("O que é o Jev?")
