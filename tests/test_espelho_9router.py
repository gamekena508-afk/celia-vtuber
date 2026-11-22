"""Espelho do consumo real do 9router, testado SEM gastar cota: banco SQLite falso, nenhuma rede, nenhum modelo, nenhum segredo real.

O que o Pai pediu (2026-10-08) e onde está provado:
  - o projeto enxerga o consumo REAL do roteador, não só o do processo da Célia ........ test_usage_*, test_painel_*
  - a fila pula o elo esgotado ANTES de gastar chamada .................................. test_fila_*
  - 9 cenários: conta livre, em cooldown, cota diária esgotada, 6 esgotadas, bloqueio do projeto,
    OpenRouter livre, OpenRouter esgotado, Gemini livre, todos indisponíveis .............. test_cenario_*
  - segurança: nenhum segredo sai; o banco do roteador não é alterado ...................... test_seguranca_*
Cada detector tem controle POSITIVO (dispara) e NEGATIVO (fica quieto): a fronteira de 97% e o "5 de 6" existem por isso.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pytest
import yaml

from celia.agent.stateless_llm.chained_llm import PERSONA_UNAVAILABLE, ChainedLLM
from celia.core import espelho_9router as esp
from celia.core import quota_ledger as ql
from celia.core import rota_debug as rd
from celia.core.model_chain import chain_links
from celia.core.rota_cenarios import CF_NOMES, relogio

ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load((ROOT / "config" / "model_chains.yaml").read_text(encoding="utf-8"))
AGORA = rd._epoch("2026-10-08T12:00:00.000Z")           # 09:00 em Brasília; o período da cota começou 00:00 UTC
M33 = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
M31 = "@cf/meta/llama-3.1-70b-instruct-fp8-fast"
CF33, CF31 = "cf/" + M33, "cf/" + M31
OR1 = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
GEM = "gemini/gemini-3-flash-preview"
SEGREDOS = ("SEGREDO-FALSO-123", "dono@exemplo.com", "TOKEN-FALSO", "ACCT-FALSO")
LIMITE = 10000.0


@pytest.fixture(autouse=True)
def _relogio():
    with relogio(AGORA):
        yield


def tokens_para(neurons: float) -> int:
    """completionTokens que valem `neurons` na tabela (204.805 neurons por milhão de tokens de saída)."""
    return int(round(neurons * 1_000_000 / 204805.0))


def monta_banco(tmp_path: Path, *, cf: dict[str, float] | None = None, locks: dict[str, dict[str, str]] | None = None,
                n_or: int = 4, n_gem: int = 3, inativas: tuple[str, ...] = (), cf_depois_da_sessao: dict[str, float] | None = None,
                sessao: float | None = None, esquema_quebrado: bool = False) -> Path:
    """Banco no esquema do 9router. `cf[nome]` = neurons já gastos hoje; `locks[nome]` = {modelo: ISO de expiração} (nome de conta qualquer provedor)."""
    db = tmp_path / "data.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""
      create table providerConnections(id text, provider text, authType text, name text, email text, priority integer, isActive integer, data text, createdAt text, updatedAt text);
      create table settings(id integer, data text);
      create table usageHistory(id integer primary key, timestamp text, provider text, model text, connectionId text, apiKey text, endpoint text,
                                promptTokens integer, completionTokens integer, cost real, status text, tokens text, meta text);""")
    if esquema_quebrado:
        con.execute("drop table usageHistory")
    seg = {"apiKey": "SEGREDO-FALSO-123", "accessToken": "TOKEN-FALSO", "providerSpecificData": {"accountId": "ACCT-FALSO"}}
    locks = locks or {}

    def conta(cid, provedor, nome, prio, ativa=1):
        extra = {"testStatus": "active", "backoffLevel": 0}
        for modelo, ate in (locks.get(nome) or {}).items():
            extra["modelLock_" + modelo] = ate
        con.execute("insert into providerConnections values (?,?,?,?,?,?,?,?,?,?)",
                    (cid, provedor, "apikey", nome, "dono@exemplo.com", prio, 0 if nome in inativas else ativa, json.dumps({**seg, **extra}), "", ""))

    for i, nome in enumerate(CF_NOMES, 1):
        conta(f"cf-{i}", "cloudflare-ai", nome, i)
    for i in range(1, n_or + 1):
        conta(f"or-{i}", "openrouter", f"or{i}", i)
    for i in range(1, n_gem + 1):
        conta(f"gm-{i}", "gemini", f"gm{i}", i)
    con.execute("insert into settings values (1, ?)", (json.dumps({"providerStrategies": {"cloudflare-ai": {"fallbackStrategy": "round-robin", "stickyRoundRobinLimit": 3}}}),))
    if not esquema_quebrado:
        def uso(cid, provedor, modelo, neurons, ts):
            con.execute("insert into usageHistory (timestamp, provider, model, connectionId, apiKey, endpoint, promptTokens, completionTokens, status) values "
                        "(?,?,?,?,'SEGREDO-FALSO-123','/v1/chat/completions',0,?,'ok')", (ts, provedor, modelo, cid, tokens_para(neurons)))

        for i, nome in enumerate(CF_NOMES, 1):
            if (cf or {}).get(nome):
                uso(f"cf-{i}", "cloudflare-ai", M33, cf[nome], "2026-10-08T03:00:00.000Z")
            if (cf_depois_da_sessao or {}).get(nome):
                uso(f"cf-{i}", "cloudflare-ai", M33, cf_depois_da_sessao[nome], "2026-10-08T11:00:00.000Z")
        uso("cf-1", "cloudflare-ai", M33, 5000.0, "2026-10-07T20:00:00.000Z")        # ontem: não conta no período de hoje
    con.commit()
    con.close()
    return db


