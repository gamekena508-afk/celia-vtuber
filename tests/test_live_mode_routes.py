"""Rotas /bridge/live/*, controlador do leitor e portões de isolamento. Sem TikTok, sem rede externa."""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from celia.live_mode import gate
from celia.live_mode.manager import LiveModeManager
from celia.live_mode.reader_ctl import ReaderController
from celia.live_mode.routes import init_live_routes
from celia.live_mode.settings import SettingsStore


class FakeProc:
    def __init__(self):
        self.pid, self.returncode, self.terminated = 4242, None, False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated, self.returncode = True, 0

    def kill(self):
        self.returncode = -9


def build(tmp_path, python_exists=True):
    store = SettingsStore(tmp_path / "s.json")
    store.update({"gift_reactions_enabled": False, "idle_host_enabled": False})
    mgr = LiveModeManager(store, state_dir=tmp_path)
    spawned = []
    fake_py = tmp_path / "python.exe"
    fake_script = tmp_path / "reader.py"
    if python_exists:
        fake_py.write_text("x"); fake_script.write_text("x")

    def spawn(env):
        spawned.append(env)
        return FakeProc()
    mgr.reader = ReaderController(mgr, python=fake_py, script=fake_script, state_dir=tmp_path, spawn=spawn)
    app = FastAPI()
    app.include_router(init_live_routes(mgr))
    return TestClient(app), mgr, spawned


def test_status_starts_off_and_reader_stopped(tmp_path):
    client, mgr, spawned = build(tmp_path)
    s = client.get("/bridge/live/status").json()
    assert s["mode"] == "off" and s["reader"]["state"] == "stopped" and s["isolation"]["mic_and_typed_input_blocked"] is False
    assert spawned == []                                        # nada sobe sozinho


@pytest.mark.asyncio
async def test_prepared_celia_wav_is_reused_without_early_publication(monkeypatch):
    import json
    import httpx
    from celia.live_mode.manager import Job
    from celia.live_mode.routes import make_celia_reply
    original=httpx.AsyncClient
    calls=[]
    def serve(request):
        body=json.loads(request.content)
        calls.append((request.url.path,body))
        if request.url.path=='/bridge/process':
            assert body['prepare_only'] is True and body['synthesize_audio'] is True
            return httpx.Response(200,json={'response':'Oi, Ana!','audio_paths':['prepared.wav'],'emotion':'happy'})
        assert request.url.path=='/bridge/speak' and body['audio_path']=='prepared.wav'
        assert body['record_after_playback'] is True
        return httpx.Response(200,json={'ok':True,'speech_id':'confirmed','playback_status':'ended'})
    transport=httpx.MockTransport(serve)
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=transport,**kw))
    callback=make_celia_reply(lambda:'http://test')
    job=Job(id='j1',character='celia',reason='default',rank=None,author='Ana',user_id='ana',
            display_id='ana',text='Oi!',message_id='real-1',origin='real',created=0)
    job.prepared_result=await callback.prepare(job)
    assert len(calls)==1 and job.prepared_result['ok']
    result=await callback(job)
    assert result['ok'] and result['speech_id']=='confirmed'
    assert [path for path,_ in calls]==['/bridge/process','/bridge/speak']


def test_simulation_requires_test_mode_and_is_labelled(tmp_path):
    client, mgr, _ = build(tmp_path)
    r = client.post("/bridge/live/simulate/chat", json={"user_id": "u1", "text": "oi"})
    assert r.status_code == 409
    assert client.post("/bridge/live/enable", json={"mode": "test"}).json()["mode"] == "test"
    ok = client.post("/bridge/live/simulate/chat", json={"user_id": "u1", "nickname": "Ana", "text": "oi gente"}).json()
    assert ok["ok"] and ok["job"]["origin"] == "simulated" and ok["job"]["author"] == "Ana"
    g = client.post("/bridge/live/simulate/gift", json={"user_id": "u1", "diamond_count": 10}).json()
    assert g["ok"] and g["seats"][0]["user_id"] == "u1"
    assert client.post("/bridge/live/disable").json()["mode"] == "off"


def test_reader_never_starts_without_authorization_or_room(tmp_path):
    client, mgr, spawned = build(tmp_path)
    r = client.post("/bridge/live/reader/start", json={"room_id": "7123456789012345678"})
    assert r.status_code == 400 and "autorizacao" in r.json()["detail"]
    r = client.post("/bridge/live/reader/start", json={"authorized": True})
    assert r.status_code == 400 and "room_id" in r.json()["detail"]
    r = client.post("/bridge/live/reader/start", json={"authorized": True, "room_id": "123"})
    assert r.status_code == 400
    assert spawned == [] and mgr.mode == "off"


