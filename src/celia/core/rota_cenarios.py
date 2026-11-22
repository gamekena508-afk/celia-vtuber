"""Cenários de roteamento em modo seco: percorre a fila como o ChainedLLM e mostra a decisão, sem rede e sem gastar cota.

Usa o que é REAL: a ordem da fila (`config/model_chains.yaml`), o `QuotaLedger` (num diretório temporário, nunca o de `var/cota`),
`classify_error` (como o projeto classifica cada 429/erro) e o espelho do laço de contas do 9router (`rota_debug.simular_chat_9router`).
O que é SIMULADO: a resposta de cada conta (200/429/…) e o relógio.

Uso: tools/celia_rota.py simular [NOME|todos]   ·   testes em tests/test_rota_debug.py
"""

from __future__ import annotations

import contextlib
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from . import quota_ledger as ql
from .model_chain import chain_links
from .rota_debug import (Conta, EstadoProvedor, PROVEDOR_9ROUTER, _dur, _iso, simular_chat_9router)

CF_NOMES = ["Cloudflare Principal", "Cloudflare Conta1", "Cloudflare Conta2", "Cloudflare Conta3", "Cloudflare Conta4", "Cloudflare Conta5"]
MSG_CF_DIARIA = ('{"errors":[{"message":"AiError: AiError: you have used up your daily free allocation of 10,000 neurons, '
                 'please upgrade to Cloudflare\'s Workers Paid plan if you would like to continue usage.","code":4006}]}')
MSG_OR_DIARIA = '{"error":{"message":"Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day","code":429}}'
UPSTREAM_OK = (200, "")
UPSTREAM_CF_429 = (429, "[429]: " + MSG_CF_DIARIA)
UPSTREAM_OR_429 = (429, "[429]: " + MSG_OR_DIARIA)
UPSTREAM_503 = (503, "[503]: upstream indisponível")


@contextlib.contextmanager
def relogio(t: float) -> Iterator[None]:
    """Fixa o relógio do QuotaLedger (módulo `quota_ledger._now`) durante o bloco."""
    antigo = ql._now
    ql._now = lambda: t
    try:
        yield
    finally:
        ql._now = antigo


def contas(nomes: list[str], base: float, *, usadas_em: dict[str, float] | None = None) -> list[Conta]:
    """Contas novas (sem bloqueio). `usadas_em[nome]` = há quantos segundos foi usada (para o round-robin)."""
    usadas_em = usadas_em or {}
    return [Conta(id=f"{n[:3].lower()}{i:02d}" + "x" * 6, nome=n, prioridade=i, ativa=True,
                  last_used_at=_iso(base - usadas_em[n]) if n in usadas_em else None)
            for i, n in enumerate(nomes, 1)]


def estado(provedor: str, cs: list[Conta], estrategia: str = "round-robin", sticky: int = 3) -> EstadoProvedor:
    return EstadoProvedor(provedor=provedor, contas=cs, estrategia=estrategia, sticky=sticky)


@dataclass
class Passo:
    elo: str
    texto: str


@dataclass
class ResultadoTurno:
    cenario: str
    passos: list[Passo] = field(default_factory=list)
    decisao: str = ""
    elo_usado: str | None = None
    conta_usada: str | None = None
    codigos: list[str] = field(default_factory=list)   # siglas gravadas no ledger, na ordem


def novo_ledger(cfg: dict[str, Any]) -> ql.QuotaLedger:
    return ql.QuotaLedger(cfg.get("providers") or {}, tempfile.mkdtemp(prefix="rota-sim-"), cfg.get("neuron_rates") or {})


