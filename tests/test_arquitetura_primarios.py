"""Arquitetura definitiva (2026-10-04): quem é PRIMÁRIO em cada fila, e que NENHUM fallback sumiu.

Jev  : Groq gpt-oss-120b primário; a fila anterior (7 elos) segue inteira atrás dele.
Célia: Llama 3.3 70B via Cloudflare primário; fila anterior (6 elos) intacta; sem gpt-oss-120b.
Offline: os elos são trocados por fakes, nada vai ao gateway.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from celia.agent.stateless_llm.chained_llm import ChainedLLM, TOOL_UNAVAILABLE  # noqa: E402
from celia.core.model_chain import chain_links, load_config  # noqa: E402
from celia.core.quota_ledger import QuotaLedger  # noqa: E402

GROQ_120B = "groq/openai/gpt-oss-120b"
GROQ_QWEN = "groq/qwen/qwen3.8-27b"
CF33 = "cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast"

# Cadeias ANTES da mudança (fonte: config/model_chains.yaml em 2026-10-03/04). Têm que continuar lá, na ordem.
JEV_ANTES = [
    CF33,
    "cf/@cf/meta/llama-3.1-70b-instruct-fp8-fast",
    "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/qwen/qwen3.8-27b:free",
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
    "gemini/gemini-3-flash-preview",
    "gemini/gemini-3.5-flash-lite",
]
CELIA_ANTES = JEV_ANTES[:-1]


class HttpErr(Exception):
    def __init__(self, status, text):
        super().__init__(text)
        self.status_code = status


def _models(brain):
    return [x["model"] for x in chain_links(load_config(), brain)]


# ── configuração real ──
def test_jev_primario_e_groq_120b_com_reasoning_baixo():
    links = chain_links(load_config(), "jev")
    assert links[0]["model"] == GROQ_120B
    assert links[0]["extra_body"] == {"reasoning_effort": "low"}


def test_jev_preserva_a_fila_anterior_inteira_e_na_ordem():
    assert _models("jev") == [GROQ_120B, GROQ_QWEN] + JEV_ANTES


def test_jev_fallback1_e_groq_qwen_sem_reasoning_effort():
    links = chain_links(load_config(), "jev")
    assert links[1]["model"] == GROQ_QWEN
    assert "reasoning_effort" not in links[1]["extra_body"]  # 400 enable_thinking pausaria o elo por 10 min


def test_jev_max_tokens_300_so_nos_elos_da_groq_e_fila_segue_1024():
    cfg = load_config()
    links = chain_links(cfg, "jev")
    assert [l.get("max_tokens") for l in links[:2]] == [300, 300]
    assert all("max_tokens" not in l for l in links[2:])  # Gemini/OpenRouter mantêm o teto da fila
    assert cfg["jev"]["max_tokens"] == 1024


def test_groq_qwen_e_120b_sao_baldes_separados_na_cota(tmp_path):
    cfg = load_config()
    led = QuotaLedger(cfg["providers"], tmp_path, cfg.get("neuron_rates") or {})
    led.record("jev", GROQ_120B, "CTM")
    assert led.can_use(GROQ_120B, "jev")[0] is False
    assert led.can_use(GROQ_QWEN, "jev")[0] is True


def test_celia_primaria_e_llama33_cloudflare_e_a_fila_nao_mudou():
    assert _models("celia") == CELIA_ANTES
    assert _models("celia")[0] == CF33


def test_celia_nunca_e_roteada_para_o_gpt_oss_120b():
    assert not [m for m in _models("celia") if "gpt-oss" in m or m.startswith("groq/")]


def test_papeis_jev_decide_com_tools_e_celia_fala_como_persona():
    cfg = load_config()
    assert cfg["jev"]["mode"] == "tool"
    assert cfg["celia"]["mode"] == "persona"
    assert cfg["jev"]["temperature"] == 0.0
    assert cfg["jev"]["total_sec"] <= 5.0  # decisão rápida, não texto longo


def test_provedor_groq_bloqueia_so_o_modelo(tmp_path):
    cfg = load_config()
    led = QuotaLedger(cfg["providers"], tmp_path, cfg.get("neuron_rates") or {})
    assert led.provider_of(GROQ_120B) == "groq"
    assert cfg["providers"]["groq"]["block_scope"] == "model"


# ── comportamento da fila (offline) ──
def _fila(tmp_path, brain, ttft=0.3, total=1.0):
    cfg = load_config()
    cfg[brain] = {**cfg[brain], "ttft_sec": ttft, "total_sec": total}
    led = QuotaLedger(cfg["providers"], tmp_path, cfg.get("neuron_rates") or {})
    return ChainedLLM(brain, cfg, led), led


def _fake(chain, model, *, ok=None, erro=None, vazio=False, calls=None):
    link = next(l for l in chain._links if l.model == model)

    async def gen(messages, system=None, tools=None):
        if calls is not None:
            calls.append(model)
        if erro is not None:
            raise erro
        if vazio:
            return
            yield  # noqa: unreachable, faz isto ser gerador
        yield ok

    link.llm.chat_completion = gen
    return link


def _rodar(chain, tools=None):
    async def go():
        return [e async for e in chain.chat_completion([{"role": "user", "content": "abre o google"}], "sys", tools or [])]

    return asyncio.run(go())


TOOLCALL = [{"id": "1", "function": {"name": "open_browser", "arguments": "{}"}}]


def test_jev_usa_groq_120b_normalmente(tmp_path):
    chain, led = _fila(tmp_path, "jev")
    calls = []
    for m in _models("jev"):
        _fake(chain, m, ok=TOOLCALL if m == GROQ_120B else "outro", calls=calls)
    assert _rodar(chain) == [TOOLCALL]
    assert calls == [GROQ_120B]  # nenhum fallback foi chamado


@pytest.mark.parametrize("erro", [
    HttpErr(429, "[429] rate limit tokens per minute (reset after 20s)"),
    HttpErr(429, "[429] tokens per day (TPD) limit reached"),
    HttpErr(400, "[400] Failed to validate JSON"),
    HttpErr(404, "[404] model does not exist"),
    HttpErr(503, "[503] service unavailable"),
])
def test_jev_cai_no_llama33_cloudflare_quando_o_groq_falha(tmp_path, erro):
    chain, led = _fila(tmp_path, "jev")
    calls = []
    _fake(chain, GROQ_120B, erro=erro, calls=calls)
    _fake(chain, CF33, ok=TOOLCALL, calls=calls)
    assert _rodar(chain) == [TOOLCALL]
    assert calls == [GROQ_120B, CF33]
    # o Groq fica em pausa: a chamada seguinte nem o tenta (pulado na hora)
    calls.clear()
    assert _rodar(chain) == [TOOLCALL]
    assert calls == [CF33]


def test_jev_groq_vazio_ou_lento_vai_ao_fallback(tmp_path):
    chain, led = _fila(tmp_path, "jev", ttft=0.2)
    calls = []
    _fake(chain, GROQ_120B, vazio=True, calls=calls)
    _fake(chain, CF33, ok=TOOLCALL, calls=calls)
    assert _rodar(chain) == [TOOLCALL]
    assert calls == [GROQ_120B, CF33]


def test_jev_percorre_toda_a_fila_ate_o_ultimo_elo(tmp_path):
    chain, led = _fila(tmp_path, "jev")
    calls = []
    for m in _models("jev")[:-1]:
        _fake(chain, m, erro=HttpErr(503, "[503] fora"), calls=calls)
    _fake(chain, "gemini/gemini-3.5-flash-lite", ok=TOOLCALL, calls=calls)
    assert _rodar(chain) == [TOOLCALL]
    assert calls == _models("jev")  # groq + os 7 anteriores, na ordem


def test_jev_sem_nenhum_elo_devolve_aviso_de_ferramenta_indisponivel(tmp_path):
    chain, led = _fila(tmp_path, "jev")
    for m in _models("jev"):
        _fake(chain, m, erro=HttpErr(503, "[503] fora"))
    assert _rodar(chain) == [TOOLCALL and TOOL_UNAVAILABLE]


def test_celia_usa_cloudflare_70b_normalmente_e_nunca_toca_no_groq(tmp_path):
    chain, led = _fila(tmp_path, "celia", ttft=1.0, total=5.0)
    calls = []
    for m in _models("celia"):
        _fake(chain, m, ok="Oi, pai!", calls=calls)
    assert "".join(_rodar(chain)) == "Oi, pai!"
    assert calls == [CF33]
    assert GROQ_120B not in [l.model for l in chain._links]


CF31 = "cf/@cf/meta/llama-3.1-70b-instruct-fp8-fast"
NEMO = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"


def test_celia_erro_isolado_da_cloudflare_cai_no_llama31_e_nao_no_groq(tmp_path):
    chain, led = _fila(tmp_path, "celia", ttft=1.0, total=5.0)
    calls = []
    _fake(chain, CF33, erro=HttpErr(503, "[503] fora"), calls=calls)
    _fake(chain, CF31, ok="Oi do fallback", calls=calls)
    assert "".join(_rodar(chain)) == "Oi do fallback"
    assert calls == [CF33, CF31]
    assert GROQ_120B not in calls


def test_celia_cota_diaria_da_cloudflare_bloqueia_o_provedor_e_vai_ao_openrouter(tmp_path):
    """429 diário na Cloudflare tira o provedor inteiro (comportamento existente): o próximo elo é o OpenRouter."""
    chain, led = _fila(tmp_path, "celia", ttft=1.0, total=5.0)
    calls = []
    _fake(chain, CF33, erro=HttpErr(429, "[429] neurons per day"), calls=calls)
    _fake(chain, CF31, ok="não deveria", calls=calls)
    _fake(chain, NEMO, ok="Oi do OpenRouter", calls=calls)
    assert "".join(_rodar(chain)) == "Oi do OpenRouter"
    assert calls == [CF33, NEMO]
    assert GROQ_120B not in calls


def test_jev_nao_e_a_fonte_da_fala_final(tmp_path):
    """O Jev responde com tool call/decisão (modo tool); a fala é da fila 'celia' (modo persona)."""
    jev, _ = _fila(tmp_path / "a", "jev")
    celia, _ = _fila(tmp_path / "b", "celia")
    assert jev.mode == "tool" and celia.mode == "persona"
    assert [l.model for l in jev._links][0] != [l.model for l in celia._links][0]
