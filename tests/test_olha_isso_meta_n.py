"""Meta+N = "Célia, olha isso" (owner, 2026-10-04). Offline tests, no GPU, no Célia process.

Target behaviour: Meta+N -> Célia, pay visual attention to this -> Célia receives a
visual EVIDENCE with an honest age -> Célia decides whether it matters -> only then acts.
"""
import asyncio
import http.server
import inspect
import json
import subprocess
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from celia.core import direct_address, vision_triggers
from celia.core.vision_cache import (
    VisionCache,
    format_vision_context,
    select_vision_for_request,
    vision_cache,
)

ROOT = Path(__file__).resolve().parents[1]


# ── helpers ───────────────────────────────────────────────────────────────
def _age_note(seconds: float) -> None:
    """Make the cached analysis look `seconds` older (analysis AND re-check)."""
    with vision_cache._lock:
        note = vision_cache._note
        vision_cache._note = replace(note, ts=note.ts - seconds, verified_ts=note.verified_ts - seconds)


def _app(monkeypatch, submitted):
    from celia import bridge_routes
    from celia.celia_singleton import CeliaSingleton

    class FakeSingleton:
        async def submit_input(self, text, **kwargs):
            submitted.append((text, kwargs))
            return True

    monkeypatch.setattr(CeliaSingleton, "get_instance", staticmethod(lambda: FakeSingleton()))
    app = FastAPI()
    app.include_router(bridge_routes.init_bridge_routes(None))
    return app


def _post(app, json_body=None, client=("testclient", 50000)):
    async def go():
        transport = httpx.ASGITransport(app=app, client=client)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            return await c.post("/bridge/direct-address", json=json_body)
    return asyncio.run(go())


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    vision_cache.clear()
    direct_address.forget_unanswered()
    monkeypatch.delenv("CELIA_VISION_MAX_AGE_MS", raising=False)
    yield
    vision_cache.clear()
    direct_address.forget_unanswered()


@pytest.fixture
def gpu_must_not_run(monkeypatch):
    """Any attempt to start a new analysis from the gesture fails the test."""
    from celia.core.screen_vision import screen_vision

    def boom(*a, **k):
        raise AssertionError("o gesto Meta+N não pode iniciar análise visual (GPU)")

    monkeypatch.setattr(screen_vision, "schedule", boom)
    monkeypatch.setattr(screen_vision, "request_refresh_after_reply", boom)
    monkeypatch.setattr(screen_vision, "refresh_if_requested", boom)


