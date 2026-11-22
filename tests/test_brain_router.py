import asyncio
import io
import json

from celia.core.brain_router import BrainRouter


def _parser(raw):
    try:
        return json.loads(raw)
    except Exception:
        return None


def _response(obj):
    payload = {"choices": [{"message": {"content": json.dumps(obj)}}]}
    return io.BytesIO(json.dumps(payload).encode())


def test_default_nano_uses_minimal_reasoning_and_project_prompt_only(monkeypatch):
    router = BrainRouter()
    monkeypatch.setenv("CELIA_BRAIN_GROK_ENABLED", "0")
    monkeypatch.setenv("CELIA_BRAIN_GEMINI_ENABLED", "0")
    captured = {}

    def urlopen(req, timeout=None):
        del timeout
        captured.update(json.loads(req.data))
        return _response({"ok": True})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    result = asyncio.run(router.route("PROJECT_ONLY_PROMPT", _parser, max_tokens=40))
    assert result and result.provider == "codex_luna"
    assert captured["model"] == "openrouter/openai/gpt-5-nano"
    assert captured["reasoning_effort"] == "minimal"
    assert "service_tier" not in captured
    assert "temperature" not in captured
    assert captured["max_tokens"] == 256
    assert captured["messages"] == [{"role": "user", "content": "PROJECT_ONLY_PROMPT"}]


def test_openrouter_nano_profiles_use_working_request_shape(monkeypatch):
    monkeypatch.setenv("CELIA_BRAIN_GROK_ENABLED", "0")
    monkeypatch.setenv("CELIA_BRAIN_GEMINI_ENABLED", "0")
    for model, effort, cap in (
        ("openrouter/openai/gpt-5-nano", "minimal", 256),
        ("openrouter/openai/gpt-4.1-nano", None, 128),
    ):
        monkeypatch.setenv("CELIA_BRAIN_LUNA_MODEL", model)
        captured = {}

        def urlopen(req, timeout=None):
            del timeout
            captured.update(json.loads(req.data))
            return _response({"ok": True})

        monkeypatch.setattr("urllib.request.urlopen", urlopen)
        result = asyncio.run(BrainRouter().route("estado", _parser, max_tokens=128))
        assert result and result.model == model
        assert captured["model"] == model
        assert captured["max_tokens"] == cap
        assert captured.get("reasoning_effort") == effort
        assert "service_tier" not in captured
        assert ("temperature" not in captured) if effort else (captured["temperature"] == 0.1)


def test_grok45_uses_low_reasoning(monkeypatch):
    router = BrainRouter()
    monkeypatch.setenv("CELIA_BRAIN_LUNA_ENABLED", "0")
    monkeypatch.setenv("CELIA_BRAIN_GEMINI_ENABLED", "0")
    captured = {}

    def urlopen(req, timeout=None):
        del timeout
        captured.update(json.loads(req.data))
        return _response({"ok": True})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    result = asyncio.run(router.route("estado", _parser, max_tokens=40))
    assert result and result.provider == "grok45"
    assert captured["model"] == "grok-cli/grok-4.5-low"
    assert captured["reasoning_effort"] == "low"
    assert "service_tier" not in captured


def test_default_uses_one_request_before_fallback(monkeypatch):
    router = BrainRouter()
    monkeypatch.delenv("CELIA_BRAIN_RACE_REMOTE", raising=False)
    monkeypatch.delenv("CELIA_BRAIN_ADAPTIVE", raising=False)
    seen = []

    async def fake_request(provider, *, prompt, image, max_tokens):
        del prompt, image, max_tokens
        seen.append(provider.name)
        return json.dumps({"ok": True}), 10

    monkeypatch.setattr(router, "_request", fake_request)
    result = asyncio.run(router.route("x", _parser))
    assert result and result.provider == "codex_luna"
    assert seen == ["codex_luna"]
    assert router.debug_snapshot()["race_policy"] == "sequential_first_valid"