def providers() -> dict:
    return copy.deepcopy(CFG["providers"])


def mk_ledger(tmp_path: Path, db: Path | None, provs: dict | None = None) -> ql.QuotaLedger:
    return ql.QuotaLedger(provs or providers(), tmp_path / "cota", CFG["neuron_rates"], router_db=db, permitidos=set(CF_NOMES))


def todas(n: float) -> dict[str, float]:
    return {nome: n for nome in CF_NOMES}


def em(seg: float) -> str:
    return rd._iso(AGORA + seg)


# ───────────────────────────────── 9 cenários pedidos ─────────────────────────────────
def test_cenario_1_conta_disponivel(tmp_path):
    led = mk_ledger(tmp_path, monta_banco(tmp_path, cf=todas(2000.0)))
    assert led.can_use(CF33, "celia") == (True, "")
    assert led.can_use(CF31, "celia") == (True, "")


def test_cenario_2_conta_em_cooldown(tmp_path):
    # uma conta travada para o modelo, cinco livres ⇒ passa (controle negativo)
    led = mk_ledger(tmp_path, monta_banco(tmp_path, cf=todas(2000.0), locks={CF_NOMES[0]: {M33: em(120)}}))
    assert led.can_use(CF33, "celia")[0] is True
    # as seis travadas ⇒ pula, com o fim do cooldown mais curto (controle positivo)
    locks = {nome: {M33: em(60 + 30 * i)} for i, nome in enumerate(CF_NOMES)}
    led = mk_ledger(tmp_path / "b", monta_banco(_dir(tmp_path, "b"), cf=todas(2000.0), locks=locks))
    ok, why = led.can_use(CF33, "celia")
    assert ok is False and "6 contas" in why and "cooldown" in why and "1m" in why
    assert led.can_use(CF31, "celia")[0] is True                        # o lock é POR MODELO: o outro Llama segue livre


