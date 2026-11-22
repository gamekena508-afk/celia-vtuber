import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from celia.agent.agents.basic_memory_agent import BasicMemoryAgent
from celia.integrations.image_generation import ImageRequest, ImageRouter
from celia.integrations.image_generation.identities import IdentityRegistry
from celia.integrations.image_generation.models import ImageResult, ProviderHealth
from celia.integrations.image_generation import router as router_module
from celia.utils.sentence_divider import SentenceDivider


@pytest.mark.parametrize("text", [
    "Célia, gera esta imagem no Flow",
    "gera esta imagem Célia",
    "edita essa imagem do clipboard, Célia",
])
def test_clipboard_vocative_is_not_a_character_reference(text):
    assert IdentityRegistry().resolve_ids(text) == []
    call = BasicMemoryAgent.__new__(BasicMemoryAgent)._forced_system_tool_call(text)
    assert call["name"] == "criar_ou_editar_imagem"
    assert call["input"]["usar_clipboard"] is True
    assert call["input"]["abrir"] is True


def test_requested_character_is_preserved():
    assert IdentityRegistry().resolve_ids("gera uma imagem da Célia") == ["celia"]
    assert IdentityRegistry().resolve_ids("edita esta imagem colocando a Célia") == ["celia"]


@pytest.mark.parametrize("explicit", [True, False])
def test_missing_clipboard_never_submits_a_generation(monkeypatch, explicit):
    router = ImageRouter()
    provider = router.providers["flow"]
    provider.health = AsyncMock(return_value=ProviderHealth("flow", True))
    provider.generate = AsyncMock()
    monkeypatch.setattr(router_module, "capture_clipboard_image", lambda: (None, "empty"))
    result = asyncio.run(router.execute(ImageRequest(
        prompt="gera esta imagem Célia", provider="flow", use_clipboard=explicit,
    )))
    assert not result.ok and result.error_code == "clipboard_missing"
    provider.generate.assert_not_awaited()
    provider.health.assert_not_awaited()


@pytest.mark.parametrize("name", ["gpt", "meta", "flow"])
def test_clipboard_is_the_only_reference_for_vocative(monkeypatch, tmp_path, name):
    clip = tmp_path / "clip.png"
    clip.write_bytes(b"fixture")
    output = tmp_path / "output.png"
    output.write_bytes(b"fixture")
    router = ImageRouter()
    provider = router.providers[name]
    provider.health = AsyncMock(return_value=ProviderHealth(name, True))
    provider.generate = AsyncMock(return_value=ImageResult(
        True, name, "prompt", output_paths=[str(output)], status="completed",
    ))
    monkeypatch.setattr(router_module, "capture_clipboard_image", lambda: (clip, None))
    opened = []
    monkeypatch.setattr(router_module, "open_image", lambda path: (opened.append(path) or True, "ok"))
    result = asyncio.run(router.execute(ImageRequest(prompt=f"Célia, gera esta imagem no {name}", provider=name)))
    assert result.ok and opened == [output]
    assert provider.generate.call_args.args[1] == [clip]
    assert "celia" not in provider.generate.call_args.args[2].lower()
    assert f"no {name}" not in provider.generate.call_args.args[2].lower()
    assert "Preserve its existing objects" in provider.generate.call_args.args[2]


def test_spoken_cue_is_fully_flushed_before_tool_runs():
    events = []

    async def stream():
        yield "Estou gerando, criador. Espera aí. "
        yield {"type": "tool_call_status", "status": "running"}
        assert " ".join(events) == "Estou gerando, criador. Espera aí."
        yield "Pronto, criador. Terminei."

    async def collect():
        divider = SentenceDivider(faster_first_response=True, segment_method="pysbd")
        async for item in divider.process_stream(stream()):
            if not isinstance(item, dict):
                events.append(item.text)

    asyncio.run(collect())
    assert " ".join(events).endswith("Pronto, criador. Terminei.")


def test_success_and_failure_dialogue_have_no_paths_or_false_completion():
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
    def answer(data):
        return agent._format_forced_system_answer("criar_ou_editar_imagem", json.dumps(data), {})
    assert answer({"ok": True, "output_paths": ["/private/image.png"]}) == "Pronto, criador. Terminei."
    assert "clipboard" in answer({"ok": False, "error_code": "clipboard_missing"})
    assert "não consegui abrir" in answer({"ok": True, "progress": ["open_failed:viewer"]})
    assert "terminou" not in answer({"ok": False})
    assert "não consegui confirmar" in agent._format_forced_system_answer("criar_ou_editar_imagem", "invalid", {})


def test_meta_duplicate_urls_download_once(monkeypatch, tmp_path):
    from celia.integrations.image_generation.providers import meta as module
    payload = {"choices": [{"message": {"content": json.dumps({"media": [
        {"src": "https://example.test/image.webp"}, {"src": "https://example.test/image.webp"},
    ]})}}]}
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return payload
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, *args, **kwargs):
            return Response()
    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    provider = module.MetaImageProvider()
    provider._download = AsyncMock(return_value=tmp_path / "image.webp")
    result = asyncio.run(provider.generate(ImageRequest(prompt="sphere"), [], "sphere"))
    assert result.ok and len(result.output_paths) == 1
    provider._download.assert_awaited_once()


def test_viewer_recovers_desktop_environment_and_checks_exit(monkeypatch, tmp_path):
    from celia.integrations.image_generation import utils
    image = tmp_path / 'image.png'
    image.write_bytes(b'fixture')
    for key in ('XDG_RUNTIME_DIR', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'XDG_CURRENT_DESKTOP'):
        monkeypatch.delenv(key, raising=False)
    exists = Path.exists
    monkeypatch.setattr(Path, 'exists', lambda p: True if str(p).startswith('/run/user/') else exists(p))
    monkeypatch.setattr(utils.shutil, 'which', lambda name: '/usr/bin/kde-open' if name == 'kde-open' else None)
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return type('Result', (), {'returncode': 0})()
    monkeypatch.setattr(utils.subprocess, 'run', run)
    assert utils.open_image(image) == (True, 'ok')
    env = calls[0][1]['env']
    assert env['WAYLAND_DISPLAY'] == 'wayland-0'
    assert env['DBUS_SESSION_BUS_ADDRESS'].endswith('/bus')
    assert env['XDG_CURRENT_DESKTOP'] == 'KDE'
    monkeypatch.setattr(utils.subprocess, 'run', lambda *a, **kw: type('Result', (), {'returncode': 3})())
    assert utils.open_image(image) == (False, 'viewer exited 3')
