"""Series voice intents: every phrase of the plan fires; quotes, negations, mentions, echoes and wrong states never do."""
import time

import pytest

from celia.core.series_intent import (
    PROFILE_ID, SeriesIntent, TrustedOwnerControlGate, classify_confirmation, detect_series_intent,
)


def one(text):
    found = detect_series_intent(text)
    assert len(found) == 1, (text, found)
    return found[0]


def acts(text):
    return [i for i in detect_series_intent(text) if i.should_act]


# ------------------------------------------------------------------ the plan's phrases fire --------------------------------------------------
@pytest.mark.parametrize("text,intent,params", [
    ("Célia, prepara a série.", "series.prepare_intro", {}),
    ("prepara o mundo da série", "series.prepare_intro", {}),
    ("Vamos preparar a introdução", "series.prepare_intro", {}),
    ("Célia, abre o mundo da série", "world.select", {}),
    ("vamos pro mundo da série", "world.select", {}),
    ("entra no mundo Célia série um", "world.select", {"world_id": "celia-serie-s01"}),
    ("Célia, abre o mundo da série dois", "world.select", {"world_id": "celia-serie-s02"}),
    ("Célia, mostra os mundos", "world.list", {}),
    ("quais mundos nós temos?", "world.list", {}),
    ("Célia, cria um novo mundo pra nossa série", "world.create", {"series": None, "name": ""}),
    ("cria a série dois", "world.create", {"series": "s02", "name": "Célia e Criador — Série 02"}),
    ("Célia, salva esse mundo", "world.checkpoint", {"label": "voz"}),
    ("salva o progresso", "world.checkpoint", {"label": "voz"}),
    ("Célia, faz um backup desse mundo", "world.backup", {"label": "voz"}),
    # decision 3c (2026-10-20): "bora jogar" means play (progression); only "solta/libera o mundo" is a bare release
    ("Vamos começar a jogar", "series.start_gameplay", {"goal": "progression"}),
    ("Bora jogar", "series.start_gameplay", {"goal": "progression"}),
    ("agora pode começar", "series.start_gameplay", {"goal": "progression"}),
    ("Célia, libera o mundo", "series.start_gameplay", {"goal": None}),
    ("Célia, pode soltar o mundo", "series.start_gameplay", {"goal": None}),
    ("Célia, pode se mexer", "companion.release", {}),  # 2026-10-21: "pode se mexer" releases a hold; "se mexe" is the demonstration
    ("pode andar", "companion.release", {}), ("volta a andar", "companion.release", {}),
    ("fica parada", "companion.hold", {}), ("Célia, fica aí parada", "companion.hold", {}), ("para de andar", "companion.hold", {}),
    ("se mexe um pouquinho", "companion.wiggle", {}), ("Célia, se mexe pra eu ver", "companion.wiggle", {}), ("dá uma andadinha", "companion.wiggle", {}),
    ("pula", "companion.jump", {}), ("Célia, agacha", "companion.crouch", {}), ("levanta", "companion.stand", {}),
    ("anda comigo", "companion.follow", {}),
    ("me segue", "companion.follow", {}),
    ("Célia, olha pra mim", "companion.look", {}),
    ("me olha", "companion.look", {}),
    ("Bora construir", "series.start_gameplay", {"goal": "build"}),
    ("vamos construir nossa casa", "series.start_gameplay", {"goal": "build"}),
    ("Bora zerar", "series.start_gameplay", {"goal": "progression"}),
    ("vamos tentar zerar o Minecraft", "series.start_gameplay", {"goal": "progression"}),
    ("Célia, minera esse ferro", "companion.task", {"kind": "mine", "resource": "ferro"}),
    ("Célia, pega esse ferro", "companion.task", {"kind": "mine", "resource": "ferro"}),
    ("pega aquela madeira", "companion.task", {"kind": "mine", "resource": "madeira"}),
    ("Célia, pega esse carvão", "companion.task", {"kind": "mine", "resource": "carvao"}),
    ("Célia, segura aí", "series.talk_pause", {"freeze_world": False}),
    ("Célia, para um pouquinho", "series.talk_pause", {"freeze_world": False}),
    ("vamos conversar", "series.talk_pause", {"freeze_world": False}),
    ("Célia, congela o mundo", "series.talk_pause", {"freeze_world": True}),
    ("Célia, vem pro meu lado", "companion.return", {}),
    ("Célia, volta aqui", "companion.return", {}),
    ("volta pra perto", "companion.return", {}),
    ("vem pra câmera", "companion.return", {}),
    ("Célia, continua", "series.resume", {}),
    ("pode voltar a minerar", "series.resume", {}),
    ("Célia, encerra esse episódio", "series.end_episode", {"with_backup": True}),
    ("terminamos por hoje", "series.end_episode", {"with_backup": True}),
    ("Célia, para agora!", "control.emergency_stop", {}),
    ("Célia, cancela!", "control.emergency_stop", {}),
])
def test_every_phrase_of_the_plan_fires(text, intent, params):
    found = one(text)
    assert found.should_act and found.intent == intent and found.params == params, found


