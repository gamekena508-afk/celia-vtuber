import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import celia.core.roteiro_live as roteiro_module
from celia.celia_singleton import CeliaSingleton
from celia.core.roteiro_live import SCRIPT_PATH, RoteiroLiveRuntime


def run(coro):
    return asyncio.run(coro)


def play_line(runtime, text):
    speech_id = "test-" + str(len(runtime.history))
    run(runtime.observe_message({"type": "audio", "speech_id": speech_id, "display_text": {"text": text}}))
    run(runtime.observe_message({"type": "audio-playback-started", "speech_id": speech_id}))
    run(runtime.observe_message({"type": "audio-playback-finished", "speech_id": speech_id, "status": "ended"}))


def advance_to_first_celia(runtime):
    run(runtime.observe_message({
        "type": "user-input-transcription",
        "text": "Fala, pessoal, tudo bem com vocês?",
    }))
    for text in (
        "Eu criei a Célia, e hoje eu vou desafiar ela.",
        "São três jogos: jogo da velha, dama e xadrez.",
        "Quem joga melhor: eu ou a IA?",
    ):
        run(runtime.observe_message({"type": "user-input-transcription", "text": text}))


def test_script_has_recording_beats_and_branches():
    data = json.loads(SCRIPT_PATH.read_text(encoding="utf-8"))
    ids = {beat["id"] for beat in data["beats"]}
    assert data["id"] == "ia-vs-criador-volta-do-canal"
    assert data["version"] == 2
    assert data["start"] == "00_abertura_host"
    assert [beat["id"] for beat in data["beats"][:5]] == [
        "00_abertura_host",
        "01_desafio",
        "02_aposta",
        "03_jogo_velha",
        "03A_velha_criador",
    ]
    assert {"03A_velha_criador", "03B_velha_celia", "03C_velha_empate"} <= ids
    velha = next(beat for beat in data["beats"] if beat["id"] == "03_jogo_velha")
    assert velha["branches"]["celia"] == "03B_velha_celia"
    assert data["beats"][0]["lines"] == [
        {"speaker": "Criador", "text": "Fala, pessoal, tudo bem com vocês?"}
    ]
    assert all(line["speaker"] != "Célia" for line in data["beats"][0]["lines"])
    assert "hiato" not in " ".join(
        line.get("text", "").casefold()
        for beat in data["beats"]
        for line in beat.get("lines", [])
    )


def test_live_snapshot_starts_at_clean_host_opening_and_tracks_first_celia_exchange():
    runtime = RoteiroLiveRuntime()
    started = run(runtime.activate())
    assert started["active"] is True
    assert started["current_beat"]["id"] == "00_abertura_host"
    assert started["current_beat"]["lines"][0]["speaker"] == "Criador"
    assert started["current_beat"]["lines"][0]["status"] == "pending"

    run(
        runtime.observe_message(
            {"type": "asr-partial", "text": "Fala, pessoal", "utterance_id": "u1"}
        )
    )
    assert runtime.snapshot()["criador"]["partial"] == "Fala, pessoal"

    run(
        runtime.observe_message(
            {
                "type": "user-input-transcription",
                "text": "Fala, pessoal, tudo bem com vocês?",
                "utterance_id": "u1",
            }
        )
    )
    assert runtime.snapshot()["criador"]["last_final"].startswith("Fala, pessoal")
    assert runtime.snapshot()["current_beat"]["id"] == "01_desafio"
    assert runtime.planned_celia_reply() is None

    for text in (
        "Eu criei a Célia, e hoje eu vou desafiar ela.",
        "São três jogos: jogo da velha, dama e xadrez.",
        "Quem joga melhor: eu ou a IA?",
    ):
        run(runtime.observe_message({
            "type": "user-input-transcription",
            "text": text,
            "utterance_id": "u1",
        }))

    assert runtime.snapshot()["current_beat"]["id"] == "01_desafio"
    assert runtime.planned_celia_reply() == {
        "beat_id": "01_desafio",
        "line_id": "01_desafio:3",
        "text": "Eu vou ganhar o quê com isso?",
    }

    play_line(runtime, "Eu vou ganhar o quê com isso?")
    snap = runtime.snapshot()
    assert snap["current_beat"]["id"] == "02_aposta"
    assert runtime.planned_celia_reply() is None
    assert snap["celia"]["last_text"] == "Eu vou ganhar o quê com isso?"
    assert snap["history"][-1]["speaker"] == "Célia"


