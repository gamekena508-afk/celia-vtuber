"""Fila de fallback + Contador de Cota, sem rede (provedores falsos)."""

import asyncio
import json
import time

from celia.agent.stateless_llm.chained_llm import ERR_PREFIX, PERSONA_UNAVAILABLE, ChainedLLM
from celia.core.quota_ledger import QuotaLedger, classify_error



def test_real_persona_chain_prioritizes_free_remote_before_local_gemma(monkeypatch):
    """Owner contract 17/11: Gemma E2B may respond only after every remote link."""
    from celia.core.model_chain import chain_links, load_config

    monkeypatch.setenv("CELIA_GEMMA_E2B_ENABLED", "1")
    models = [link["model"] for link in chain_links(load_config(), "celia")]
    assert models[0] == "cx/gpt-5.6-luna"
    assert models[-1] == "celia-gemma4-e2b"
    assert models.count("celia-gemma4-e2b") == 1
    assert len(models) >= 3  # Never collapse the remote pool to a local primary.

PROVIDERS = {
    "cloudflare": {"match": ["cf/"], "kind": "requests", "daily_limit": 10, "reserve_for_jev": 0.2},
    "openrouter-free": {"match": ["openrouter/"], "match_suffix": ":free", "kind": "requests",
                        "daily_limit": 50, "minute_limit": 20, "hard_limit": True},
    "gemini": {"match": ["gemini/"], "kind": "requests"},
}


class FakeErr(Exception):
    def __init__(self, status_code, text):
        super().__init__(text)
        self.status_code = status_code


def make_chain(tmp_path, brain="celia", mode="persona", ttft=0.2, total=1.0):
    cfg = {"gateway": {"base_url": "http://x/v1", "api_key": "k"},
           brain: {"mode": mode, "ttft_sec": ttft, "total_sec": total,
                   "chain": ["cf/a", "openrouter/b:free", "gemini/c"]}}
    led = QuotaLedger(PROVIDERS, tmp_path)
    return ChainedLLM(brain, cfg, led), led


def fake(link, *events, sleep=0.0, raise_exc=None, last_error=None, calls=None):
    async def gen(messages, system=None, tools=None):
        if calls is not None:
            calls.append(link.model)
        link.llm.last_error = last_error
        if sleep:
            await asyncio.sleep(sleep)
        if raise_exc is not None:
            raise raise_exc
        for ev in events:
            yield ev
    link.llm.chat_completion = gen


def collect(chain, tools=None):
    async def run():
        out = []
        async for ev in chain.chat_completion([{"role": "user", "content": "oi"}], "sys", tools):
            out.append(ev)
        return out
    return asyncio.run(run())


def codes(led):
    files = list(led.log_dir.glob("*.jsonl"))
    files = [f for f in files if f.name != "commands.jsonl"]
    return [json.loads(line)["code"] for f in files for line in f.read_text().splitlines()]


def test_classify_error_siglas():
    assert classify_error(FakeErr(429, "Rate limit exceeded: free-models-per-day"))[0] == "CTD"
    code, pause, _ = classify_error(FakeErr(413, "Too many (reset after 1m 28s)"))
    assert code == "CTM" and pause == 88
    assert classify_error(FakeErr(429, "slow down"))[0] == "CTM"
    assert classify_error(FakeErr(402, "not included in your free usage"))[0] == "SLD"
    assert classify_error(FakeErr(403, "Key limit exceeded (total limit)"))[0] == "CHV"
    assert classify_error(FakeErr(410, "Gone"))[0] == "MRT"
    assert classify_error("[cloudflare] [400]: AiError: No such model")[0] == "MRT"
    code, pause, _ = classify_error(FakeErr(400, "Invalid input"))
    assert code == "ERR" and pause == 600


def test_lento_pula_para_o_proximo(tmp_path):
    chain, led = make_chain(tmp_path, ttft=0.1)
    a, b, _ = chain._links
    fake(a, "tarde demais", sleep=0.5)
    fake(b, "Oi", " pai")
    assert collect(chain) == ["Oi", " pai"]
    assert codes(led) == ["LNT", "OK"]