def _dir(base: Path, nome: str) -> Path:
    d = base / nome
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_cenario_3_uma_conta_com_cota_diaria_esgotada_nao_derruba_o_provedor(tmp_path):
    cf = todas(3000.0)
    cf[CF_NOMES[2]] = 10400.0
    led = mk_ledger(tmp_path, monta_banco(tmp_path, cf=cf))
    assert led.can_use(CF33, "celia")[0] is True
    r = led.resumo_roteador("cloudflare")
    assert [c["nome"] for c in r["por_conta"] if c["neurons"] >= LIMITE] == [CF_NOMES[2]]


def test_cenario_4_as_seis_esgotadas_pula_os_dois_elos_cloudflare(tmp_path):
    led = mk_ledger(tmp_path, monta_banco(tmp_path, cf=todas(10300.0)))
    led.espelho.diagnostico = True               # veredito cru: sem a janela de re-sonda
    for brain in ("jev", "celia"):
        for elo in (CF33, CF31):
            ok, why = led.can_use(elo, brain)
            assert ok is False, (brain, elo)
            assert "6/6 contas esgotadas" in why and "61.800/60.000 neurons" in why and "renova" in why, why
    # o motivo vem do 9router, não de um contador local que está em zero
    assert led.usage("cloudflare")["local"] == 0.0


def test_cenario_5_cloudflare_bloqueado_pelo_projeto_nao_consulta_o_roteador(tmp_path, monkeypatch):
    led = mk_ledger(tmp_path, monta_banco(tmp_path, cf=todas(100.0)))
    led.record("celia", CF33, "CTD", message="daily free allocation")
    monkeypatch.setattr(led.espelho, "ler", lambda *a, **k: (_ for _ in ()).throw(AssertionError("consultou o roteador sem precisar")))
    ok, why = led.can_use(CF31, "celia")
    assert ok is False and "CTD no provedor cloudflare" in why


def test_cenario_6_openrouter_disponivel(tmp_path):
    led = mk_ledger(tmp_path, monta_banco(tmp_path))
    assert led.can_use(OR1, "celia") == (True, "")


def test_cenario_7_openrouter_esgotado_todas_as_conexoes_travadas(tmp_path):
    alvo = "nvidia/nemotron-3-super-120b-a12b:free"
    locks = {f"or{i}": {alvo: em(3600)} for i in range(1, 5)}
    led = mk_ledger(tmp_path, monta_banco(tmp_path, locks=locks))
    ok, why = led.can_use(OR1, "celia")
    assert ok is False and "4 contas" in why and "cooldown" in why
    locks.pop("or3")                                                    # controle negativo: uma conexão livre ⇒ passa
    led = mk_ledger(_dir(tmp_path, "b"), monta_banco(_dir(tmp_path, "b"), locks=locks))
    assert led.can_use(OR1, "celia")[0] is True


def test_cenario_8_gemini_disponivel_mesmo_com_cloudflare_e_openrouter_fora(tmp_path):
    alvo = "nvidia/nemotron-3-super-120b-a12b:free"
    led = mk_ledger(tmp_path, monta_banco(tmp_path, cf=todas(10300.0), locks={f"or{i}": {alvo: em(3600)} for i in range(1, 5)}))
    led.espelho.diagnostico = True                                      # veredito cru (sem a sonda)
    assert led.can_use(CF33, "jev")[0] is False and led.can_use(OR1, "celia")[0] is False
    assert led.can_use(GEM, "celia") == (True, "")


def test_cenario_9_todos_indisponiveis_a_fila_nao_chama_ninguem(tmp_path):
    """Cloudflare esgotada, OpenRouter e Gemini com todas as conexões travadas ⇒ FIM, e nenhum elo recebeu uma chamada."""
    locks = {}
    for l in chain_links(CFG, "celia"):
        alvo = l["model"].split("/", 1)[1]
        if l["model"].startswith("openrouter/"):
            locks.update({f"or{i}": {**locks.get(f"or{i}", {}), alvo: em(3600)} for i in range(1, 5)})
        if l["model"].startswith("gemini/"):
            locks.update({f"gm{i}": {**locks.get(f"gm{i}", {}), alvo: em(3600)} for i in range(1, 4)})
    led = mk_ledger(tmp_path, monta_banco(tmp_path, cf=todas(10300.0), locks=locks))
    chain = ChainedLLM("celia", CFG, led)
    chamadas = []
    for link in chain._links:
        async def gen(messages, system=None, tools=None, _m=link.model):
            chamadas.append(_m)
            yield "não devia ser chamado"
        link.llm.chat_completion = gen
    saida = asyncio.run(_coleta(chain))
    assert saida == [PERSONA_UNAVAILABLE] and chamadas == []
    eventos = [json.loads(x)["code"] for f in led.log_dir.glob("2*.jsonl") for x in f.read_text().splitlines()]
    assert eventos == ["FIM"]


