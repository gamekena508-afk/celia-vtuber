from pathlib import Path

import celia.core.continuous_behavior as behavior_module
from celia.core.continuous_behavior import ContinuousBehaviorRuntime


def runtime(tmp_path: Path) -> ContinuousBehaviorRuntime:
    return ContinuousBehaviorRuntime(tmp_path / "latency-profile.json")


def test_speech_ended_does_not_reset_ingress_clock(tmp_path, monkeypatch):
    now = {"value": 100.0}
    monkeypatch.setattr(behavior_module.time, "monotonic", lambda: now["value"])
    rt = runtime(tmp_path)
    turn = rt.begin_turn("typed-turn")
    assert rt._turn_started_at_by_id[turn] == 100.0

    now["value"] = 101.0
    rt.observe("speech.ended", utterance_id=turn)
    assert rt._turn_started_at_by_id[turn] == 100.0

    now["value"] = 102.5
    rt.mark_latency("probe_e2e", turn_id=turn)
    assert rt.latency.estagios_do_turno(turn)["probe_e2e"] == 2500


def test_each_turn_keeps_its_own_immutable_clock(tmp_path, monkeypatch):
    now = {"value": 10.0}
    monkeypatch.setattr(behavior_module.time, "monotonic", lambda: now["value"])
    rt = runtime(tmp_path)
    first = rt.begin_turn("first")
    now["value"] = 20.0
    second = rt.begin_turn("second")
    now["value"] = 25.0
    rt.mark_latency("e2e", turn_id=first)
    rt.mark_latency("e2e", turn_id=second)
    assert rt.latency.estagios_do_turno(first)["e2e"] == 15000
    assert rt.latency.estagios_do_turno(second)["e2e"] == 5000


def test_playback_exposes_e2e_and_tts_local_clocks_separately(tmp_path, monkeypatch):
    now = {"value": 100.0}
    monkeypatch.setattr(behavior_module.time, "monotonic", lambda: now["value"])
    rt = runtime(tmp_path)
    turn = rt.begin_turn("turn")

    now["value"] = 102.0
    chunk = rt.create_tts_chunk("fala", turn_id=turn)
    now["value"] = 105.0
    rt.observe("celia.tts_started", utterance_id=chunk["speech_id"])

    stages = rt.latency.estagios_do_turno(turn)
    assert stages["first_audio_playback_started"] == 5000
    assert stages["tts_first_audio"] == 3000


def test_compact_prompt_preserves_direct_factual_answer_rule(tmp_path):
    from celia.agent.agents.basic_memory_agent import BasicMemoryAgent
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
    compact = agent._compact_system_prompt(
        "SYSTEM\n\n[CONVERSA NATURAL]\n- pergunta factual direta"
    )
    assert "Pergunta factual direta" in compact
    assert "responda o fato na primeira frase" in compact