# ── 1. Meta+N calls the route (command layer) ─────────────────────────────
class _Stub(http.server.BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        _Stub.seen.append((self.path, self.rfile.read(n).decode(), self.headers.get("content-type")))
        out = b'{"ok": true}'
        self.send_response(200)
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def test_shortcut_command_posts_to_route_and_logs(tmp_path):
    _Stub.seen = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log = tmp_path / "gesto.log"
    env = {
        "PATH": "/usr/bin:/bin",
        "CELIA_DIRECT_ADDRESS_URL": f"http://127.0.0.1:{srv.server_port}/bridge/direct-address",
        "CELIA_GESTO_LOG": str(log),
    }
    try:
        subprocess.run([str(ROOT / "bin/celia-olha-isso")], env=env, check=True, timeout=15)
    finally:
        srv.shutdown()
    path, body, ctype = _Stub.seen[0]
    assert path == "/bridge/direct-address"
    assert json.loads(body) == {"gesture": "look", "source": "kde-meta-n"}
    assert ctype == "application/json"
    line = log.read_text(encoding="utf-8")
    assert "http=200" in line and "/bridge/direct-address" in line and "kde-meta-n" in line


def test_shortcut_logs_even_when_celia_is_off(tmp_path):
    """http=000 in the log proves keyboard -> KDE -> command happened, bridge or not."""
    log = tmp_path / "gesto.log"
    env = {
        "PATH": "/usr/bin:/bin",
        "CELIA_DIRECT_ADDRESS_URL": "http://127.0.0.1:9/bridge/direct-address",
        "CELIA_GESTO_LOG": str(log),
    }
    subprocess.run([str(ROOT / "bin/celia-olha-isso")], env=env, check=True, timeout=15)
    assert "http=000" in log.read_text(encoding="utf-8")


def test_kde_registration_files_agree():
    installer = (ROOT / "bin/instalar-atalho-meta-n.sh").read_text(encoding="utf-8")
    assert "Meta+N" in installer and "net.local.celia-olha-isso.desktop" in installer
    assert "X-KDE-GlobalAccel-CommandShortcut=true" in installer
    assert "$DIR/bin/celia-olha-isso" in installer
    assert "setForeignShortcut" in installer  # live registration (kglobalaccel runs inside kwin_wayland)
    assert "127.0.0.1:12393/bridge/direct-address" in (ROOT / "bin/celia-olha-isso").read_text(encoding="utf-8")


# ── 2. route works without a pending line ─────────────────────────────────
def test_route_works_without_pending_line(monkeypatch, gpu_must_not_run):
    submitted = []
    r = _post(_app(monkeypatch, submitted), {"source": "kde-meta-n"})
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["gesture"] == "look"
    text, _ = submitted[0]
    assert direct_address.is_look_gesture(text)
    assert "olha isso agora" in text and "não precisa falar" in text
    assert "SEM CONTEXTO VISUAL" in text  # honest: nothing analysed yet


def test_route_does_not_need_a_json_body(monkeypatch, gpu_must_not_run):
    submitted = []
    assert _post(_app(monkeypatch, submitted)).json()["ok"] is True


# ── 3. fresh vision is attached ───────────────────────────────────────────
def test_fresh_vision_is_attached(monkeypatch, gpu_must_not_run):
    vision_cache.put("blender", "timeline com erro vermelho", "salience=0.8")
    submitted = []
    r = _post(_app(monkeypatch, submitted))
    assert r.json()["vision"] == "cache"
    text, _ = submitted[0]
    assert "CONTEXTO VISUAL POSSÍVEL" in text and "timeline com erro vermelho" in text
    assert "VELHO" not in text


# ── 4/5. stale vision: no GPU, and it produces "ask" ──────────────────────
def test_stale_vision_asks_and_never_starts_gpu(monkeypatch, gpu_must_not_run):
    vision_cache.put("blender", "timeline com erro vermelho")
    _age_note(60)
    submitted = []
    r = _post(_app(monkeypatch, submitted))
    assert r.json()["vision"] == "ask"
    assert r.json()["analysis_age_ms"] >= 60000
    text, _ = submitted[0]
    assert "CONTEXTO VISUAL VELHO" in text
    assert "a GPU não foi acionada" in text and "olhar de novo" in text


def test_gesture_policy_is_ask_even_if_env_says_refresh(monkeypatch, gpu_must_not_run):
    monkeypatch.setenv("CELIA_VISION_CACHE_POLICY", "refresh")
    vision_cache.put("blender", "x y z")
    _age_note(60)
    assert direct_address.select_for_gesture().action == "ask"


def test_word_agora_in_gesture_prompt_does_not_force_ask_on_fresh_cache():
    # "agora" is in the gesture prompt; the selector must not read it as "wants fresh".
    assert "agora" in direct_address.GESTURE_LOOK_PROMPT
    vision_cache.put("blender", "ok")
    assert direct_address.select_for_gesture().action == "cache"


def test_gesture_never_enables_tools():
    from celia.agent.agents.basic_memory_agent import BasicMemoryAgent
    from celia.agent.bridge_client import needs_tool

    text = direct_address.build_look_request(select_vision_for_request("", policy="ask"))
    assert "tela" in text  # words that normally trigger the live vision tool
    assert needs_tool(text) is None
    tools = [{"type": "function", "function": {"name": "analisar_tela"}}]
    assert BasicMemoryAgent._filter_tools_for_turn(SimpleNamespace(), tools, text, origin="owner") == []


# ── 6/7. the age is the analysis age; touch never fakes freshness ─────────
def _clocked_cache(monkeypatch, clock):
    monkeypatch.setattr("celia.core.vision_cache.time", SimpleNamespace(monotonic=lambda: clock[0]))
    return VisionCache()


def test_touch_does_not_fake_freshness(monkeypatch):
    clock = [100.0]
    cache = _clocked_cache(monkeypatch, clock)
    cache.put("editor", "tela parada")
    clock[0] = 150.0
    cache.touch()  # someone re-checked the file 1 s ago
    clock[0] = 151.0
    note, age, verified = cache.get_ages()
    assert note is not None
    assert age == 51000  # when the image was ACTUALLY analysed
    assert verified == 1000  # re-check is reported separately
    assert cache.get()[1] == 51000


def test_stale_by_analysis_age_even_after_touch(monkeypatch):
    clock = [100.0]
    cache = _clocked_cache(monkeypatch, clock)
    cache.put("editor", "tela parada")
    clock[0] = 150.0
    cache.touch()
    monkeypatch.setattr("celia.core.vision_cache.vision_cache", cache)
    selection = select_vision_for_request("", policy="ask", max_age_ms=8000)
    assert selection.action == "ask" and selection.age_ms == 50000


def test_context_shows_analysis_age_and_mentions_recheck_separately(monkeypatch):
    clock = [100.0]
    cache = _clocked_cache(monkeypatch, clock)
    cache.put("editor", "tela parada")
    clock[0] = 118.0
    cache.touch()
    clock[0] = 120.0
    monkeypatch.setattr("celia.core.vision_cache.vision_cache", cache)
    text = format_vision_context(select_vision_for_request("", policy="ask", max_age_ms=8000))
    assert "análise feita há 20 segundos" in text  # not 2
    assert "vista sem mudança há 2s" in text and "análise em si é a do tempo acima" in text


# ── 8. semantic triggers: shadow only ─────────────────────────────────────
@pytest.mark.parametrize(
    "text,category",
    [
        ("olha aí", "olhar"),
        ("Olha isso, Célia!", "olhar"),
        ("dá uma olhada nisso", "olhar"),
        ("dá uma olhada", "olhar"),
        ("e aí, Célia?", "opiniao"),
        ("o que tu acha?", "opiniao"),
        ("o que você acha disso", "opiniao"),
        ("fala sobre isso", "comentar"),
        ("comenta aí", "comentar"),
    ],
)
def test_semantic_triggers_detected(text, category):
    match = vision_triggers.detect_vision_trigger(text)
    assert (match.category if match else None) == category


@pytest.mark.parametrize(
    "text",
    ["olha, eu acho que vai dar certo", "olha", "bom dia", "o que tem pra jantar", "", "e aí, pessoal"],
)
def test_generic_lines_do_not_trigger(text):
    assert vision_triggers.detect_vision_trigger(text) is None


def test_shadow_records_and_calls_nothing(monkeypatch, tmp_path, gpu_must_not_run):
    out = tmp_path / "shadow.jsonl"
    monkeypatch.setenv("CELIA_VISION_TRIGGER_SHADOW_FILE", str(out))
    monkeypatch.setenv("CELIA_VISION_TRIGGER_SHADOW", "1")
    vision_cache.put("blender", "algo")
    match = vision_triggers.observe("olha aí", origin="owner", turn_id="t1")
    assert match is not None
    rec = json.loads(out.read_text(encoding="utf-8").strip())
    assert rec["would_trigger_vision"] is True and rec["vision_called"] is False
    assert rec["category"] == "olhar" and rec["turn_id"] == "t1"
    assert rec["cache"]["action"] == "cache"
    assert vision_triggers.observe("bom dia", origin="owner") is None
    assert len(out.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_shadow_skips_the_gesture_turn_and_can_be_disabled(monkeypatch, tmp_path):
    out = tmp_path / "shadow.jsonl"
    monkeypatch.setenv("CELIA_VISION_TRIGGER_SHADOW_FILE", str(out))
    assert vision_triggers.observe(direct_address.GESTURE_LOOK_PROMPT + " olha aí") is None
    monkeypatch.setenv("CELIA_VISION_TRIGGER_SHADOW", "0")
    assert vision_triggers.observe("olha aí") is None
    assert not out.exists()


def test_shadow_is_wired_in_agent_and_has_no_vision_call():
    agent = (ROOT / "src/celia/agent/agents/basic_memory_agent.py").read_text(encoding="utf-8")
    assert "vision_triggers.observe(" in agent
    mod = inspect.getsource(vision_triggers)
    for forbidden in ("screen_vision", ".schedule(", "analisar_tela", "gpu_scheduler"):
        assert forbidden not in mod


# ── 9. the frame is evidence, never authority ─────────────────────────────
def test_frame_text_is_not_authoritative():
    vision_cache.put("blender", "timeline com erro vermelho")
    fresh = format_vision_context(select_vision_for_request("", policy="cache"))
    assert "CONTEXTO VISUAL POSSÍVEL" in fresh
    assert "pode estar desatualizada" in fresh
    assert "Use-a apenas se tiver relação com o contexto atual" in fresh
    assert "Não trate como estado atual garantido" in fresh
    _age_note(60)
    stale = format_vision_context(select_vision_for_request("", policy="ask", max_age_ms=8000))
    none = format_vision_context(
        SimpleNamespace(note=None, age_ms=None, verified_age_ms=None, action="ask")
    )
    for text in (fresh, stale, none):
        assert "AUTORITATIV" not in text.upper()
        assert "ja resolvida" not in text and "já resolvida" not in text


def test_agent_no_longer_injects_authoritative_vision():
    agent = (ROOT / "src/celia/agent/agents/basic_memory_agent.py").read_text(encoding="utf-8")
    assert "VISAO CACHE AUTORITATIVA" not in agent
    assert "[VISAO CACHE]" not in agent
    assert agent.count("format_vision_context(vision_selection)") == 2


# ── 10. the old "last unanswered line" path does not control Meta+N ───────
def test_pending_line_does_not_control_the_gesture(monkeypatch, gpu_must_not_run):
    direct_address.remember_unanswered("Que site é esse", "u9")
    submitted = []
    r = _post(_app(monkeypatch, submitted))
    assert r.json()["ok"] is True
    text, kwargs = submitted[0]
    assert "Que site é esse" not in text  # not resent
    assert kwargs.get("turn_id") is None
    taken = direct_address.take_unanswered()  # still there for other purposes
    assert taken is not None and taken["text"] == "Que site é esse"


def test_route_source_has_no_unanswered_or_refresh_path():
    src = inspect.getsource(__import__("celia.bridge_routes", fromlist=["x"]))
    start = src.index('@router.post("/direct-address")')
    end = src.index('@router.post("/behavior/profile")')
    route = src[start:end]
    for forbidden in ("take_unanswered", "request_refresh_after_reply", "last_analysis", "screen_vision"):
        assert forbidden not in route
    assert "select_for_gesture" in route and "build_look_request" in route


def test_gesture_is_loopback_only(monkeypatch):
    r = _post(_app(monkeypatch, []), client=("192.168.0.121", 50000))
    assert r.status_code == 403