def test_cota_do_dia_desliga_provedor_ate_o_reset(tmp_path):
    chain, led = make_chain(tmp_path)
    a, b, _ = chain._links
    calls = []
    fake(a, raise_exc=FakeErr(429, "free-models-per-day"), calls=calls)
    fake(b, "ok", calls=calls)
    assert collect(chain) == ["ok"]
    assert collect(chain) == ["ok"]
    assert calls == ["cf/a", "openrouter/b:free", "openrouter/b:free"]  # cf não foi chamado de novo
    st = led.snapshot()["providers"]["cloudflare"]
    assert st["blocked_code"] == "CTD" and st["blocked_until"] > time.time()


def test_modelo_morto_via_last_error(tmp_path):
    chain, led = make_chain(tmp_path)
    a, b, _ = chain._links
    fake(a, f"{ERR_PREFIX} boom", last_error=FakeErr(410, "Gone"))
    fake(b, "voltei")
    assert collect(chain) == ["voltei"]
    assert led.snapshot()["links"]["cf/a"]["blocked_code"] == "MRT"
    assert led.release("cf/a") == ["cf/a"]
    assert led.can_use("cf/a", "celia")[0]


def test_modo_tool_valida_e_entrega_lista(tmp_path):
    chain, led = make_chain(tmp_path, brain="jev", mode="tool")
    a, b, _ = chain._links
    fake(a, "", "")  # vazio
    fake(b, [{"name": "open_browser"}])
    assert collect(chain, tools=[{"type": "function"}]) == [[{"name": "open_browser"}]]
    assert codes(led) == ["VAZ", "OK"]


def test_tudo_falha_vira_FIM(tmp_path):
    chain, led = make_chain(tmp_path)
    for link in chain._links:
        fake(link, raise_exc=FakeErr(503, "overloaded"))
    assert collect(chain) == [PERSONA_UNAVAILABLE]
    assert codes(led) == ["ERR", "ERR", "ERR", "FIM"]


def test_reserva_do_jev(tmp_path):
    led = QuotaLedger(PROVIDERS, tmp_path)
    for _ in range(9):
        led.record("jev", "cf/a", "OK")
    assert led.can_use("cf/a", "jev")[0] is True
    ok, why = led.can_use("cf/a", "celia")
    assert ok is False and "reserva" in why


def test_limite_exato_e_por_minuto_do_openrouter(tmp_path):
    led = QuotaLedger(PROVIDERS, tmp_path)
    for _ in range(20):
        led.record("jev", "openrouter/b:free", "OK")
    ok, why = led.can_use("openrouter/b:free", "jev")
    assert ok is False and "20/min" in why


def test_virada_do_dia_solta_CTD(tmp_path):
    led = QuotaLedger(PROVIDERS, tmp_path)
    led.record("jev", "cf/a", "CTD")
    assert led.can_use("cf/a", "jev")[0] is False
    led._prov["cloudflare"]["period"] = "2000-01-01"  # simula reset
    assert led.can_use("cf/a", "jev")[0] is True


def test_comando_liberar_chega_no_processo(tmp_path):
    led = QuotaLedger(PROVIDERS, tmp_path)
    led.record("jev", "cf/a", "CHV")
    assert led.can_use("cf/a", "jev")[0] is False
    with (tmp_path / "commands.jsonl").open("a") as fh:
        fh.write(json.dumps({"cmd": "liberar", "target": "cloudflare"}) + "\n")
    assert led.can_use("cf/a", "jev")[0] is True


def test_estado_sobrevive_a_reinicio(tmp_path):
    led = QuotaLedger(PROVIDERS, tmp_path)
    led.record("jev", "cf/a", "CTD")
    again = QuotaLedger(PROVIDERS, tmp_path)
    assert again.can_use("cf/a", "jev")[0] is False


# ── cota: Cloudflare 6 × 10 mil (todas LLM desde 2026-10-04), Gemini bloqueia por modelo ──
def _real_ledger(tmp_path):
    from celia.core.model_chain import load_config

    cfg = load_config()
    return QuotaLedger(cfg["providers"], tmp_path, cfg.get("neuron_rates") or {})


CF33 = "cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast"


def test_config_real_cloudflare_6_contas_llm_e_gemini_por_modelo():
    from celia.core.model_chain import load_config

    providers = load_config()["providers"]
    assert providers["cloudflare"]["daily_limit"] == 60000
    assert providers["cloudflare-asr"]["block_scope"] == "model"   # provedor dormente
    assert providers["gemini"]["block_scope"] == "model"


