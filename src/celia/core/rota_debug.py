"""Diagnóstico do roteamento de modelos SEM chamar nenhum modelo (não gasta cota).

Responde, em segundos, "qual conta / modelo / provedor a Célia usaria agora, e por quê":

  Célia → ChainedLLM (fila do projeto, `config/model_chains.yaml`)
        → gateway 9router (`:20127`) → provedor (ex.: `cloudflare-ai`) → CONTA (conexão) → modelo
        → 429/erro → 9router bloqueia (conta, modelo) com backoff e tenta a PRÓXIMA conta
        → todas bloqueadas → 429 com "(reset after Ns)" → ChainedLLM classifica (CTD/CTM/…) e passa ao PRÓXIMO elo/provedor.

Três peças, todas puras ou somente leitura:

1. LEITOR do estado do 9router (`~/.9router/db/data.sqlite`, modo `ro`). Só SQL com `json_extract` de campos NOMEADOS e não
   secretos. Nunca seleciona `apiKey`, `accessToken`, `refreshToken`, `idToken`, `email` nem `providerSpecificData` (há teste que
   falha se esses nomes aparecerem em qualquer SQL deste módulo, e outro que planta segredos falsos num banco de teste).
2. ESPELHO fiel da lógica do 9router (`src/sse/services/auth.js` + `open-sse/services/accountFallback.js`, v0.5.40): escolha de conta
   (fill-first / round-robin com sticky), bloqueio por (conta, modelo), backoff 2 s → 5 min. Validado contra o JS original por teste.
3. DECISOR da fila do projeto: para cada elo, usa o `QuotaLedger` (bloqueios do projeto) + as contas do roteador e diz qual elo/conta
   atenderia, ou por que foi pulado.

Nada aqui escreve no banco do roteador nem no estado real da cota (as simulações usam estado em memória/temporário).
"""

from __future__ import annotations

import functools
import json
import math
import re
import sqlite3
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from .quota_ledger import classify_error

# ──────────────────────────────────────────────────────────────────────────────
# Espelho de open-sse/config/errorConfig.js (9router 0.5.40)
# ──────────────────────────────────────────────────────────────────────────────
BACKOFF_BASE_MS = 2000
BACKOFF_MAX_MS = 5 * 60 * 1000
BACKOFF_MAX_LEVEL = 15
TRANSIENT_COOLDOWN_MS = 30 * 1000
COOLDOWN_LONG_MS = 2 * 60 * 1000
COOLDOWN_SHORT_MS = 5 * 1000
# (tipo, chave, cooldown_ms | None=backoff). Ordem = prioridade: regras de TEXTO primeiro, depois STATUS.
ERROR_RULES: tuple[tuple[str, Any, int | None], ...] = (
    ("text", "no credentials", COOLDOWN_LONG_MS),
    ("text", "request not allowed", COOLDOWN_SHORT_MS),
    ("text", "improperly formed request", COOLDOWN_LONG_MS),
    ("text", "rate limit", None),
    ("text", "too many requests", None),
    ("text", "quota exceeded", None),
    ("text", "capacity", None),
    ("text", "overloaded", None),
    ("status", 401, COOLDOWN_LONG_MS),
    ("status", 402, COOLDOWN_LONG_MS),
    ("status", 403, COOLDOWN_LONG_MS),
    ("status", 404, COOLDOWN_LONG_MS),
    ("status", 429, None),
)
MODEL_LOCK_ALL = "__all"
DAILY_MARK = re.compile(r"daily free allocation|neurons per day|per[- ]?day|\bdaily\b", re.I)

# provedor do ledger (config/model_chains.yaml) → provedor no 9router (tabela providerConnections)
PROVEDOR_9ROUTER = {"cloudflare": "cloudflare-ai", "openrouter-free": "openrouter", "gemini": "gemini", "groq": "groq"}
LIMITE_DIARIO_CONTA = {"cloudflare-ai": 10000.0}  # neurons/dia grátis por conta Cloudflare (Workers AI)
MARGEM_ESGOTADA = 0.97  # consumo medido ≥ 97% do limite ⇒ conta tratada como esgotada


def quota_cooldown_ms(nivel: int) -> int:
    """getQuotaCooldown: nível 1 = 2 s, 2 = 4 s, … teto 5 min."""
    return min(BACKOFF_BASE_MS * (2 ** max(0, nivel - 1)), BACKOFF_MAX_MS)