def simular_turno(cfg: dict[str, Any], brain: str, roteador: dict[str, EstadoProvedor], upstream: Callable[[str, Conta], tuple[int, str]],
                  ledger: ql.QuotaLedger, agora: float, nome: str = "", gateway_ok: bool = True) -> ResultadoTurno:
    """Uma fala da Célia: percorre a fila, tenta cada elo permitido e grava no ledger o que o ChainedLLM gravaria.

    `upstream(provedor_9router, conta) -> (status, texto)`; (200, "") = resposta ok. Gateway fora = status 0."""
    res = ResultadoTurno(cenario=nome)
    for link in chain_links(cfg, brain):
        modelo = link["model"]
        prov_ledger = ledger.provider_of(modelo)
        prov_router = PROVEDOR_9ROUTER.get(prov_ledger, prov_ledger)
        ok, porque = ledger.can_use(modelo, brain)
        if not ok:
            res.passos.append(Passo(modelo, f"pulado antes de chamar: {porque}"))
            continue
        alvo = modelo.split("/", 1)[1] if "/" in modelo else modelo
        rot = roteador.get(prov_router)
        if not gateway_ok:                                   # conexão recusada: falha antes de o 9router escolher conta
            res.passos.append(Passo(modelo, "gateway :20127 recusou a conexão (nenhuma conta foi sequer consultada)"))
            codigo, pausa, msg = ql.classify_error(ConnectionError("Connection refused"))
            ledger.record(brain, modelo, codigo, in_chars=3000, message=msg, pause_sec=pausa)
            res.codigos.append(codigo)
            res.passos.append(Passo(modelo, f"projeto classifica {codigo} ({ql.CODES.get(codigo, codigo)}), pausa {_dur(pausa)} só neste elo"))
            continue
        if rot is None:
            res.passos.append(Passo(modelo, "sem contas deste provedor no roteador simulado → 404 'No active credentials'"))
            codigo, pausa, msg = ql.classify_error(f"[404]: No active credentials for provider: {prov_router}")
        else:
            sim = simular_chat_9router(rot.contas, alvo, lambda c: upstream(prov_router, c), rot.estrategia, rot.sticky, agora)
            vistas = "; ".join(f"{t['conta']}→{t['resultado']}" + (f" (lock {t['cooldown_s']}s)" if "cooldown_s" in t else "") for t in sim["tentativas"])
            if sim["resultado"] == "ok":
                res.passos.append(Passo(modelo, f"9router: {vistas} → atende pela conta {sim['conta']}"))
                ledger.record(brain, modelo, "OK", in_chars=3000, out_chars=200)
                res.codigos.append("OK")
                res.elo_usado, res.conta_usada = modelo, sim["conta"]
                res.decisao = f"DECISÃO: {prov_ledger} {sim['conta']} → modelo {modelo}"
                for c in sim["contas"]:                      # o 9router persiste os bloqueios: reflete no estado simulado
                    rot.contas[[x.id for x in rot.contas].index(c.id)] = c
                return res
            if sim["resultado"] == "sem_credenciais":
                texto, status = f"No active credentials for provider: {prov_router}", 404
            elif sim["resultado"] == "todas_bloqueadas":
                status, texto = sim["status_final"], f"[{prov_router}/{alvo}] {sim['erro_final']} (reset after {_dur(sim['retry_after_s'])})"
            else:
                status, texto = sim["status_final"], sim["erro_final"]
            res.passos.append(Passo(modelo, f"9router: {vistas or 'nenhuma tentativa'} → {sim['resultado']} → devolve {status}"))
            for c in sim["contas"]:
                rot.contas[[x.id for x in rot.contas].index(c.id)] = c
            codigo, pausa, msg = ql.classify_error(f"[{status}]: {texto}" if not texto.startswith("[") else texto)
        ledger.record(brain, modelo, codigo, in_chars=3000, message=msg, pause_sec=pausa)
        res.codigos.append(codigo)
        res.passos.append(Passo(modelo, f"projeto classifica {codigo} ({ql.CODES.get(codigo, codigo)})"
                                + (f", pausa {_dur(pausa)}" if pausa else ", sem prazo: até o reset diário / liberação manual")))
    ledger.record(brain, "", "FIM", message="simulação: todos os elos falharam")
    res.codigos.append("FIM")
    res.decisao = "DECISÃO: nenhum elo respondeu → a Célia devolve 'todos os provedores da fila falharam' (PERSONA_UNAVAILABLE)"
    return res


# ──────────────────────────────────────────────────────────────────────────────
# Cenários pedidos
# ──────────────────────────────────────────────────────────────────────────────
def _base(agora: float) -> dict[str, EstadoProvedor]:
    """6 contas Cloudflare + 4 OpenRouter + 6 Gemini + 7 Groq, todas livres. Estratégia do Cloudflare = a real (round-robin, sticky 3)."""
    return {
        "cloudflare-ai": estado("cloudflare-ai", contas(CF_NOMES, agora, usadas_em={CF_NOMES[0]: 60}), "round-robin", 3),
        "openrouter": estado("openrouter", contas([f"or{i}" for i in range(1, 5)], agora), "fill-first"),
        "gemini": estado("gemini", contas([f"g{i}" for i in range(1, 7)], agora), "round-robin", 3),
        "groq": estado("groq", contas([f"q{i}" for i in range(1, 8)], agora), "fill-first"),
    }


def _por_nome(regras: dict[str, tuple[int, str]], padrao: tuple[int, str] = UPSTREAM_OK) -> Callable[[Conta], tuple[int, str]]:
    return lambda conta: regras.get(conta.nome, padrao)


