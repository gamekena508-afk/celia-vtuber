"""max_tokens por elo: opcional na fila; sem a chave vale o max_tokens da fila inteira."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from celia.agent.stateless_llm.chained_llm import _Link  # noqa: E402
from celia.core.model_chain import chain_links  # noqa: E402


def _cfg(chain):
    return {"gateway": {"base_url": "http://127.0.0.1:1/v1", "api_key": "x"},
            "jev": {"max_tokens": 1024, "chain": chain}}


def test_chain_links_sem_max_tokens_nao_inclui_a_chave():
    links = chain_links(_cfg(["a/x", {"model": "b/y"}]), "jev")
    assert all("max_tokens" not in link for link in links)


def test_chain_links_le_max_tokens_do_elo():
    links = chain_links(_cfg([{"model": "groq/m", "max_tokens": 300}, "cf/n"]), "jev")
    assert links[0]["max_tokens"] == 300
    assert "max_tokens" not in links[1]


def test_link_usa_o_max_tokens_do_elo_e_cai_no_da_fila():
    links = chain_links(_cfg([{"model": "groq/m", "max_tokens": 300}, "cf/n"]), "jev")
    a = _Link(links[0], 0.0, 1024, 4.0)
    b = _Link(links[1], 0.0, 1024, 4.0)
    assert a.max_tokens == 300 and a.llm.max_tokens == 300
    assert b.max_tokens == 1024 and b.llm.max_tokens == 1024