def test_criador_only_turns_blue_for_script_match_and_persists_session_log(tmp_path, monkeypatch):
    monkeypatch.setattr(roteiro_module, "SESSION_LOG_DIR", tmp_path)
    runtime = RoteiroLiveRuntime()
    run(runtime.activate())

    run(runtime.observe_message({"type": "user-input-transcription", "text": "Vou falar uma coisa fora do roteiro"}))
    assert runtime.snapshot()["criador_match"]["matched"] is False
    assert runtime.snapshot()["current_beat"]["id"] == "00_abertura_host"

    run(runtime.observe_message({
        "type": "user-input-transcription",
        "text": "Fala, pessoal, tudo bem com vocês?",
    }))
    snapshot = runtime.snapshot()
    assert snapshot["criador_match"]["matched"] is True
    assert snapshot["current_beat"]["id"] == "01_desafio"
    assert runtime.planned_celia_reply() is None

    log_path = tmp_path / f"{snapshot['session_id']}.jsonl"
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["event"] == "session.started"
    assert any(
        record["event"] == "criador.final"
        and record["data"]["script_match"]["matched"] is False
        for record in records
    )
    assert any(record["event"] == "beat.changed" for record in records)


def test_short_asr_cue_with_one_clear_anchor_still_matches_current_line():
    runtime = RoteiroLiveRuntime()
    run(runtime.activate())

    run(runtime.observe_message({
        "type": "user-input-transcription",
        "text": "Fala, pessoal!",
        "utterance_id": "asr-observed-1",
    }))

    snapshot = runtime.snapshot()
    assert snapshot["criador_match"]["matched"] is True
    assert snapshot["criador_match"]["line_id"] == "00_abertura_host:0"
    assert runtime.planned_celia_reply() is None


def test_celia_script_line_can_advance_to_the_next_beat():
    runtime = RoteiroLiveRuntime()
    run(runtime.activate())
    advance_to_first_celia(runtime)
    play_line(runtime, "Eu vou ganhar o quê com isso?")
    run(runtime.observe_message({"type": "user-input-transcription", "text": "Como assim?"}))
    play_line(runtime, "Tu que tá me chamando pra jogar.")
    assert runtime.snapshot()["current_beat"]["id"] == "02_aposta"


def test_first_celia_exchange_is_recorded_before_advancing_to_the_bet():
    runtime = RoteiroLiveRuntime()
    run(runtime.activate())
    advance_to_first_celia(runtime)
    assert runtime.snapshot()["current_beat"]["id"] == "01_desafio"
    play_line(runtime, "Eu vou ganhar o quê com isso?")
    assert runtime.snapshot()["history"][-1]["speaker"] == "Célia"
    assert runtime.snapshot()["current_beat"]["id"] == "02_aposta"


def test_approved_text_is_hidden_before_audio_and_tts_failure_is_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(roteiro_module, "SESSION_LOG_DIR", tmp_path)
    runtime = RoteiroLiveRuntime()
    run(runtime.activate())

    run(runtime.observe_message({"type": "celia.text.approved", "text": "Finalmente."}))
    run(runtime.observe_message({
        "type": "celia.tts.failed",
        "speech_id": "speech-1",
        "error": "GPU timeout",
    }))
    snapshot = runtime.snapshot()
    assert snapshot["celia"]["last_text"] == ""
    # Failure is logged, but it must not strand the live panel in speaking/error.
    assert snapshot["celia"]["state"] == "idle"
    assert snapshot["status"] == "LIVE • FALA RECUPERÁVEL"
    assert len([item for item in snapshot["history"] if item["speaker"] == "Célia"]) == 0
    log_path = tmp_path / f"{snapshot['session_id']}.jsonl"
    assert "celia.tts_failed" in log_path.read_text(encoding="utf-8")


