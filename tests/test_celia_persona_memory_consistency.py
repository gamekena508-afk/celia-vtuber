"""Regressoes deterministicas do contrato de prompt e lembranca de video."""
from pathlib import Path

from celia.core import memory_claim_guard
from celia.core import personality_turn


ROOT = Path(__file__).resolve().parents[1]
AUSENTE = "LEMBRANÇA AUSENTE NOS VÍDEOS: não há registro do fato pedido."


def test_video_nao_confirmado_nunca_e_afirmado_no_chat_normal():
    request = "Lembra daquele vulcão na Lua que a gente filmou?"
    answer = "Sim! Eu lembro quando a gente filmou o vulcão na Lua."
    corrected = memory_claim_guard.guard_unverified_video_memory(request, answer, AUSENTE)
    assert corrected != answer
    assert "Não encontrei esse episódio" in corrected
    assert "nunca aconteceu" not in corrected.lower()


def test_recordacao_legitima_ou_outra_fonte_nao_e_apagada():
    question = "Lembra daquele vídeo que a gente gravou no Natal?"
    answer = "Sim, lembro da nossa gravação de Natal!"
    assert memory_claim_guard.guard_unverified_video_memory(question, answer, "") == answer
    assert memory_claim_guard.guard_unverified_video_memory(
        question, answer, AUSENTE, independent_evidence=True
    ) == answer
    assert memory_claim_guard.guard_unverified_video_memory(
        "Lembra daquele dia?", answer, AUSENTE
    ) == answer


def test_admissao_de_incerteza_e_preservada():
    question = "Lembra daquele vídeo do vulcão?"
    answer = "Não lembro desse vídeo, Criador. Foi outra gravação?"
    assert memory_claim_guard.guard_unverified_video_memory(question, answer, AUSENTE) == answer


def test_prompt_atual_nao_forca_rebeldia_ou_memoria():
    yaml = (ROOT / "conf.yaml").read_text(encoding="utf-8")
    persona = yaml.split("  persona_prompt: |-\n", 1)[1].split("\n  continuous_behavior:", 1)[0]
    assert "filha rebelde" not in persona
    assert "ironia primeiro" not in persona
    assert "mandam no tom" not in persona
    assert "cite o título e minuto" not in persona
    assert "PESSOA CONHECIDA" in persona
    assert "brinque de igual para igual" in persona


def test_rari_tom_prioriza_ajuda_e_nao_rebaixa(tmp_path):
    import json
    people = tmp_path / "profiles.json"
    people.write_text(json.dumps({"profiles": [
        {"discord_user_id": "rari_id", "name": "Amiga"}
    ]}), encoding="utf-8")
    joke = personality_turn.turn_hint(
        "Eu sou a rainha do Discord!", author_id="rari_id", profiles_path=people
    )
    assert "de igual para igual" in joke
    assert "nunca diminua" in joke
    assert "poderes reais" in joke
    serious = personality_turn.turn_hint(
        "Célia, tô triste, preciso de ajuda com meu bot",
        author_id="rari_id", profiles_path=people
    )
    assert "conteúdo concreto" in serious
    assert "TOM COM A AMIGA" in serious


def test_gemma_usa_a_persona_ativa_sem_duplicar_ou_reordenar():
    from celia.core import local_persona_profile as local

    local._original_persona.cache_clear()
    active = local._original_persona()
    assert active and "ironia primeiro" not in active
    system = "[CONDUTA CONVERSACIONAL]\n[PERSONA]\n" + active + "\n[FOCO DA CONVERSA]\nDados"
    rendered = local.local_persona_system(system)
    assert rendered.startswith("[CONDUTA CONVERSACIONAL]")
    assert rendered.count(active) == 1
    assert local.LOCAL_RUNTIME_CONTRACTS in rendered
    assert "CÉLIA — CONTRATO ATIVO DE CONVERSA REAL" not in rendered


def test_guard_bloqueia_falsa_filmagem_antes_da_fala_final(monkeypatch):
    from test_jev import _RecordingLLM, _agent, _say
    from celia.core import video_memory, social_score
    from celia.core.episode_context import episode_context

    # Nenhum acesso/escrita no corpus ou no score persistente da Célia.
    monkeypatch.setattr(video_memory, "recall_block", lambda *a, **kw: AUSENTE)
    monkeypatch.setattr(social_score, "observe_turn", lambda *a, **kw: None)
    monkeypatch.setattr(episode_context, "agendar", lambda *a, **kw: None)
    llm = _RecordingLLM(["Sim, lembro quando a gente filmou o vulcão na Lua!"])
    agent = _agent(llm)
    spoken = " ".join(
        x.display_text.text for x in _say(
            agent, "Lembra daquele vídeo que a gente filmou na Lua?"
        ) if hasattr(x, "display_text")
    )
    assert "Não encontrei esse episódio" in spoken
    assert "lembro quando a gente filmou" not in spoken