def checar_erro(status: int | None, texto: str | None, nivel: int = 0) -> tuple[int, int | None]:
    """checkFallbackError: (cooldown_ms, novo_nivel_de_backoff | None). Todo erro cai para a próxima conta."""
    baixo = (texto if isinstance(texto, str) else json.dumps(texto or "")).lower() if texto else ""
    for tipo, chave, cooldown in ERROR_RULES:
        casou = (tipo == "text" and baixo and chave in baixo) or (tipo == "status" and status == chave)
        if not casou:
            continue
        if cooldown is None:
            novo = min(nivel + 1, BACKOFF_MAX_LEVEL)
            return quota_cooldown_ms(novo), novo
        return cooldown, None
    return TRANSIENT_COOLDOWN_MS, None


# ──────────────────────────────────────────────────────────────────────────────
# Modelo de dados
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class Conta:
    id: str
    nome: str
    prioridade: int = 999
    ativa: bool = True
    test_status: str | None = None
    error_code: int | None = None
    backoff_level: int = 0
    last_used_at: str | None = None
    consecutive: int = 0
    last_error_at: str | None = None
    last_error: str | None = None
    locks: dict[str, str] = field(default_factory=dict)   # modelo (ou "__all") → ISO de expiração
    chamadas_hoje: int | None = None                       # medido (usageHistory), None = sem dado
    neurons_hoje: float | None = None


@dataclass
class EstadoProvedor:
    provedor: str                      # id no 9router (ex.: cloudflare-ai)
    contas: list[Conta]
    estrategia: str = "fill-first"
    sticky: int = 3
    limite_diario: float | None = None
    erro: str | None = None            # falha ao ler (banco ausente, esquema diferente…)