def cenarios() -> dict[str, dict[str, Any]]:
    """nome → {descricao, montar(agora) -> (roteador, upstream, ledger_ops)}. `ledger_ops` = [(modelo, sigla, pausa_s | None)]."""
    cf = CF_NOMES

    def cf_resp(regras: dict[str, tuple[int, str]], padrao=UPSTREAM_OK):
        por_conta = _por_nome(regras, padrao)
        return lambda prov, conta: por_conta(conta) if prov == "cloudflare-ai" else UPSTREAM_OK

    def tudo_cf_429():
        return cf_resp({}, UPSTREAM_CF_429)

    def mk(descricao, resp, ledger_ops=(), pre=None, gateway_ok=True):
        def montar(agora):
            rot = _base(agora)
            if pre:
                pre(rot, agora)
            return {"roteador": rot, "upstream": resp, "ops": list(ledger_ops), "gateway_ok": gateway_ok}
        return {"descricao": descricao, "montar": montar}

    def so_tres_cf(rot, agora):
        rot["cloudflare-ai"] = estado("cloudflare-ai", contas(cf[:3], agora), "fill-first")

    def a_ja_bloqueada(rot, agora):
        c = rot["cloudflare-ai"].contas[0]
        c.locks["@cf/meta/llama-3.3-70b-instruct-fp8-fast"] = _iso(agora + 64)
        c.test_status, c.error_code, c.backoff_level, c.last_error = "unavailable", 429, 6, "[429]: AiError: you have used up your daily free allocation"

    def or_todas_bloqueadas(rot, agora):
        for c in rot["openrouter"].contas:
            c.locks["__all"] = _iso(agora + 3600)
            c.test_status, c.error_code, c.last_error = "unavailable", 429, "[429]: free-models-per-day"

    return {
        "a429-b-livre": mk("conta A → 429; conta B livre ⇒ o 9router cai para B e atende", cf_resp({cf[0]: UPSTREAM_CF_429}),
                           pre=lambda rot, ag: rot["cloudflare-ai"].contas.__setitem__(slice(None), contas(cf, ag))),
        "a429-b-livre-c429": mk("conta A → 429; B livre; C → 429 (3 contas, fill-first) ⇒ A falha, B atende, C nem é tocada",
                                cf_resp({cf[0]: UPSTREAM_CF_429, cf[2]: UPSTREAM_CF_429}), pre=so_tres_cf),
        "todas-cf-429": mk("as 6 contas Cloudflare → 429 diário ⇒ 6 tentativas, 9router devolve 429, projeto grava CTD e cai para o OpenRouter",
                           tudo_cf_429()),
        "cloudflare-bloqueado": mk("Cloudflare já está em CTD no ledger do projeto ⇒ os 2 elos cf/ são pulados sem chamar ninguém", cf_resp({}),
                                   ledger_ops=[("cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast", "CTD", None)]),
        "cloudflare-sem-contas": mk("nenhuma conta Cloudflare ativa no 9router ⇒ 404 'No active credentials' ⇒ projeto grava MRT (manual!)", cf_resp({}),
                                    pre=lambda rot, ag: rot.pop("cloudflare-ai")),
        "openrouter-ok": mk("Cloudflare esgotada, OpenRouter livre ⇒ decisão no OpenRouter", tudo_cf_429()),
        "openrouter-fora": mk("Cloudflare e OpenRouter esgotados ⇒ Gemini atende",
                              lambda prov, conta: UPSTREAM_CF_429 if prov == "cloudflare-ai" else (UPSTREAM_OR_429 if prov == "openrouter" else UPSTREAM_OK),
                              pre=or_todas_bloqueadas),
        "gemini-ok": mk("Cloudflare e OpenRouter fora, Gemini disponível ⇒ decisão no Gemini",
                        lambda prov, conta: UPSTREAM_CF_429 if prov == "cloudflare-ai" else (UPSTREAM_OR_429 if prov == "openrouter" else UPSTREAM_OK)),
        "tudo-fora": mk("Cloudflare, OpenRouter e Gemini fora ⇒ FIM",
                        lambda prov, conta: UPSTREAM_CF_429 if prov == "cloudflare-ai" else (UPSTREAM_OR_429 if prov == "openrouter" else UPSTREAM_503)),
        "gateway-fora": mk("gateway 9router fora do ar (conexão recusada) em todos os elos ⇒ ERR de 30 s em cada elo, FIM",
                           lambda prov, conta: UPSTREAM_OK, gateway_ok=False),
        "recuperacao-cooldown": mk("conta A em cooldown (64 s) e depois do prazo volta a ser candidata (relógio simulado +65 s)",
                                   cf_resp({}), pre=a_ja_bloqueada),
    }


def rodar_cenario(nome: str, cfg: dict[str, Any], brain: str = "celia", agora: float | None = None) -> ResultadoTurno:
    agora = time.time() if agora is None else agora
    definicao = cenarios()[nome]
    m = definicao["montar"](agora)
    with relogio(agora):
        ledger = novo_ledger(cfg)
        for modelo, sigla, pausa in m["ops"]:
            ledger.record(brain, modelo, sigla, message="pré-condição do cenário", pause_sec=pausa)
        return simular_turno(cfg, brain, m["roteador"], m["upstream"], ledger, agora, nome, m["gateway_ok"])
