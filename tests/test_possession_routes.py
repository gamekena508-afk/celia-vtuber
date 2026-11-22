"""Possession door v2: loopback only, one lease, NO say, the body acts through the daemon's guards, controls are central, persona keeps the conversation.
Fakes stand in for the daemon and the screen; the decision logic under test is the real one."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from celia.core import possession as possession_module  # noqa: E402
from celia.core import possession_io as io  # noqa: E402
from celia import possession_routes as routes  # noqa: E402


class Fakes:
    def __init__(self):
        self.daemon, self.cancelled = [], []
        self.owner = {"x": 10.0, "y": 64.0, "z": 5.0}
        self.series, self.epoch = "GAMEPLAY", 12
        self.act_result = None
        self.pos = {"x": 0.0, "y": 64.0, "z": 0.0}
        self.refuse = None  # e.g. "world_epoch_changed" / "series_holds_the_body"

    def daemon_call(self, method, path, payload=None, timeout=8.0):
        self.daemon.append((method, path, payload))
        if path == "/possession/perceive":
            return {"ok": True, "series": self.series, "epoch": self.epoch, "world_id": "w", "daylight_state": {"cycle": False}, "tick_state": {"frozen": True},
                    "perception": {"owner_position": self.owner, "position": dict(self.pos), "owner_distance": 11.2, "inventory": {}}}
        if path == "/body/act":
            if self.refuse:
                return {"ok": False, "error": "http_409", "detail": self.refuse}
            if self.act_result is not None:
                return self.act_result
            self.pos = {"x": 8.0, "y": 64.0, "z": 4.0}
            return {"ok": True, "status": "succeeded", "error": "", "perception": {"position": dict(self.pos), "owner_distance": 2.4, "inventory": {}},
                    "timing_ms": {"validate": 0.4, "body_act": 12.0}, "world_epoch": self.epoch}
        return {"ok": True, "status": "succeeded"}

    def capture_screen(self, root=None, *, active_window=False):
        return {"ok": True, "path": "/x/screen.jpg", "bytes": 100, "sha256": "ab", "perspective": "owner_screen"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    fakes = Fakes()
    monkeypatch.setattr(possession_module, "_instance", possession_module.Possession(tmp_path / "poss"))
    monkeypatch.setattr(routes, "_trace", None)
    monkeypatch.setattr(io, "daemon_call", fakes.daemon_call)
    monkeypatch.setattr(io, "capture_screen", fakes.capture_screen)
    monkeypatch.setattr(io, "persona", lambda: {"ok": True, "persona_prompt": "Você é a Célia", "chars": 15})
    monkeypatch.setattr(io, "cancel_playback", lambda sid: fakes.cancelled.append(sid) or {"ok": True})
    monkeypatch.setattr(io, "current_speech_id", lambda: "sp-1")
    app = FastAPI()
    app.include_router(routes.init_possession_routes())
    return app, fakes, possession_module.get_possession()


def H(lease):
    return {"X-Possession-Lease": lease}


def start(client, controller="claude"):
    return client.post("/bridge/possession/start", json={"controller": controller}).json()


def say_owner(text, **kw):
    async def go():
        consumed = await routes.mirror_owner_text(text, source=kw.get("source", "microphone"), authenticated_owner=kw.get("auth", True),
                                                  self_echo=kw.get("echo", False), utterance_id=kw.get("uid"))
        await asyncio.sleep(0.05)  # let the urgent / stop side effects run
        return consumed
    return asyncio.run(go())


def acts(fakes):
    return [c for c in fakes.daemon if c[1] == "/body/act"]


def test_the_door_is_loopback_only(env):
    app, _, _ = env
    outside = TestClient(app, client=("8.8.8.8", 4444))
    for method, path in (("post", "/start"), ("get", "/next"), ("post", "/act"), ("post", "/sheet"), ("get", "/sheet"), ("get", "/state"), ("post", "/shot"),
                         ("post", "/stop"), ("get", "/persona"), ("get", "/stats"), ("post", "/quest"), ("post", "/log")):
        assert getattr(outside, method)("/bridge/possession" + path).status_code == 403, path


def test_the_agent_has_no_authority_over_speech_there_is_no_say_endpoint(env):
    app, fakes, _ = env
    client = TestClient(app)
    lease = start(client)["lease"]
    for method, path in (("post", "/say"), ("post", "/speak"), ("post", "/tts")):
        assert getattr(client, method)("/bridge/possession" + path, headers=H(lease), json={"text": "oi"}).status_code in (404, 405), path
    assert not any("speak" in c[1] for c in fakes.daemon)
    assert "/say" not in {r.path.removeprefix("/bridge/possession") for r in routes.init_possession_routes().routes}
    assert not hasattr(io, "speak"), "the adapter that could voice the agent's text does not exist any more"


def test_one_lease_the_body_is_held_and_the_owner_can_always_stop(env):
    app, fakes, poss = env
    client = TestClient(app)
    first = start(client)
    assert first["body"] == "held" and ("POST", "/possession/hold", {"on": True}) in fakes.daemon and first["world_epoch"] == 12
    other = client.post("/bridge/possession/start", json={"controller": "codex"})
    assert other.status_code == 409 and other.json()["detail"]["code"] == "already_possessed"
    assert client.get("/bridge/possession/next?wait=0").status_code == 403 and client.get("/bridge/possession/next?wait=0", headers=H("wrong")).status_code == 403
    stopped = client.post("/bridge/possession/stop").json()
    assert stopped["stopped"] and ("POST", "/possession/hold", {"on": False}) in fakes.daemon and not poss.active()
    assert client.post("/bridge/possession/act", headers=H(first["lease"]), json={"shortcut": "stop"}).status_code == 409


def test_starting_a_possession_never_releases_the_prepared_world_scenarios_1_and_2(env):
    app, fakes, poss = env
    fakes.series = "INTRO_READY"
    fakes.refuse = "series_holds_the_body"
    client = TestClient(app)
    started = start(client)
    assert started["series_state"] == "INTRO_READY" and started["body"] == "held"
    assert not any(c[1].startswith("/series") or "gameplay" in json.dumps(c[2] or {}) for c in fakes.daemon), "no series command, no play, nothing that frees the world"
    refused = client.post("/bridge/possession/act", headers=H(started["lease"]), json={"shortcut": "come_here"})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "series_holds_the_body"
    assert poss.active(), "refusing the body is not the end of the possession: it observes until the owner releases the game"
    assert client.get("/bridge/possession/state", headers=H(started["lease"])).json()["series"] == "INTRO_READY"


def test_the_persona_keeps_the_conversation_and_the_agent_gets_the_same_words_as_an_event(env):
    app, _, poss = env
    client = TestClient(app)
    lease = start(client)["lease"]
    assert say_owner("Célia, vem aqui do meu lado pra aparecer", uid="u1") is False, "not consumed: the persona still answers"
    got = client.get("/bridge/possession/next?since=0&wait=1&coalesce_ms=0", headers=H(lease)).json()
    assert got["events"][0]["type"] == "owner_speech" and "vem aqui" in got["events"][0]["text"] and got["events"][0]["utterance_id"] == "u1"
    assert say_owner("vem aqui", auth=False) is False and say_owner("vem aqui", echo=True) is False
    assert client.get(f"/bridge/possession/next?since={got['last_seq']}&wait=0&coalesce_ms=0", headers=H(lease)).json()["events"] == []


def test_the_agent_commands_the_body_and_the_effect_is_confirmed_by_position_scenario_2(env):
    app, fakes, poss = env
    client = TestClient(app)
    lease = start(client)["lease"]
    client.get("/bridge/possession/state", headers=H(lease))  # the perception BEFORE
    res = client.post("/bridge/possession/act", headers=H(lease), json={"shortcut": "come_here", "cmd": 0, "world_epoch": 12}).json()
    assert res["ok"] and res["effect"]["kind"] == "position" and res["effect"]["confirmed"] is True and res["effect"]["moved_m"] > 5
    observation = next(event for event in poss.events if event["type"] == "action_observation")
    assert observation["facts"]["action_id"] == res["action_id"] and observation["facts"]["effect"]["confirmed"] is True
    sent = acts(fakes)[-1][2]
    assert sent["world_epoch"] == 12 and sent["action_id"] == res["action_id"] and sent["action"]["type"] == "follow"
    look = client.post("/bridge/possession/act", headers=H(lease), json={"shortcut": "face_owner"}).json()
    assert acts(fakes)[-1][2]["action"]["params"] == {"x": 10.0, "y": 65.62, "z": 5.0} and look["effect"]["kind"] == "not_measured", "she turns to his EYES; a head turn is not 'confirmed'"
    assert client.post("/bridge/possession/shot", headers=H(lease), json={"active_window": True}).json()["perspective"] == "owner_screen"
    client.post("/bridge/possession/sheet", headers=H(lease), json={"framing": {"status": "unknown"}, "hint": "posição ajustada; câmera não verificada"})
    block = client.get("/bridge/possession/sheet", headers=H(lease)).json()["block"]
    assert "enquadramento: desconhecido" in block and "câmera não verificada" in block


def test_external_collection_result_wakes_pending_agent_poll_with_verified_resource_delta(env):
    import httpx

    app, fakes, poss = env
    fakes.act_result = {
        "ok": True,
        "status": "succeeded",
        "perception": {"position": dict(fakes.pos), "owner_distance": 11.2, "inventory": {"oak_log": 6}},
        "timing_ms": {"body_act": 24.0},
    }

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            started = (await client.post("/bridge/possession/start", json={"controller": "codex"})).json()
            lease = started["lease"]
            await client.get("/bridge/possession/state", headers=H(lease))
            poll = asyncio.create_task(client.get("/bridge/possession/next?since=0&wait=20&coalesce_ms=0", headers=H(lease)))
            await asyncio.sleep(0.02)
            result = await client.post("/bridge/possession/act", headers=H(lease), json={
                "action": {"type": "mine", "params": {"block": "oak_log"}}, "cmd": 0, "world_epoch": 12,
            })
            return result, await asyncio.wait_for(poll, timeout=1.0)

    result, delivered = asyncio.run(scenario())
    action = result.json()
    assert action["effect"]["confirmed"] is True
    assert action["effect"]["outcome"] == "collected"
    assert action["effect"]["delta"] == {"oak_log": 6}
    event = next(event for event in delivered.json()["events"] if event["type"] == "action_observation")
    assert event["facts"]["action_id"] == action["action_id"]
    assert event["facts"]["effect"]["delta"] == {"oak_log": 6}
    assert poss.events and event["observed_at"] > 0


def test_ok_true_alone_is_not_reported_as_a_physical_effect(env):
    app, fakes, _ = env
    fakes.act_result = {"ok": True, "status": "succeeded", "perception": {"position": {"x": 0.0, "y": 64.0, "z": 0.0}, "owner_distance": 11.2}, "timing_ms": {}}
    client = TestClient(app)
    lease = start(client)["lease"]
    client.get("/bridge/possession/state", headers=H(lease))
    res = client.post("/bridge/possession/act", headers=H(lease), json={"action": {"type": "navigate", "params": {"x": 5, "y": 64, "z": 5, "distance": 2}}}).json()
    assert res["ok"] is True and res["effect"]["confirmed"] is False, "the contract said ok but nothing moved: the effect says so"


@pytest.mark.parametrize("result,expected_class", [
    ({"ok": False, "error": "unavailable:TimeoutError"}, "infrastructure_error"),
    ({"ok": False, "status": "failed", "error": "body_unavailable"}, "infrastructure_error"),
    ({"ok": False, "status": "failed", "error": "no_fresh_state"}, "precondition_refused"),
    ({"ok": False, "status": "uncertain", "error": "ConnectionError"}, "infrastructure_uncertain"),
    ({"ok": False, "status": "blocked", "error": "no_path"}, "blocked"),
    ({"ok": False, "status": "failed", "error": "tool_failed"}, "executor_failed"),
    ({"ok": True, "status": "succeeded", "perception": {"position": {"x": 0.0, "y": 64.0, "z": 0.0}}}, "accepted_unverified"),
])
def test_action_observation_classifies_failure_without_claiming_unobserved_success(env, result, expected_class):
    app, fakes, poss = env
    fakes.act_result = result
    client = TestClient(app)
    lease = start(client)["lease"]
    response = client.post("/bridge/possession/act", headers=H(lease), json={
        "action": {"type": "scan", "params": {"blocks": ["oak_log"], "radius": 8}},
    })

    assert response.status_code == 200
    assert response.json()["outcome_class"] == expected_class
    event = next(event for event in poss.events if event["type"] == "action_observation")
    assert event["facts"]["outcome_class"] == expected_class
    if expected_class == "accepted_unverified":
        assert event["facts"]["effect"].get("confirmed") is not True
    if expected_class == "infrastructure_uncertain":
        assert event["facts"]["effect"].get("confirmed") is not True


def test_late_action_from_ended_session_cannot_clear_new_session_gate_or_publish_progress(env, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    app, fakes, poss = env
    client = TestClient(app)
    lease_a = start(client)["lease"]
    entered, release = threading.Event(), threading.Event()
    base_call = io.daemon_call

    def delayed_call(method, path, payload=None, timeout=8.0):
        if path == "/body/act":
            entered.set()
            if not release.wait(3):
                return {"ok": False, "status": "uncertain", "error": "TimeoutError"}
            return {"ok": True, "status": "succeeded", "perception": {
                "position": dict(fakes.pos), "owner_distance": 11.2, "inventory": {"oak_log": 6},
            }, "world_epoch": 12}
        return base_call(method, path, payload, timeout)

    monkeypatch.setattr(io, "daemon_call", delayed_call)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old_action = pool.submit(client.post, "/bridge/possession/act", headers=H(lease_a), json={
            "action": {"type": "mine", "params": {"block": "oak_log"}, "timeout_seconds": 10},
            "cmd": poss.s.command_version, "world_epoch": 12,
        })
        assert entered.wait(1)
        old_session = poss.s.session_id
        assert client.post("/bridge/possession/stop").status_code == 200
        new = start(client)
        assert new["session_id"] != old_session and poss._action_in_flight
        with pytest.raises(possession_module.PossessionError, match="action_in_flight"):
            poss.begin_action(new["lease"])

        release.set()
        stale_response = old_action.result(timeout=2)

    assert stale_response.status_code == 409
    assert stale_response.json()["detail"]["code"] == "possession_session_changed"
    assert not any(event["type"] == "action_observation" for event in poss.events)
    poss.begin_action(new["lease"])  # the old request's finally released only its own outstanding gate
    poss.finish_action()


def test_late_action_effect_from_old_world_epoch_is_not_published_as_current(env, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    app, fakes, poss = env
    client = TestClient(app)
    lease = start(client)["lease"]
    entered, release = threading.Event(), threading.Event()
    base_call = io.daemon_call

    def delayed_call(method, path, payload=None, timeout=8.0):
        if path == "/body/act":
            entered.set()
            if not release.wait(3):
                return {"ok": False, "status": "uncertain", "error": "TimeoutError"}
            return {"ok": True, "status": "succeeded", "perception": {
                "position": dict(fakes.pos), "owner_distance": 11.2, "inventory": {"oak_log": 6},
            }, "world_epoch": 12}
        return base_call(method, path, payload, timeout)

    monkeypatch.setattr(io, "daemon_call", delayed_call)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, "/bridge/possession/act", headers=H(lease), json={
            "action": {"type": "mine", "params": {"block": "oak_log"}, "timeout_seconds": 10},
            "cmd": poss.s.command_version, "world_epoch": 12,
        })
        assert entered.wait(1)
        poss.note_world_epoch(13)
        release.set()
        response = pending.result(timeout=2)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "world_epoch_changed"
    assert response.json()["detail"]["world_epoch"] == 13
    assert not any(event["type"] == "action_observation" for event in poss.events)


def test_cancelled_http_waiter_keeps_body_action_gate_until_executor_finishes(env, monkeypatch):
    import httpx
    import threading

    app, fakes, poss = env
    lease = start(TestClient(app))["lease"]
    entered, release = threading.Event(), threading.Event()

    def delayed_call(method, path, payload=None, timeout=8.0):
        if path == "/body/act":
            entered.set()
            if not release.wait(3):
                return {"ok": False, "status": "uncertain", "error": "TimeoutError"}
            return {"ok": True, "status": "succeeded", "perception": {
                "position": dict(fakes.pos), "owner_distance": 11.2, "inventory": {},
            }, "world_epoch": 12}
        return fakes.daemon_call(method, path, payload, timeout)

    monkeypatch.setattr(io, "daemon_call", delayed_call)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            first = asyncio.create_task(client.post("/bridge/possession/act", headers=H(lease), json={
                "action": {"type": "scan", "params": {"blocks": ["oak_log"]}, "timeout_seconds": 10},
                "cmd": poss.s.command_version, "world_epoch": 12,
            }))
            assert await asyncio.to_thread(entered.wait, 1)
            first.cancel()
            try:
                await asyncio.sleep(0.02)
                assert not first.done(), "cancelled HTTP caller must wait for the shielded body request to settle"
                second = await client.post("/bridge/possession/act", headers=H(lease), json={
                    "action": {"type": "scan", "params": {"blocks": ["oak_log"]}, "timeout_seconds": 10},
                    "cmd": poss.s.command_version, "world_epoch": 12,
                })
                assert second.status_code == 409
                assert second.json()["detail"]["code"] == "action_in_flight"
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await first
        assert not poss._action_in_flight

    asyncio.run(scenario())


def test_only_one_body_action_can_be_in_flight(env):
    _, _, poss = env
    client = TestClient(env[0])
    lease = start(client)["lease"]
    poss.begin_action(lease)
    with pytest.raises(possession_module.PossessionError, match="action_in_flight"):
        poss.begin_action(lease)
    poss.finish_action()
    poss.begin_action(lease)


def test_following_goes_on_while_the_owner_talks_in_fragments_scenario_3(env):
    app, fakes, poss = env
    client = TestClient(app)
    lease = start(client)["lease"]
    cmd = client.get("/bridge/possession/next?wait=0", headers=H(lease)).json()["command_version"]
    for text in ("e aí", "hum", "então", "tá", "o que eu tava falando", "ah"):
        say_owner(text)
    got = client.get("/bridge/possession/next?since=0&wait=1&coalesce_ms=0", headers=H(lease)).json()
    assert got["command_version"] == cmd, "fragments and interjections never moved the command version"
    ok = client.post("/bridge/possession/act", headers=H(lease), json={"shortcut": "come_here", "cmd": cmd})
    assert ok.status_code == 200


def test_a_conflicting_instruction_replaces_the_task_and_older_commands_go_stale_scenario_4(env):
    app, fakes, poss = env
    client = TestClient(app)
    lease = start(client)["lease"]
    old = client.get("/bridge/possession/next?wait=0", headers=H(lease)).json()["command_version"]
    new = client.post("/bridge/possession/act", headers=H(lease), json={"shortcut": "come_here", "cmd": old, "supersede": True}).json()["command_version"]
    assert new == old + 1 and ("POST", "/possession/halt", {}) in fakes.daemon, "the running task was halted before the new plan"
    stale = client.post("/bridge/possession/act", headers=H(lease), json={"action": {"type": "mine", "params": {"block": "oak_log"}}, "cmd": old})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale"


def test_stop_acts_at_once_and_cancels_pending_commands_scenario_5(env):
    app, fakes, poss = env
    client = TestClient(app)
    lease = start(client)["lease"]
    planned = client.get("/bridge/possession/next?wait=0", headers=H(lease)).json()["command_version"]
    assert say_owner("Célia, para tudo agora") is False, "stays on the normal path so the series' deterministic emergency stop also runs"
    assert ("POST", "/possession/halt", {}) in fakes.daemon and fakes.cancelled == ["sp-1"] and poss.active()
    stale = client.post("/bridge/possession/act", headers=H(lease), json={"shortcut": "come_here", "cmd": planned})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale"


def test_owner_stop_interrupts_an_inflight_long_body_action_without_waiting_for_its_timeout(env, monkeypatch):
    import threading
    import httpx

    app, fakes, poss = env
    entered_action, halt_ack = threading.Event(), threading.Event()
    base_call = io.daemon_call

    def controlled_daemon_call(method, path, payload=None, timeout=8.0):
        if path == "/body/act":
            entered_action.set()
            assert halt_ack.wait(2.0), "urgent halt must reach the body while the action is still running"
            return {"ok": False, "status": "cancelled", "error": "cancelled by owner stop",
                    "perception": {"position": dict(fakes.pos), "owner_distance": 11.2}, "timing_ms": {"body_act": 20.0}}
        if path == "/possession/halt":
            halt_ack.set()
            return {"ok": True, "status": "stopped"}
        return base_call(method, path, payload, timeout)

    monkeypatch.setattr(io, "daemon_call", controlled_daemon_call)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            started = (await client.post("/bridge/possession/start", json={"controller": "codex"})).json()
            lease = started["lease"]
            action_task = asyncio.create_task(client.post("/bridge/possession/act", headers=H(lease), json={
                "action": {"type": "navigate", "params": {"x": 20, "y": 64, "z": 20}, "timeout_seconds": 35},
                "cmd": 0, "world_epoch": 12,
            }))
            assert await asyncio.to_thread(entered_action.wait, 1.0)
            stopped_at = asyncio.get_running_loop().time()
            await routes.mirror_owner_text("Célia, para tudo agora", source="microphone", authenticated_owner=True, self_echo=False)
            assert await asyncio.to_thread(halt_ack.wait, 0.5)
            response = await asyncio.wait_for(action_task, timeout=1.0)
            return response, asyncio.get_running_loop().time() - stopped_at

    response, elapsed = asyncio.run(scenario())
    assert elapsed < 0.5
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    assert response.json()["effect"].get("confirmed") is not True
    assert poss.active(), "stop cancels body motion but keeps the possession and conversation alive"


def test_silence_cuts_her_voice_but_not_the_body_and_the_persona_initiative_stays_quiet_scenario_6(env):
    app, fakes, poss = env
    client = TestClient(app)
    lease = start(client)["lease"]
    halts = lambda: [c for c in fakes.daemon if c[1] == "/possession/halt"]
    assert say_owner("Célia, fica quieta") is True, "consumed: the persona must not answer a request to be quiet"
    assert fakes.cancelled == ["sp-1"] and not halts(), "the voice was cut and the body was NOT halted"
    assert possession_module.is_silenced()
    assert client.post("/bridge/possession/act", headers=H(lease), json={"shortcut": "face_owner"}).status_code == 200, "she keeps following"
    say_owner("pode falar de novo")
    assert not possession_module.is_silenced()


def test_simple_body_command_uses_authenticated_owner_ingress_without_parallel_body_dispatch(env):
    app, fakes, poss = env
    client = TestClient(app)
    lease = start(client)["lease"]
    assert say_owner("Célia, pula", uid="u-jump") is False  # the persona still receives the utterance
    got = client.get("/bridge/possession/next?since=0&wait=0&coalesce_ms=0", headers=H(lease)).json()
    command = next(event for event in got["events"] if event["type"] == "owner_jump")
    assert command["utterance_id"] == "u-jump"
    assert not acts(fakes), "the possession agent remains the sole body-action owner"
    assert say_owner("não pula") is False
    got = client.get(f"/bridge/possession/next?since={got['last_seq']}&wait=0&coalesce_ms=0", headers=H(lease)).json()
    assert [event["type"] for event in got["events"]] == ["owner_speech"]
    assert not acts(fakes)


def test_local_possession_body_command_returns_result_event_instead_of_executable_imperative(env, monkeypatch):
    from celia.core import minecraft_series
    from celia.core.series_intent import SeriesIntent
    from celia.core.minecraft_series import Triage, Outcome
    from celia.core import cognitive_loop as cognitive_module

    _, _, poss = env
    poss.start("codex", world_epoch=12)
    before_version = poss.s.command_version
    item = SeriesIntent("command", "companion.jump", {}, "pula", "explicit_command", frozenset({"GAMEPLAY"}))

    class Center:
        async def triage(self, *_args, **_kwargs):
            return Triage(True, "command", [(item, False)], source="microphone")

        async def execute(self, _triage, *, utterance_id=None):
            return Outcome([{"ok": True, "status": "succeeded", "request_id": f"req-{utterance_id}"}], "FATO: pulei uma vez")

    monkeypatch.setattr(minecraft_series, "get_series_center", lambda: Center())
    monkeypatch.setattr(cognitive_module.cognitive_loop, "note_minecraft_event", lambda *_a, **_k: True)
    monkeypatch.setattr(cognitive_module.cognitive_loop, "request_think", lambda *_a, **_k: True)
    persona = []

    async def submit(text):
        persona.append(text)

    async def run():
        handled = await routes.dispatch_local_owner_body_command(
            "Célia, pula", source="microphone", authenticated_owner=True, self_echo=False,
            utterance_id="jump-once", persona_submit=submit)
        await asyncio.sleep(0.02)
        return handled

    assert asyncio.run(run()) is True
    assert [event["type"] for event in poss.events] == ["owner_body_command_result"]
    event = poss.events[0]
    assert event["utterance_id"] == "jump-once"
    assert event["facts"]["status"] == "completed" and event["facts"]["original_text"] == "Célia, pula"
    assert isinstance(event["facts"]["duration_ms"], (int, float)) and event["facts"]["duration_ms"] >= 0
    assert event["facts"]["command_version"] == before_version + 1
    assert poss.s.command_version == before_version + 1
    assert persona == ["FATO: pulei uma vez"]


@pytest.mark.parametrize("utterance", ["não pula", "eu disse 'pula'"])
def test_local_possession_dispatch_ignores_negated_or_quoted_commands(env, monkeypatch, utterance):
    from celia.core import minecraft_series

    _, _, poss = env
    poss.start("codex", world_epoch=12)
    monkeypatch.setattr(minecraft_series, "get_series_center", lambda: (_ for _ in ()).throw(AssertionError("no triage for non-imperative speech")))

    async def run():
        return await routes.dispatch_local_owner_body_command(utterance, source="microphone", authenticated_owner=True,
                                                              self_echo=False, utterance_id="quoted", persona_submit=None)

    assert asyncio.run(run()) is False
    assert not poss.events


def test_a_world_or_session_change_invalidates_the_plan_and_updates_the_epoch(env):
    app, fakes, poss = env
    client = TestClient(app)
    lease = start(client)["lease"]
    fakes.refuse, fakes.epoch = "world_epoch_changed", 13
    res = client.post("/bridge/possession/act", headers=H(lease), json={"shortcut": "come_here", "world_epoch": 12})
    assert res.status_code == 409 and res.json()["detail"]["code"] == "world_epoch_changed" and res.json()["detail"]["world_epoch"] == 13
    assert poss.s.world_epoch == 13


def test_the_sheet_is_published_by_the_holder_replaced_atomically_and_bounded(env):
    app, fakes, poss = env
    client = TestClient(app)
    lease = start(client)["lease"]
    assert client.post("/bridge/possession/sheet", json={"objective": "x"}).status_code == 403
    r1 = client.post("/bridge/possession/sheet", headers=H(lease), json={"objective": "abrigo", "task": "juntando madeira"}).json()
    r2 = client.post("/bridge/possession/sheet", headers=H(lease), json={"objective": "abrigo", "last_result": "madeira ok"}).json()
    assert (r1["version"], r2["version"]) == (1, 2)
    block = client.get("/bridge/possession/sheet", headers=H(lease)).json()["block"]
    assert "madeira ok" in block and "juntando madeira" not in block and block.count("[FICHA DO CORPO") == 1
    assert client.post("/bridge/possession/sheet", headers=H(lease), json={"objective": "x" * 5000}).status_code == 413
    assert possession_module.sheet_prompt_block().startswith("[FICHA DO CORPO"), "this is exactly what the persona's prompt receives"


def test_end_phrase_ends_it_cancels_the_pending_and_nothing_acts_afterwards_scenario_10(env):
    app, fakes, poss = env
    client = TestClient(app)
    lease = start(client)["lease"]
    client.post("/bridge/possession/sheet", headers=H(lease), json={"objective": "abrigo"})
    assert say_owner("Acabou a gravação.") is True
    assert not poss.active() and ("POST", "/possession/hold", {"on": False}) in fakes.daemon and ("POST", "/possession/halt", {}) in fakes.daemon
    assert possession_module.sheet_prompt_block() == "", "the ended session's facts are gone from the persona's prompt"
    for call in (lambda: client.post("/bridge/possession/act", headers=H(lease), json={"shortcut": "come_here"}), lambda: client.get("/bridge/possession/next?wait=0", headers=H(lease)),
                 lambda: client.post("/bridge/possession/sheet", headers=H(lease), json={"objective": "x"})):
        assert call().status_code == 409
    assert say_owner("vem aqui") is False
    before = len(fakes.daemon)
    client.post("/bridge/possession/stop")
    assert len(fakes.daemon) == before, "stopping twice does nothing, and nothing restarts by itself"


def test_the_journal_records_instructions_decisions_results_ids_and_labels_the_assistance(env):
    app, fakes, poss = env
    client = TestClient(app)
    lease = start(client, "codex")["lease"]
    say_owner("vem aqui", uid="u9")
    seq = client.get("/bridge/possession/next?since=0&wait=0&coalesce_ms=0", headers=H(lease)).json()["events"][0]["seq"]
    client.post("/bridge/possession/act", headers=H(lease), json={"shortcut": "come_here", "reply_to": seq, "utterance_id": "u9"})
    client.post("/bridge/possession/stop")
    rows = [json.loads(l) for l in (poss.root / "sessions" / f"{poss.s.session_id}.jsonl").read_text().splitlines()]
    assert rows[0]["kind"] == "started" and rows[0]["external_assistance"] is True and rows[0]["controller"] == "codex"
    act = [r for r in rows if r["kind"] == "act"][0]
    assert act["ok"] is True and act["action_id"].startswith("act-") and act["latency_ms"] is not None and act["effect"]["kind"] in {"position", "not_measured"}
    trace = [json.loads(l) for l in (poss.root / "trace" / f"{poss.s.session_id}.jsonl").read_text().splitlines()]
    stages = [t["stage"] for t in trace]
    assert stages[:1] == ["owner_final"] and {"agent_polled", "act_emit", "act_daemon_returned", "effect_checked"} <= set(stages)
    emit = [t for t in trace if t["stage"] == "act_emit"][0]
    assert emit["utterance_id"] == "u9" and emit["action_id"] == act["action_id"] and "mono_ns" in emit, "the ids are correlated across stages"


# ---------------------------------------------------------------- the persona keeps its own tools apart from the body ------------------------------
def test_the_persona_does_not_see_or_force_body_control_tools_while_possessed(env):
    src = (ROOT / "src/celia/agent/agents/basic_memory_agent.py").read_text(encoding="utf-8")
    assert '{"minecraft_acao", "minecraft_objetivo"}' in src and src.count("is_possessed()") >= 2
    assert "sheet_prompt_block" in src and src.index("conversation_focus_prompt\n") < src.index("_body_sheet = sheet_prompt_block()")


def test_there_is_no_global_input_block_any_more_and_silence_not_possession_mutes_the_initiative_loop():
    singleton = (ROOT / "src/celia/celia_singleton.py").read_text(encoding="utf-8")
    assert "is_possessed" not in singleton, "submit_input is no longer blocked as a whole during a possession"
    loop = (ROOT / "src/celia/core/cognitive_loop.py").read_text(encoding="utf-8")
    assert "is_silenced()" in loop and "is_possessed()" not in loop


def test_the_initiative_loop_is_muted_only_while_the_owner_asked_for_silence(env, monkeypatch):
    from celia.core.cognitive_loop import cognitive_loop
    from celia.core.continuous_behavior import continuous_behavior

    app, _, _ = env
    client = TestClient(app)
    start(client)
    touched = []

    async def spy():
        touched.append(1)
        raise asyncio.CancelledError  # stop right after the first step past the gate
    monkeypatch.setattr(cognitive_loop, "_refresh_minecraft_context", spy)
    monkeypatch.setattr(type(continuous_behavior), "profile", property(lambda self: "conversation_autonomous"))
    say_owner("Célia, fica quieta")
    asyncio.run(cognitive_loop._think_once())
    assert touched == [], "silenced: the loop must not even read the game"
    say_owner("pode falar")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cognitive_loop._think_once())
    assert touched == [1], "not silenced: the persona's own initiative loop runs normally during the possession"


def test_the_voice_routes_mirror_the_owner_before_the_series_and_never_swallow_ordinary_speech():
    src = (ROOT / "src/celia/routes.py").read_text(encoding="utf-8")
    assert src.count("possession_mirror(") == 2
    assert src.count("dispatch_local_owner_body_command(") == 2
    assert src.index("dispatch_local_owner_body_command(") < src.index("possession_mirror(")
    assert src.rindex("dispatch_local_owner_body_command(") < src.rindex("possession_mirror(")
    assert src.index("possession_mirror(") < src.index("series_triage = await series_center.triage(")
    assert src.rindex("possession_mirror(") < src.rindex("series_triage = await series_center.triage(")
