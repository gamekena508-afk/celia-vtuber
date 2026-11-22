import json

import pytest

from celia.celia_singleton import CeliaSingleton


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


@pytest.mark.asyncio
async def test_pet_preempts_browser_for_audio_only():
    singleton = CeliaSingleton()
    browser = FakeSocket()
    pet = FakeSocket()

    await singleton.register("browser-1", browser, "browser", "browser")
    assert singleton.playback_owner_uid == "browser-1"

    await singleton.register("pet-1", pet, "browser", "pet")
    assert singleton.playback_owner_uid == "pet-1"

    payload = {
        "type": "audio",
        "audio": "BASE64-WAV",
        "volumes": [0.2, 0.8],
        "slice_length": 20,
        "display_text": {"text": "oi", "name": "Célia"},
    }
    await singleton.broadcast(payload)

    assert pet.sent[-1]["audio"] == "BASE64-WAV"
    assert pet.sent[-1]["volumes"] == [0.2, 0.8]
    assert "playback_observer" not in pet.sent[-1]

    assert browser.sent[-1]["audio"] is None
    assert browser.sent[-1]["volumes"] == []
    assert browser.sent[-1]["playback_observer"] is True
    assert browser.sent[-1]["display_text"]["text"] == "oi"

    await singleton.unregister("pet-1")
    assert singleton.playback_owner_uid == "browser-1"


@pytest.mark.asyncio
async def test_reloaded_page_takes_the_voice_back_from_an_older_tab():
    # 28/10: a página que tocava recarregou e a voz ficou presa numa aba velha que não toca.
    singleton = CeliaSingleton()
    playing, old_tab = FakeSocket(), FakeSocket()
    await singleton.register("playing", playing, "browser", "browser")
    await singleton.register("old-tab", old_tab, "browser", "browser")
    await singleton.register("playing", playing, "browser", "browser")
    await singleton.unregister("playing")
    reloaded = FakeSocket()
    await singleton.register("reloaded", reloaded, "browser", "browser")
    assert singleton.playback_owner_uid == "reloaded"

    await singleton.broadcast({"type": "audio", "audio": "WAV", "volumes": [0.5]})
    assert reloaded.sent[-1]["audio"] == "WAV"
    assert old_tab.sent[-1]["audio"] is None


@pytest.mark.asyncio
async def test_owner_leaving_hands_voice_to_most_recent_browser():
    singleton = CeliaSingleton()
    for uid in ("a", "b", "c"):
        await singleton.register(uid, FakeSocket(), "browser", "browser")
    assert singleton.playback_owner_uid == "c"
    await singleton.unregister("c")
    assert singleton.playback_owner_uid == "b"


@pytest.mark.asyncio
async def test_new_browser_does_not_steal_voice_from_pet():
    singleton = CeliaSingleton()
    await singleton.register("pet-1", FakeSocket(), "browser", "pet")
    await singleton.register("browser-1", FakeSocket(), "browser", "browser")
    assert singleton.playback_owner_uid == "pet-1"


@pytest.mark.asyncio
async def test_owner_that_cannot_play_hands_voice_to_another_browser():
    singleton = CeliaSingleton()
    for uid in ("plays", "stuck-1", "stuck-2"):
        await singleton.register(uid, FakeSocket(), "browser", "browser")
    assert singleton.playback_owner_uid == "stuck-2"
    singleton.report_playback_failure("stuck-2")
    assert singleton.playback_owner_uid == "stuck-1"
    singleton.report_playback_failure("stuck-1")
    assert singleton.playback_owner_uid == "plays"
    singleton.report_playback_started("plays")
    singleton.report_playback_failure("not-owner")  # só o dono pode se rebaixar
    assert singleton.playback_owner_uid == "plays"


@pytest.mark.asyncio
async def test_pet_is_never_demoted_by_a_playback_failure():
    singleton = CeliaSingleton()
    await singleton.register("browser-1", FakeSocket(), "browser", "browser")
    await singleton.register("pet-1", FakeSocket(), "browser", "pet")
    singleton.report_playback_failure("pet-1")
    assert singleton.playback_owner_uid == "pet-1"


@pytest.mark.asyncio
async def test_live_mode_sends_voice_only_to_the_scene(monkeypatch):
    # Ordem do Pai (28/10): modo Live ligado => só a cena da bancada toca; interface e Pet ficam mudos.
    from celia.live_mode import gate as live_gate
    live = {"on": True}
    monkeypatch.setattr(live_gate, "live_active", lambda: live["on"])
    singleton = CeliaSingleton()
    scene, interface, pet = FakeSocket(), FakeSocket(), FakeSocket()
    await singleton.register("scene-1", scene, "browser", "scene")
    await singleton.register("interface-1", interface, "browser", "browser")
    await singleton.register("pet-1", pet, "browser", "pet")
    assert singleton.playback_owner_uid == "scene-1"

    await singleton.broadcast({"type": "audio", "audio": "WAV", "volumes": [0.5]})
    assert scene.sent[-1]["audio"] == "WAV"
    assert interface.sent[-1]["audio"] is None
    assert pet.sent[-1]["audio"] is None

    live["on"] = False
    assert singleton.playback_owner_uid == "pet-1"


@pytest.mark.asyncio
async def test_newest_scene_takes_the_voice(monkeypatch):
    # 28/10: com duas cenas abertas a voz ficava na mais antiga e sumia da bancada que o Pai via.
    from celia.live_mode import gate as live_gate
    monkeypatch.setattr(live_gate, "live_active", lambda: True)
    singleton = CeliaSingleton()
    await singleton.register("scene-old", FakeSocket(), "browser", "scene")
    await singleton.register("scene-new", FakeSocket(), "browser", "scene")
    assert singleton.playback_owner_uid == "scene-new"


@pytest.mark.asyncio
async def test_scene_without_live_mode_does_not_steal_voice(monkeypatch):
    from celia.live_mode import gate as live_gate
    monkeypatch.setattr(live_gate, "live_active", lambda: False)
    singleton = CeliaSingleton()
    await singleton.register("interface-1", FakeSocket(), "browser", "browser")
    await singleton.register("scene-1", FakeSocket(), "browser", "scene")
    assert singleton.playback_owner_uid == "interface-1"


@pytest.mark.asyncio
async def test_source_connection_never_becomes_playback_owner():
    singleton = CeliaSingleton()
    source = FakeSocket()
    await singleton.register("source-1", source, "source", "pet")
    assert singleton.playback_owner_uid is None
    assert singleton.is_playback_owner("source-1") is False
