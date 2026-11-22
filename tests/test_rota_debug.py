"""Roteamento de modelos testado SEM gastar cota: nenhuma chamada de rede, nenhum modelo, nenhum segredo.

Cobre: (1) espelho do cooldown/seleção do 9router, inclusive PARIDADE contra o JavaScript original (node); (2) leitor somente leitura do
banco do roteador com segredos falsos plantados; (3) classificação de erros do projeto; (4) os cenários pedidos pelo Pai: 429 em uma
conta, em várias, todas, Cloudflare inteiro fora, OpenRouter, Gemini, gateway fora e recuperação após cooldown.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from celia.core import quota_ledger as ql
from celia.core import rota_debug as rd
from celia.core.rota_cenarios import CF_NOMES, cenarios, contas, relogio, rodar_cenario
from celia.core.rota_debug import Conta, EstadoProvedor

ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load((ROOT / "config" / "model_chains.yaml").read_text(encoding="utf-8"))
JS_DIR = Path(os.environ.get("ROTEADOR_9ROUTER_DIR") or (Path.home() / "Documents" / "Automacoes" / "Proxies" / "9router"))
T0 = 1_791_459_000.0
M33 = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
SEGREDOS = ("SEGREDO-FALSO-123", "dono@exemplo.com", "ACCT-FALSO", "TOKEN-FALSO")
CF_DIARIA = '[429]: {"errors":[{"message":"AiError: AiError: you have used up your daily free allocation of 10,000 neurons"}]}'


# ───────────────────────── 1) cooldown / backoff ─────────────────────────
def test_backoff_dobra_ate_o_teto_de_5_minutos():
    assert [rd.quota_cooldown_ms(n) // 1000 for n in range(1, 10)] == [2, 4, 8, 16, 32, 64, 128, 256, 300]
    assert rd.quota_cooldown_ms(15) == 300_000


def test_niveis_observados_no_log_real_batem():
    """Log do 9router (08/10): locks de 64 s, 128 s e 256 s com backoffLevel 6, 7 e 8 no banco."""
    assert (rd.quota_cooldown_ms(6), rd.quota_cooldown_ms(7), rd.quota_cooldown_ms(8)) == (64_000, 128_000, 256_000)


@pytest.mark.parametrize("status, texto, nivel, esperado", [
    (429, CF_DIARIA, 0, (2000, 1)),                      # a mensagem real do Cloudflare não casa regra de TEXTO; cai na regra de status 429
    (429, "rate limit", 5, (64_000, 6)),
    (429, "Too Many Requests", 0, (2000, 1)),
    (429, "x", 15, (300_000, 15)),                       # nível máximo não passa de 15
    (404, "No active credentials for provider: x", 0, (120_000, None)),
    (401, "", 0, (120_000, None)),
    (403, "", 0, (120_000, None)),
    (400, "bad request", 0, (30_000, None)),             # 400 também derruba para a próxima conta, com 30 s de bloqueio
    (500, "boom", 3, (30_000, None)),
    (None, "", 0, (30_000, None)),
])
def test_checar_erro_regras(status, texto, nivel, esperado):
    assert rd.checar_erro(status, texto, nivel) == esperado


@pytest.mark.skipif(shutil.which("node") is None or not (JS_DIR / "open-sse/services/accountFallback.js").exists(),
                    reason="node ou o código do 9router não estão disponíveis")
def test_paridade_com_o_javascript_original_do_9router():
    """O espelho em Python tem de dar o MESMO cooldown e nível que `checkFallbackError` do 9router, numa grade de entradas."""
    statuses = [None, 400, 401, 402, 403, 404, 429, 500, 503]
    textos = ["", "boom", "rate limit hit", "Too Many Requests", "no credentials", "quota exceeded", "capacity", "overloaded",
              "request not allowed", "improperly formed request", CF_DIARIA, "[429]: free-models-per-day"]
    niveis = [0, 1, 2, 5, 6, 14, 15, 16]
    grade = [(s, t, n) for s in statuses for t in textos for n in niveis]
    uri = (JS_DIR / "open-sse/services/accountFallback.js").as_uri()
    script = (f'import {{ checkFallbackError }} from "{uri}";\n'
              f'const g = {json.dumps(grade)};\n'
              'console.log(JSON.stringify(g.map(([s,t,n]) => { const r = checkFallbackError(s,t,n); return [r.cooldownMs, r.newBackoffLevel ?? null]; })));')
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-400:]
    js = json.loads(out.stdout.strip().splitlines()[-1])
    py = [[rd.checar_erro(s, t, n)[0], rd.checar_erro(s, t, n)[1]] for s, t, n in grade]
    diferentes = [(g, a, b) for g, a, b in zip(grade, js, py) if a != b]
    assert not diferentes, f"{len(diferentes)} divergências, ex.: {diferentes[:3]}"
    assert len(grade) == 9 * 12 * 8


# ───────────────────────── 2) seleção de conta (espelho de auth.js) ─────────────────────────
def mk(n=3, **kw):
    return contas([f"c{i}" for i in range(1, n + 1)], T0, **kw)


def test_fill_first_pega_a_primeira_por_prioridade_e_pula_bloqueada():
    cs = mk(3)
    c, _ = rd.escolher_conta(cs, M33, set(), "fill-first", 3, T0)
    assert c.nome == "c1"
    cs[0].locks[M33] = rd._iso(T0 + 60)
    c, _ = rd.escolher_conta(cs, M33, set(), "fill-first", 3, T0)
    assert c.nome == "c2"
    c, _ = rd.escolher_conta(cs, M33, {cs[1].id}, "fill-first", 3, T0)
    assert c.nome == "c3"


def test_bloqueio_e_por_modelo_nao_por_conta():
    cs = mk(2)
    cs[0].locks["@cf/meta/llama-3.1-70b-instruct-fp8-fast"] = rd._iso(T0 + 60)
    c, _ = rd.escolher_conta(cs, M33, set(), "fill-first", 3, T0)
    assert c.nome == "c1"                                  # lock de OUTRO modelo não afeta este
    cs[0].locks[rd.MODEL_LOCK_ALL] = rd._iso(T0 + 60)
    c, _ = rd.escolher_conta(cs, M33, set(), "fill-first", 3, T0)
    assert c.nome == "c2"                                  # lock __all vale para qualquer modelo


def test_quirk_do_js_lock_do_modelo_expirado_nao_cai_para_o_all():
    """`connection[key] || connection[ALL]`: chave do modelo (mesmo expirada) tem precedência sobre `__all` ativo."""
    c = mk(1)[0]
    c.locks[M33] = rd._iso(T0 - 5)
    c.locks[rd.MODEL_LOCK_ALL] = rd._iso(T0 + 600)
    assert rd._lock_ativo(c, M33, T0) is False
    assert rd._lock_ativo(c, "outro-modelo", T0) is True


def test_round_robin_fica_na_mais_recente_ate_o_sticky_e_depois_troca_para_a_mais_antiga():
    cs = mk(3, usadas_em={"c1": 30, "c2": 20, "c3": 10})   # c3 = mais recente
    c, info = rd.escolher_conta(cs, M33, set(), "round-robin", 3, T0)
    assert c.nome == "c3" and "fica na mais recente" in info["motivo"]
    cs[2].consecutive = 3                                  # estourou o sticky
    c, info = rd.escolher_conta(cs, M33, set(), "round-robin", 3, T0)
    assert c.nome == "c1" and "menos recente" in info["motivo"]


def test_round_robin_sem_historico_segue_a_prioridade():
    c, _ = rd.escolher_conta(mk(3), M33, set(), "round-robin", 3, T0)
    assert c.nome == "c1"


def test_todas_bloqueadas_devolve_retry_mais_cedo_e_o_ultimo_erro_da_primeira_a_vencer():
    cs = mk(3)
    for i, c in enumerate(cs):
        c.locks[M33] = rd._iso(T0 + 30 * (i + 1))
        c.last_error, c.error_code = f"erro-{i}", 429
    c, info = rd.escolher_conta(cs, M33, set(), "round-robin", 3, T0)
    assert c is None and info["motivo"] == "todas_bloqueadas"
    assert info["retry_after"] == pytest.approx(T0 + 30) and info["ultimo_erro"] == "erro-0" and info["ultimo_codigo"] == 429


def test_sem_conta_ativa_e_sem_credenciais_e_nao_confunde_com_bloqueio():
    cs = mk(2)
    for c in cs:
        c.ativa = False
    c, info = rd.escolher_conta(cs, M33, set(), "fill-first", 3, T0)
    assert c is None and info["motivo"] == "sem_credenciais"


# ───────────────────────── laço do chat.js ─────────────────────────
def laco(cs, regras, **kw):
    return rd.simular_chat_9router(cs, M33, lambda c: regras.get(c.nome, (200, "")), kw.get("estrategia", "fill-first"), 3, kw.get("agora", T0))


def test_laco_429_em_a_cai_para_b_e_atende():
    r = laco(mk(3), {"c1": (429, CF_DIARIA)})
    assert r["resultado"] == "ok" and r["conta"] == "c2"
    assert [t["conta"] for t in r["tentativas"]] == ["c1", "c2"]
    assert r["tentativas"][0]["cooldown_s"] == 2           # 1ª falha = nível 1 = 2 s


def test_laco_a_429_b_livre_c_429_nao_toca_em_c():
    r = laco(mk(3), {"c1": (429, CF_DIARIA), "c3": (429, CF_DIARIA)})
    assert r["conta"] == "c2" and all(t["conta"] != "c3" for t in r["tentativas"])


def test_laco_todas_429_percorre_todas_e_devolve_429_com_retry():
    r = laco(mk(6), {f"c{i}": (429, CF_DIARIA) for i in range(1, 7)})
    assert r["resultado"] == "todas_bloqueadas" and len(r["tentativas"]) == 6
    assert r["status_final"] == 429 and "daily free allocation" in r["erro_final"] and r["retry_after_s"] == 2


def test_laco_backoff_cresce_a_cada_falha_consecutiva():
    cs = mk(1)
    ate = []
    for i in range(4):
        r = rd.simular_chat_9router(cs, M33, lambda c: (429, "x"), "fill-first", 3, T0 + 1000 * i)
        cs = r["contas"]
        ate.append(r["tentativas"][0]["cooldown_s"])
    assert ate == [2, 4, 8, 16]


def test_laco_erro_400_tambem_bloqueia_e_passa_para_a_proxima_conta():
    r = laco(mk(3), {"c1": (400, "bad request"), "c2": (400, "bad request")})
    assert r["conta"] == "c3" and [t["resultado"] for t in r["tentativas"][:2]] == [400, 400]
    assert r["tentativas"][0]["cooldown_s"] == 30          # risco real: um pedido malformado trava 2 contas por 30 s


def test_laco_sucesso_limpa_o_bloqueio_do_modelo():
    cs = mk(1)
    cs[0].locks[M33] = rd._iso(T0 - 1)
    cs[0].test_status, cs[0].backoff_level = "unavailable", 6
    r = laco(cs, {})
    ok = r["contas"][0]
    assert r["resultado"] == "ok" and M33 not in ok.locks and ok.backoff_level == 0 and ok.test_status == "active"


def test_laco_nao_altera_as_contas_originais():
    cs = mk(2)
    antes = json.dumps([c.locks for c in cs])
    laco(cs, {"c1": (429, "x")})
    assert json.dumps([c.locks for c in cs]) == antes


# ───────────────────────── 3) estado da conta ─────────────────────────
def conta_cf(**kw):
    base = dict(id="a" * 12, nome="Cloudflare Conta1", prioridade=1, ativa=True)
    base.update(kw)
    return Conta(**base)


def test_estado_disponivel_cooldown_esgotada_inativa_desconhecida():
    lim = 10000.0
    assert rd.estado_conta(conta_cf(neurons_hoje=2500.0, last_used_at=rd._iso(T0 - 5)), M33, T0, lim)[0] == "DISPONIVEL"
    cd = conta_cf(neurons_hoje=100.0, locks={M33: rd._iso(T0 + 40)}, error_code=503, test_status="unavailable", last_error="[503]: oops")
    assert rd.estado_conta(cd, M33, T0, lim)[0] == "COOLDOWN"
    es = conta_cf(neurons_hoje=10100.0, locks={M33: rd._iso(T0 + 40)}, test_status="unavailable", last_error=CF_DIARIA, error_code=429)
    assert rd.estado_conta(es, M33, T0, lim)[0] == "ESGOTADA"
    assert rd.estado_conta(conta_cf(ativa=False), M33, T0, lim)[0] == "INATIVA"
    assert rd.estado_conta(conta_cf(), M33, T0, None)[0] == "DESCONHECIDA"


def test_estado_esgotada_provavel_quando_o_lock_expirou_mas_o_consumo_medido_estourou():
    c = conta_cf(neurons_hoje=10400.0, locks={M33: rd._iso(T0 - 10)})
    assert rd.estado_conta(c, M33, T0, 10000.0)[0] == "ESGOTADA_PROVAVEL"


def test_controle_negativo_conta_com_saldo_nao_e_marcada_esgotada():
    assert rd.estado_conta(conta_cf(neurons_hoje=9000.0, last_used_at=rd._iso(T0 - 1)), M33, T0, 10000.0)[0] == "DISPONIVEL"


# ───────────────────────── 4) leitor read-only do banco do 9router ─────────────────────────
def banco_falso(tmp_path: Path, esquema_quebrado=False) -> Path:
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
    linhas = [
        ("id-A", "Cloudflare Principal", 1, 1, {"testStatus": "unavailable", "errorCode": 429, "backoffLevel": 6, "lastUsedAt": "2026-10-08T11:29:18.000Z",
                                               "consecutiveUseCount": 2, "lastError": CF_DIARIA, "modelLock_" + M33: "2026-10-08T11:30:22.000Z"}),
        ("id-B", "Cloudflare Conta1", 2, 1, {"testStatus": "active", "backoffLevel": 0}),
        ("id-C", "Cloudflare Conta2", 3, 0, {}),
    ]
    for cid, nome, prio, ativa, extra in linhas:
        con.execute("insert into providerConnections values (?,?,?,?,?,?,?,?,?,?)",
                    (cid, "cloudflare-ai", "apikey", nome, "dono@exemplo.com", prio, ativa, json.dumps({**seg, **extra}), "", ""))
    con.execute("insert into providerConnections values ('o1','openrouter','apikey','minha conta pessoal','dono@exemplo.com',1,1,?, '', '')",
                (json.dumps({**seg, "testStatus": "active"}),))
    con.execute("insert into settings values (1, ?)", (json.dumps({"providerStrategies": {"cloudflare-ai": {"fallbackStrategy": "round-robin", "stickyRoundRobinLimit": 3}},
                                                                  "ccFilterNaming": False}),))
    if not esquema_quebrado:
        for cid, p, c in (("id-A", 1000, 100), ("id-A", 3000, 50), ("id-B", 100000, 10000)):
            con.execute("insert into usageHistory (timestamp, provider, model, connectionId, apiKey, endpoint, promptTokens, completionTokens, status) values "
                        "('2026-10-08T10:00:00.000Z','cloudflare-ai',?,?,'SEGREDO-FALSO-123','/v1/chat/completions',?,?,'ok')", (M33, cid, p, c))
        con.execute("insert into usageHistory (timestamp, provider, model, connectionId, promptTokens, completionTokens, status) values "
                    "('2026-10-07T20:00:00.000Z','cloudflare-ai',?,'id-B',999999,0,'ok')", (M33,))   # ontem: não conta hoje
    con.commit()
    con.close()
    return db


AGORA_BANCO = 1_791_459_000.0 + 0  # 2026-10-08 11:30 UTC aprox.


def test_leitor_le_contas_locks_estrategia_e_consumo_medido(tmp_path):
    agora = rd._epoch("2026-10-08T11:31:00.000Z")
    est = rd.ler_provedor(banco_falso(tmp_path), "cloudflare-ai", agora=agora)
    assert est.erro is None and [c.nome for c in est.contas] == ["Cloudflare Principal", "Cloudflare Conta1", "Cloudflare Conta2"]
    assert est.estrategia == "round-robin" and est.sticky == 3          # chave com hífen lida de providerStrategies."cloudflare-ai"
    a, b, c = est.contas
    assert a.backoff_level == 6 and a.error_code == 429 and a.locks == {M33: "2026-10-08T11:30:22.000Z"} and a.ativa
    assert c.ativa is False
    # neurons = tokens × tabela: A = (4000×26668 + 150×204805)/1e6 ; B = (100000×26668 + 10000×204805)/1e6 ; ontem fora
    assert a.neurons_hoje == pytest.approx((4000 * 26668 + 150 * 204805) / 1e6) and a.chamadas_hoje == 2
    assert b.neurons_hoje == pytest.approx((100000 * 26668 + 10000 * 204805) / 1e6)
    assert c.neurons_hoje == 0.0 and c.chamadas_hoje == 0


def test_leitor_nunca_devolve_nem_imprime_segredos(tmp_path):
    db = banco_falso(tmp_path)
    agora = rd._epoch("2026-10-08T11:31:00.000Z")
    est = rd.ler_provedor(db, "cloudflare-ai", agora=agora)
    outros = rd.ler_provedor(db, "openrouter", agora=agora)
    blob = repr(est) + repr(outros) + repr(rd.ultimas_chamadas_ok(db, "cloudflare-ai", 5))
    cf = {"cloudflare-ai": est, "openrouter": outros}
    cfg_links = [{"model": "cf/" + M33}, {"model": "openrouter/x:free"}]
    led = ql.QuotaLedger(CFG["providers"], tmp_path / "cota", CFG["neuron_rates"])
    fila = rd.decidir_fila(cfg_links, "celia", led, cf, agora, set(CF_NOMES))
    texto = rd.formatar("teste", "Célia", fila, cf, led.snapshot(), agora, M33, set(CF_NOMES))
    for segredo in SEGREDOS:
        assert segredo not in blob and segredo not in texto, f"vazou {segredo}"
    assert "minha conta pessoal" not in texto                 # nome livre de conta não-Cloudflare sai mascarado
    assert "conta #1 [o1]" in texto


def test_controle_positivo_o_detector_de_segredo_pega_vazamento():
    assert any(s in "chave SEGREDO-FALSO-123 aqui" for s in SEGREDOS)         # o teste acima TEM de falhar se um segredo aparecer


def test_nenhum_sql_do_modulo_toca_colunas_secretas():
    proibidos = re.compile(r"apiKey|accessToken|refreshToken|idToken|\bemail\b|providerSpecificData|password|secret", re.I)
    for sql in rd.TODAS_AS_SQL:
        achados = proibidos.findall(sql)
        assert not achados, f"SQL lê campo sensível: {achados}\n{sql}"
    assert proibidos.search("select json_extract(data,'$.apiKey') from providerConnections")  # controle positivo do verificador


def test_leitor_e_somente_leitura(tmp_path):
    db = banco_falso(tmp_path)
    con = rd._abrir_ro(db)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("insert into settings values (2, '{}')")
    with pytest.raises(sqlite3.OperationalError):
        con.execute("update providerConnections set name = 'x'")
    con.close()
    antes = hashlib.sha256(db.read_bytes()).hexdigest()
    rd.ler_provedor(db, "cloudflare-ai")
    assert hashlib.sha256(db.read_bytes()).hexdigest() == antes


def test_leitor_falha_com_mensagem_clara_sem_excecao(tmp_path):
    assert "não abriu o banco" in rd.ler_provedor(tmp_path / "nao-existe.sqlite", "cloudflare-ai").erro
    assert "esquema" in rd.ler_provedor(banco_falso(tmp_path, esquema_quebrado=True), "cloudflare-ai").erro


def test_inicio_do_periodo_respeita_a_hora_de_reset():
    assert rd.inicio_do_periodo(rd._epoch("2026-10-08T11:30:00Z"), 0) == "2026-10-08T00:00:00.000Z"
    assert rd.inicio_do_periodo(rd._epoch("2026-10-08T03:00:00Z"), 7) == "2026-10-07T07:00:00.000Z"   # antes do reset das 07: período de ontem


def test_eventos_do_log_so_devolve_linhas_de_fallback_e_corta_chaves(tmp_path):
    log = tmp_path / "server.log"
    log.write_text("[08:29:17] ⚪ ▶ POST cf/x → corpo secreto SEGREDO-FALSO-123\n"
                   "[08:29:17] ⚠️  [FALLBACK] ⇄ ACC:Cloudflare Conta2 UNAVAILABLE (429) → NEXT ACCOUNT\n"
                   "[08:29:18] ⚠️  [AUTH] cloudflare-ai | all 6 accounts locked for X (reset after 18s) | lastError=[429]: {\"errors\":SEGREDO-FALSO-123\n", encoding="utf-8")
    ev = rd.eventos_do_log(log)
    assert len(ev) == 2 and "Conta2" in ev[0] and "all 6 accounts locked" in ev[1]
    assert not any("SEGREDO" in e for e in ev)
    assert rd.eventos_do_log(tmp_path / "nao-existe.log") == []


# ───────────────────────── 5) classificação do projeto ─────────────────────────
def test_classify_429_diario_do_cloudflare_vira_ctd_ate_o_reset():
    cod, pausa, _ = ql.classify_error(f"[cloudflare-ai/{M33}] {CF_DIARIA} (reset after 3s)")
    assert (cod, pausa) == ("CTD", None)


def test_classify_mensagem_truncada_em_100_chars_ainda_e_ctd():
    """auth.js grava lastError com 100 chars; 'daily' ainda cabe, então o caminho 'tudo já bloqueado' também vira CTD."""
    truncada = CF_DIARIA[:100]
    assert "daily" in truncada.lower()
    assert ql.classify_error(f"[cloudflare-ai/{M33}] {truncada} (reset after 28s)")[0] == "CTD"


def test_classify_429_sem_palavra_diaria_vira_ctm_com_o_prazo_do_roteador():
    cod, pausa, _ = ql.classify_error("[openrouter/x] [429]: slow down (reset after 28s)")
    assert (cod, pausa) == ("CTM", 28.0)


def test_classify_outros_tipos_de_erro():
    assert ql.classify_error("[404]: No active credentials for provider: cloudflare-ai")[0] == "MRT"     # modelo morto, pausa manual
    assert ql.classify_error("[401]: bad key")[0] == "CHV"
    assert ql.classify_error("[402]: payment required")[0] == "SLD"
    assert ql.classify_error(ConnectionError("Connection refused")) [:2] == ("ERR", 30.0)
    assert ql.classify_error("[400]: invalid")[:2] == ("ERR", ql.ERR_400_PAUSE_SEC)


# ───────────────────────── 6) cenários pedidos (modo seco, sem cota) ─────────────────────────
def correr(nome):
    return rodar_cenario(nome, CFG, "celia", T0)


def test_cenario_a_429_b_livre():
    r = correr("a429-b-livre")
    assert r.codigos == ["OK"] and r.elo_usado == "cf/" + M33 and r.conta_usada == "Cloudflare Conta1"
    assert "Cloudflare Principal→429" in r.passos[0].texto


def test_cenario_a_429_b_livre_c_429_atende_por_b_e_nao_toca_c():
    r = correr("a429-b-livre-c429")
    assert r.codigos == ["OK"] and r.conta_usada == "Cloudflare Conta1"
    assert "Conta2" not in r.passos[0].texto


def test_cenario_todas_as_contas_429_cai_para_openrouter_com_ctd_no_cloudflare():
    r = correr("todas-cf-429")
    assert r.codigos == ["CTD", "OK"] and r.elo_usado.startswith("openrouter/")
    pulados = [p.texto for p in r.passos if "pulado antes de chamar" in p.texto]
    assert any("CTD no provedor cloudflare" in t for t in pulados)           # o 2º elo cf/ nem é tentado
    assert sum("Cloudflare Conta" in p.texto and "→429" in p.texto for p in r.passos) >= 1


def test_cenario_cloudflare_inteiro_bloqueado_no_ledger_pula_sem_chamar():
    r = correr("cloudflare-bloqueado")
    # o CTD é pré-condição (gravado antes da fala): na fala só se grava o OK do OpenRouter
    assert r.codigos == ["OK"] and r.elo_usado.startswith("openrouter/")
    cf = [p for p in r.passos if p.elo.startswith("cf/")]
    assert len(cf) == 2 and all("pulado antes de chamar: CTD no provedor cloudflare" in p.texto for p in cf)
    assert not any("9router:" in p.texto for p in cf)                       # nenhuma conta Cloudflare foi consultada


def test_cenario_sem_contas_cloudflare_vira_mrt_nos_dois_elos_e_cai_para_openrouter():
    r = correr("cloudflare-sem-contas")
    assert r.codigos[:2] == ["MRT", "MRT"] and r.codigos[-1] == "OK"         # MRT é por ELO (e manual): risco documentado
    assert r.elo_usado.startswith("openrouter/")


def test_cenario_openrouter_disponivel():
    r = correr("openrouter-ok")
    assert r.codigos == ["CTD", "OK"]
    assert r.elo_usado == "openrouter/nvidia/nemotron-3-super-120b-a12b:free" and r.conta_usada == "or1"
    assert r.decisao == "DECISÃO: openrouter-free or1 → modelo openrouter/nvidia/nemotron-3-super-120b-a12b:free"


def test_cenario_openrouter_indisponivel_cai_para_gemini():
    r = correr("openrouter-fora")
    assert r.codigos == ["CTD", "CTD", "OK"] and r.elo_usado == "gemini/gemini-3-flash-preview"


def test_cenario_gemini_disponivel_apos_cloudflare_e_openrouter_falharem():
    r = correr("gemini-ok")
    assert r.elo_usado.startswith("gemini/") and r.codigos[-1] == "OK" and r.codigos.count("CTD") == 2


def test_cenario_tudo_fora_termina_em_fim():
    r = correr("tudo-fora")
    assert r.codigos == ["CTD", "CTD", "ERR", "FIM"]          # cf (diária), openrouter (diária), gemini 503 (erro do elo), fim
    assert r.elo_usado is None and r.conta_usada is None and "nenhum elo respondeu" in r.decisao


def test_cenario_gateway_fora_gera_err_de_30s_em_cada_elo_e_fim():
    r = correr("gateway-fora")
    n_elos = len(CFG["celia"]["chain"])
    assert r.codigos == ["ERR"] * n_elos + ["FIM"]
    assert sum("recusou a conexão" in p.texto for p in r.passos) == n_elos      # nenhuma conta foi consultada em nenhum elo
    assert not any("9router:" in p.texto for p in r.passos)


def test_cenario_recuperacao_apos_cooldown_da_conta():
    """A está bloqueada por 64 s: no instante 0 outra conta atende; 65 s depois A volta a ser candidata."""
    rot = {"cloudflare-ai": EstadoProvedor("cloudflare-ai", contas(CF_NOMES, T0, usadas_em={CF_NOMES[0]: 60}), "round-robin", 3)}
    a = rot["cloudflare-ai"].contas[0]
    a.locks[M33] = rd._iso(T0 + 64)
    c0, _ = rd.escolher_conta(rot["cloudflare-ai"].contas, M33, set(), "round-robin", 3, T0)
    assert c0.nome != a.nome
    assert rd.estado_conta(a, M33, T0, 10000.0)[0] == "COOLDOWN"
    c1, _ = rd.escolher_conta(rot["cloudflare-ai"].contas, M33, set(), "fill-first", 3, T0 + 65)
    assert c1.nome == a.nome                                   # lock venceu: A é a primeira por prioridade de novo
    assert rd.estado_conta(a, M33, T0 + 65, 10000.0)[0] != "COOLDOWN"


def test_recuperacao_do_ctd_no_reset_diario_do_ledger():
    led = ql.QuotaLedger(CFG["providers"], __import__("tempfile").mkdtemp(prefix="rota-t-"), CFG["neuron_rates"])
    modelo = "cf/" + M33
    with relogio(T0):
        led.record("celia", modelo, "CTD", message="daily")
        assert led.can_use(modelo, "celia")[0] is False
        reset = led.next_reset("cloudflare", T0)
    with relogio(reset - 1):
        assert led.can_use(modelo, "celia")[0] is False
    with relogio(reset + 1):
        assert led.can_use(modelo, "celia")[0] is True        # virou o dia da cota: o bloqueio diário solta sozinho


def test_simulacoes_nao_tocam_no_estado_real_da_cota():
    real = ROOT / "var" / "cota" / "state.json"
    antes = hashlib.sha256(real.read_bytes()).hexdigest() if real.exists() else None
    for nome in cenarios():
        correr(nome)
    depois = hashlib.sha256(real.read_bytes()).hexdigest() if real.exists() else None
    assert antes == depois


def test_todos_os_cenarios_pedidos_existem():
    pedidos = {"a429-b-livre", "a429-b-livre-c429", "todas-cf-429", "cloudflare-bloqueado", "openrouter-ok", "openrouter-fora", "gemini-ok",
               "recuperacao-cooldown"}
    assert pedidos <= set(cenarios())


# ───────────────────────── 7) decisor sobre o estado "real" simulado + CLI ─────────────────────────
def test_decidir_fila_com_cloudflare_esgotada_decide_openrouter_e_avisa_a_divergencia(tmp_path):
    agora = rd._epoch("2026-10-08T11:31:00.000Z")
    cf = EstadoProvedor("cloudflare-ai", [Conta(id=f"i{i}", nome=n, prioridade=i, neurons_hoje=10400.0, chamadas_hoje=100, test_status="unavailable",
                                                 error_code=429, last_error=CF_DIARIA, locks={M33: rd._iso(agora + 20)}) for i, n in enumerate(CF_NOMES, 1)],
                        "round-robin", 3, 10000.0)
    orr = EstadoProvedor("openrouter", contas(["o1", "o2"], agora), "fill-first")
    led = ql.QuotaLedger(CFG["providers"], tmp_path / "cota", CFG["neuron_rates"])
    links = [{"model": "cf/" + M33}, {"model": "openrouter/nvidia/nemotron-3-super-120b-a12b:free"}]
    with relogio(agora):
        fila = rd.decidir_fila(links, "celia", led, {"cloudflare-ai": cf, "openrouter": orr}, agora, set(CF_NOMES))
        texto = rd.formatar("t", "Célia", fila, {"cloudflare-ai": cf, "openrouter": orr}, led.snapshot(), agora, M33, set(CF_NOMES))
    assert fila["elos"][0]["acao"] == "FALHARIA" and "CTD" in fila["elos"][0]["motivo"]
    assert fila["decisao"]["provedor"] == "openrouter-free"
    assert "DIVERGÊNCIA" in texto and "DECISÃO: openrouter-free" in texto and "Próxima tentativa útil" in texto


def test_cli_simular_todos_roda_offline(capsys):
    sys.path.insert(0, str(ROOT / "tools"))
    import celia_rota
    assert celia_rota.main(["simular", "todos"]) == 0
    saida = capsys.readouterr().out
    assert saida.count("DECISÃO") == len(cenarios())
    assert celia_rota.main(["simular", "inexistente"]) == 2
    assert celia_rota.main(["--cerebro", "xx"]) == 2


def test_cli_status_com_banco_falso_nao_vaza_e_nao_chama_modelo(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, str(ROOT / "tools"))
    import celia_rota
    monkeypatch.setenv("CELIA_ROUTER_DB", str(banco_falso(tmp_path)))
    monkeypatch.setenv("CELIA_ROUTER_LOG", str(tmp_path / "sem-log.log"))
    import socket
    def proibido(*a, **k):
        raise AssertionError("o diagnóstico tentou abrir socket de rede")
    monkeypatch.setattr(socket.socket, "connect", proibido)
    assert celia_rota.main(["status"]) == 0
    saida = capsys.readouterr().out
    assert "DECISÃO" in saida and "ROUTE DEBUG" in saida
    for segredo in SEGREDOS:
        assert segredo not in saida
    assert celia_rota.main(["status", "--json"]) == 0
    assert not any(s in capsys.readouterr().out for s in SEGREDOS)