async def _coleta(chain):
    return [ev async for ev in chain.chat_completion([{"role": "user", "content": "oi"}], "sys")]


# ───────────────────────────────── a fila pula sem desperdiçar chamada ─────────────────────────────────
def test_fila_pula_cloudflare_e_so_o_primeiro_elo_livre_recebe_a_chamada(tmp_path):
    led = mk_ledger(tmp_path, monta_banco(tmp_path, cf=todas(10300.0)))
    chain = ChainedLLM("celia", CFG, led)
    chamadas = []
    for link in chain._links:
        async def gen(messages, system=None, tools=None, _m=link.model):
            chamadas.append(_m)
            yield "[teasing] oi"
        link.llm.chat_completion = gen
    assert asyncio.run(_coleta(chain)) == ["[teasing] oi"]
    assert chamadas == [OR1]                                     # nem uma chamada ao Cloudflare: zero 429 desperdiçado
    assert not any(m.startswith("cf/") for m in chamadas)


def test_fila_sem_espelho_se_comporta_como_antes(tmp_path):
    """Compatibilidade: ledger criado sem `router_db` ignora o bloco router_mirror (testes antigos e cenários secos seguem iguais)."""
    led = mk_ledger(tmp_path, None)
    assert led.espelho is None and led.can_use(CF33, "jev") == (True, "")


# ───────────────────────────────── a fronteira da margem (controles positivo e negativo) ─────────────────────────────────
@pytest.mark.parametrize("neurons, bloqueia", [(9600.0, False), (9690.0, False), (9710.0, True), (10500.0, True)])   # fronteira = 9.700 (97% de 10.000)
def test_margem_de_97_por_cento_todas_as_contas(tmp_path, neurons, bloqueia):
    esp_ = esp.EspelhoRoteador(monta_banco(tmp_path, cf=todas(neurons)), {"cloudflare": CFG["providers"]["cloudflare"]["router_mirror"]})
    esp_.diagnostico = True
    v = esp_.avaliar("cloudflare", CF33, AGORA, AGORA + 43200)
    assert v.bloqueado is bloqueia


def test_fronteira_exata_da_margem_igual_bloqueia(tmp_path):
    """neurons == margem × limite (sem arredondamento de token): igual BLOQUEIA; um token a menos NÃO. Mata a troca de >= por >."""
    def banco(completion):
        db = monta_banco(_dir(tmp_path, f"f{completion}"))
        con = sqlite3.connect(db)
        for i in range(1, 7):
            con.execute("insert into usageHistory (timestamp, provider, model, connectionId, promptTokens, completionTokens, status) values "
                        "('2026-10-08T03:00:00.000Z','cloudflare-ai',?,?,0,?,'ok')", (M33, f"cf-{i}", completion))
        con.commit(); con.close()
        return db
    cfg = {"provider": "cloudflare-ai", "account_limit": 204805.0, "exhausted_margin": 1.0, "reprobe_sec": 0}
    exato = esp.EspelhoRoteador(banco(1_000_000), {"cloudflare": cfg})                # 1.000.000 × 204.805 / 1e6 = 204.805 neurons == limite
    abaixo = esp.EspelhoRoteador(banco(999_999), {"cloudflare": cfg})
    assert exato.avaliar("cloudflare", CF33, AGORA).bloqueado is True
    assert abaixo.avaliar("cloudflare", CF33, AGORA).bloqueado is False


