"""The 70B conversation prompt keeps the recording context (Fatia 2, 2026-10-02).

Measured: _compact_system_prompt() rebuilt the prompt from an allowlist and
dropped [SCRIPT REFERENCE]. During the 3D recording Célia answered
"Então, você acha que a visão da Célia vai melhorar isso aqui?" without knowing
she was recording or which beat was on. Decision 1 of
docs/planos/PLANO-CELIA-GRAVACAO-RESPOSTA-2026-10-02.md: keep only the current
beat, next beats and profile, bounded (~600 chars). Chat stays unchanged.
"""
import json
from pathlib import Path

from celia.agent.agents.basic_memory_agent import BasicMemoryAgent
from celia.core.continuous_behavior import ContinuousBehaviorRuntime

ROOT = Path(__file__).resolve().parents[1]
BOUND = 600


def _full_prompt(rt):
    return (
        "Persona base longa.\n\nINTERLOCUTOR ATUAL: Criador.\n\n"
        + rt.build_prompt_context()
        + "\n\n[CELIA IDENTITY - shared across every operational profile]\n- identidade"
    )


def _recording_runtime(tmp_path):
    rt = ContinuousBehaviorRuntime(tmp_path / "profile.json")
    rt.select_profile("gravacao")
    rt.load_script(json.loads((ROOT / "roteiros/celia_3d_passo_a_passo.json").read_text(encoding="utf-8")))
    rt.set_script_position("03_primeiro_beco")
    return rt


def test_recording_prompt_keeps_current_beat_and_profile(tmp_path):
    rt = _recording_runtime(tmp_path)
    full = _full_prompt(rt)
    assert "SCRIPT REFERENCE" in full  # control: the context exists before compaction
    out = BasicMemoryAgent._compact_system_prompt(None, full)
    assert "Neural 4" in out, "current beat text must reach the 70B"
    assert "gravacao" in out
    assert "INTERLOCUTOR ATUAL" in out and "CELIA IDENTITY" in out


def test_recording_block_is_bounded(tmp_path):
    rt = _recording_runtime(tmp_path)
    out = BasicMemoryAgent._compact_system_prompt(None, _full_prompt(rt))
    start = out.index("[GRAVACAO EM ANDAMENTO")
    block = out[start:].split("\n\n[", 1)[0]
    assert len(block) <= BOUND, len(block)


def test_chat_prompt_is_unchanged(tmp_path):
    rt = ContinuousBehaviorRuntime(tmp_path / "profile.json")
    rt.select_profile("chat")
    full = _full_prompt(rt)
    assert "SCRIPT REFERENCE" not in full
    out = BasicMemoryAgent._compact_system_prompt(None, full)
    assert "GRAVACAO EM ANDAMENTO" not in out
    assert "PROFILE:" not in out