def test_reader_start_is_idempotent_and_stop_clears_everything(tmp_path):
    client, mgr, spawned = build(tmp_path)
    body = {"authorized": True, "room_id": "7123456789012345678"}
    first = client.post("/bridge/live/reader/start", json=body).json()
    second = client.post("/bridge/live/reader/start", json=body).json()
    assert len(spawned) == 1 and first["pid"] == second["pid"] == 4242      # sem processo duplicado
    assert mgr.mode == "live" and mgr.room_id == "7123456789012345678"
    env = spawned[0]
    assert env["LIVE_ROOM_ID"] == "7123456789012345678" and env["LIVE_READER_TOKEN"]
    assert "EULER_API_KEY" not in env and not any("KEY" in k and k != "EULER_KEY_FILE" for k in env)  # só o caminho, nunca o valor
    s = client.post("/bridge/live/disable").json()
    assert s["mode"] == "off" and mgr.reader.status()["state"] == "stopped" and mgr.reader.proc is None


def test_reader_missing_environment_is_reported_not_faked(tmp_path):
    client, mgr, spawned = build(tmp_path, python_exists=False)
    r = client.post("/bridge/live/reader/start", json={"authorized": True, "room_id": "7123456789012345678"})
    assert r.status_code == 400 and "leitor_nao_instalado" in r.json()["detail"] and spawned == []


def test_handle_discovery_requires_confirmation_and_is_not_repeated(tmp_path, monkeypatch):
    import subprocess
    client, mgr, spawned = build(tmp_path)
    calls = []
    def discovery(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout='{"ok":true,"room_id":"7123456789012345678"}')
    monkeypatch.setattr(subprocess, "run", discovery)
    body = {"authorized": True, "handle": "@example"}
    assert client.post("/bridge/live/reader/start", json=body).status_code == 400
    assert not calls and not spawned
    with client:
        body["live_confirmed"] = True
        assert client.post("/bridge/live/reader/start", json=body).status_code == 200
        assert client.post("/bridge/live/reader/start", json=body).status_code == 200
        assert len(calls) == len(spawned) == 1
        assert mgr.room_id == "7123456789012345678"
        client.post("/bridge/live/disable")


def test_explicit_audio_activation_takes_lease_from_silent_old_tab(tmp_path):
    client, mgr, _ = build(tmp_path)
    assert client.post("/bridge/live/audio/claim", json={"client": "unknown"}).status_code == 400
    client.get("/bridge/live/stream?client=oldtab&wait=0")
    client.get("/bridge/live/stream?client=newtab&wait=0")
    assert mgr._lease_holder() is None
    assert mgr.snapshot()["audio_lease_claimed"] is False
    assert client.post("/bridge/live/audio/claim", json={"client": "newtab"}).json()["ok"]
    assert mgr._lease_holder() == "newtab"


def test_same_old_room_needs_confirmation(tmp_path):
    client, mgr, spawned = build(tmp_path)
    (tmp_path / "last_session.json").write_text('{"room_id": "7123456789012345678", "ended_at": 1}')
    body = {"authorized": True, "room_id": "7123456789012345678"}
    assert "sala_repetida" in client.post("/bridge/live/reader/start", json=body).json()["detail"]
    assert client.post("/bridge/live/reader/start", json={**body, "confirm_same_room": True}).status_code == 200
    client.post("/bridge/live/disable")


def test_real_events_need_the_reader_token_and_cannot_forge_origin(tmp_path):
    client, mgr, spawned = build(tmp_path)
    ev = {"type": "chat", "msg_id": "m1", "user_id": "u9", "nickname": "Zé", "text": "olá live"}
    assert client.post("/bridge/live/event", json=ev).status_code == 401             # sem leitor, sem token
    client.post("/bridge/live/reader/start", json={"authorized": True, "room_id": "7123456789012345678"})
    token = spawned[0]["LIVE_READER_TOKEN"]
    assert client.post("/bridge/live/event", json=ev, headers={"x-live-token": "errado"}).status_code == 401
    r = client.post("/bridge/live/event", json={**ev, "origin": "simulated"}, headers={"x-live-token": token}).json()
    assert r["ok"] and r["job"]["origin"] == "real"                                   # origem sempre vira "real"
    assert client.post("/bridge/live/event", json={"type": "status", "state": "connected", "message": "ConnectEvent"},
                       headers={"x-live-token": token}).json()["ok"]
    assert mgr.reader.status()["state"] == "connected"
    client.post("/bridge/live/disable")
    assert client.post("/bridge/live/event", json=ev, headers={"x-live-token": token}).status_code == 401  # token morreu