def test_bridge_tts_ready_clears_preparing_audio_state(tmp_path, monkeypatch):
    monkeypatch.setattr(roteiro_module, "SESSION_LOG_DIR", tmp_path)
    runtime = RoteiroLiveRuntime()
    run(runtime.activate())

    play_line(runtime, "Por enquanto.")
    run(runtime.observe_message({"type": "celia.tts.ready", "source": "bridge.process"}))

    assert runtime.snapshot()["celia"]["state"] == "audio_pronto"
    log_path = tmp_path / f"{runtime.snapshot()['session_id']}.jsonl"
    assert "celia.tts_ready" in log_path.read_text(encoding="utf-8")


def test_singleton_suppresses_duplicate_owner_turn_inside_audio_handoff_window():
    singleton = CeliaSingleton()
    singleton._processing = True
    first = run(singleton.submit_input(
        "Célia, se tu ganhar eu te dou 100 reais.",
        origin="owner",
        speaker_name="Criador",
        behavior_observed=True,
    ))
    second = run(singleton.submit_input(
        "Célia, se tu ganhar eu te dou 100 reais.",
        origin="owner",
        speaker_name="Criador",
        behavior_observed=True,
    ))
    assert first is True
    assert second is False
    assert singleton.pending_batches == 1


def test_game_beat_waits_for_structured_result_instead_of_guessing_winner_from_speech():
    runtime = RoteiroLiveRuntime()
    run(runtime.activate())
    run(
        runtime.control(
            {"action": "branch", "beat_id": "03_jogo_velha", "confirm": True}
        )
    )

    # Finishing the host's game-intro line must not choose next[0] (Criador wins).
    run(runtime.observe_message({
        "type": "user-input-transcription",
        "text": "Vamos começar no fácil: jogo da velha.",
    }))
    snapshot = runtime.snapshot()
    assert snapshot["current_beat"]["id"] == "03_jogo_velha"
    assert snapshot["current_beat"]["branch_source"] == "game_result"
    assert snapshot["next_beats"] == []

    # Casual speech containing only the name Célia previously matched
    # "Célia ganhou" with score 1 and falsely declared her the winner.
    run(runtime.observe_message({
        "type": "user-input-transcription",
        "text": "Qual foi, Célia? O negócio é assim.",
    }))
    assert runtime.snapshot()["current_beat"]["id"] == "03_jogo_velha"

    run(runtime.observe_message({
        "type": "game.result",
        "game": "tic-tac-toe",
        "match_id": "match-real",
        "winner_controller": "celia",
        "turn": 5,
    }))
    snapshot = runtime.snapshot()
    assert snapshot["current_beat"]["id"] == "03B_velha_celia"
    assert snapshot["next_beats"][0]["id"] == "04_dama"


def test_game_result_and_manual_controls_update_route_without_new_runtime():
    runtime = RoteiroLiveRuntime()
    run(runtime.activate())
    run(
        runtime.control(
            {"action": "branch", "beat_id": "03_jogo_velha", "confirm": True}
        )
    )
    run(
        runtime.observe_message(
            {"type": "game.result", "game": "jogo da velha", "winner": "celia"}
        )
    )
    assert runtime.snapshot()["current_beat"]["id"] == "03B_velha_celia"

    run(runtime.control({"action": "manual"}))
    assert runtime.snapshot()["auto_follow"] is False
    run(runtime.control({"action": "next"}))
    assert runtime.snapshot()["current_beat"]["id"] == "04_dama"
    run(runtime.control({"action": "live"}))
    assert runtime.snapshot()["auto_follow"] is True
    run(runtime.control({"action": "next"}))
    assert runtime.snapshot()["auto_follow"] is False
    run(runtime.control({"action": "live"}))
    assert runtime.snapshot()["auto_follow"] is True


