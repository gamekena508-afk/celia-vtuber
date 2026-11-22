"""Jev -> Célia narration: turn a journaled Jev event into a short, factual brief for the persona.

The persona never sees raw tool output, world text or chat here. Only whitelisted facts that Jev wrote
to its own durable journal reach her, and an event is spoken at most once and only while its mission
epoch is still current (a stop or restart invalidates anything still queued: no stale speech).
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Any

ACHIEVEMENT_FACTS = {
    "wood_6": "juntei madeira suficiente (6 toras ou mais)",
    "wooden_pickaxe": "fabriquei a picareta de madeira",
    "cobblestone_8": "já tenho pedregulho suficiente (8 ou mais)",
    "stone_pickaxe": "fabriquei a picareta de pedra",
    "furnace": "fiz uma fornalha",
    "iron_located": "avistei minério de ferro exposto no mapa",
    "raw_iron_3": "tenho 3 ferros brutos",
    "iron_ingots_3": "fundi 3 lingotes de ferro",
    "iron_pickaxe": "fabriquei a picareta de ferro",
}
_RESOURCE = re.compile(r"^[a-z0-9_]{1,40}$")
TERMINAL = frozenset({"succeeded", "blocked", "stopped", "paused", "interrupted"})
MIN_GAP_S = 25.0
_last_spoken = 0.0


def _inventory_line(payload: dict[str, Any]) -> str:
    inventory = payload.get("inventory")
    if not isinstance(inventory, dict) or not inventory:
        return ""
    items = [f"{name} x{n}" for name, n in list(inventory.items())[:10]
             if isinstance(name, str) and _RESOURCE.fullmatch(name) and isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 100000]
    return "FATO: inventário agora: " + ", ".join(items) if items else ""


def event_facts(kind: str, payload: dict[str, Any], mission: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    if kind == "mission_started":
        facts.append("FATO: aceitei a tarefa que me pediram; ainda não coletei nada")
    elif kind == "mission_resumed":
        facts.append("FATO: a missão foi retomada de onde parou; o inventário real foi conferido de novo")
    elif kind == "achievement":
        if payload.get("key") == "iron_pickaxe":
            return []  # the closing 'succeeded' event says it, with the equipped proof
        text = ACHIEVEMENT_FACTS.get(str(payload.get("key")))
        if text is None:
            return []
        facts.append(f"FATO: {text}")
        facts.append(_inventory_line(payload))
    elif kind == "escalation":
        facts.append("FATO: fiquei sem avançar por um tempo e mudei de estratégia")
        if int(payload.get("level", 0)) >= 2:
            facts.append("FATO: passei a usar o mapa de blocos conhecido, não só o que estava à vista")
    elif kind == "blocked":
        # Keep arbitrary diagnostics in the journal, never in the persona prompt.
        blocker = {
            "sem progresso após 3 estratégias": "fiquei sem progresso após três tentativas",
            "no_path": "não achei um caminho",
            "return_stalled": "não consegui voltar",
            "owner_not_visible": "não estou vendo o criador",
        }.get(str(mission.get("blocker") or ""))
        facts.append("FATO: não consegui continuar e parei de tentar" + (f": {blocker}" if blocker else ""))
        facts.append(_inventory_line(payload))
    elif kind == "succeeded":
        facts.append("FATO: a missão terminou: a picareta de ferro foi fabricada e está equipada (conferido no inventário)")
        facts.append(_inventory_line(payload))
        if payload.get("xray_assist_used"):
            facts.append("FATO: em parte do caminho usei o mapa de blocos conhecido para achar o ferro")
    elif kind == "danger":
        threat = payload.get("threat")
        health = payload.get("health")
        if not (isinstance(threat, str) and _RESOURCE.fullmatch(threat)) or not isinstance(health, (int, float)) or isinstance(health, bool):
            return []
        facts.append(f"FATO: tem um {threat} perto, a vida está em {health:g}")
    elif kind == "death":
        facts.append("FATO: morri no jogo; o que eu carregava pode ter caído")
    elif kind == "paused":
        facts.append("FATO: não consegui mais agir no jogo e pausei a tarefa")
    elif kind == "stopped":
        facts.append("FATO: a missão foi parada pelo comando de parar")
    elif kind == "interrupted":
        facts.append("FATO: minha tarefa ficou em espera; preciso que me mandem continuar")
    return [fact for fact in facts if fact]


def build_narration(store: Any, event_id: int, mission_id: str, epoch: int, *, now: float | None = None) -> dict[str, Any]:
    """Decide whether this event may be spoken now and build the persona prompt."""
    global _last_spoken
    event = store.event(int(event_id))
    if event is None or event["mission_id"] != mission_id or event["epoch"] != epoch:
        return {"ok": False, "spoken": False, "reason": "unknown_event"}
    mission = store.mission(mission_id)
    if mission is None:
        return {"ok": False, "spoken": False, "reason": "unknown_mission"}
    if mission["epoch"] != event["epoch"]:
        return {"ok": True, "spoken": False, "reason": "stale_epoch"}
    kind = event["kind"]
    if kind not in TERMINAL and mission["status"] != "running":
        return {"ok": True, "spoken": False, "reason": "mission_not_running"}
    stamp = time.monotonic() if now is None else now
    if kind not in TERMINAL and stamp - _last_spoken < MIN_GAP_S:
        return {"ok": True, "spoken": False, "reason": "rate_limited"}
    facts = event_facts(kind, event["payload"], mission)
    if not facts:
        return {"ok": True, "spoken": False, "reason": "nothing_to_say"}
    if not store.mark_spoken(int(event_id)):
        return {"ok": True, "spoken": False, "reason": "already_spoken"}
    _last_spoken = stamp
    text = (
        "[FICHA DO JOGO — fatos verificados do que acabei de fazer ou enfrentar. Contexto interno, não é fala de ninguém]\n"
        + "\n".join(facts)
        + "\nRegras da tua fala: (1) primeira pessoa, sobre o que EU acabei de fazer ou enfrentar, com verbos como \"juntei\", \"fiz\", \"achei\", \"travei\"; "
        "(2) só o que está nos FATOS acima: não cite item, ferramenta, lugar, número ou plano que não esteja neles; "
        "(3) no máximo 22 palavras, no teu tom, e pode ter UMA tirada engraçada curta de quem joga Minecraft, desde que não acrescente fato; (4) sem conselho, sem pergunta, sem prometer nada, não comece com 'Que legal' nem 'Que bom' (comece direto pelo que aconteceu ou pelo que você sente), sem JSON, sem ferramentas."
        + ("" if kind in TERMINAL else " (5) a missão continua em andamento: não diga que está parada nem esperando ordem.")
    )
    return {"ok": True, "spoken": True, "reason": "ok", "text": text, "kind": kind}


def open_store() -> Any:
    root = Path(__file__).resolve().parents[3] / "integrations" / "minecraft"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from celia_minecraft.worldmap import JevStore
    from .raizes import VAR

    return JevStore(Path(VAR) / "minecraft" / "jev.sqlite")