@pytest.mark.parametrize("variant", ["CÉLIA, PREPARA A SÉRIE!!!", "selia prepara a serie", "Célia,   prepara   a  série", "por favor, Célia, prepara a série",
                                     "Célia prepara a série por favor", "prepara a série agora", "  prepara a série  "])
def test_asr_noise_case_accents_and_politeness_do_not_break_a_command(variant):
    assert one(variant).intent == "series.prepare_intro"


def test_two_commands_in_one_breath_run_in_order():
    found = acts("Célia, salva o progresso. Por hoje é isso!")
    assert [i.intent for i in found] == ["world.checkpoint", "series.end_episode"]


def test_a_repeated_command_is_one_command():
    assert len(acts("Bora jogar. Bora jogar!")) == 1


def test_three_commands_are_not_executed_at_all():
    assert acts("salva o mundo. faz um backup. encerra o episódio. bora jogar") == []


def test_if_any_segment_is_not_a_command_nothing_runs():
    assert acts("Célia, salva o mundo. E depois me conta uma piada") == []


# ------------------------------------------------------------------ false positives: the plan's adversarial cases ------------------------------
@pytest.mark.parametrize("text", [
    "quando eu falar bora jogar você começa",
    "quando eu disser vamos começar a jogar, solta o mundo",
    "se eu falar bora zerar você já sai minerando?",
    "eu disse bora zerar ontem",
    "ele disse bora construir",
    "a frase bora jogar libera o mundo",
    "não vamos começar a jogar ainda",
    "não, não salva esse mundo",
    "ainda não encerra o episódio",
    "calma, não prepara a série agora",
    "jamais pega esse ferro",
    "queria que a gente começasse a jogar",
    "depois a gente vai jogar",
    "amanhã a gente prepara a série",
    "talvez bora construir",
    "será que a gente consegue zerar o Minecraft?",
    "gostaria de salvar esse mundo",
])
def test_quoted_negated_hypothetical_and_postponed_speech_never_acts(text):
    assert acts(text) == []


@pytest.mark.parametrize("text", [
    "Fala pessoal, hoje eu e a Célia vamos jogar Minecraft. Ela já conseguiu até ferro em testes. Será que agora a gente zera o jogo?",
    "bora gravar", "estamos gravando", "nós vamos gravar", "hoje a gente grava a série", "bora jogar um jogo de tabuleiro hoje à noite",
    "o mundo é muito grande", "esse mundo é lindo", "a série está ótima", "gravar", "zerar", "mundo", "para", "vem", "volta", "continua isso aqui depois",
    "ferro", "pega", "salvar", "backup", "cancelar a reunião", "para de falar do meu cabelo", "vem cá que eu te conto uma coisa",
    "pega esse ferro e faz uma espada", "minera esse ferro até acabar o mundo", "vamos jogar outra coisa", "bora comer",
    "o episódio de ontem foi legal", "terminamos a série de ontem", "encerra a live com um tchau",
])
def test_ordinary_talk_and_lookalikes_never_act(text):
    assert acts(text) == []


def test_the_intro_monologue_of_the_plan_is_not_a_command_even_with_command_words_inside():
    text = ("Fala pessoal, hoje eu e a Célia vamos jogar Minecraft. Ela já conseguiu até ferro em testes. "
            "Será que agora a gente zera o jogo? Bora jogar isso hoje!")
    assert acts(text) == []


def test_mentions_are_labelled_but_never_acted_on():
    found = detect_series_intent("amanhã eu falo bora jogar com calma")
    assert all(not i.should_act for i in found)


def test_unknown_resources_and_dangerous_words_are_not_mined_by_voice():
    for text in ["pega esse bedrock", "minera esse spawner", "pega esse tnt", "pega esse ferro e esse ouro"]:
        assert acts(text) == [], text


def test_a_stop_in_the_same_breath_wins_and_runs_alone():
    found = acts("pega esse ferro. para tudo")
    assert [i.intent for i in found] == ["control.emergency_stop"]
    found = acts("Célia, salva o mundo. Célia, para agora!")
    assert [i.intent for i in found] == ["control.emergency_stop"]


def test_empty_and_noise_inputs_are_safe():
    for text in ["", "   ", None, "...", "!!!", "ééé", "\n\n"]:
        assert detect_series_intent(text) == []