def test_checkers_and_chess_only_advance_on_matching_result_and_score_once():
    runtime = RoteiroLiveRuntime()
    run(runtime.activate())
    run(runtime.control({"action": "branch", "beat_id": "04_dama", "confirm": True}))

    snapshot = runtime.snapshot()
    assert snapshot["current_beat"]["id"] == "04_dama"
    assert snapshot["current_beat"]["branch_source"] == "game_result"
    assert snapshot["current_beat"]["result_game"] == "checkers"
    assert snapshot["next_beats"] == []

    # A late result from tic-tac-toe must not unlock the checkers beat.
    run(runtime.observe_message({
        "type": "game.result",
        "game": "tic-tac-toe",
        "match_id": "old-ttt",
        "winner_controller": "celia",
        "turn": 5,
    }))
    assert runtime.snapshot()["current_beat"]["id"] == "04_dama"
    assert runtime.snapshot()["game_scoreboard"] == {"criador": 0, "celia": 0, "draw": 0}

    run(runtime.observe_message({
        "type": "game.result",
        "game": "checkers",
        "match_id": "checkers-1",
        "winner_controller": "criador",
        "turn": 17,
    }))
    snapshot = runtime.snapshot()
    assert snapshot["current_beat"]["id"] == "05_escalada"
    assert snapshot["game_scoreboard"] == {"criador": 1, "celia": 0, "draw": 0}

    run(runtime.control({"action": "branch", "beat_id": "06_xadrez", "confirm": True}))
    snapshot = runtime.snapshot()
    assert snapshot["current_beat"]["result_game"] == "chess"
    assert snapshot["next_beats"] == []

    # Replaying the previous game's result cannot advance chess or add score.
    run(runtime.observe_message({
        "type": "game.result",
        "game": "checkers",
        "match_id": "checkers-1",
        "winner_controller": "criador",
        "turn": 17,
    }))
    assert runtime.snapshot()["current_beat"]["id"] == "06_xadrez"
    assert runtime.snapshot()["game_scoreboard"] == {"criador": 1, "celia": 0, "draw": 0}

    run(runtime.observe_message({
        "type": "game.result",
        "game": "chess",
        "match_id": "chess-1",
        "winner_controller": "celia",
        "turn": 42,
    }))
    snapshot = runtime.snapshot()
    assert snapshot["current_beat"]["id"] == "07_resultado"
    assert snapshot["game_scoreboard"] == {"criador": 1, "celia": 1, "draw": 0}


def test_teleprompter_is_separate_black_white_surface():
    root = Path(__file__).parents[1] / "frontend" / "roteiro-live"
    html = (root / "index.html").read_text(encoding="utf-8")
    css = (root / "style.css").read_text(encoding="utf-8")
    assert "/roteiro/ws" in html or "/roteiro/ws" in (root / "app.js").read_text(
        encoding="utf-8"
    )
    assert "--bg: #000" in css
    assert "color: var(--fg)" in css
    assert "[hidden]" in css
    assert "CONTROLES" in html
    assert "--criador: #55a8ff" in css
    assert "--celia: #ffd84d" in css


def test_planned_celia_reply_becomes_due_only_after_setup_not_after_greeting():
    runtime = RoteiroLiveRuntime()
    run(runtime.activate())
    assert runtime.planned_celia_reply() is None
    run(runtime.observe_message({
        "type": "user-input-transcription",
        "text": "Fala, pessoal, tudo bem com vocês?",
    }))
    assert runtime.current_beat_id == "01_desafio"
    assert runtime.planned_celia_reply() is None

    for text in (
        "Eu criei a Célia, e hoje eu vou desafiar ela.",
        "São três jogos: jogo da velha, dama e xadrez.",
        "Quem joga melhor: eu ou a IA?",
    ):
        run(runtime.observe_message({"type": "user-input-transcription", "text": text}))

    assert runtime.planned_celia_reply() == {
        "beat_id": "01_desafio",
        "line_id": "01_desafio:3",
        "text": "Eu vou ganhar o quê com isso?",
    }
    play_line(runtime, "Eu vou ganhar o quê com isso?")
    assert runtime.planned_celia_reply() is None


def test_force_stop_cancels_only_roteiro_audio_without_a_user_turn(monkeypatch):
    runtime = RoteiroLiveRuntime()
    singleton = SimpleNamespace(
        broadcast=AsyncMock(),
        interrupt=AsyncMock(return_value=False),
        clear_pending_inputs=lambda: None,
    )
    monkeypatch.setattr(CeliaSingleton, "get_instance", lambda: singleton)

    run(runtime.activate())
    run(runtime.observe_message({
        "type": "audio", "speech_id": "active", "display_text": {"text": "Finalmente."},
    }))
    run(runtime.observe_message({"type": "audio-playback-started", "speech_id": "active"}))
    run(runtime.observe_message({
        "type": "audio", "speech_id": "pending", "display_text": {"text": "Por enquanto."},
    }))
    before_history = list(runtime.history)

    snapshot = run(runtime.control({"action": "force_stop", "source": "roteiro.ctrl_p"}))

    assert snapshot["status"] == "FORCE-STOP • PLAYBACK CANCELADO"
    assert snapshot["celia"]["state"] == "force_stop"
    assert runtime._pending_audio == {}
    assert list(runtime.history) == before_history
    assert all(item["speaker"] != "Criador" for item in runtime.history)
    assert singleton.broadcast.await_args_list == [
        call({"type": "playback.cancel", "speech_id": "active", "source": "roteiro.force_stop"}),
        call({"type": "playback.cancel", "speech_id": "pending", "source": "roteiro.force_stop"}),
        call({"type": "interrupt-signal", "text": "roteiro-force-stop"}),
    ]
    singleton.interrupt.assert_awaited_once_with(role="browser", force=True)


