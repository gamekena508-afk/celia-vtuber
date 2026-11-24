import asyncio
import base64
import io
import wave
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from celia.asr.asr_interface import ASRInterface, transcription_snapshot
from celia.config_manager.profile_v6 import preset, validated_settings
from celia.core import settings_preferences as prefs
from celia.observability_routes import init_observability_routes
from celia.utils.stream_audio import prepare_audio_payload

@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(prefs, "STATE_PATH", tmp_path / "preferences.json")

def test_model_catalog_save_and_loopback(tmp_path):
    engine = SimpleNamespace(_tool_llm=SimpleNamespace(_links=[
        SimpleNamespace(model="model-a", local_gpu_job=False),
        SimpleNamespace(model="model-b", local_gpu_job=False),
        SimpleNamespace(model="local-persona", local_gpu_job=True)]))
    app = FastAPI()
    app.include_router(init_observability_routes(SimpleNamespace(agent_engine=engine)))
    c = TestClient(app)
    path = "/debug/observability/settings-preferences"
    assert c.get(path).json()["jev_models"] == ["model-a", "model-b"]
    r = c.put(path, json={"jev_model":"model-b", "volume":50, "discord_reply_output":"window"})
    assert r.status_code == 200
    assert prefs.get_preferences()["jev_model"] == "model-b"
    assert c.put(path, json={"jev_model":"invented"}).status_code == 422
    assert c.put(path, json={"volume":101}).status_code == 422
    assert c.put(path, json={"volume":20}, headers={"origin":"https://external.invalid"}).status_code == 403
    assert c.get(path).json()["preferences"]["volume"] == 50
    with TestClient(app, client=("203.0.113.2", 12345)) as remote:
        assert remote.put(path, json={"volume":0}).status_code == 403

def test_destination_only_applies_to_call():
    assert prefs.discord_reply_destination("discord_call") == "discord"
    assert prefs.discord_reply_destination("discord_call_greeting") == "discord"
    assert prefs.discord_reply_destination("discord_chat") == "window"
    prefs.update_preferences({"discord_reply_output":"window"})
    assert prefs.discord_reply_destination("discord_call") == "window"
    prefs.update_preferences({"discord_reply_output":"discord"})
    assert prefs.discord_reply_destination("browser") == "window"

def test_volume_controls_payload_without_modifying_original(tmp_path):
    p = tmp_path / "tone.wav"
    data = np.full(3200, 10000, dtype="<i2").tobytes()
    with wave.open(str(p),"wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000); out.writeframes(data)
    original = p.read_bytes()
    def samples():
        result=prepare_audio_payload(str(p))
        with wave.open(io.BytesIO(base64.b64decode(result["audio"])),"rb") as inp:
            return np.frombuffer(inp.readframes(inp.getnframes()), dtype="<i2")
    assert np.max(samples()) == 10000
    prefs.update_preferences({"volume":50})
    assert 4999 <= np.max(samples()) <= 5001
    prefs.update_preferences({"volume":0})
    assert not np.any(samples())
    assert p.read_bytes() == original

def test_contextual_prompt_schema_is_backward_compatible():
    old = preset("chat").model_dump()
    old.pop("contextual_prompt")
    assert validated_settings(old).contextual_prompt == ""
    old["contextual_prompt"] = "Be brief."
    assert validated_settings(old).contextual_prompt == "Be brief."
    old["contextual_prompt"] = "x" * 3001
    with pytest.raises(ValueError):
        validated_settings(old)

def test_contextual_prompt_persists_and_changes_only_selected_profile(tmp_path):
    from celia.core.continuous_behavior import ContinuousBehaviorRuntime
    p = tmp_path / "profiles.json"
    rt = ContinuousBehaviorRuntime(p)
    cfg = rt.profile_settings("chat")
    cfg["settings"]["contextual_prompt"] = "Use short sentences."
    rt.update_profile_settings("chat", cfg["settings"], expected_revision=cfg["revision"])
    assert rt.profile_settings("chat")["settings"]["contextual_prompt"] == "Use short sentences."
    assert rt.profile_settings("cohost")["settings"]["contextual_prompt"] == ""
    restored = ContinuousBehaviorRuntime(p)
    assert restored.profile_settings("chat")["settings"]["contextual_prompt"] == "Use short sentences."
    assert "Use short sentences." in restored.get_profile_prompt()

def test_jev_preference_reorders_real_chain_and_keeps_fallback(tmp_path):
    from celia.agent.stateless_llm.chained_llm import ChainedLLM
    from celia.core.quota_ledger import QuotaLedger
    cfg = {"gateway":{"base_url":"http://example.invalid/v1","api_key":"fixture"},
           "jev":{"mode":"tool","chain":["a","b"],"ttft_sec":0.2,"total_sec":1.0}}
    chain = ChainedLLM("jev",cfg,QuotaLedger({},tmp_path/"quota"))
    calls=[]
    async def first(*args,**kwargs):
        calls.append("a")
        yield "fallback"
    async def second(*args,**kwargs):
        calls.append("b")
        if False:
            yield ""
    chain._links[0].llm.chat_completion=first
    chain._links[1].llm.chat_completion=second
    prefs.update_preferences({"jev_model":"b"},allowed_models=["a","b"])
    async def run():
        result=[x async for x in chain.chat_completion([{"role":"user","content":"hi"}])]
        assert result==["fallback"]
    asyncio.run(run())
    assert calls == ["b","a"]

def test_speed_changes_duration_but_not_original(tmp_path):
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg unavailable")
    p = tmp_path / "tone.wav"
    wave_data = (np.sin(np.arange(16000) * 2 * np.pi * 440 / 16000) * 10000).astype("<i2")
    with wave.open(str(p),"wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000); out.writeframes(wave_data.tobytes())
    original = p.read_bytes()
    prefs.update_preferences({"playback_speed":1.25})
    result=prepare_audio_payload(str(p))
    with wave.open(io.BytesIO(base64.b64decode(result["audio"])),"rb") as inp:
        assert 11500 < inp.getnframes() < 14000
    assert p.read_bytes()==original

def test_asr_counter_tracks_jobs_without_audio_capture():
    class FakeASR(ASRInterface):
        def transcribe_np(self, audio):
            assert transcription_snapshot()["running"] >= 1
            return "mock"
    async def run():
        assert await FakeASR().async_transcribe_np(np.zeros(16, dtype=np.float32)) == "mock"
        assert transcription_snapshot()["running"] == 0
    asyncio.run(run())
