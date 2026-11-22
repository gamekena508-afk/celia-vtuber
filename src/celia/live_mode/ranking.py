"""Ranking de doadores a partir de eventos de presente REAIS (ou simulados, em livro separado).

Regras (documentadas em docs/LIVE-MODE.md):
- Valor vem do próprio evento (`diamond_count`); sem valor conhecido o presente NÃO conta.
- Identidade é o `user_id` estável do TikTok; apelido muda e colide, então nunca é chave.
- Evento repetido (mesmo msg_id) é ignorado.
- Presente "streakable" (combo): atualizações acumuladas do mesmo combo
  (user, gift, group_id) carregam o total até ali; só a diferença ainda não
  creditada soma. O fim do combo (`repeat_end`) fecha o combo.
- Janela: 0 = sessão inteira; N>0 = últimos N minutos (deslizante).
- Empate: quem chegou primeiro ao total fica na frente.
- Livro real e simulado NUNCA se misturam.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

MAX_RECORDS = 50_000
SEEN_MAX = 20_000
COMBO_TTL_S = 900.0


@dataclass
class Donor:
    user_id: str
    display_id: str = ""
    nickname: str = ""
    total: int = 0
    reached_at: float = 0.0
    last_gift: str = ""
    avatar_url: str = ""


@dataclass
class GiftLedger:
    origin: str  # "real" | "simulated"
    window_minutes: int = 0
    records: list[dict[str, Any]] = field(default_factory=list)
    _seen: "OrderedDict[str, float]" = field(default_factory=OrderedDict)
    _combos: dict[tuple, dict[str, Any]] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=lambda: {
        "events": 0, "credited": 0, "duplicates": 0, "combo_updates_ignored": 0, "invalid": 0})

    def reset(self) -> None:
        self.records.clear()
        self._seen.clear()
        self._combos.clear()
        for key in self.counters:
            self.counters[key] = 0

    # ---- entrada -------------------------------------------------------
    def apply(self, event: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        self.counters["events"] += 1
        if event.get("origin") != self.origin:
            self.counters["invalid"] += 1
            return {"credited": 0, "reason": "origin_mismatch"}
        user_id = str(event.get("user_id") or "").strip()
        msg_id = str(event.get("msg_id") or "").strip()
        value = event.get("diamond_count")
        if not user_id or not msg_id or not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            self.counters["invalid"] += 1
            return {"credited": 0, "reason": "missing_user_msg_or_value"}
        if msg_id in self._seen:
            self.counters["duplicates"] += 1
            return {"credited": 0, "reason": "duplicate"}
        self._seen[msg_id] = now
        while len(self._seen) > SEEN_MAX:
            self._seen.popitem(last=False)
        self._sweep_combos(now)

        units = max(1, int(event.get("repeat_count") or 1))
        if event.get("streakable"):
            key = (user_id, str(event.get("gift_id") or event.get("gift_name") or ""), str(event.get("group_id") or "nogroup"))
            state = self._combos.setdefault(key, {"units": 0, "seen": now})
            delta = units - state["units"]
            state["seen"] = now
            if event.get("repeat_end"):
                state["closed"] = True
            if delta <= 0:
                self.counters["combo_updates_ignored"] += 1
                return {"credited": 0, "reason": "combo_already_counted"}
            state["units"] = units
            units = delta
        amount = units * value
        self.records.append({
            "ts": now, "user_id": user_id, "display_id": str(event.get("display_id") or ""),
            "nickname": str(event.get("nickname") or ""), "avatar_url": str(event.get("avatar_url") or "")[:2048], "amount": amount,
            "gift": str(event.get("gift_name") or ""), "msg_id": msg_id})
        if len(self.records) > MAX_RECORDS:
            del self.records[: len(self.records) - MAX_RECORDS]
        self.counters["credited"] += 1
        return {"credited": amount, "reason": "ok"}

    def _sweep_combos(self, now: float) -> None:
        stale = [k for k, v in self._combos.items() if now - v["seen"] > COMBO_TTL_S]
        for key in stale:
            del self._combos[key]

    # ---- saída ---------------------------------------------------------
    def standings(self, now: float | None = None) -> list[Donor]:
        now = time.time() if now is None else now
        cutoff = now - self.window_minutes * 60 if self.window_minutes > 0 else None
        donors: dict[str, Donor] = {}
        for rec in self.records:
            if cutoff is not None and rec["ts"] < cutoff:
                continue
            donor = donors.get(rec["user_id"])
            if donor is None:
                donor = donors[rec["user_id"]] = Donor(rec["user_id"])
            donor.total += rec["amount"]
            donor.reached_at = rec["ts"]  # instante em que atingiu o total atual
            donor.display_id = rec["display_id"] or donor.display_id
            donor.nickname = rec["nickname"] or donor.nickname
            donor.avatar_url = rec.get("avatar_url") or donor.avatar_url
            donor.last_gift = rec["gift"] or donor.last_gift
        return sorted(donors.values(), key=lambda d: (-d.total, d.reached_at, d.user_id))

    def top(self, n: int = 2, now: float | None = None) -> list[Donor]:
        return self.standings(now)[:n]

    def to_state(self) -> dict[str, Any]:
        return {"origin": self.origin, "records": self.records[-MAX_RECORDS:],
                "seen": list(self._seen.keys())[-SEEN_MAX:],
                "combos": [[list(k), v] for k, v in self._combos.items()], "counters": self.counters}

    @classmethod
    def from_state(cls, data: dict[str, Any], window_minutes: int = 0) -> "GiftLedger":
        ledger = cls(origin=str(data.get("origin") or "real"), window_minutes=window_minutes)
        ledger.records = [r for r in data.get("records", []) if isinstance(r, dict)]
        ledger._seen = OrderedDict((str(m), 0.0) for m in data.get("seen", []))
        ledger._combos = {tuple(k): v for k, v in data.get("combos", [])}
        ledger.counters.update({k: int(v) for k, v in (data.get("counters") or {}).items() if k in ledger.counters})
        return ledger
