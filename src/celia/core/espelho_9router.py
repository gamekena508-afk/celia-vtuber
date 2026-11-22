"""Espelho SOMENTE LEITURA do consumo real do 9router: a fila para de gastar chamada num elo que o roteador já sabe que está esgotado.

Por que existe (incidente 2026-10-08): o `QuotaLedger` só contava as chamadas feitas PELO processo da Célia. Replay, testes e outros agentes
passam pelo mesmo 9router e gastam a mesma cota, então o painel marcava `0 / 60.000 neurons` com as 6 contas Cloudflare já em 98–105%. A Célia só
descobria o fim da cota depois de levar um 429 (varredura de 1,3–3,0 s pelas 6 contas) a cada fala.

A fonte da verdade é o próprio 9router (`~/.9router/db/data.sqlite`, aberto em `mode=ro`). Este módulo NÃO mantém contador: cada leitura é uma
fotografia do banco dele, com o horário em que foi tirada (`lido_em`) e um cache curto só para não abrir o banco a cada elo da fila.

O que o espelho sabe, por provedor do roteador:
  - uso do dia (período = reset do provedor) por CONEXÃO e por MODELO, e quanto disso é de antes × depois do início da sessão da Célia;
  - bloqueios (`modelLock_*`) de cada conta, que o próprio roteador gravou;
  - o limite diário por conta quando ele é conhecido (Cloudflare: 10.000 neurons).

Duas regras de bloqueio, ambas conservadoras:
  1. ESGOTADA — provedor com limite por conta conhecido e TODAS as contas ativas com consumo ≥ margem do limite. Atenção: neurons aqui são
     `tokens × tabela oficial` (o `usageHistory` não guarda neurons nem as chamadas que levaram 429): é derivado, não o medidor da Cloudflare.
     Por ser estimativa, existe uma re-sonda: 1 chamada a cada `reprobe_sec`. Se a estimativa estiver alta demais, o prejuízo é no máximo esse intervalo.
  2. COOLDOWN — TODAS as contas ativas com bloqueio vigente para o modelo pedido (estado gravado pelo roteador, sem estimativa).

Qualquer falha de leitura (banco ausente, esquema diferente) devolve "não bloqueia": o comportamento cai no que a Célia já fazia antes.
Nunca seleciona `apiKey`, `endpoint`, `meta`, e-mail, token nem `providerSpecificData` (há teste que varre o SQL deste módulo).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .rota_debug import (LIMITE_DIARIO_CONTA, MARGEM_ESGOTADA, PROVEDOR_9ROUTER, EstadoProvedor, _abrir_ro, _dur, _iso, _lock_ate,
                         _lock_ativo, _mil, _nome_publico, inicio_do_periodo, ler_provedor)

CACHE_PADRAO_S = 20.0
REPROBE_PADRAO_S = 900.0
TAXAS_PADRAO = {"in": 26668.0, "out": 204805.0}   # neurons por milhão de tokens (Workers AI, Llama 70B fp8-fast)

# SOMENTE colunas não secretas. `apiKey`, `endpoint`, `meta` e `tokens` ficam de fora de propósito.
SQL_USO_DETALHE = """
select model, connectionId, count(*), coalesce(sum(promptTokens),0), coalesce(sum(completionTokens),0),
       coalesce(sum(case when timestamp >= ? then 1 else 0 end),0),
       coalesce(sum(case when timestamp >= ? then promptTokens else 0 end),0),
       coalesce(sum(case when timestamp >= ? then completionTokens else 0 end),0)
