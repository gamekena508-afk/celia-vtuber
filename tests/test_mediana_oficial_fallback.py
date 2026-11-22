"""Regressoes offline: Gemma e modelos remotos compartilham a persona ativa.

Arquivo Mediana permanece histórico; nenhum teste faz chamada de rede.
"""
from pathlib import Path

import pytest
import yaml

from celia.core.model_chain import chain_links, load_config
from celia.core.local_persona_profile import (
    _original_persona, local_persona_system, LOCAL_RUNTIME_CONTRACTS,
)
from celia.core.gpu_scheduler import gpu_scheduler
from celia.core.quota_ledger import QuotaLedger
from celia.agent.stateless_llm.chained_llm import ChainedLLM

ROOT = Path(__file__).resolve().parents[1]
MEDIANA = ROOT / "config/personas/celia-mediana-oficial.txt"


def test_gemma_preserva_mesmo_prompt_ativo_e_contexto_sem_duplicar():
    antiga = MEDIANA.read_text(encoding="utf-8").strip()
    conf = yaml.safe_load((ROOT / "conf.yaml").read_text(encoding="utf-8"))
    active = conf["character_config"]["persona_prompt"]
    assert active == _original_persona()
    assert "ironia primeiro" not in active and "PESSOA CONHECIDA" in active
    assert antiga != active
    state = "CONTEXTO ATUAL: câmera desligada; Criador falando de microfone."
    system = "[CONDUTA CONVERSACIONAL]\n[PERSONA]\n" + active + "\n" + state
    rendered = local_persona_system(system)
    assert rendered.startswith("[CONDUTA CONVERSACIONAL]")
    assert rendered.count(active) == 1
    assert state in rendered and LOCAL_RUNTIME_CONTRACTS in rendered
    assert antiga not in rendered


def test_ordem_de_modelos_atual_e_gemma_compartilha_persona(monkeypatch):
    monkeypatch.setenv("CELIA_GEMMA_E2B_ENABLED", "1")
    cfg = load_config()
    links = chain_links(cfg, "celia")
    assert [x["model"] for x in links[:3]] == [
        "codex/gpt-5.6-luna", "celia-gemma4-e2b",
        "groq/openai/gpt-oss-120b",
    ]
    assert links[1]["local_gpu_job"]
    assert links[1]["base_url"].startswith("http://127.0.0.1:8031/")


@pytest.mark.asyncio
async def test_fallback_codex_vazio_para_gemma_sem_rede(monkeypatch, tmp_path):
    monkeypatch.setenv("CELIA_GEMMA_E2B_ENABLED", "1")
    monkeypatch.setenv("CELIA_GPU_SCHEDULER", "1")
    cfg = load_config()
    ledger = QuotaLedger(cfg["providers"], tmp_path)
    chain = ChainedLLM("celia", cfg, ledger)
    called = []
    received = []

    async def empty_codex(messages, system=None, tools=None):
        called.append("Codex")
        if False:
            yield ""

    async def gemma(messages, system=None, tools=None):
        called.append("Gemma")
        received.append((system, tools))
        yield "Vou brincar quando couber, Criador."

    async def job(kind, fn, **kwargs):
        assert kind == "brain"
        return await fn()

    monkeypatch.setattr(gpu_scheduler, "run_job", job)
    chain._links[0].llm.chat_completion = empty_codex
    chain._links[1].llm.chat_completion = gemma
    messages = [{"role": "user", "content": "Oi, Célia."}]
    active = _original_persona()
    system = "[CONDUTA CONVERSACIONAL]\n[PERSONA]\n" + active + "\nCONTEXTO: Criador cumprimentou."
    result = [x async for x in chain.chat_completion(messages, system)]
    assert result == ["Vou brincar quando couber, Criador."]
    assert called == ["Codex", "Gemma"]
    assert received and received[0][0].count(active) == 1
    assert "CONTEXTO: Criador" in received[0][0]