def test_cinco_de_seis_esgotadas_nao_bloqueia(tmp_path):
    cf = todas(10400.0)
    cf[CF_NOMES[5]] = 1000.0
    e = esp.EspelhoRoteador(monta_banco(tmp_path, cf=cf), {"cloudflare": CFG["providers"]["cloudflare"]["router_mirror"]})
    assert e.avaliar("cloudflare", CF33, AGORA).bloqueado is False


def test_conta_inativa_nao_entra_na_conta(tmp_path):
    """Só as ativas contam: 5 ativas esgotadas + 1 desativada ⇒ esgotado (o roteador não usa a desativada)."""
    cf = todas(10400.0)
    cf[CF_NOMES[5]] = 0.0
    e = esp.EspelhoRoteador(monta_banco(tmp_path, cf=cf, inativas=(CF_NOMES[5],)), {"cloudflare": CFG["providers"]["cloudflare"]["router_mirror"]})
    v = e.avaliar("cloudflare", CF33, AGORA)
    assert v.bloqueado and "5/5" in v.motivo


def test_uso_de_ontem_nao_conta_hoje(tmp_path):
    e = esp.EspelhoRoteador(monta_banco(tmp_path, cf=todas(100.0)), {"cloudflare": CFG["providers"]["cloudflare"]["router_mirror"]})
    r = e.resumo("cloudflare", AGORA)
    assert r["usado_total"] == pytest.approx(600.0, rel=0.01)           # as 5.000 neurons de ontem ficaram de fora


# ───────────────────────────────── re-sonda: a estimativa pode estar alta ─────────────────────────────────
def espelho_esgotado(tmp_path, reprobe=900):
    return esp.EspelhoRoteador(monta_banco(tmp_path, cf=todas(10400.0)),
                               {"cloudflare": {**CFG["providers"]["cloudflare"]["router_mirror"], "reprobe_sec": reprobe}})


def test_resonda_concede_uma_chamada_por_intervalo(tmp_path):
    e = espelho_esgotado(tmp_path)
    assert e.avaliar("cloudflare", CF33, AGORA).bloqueado is True                     # o veredito é puro e não muda por causa da sonda
    assert e.conceder_resonda("cloudflare", AGORA) is True                            # a 1ª passa para o dado real conferir
    assert e.conceder_resonda("cloudflare", AGORA + 5) is False                       # dentro do intervalo, não
    assert e.conceder_resonda("cloudflare", AGORA + 899) is False
    assert e.conceder_resonda("cloudflare", AGORA + 901) is True                      # passado o intervalo, uma nova sonda
    assert e.avaliar("cloudflare", CF33, AGORA + 902).bloqueado is True


def test_resonda_zero_nunca_concede(tmp_path):
    e = espelho_esgotado(tmp_path, reprobe=0)
    assert not any(e.conceder_resonda("cloudflare", AGORA + t) for t in (0, 10, 5000))


def test_painel_nao_gasta_a_resonda(tmp_path):
    e = espelho_esgotado(tmp_path)
    e.diagnostico = True
    assert all(e.conceder_resonda("cloudflare", AGORA + t) is False for t in (0, 1000)) and e.resumo("cloudflare", AGORA)["veredito"]["bloqueado"] is True
    e.diagnostico = False
    assert e.resumo("cloudflare", AGORA)["veredito"]["proxima_resonda"] == AGORA       # a leitura de painel nem encostou na concessão
    assert e.conceder_resonda("cloudflare", AGORA) is True                            # e ela continuava inteira para o processo real