def test_settings_endpoint_persists_and_validates(tmp_path):
    client, mgr, _ = build(tmp_path)
    new = client.post("/bridge/live/settings", json={"volumes": {"ursinho": 0.3}, "queue_max": 5000}).json()
    assert new["volumes"]["ursinho"] == 0.3 and new["queue_max"] == 100
    assert SettingsStore(tmp_path / "s.json").get()["volumes"]["ursinho"] == 0.3


def test_audio_route_rejects_traversal(tmp_path):
    client, *_ = build(tmp_path)
    assert client.get("/bridge/live/audio/..%2Fsecret.wav").status_code in (400, 404)
    assert client.get("/bridge/live/audio/nao-existe.wav").status_code == 404


def test_gate_functions_follow_manager_state(tmp_path):
    mgr = LiveModeManager(SettingsStore(tmp_path / "s.json"), state_dir=tmp_path)
    gate._MANAGER = mgr
    try:
        assert gate.owner_input_blocked() is False and gate.submit_input_allowed("microphone") is True
        assert gate.process_source_allowed("discord") is True
        asyncio.run(mgr.enable("test"))
        assert gate.owner_input_blocked() is True
        assert gate.submit_input_allowed("microphone") is False and gate.submit_input_allowed("owner") is False
        assert gate.submit_input_allowed("tiktok_live_chat") is True
        assert gate.process_source_allowed("discord") is False and gate.process_source_allowed("live_test") is True
        gate.note_mic_blocked()
        assert mgr.counters["mic_blocked"] == 1
        asyncio.run(mgr.disable())
        assert gate.owner_input_blocked() is False and gate.submit_input_allowed("microphone") is True
    finally:
        gate._MANAGER = None


def test_reader_crash_is_reported_and_live_mode_keeps_running(tmp_path):
    """Recuperação: se o processo do leitor cair, o estado diz 'error' e o módulo continua; religar não duplica."""
    client, mgr, spawned = build(tmp_path)
    body = {"authorized": True, "room_id": "7123456789012345678"}
    client.post("/bridge/live/reader/start", json=body)
    proc = mgr.reader.proc
    proc.returncode = 1                                     # o leitor morreu sozinho
    with TestClient(client.app) as live:                    # um loop único para o monitor rodar
        import time
        time.sleep(1.3)
        st = live.get("/bridge/live/status").json()
    assert st["mode"] == "live" and st["reader"]["running"] is False
    assert mgr.reader.state in ("error", "exited") and mgr.reader.exit_code == 1
    again = client.post("/bridge/live/reader/start", json=body).json()   # recuperação: sobe UM novo processo
    assert len(spawned) == 2 and again["running"] is True
    client.post("/bridge/live/disable")


def test_soak_memory_stays_bounded(tmp_path):
    """Execução prolongada acelerada: milhares de eventos e nenhuma estrutura cresce sem limite."""
    store = SettingsStore(tmp_path / "s.json")
    store.update({"per_user_interval_s": 0, "per_user_max_per_minute": 60, "queue_max": 12})
    mgr = LiveModeManager(store, state_dir=tmp_path)

    async def run():
        await mgr.enable("test")
        mgr._worker.cancel()                                # sem consumir: a fila é que precisa se limitar
        await asyncio.gather(mgr._worker, return_exceptions=True)
        for i in range(6000):
            await mgr.ingest({"type": "chat", "origin": "simulated", "msg_id": f"c{i}", "user_id": f"u{i % 700}",
                              "nickname": "N", "text": f"mensagem {i}"})
            if i % 3 == 0:
                await mgr.ingest({"type": "gift", "origin": "simulated", "msg_id": f"g{i}", "user_id": f"u{i % 700}",
                                  "gift_id": "rose", "gift_name": "Rose", "diamond_count": 1 + i % 50,
                                  "repeat_count": 1, "repeat_end": True, "streakable": False, "group_id": "g"})
        await mgr.shutdown()
    asyncio.run(run())
    assert len(mgr._queue) == 0 or len(mgr._queue) <= 12
    assert len(mgr._seen_msgs) <= 5000 and len(mgr._history) <= 40
    assert len(mgr._user_times) <= 700 and len(mgr._events) <= 200
    ledger = mgr._ledgers["simulated"]
    assert len(ledger._seen) <= 20000 and len(ledger.records) <= 50000
    assert mgr.counters["chat_accepted"] > 0
