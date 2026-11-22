from celia.agent.agents.basic_memory_agent import BasicMemoryAgent


def make_agent():
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
    agent._conversation_scene = "playing"
    agent._blocked_topics = []
    return agent


def test_outro_switches_game_context_to_narrative_then_none_for_unrelated_topic():
    agent = make_agent()

    level, prompt = agent._update_focus_state("Chegamos ao final do vídeo.")
    assert level == "narrative"
    assert agent._conversation_scene == "ending"
    assert "ENCERRANDO O VÍDEO" in prompt

    level, _ = agent._update_focus_state(
        "O que nós aprendemos sobre inteligência artificial hoje?"
    )
    assert level == "none"


def test_tactical_question_gets_tactical_context():
    agent = make_agent()
    level, _ = agent._update_focus_state("Por que você mexeu essa torre?")
    assert level == "tactical"


def test_forget_topic_blocks_semantic_return_to_how_she_won():
    agent = make_agent()
    level, prompt = agent._update_focus_state("Esquece como tu ganhou.")
    assert level in {"none", "scene"}
    assert agent._blocked_topics
    assert agent._blocked_topics[-1]["signature"] == "game_win_explanation"
    assert "Tópicos que o criador mandou abandonar" in prompt

    reason = agent._focus_violation_reason(
        "Agora vamos fechar o vídeo.",
        "Eu ainda não entendi como consegui virar o jogo.",
        [],
    )
    assert reason == "blocked_topic_returned"


def test_explicit_question_reopens_a_previously_blocked_topic():
    agent = make_agent()
    agent._update_focus_state("Esquece como ganhou.")
    assert agent._blocked_topics

    agent._update_focus_state("Tá, mas como você ganhou?")
    assert not any(
        item["signature"] == "game_win_explanation"
        for item in agent._blocked_topics
    )


def test_semantic_loop_detects_paraphrase_not_only_exact_copy():
    agent = make_agent()
    previous = [
        {
            "role": "assistant",
            "content": "Eu ainda não entendi como consegui virar essa partida no final.",
        }
    ]
    reason = agent._focus_violation_reason(
        "E o que você aprendeu hoje?",
        "Continuo sem entender direito como consegui virar essa partida no final.",
        previous,
    )
    assert reason == "semantic_loop"


def test_user_requested_clarification_may_repeat_information():
    agent = make_agent()
    previous = [
        {
            "role": "assistant",
            "content": "O motor escolhe a jogada e a Célia comenta depois.",
        }
    ]
    reason = agent._focus_violation_reason(
        "Explica de novo, não entendi.",
        "O motor escolhe a jogada e a Célia comenta depois.",
        previous,
    )
    assert reason == ""


def test_cta_focus_does_not_pull_game_back_in():
    agent = make_agent()
    agent._update_focus_state("Chegamos ao final do vídeo.")
    level, prompt = agent._update_focus_state("Manda o pessoal se inscrever.")
    assert level == "narrative"
    assert "CTA" in prompt