def test_vision_sends_only_requested_frame(monkeypatch):
    router = BrainRouter()
    monkeypatch.setenv("CELIA_BRAIN_GROK_ENABLED", "0")
    monkeypatch.setenv("CELIA_BRAIN_GEMINI_ENABLED", "0")
    captured = {}

    def urlopen(req, timeout=None):
        del timeout
        captured.update(json.loads(req.data))
        return _response({"summary": "editor", "topic": "app"})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    result = asyncio.run(router.route(
        "vision prompt", _parser, image="data:image/jpeg;base64,AA==", max_tokens=50
    ))
    assert result and result.provider == "codex_luna"
    assert result.model == "codex/gpt-5.6-luna"
    assert captured["model"] == "codex/gpt-5.6-luna"
    assert captured["max_tokens"] == 50
    content = captured["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "vision prompt"}
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,AA=="


def test_failed_provider_enters_cooldown_and_falls_through(monkeypatch):
    router = BrainRouter()
    monkeypatch.setenv("CELIA_BRAIN_GEMINI_ENABLED", "0")
    monkeypatch.setenv("CELIA_BRAIN_ADAPTIVE", "0")
    monkeypatch.setenv("CELIA_BRAIN_RACE_REMOTE", "0")
    calls = []

    def urlopen(req, timeout=None):
        del timeout
        body = json.loads(req.data)
        calls.append(body["model"])
        if body["model"] == "openrouter/openai/gpt-5-nano":
            err = Exception("missing")
            err.code = 404
            raise err
        return _response({"ok": True})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    result = asyncio.run(router.route("x", _parser))
    assert result and result.provider == "grok45"
    snap = router.debug_snapshot()["providers"]["codex_luna"]
    assert snap["cooldown_ms"] > 0
    assert calls == ["openrouter/openai/gpt-5-nano", "grok-cli/grok-4.5-low"]


def test_luna_grok_and_gemini_are_peer_raced(monkeypatch):
    import time

    router = BrainRouter()
    monkeypatch.setenv("CELIA_BRAIN_ADAPTIVE", "0")
    monkeypatch.setenv("CELIA_BRAIN_RACE_REMOTE", "1")
    monkeypatch.setenv("CELIA_BRAIN_PROVIDER_ORDER", "codex_luna,grok45,gemini_9router")

    async def fake_request(provider, *, prompt, image, max_tokens):
        del prompt, image, max_tokens
        delays = {"codex_luna": 0.12, "grok45": 0.08, "gemini_9router": 0.02}
        await asyncio.sleep(delays[provider.name])
        return json.dumps({"winner": provider.name}), int(delays[provider.name] * 1000)

    monkeypatch.setattr(router, "_request", fake_request)
    started = time.perf_counter()
    result = asyncio.run(router.route("x", _parser))
    elapsed = time.perf_counter() - started

    assert result and result.provider == "gemini_9router"
    assert result.value == {"winner": "gemini_9router"}
    assert elapsed < 0.09

def test_sequential_mode_uses_adaptive_latency_across_all_peers(monkeypatch):
    router = BrainRouter()
    monkeypatch.setenv("CELIA_BRAIN_RACE_REMOTE", "0")
    monkeypatch.setenv("CELIA_BRAIN_ADAPTIVE", "1")
    router._state_for("codex_luna")["ema_latency_ms"] = 3000.0
    router._state_for("grok45")["ema_latency_ms"] = 1200.0
    router._state_for("gemini_9router")["ema_latency_ms"] = 400.0
    seen = []

    async def fake_request(provider, *, prompt, image, max_tokens):
        del prompt, image, max_tokens
        seen.append(provider.name)
        return json.dumps({"ok": True}), 10

    monkeypatch.setattr(router, "_request", fake_request)
    result = asyncio.run(router.route("x", _parser))
    assert result and result.provider == "gemini_9router"
    assert seen == ["gemini_9router"]
