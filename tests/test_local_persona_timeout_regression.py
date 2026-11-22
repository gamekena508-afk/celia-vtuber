"""Regression: buffered local E2B needs a full-reply deadline, not TTFT=15s."""

from celia.agent.stateless_llm.chained_llm import _Link
from celia.core.model_chain import chain_links, load_config


def test_local_llm_full_reply_deadline_and_http_timeout(monkeypatch):
    monkeypatch.setenv("CELIA_GEMMA_E2B_ENABLED", "1")
    spec = next(s for s in chain_links(load_config(), "celia") if s["model"] == "celia-gemma4-e2b")
    assert spec["ttft_sec"] >= 45.0
    assert spec["local_gpu_job"] is True
    link = _Link(spec, temperature=0.55, max_tokens=4096, total_sec=30.0)
    assert link.ttft_sec >= 45.0
    assert link.llm.request_timeout_sec >= link.ttft_sec + 10.0


def test_remote_llm_keeps_existing_request_timeout():
    spec = {"model": "groq/demo", "base_url": "http://127.0.0.1:20127/v1", "api_key": "local"}
    link = _Link(spec, temperature=0.55, max_tokens=4096, total_sec=30.0)
    assert link.llm.request_timeout_sec == 30.0
