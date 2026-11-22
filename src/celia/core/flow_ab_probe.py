"""Unified /client-ws routing observer for the frozen FLOW20 run.

Only active with CELIA_FLOW_AB_PROBE=1. It records production-as-is routing
facts plus rule/linear/Qwen shadows. It never authorizes or executes a tool.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from celia.agent.bridge_client import needs_tool

from .linear_router_shadow import linear_router_shadow
from .local_brain import DecisionState, local_brain_runtime


def _truthy(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


_CLASS = {
    "ferramenta": "T",
    "visao": "V",
    "conversa": "C",
    "estado": "S",
    "silencio": "N",
}

_BLOCKERS = re.compile(
    r"\b(não|nao|nunca|amanhã|amanha|depois|mais tarde|se eu|se fosse|talvez|"
    r"seria|poderia|consegue|dá pra|da pra|é possível|e possivel)\b",
    re.I,
)

_RULES = [
    (re.compile(r"^(por favor\s+)?(abre|liga)\s+(o\s+)?(discord|bot do discord)\b", re.I), "discord", "discord_ligar_bot"),
    (re.compile(r"^(por favor\s+)?(posta|manda)\b.*\bdiscord\b", re.I), "discord", "discord_postar_mensagem"),
    (re.compile(r"^(por favor\s+)?(checa|confere|me diz)\b.*\bstatus\b.*\bdiscord\b", re.I), "discord", "discord_status_bot"),
    (re.compile(r"^(por favor\s+)?(abre|inicia|coloca)\s+.*\bjogo\b", re.I), "jogo", "abrir_jogo"),
    (re.compile(r"^(por favor\s+)?(fecha|encerra)\s+(o\s+)?(jogo|partida)\b", re.I), "jogo", "fechar_jogo"),
    (re.compile(r"^(por favor\s+)?(lista|mostra)\s+(os\s+)?jogos\b", re.I), "jogo", "listar_jogos"),
    (re.compile(r"^(por favor\s+)?abre\s+(o\s+)?(navegador|browser)\b", re.I), "sistema", "open_browser"),
    (re.compile(r"^(por favor\s+)?(tira|faz|quero)\s+.*\b(print|screenshot|captura)\b", re.I), "sistema", "take_screenshot"),
    (re.compile(r"^(por favor\s+)?(checa|confere|verifica)\s+.*\b(servico|serviço)\b", re.I), "sistema", "status_servico"),
]

_FORCED = [
    (re.compile(r"^(?:qual (?:e )?o )?status (?:do |da )?discord\b", re.I), "discord", "discord_status_bot"),
    (re.compile(r"^(?:quais (?:sao )?os )?jogos(?: disponiveis)?\b|^(?:lista|liste|listar) (?:os )?jogos\b", re.I), "jogo", "listar_jogos"),
    (re.compile(r"^(?:tira|tire|faz|faca) (?:um )?(?:print|screenshot)(?: da tela)?\b", re.I), "sistema", "take_screenshot"),
]


class FlowABProbe:
    def __init__(self) -> None:
        self._write_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return _truthy("CELIA_FLOW_AB_PROBE", "0")

    @staticmethod
    def _log_path() -> Path:
        root = Path(__file__).resolve().parents[3]
        return Path(
            os.getenv(
                "CELIA_FLOW_AB_LOG",
                str(root / "logs" / "router_flow_unified.jsonl"),
            )
        )

    @staticmethod
    def _production_snapshot(text: str) -> dict[str, Any]:
        started = time.perf_counter()
        direct = re.sub(r"^\s*(?:celia[\s,:!.-]+)?", "", text.casefold()).strip()
        for pattern, category, tool in _FORCED:
            if pattern.search(direct):
                return {
                    "decided_by": "forced_system_rule",
                    "route": "T",
                    "category": category,
                    "tool": tool,
                    "executed": False,
                    "authorized": None,
                    "intercepted_by": "forced_system_rule",
                    "latency_ms": {"total": round((time.perf_counter() - started) * 1000, 4)},
                }

        intent = needs_tool(text)
        if intent == "vision":
            route = "V"
            category = tool = None
        elif intent:
            route = "T"
            category = {
                "discord": "discord",
                "system": "sistema",
                "minecraft": "jogo",
            }.get(intent)
            tool = None
        else:
            route, category, tool = "C", None, None
        return {
            "decided_by": "legacy_needs_tool" if intent else "legacy_default",
            "route": route,
            "category": category,
            "tool": tool,
            "executed": False,
            "authorized": None,
            "intercepted_by": None,
            "latency_ms": {"total": round((time.perf_counter() - started) * 1000, 4)},
        }

    @staticmethod
    def _rule_snapshot(text: str) -> dict[str, Any]:
        stripped = text.strip()
        if _BLOCKERS.search(stripped) or stripped.endswith("?"):
            return {"matched": False}
        for pattern, category, tool in _RULES:
            if pattern.search(stripped):
                return {
                    "matched": True,
                    "route": "T",
                    "category": category,
                    "tool": tool,
                    "latency_ms": 0.0,
                }
        return {"matched": False}

    @staticmethod
    def _linear_snapshot(text: str) -> dict[str, Any] | None:
        try:
            decision = linear_router_shadow.predict(text)
        except Exception:
            return None
        out = decision.as_dict()
        return {
            "route": decision.cls,
            "probs": out["probs"],
            "margin": decision.margin,
            "thresholds_id": "fallback_v1",
            "latency_ms": decision.latency_ms,
            "would_confirm": decision.margin < 0.113483,
        }

    async def observe(
        self,
        *,
        turn_id: str,
        text: str,
        orchestrator_intent: str | None,
        case_id: str | None = None,
        model_label: str | None = None,
    ) -> None:
        if not self.enabled:
            return

        case_id = str(case_id or "").strip()
        if not case_id:
            # Normal user traffic is not part of a frozen benchmark.
            return

        started = time.perf_counter()
        production = self._production_snapshot(text)
        rule = self._rule_snapshot(text)
        linear = await asyncio.to_thread(self._linear_snapshot, text)
        production_done_ms = (time.perf_counter() - started) * 1000

        state = DecisionState(
            turn_id=str(turn_id or ""),
            text=str(text or ""),
            mode="chat",
            celia_speaking=False,
            tool_running=False,
            screen_changed=False,
            vision_age_ms=None,
            app=None,
            legacy_decision=None,
        )

        qwen = None
        qwen_error = None
        qwen_started = time.perf_counter()
        try:
            decision, probs = await asyncio.wait_for(
                local_brain_runtime.brain.decide_observed(state, use_scheduler=True),
                timeout=max(1.5, float(os.getenv("CELIA_FLOW_AB_TIMEOUT", "3.0"))),
            )
            qwen = {
                "route": _CLASS.get(decision.route, "C"),
                "category": decision.category,
                "tool": decision.tool,
                "probs": {k: round(float(v), 8) for k, v in probs.items()},
                "reused_production_call": False,
                "ran_after_production_ms": round(production_done_ms, 4),
                "latency_ms": {
                    "queue_wait": None,
                    "total_model": decision.latency_ms,
                    "total": round((time.perf_counter() - qwen_started) * 1000, 4),
                },
                "cache": {"prefix_hit": None},
            }
        except Exception as exc:
            qwen_error = {
                "type": type(exc).__name__,
                "message": str(exc)[:300],
                "elapsed_ms": round((time.perf_counter() - qwen_started) * 1000, 4),
            }

        record = {
            "schema_version": 2,
            "case_id": case_id,
            "turn_id": state.turn_id,
            "timestamp_unix": round(time.time(), 3),
            "text": state.text,
            "orchestrator_intent": orchestrator_intent,
            "model_label": model_label or "qwen",
            "production": production,
            "rule_shadow": rule,
            "linear_shadow": linear,
            "qwen_shadow": qwen,
            "qwen_error": qwen_error,
        }
        path = self._log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


flow_ab_probe = FlowABProbe()
