"""23/10: a scripted series line becomes HER turn only after the audio really played (so the LLM has it when the owner comments)."""
from types import SimpleNamespace

import celia.bridge_routes as br


class Agent:
    def __init__(self):
        self.memory = []

    def _add_message(self, text, role, display_text=None):
        self.memory.append((role, text, getattr(display_text, "name", None)))


def singleton(monkeypatch):
    stored = []
    monkeypatch.setattr(br, "store_message", lambda conf, uid, role, text, name=None: stored.append((role, text, name)))
    agent = Agent()
    ctx = SimpleNamespace(history_uid="h1", character_config=SimpleNamespace(conf_uid="c1"), agent_engine=agent)
    return SimpleNamespace(context=ctx), agent, stored


def test_played_line_becomes_her_own_turn_in_memory_and_on_disk(monkeypatch):
    s, agent, stored = singleton(monkeypatch)
    assert br._record_spoken_line(s, "Toma, guardei um presente pra ti também.", "Célia", "ended") == "spoken"
    assert agent.memory == [("assistant", "Toma, guardei um presente pra ti também.", "Célia")]
    assert stored == [("ai", "Toma, guardei um presente pra ti também.", "Célia")]


def test_broadcast_without_browser_ack_still_counts_as_spoken(monkeypatch):
    s, agent, _ = singleton(monkeypatch)
    assert br._record_spoken_line(s, "Pai, morri.", "Célia", "timeout") == "spoken"
    assert br._record_spoken_line(s, "Pai, morri.", "Célia", "local_duration_elapsed") == "spoken"


def test_cut_line_is_marked_and_never_played_line_is_not_recorded(monkeypatch):
    s, agent, stored = singleton(monkeypatch)
    assert br._record_spoken_line(s, "Eu prefiro morrer do que perder a vida.", "Célia", "cancelled") == "interrupted"
    assert agent.memory[-1][1].endswith("(fui interrompida)")
    before = len(agent.memory)
    assert br._record_spoken_line(s, "Nunca tocou.", "Célia", "pending") is None
    assert br._record_spoken_line(s, "Nunca tocou.", "Célia", None) is None
    assert len(agent.memory) == before and len(stored) == before


def test_request_flag_exists_and_forces_waiting_for_playback():
    req = br.SpeakRequest(text="Pai, morri.", record_context=True, record_after_playback=True)
    assert req.record_after_playback is True
    assert "recorded" in br.SpeakResponse.model_fields


# ---- 24/10 (missão EP1, risco R2): o revisor GPT barrou falas do Jev em 21/10; a fala roteirizada com a credencial do dono passa só pelo guardião local
def _guard(monkeypatch, token="segredo"):
    import celia.core.minecraft_series as ms

    monkeypatch.setattr(ms, "_token", lambda: token)
    return lambda source, headers: br._speak_guard_context(SimpleNamespace(speech_source=source), SimpleNamespace(headers=headers))


def test_scene_line_with_owner_token_is_not_enforced_by_the_gpt_reviewer(monkeypatch):
    ctx = _guard(monkeypatch)("series_scene", {"x-jev-token": "segredo"})
    assert ctx.policy_mode == "observation" and ctx.trusted_source == "bridge_speak_series_scene"


def test_wrong_or_missing_token_or_other_source_keeps_the_strict_guard(monkeypatch):
    guard = _guard(monkeypatch)
    for source, headers in [("series_scene", {"x-jev-token": "outro"}), ("series_scene", {}), ("minecraft", {"x-jev-token": "segredo"}),
                            (None, {"x-jev-token": "segredo"})]:
        ctx = guard(source, headers)
        assert ctx.policy_mode == "enforce" and ctx.trusted_source == "bridge_speak_context", (source, headers)
    assert br._speak_guard_context(SimpleNamespace(speech_source="series_scene"), None).policy_mode == "enforce"


def test_unreadable_token_file_fails_closed(monkeypatch):
    import celia.core.minecraft_series as ms

    def boom():
        raise FileNotFoundError("token")

    monkeypatch.setattr(ms, "_token", boom)
    ctx = br._speak_guard_context(SimpleNamespace(speech_source="series_scene"), SimpleNamespace(headers={"x-jev-token": "x"}))
    assert ctx.policy_mode == "enforce"