def test_ledger_resonda_o_jev_passa_uma_vez_e_a_celia_nao_queima_a_concessao(tmp_path):
    led = mk_ledger(tmp_path, monta_banco(tmp_path, cf=todas(10400.0)))
    ok, why = led.can_use(CF33, "celia")                                              # a reserva do Jev barraria de qualquer jeito
    assert ok is False and "6/6 contas esgotadas" in why                              # o motivo mostrado é o do roteador (o mais informativo)
    assert led.can_use(CF33, "jev") == (True, "")                                     # a concessão estava intacta: o Jev usa a sonda
    ok, why = led.can_use(CF31, "jev")
    assert ok is False and "6/6 contas esgotadas" in why                              # só uma por intervalo, valendo para o provedor inteiro
    with relogio(AGORA + 901):
        assert led.can_use(CF31, "jev") == (True, "")                                 # intervalo vencido: nova sonda


def test_ledger_sonda_que_da_certo_nao_trava_nada(tmp_path):
    """Se a sonda responder, o ledger só registra OK: nenhum bloqueio novo aparece por causa do espelho."""
    led = mk_ledger(tmp_path, monta_banco(tmp_path, cf=todas(10400.0)))
    assert led.can_use(CF33, "jev")[0] is True
    led.record("jev", CF33, "OK", in_chars=3000, out_chars=300)
    assert led.snapshot()["providers"]["cloudflare"]["blocked_code"] == ""


# ───────────────────────────────── fail-open: sem leitura, nada muda ─────────────────────────────────
def test_banco_ausente_libera_como_antes(tmp_path):
    led = mk_ledger(tmp_path, tmp_path / "nao-existe.sqlite")
    assert [led.can_use(CF33, "jev") for _ in range(3)] == [(True, "")] * 3        # 3 seguidas: a re-sonda só cobriria a 1ª
    assert led.usage("cloudflare")["fonte"] == "local"
    assert led.resumo_roteador("cloudflare")["erro"]


def test_esquema_diferente_libera_como_antes(tmp_path):
    led = mk_ledger(tmp_path, monta_banco(tmp_path, cf=todas(10400.0), esquema_quebrado=True))
    assert [led.can_use(CF33, "jev") for _ in range(3)] == [(True, "")] * 3


# ───────────────────────────────── o contador do projeto deixa de ser cego ─────────────────────────────────
def test_usage_reflete_o_roteador_e_nao_soma_com_o_local(tmp_path):
    led = mk_ledger(tmp_path, monta_banco(tmp_path, cf=todas(10300.0)))
    uso = led.usage("cloudflare")
    assert uso["fonte"] == "9router" and uso["local"] == 0.0 and uso["used"] == pytest.approx(61800.0, rel=0.01)
    led.record("celia", CF33, "OK", in_chars=3000, out_chars=100_000)                  # chamada do próprio processo: o roteador JÁ a contou
    depois = led.usage("cloudflare")
    assert depois["local"] > 5000                                                      # grande o bastante: soma (≈68.6 mil) ≠ máximo (61.800)
    assert depois["used"] == pytest.approx(61800.0, rel=0.01)                          # não vira 61.800 + local


def test_usage_sem_espelho_continua_local(tmp_path):
    led = mk_ledger(tmp_path, None)
    led.record("celia", CF33, "OK", in_chars=3000, out_chars=300)
    assert led.usage("cloudflare")["fonte"] == "local"


def test_aviso_de_cota_usa_o_numero_real(tmp_path):
    from celia.core import avisos

    led = mk_ledger(tmp_path, monta_banco(tmp_path, cf=todas(8500.0)))                 # 51.000/60.000 = 85%
    texto = avisos.texto_cota(led, "cloudflare", "p80")
    assert "51.000 de 60.000 neurons" in texto, texto           # o número do roteador, não os 0 do contador local