def test_force_stop_is_ignored_outside_an_active_roteiro_session(monkeypatch):
    runtime = RoteiroLiveRuntime()
    singleton = SimpleNamespace(
        broadcast=AsyncMock(),
        interrupt=AsyncMock(),
        clear_pending_inputs=lambda: None,
    )
    monkeypatch.setattr(CeliaSingleton, "get_instance", lambda: singleton)

    snapshot = run(runtime.control({"action": "force_stop"}))

    assert snapshot["status"] == "INATIVO"
    singleton.broadcast.assert_not_awaited()
    singleton.interrupt.assert_not_awaited()



def test_multi_chunk_turn_only_completes_after_all_successful_playback():
    runtime = RoteiroLiveRuntime()
    run(runtime.activate())
    turn_id = "turn-multi"

    for speech_id, text in (("s1", "Um"), ("s2", "dois.")):
        run(runtime.observe_message({
            "type": "audio",
            "speech_id": speech_id,
            "turn_id": turn_id,
            "display_text": {"text": text},
        }))
    run(runtime.observe_message({
        "type": "celia.tts.turn-sealed",
        "turn_id": turn_id,
        "speech_ids": ["s1", "s2"],
    }))

    run(runtime.observe_message({"type": "audio-playback-started", "speech_id": "s1"}))
    run(runtime.observe_message({
        "type": "audio-playback-finished", "speech_id": "s1", "status": "ended",
    }))
    assert [item for item in runtime.history if item["speaker"] == "Célia"] == []

    run(runtime.observe_message({"type": "audio-playback-started", "speech_id": "s2"}))
    run(runtime.observe_message({
        "type": "audio-playback-finished", "speech_id": "s2", "status": "ended",
    }))
    celia = [item for item in runtime.history if item["speaker"] == "Célia"]
    assert len(celia) == 1
    assert celia[0]["text"] == "Um dois."



def test_multi_chunk_cancel_keeps_script_line_recoverable():
    runtime = RoteiroLiveRuntime()
    run(runtime.activate())
    turn_id = "turn-cancel"

    for speech_id, text in (("s1", "Uma"), ("s2", "fala.")):
        run(runtime.observe_message({
            "type": "audio",
            "speech_id": speech_id,
            "turn_id": turn_id,
            "display_text": {"text": text},
        }))
    run(runtime.observe_message({
        "type": "celia.tts.turn-sealed",
        "turn_id": turn_id,
        "speech_ids": ["s1", "s2"],
    }))
    run(runtime.observe_message({"type": "audio-playback-started", "speech_id": "s1"}))
    run(runtime.observe_message({
        "type": "audio-playback-finished", "speech_id": "s1", "status": "ended",
    }))
    run(runtime.observe_message({"type": "audio-playback-started", "speech_id": "s2"}))
    run(runtime.observe_message({
        "type": "audio-playback-finished", "speech_id": "s2", "status": "cancelled",
    }))

    assert [item for item in runtime.history if item["speaker"] == "Célia"] == []
    assert runtime.snapshot()["status"] == "LIVE • FALA RECUPERÁVEL"