def test_a_huge_input_cannot_stall_the_detector():
    text = ("bora jogar " * 4000) + "bora"
    start = time.perf_counter()
    detect_series_intent(text)
    detect_series_intent("a" * 200000)
    detect_series_intent("celia " * 20000)
    assert time.perf_counter() - start < 1.5


# ------------------------------------------------------------------ state-aware validity ------------------------------------------------------
def test_start_gameplay_words_are_valid_only_inside_a_prepared_intro():
    start = one("Vamos começar a jogar")
    assert start.valid_states == frozenset({"INTRO_READY"})
    build = one("Bora construir")
    assert build.valid_states == frozenset({"INTRO_READY", "GAMEPLAY", "TALK_PAUSE"})
    assert "NO_WORLD" not in build.valid_states and "NO_WORLD" not in start.valid_states


def test_prepare_and_open_world_make_no_sense_in_the_middle_of_an_episode():
    assert "GAMEPLAY" not in one("prepara a série").valid_states and "GAMEPLAY" not in one("abre o mundo da série").valid_states
    assert "NO_WORLD" in one("prepara a série").valid_states


def test_pause_and_resume_are_bound_to_their_states():
    assert one("para um pouquinho").valid_states == frozenset({"GAMEPLAY"})
    assert one("pode voltar a minerar").valid_states == frozenset({"TALK_PAUSE"})


# ------------------------------------------------------------------ the gate -------------------------------------------------------------------
GATE = TrustedOwnerControlGate()


def decide(text, **kw):
    defaults = dict(authenticated_owner=True, self_echo=False, profile=PROFILE_ID, series_state="INTRO_READY", source="microphone")
    defaults.update(kw)
    return GATE.decide(one(text), **defaults)


def test_the_owner_in_the_series_profile_may_start_the_game_from_the_intro():
    assert decide("Vamos começar a jogar").allowed


@pytest.mark.parametrize("kw,reason", [
    ({"authenticated_owner": False}, "not_the_authenticated_owner"),
    ({"self_echo": True}, "probable_self_echo"),
    ({"source": "game_chat"}, "unknown_source"),
    ({"source": "stream"}, "unknown_source"),
    ({"profile": "chat"}, "profile_is_not_conversation_autonomous"),
    ({"profile": None}, "profile_is_not_conversation_autonomous"),
    ({"series_state": "NO_WORLD"}, "not_valid_in_state_NO_WORLD"),
    ({"series_state": "GAMEPLAY"}, "not_valid_in_state_GAMEPLAY"),
    ({"series_state": "BLOCKED"}, "not_valid_in_state_BLOCKED"),
])
def test_the_gate_denies_by_default(kw, reason):
    result = decide("Vamos começar a jogar", **kw)
    assert result.allowed is False and result.reason == reason


def test_emergency_stop_and_return_work_for_the_owner_in_any_profile_or_state_but_not_for_a_stranger_or_an_echo():
    for text in ["Célia, para agora!", "Célia, volta aqui"]:
        assert decide(text, profile="chat", series_state="NO_WORLD").allowed
        assert not decide(text, authenticated_owner=False).allowed
        assert not decide(text, self_echo=True).allowed


def test_a_non_command_is_never_allowed():
    marker = detect_series_intent("quando eu disser bora jogar")[0]
    assert GATE.decide(marker, authenticated_owner=True, self_echo=False, profile=PROFILE_ID, series_state="INTRO_READY", source="microphone").allowed is False
    assert GATE.decide(SeriesIntent("command", "shell.exec", {}), authenticated_owner=True, self_echo=False, profile=PROFILE_ID,
                       series_state="INTRO_READY", source="microphone").reason == "unlisted_intent"


# ------------------------------------------------------------------ confirmation words ---------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("confirma", "confirm"), ("Célia, confirma!", "confirm"), ("sim, confirma", "confirm"), ("pode reiniciar o servidor", "confirm"),
    ("pode criar", "confirm"), ("cancela", "cancel"), ("deixa pra lá", "cancel"), ("melhor não", "cancel"), ("não confirma", "cancel"),
    ("acho que não confirmo nada hoje, estou pensando em outra coisa", None), ("quando você quiser, confirma depois", None),
    ("confirma que eu tomei café", None), ("", None), ("sim", None), ("pode", None),
])
def test_confirmation_words(text, expected):
    assert classify_confirmation(text) == expected


# ------------------------------------------------------------------ the labels matter: they become the reason in logs and in the central ----------
@pytest.mark.parametrize("text", ["quando eu disser bora jogar", "se eu falar bora zerar você já sai minerando?", "eu disse bora zerar ontem", "ele falou vamos começar a jogar",
                                  "a frase bora jogar libera o mundo"])