def test_painel_mostra_fonte_carimbo_antes_e_sessao_e_divergencia(tmp_path):
    sessao = rd._epoch("2026-10-08T10:30:00.000Z")
    db = monta_banco(tmp_path, cf=todas(2000.0), cf_depois_da_sessao=todas(8400.0))
    e = esp.EspelhoRoteador(db, {"cloudflare": CFG["providers"]["cloudflare"]["router_mirror"]}, sessao_inicio=sessao, permitidos=set(CF_NOMES))
    r = e.resumo("cloudflare", AGORA, AGORA + 43200)
    assert r["chamadas_total"] == 12 and r["chamadas_sessao"] == 6
    assert r["usado_total"] == pytest.approx(62400.0, rel=0.01) and r["neurons_sessao"] == pytest.approx(50400.0, rel=0.01)
    texto = "\n".join(esp.linhas_resumo(r, AGORA, local_usado=0.0, reset_em=AGORA + 43200, rotulo="cloudflare"))
    for trecho in ("lido às", "somente leitura", "BLOQUEADO (ESGOTADA)", "antes da sessão", "da sessão: 6 chamadas", "DIVERGÊNCIA", "re-sonda", "renova"):
        assert trecho in texto, trecho
    sem = esp.EspelhoRoteador(db, {"cloudflare": CFG["providers"]["cloudflare"]["router_mirror"]}, sessao_inicio=None).resumo("cloudflare", AGORA)
    assert sem["sessao_conhecida"] is False and sem["chamadas_sessao"] == 0
    assert "sessão desconhecida" in "\n".join(esp.linhas_resumo(sem, AGORA))


def test_consumo_por_modelo_e_por_conta_batem_com_o_banco(tmp_path):
    db = monta_banco(tmp_path, cf={CF_NOMES[0]: 1000.0, CF_NOMES[1]: 2500.0})
    con = sqlite3.connect(db)
    con.execute("insert into usageHistory (timestamp, provider, model, connectionId, promptTokens, completionTokens, status) values "
                "('2026-10-08T05:00:00.000Z','cloudflare-ai',?, 'cf-3', 1000, 0, 'ok')", (M31,))
    con.commit(); con.close()
    r = esp.EspelhoRoteador(db, {"cloudflare": CFG["providers"]["cloudflare"]["router_mirror"]}, permitidos=set(CF_NOMES)).resumo("cloudflare", AGORA)
    por_modelo = {m["modelo"]: m for m in r["por_modelo"]}
    assert por_modelo[M33]["chamadas"] == 2 and por_modelo[M31]["chamadas"] == 1
    assert por_modelo[M31]["neurons"] == pytest.approx(1000 * 26668 / 1e6)
    por_conta = {c["nome"]: c for c in r["por_conta"]}
    assert por_conta[CF_NOMES[1]]["neurons"] == pytest.approx(2500.0, rel=0.01) and por_conta[CF_NOMES[2]]["chamadas"] == 1


def test_cache_le_o_banco_uma_vez_por_janela(tmp_path, monkeypatch):
    e = esp.EspelhoRoteador(monta_banco(tmp_path, cf=todas(100.0)), {"cloudflare": {**CFG["providers"]["cloudflare"]["router_mirror"], "cache_sec": 20}})
    leituras = []
    original = e._fotografar
    monkeypatch.setattr(e, "_fotografar", lambda *a, **k: leituras.append(1) or original(*a, **k))
    for t in (0, 3, 19):
        e.ler("cloudflare", AGORA + t)
    assert len(leituras) == 1                                   # dentro da janela: uma leitura só
    e.ler("cloudflare", AGORA + 21)
    assert len(leituras) == 2                                   # passada a janela: lê de novo (a foto é carimbada, não eterna)


# ───────────────────────────────── segurança ─────────────────────────────────
def test_seguranca_sql_do_espelho_nao_toca_coluna_secreta():
    for sql in esp.TODAS_AS_SQL:
        for proibido in ("apiKey", "accessToken", "refreshToken", "idToken", "email", "providerSpecificData", "endpoint", "meta", "tokens,"):
            assert proibido not in sql, proibido
    codigo = Path(esp.__file__).read_text(encoding="utf-8")
    assert not re.search(r"select\s+\*", codigo, re.I)