def test_retake_rejects_late_audio_from_previous_take(monkeypatch):
    runtime = RoteiroLiveRuntime()
    run(runtime.activate())
    old_take = runtime.take_id
    turn_id = "old-turn"
    speech_id = "old-speech"

    run(runtime.observe_message({
        "type": "speech.started",
        "utterance_id": turn_id,
    }))
    run(runtime.observe_message({
        "type": "audio",
        "speech_id": speech_id,
        "turn_id": turn_id,
        "display_text": {"text": "Áudio antigo que não pode reaparecer."},
    }))
    assert speech_id in runtime._pending_audio
    monkeypatch.setattr(runtime, "_force_stop_playback", AsyncMock())

    snapshot = run(runtime.control({"action": "retake"}))
    assert snapshot["take_id"] != old_take

    run(runtime.observe_message({
        "type": "audio-playback-started",
        "speech_id": speech_id,
    }))
    run(runtime.observe_message({
        "type": "audio-playback-finished",
        "speech_id": speech_id,
        "status": "ended",
    }))

    assert speech_id not in runtime._pending_audio
    assert speech_id not in runtime._active_audio
    assert not any(
        item.get("speaker") == "Célia"
        and "Áudio antigo" in item.get("text", "")
        for item in runtime.history
    )


def test_back_line_reopens_only_the_last_completed_line(monkeypatch):
    runtime = RoteiroLiveRuntime()
    run(runtime.activate())
    advance_to_first_celia(runtime)
    play_line(runtime, "Eu vou ganhar o quê com isso?")
    assert runtime.current_beat_id == "02_aposta"
    old_take = runtime.take_id
    monkeypatch.setattr(runtime, "_force_stop_playback", AsyncMock())

    snapshot = run(runtime.control({"action": "back_line"}))

    assert snapshot["current_beat"]["id"] == "01_desafio"
    statuses = {line["id"]: line["status"] for line in snapshot["current_beat"]["lines"]}
    assert statuses["01_desafio:0"] == "done"
    assert statuses["01_desafio:1"] == "done"
    assert statuses["01_desafio:2"] == "done"
    assert statuses["01_desafio:3"] == "pending"
    assert snapshot["take_id"] != old_take



def test_forward_line_paints_the_last_line_before_changing_beat():
    """Um clique, um efeito visível.

    O clique que conclui a última linha do beat NÃO troca de beat: se trocasse,
    o snapshot devolveria o beat seguinte e a cor da linha recém-concluída
    (azul do criador / amarelo da Célia) nunca apareceria no celular.
    """
    runtime = RoteiroLiveRuntime()
    run(runtime.activate())

    # `00_abertura_host` tem uma única linha: ela é pintada e o beat continua.
    snapshot = run(runtime.control({"action": "forward_line"}))
    assert snapshot["current_beat"]["id"] == "00_abertura_host"
    statuses = {line["id"]: line["status"] for line in snapshot["current_beat"]["lines"]}
    assert statuses["00_abertura_host:0"] == "done"
    assert "AVANÇOU FALA 00_abertura_host:0" in snapshot["status"]

    # Só o clique seguinte, sem linha pendente, muda de beat.
    snapshot = run(runtime.control({"action": "forward_line"}))
    assert snapshot["current_beat"]["id"] == "01_desafio"
    assert "AVANÇOU PARA 01_desafio" in snapshot["status"]

    snapshot = run(runtime.control({"action": "forward_line"}))
    statuses = {line["id"]: line["status"] for line in snapshot["current_beat"]["lines"]}
    assert snapshot["current_beat"]["id"] == "01_desafio"
    assert statuses["01_desafio:0"] == "done"
    assert statuses["01_desafio:1"] == "pending"


def test_forward_line_advances_one_visible_line_per_click_until_the_beat_ends():
    runtime = RoteiroLiveRuntime()
    run(runtime.activate())
    run(runtime.control({"action": "forward_line"}))  # 00_abertura_host:0
    run(runtime.control({"action": "forward_line"}))  # -> 01_desafio

    # `01_desafio` tem 6 linhas: 6 cliques pintam todas, sem trocar de beat.
    for index in range(6):
        snapshot = run(runtime.control({"action": "forward_line"}))
        assert snapshot["current_beat"]["id"] == "01_desafio"
        statuses = {
            line["id"]: line["status"] for line in snapshot["current_beat"]["lines"]
        }
        assert statuses[f"01_desafio:{index}"] == "done"

    assert all(status == "done" for status in statuses.values())

    snapshot = run(runtime.control({"action": "forward_line"}))
    assert snapshot["current_beat"]["id"] == "02_aposta"