def test_quoted_speech_is_labelled_quoted(text):
    found = detect_series_intent(text)
    assert len(found) == 1 and found[0].action == "quoted" and found[0].reason == "quoted_or_reported_speech" and not found[0].should_act


@pytest.mark.parametrize("text", ["não vamos começar a jogar ainda", "não, não salva esse mundo", "ainda não encerra o episódio", "calma, não prepara a série agora",
                                  "nunca pega esse ferro"])
def test_negated_speech_is_labelled_negated(text):
    found = detect_series_intent(text)
    assert len(found) == 1 and found[0].action == "negated" and found[0].reason == "negated_or_postponed" and not found[0].should_act


def test_a_command_phrase_inside_a_longer_sentence_is_labelled_mention():
    found = detect_series_intent("hoje eu acho que a gente vai bora jogar aquele jogo novo")
    assert found and found[0].action == "mention" and found[0].reason == "command_phrase_inside_a_longer_sentence"


def test_ordinary_talk_has_no_label_at_all():
    assert detect_series_intent("me conta como foi o seu dia") == []


# ------------------------------------------------------------------ decision 4a: short movement orders, never the language model ---------------
@pytest.mark.parametrize("text", ["olha", "olha o zumbi", "não anda", "quando eu disser anda", "anda logo com isso, caramba", "Pode se mexer, mas já tá parado.",
                                  "ele anda de bicicleta"])
def test_movement_words_inside_a_sentence_negated_or_quoted_never_act(text):
    found = [i for i in detect_series_intent(text) if i.intent in {"companion.follow", "companion.look"} and i.should_act]
    assert found == [], (text, found)


def test_follow_and_look_are_bound_to_the_series_profile_and_a_session():
    gate = TrustedOwnerControlGate()
    for text, intent in [("anda comigo", "companion.follow"), ("me olha", "companion.look")]:
        item = one(text)
        assert item.intent == intent
        assert "NO_WORLD" not in item.valid_states, "an ordinary 'anda' in a normal conversation must not become an order"
        kw = dict(authenticated_owner=True, self_echo=False, source="microphone")
        assert gate.decide(item, profile=PROFILE_ID, series_state="GAMEPLAY", **kw).allowed
        assert not gate.decide(item, profile="chat", series_state="GAMEPLAY", **kw).allowed
        assert not gate.decide(item, profile=PROFILE_ID, series_state="NO_WORLD", **kw).allowed
        assert not gate.decide(item, profile=PROFILE_ID, series_state="GAMEPLAY", authenticated_owner=False, self_echo=False, source="microphone").allowed
        assert not gate.decide(item, profile=PROFILE_ID, series_state="GAMEPLAY", authenticated_owner=True, self_echo=True, source="microphone").allowed


# ------------------------------------------------------------------ presence controls: gated, never from a quote or a negation ----------------------
@pytest.mark.parametrize("text", ["não fica parada", "quando eu disser fica parada você fica", "ele disse se mexe um pouquinho", "fica quieta"])
def test_presence_controls_do_not_fire_from_negation_quotation_or_the_silence_phrase(text):
    assert [i for i in detect_series_intent(text) if i.intent in {"companion.hold", "companion.release", "companion.wiggle"} and i.should_act] == [], text


@pytest.mark.parametrize("text", ["não pula", "não agacha", "não levanta", "eu disse 'pula'", "quando eu disser agacha"])
def test_posture_commands_do_not_fire_from_negation_or_quotation(text):
    assert [i for i in detect_series_intent(text) if i.intent in {"companion.jump", "companion.crouch", "companion.stand"} and i.should_act] == []


def test_hold_release_and_wiggle_only_exist_inside_a_series_session_in_the_series_profile():
    gate = TrustedOwnerControlGate()
    for text, intent in (("fica parada", "companion.hold"), ("pode se mexer", "companion.release"), ("se mexe um pouquinho", "companion.wiggle")):
        item = one(text)
        assert item.intent == intent and "NO_WORLD" not in item.valid_states
        kw = dict(authenticated_owner=True, self_echo=False, source="microphone")
        for state in ("INTRO_READY", "GAMEPLAY", "TALK_PAUSE"):
            assert gate.decide(item, profile=PROFILE_ID, series_state=state, **kw).allowed, (text, state)
        assert not gate.decide(item, profile="chat", series_state="GAMEPLAY", **kw).allowed
        assert not gate.decide(item, profile=PROFILE_ID, series_state="NO_WORLD", **kw).allowed
        assert not gate.decide(item, profile=PROFILE_ID, series_state="GAMEPLAY", authenticated_owner=False, self_echo=False, source="microphone").allowed