def test_seguranca_nenhum_segredo_sai_e_o_banco_nao_muda(tmp_path):
    db = monta_banco(tmp_path, cf=todas(10400.0), locks={CF_NOMES[0]: {M33: em(60)}})
    antes = hashlib.sha256(db.read_bytes()).hexdigest()
    led = mk_ledger(tmp_path, db)
    led.can_use(CF33, "jev"); led.can_use(OR1, "celia")
    r = led.resumo_roteador("cloudflare")
    saida = repr(r) + "\n".join(esp.linhas_resumo(r, AGORA, local_usado=0.0)) + repr(led.usage("cloudflare")) + json.dumps(led.snapshot(), default=str)
    for segredo in SEGREDOS:
        assert segredo not in saida, segredo
    assert hashlib.sha256(db.read_bytes()).hexdigest() == antes                        # leitura não escreve
    assert not list(tmp_path.glob("data.sqlite-*"))                                    # nem journal/WAL deixado para trás


def test_seguranca_conta_fora_da_lista_sai_mascarada(tmp_path):
    db = monta_banco(tmp_path, cf=todas(100.0))
    e = esp.EspelhoRoteador(db, {"cloudflare": CFG["providers"]["cloudflare"]["router_mirror"]}, permitidos={CF_NOMES[0]})
    nomes = [c["nome"] for c in e.resumo("cloudflare", AGORA)["por_conta"]]
    assert CF_NOMES[0] in nomes and all(n.startswith("conta #") for n in nomes if n != CF_NOMES[0])


def test_sessao_gravada_e_desde_local(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"session_started_at": 1791459000.0}), encoding="utf-8")
    assert esp.sessao_gravada(tmp_path) == 1791459000.0
    assert esp.sessao_gravada(tmp_path / "x") is None
    (tmp_path / "y").mkdir(); (tmp_path / "y" / "state.json").write_text("{}", encoding="utf-8")
    assert esp.sessao_gravada(tmp_path / "y") is None
    assert esp.desde_local("2026-10-08 07:30") is not None and esp.desde_local("07:30") is not None and esp.desde_local("lixo") is None


# ───────────────────────────────── portão de saldo para baterias (celia-cota pronto) ─────────────────────────────────
def _resumo(tmp_path, cf):
    e = esp.EspelhoRoteador(monta_banco(tmp_path, cf=cf), {"cloudflare": CFG["providers"]["cloudflare"]["router_mirror"]}, permitidos=set(CF_NOMES))
    e.diagnostico = True
    return e.resumo("cloudflare", AGORA, AGORA + 43200)


def test_pronto_com_saldo_suficiente(tmp_path):
    ok, txt = esp.saldo_para(_resumo(tmp_path, todas(2000.0)), 7000.0)           # 6 × 8.000 = 48.000 de saldo
    assert ok is True and "48.000" in txt and "6 conta(s)" in txt


def test_nao_roda_quando_o_roteador_diz_esgotado(tmp_path):
    ok, txt = esp.saldo_para(_resumo(tmp_path, todas(10300.0)), 100.0)
    assert ok is False and "6/6 contas esgotadas" in txt


def test_nao_roda_quando_o_saldo_e_menor_que_o_pedido_mesmo_sem_bloqueio(tmp_path):
    cf = todas(9000.0)                                                            # nenhuma ≥ 97%, mas só 6 × 1.000 = 6.000 de saldo
    ok, txt = esp.saldo_para(_resumo(tmp_path, cf), 7000.0)
    assert ok is False and "6.000" in txt and "7.000" in txt
    assert esp.saldo_para(_resumo(_dir(tmp_path, "b"), cf), 5000.0)[0] is True    # controle: pedindo menos, passa


def test_sem_leitura_nao_afirma_que_ha_saldo(tmp_path):
    e = esp.EspelhoRoteador(tmp_path / "nao-existe.sqlite", {"cloudflare": CFG["providers"]["cloudflare"]["router_mirror"]})
    ok, txt = esp.saldo_para(e.resumo("cloudflare", AGORA), 1.0)
    assert ok is False and "não afirmo" in txt
    assert esp.saldo_para(None, 1.0)[0] is False
