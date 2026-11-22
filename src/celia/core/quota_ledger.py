"""Contador de Cota: estado por provedor, siglas de evento e registro do dia.

Cada chamada das filas de fallback (model_chain.py) termina com uma SIGLA:

  OK   respondeu certo
  LNT  lento: passou do prazo do 1º token (pula só nesta fala)
  CTM  cota por minuto estourou (pausa até o minuto virar)
  CTD  cota do dia acabou (provedor desligado até o reset)
  SLD  sem saldo / pagamento (402) (desligado até "celia-cota liberar")
  CHV  problema na chave (401/403, limite da chave) (desligado até liberar)
  MRT  modelo morto (410/404) (elo desligado até liberar)
  ERR  erro do provedor (5xx, conexão) (elo pausa 30 s)
  JSN  resposta inválida (JSON quebrado / tool inexistente)
  VAZ  resposta vazia

O contador deste arquivo só enxerga as chamadas do processo da Célia. Quem sabe o consumo REAL é o 9router: com `router_db` e um bloco
`router_mirror` no provedor (config/model_chains.yaml), `can_use` consulta o espelho somente leitura (core/espelho_9router.py) e pula o elo cujo
provedor o roteador já esgotou, em vez de descobrir o fim da cota num 429. O espelho não guarda contador: cada leitura é uma fotografia carimbada.

Arquivos (em var/cota/ por padrão):
  AAAA-MM-DD.jsonl  uma linha por evento (data local)
  state.json        estado atual (lido pelo comando celia-cota)
  commands.jsonl    comandos do celia-cota para o processo em execução
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .raizes import RAIZ, VAR

DEFAULT_DIR = VAR / "cota"  # respeita CELIA_VAR_DIR

CODES: dict[str, str] = {
    "OK": "respondeu certo",
    "LNT": "lento: passou do prazo do 1º token",
    "CTM": "cota por minuto estourou",
    "CTD": "cota do dia acabou",
    "SLD": "sem saldo",
    "CHV": "problema na chave",
    "MRT": "modelo morto (aposentado ou inexistente)",
    "ERR": "erro do provedor",
    "JSN": "resposta inválida",
    "VAZ": "resposta vazia",
    "FIM": "todos os elos falharam",
    "LIB": "liberado manualmente",
}
FAIL_CODES = {"LNT", "CTM", "CTD", "SLD", "CHV", "MRT", "ERR", "JSN", "VAZ"}

# Quanto tempo cada sigla deixa o provedor (P) ou o elo (E) fora da fila.
# None = até o reset diário; "manual" = até `celia-cota liberar`.
BLOCK_RULES: dict[str, tuple[str, Any]] = {
    "CTM": ("P", 60.0),
    "CTD": ("P", None),
    "SLD": ("P", "manual"),
    "CHV": ("P", "manual"),
    "MRT": ("E", "manual"),
    "ERR": ("E", 30.0),
}
# Em provedor com `block_scope: model` (Gemini: a cota é por projeto E POR MODELO), estas siglas
# tiram só o modelo que tomou o 429, não o provedor inteiro.
MODEL_SCOPE_CODES = {"CTM", "CTD", "CHV", "SLD"}
ERR_400_PAUSE_SEC = 600.0  # 400 permanente (parâmetro/modelo): pausa longa no elo

_RESET_RE = re.compile(r"reset after\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?", re.I)


def _status_of(err: Any) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(err, attr, None)
        if isinstance(value, int):
            return value
    m = re.search(r"\[(\d{3})\]|http_(\d{3})|Error code: (\d{3})", str(err or ""))
    if m:
        return int(next(g for g in m.groups() if g))
    return None


def classify_error(err: Any) -> tuple[str, float | None, str]:
    """Erro → (sigla, pausa em segundos ou None, mensagem curta)."""
    text = str(err or "")
    low = text.lower()
    status = _status_of(err)
    short = re.sub(r"\s+", " ", text)[:160]
    name = type(err).__name__ if not isinstance(err, str) else ""
    if status in (429, 413) or "rate limit" in low or "ratelimiterror" in name.lower():
        if any(k in low for k in ("per-day", "per day", "perday", "daily", "free-models-per-day", "neurons per day")):
            return "CTD", None, short
        m = _RESET_RE.search(text)
        if m and any(m.groups()):
            h, mi, s = (int(x) if x else 0 for x in m.groups())
            secs = h * 3600 + mi * 60 + s
            return ("CTD" if secs > 600 else "CTM"), float(max(secs, 5)), short
        return "CTM", 60.0, short
    if status == 402 or "payment" in low or "insufficient credit" in low or "add usage credits" in low:
        return "SLD", None, short
    if status in (401, 403) or "authentication" in low or "key limit exceeded" in low:
        return "CHV", None, short
    if status in (404, 410) or "no such model" in low or "no endpoints found" in low or "retired" in low:
        return "MRT", None, short
    if status == 400:
        return "ERR", ERR_400_PAUSE_SEC, short
    return "ERR", 30.0, short or name


def _now() -> float:
    return time.time()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class QuotaLedger:
    """Estado de cota por provedor e por elo, compartilhado pelas filas do processo."""

    def __init__(self, providers: dict[str, dict[str, Any]], log_dir: Path | str = DEFAULT_DIR,
                 neuron_rates: dict[str, dict[str, float]] | None = None, router_db: Path | str | None = None,
                 permitidos: set[str] | None = None) -> None:
        self.providers = providers
        self.session_started_at = _now()     # início desta sessão (processo): separa "antes da sessão" × "durante" no espelho
        self.log_dir = Path(log_dir)
        if not self.log_dir.is_absolute():
            self.log_dir = RAIZ / self.log_dir
        self.neuron_rates = neuron_rates or {}
        self._lock = threading.RLock()
        self._prov: dict[str, dict[str, Any]] = {}
        self._links: dict[str, dict[str, Any]] = {}
        self._brains: dict[str, dict[str, Any]] = {}
        self._minute: dict[str, deque] = {}
        self._observers: list[Any] = []  # avisos: quem quer saber de cada evento
        self._cmd_offset = 0
        self._cmd_mtime = 0.0
        self.espelho = self._montar_espelho(router_db, neuron_rates, permitidos)
        self._load_state()
        try:  # comandos antigos já aplicados não voltam a valer
            self._cmd_offset = (self.log_dir / "commands.jsonl").stat().st_size
        except OSError:
            self._cmd_offset = 0

    def _montar_espelho(self, router_db: Path | str | None, neuron_rates: dict[str, dict[str, float]] | None,
                        permitidos: set[str] | None) -> Any:
        """Espelho do 9router, só se houver banco E algum provedor declarar `router_mirror`. Sem isso o ledger age como sempre agiu."""
        if not router_db or not any((c or {}).get("router_mirror") for c in self.providers.values()):
            return None
        from .espelho_9router import EspelhoRoteador, TAXAS_PADRAO

        declarados = {n: dict(c["router_mirror"]) for n, c in self.providers.items() if (c or {}).get("router_mirror")}
        taxas = (neuron_rates or {}).get("default") or TAXAS_PADRAO
        return EspelhoRoteador(router_db, declarados, reset_utc_hour={n: int(self.providers[n].get("reset_utc_hour", 0)) for n in declarados},
                               taxas={"in": float(taxas["in"]), "out": float(taxas["out"])}, sessao_inicio=self.session_started_at,
                               permitidos=permitidos)

    def _veredito_roteador(self, provider: str, model: str, now: float) -> Any:
        """Veredito cru do 9router para o elo (leitura do banco fora do lock). Qualquer falha = None = sem opinião (como antes do espelho)."""
        if self.espelho is None:
            return None
        try:
            return self.espelho.avaliar(provider, model, now, self.next_reset(provider, now))
        except Exception:
            return None

    def _usado_efetivo(self, provider: str, now: float, local: float) -> tuple[float, str, float | None]:
        """(usado, fonte, lido_em). Provedor em neurons com espelho: o MAIOR entre o contador local e o medido pelo roteador.
        Não soma: o roteador já viu as chamadas do processo local. Sem leitura válida, devolve o local."""
        if self.espelho is None or (self.providers.get(provider) or {}).get("kind") != "neurons":
            return local, "local", None
        try:
            r = self.espelho.resumo(provider, now, self.next_reset(provider, now))
        except Exception:
            return local, "local", None
        if not r or r["erro"] or r["usado_total"] is None:
            return local, "local", None
        return max(local, float(r["usado_total"])), "9router", r["lido_em"]

    # ── provedor, período e reset ──
    def provider_of(self, model: str) -> str:
        for name, cfg in self.providers.items():
            prefixes = cfg.get("match") or []
            suffix = cfg.get("match_suffix")
            if any(model.startswith(p) for p in prefixes) and (not suffix or model.endswith(suffix)):
                return name
        return model.split("/", 1)[0]

    def _reset_hour(self, provider: str) -> int:
        return int((self.providers.get(provider) or {}).get("reset_utc_hour", 0))

    def _period(self, provider: str, now: float | None = None) -> str:
        now = _now() if now is None else now
        dt = datetime.fromtimestamp(now, timezone.utc) - timedelta(hours=self._reset_hour(provider))
        return dt.strftime("%Y-%m-%d")

    def next_reset(self, provider: str, now: float | None = None) -> float:
        now = _now() if now is None else now
        h = self._reset_hour(provider)
        dt = datetime.fromtimestamp(now, timezone.utc)
        reset = dt.replace(hour=h, minute=0, second=0, microsecond=0)
        if reset <= dt:
            reset += timedelta(days=1)
        return reset.timestamp()

    def _pstate(self, provider: str, now: float) -> dict[str, Any]:
        st = self._prov.setdefault(provider, {"period": self._period(provider, now), "used": 0.0,
                                              "calls": 0, "blocked_until": 0.0, "blocked_code": "",
                                              "blocked_since": 0.0, "last_code": "", "last_at": 0.0,
                                              "avisos": {}})
        st.setdefault("avisos", {})  # estado salvo antes dos avisos não tem a chave
        period = self._period(provider, now)
        if st["period"] != period:  # virou o dia da cota: zera uso, avisos e solta bloqueio diário
            st.update(period=period, used=0.0, calls=0, avisos={})
            if st.get("blocked_code") == "CTD":
                st.update(blocked_until=0.0, blocked_code="", blocked_since=0.0)
        return st

    def _lstate(self, model: str) -> dict[str, Any]:
        return self._links.setdefault(model, {"blocked_until": 0.0, "blocked_code": "", "blocked_since": 0.0,
                                              "last_code": "", "last_at": 0.0, "ok": 0, "fail": 0})

    # ── decisão: pode usar este elo agora? ──
    def can_use(self, model: str, brain: str) -> tuple[bool, str]:
        self._poll_commands()
        now = _now()
        provider = self.provider_of(model)
        cfg = self.providers.get(provider) or {}
        with self._lock:
            st = self._pstate(provider, now)
            if st["blocked_until"] > now:
                return False, f"{st['blocked_code']} no provedor {provider}"
            lk = self._lstate(model)
            if lk["blocked_until"] > now:
                return False, f"{lk['blocked_code']} no elo"
        # O roteador sabe o consumo real: se ele já esgotou o provedor, pula SEM gastar chamada. Vem antes dos limites locais porque o motivo dele é o
        # mais informativo; a re-sonda (estimativa pode estar alta) só é concedida se nenhuma regra local barraria a chamada de qualquer jeito.
        veredito = self._veredito_roteador(provider, model, now)
        ok_local, motivo_local = self._limites_locais(model, brain, provider, cfg, now)
        if veredito is not None and veredito.bloqueado:
            if veredito.codigo == "ESGOTADA" and ok_local and self.espelho.conceder_resonda(provider, now):
                return True, ""
            return False, veredito.motivo
        return ok_local, motivo_local

    def _limites_locais(self, model: str, brain: str, provider: str, cfg: dict[str, Any], now: float) -> tuple[bool, str]:
        """Limite por minuto, teto diário e reserva do Jev (com o uso efetivo: o maior entre o local e o medido no 9router)."""
        with self._lock:
            st = self._pstate(provider, now)
            minute_limit = int(cfg.get("minute_limit") or 0)
            if minute_limit:
                win = self._minute.setdefault(provider, deque())
                while win and now - win[0] > 60:
                    win.popleft()
                if len(win) >= minute_limit:
                    return False, f"limite de {minute_limit}/min atingido"
            local = float(st["used"])
            limit = float(cfg.get("daily_limit") or 0)
        if limit:
            used, fonte, _ = self._usado_efetivo(provider, now, local)
            origem = "contagem local" if fonte == "local" else "consumo medido no 9router"
            if cfg.get("hard_limit") and used >= limit:
                return False, f"cota do dia esgotada ({origem})"
            reserve = float(cfg.get("reserve_for_jev") or 0)
            if brain == "celia" and reserve > 0 and used >= limit * (1 - reserve):
                return False, f"reserva do Jev ({int(reserve * 100)}% da cota; {origem})"
        return True, ""

    def estimate_units(self, provider: str, model: str, in_chars: int, out_chars: int) -> float:
        cfg = self.providers.get(provider) or {}
        if cfg.get("kind") == "neurons":
            rates = self.neuron_rates.get(model) or self.neuron_rates.get("default") or {"in": 0, "out": 0}
            tok_in, tok_out = in_chars / 3.0, out_chars / 3.0  # mesma heurística do AsyncLLM
            return (tok_in * float(rates["in"]) + tok_out * float(rates["out"])) / 1_000_000
        return 1.0

    # ── registro de evento ──
    def record(self, brain: str, model: str, code: str, *, ttft_ms: float | None = None,
               total_ms: float | None = None, in_chars: int = 0, out_chars: int = 0,
               message: str = "", pause_sec: float | None = None,
               units_override: float | None = None) -> dict[str, Any]:
        now = _now()
        provider = self.provider_of(model) if model else "-"
        with self._lock:
            units = 0.0
            if model and code not in {"CTM", "CTD", "SLD", "CHV", "MRT", "FIM"}:
                st = self._pstate(provider, now)
                units = (self.estimate_units(provider, model, in_chars, out_chars)
                         if units_override is None else float(units_override))  # ASR: neurons por minuto de áudio
                st["used"] += units
                st["calls"] += 1
                self._minute.setdefault(provider, deque()).append(now)
            if model:
                st = self._pstate(provider, now)
                st.update(last_code=code, last_at=now)
                lk = self._lstate(model)
                lk.update(last_code=code, last_at=now)
                lk["ok" if code == "OK" else "fail"] += 1
                rule = BLOCK_RULES.get(code)
                if rule:
                    scope, default = rule
                    if scope == "P" and code in MODEL_SCOPE_CODES and \
                            (self.providers.get(provider) or {}).get("block_scope") == "model":
                        scope = "E"
                    if default == "manual":
                        until = 4.0e9  # só sai com `celia-cota liberar`
                    elif pause_sec is not None:  # o provedor disse quanto esperar
                        until = now + float(pause_sec)
                    elif default is None:
                        until = self.next_reset(provider, now)
                    else:
                        until = now + float(default)
                    target = st if scope == "P" else lk
                    target.update(blocked_until=until, blocked_code=code, blocked_since=now)
                if code == "OK":
                    self._brains[brain] = {"model": model, "provider": provider, "at": now}
            event = {"ts": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
                     "brain": brain, "provider": provider, "model": model, "code": code,
                     "desc": CODES.get(code, code),
                     "ttft_ms": None if ttft_ms is None else round(ttft_ms),
                     "total_ms": None if total_ms is None else round(total_ms),
                     "units": round(units, 2), "message": message[:200]}
            self._append_event(event)
            self._save_state()
        self._notify(brain, provider, code, event)
        return event

    # ── avisos (política de alertas: core/avisos.py) ──
    def add_observer(self, fn: Any) -> None:
        """fn(ledger, brain, provider, code, event) roda depois de cada evento, fora do lock."""
        if fn not in self._observers:
            self._observers.append(fn)

    def _notify(self, brain: str, provider: str, code: str, event: dict[str, Any]) -> None:
        for fn in list(self._observers):
            try:
                fn(self, brain, provider, code, event)
            except Exception:
                pass  # aviso nunca derruba a fala

    def mark_aviso(self, provider: str, key: str) -> bool:
        """Marca que o aviso `key` já saiu neste período da cota. True só na primeira vez."""
        with self._lock:
            marks = self._pstate(provider, _now())["avisos"]
            if marks.get(key):
                return False
            marks[key] = _now()
            self._save_state()
            return True

    def usage(self, provider: str) -> dict[str, Any]:
        """Uso do período: usado, limite diário (0 = sem número), bloqueio e próximo reset."""
        now = _now()
        cfg = self.providers.get(provider) or {}
        with self._lock:
            st = self._pstate(provider, now)
            local = float(st["used"])
            blocked_code, blocked_until = st.get("blocked_code") or "", float(st["blocked_until"])
        used, fonte, lido_em = self._usado_efetivo(provider, now, local)
        return {"used": used, "limit": float(cfg.get("daily_limit") or 0),
                "kind": cfg.get("kind") or "calls", "hard": bool(cfg.get("hard_limit")),
                "blocked_code": blocked_code, "blocked_until": blocked_until,
                "next_reset": self.next_reset(provider, now), "label": cfg.get("label") or "",
                "local": local, "fonte": fonte, "lido_em": lido_em}

    def resumo_roteador(self, provider: str, model: str | None = None) -> dict[str, Any] | None:
        """Fotografia do 9router para este provedor (consumo por conta/modelo, antes × sessão, veredito). None = sem espelho."""
        if self.espelho is None:
            return None
        try:
            now = _now()
            return self.espelho.resumo(provider, now, self.next_reset(provider, now), model)
        except Exception:
            return None

    # ── liberar na mão ──
    def release(self, target: str) -> list[str]:
        """Solta bloqueio de um provedor, de um elo (modelo) ou de tudo ("tudo")."""
        released: list[str] = []
        with self._lock:
            for name, st in self._prov.items():
                if target in ("tudo", name) and st.get("blocked_until", 0) > 0:
                    st.update(blocked_until=0.0, blocked_code="", blocked_since=0.0)
                    released.append(name)
            for model, lk in self._links.items():
                if target in ("tudo", model) or (target in self._prov and self.provider_of(model) == target):
                    if lk.get("blocked_until", 0) > 0:
                        lk.update(blocked_until=0.0, blocked_code="", blocked_since=0.0)
                        released.append(model)
            if released:
                self._append_event({"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                                    "brain": "manual", "provider": target, "model": "", "code": "LIB",
                                    "desc": CODES["LIB"], "ttft_ms": None, "total_ms": None,
                                    "units": 0, "message": ", ".join(released)})
                self._save_state()
        return released

    def _poll_commands(self) -> None:
        path = self.log_dir / "commands.jsonl"
        try:
            st = path.stat()
        except OSError:
            return
        if st.st_mtime == self._cmd_mtime and st.st_size == self._cmd_offset:
            return
        self._cmd_mtime = st.st_mtime
        try:
            with path.open("r", encoding="utf-8") as fh:
                fh.seek(self._cmd_offset)
                lines = fh.read()
                self._cmd_offset = fh.tell()
        except OSError:
            return
        for line in lines.splitlines():
            try:
                cmd = json.loads(line)
            except Exception:
                continue
            if cmd.get("cmd") == "liberar" and cmd.get("target"):
                self.release(str(cmd["target"]))

    # ── arquivos ──
    def _append_event(self, event: dict[str, Any]) -> None:
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            day = datetime.now().astimezone().strftime("%Y-%m-%d")
            with (self.log_dir / f"{day}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            pass  # registro nunca derruba a fala

    def snapshot(self) -> dict[str, Any]:
        now = _now()
        with self._lock:
            for name in self.providers:
                self._pstate(name, now)
            return {"updated_at": now, "session_started_at": self.session_started_at,
                    "providers": json.loads(json.dumps(self._prov)),
                    "links": json.loads(json.dumps(self._links)),
                    "brains": json.loads(json.dumps(self._brains)),
                    "limits": {n: {k: c.get(k) for k in ("kind", "daily_limit", "minute_limit",
                                                         "reserve_for_jev", "reset_utc_hour")}
                               for n, c in self.providers.items()},
                    "next_reset": {n: self.next_reset(n, now) for n in self.providers}}

    def _save_state(self) -> None:
        try:
            _atomic_write(self.log_dir / "state.json", json.dumps(self.snapshot(), ensure_ascii=False, indent=1))
        except OSError:
            pass

    def _load_state(self) -> None:
        try:
            data = json.loads((self.log_dir / "state.json").read_text(encoding="utf-8"))
        except Exception:
            return
        self._prov = data.get("providers") or {}
        self._links = data.get("links") or {}
        self._brains = data.get("brains") or {}
        self._migrate_model_scope()

    def _migrate_model_scope(self) -> None:
        """Estado salvo antes do `block_scope: model`: o bloqueio de cota que estava no provedor
        passa para o modelo que o causou (o elo com a mesma sigla e o mesmo instante)."""
        for name, st in self._prov.items():
            if (self.providers.get(name) or {}).get("block_scope") != "model":
                continue
            code = st.get("blocked_code")
            if code not in MODEL_SCOPE_CODES or st.get("blocked_until", 0) <= 0:
                continue
            for model, lk in self._links.items():
                if (self.provider_of(model) == name and lk.get("last_code") == code
                        and abs(float(lk.get("last_at", 0)) - float(st.get("blocked_since", 0))) < 5):
                    lk.update(blocked_until=st["blocked_until"], blocked_code=code,
                              blocked_since=st["blocked_since"])
            st.update(blocked_until=0.0, blocked_code="", blocked_since=0.0)


_LEDGER: QuotaLedger | None = None
_LEDGER_LOCK = threading.Lock()


def get_ledger(config: dict[str, Any] | None = None) -> QuotaLedger:
    """Um único contador por processo, criado a partir de config/model_chains.yaml."""
    global _LEDGER
    with _LEDGER_LOCK:
        if _LEDGER is None:
            cfg = config or {}
            contas = cfg.get("cloudflare_contas") or {}
            _LEDGER = QuotaLedger(cfg.get("providers") or {}, cfg.get("log_dir") or DEFAULT_DIR,
                                  cfg.get("neuron_rates") or {}, router_db=contas.get("router_db"),
                                  permitidos={str(n) for g in ("llm", "asr") for n in (contas.get(g) or [])})
            from . import avisos  # avisos de cota/FIM saem do mesmo ledger, sem 2ª infraestrutura
            avisos.configurar(cfg.get("alertas"))
            _LEDGER.add_observer(avisos.observar_ledger)
        return _LEDGER
