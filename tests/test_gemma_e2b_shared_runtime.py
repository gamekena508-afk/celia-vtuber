"""Gemma E2B shared-VLM rollout: offline guards, routes and persona queue."""
from __future__ import annotations

import asyncio

import pytest
from openai import NOT_GIVEN

from celia.agent.stateless_llm.chained_llm import ChainedLLM
from celia.core.gpu_scheduler import gpu_scheduler
from celia.core.local_persona_profile import _original_persona, local_persona_system
from celia.core.model_chain import chain_links, load_config
from celia.core.quota_ledger import QuotaLedger
from celia.core.vision_policy import GEMMA_E2B_MODEL, VisionUnavailable, vision_policy


def test_disabled_by_default_preserves_original_celia_chain(monkeypatch):
    monkeypatch.delenv("CELIA_GEMMA_E2B_ENABLED", raising=False)
    links = chain_links(load_config(), "celia")
    assert len(links) == len(load_config()["celia"]["chain"]) - 1
    assert all(link["model"] != GEMMA_E2B_MODEL for link in links)


def test_optin_local_before_slow_remote_fallbacks(monkeypatch):
    monkeypatch.setenv("CELIA_GEMMA_E2B_ENABLED", "1")
    links = chain_links(load_config(), "celia")
    assert links[1]["model"] == GEMMA_E2B_MODEL
    assert links[1]["base_url"] == "http://127.0.0.1:8031/v1"
    assert links[1]["extra_body"] == {}
    assert links[1]["local_gpu_job"] is True
    assert links[1]["max_tokens"] <= 256
    assert links[1]["context_tokens"] == 8192
    assert links[0]["model"].startswith("codex/")
    assert any(link["model"].startswith("cf/") for link in links[2:])
    assert "gemini/" in links[-1]["model"]
    assert all(link["model"] != GEMMA_E2B_MODEL for link in chain_links(load_config(), "jev"))


def test_local_vision_optin_only_loopback_and_exact_model(monkeypatch):
    monkeypatch.setenv("CELIA_GEMMA_E2B_ENABLED", "1")
    monkeypatch.setenv("CELIA_LOCAL_VISION_MODEL", GEMMA_E2B_MODEL)
    monkeypatch.setenv("CELIA_LOCAL_VISION_URL", "http://127.0.0.1:8031/v1/chat/completions")
    monkeypatch.setenv("CELIA_VISION_BACKEND", "local")
    monkeypatch.setenv("CELIA_LOCAL_VISION_ENABLED", "1")
    vision_policy().require_local()
    monkeypatch.setenv("CELIA_LOCAL_VISION_URL", "https://example.com/v1/chat/completions")
    with pytest.raises(VisionUnavailable):
        vision_policy().require_local()
    monkeypatch.setenv("CELIA_LOCAL_VISION_URL", "http://127.0.0.1:8031/v1")
    monkeypatch.setenv("CELIA_LOCAL_VISION_MODEL", "untrusted-remote-model")
    with pytest.raises(VisionUnavailable):
        vision_policy().require_local()
    monkeypatch.setenv("CELIA_LOCAL_VISION_MODEL", GEMMA_E2B_MODEL)
    monkeypatch.setenv("CELIA_GEMMA_E2B_ENABLED", "0")
    with pytest.raises(VisionUnavailable):
        vision_policy().require_local()


def test_short_persona_removes_only_static_conf_prompt():
    static = _original_persona()
    assert static
    scene = "ESTADO ATUAL: ainda no menu, Criador testando o microfone."
    result = local_persona_system(static + "\n" + scene)
    # The currently approved mediana is identical to the conf persona. It
    # should appear once, not be accidentally duplicated by the compactor.
    assert result.count(static) == 1
    assert scene in result
    assert "Você é Célia" in result
    assert "vontade" not in result or len(result) > 150


def test_shared_gpu_queue_and_failover_from_cloudflare(monkeypatch, tmp_path):
    monkeypatch.setenv("CELIA_GEMMA_E2B_ENABLED", "1")
    monkeypatch.setenv("CELIA_GPU_SCHEDULER", "1")
    cfg = load_config()
    ledger = QuotaLedger(cfg["providers"], tmp_path)
    chain = ChainedLLM("celia", cfg, ledger)
    jobs = []
    received = []
    local = next(x for x in chain._links if x.model == GEMMA_E2B_MODEL)

    async def run_job(kind, fn, **kwargs):
        jobs.append(kind)
        return await fn()

    monkeypatch.setattr(gpu_scheduler, "run_job", run_job)

    async def fake_gemma(messages, system=None, tools=NOT_GIVEN):
        received.append((messages, system, tools))
        yield "[teasing] O dragão pode esperar; sua espada de madeira, não."

    local.llm.chat_completion = fake_gemma
    first_remote = chain._links[0]
    assert not first_remote.local_gpu_job

    async def unexpected_remote(*args, **kwargs):
        received.append(("unexpected_remote", args, kwargs))
        yield "remote"

    monkeypatch.setattr(first_remote.llm, "chat_completion", unexpected_remote)

    async def run():
        return [
            event async for event in chain.chat_completion(
                [{"role": "user", "content": "Vamos derrotar o dragão sem armadura?"}],
                _original_persona() + "\nCENA: introdução, ainda não jogando",
                [{"type": "function", "function": {"name": "not_for_gemma"}}],
                prefer_local=True,
            )
        ]

    output = asyncio.run(run())
    assert output == ["[teasing] O dragão pode esperar; sua espada de madeira, não."]
    assert jobs == ["brain"]
    assert received[0][2] is NOT_GIVEN
    assert "CENA: introdução" in received[0][1]
    assert received[0][1].count(_original_persona()) == 1


def test_disabled_gpu_skips_local_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("CELIA_GEMMA_E2B_ENABLED", "1")
    monkeypatch.setenv("CELIA_GPU_SCHEDULER", "0")
    cfg = load_config()
    cfg["celia"]["ttft_sec"] = 0.1
    ledger = QuotaLedger(cfg["providers"], tmp_path)
    chain = ChainedLLM("celia", cfg, ledger)
    ledger.record("celia", chain._links[0].model, "CTD")

    # Both known-dead remote links must be skipped; the local link is skipped
    # independently because the GPU scheduler is disabled in this test.
    ledger.record("celia", "groq/openai/gpt-oss-120b", "MRT")
    remote = next(x for x in chain._links if x.model == "groq/qwen/qwen3.8-27b")
    assert remote.model.startswith("groq/")
    called = []
    async def fake_remote(messages, system=None, tools=NOT_GIVEN):
        called.append(True)
        yield "resposta remota"
    remote.llm.chat_completion = fake_remote

    async def run():
        return [e async for e in chain.chat_completion([{"role": "user", "content": "oi"}], "sys")]

    assert asyncio.run(run()) == ["resposta remota"]
    assert called == [True]