def test_retake_self_reopens_criador_line_and_its_following_celia_line(monkeypatch):
    runtime = RoteiroLiveRuntime()
    run(runtime.activate())
    advance_to_first_celia(runtime)
    play_line(runtime, "Eu vou ganhar o quê com isso?")
    monkeypatch.setattr(runtime, "_force_stop_playback", AsyncMock())

    snapshot = run(runtime.control({"action": "retake_self"}))

    assert snapshot["current_beat"]["id"] == "01_desafio"
    statuses = {line["id"]: line["status"] for line in snapshot["current_beat"]["lines"]}
    assert statuses["01_desafio:0"] == "done"
    assert statuses["01_desafio:1"] == "done"
    assert statuses["01_desafio:2"] == "pending"
    assert statuses["01_desafio:3"] == "pending"
    assert "REFAZER 01_desafio:2" in snapshot["status"]


def test_word_anchor_reopens_the_matching_script_line(monkeypatch):
    runtime = RoteiroLiveRuntime()
    run(runtime.activate())
    advance_to_first_celia(runtime)
    play_line(runtime, "Eu vou ganhar o quê com isso?")
    monkeypatch.setattr(runtime, "_force_stop_playback", AsyncMock())

    snapshot = run(runtime.control({
        "action": "back_to_word", "anchor": "Quem joga"
    }))

    assert snapshot["current_beat"]["id"] == "01_desafio"
    statuses = {line["id"]: line["status"] for line in snapshot["current_beat"]["lines"]}
    assert statuses["01_desafio:0"] == "done"
    assert statuses["01_desafio:1"] == "done"
    assert statuses["01_desafio:2"] == "pending"
    assert statuses["01_desafio:3"] == "pending"
    assert "ÂNCORA 01_desafio:2" in snapshot["status"]


def test_restore_from_behavior_volta_ao_roteiro_do_dono_no_boot():
    """Decisão 6(a): reiniciar não pode mais jogar o celular no roteiro canônico."""
    runtime = RoteiroLiveRuntime()

    class _Comportamento:
        profile = "gravacao"

        class script:
            @staticmethod
            def export():
                return {
                    "id": "discord-tutorial-2026-09-29",
                    "title": "Discord",
                    "version": 2,
                    "start": "01_abertura",
                    "beats": [
                        {"id": "01_abertura", "title": "Abertura",
                         "lines": [{"speaker": "Criador", "text": "oi"}], "next": []}
                    ],
                }

        @staticmethod
        def load_script(dados):
            return dados

        @staticmethod
        def set_script_position(_beat):
            return None

    restaurado = run(runtime.restore_from_behavior(_Comportamento()))

    assert restaurado == "discord-tutorial-2026-09-29"
    # ORDEM DO DONO, 2026-09-30: "no início é pra eu escolher o roteiro."
    # A expectativa antiga era `active is True`. Ela deixou de valer: ativar no
    # boot escondia o seletor do painel, que só aparece com `!snapshot.active`.
    # Restaurar continua obrigatório (decisão 6a); ATIVAR virou escolha do dono.
    assert runtime.active is False, "o boot não pode escolher por ele"
    # Mas o roteiro dele tem de estar carregado e pronto, não o canônico.
    assert runtime._script["id"] == "discord-tutorial-2026-09-29"
    assert runtime._script["id"] != "ia-vs-criador-volta-do-canal"
    assert runtime._beats, "o teleprompter precisa dos beats prontos para quando ele escolher"


def test_restore_from_behavior_nao_faz_nada_sem_roteiro_carregado():
    """CONTROLE NEGATIVO: comportamento vazio não pode ativar o canônico do disco."""
    runtime = RoteiroLiveRuntime()

    class _Vazio:
        profile = "gravacao"

        class script:
            @staticmethod
            def export():
                return {"beats": []}

    assert run(runtime.restore_from_behavior(_Vazio())) is None
    assert runtime.active is False


def test_restore_from_behavior_ignora_perfil_de_conversa():
    runtime = RoteiroLiveRuntime()

    class _Chat:
        profile = "chat"

        class script:
            @staticmethod
            def export():
                return {"id": "x", "beats": [{"id": "a", "lines": []}]}

    assert run(runtime.restore_from_behavior(_Chat())) is None
    assert runtime.active is False