from usageHistory where provider = ? and timestamp >= ? group by model, connectionId
"""
TODAS_AS_SQL = (SQL_USO_DETALHE,)
FUTURO_INDEFINIDO = "9999-12-31T23:59:59.999Z"   # "sessão desconhecida": nada cai no lado "depois"


@dataclass
class Veredito:
    bloqueado: bool
    codigo: str = ""          # "ESGOTADA" | "COOLDOWN" | "" (não bloqueia)
    motivo: str = ""
    ate: float | None = None  # quando a tentativa volta a valer (reset diário ou fim do cooldown mais curto)


@dataclass
class Leitura:
    provedor: str                                   # id no 9router (ex.: cloudflare-ai)
    lido_em: float
    periodo_inicio: str
    estado: EstadoProvedor
    por_conta: list[dict[str, Any]] = field(default_factory=list)    # uma linha por conexão: chamadas e neurons hoje, antes × sessão
    por_modelo: list[dict[str, Any]] = field(default_factory=list)   # uma linha por modelo
    sessao_inicio: float | None = None
    erro: str | None = None

    def total(self, campo: str) -> float:
        return float(sum(float(c.get(campo) or 0) for c in self.por_conta))


def _neurons(p: float, c: float, taxas: dict[str, float]) -> float:
    return (p * taxas["in"] + c * taxas["out"]) / 1_000_000


class EspelhoRoteador:
    """Fotografias do 9router com carimbo. Sem estado próprio além do cache e do relógio da re-sonda."""

    def __init__(self, db: Path | str, provedores: dict[str, dict[str, Any]], *, reset_utc_hour: dict[str, int] | None = None,
                 taxas: dict[str, float] | None = None, sessao_inicio: float | None = None, permitidos: set[str] | None = None) -> None:
        self.db = Path(str(db)).expanduser()
        self.cfg = {nome: dict(c) for nome, c in provedores.items() if c}        # provedor do ledger → config do espelho
        self.reset_utc_hour = reset_utc_hour or {}
        self.taxas = taxas or dict(TAXAS_PADRAO)
        self.sessao_inicio = sessao_inicio
        self.permitidos = permitidos or set()
        self.diagnostico = False        # True = painel/CLI: observa e NUNCA gasta a re-sonda (só o processo da Célia gasta)
        self._cache: dict[str, Leitura] = {}
        self._sonda: dict[str, float] = {}
        self._lock = threading.RLock()

    # ── leitura ──
    def provedor_id(self, provedor_ledger: str) -> str | None:
        cfg = self.cfg.get(provedor_ledger)
        return str(cfg.get("provider") or PROVEDOR_9ROUTER.get(provedor_ledger) or "") or None if cfg else None

    def ler(self, provedor_ledger: str, agora: float, *, forcar: bool = False) -> Leitura | None:
        """Fotografia do provedor (cache de `cache_sec`). None = este provedor não tem espelho configurado."""
        cfg = self.cfg.get(provedor_ledger)
        rid = self.provedor_id(provedor_ledger)
        if not cfg or not rid:
            return None
        with self._lock:
            antiga = self._cache.get(rid)
            if antiga and not forcar and 0 <= agora - antiga.lido_em < float(cfg.get("cache_sec", CACHE_PADRAO_S)):
                return antiga
            nova = self._fotografar(rid, agora, int(self.reset_utc_hour.get(provedor_ledger, 0)))
            self._cache[rid] = nova
            return nova

    def _fotografar(self, rid: str, agora: float, reset_hour: int) -> Leitura:
        periodo = inicio_do_periodo(agora, reset_hour)
        estado = ler_provedor(self.db, rid, agora=agora, reset_utc_hour=reset_hour, taxas=self.taxas)
        leitura = Leitura(provedor=rid, lido_em=agora, periodo_inicio=periodo, estado=estado, sessao_inicio=self.sessao_inicio, erro=estado.erro)
        if estado.erro:
            return leitura
        limite = self._limite_conta(rid)
        estado.limite_diario = limite
        sessao_iso = _iso(self.sessao_inicio) if self.sessao_inicio else FUTURO_INDEFINIDO
        por_conta = {c.id: {"id": c.id, "nome": _nome_publico(c, self.permitidos), "ativa": c.ativa, "chamadas": 0, "neurons": 0.0,
                            "chamadas_sessao": 0, "neurons_sessao": 0.0} for c in estado.contas}
        por_modelo: dict[str, dict[str, Any]] = {}
        try:
            con = _abrir_ro(self.db)
            try:
                for modelo, cid, n, p, c, n_s, p_s, c_s in con.execute(SQL_USO_DETALHE, (sessao_iso, sessao_iso, sessao_iso, rid, periodo)):
                    neu, neu_s = _neurons(p, c, self.taxas), _neurons(p_s, c_s, self.taxas)
                    linha = por_conta.get(cid)
                    if linha is not None:
                        linha["chamadas"] += int(n)
                        linha["neurons"] += neu
                        linha["chamadas_sessao"] += int(n_s)
                        linha["neurons_sessao"] += neu_s
                    m = por_modelo.setdefault(str(modelo), {"modelo": str(modelo), "chamadas": 0, "tokens_in": 0, "tokens_out": 0,
                                                            "neurons": 0.0, "chamadas_sessao": 0, "neurons_sessao": 0.0})
                    m["chamadas"] += int(n)
                    m["tokens_in"] += int(p)
                    m["tokens_out"] += int(c)
                    m["neurons"] += neu
                    m["chamadas_sessao"] += int(n_s)
                    m["neurons_sessao"] += neu_s
            finally:
                con.close()
        except sqlite3.Error as exc:
            leitura.erro = f"esquema do 9router diferente do esperado ({type(exc).__name__})"
            return leitura
        leitura.por_conta = sorted(por_conta.values(), key=lambda x: x["nome"])
        leitura.por_modelo = sorted(por_modelo.values(), key=lambda x: -x["chamadas"])
        return leitura

    def _limite_conta(self, rid: str) -> float | None:
        for cfg in self.cfg.values():
            if str(cfg.get("provider") or "") == rid and cfg.get("account_limit"):
                return float(cfg["account_limit"])
        return LIMITE_DIARIO_CONTA.get(rid)

    # ── veredito ──
    def avaliar(self, provedor_ledger: str, modelo: str, agora: float, reset_em: float | None = None) -> Veredito | None:
        """Veredito CRU do roteador para este elo (puro: não gasta nada, nem a re-sonda). None = sem espelho para o provedor."""
        leitura = self.ler(provedor_ledger, agora)
        if leitura is None:
            return None
        cfg = self.cfg.get(provedor_ledger) or {}
        if leitura.erro or not leitura.estado.contas:
            return Veredito(False)                              # sem leitura: fail-open, igual a antes do espelho
        ativas = [c for c in leitura.estado.contas if c.ativa]
        if not ativas:
            return Veredito(False)
        alvo = modelo.split("/", 1)[1] if "/" in modelo else modelo
        limite = leitura.estado.limite_diario
        margem = float(cfg.get("exhausted_margin", MARGEM_ESGOTADA))
        uso = {c["id"]: c for c in leitura.por_conta}
        if limite and all(uso.get(c.id, {}).get("neurons", 0.0) >= margem * limite for c in ativas):
            total = sum(uso[c.id]["neurons"] for c in ativas)
            quando = f"; renova {datetime.fromtimestamp(reset_em).strftime('%H:%M')}" if reset_em else ""
            v = Veredito(True, "ESGOTADA", f"9router: {len(ativas)}/{len(ativas)} contas esgotadas ({_mil(total)}/{_mil(limite * len(ativas))} neurons, "
                                          f"consumo derivado de tokens, lido às {datetime.fromtimestamp(leitura.lido_em).strftime('%H:%M:%S')}){quando}", reset_em)
            return v
        ate = [_lock_ate(c, alvo) for c in ativas if _lock_ativo(c, alvo, agora)]
        if len(ate) == len(ativas):
            fim = min(t for t in ate if t)
            return Veredito(True, "COOLDOWN", f"9router: as {len(ativas)} contas estão em cooldown para este modelo (volta em {_dur(fim - agora)})", fim)
        return Veredito(False)

    def conceder_resonda(self, provedor_ledger: str, agora: float) -> bool:
        """A estimativa pode estar alta: autoriza UMA chamada a cada `reprobe_sec` para o dado real confirmar ou desmentir o veredito ESGOTADA.
        Quem chama só deve pedir quando a chamada realmente vai acontecer (a concessão é consumida). Painel/CLI (`diagnostico`) nunca consome."""
        if self.diagnostico:
            return False
        intervalo = float((self.cfg.get(provedor_ledger) or {}).get("reprobe_sec", REPROBE_PADRAO_S))
        if intervalo <= 0:
            return False
        with self._lock:
            ultima = self._sonda.get(provedor_ledger)
            if ultima is None or agora - ultima >= intervalo:
                self._sonda[provedor_ledger] = agora
                return True
        return False

    # ── resumo para painéis e CLI ──
    def resumo(self, provedor_ledger: str, agora: float, reset_em: float | None = None, modelo: str | None = None) -> dict[str, Any] | None:
        leitura = self.ler(provedor_ledger, agora)
        if leitura is None:
            return None
        limite = leitura.estado.limite_diario
        ativas = [c for c in leitura.estado.contas if c.ativa]
        v = self.avaliar(provedor_ledger, modelo or "", agora, reset_em)
        intervalo = float((self.cfg.get(provedor_ledger) or {}).get("reprobe_sec", REPROBE_PADRAO_S))
        ultima = self._sonda.get(provedor_ledger)
        proxima = None if (v is None or not v.bloqueado or v.codigo != "ESGOTADA" or intervalo <= 0) else (agora if ultima is None else max(agora, ultima + intervalo))
        return {"fonte": "9router usageHistory (somente leitura; neurons = tokens × tabela oficial, derivado)", "provedor": leitura.provedor,
                "lido_em": leitura.lido_em, "periodo_inicio": leitura.periodo_inicio, "erro": leitura.erro,
                "limite_por_conta": limite, "limite_total": (limite * len(ativas)) if limite else None,
                "usado_total": leitura.total("neurons") if limite else None, "chamadas_total": int(leitura.total("chamadas")),
                "chamadas_sessao": int(leitura.total("chamadas_sessao")), "neurons_sessao": leitura.total("neurons_sessao"),
                "sessao_conhecida": bool(self.sessao_inicio), "sessao_inicio": self.sessao_inicio,
                "por_conta": leitura.por_conta, "por_modelo": leitura.por_modelo, "estrategia": leitura.estado.estrategia,
                "veredito": None if v is None else {"bloqueado": v.bloqueado, "codigo": v.codigo, "motivo": v.motivo, "ate": v.ate,
                                                    "proxima_resonda": proxima}}


def construir(cfg: dict[str, Any], *, router_db: str | None = None, sessao_inicio: float | None = None) -> EspelhoRoteador | None:
    """Espelho a partir de `config/model_chains.yaml`: os provedores que declaram `router_mirror`. None = nenhum declarou (ou sem banco)."""
    provedores = cfg.get("providers") or {}
    declarados = {nome: (c.get("router_mirror") or {}) for nome, c in provedores.items() if c.get("router_mirror")}
    banco = router_db or (cfg.get("cloudflare_contas") or {}).get("router_db")
    if not declarados or not banco:
        return None
    sec = cfg.get("cloudflare_contas") or {}
    permitidos = {str(n) for g in ("llm", "asr") for n in (sec.get(g) or [])}
    taxas = (cfg.get("neuron_rates") or {}).get("default") or TAXAS_PADRAO
    return EspelhoRoteador(banco, declarados, reset_utc_hour={n: int(provedores[n].get("reset_utc_hour", 0)) for n in declarados},
                           taxas={"in": float(taxas["in"]), "out": float(taxas["out"])}, sessao_inicio=sessao_inicio, permitidos=permitidos)


def saldo_para(resumo: dict[str, Any] | None, precisa: float) -> tuple[bool, str]:
    """Dá para gastar `precisa` neurons agora? (pronto, explicação). Usa só o que o roteador mediu; sem leitura ⇒ NÃO afirma que dá."""
    if not resumo or resumo.get("erro"):
        return False, f"sem leitura do 9router ({(resumo or {}).get('erro') or 'espelho desligado'}): não afirmo que há saldo"
    v = resumo.get("veredito") or {}
    if v.get("bloqueado"):
        return False, v["motivo"]
    if resumo["limite_por_conta"] is None:
        return True, "provedor sem limite numérico conhecido; nenhum bloqueio ativo no roteador"
    contas = [max(0.0, resumo["limite_por_conta"] - c["neurons"]) for c in resumo["por_conta"]]
    restante = sum(contas)
    if restante < precisa:
        return False, f"saldo medido {_mil(restante)} neurons < {_mil(precisa)} pedidos ({sum(1 for c in contas if c > 0)} conta(s) com saldo)"
    return True, f"saldo medido {_mil(restante)} neurons ≥ {_mil(precisa)} pedidos ({sum(1 for c in contas if c > 0)} conta(s) com saldo)"


# ── sessão e apresentação (compartilhado por `celia-cota` e `celia-rota`) ─────────────────────────────────────────────────────────────
def sessao_gravada(log_dir: Path | str) -> float | None:
    """Início da última sessão da Célia (gravado no state.json do ledger). None = nenhuma sessão gravou o início ainda."""
    import json

    try:
        valor = json.loads((Path(str(log_dir)) / "state.json").read_text(encoding="utf-8")).get("session_started_at")
    except Exception:
        return None
    return float(valor) if isinstance(valor, (int, float)) and valor > 0 else None


def desde_local(texto: str, agora: float | None = None) -> float | None:
    """"2026-10-08 07:30" ou "07:30" (hoje, hora local) → epoch. Texto inválido → None."""
    agora = time.time() if agora is None else agora
    texto = (texto or "").strip()
    for formato in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(texto, formato).timestamp()
        except ValueError:
            pass
    try:
        hh, mm = (int(x) for x in texto.split(":"))
        base = datetime.fromtimestamp(agora).replace(hour=hh, minute=mm, second=0, microsecond=0)
        return base.timestamp()
    except ValueError:
        return None


def linhas_resumo(r: dict[str, Any], agora: float, *, local_usado: float | None = None, reset_em: float | None = None, rotulo: str = "") -> list[str]:
    """O que o Pai lê: fonte, carimbo, consumo × limite, estado, por conta, por modelo, antes × sessão e divergência com o contador local."""
    hora = lambda t: datetime.fromtimestamp(t).strftime("%H:%M:%S")   # noqa: E731
    out = [f"{rotulo or r['provedor']} — espelho do 9router · fonte: {r['fonte']} · lido às {hora(r['lido_em'])} · período desde {r['periodo_inicio']}"]
    if r.get("erro"):
        out.append(f"  ⚠ sem leitura: {r['erro']} (a fila segue o contador do projeto, como antes do espelho)")
        return out
    v = r.get("veredito") or {}
    if r["limite_total"]:
        pct = r["usado_total"] / r["limite_total"]
        estado = (f"BLOQUEADO ({v['codigo']})" if v.get("bloqueado") else "liberado")
        quando = f" — renova {datetime.fromtimestamp(reset_em).strftime('%H:%M')} (em {_dur(reset_em - agora)})" if (reset_em and v.get("bloqueado") and v.get("codigo") == "ESGOTADA") else ""
        out.append(f"  {_mil(r['usado_total'])} / {_mil(r['limite_total'])} neurons ({pct:.0%}) — {estado}{quando}")
    else:
        estado = f"BLOQUEADO ({v['codigo']})" if v.get("bloqueado") else "liberado"
        out.append(f"  {r['chamadas_total']} chamadas hoje — {estado}")
    if v.get("bloqueado"):
        out.append(f"  motivo: {v['motivo']}")
        if v.get("proxima_resonda") is not None:
            quando = "na próxima fala" if v["proxima_resonda"] <= agora else f"às {hora(v['proxima_resonda'])}"
            out.append(f"  re-sonda: uma chamada passa {quando} para o dado real confirmar a estimativa")
    for c in r["por_conta"]:
        extra = f"{_mil(c['neurons'])}/{_mil(r['limite_por_conta'])} neurons ({c['neurons'] / r['limite_por_conta']:.0%})" if r["limite_por_conta"] else f"{c['neurons']:.0f} neurons"
        out.append(f"  conta {c['nome']:<22} {extra}; {c['chamadas']} chamadas")
    for m in r["por_modelo"][:5]:
        out.append(f"  modelo {m['modelo']}: {m['chamadas']} chamadas" + (f", {_mil(m['neurons'])} neurons" if r["limite_por_conta"] else ""))
    if r["sessao_conhecida"]:
        antes_c, antes_n = r["chamadas_total"] - r["chamadas_sessao"], (r["usado_total"] or 0) - r["neurons_sessao"]
        out.append(f"  antes da sessão (desde {datetime.fromtimestamp(r['sessao_inicio']).strftime('%d/%m %H:%M')}): {antes_c} chamadas"
                   + (f" / {_mil(antes_n)} neurons" if r["limite_por_conta"] else "")
                   + f" · da sessão: {r['chamadas_sessao']} chamadas" + (f" / {_mil(r['neurons_sessao'])} neurons" if r["limite_por_conta"] else ""))
    else:
        out.append("  antes × sessão: sessão desconhecida (nenhuma Célia gravou o início); use --desde \"AAAA-MM-DD HH:MM\" ou \"HH:MM\"")
    if local_usado is not None and r["limite_total"] and abs(r["usado_total"] - local_usado) > 0.2 * max(r["usado_total"], 1):
        out.append(f"  ⚠ DIVERGÊNCIA: o contador do processo da Célia marca {_mil(local_usado)} neurons; o roteador mediu {_mil(r['usado_total'])}. "
                   "O contador local só vê esta Célia; vale o do roteador.")
    return out
