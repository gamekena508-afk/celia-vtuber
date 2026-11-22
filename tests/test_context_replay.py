import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def replay():
    path = Path(__file__).resolve().parents[1] / "tools/celia_context_replay.py"
    spec = importlib.util.spec_from_file_location("context_replay_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("failure", ["error", "exception", "empty"])
def test_infrastructure_retry_never_yields_partial_or_error(replay, monkeypatch, failure):
    calls = []

    async def completion(*args):
        calls.append(args)
        if len(calls) == 1:
            if failure == "exception":
                yield "partial"
                raise ConnectionError("lost")
            if failure == "error":
                yield "Error calling the chat endpoint: unavailable"
            return
        yield "valid response"

    async def no_sleep(_):
        pass

    monkeypatch.setattr(replay.asyncio, "sleep", no_sleep)
    model = replay._ModeloReal(SimpleNamespace(llm=SimpleNamespace(chat_completion=completion)), False)

    async def run():
        return [part async for part in model.chat_completion([], "system")]

    assert asyncio.run(run()) == ["valid response"]
    assert len(calls) == 2
    assert model.erros_infra == 1


def test_persistent_infrastructure_failure_invalidates_chain(replay, monkeypatch):
    async def completion(*args):
        yield "Error calling the chat endpoint: unavailable"

    async def no_sleep(_):
        pass

    monkeypatch.setattr(replay.asyncio, "sleep", no_sleep)
    model = replay._ModeloReal(SimpleNamespace(llm=SimpleNamespace(chat_completion=completion)), False)

    async def run():
        return [part async for part in model.chat_completion([], "system")]

    with pytest.raises(RuntimeError, match="Replay invalid"):
        asyncio.run(run())
    assert model.respostas == []
    assert model.erros_infra == 3


def test_simulated_failure_does_not_call_real_provider(replay):
    model = replay._ModeloReal(None, True)
    model.falha_agora = True

    async def run():
        return [part async for part in model.chat_completion([], "system")]

    assert asyncio.run(run()) == [replay.ERRO_PROVEDOR]
    assert model.erros_infra == 0