def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _hhmmss(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _mil(x: float) -> str:
    """12345 → 12.345 (separador de milhar brasileiro, sem tocar em vírgulas do resto do texto)."""
    return f"{x:,.0f}".replace(",", ".")


def _dur(seg: float) -> str:
    seg = max(0, int(math.ceil(seg)))
    h, resto = divmod(seg, 3600)
    m, s = divmod(resto, 60)
    return (f"{h}h" if h else "") + (f"{m}m" if m else "") + (f"{s}s" if s or not (h or m) else "")


# ──────────────────────────────────────────────────────────────────────────────
# 1) LEITOR somente leitura do banco do 9router — SÓ campos não secretos
# ──────────────────────────────────────────────────────────────────────────────
SQL_CONTAS = """
select id, name, priority, isActive,
       json_extract(data,'$.testStatus'), json_extract(data,'$.errorCode'), json_extract(data,'$.backoffLevel'),
       json_extract(data,'$.lastUsedAt'), json_extract(data,'$.consecutiveUseCount'), json_extract(data,'$.lastErrorAt'),
       substr(json_extract(data,'$.lastError'),1,160)
from providerConnections where provider = ?
"""
SQL_LOCKS = """
select p.id, j.key, j.value from providerConnections p, json_each(p.data) j
where p.provider = ? and j.key like 'modelLock\\_%' escape '\\' and j.value is not null
"""
SQL_ESTRATEGIA = """
select json_extract(data,'$.providerStrategies.\"' || ? || '\".fallbackStrategy'),
       json_extract(data,'$.providerStrategies.\"' || ? || '\".stickyRoundRobinLimit'),
       json_extract(data,'$.fallbackStrategy'), json_extract(data,'$.stickyRoundRobinLimit')
from settings limit 1
"""
SQL_USO = """
select connectionId, count(*), coalesce(sum(promptTokens),0), coalesce(sum(completionTokens),0)
from usageHistory where provider = ? and timestamp >= ? group by connectionId
"""
SQL_ULTIMAS_OK = """
select timestamp, connectionId, model from usageHistory where provider = ? order by timestamp desc limit ?
"""
TODAS_AS_SQL = (SQL_CONTAS, SQL_LOCKS, SQL_ESTRATEGIA, SQL_USO, SQL_ULTIMAS_OK)


def _abrir_ro(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{quote(str(db))}?mode=ro", uri=True, timeout=3)
    con.execute("pragma query_only = on")
    return con


def inicio_do_periodo(agora: float, reset_utc_hour: int = 0) -> str:
    """Início (UTC, ISO) do período diário da cota: o último `reset_utc_hour:00 UTC` antes de `agora`."""
    dt = datetime.fromtimestamp(agora, timezone.utc)
    ini = dt.replace(hour=reset_utc_hour, minute=0, second=0, microsecond=0)
    if ini > dt:
        ini -= timedelta(days=1)
    return _iso(ini.timestamp())


def ler_provedor(db: Path | str, provedor: str, *, agora: float | None = None, reset_utc_hour: int = 0,
                 taxas: dict[str, float] | None = None) -> EstadoProvedor:
    """Contas, estratégia e consumo medido de um provedor do 9router. Falha vira `erro`, nunca exceção."""
    agora = time.time() if agora is None else agora
    db = Path(str(db)).expanduser()
    estado = EstadoProvedor(provedor=provedor, contas=[], limite_diario=LIMITE_DIARIO_CONTA.get(provedor))
    try:
        con = _abrir_ro(db)
    except sqlite3.Error as exc:
        estado.erro = f"não abriu o banco do 9router ({type(exc).__name__})"
        return estado
    try:
        for (cid, nome, prio, ativa, ts, ec, bo, lu, cons, lea, le) in con.execute(SQL_CONTAS, (provedor,)):
            estado.contas.append(Conta(
                id=str(cid), nome=str(nome or cid)[:60], prioridade=int(prio or 999), ativa=bool(ativa), test_status=ts,
                error_code=int(ec) if isinstance(ec, (int, float)) else None, backoff_level=int(bo or 0),
                last_used_at=lu, consecutive=int(cons or 0), last_error_at=lea, last_error=le))
        por_id = {c.id: c for c in estado.contas}
        for cid, chave, valor in con.execute(SQL_LOCKS, (provedor,)):
            if cid in por_id:
                por_id[cid].locks[str(chave)[len("modelLock_"):]] = str(valor)
        sp, ss, sg, sgs = con.execute(SQL_ESTRATEGIA, (provedor, provedor)).fetchone() or (None,) * 4
        estado.estrategia = sp or sg or "fill-first"
        estado.sticky = int(ss or sgs or 3)
        taxas = taxas or {"in": 26668.0, "out": 204805.0}
        for cid, n, p, c in con.execute(SQL_USO, (provedor, inicio_do_periodo(agora, reset_utc_hour))):
            if cid in por_id:
                por_id[cid].chamadas_hoje = int(n)
                por_id[cid].neurons_hoje = (int(p) * taxas["in"] + int(c) * taxas["out"]) / 1_000_000
        for conta in estado.contas:  # conta sem nenhuma linha hoje: uso medido = 0 (há tabela), não "desconhecido"
            if conta.neurons_hoje is None:
                conta.chamadas_hoje, conta.neurons_hoje = 0, 0.0
    except sqlite3.Error as exc:
        estado.erro = f"esquema do 9router diferente do esperado ({type(exc).__name__})"
    finally:
        con.close()
    return estado


def ultimas_chamadas_ok(db: Path | str, provedor: str, n: int = 5) -> list[tuple[str, str, str]]:
    """(timestamp UTC, id da conta, modelo) das últimas chamadas que DERAM CERTO. Falhas (429) só existem no log do roteador."""
    try:
        con = _abrir_ro(Path(str(db)).expanduser())
    except sqlite3.Error:
        return []
    try:
        return [(str(t), str(c), str(m)) for t, c, m in con.execute(SQL_ULTIMAS_OK, (provedor, n))]
    except sqlite3.Error:
        return []
    finally:
        con.close()


RE_LOG = re.compile(r"^\[(\d\d:\d\d:\d\d)\]\s+\S+\s+\[(FALLBACK|AUTH)\]\s+(.*)$")


def eventos_do_log(caminho: Path | str, n: int = 12, max_bytes: int = 400_000) -> list[str]:
    """Últimas linhas FALLBACK/AUTH do server.log do 9router (nomes de conta e modelo; sem corpo de requisição nem chaves)."""
    p = Path(str(caminho)).expanduser()
    try:
        with open(p, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - max_bytes))
            texto = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    saida = []
    for linha in texto.splitlines():
        m = RE_LOG.match(linha)
        if m:
            saida.append(f"{m.group(1)} {m.group(2)}: {re.sub(r'lastError=.*$', '', m.group(3)).strip()[:150]}")
    return saida[-n:]


# ──────────────────────────────────────────────────────────────────────────────
# 2) ESPELHO da seleção de conta do 9router (auth.js · getProviderCredentials)
# ──────────────────────────────────────────────────────────────────────────────
def _lock_ate(conta: Conta, modelo: str | None) -> float | None:
    """isModelLockActive: chave do modelo OU, se ausente, `__all` (o `||` do JS: chave expirada NÃO cai para `__all`)."""
    exp = conta.locks.get(modelo or MODEL_LOCK_ALL) or conta.locks.get(MODEL_LOCK_ALL)
    return _epoch(exp)


def _lock_ativo(conta: Conta, modelo: str | None, agora: float) -> bool:
    t = _lock_ate(conta, modelo)
    return bool(t and t > agora)


def _lock_mais_cedo(conta: Conta, agora: float) -> float | None:
    """getEarliestModelLockUntil: o menor vencimento ativo entre TODOS os modelLock_* da conta."""
    ativos = [t for t in (_epoch(v) for v in conta.locks.values()) if t and t > agora]
    return min(ativos) if ativos else None


def _cmp_recencia(a: Conta, b: Conta) -> int:
    ta, tb = _epoch(a.last_used_at), _epoch(b.last_used_at)
    if ta is None and tb is None:
        return (a.prioridade or 999) - (b.prioridade or 999)
    if ta is None:
        return 1
    if tb is None:
        return -1
    return -1 if tb < ta else (1 if tb > ta else 0)   # mais recente primeiro


def _cmp_antiguidade(a: Conta, b: Conta) -> int:
    ta, tb = _epoch(a.last_used_at), _epoch(b.last_used_at)
    if ta is None and tb is None:
        return (a.prioridade or 999) - (b.prioridade or 999)
    if ta is None:
        return -1
    if tb is None:
        return 1
    return -1 if ta < tb else (1 if ta > tb else 0)   # mais antigo primeiro


def escolher_conta(contas: list[Conta], modelo: str | None, excluidas: set[str], estrategia: str, sticky: int,
                   agora: float, *, mutar: bool = False) -> tuple[Conta | None, dict[str, Any]]:
    """Qual conta o 9router usaria agora. `mutar=True` atualiza lastUsedAt/consecutive como ele faz (só em simulação)."""
    ordenadas = sorted([c for c in contas if c.ativa], key=lambda c: c.prioridade or 999)
    if not ordenadas:
        return None, {"motivo": "sem_credenciais"}
    livres = [c for c in ordenadas if c.id not in excluidas and not _lock_ativo(c, modelo, agora)]
    if not livres:
        travadas = [c for c in ordenadas if _lock_ativo(c, modelo, agora)]
        venc = [t for t in (_lock_mais_cedo(c, agora) for c in travadas) if t]
        if venc:
            # auth.js devolve o lastError/errorCode da conta cujo bloqueio vence primeiro (é o que o cliente lê quando nada foi tentado)
            primeira = min((c for c in travadas if _lock_mais_cedo(c, agora)), key=lambda c: _lock_mais_cedo(c, agora))
            return None, {"motivo": "todas_bloqueadas", "retry_after": min(venc), "contas": [c.nome for c in travadas],
                          "ultimo_erro": primeira.last_error, "ultimo_codigo": primeira.error_code}
        return None, {"motivo": "todas_indisponiveis"}
    if estrategia == "round-robin":
        atual = sorted(livres, key=functools.cmp_to_key(_cmp_recencia))[0]
        if atual.last_used_at and atual.consecutive < sticky:
            escolhida, motivo = atual, f"round-robin: fica na mais recente ({atual.consecutive + 1}/{sticky})"
            if mutar:
                escolhida.consecutive += 1
        else:
            escolhida = sorted(livres, key=functools.cmp_to_key(_cmp_antiguidade))[0]
            motivo = "round-robin: troca para a menos recente"
            if mutar:
                escolhida.consecutive = 1
    else:
        escolhida, motivo = livres[0], "fill-first: primeira por prioridade"
    if mutar:
        escolhida.last_used_at = _iso(agora)
    return escolhida, {"motivo": motivo}


def simular_chat_9router(contas: list[Conta], modelo: str | None, resposta: Callable[[Conta], tuple[int, str]],
                         estrategia: str, sticky: int, agora: float) -> dict[str, Any]:
    """Reproduz o laço de chat.js: tenta conta → erro → bloqueia (conta, modelo) → exclui → próxima, até acabar.

    `resposta(conta)` devolve (status HTTP, texto do erro); status 200 = sucesso. Trabalha numa cópia."""
    cs = [replace(c, locks=dict(c.locks)) for c in contas]
    excluidas: set[str] = set()
    tentativas: list[dict[str, Any]] = []
    ultimo: tuple[int, str] | None = None
    while True:
        conta, info = escolher_conta(cs, modelo, excluidas, estrategia, sticky, agora, mutar=True)
        if conta is None:
            res = {"resultado": info["motivo"], "tentativas": tentativas, "contas": cs,
                   "status_final": ultimo[0] if ultimo else int(info.get("ultimo_codigo") or 503),
                   "erro_final": ultimo[1] if ultimo else (info.get("ultimo_erro") or "Unavailable")}
            if info["motivo"] == "todas_bloqueadas":
                res["retry_after_s"] = max(1, math.ceil(info["retry_after"] - agora))
            return res
        status, texto = resposta(conta)
        if status == 200:
            conta.locks.pop(modelo or MODEL_LOCK_ALL, None)
            conta.test_status, conta.last_error, conta.backoff_level = "active", None, 0
            tentativas.append({"conta": conta.nome, "resultado": "ok"})
            return {"resultado": "ok", "conta": conta.nome, "tentativas": tentativas, "contas": cs}
        cooldown_ms, novo = checar_erro(status, texto, conta.backoff_level)
        conta.locks[modelo or MODEL_LOCK_ALL] = _iso(agora + cooldown_ms / 1000)
        conta.test_status, conta.error_code, conta.last_error = "unavailable", status, (texto or "")[:100]
        if novo is not None:
            conta.backoff_level = novo
        excluidas.add(conta.id)
        ultimo = (status, texto or "")
        tentativas.append({"conta": conta.nome, "resultado": status, "cooldown_s": cooldown_ms // 1000})


# ──────────────────────────────────────────────────────────────────────────────
# Classificação do estado de uma conta (o que o ROUTE DEBUG mostra)
# ──────────────────────────────────────────────────────────────────────────────
def estado_conta(conta: Conta, modelo: str | None, agora: float, limite: float | None = None) -> tuple[str, str]:
    """(código, texto). Códigos: DISPONIVEL, COOLDOWN, ESGOTADA, ESGOTADA_PROVAVEL, INATIVA, DESCONHECIDA."""
    if not conta.ativa:
        return "INATIVA", "desativada no 9router"
    pct = (conta.neurons_hoje / limite) if (limite and conta.neurons_hoje is not None) else None
    diaria = bool(conta.last_error and DAILY_MARK.search(conta.last_error)) and conta.test_status == "unavailable"
    ate = _lock_ate(conta, modelo)
    if ate and ate > agora:
        if diaria or (pct is not None and pct >= MARGEM_ESGOTADA):
            return "ESGOTADA", f"esgotada (cota diária); o roteador re-sonda em {_dur(ate - agora)} (volta de verdade só no reset)"
        return "COOLDOWN", f"em cooldown até {_hhmmss(ate)} (faltam {_dur(ate - agora)}); erro {conta.error_code or '?'}"
    if pct is not None and pct >= MARGEM_ESGOTADA:
        return "ESGOTADA_PROVAVEL", f"consumo medido {pct:.0%} do limite; sem lock ativo, a próxima chamada gasta 1 tentativa 429"
    if diaria:
        return "ESGOTADA_PROVAVEL", "último erro foi cota diária; lock expirou, a próxima chamada re-sonda"
    if pct is None and not conta.last_used_at and not conta.last_error_at:
        return "DESCONHECIDA", "sem uso nem erro registrados"
    resto = f"~{max(0.0, limite - conta.neurons_hoje):,.0f} neurons restantes (medido)".replace(",", ".") if (limite and conta.neurons_hoje is not None) else "sem bloqueio ativo"
    return "DISPONIVEL", resto


# ──────────────────────────────────────────────────────────────────────────────
# 3) DECISOR da fila do projeto (ChainedLLM + QuotaLedger + contas do roteador)
# ──────────────────────────────────────────────────────────────────────────────
def _nome_publico(conta: Conta, permitidos: set[str]) -> str:
    """Nome real só para contas conhecidas (Cloudflare nomeadas na config); as demais saem como #prioridade [id curto]."""
    return conta.nome if conta.nome in permitidos else f"conta #{conta.prioridade} [{conta.id[:6]}]"


def decidir_fila(links: list[dict[str, Any]], brain: str, ledger: Any, roteador: dict[str, EstadoProvedor],
                 agora: float, permitidos: set[str] | None = None) -> dict[str, Any]:
    """Percorre a fila como o ChainedLLM e devolve a decisão. Não faz chamada de rede."""
    permitidos = permitidos or set()
    linhas: list[dict[str, Any]] = []
    decisao: dict[str, Any] | None = None
    for i, link in enumerate(links, 1):
        modelo = link["model"]
        prov = ledger.provider_of(modelo)
        rot = roteador.get(PROVEDOR_9ROUTER.get(prov, ""))
        ok, porque = ledger.can_use(modelo, brain)
        item: dict[str, Any] = {"ordem": i, "modelo": modelo, "provedor": prov, "conta": None, "acao": "", "motivo": ""}
        if not ok:
            item.update(acao="PULA", motivo=f"ledger do projeto: {porque}")
        elif rot is None or rot.erro or not rot.contas:
            item.update(acao="TENTA", motivo="sem leitura do roteador" + (f" ({rot.erro})" if rot and rot.erro else "") + ": segue o ledger")
        else:
            alvo = modelo.split("/", 1)[1] if "/" in modelo else modelo
            conta, info = escolher_conta(rot.contas, alvo, set(), rot.estrategia, rot.sticky, agora)
            if conta is None:
                if info["motivo"] == "todas_bloqueadas":
                    retry = max(1, math.ceil(info["retry_after"] - agora))
                    ult = min((c for c in rot.contas if _lock_mais_cedo(c, agora)), key=lambda c: _lock_mais_cedo(c, agora))
                    codigo, pausa, _ = classify_error(f"[429]: {ult.last_error or ''} (reset after {retry}s)")
                    item.update(acao="FALHARIA", motivo=(f"todas as {len(info['contas'])} contas bloqueadas p/ o modelo; o 9router devolve 429 "
                                                         f"(retry {_dur(retry)}) → projeto classifica {codigo}"
                                                         + (f" e bloqueia {prov} até o reset" if codigo == "CTD" else f" e pausa {prov} por {_dur(pausa or 60)}")))
                else:
                    item.update(acao="FALHARIA", motivo=f"9router sem conta utilizável ({info['motivo']})")
            else:
                st, txt = estado_conta(conta, alvo, agora, rot.limite_diario)
                if st in ("ESGOTADA_PROVAVEL",):
                    item.update(acao="TENTA_E_FALHA", conta=_nome_publico(conta, permitidos),
                                motivo=f"{txt}; o roteador percorre as demais contas antes de devolver 429")
                else:
                    item.update(acao="USA", conta=_nome_publico(conta, permitidos), motivo=info["motivo"])
        linhas.append(item)
        if decisao is None and item["acao"] in ("USA", "TENTA"):
            decisao = item
    return {"cerebro": brain, "elos": linhas, "decisao": decisao}


# ──────────────────────────────────────────────────────────────────────────────
# Formatação — ROUTE DEBUG
# ──────────────────────────────────────────────────────────────────────────────
ROTULO = {"DISPONIVEL": "disponível", "COOLDOWN": "cooldown", "ESGOTADA": "ESGOTADA", "ESGOTADA_PROVAVEL": "esgotada (provável)",
          "INATIVA": "inativa", "DESCONHECIDA": "desconhecida"}


def formatar(titulo: str, cerebro: str, fila: dict[str, Any], roteador: dict[str, EstadoProvedor], ledger_snap: dict[str, Any],
             agora: float, modelo_cf: str | None, permitidos: set[str], eventos: list[str] | None = None) -> str:
    out = [f"ROUTE DEBUG — {titulo}  ({datetime.fromtimestamp(agora).strftime('%d/%m %H:%M:%S')})  [0 chamadas a modelos]",
           f"Cérebro: {cerebro}"]
    cf = roteador.get("cloudflare-ai")
    if cf is not None:
        out.append(f"\nCloudflare  (9router: cloudflare-ai · {len(cf.contas)} contas · estratégia {cf.estrategia}"
                   + (f", sticky {cf.sticky}" if cf.estrategia == "round-robin" else "") + ")")
        if cf.erro:
            out.append(f"  ⚠ {cf.erro}")
        tot = 0.0
        for c in sorted(cf.contas, key=lambda c: c.prioridade):
            cod, txt = estado_conta(c, modelo_cf, agora, cf.limite_diario)
            uso = (f"{_mil(c.neurons_hoje)}/{_mil(cf.limite_diario)} neurons ({c.neurons_hoje / cf.limite_diario:.0%}; {c.chamadas_hoje} chamadas hoje)"
                   if cf.limite_diario and c.neurons_hoje is not None else "uso medido: n/d")
            tot += c.neurons_hoje or 0
            out.append(f"  {_nome_publico(c, permitidos):<22} {ROTULO[cod]:<20} {uso}\n  {'':<22} ↳ {txt}")
        if cf.limite_diario and cf.contas:
            out.append(f"  TOTAL medido hoje: {_mil(tot)} / {_mil(cf.limite_diario * len(cf.contas))} neurons")
        esgotadas = [c for c in cf.contas if estado_conta(c, modelo_cf, agora, cf.limite_diario)[0] in ("ESGOTADA", "ESGOTADA_PROVAVEL")]
        reset = (ledger_snap.get("next_reset") or {}).get("cloudflare")
        if cf.contas and len(esgotadas) == len(cf.contas):
            quando = f"{datetime.fromtimestamp(reset).strftime('%H:%M')} (em {_dur(reset - agora)})" if reset else "no próximo reset diário"
            out.append(f"  Próxima tentativa útil: só depois do reset da cota, {quando}. Antes disso o 9router re-sonda a cada ≤5 min e recebe 429 (não gasta neurons).")
        else:
            alvo, info = escolher_conta(cf.contas, modelo_cf, set(), cf.estrategia, cf.sticky, agora)
            out.append(f"  Próxima chamada deve usar: {_nome_publico(alvo, permitidos) if alvo else '— nenhuma conta serve agora'}"
                       + (f"  ({info['motivo']})" if alvo else f"  ({info.get('motivo')})"))
        prov = (ledger_snap.get("providers") or {}).get("cloudflare", {})
        usado = prov.get("used", 0.0)
        if cf.limite_diario and tot and abs(tot - usado) > 0.2 * max(tot, 1):
            out.append(f"  ⚠ DIVERGÊNCIA: o contador do projeto marca {_mil(usado)} neurons; o roteador mediu {_mil(tot)}. "
                       "O contador só vê chamadas feitas por este processo da Célia (replay/testes/outros agentes ficam de fora).")
    out.append("\nFila (ordem real do ChainedLLM)")
    for e in fila["elos"]:
        marca = {"USA": "✔ usa", "TENTA": "✔ tenta", "PULA": "⤼ pula", "FALHARIA": "✗ falharia", "TENTA_E_FALHA": "✗ tenta e falha"}[e["acao"]]
        conta = f" [{e['conta']}]" if e["conta"] else ""
        out.append(f"  {e['ordem']}. {e['modelo']:<52}{marca}{conta}\n       ↳ {e['motivo']}")
    outros = [p for p in ("openrouter", "gemini", "groq") if p in roteador]
    if outros:
        out.append("\nFallback (provedores depois do Cloudflare)")
        nomes = {"openrouter": "OpenRouter", "gemini": "Gemini", "groq": "Groq"}
        for p in outros:
            r = roteador[p]
            livres = [c for c in r.contas if c.ativa and not any(_lock_ativo(c, m, agora) for m in list(c.locks) or [None])]
            led = (ledger_snap.get("providers") or {}).get({v: k for k, v in PROVEDOR_9ROUTER.items()}.get(p, ""), {})
            bloq_led = led.get("blocked_until", 0) > agora
            estado = "INDISPONÍVEL" if (bloq_led or not livres) else "disponível"
            extra = f"; ledger do projeto bloqueia ({led.get('blocked_code')}) até {_hhmmss(led['blocked_until'])}" if bloq_led else ""
            out.append(f"  {nomes[p]:<11} {estado}  ({len(livres)}/{len(r.contas)} conexões do 9router sem bloqueio ativo, estratégia {r.estrategia}{extra})")
    d = fila["decisao"]
    out.append("\nDECISÃO: " + (f"{d['provedor']} {d['conta'] or '(conta a critério do 9router)'} → modelo {d['modelo']}" if d
                               else "NENHUM elo utilizável → o projeto devolveria 'todos os provedores da fila falharam'"))
    if eventos:
        out.append("\nÚltimos eventos do roteador (log, sem corpo de requisição):")
        out.extend(f"  {x}" for x in eventos)
    return "\n".join(out)