def test_reserva_do_jev_conta_sobre_as_6_contas_de_llm_da_cloudflare(tmp_path):
    led = _real_ledger(tmp_path)
    led.record("celia", CF33, "OK", in_chars=1_309_000)  # ~11,6 mil neurons: abaixo de 80% de 60 mil
    assert led.usage("cloudflare")["used"] > 11000
    assert led.can_use(CF33, "celia")[0] is True
    led.record("celia", CF33, "OK", in_chars=5_500_000)   # ~+49 mil: passa de 80% (48 mil) de 60 mil
    ok, why = led.can_use(CF33, "celia")
    assert ok is False and "reserva do Jev" in why
    assert led.can_use(CF33, "jev")[0] is True            # o Jev segue usando a cota reservada


def test_gemini_429_diario_bloqueia_so_o_modelo(tmp_path):
    led = _real_ledger(tmp_path)
    erro = FakeErr(429, "[429] GenerateRequestsPerDayPerProjectPerModel-FreeTier model gemini-3-flash-preview")
    code, pause, msg = classify_error(erro)
    assert code == "CTD"
    led.record("celia", "gemini/gemini-3-flash-preview", code, message=msg, pause_sec=pause)
    ok, why = led.can_use("gemini/gemini-3-flash-preview", "celia")
    assert ok is False and "CTD" in why
    for liberado in ("gemini/gemini-3.1-flash-lite", "gemini/gemini-3.5-flash-lite", "gemini/gemini-2.5-flash"):
        assert led.can_use(liberado, "celia")[0] is True
    assert led._lstate("gemini/gemini-3-flash-preview")["blocked_until"] == led.next_reset("gemini")


def test_gemini_429_por_minuto_pausa_so_o_modelo_por_poucos_segundos(tmp_path):
    led = _real_ledger(tmp_path)
    erro = FakeErr(429, "[429] GenerateRequestsPerMinutePerProjectPerModel-FreeTier gemini-2.5-flash (reset after 8s)")
    code, pause, msg = classify_error(erro)
    assert code == "CTM" and pause == 8.0
    led.record("celia", "gemini/gemini-2.5-flash", code, message=msg, pause_sec=pause)
    assert led.can_use("gemini/gemini-2.5-flash", "celia")[0] is False
    assert led.can_use("gemini/gemini-3.5-flash-lite", "celia")[0] is True
    assert led._lstate("gemini/gemini-2.5-flash")["blocked_until"] - time.time() <= 8.5


def test_provedor_sem_block_scope_continua_bloqueando_inteiro(tmp_path):
    led = _real_ledger(tmp_path)
    led.record("celia", "openrouter/nvidia/nemotron-3-super-120b-a12b:free", "CTD")
    assert led.can_use("openrouter/qwen/qwen3.8-27b:free", "celia")[0] is False


def test_estado_antigo_do_gemini_migra_o_bloqueio_para_o_modelo(tmp_path):
    agora = time.time()
    ate = agora + 3600
    (tmp_path / "state.json").write_text(json.dumps({
        "providers": {"gemini": {"period": "2026-10-03", "used": 67.0, "calls": 67, "blocked_until": ate,
                                 "blocked_code": "CTD", "blocked_since": agora - 60, "last_code": "CTD",
                                 "last_at": agora - 60}},
        "links": {"gemini/gemini-3-flash-preview": {"blocked_until": 0.0, "blocked_code": "", "blocked_since": 0.0,
                                                     "last_code": "CTD", "last_at": agora - 59.6, "ok": 32, "fail": 32},
                  "gemini/gemini-3.5-flash-lite": {"blocked_until": 0.0, "blocked_code": "", "blocked_since": 0.0,
                                                    "last_code": "LNT", "last_at": agora - 900, "ok": 0, "fail": 1}},
        "brains": {}}), encoding="utf-8")
    led = _real_ledger(tmp_path)
    assert led.can_use("gemini/gemini-3-flash-preview", "celia")[0] is False
    assert led.can_use("gemini/gemini-3.5-flash-lite", "celia")[0] is True
    assert led.can_use("gemini/gemini-2.5-flash", "celia")[0] is True
    assert led._pstate("gemini", agora)["blocked_until"] == 0.0
