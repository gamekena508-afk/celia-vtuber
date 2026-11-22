"""Configuração das filas de fallback (Jev e Célia).

Lê config/model_chains.yaml (ou o caminho em CELIA_MODEL_CHAINS). Cada cérebro
tem `enabled`; com false, a Célia segue exatamente como antes. As variáveis
CELIA_CHAIN_JEV / CELIA_CHAIN_CELIA ("1" ou "0") sobrepõem o `enabled`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .raizes import RAIZ

DEFAULT_PATH = RAIZ / "config" / "model_chains.yaml"
BRAINS = ("jev", "celia")


def config_path() -> Path:
    return Path(os.environ.get("CELIA_MODEL_CHAINS") or DEFAULT_PATH)


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def brain_enabled(cfg: dict[str, Any], brain: str) -> bool:
    env = os.environ.get(f"CELIA_CHAIN_{brain.upper()}")
    if env is not None and env.strip() != "":
        return env.strip().lower() in ("1", "true", "sim", "yes", "on")
    return bool((cfg.get(brain) or {}).get("enabled", False))


def chain_links(cfg: dict[str, Any], brain: str) -> list[dict[str, Any]]:
    """Normaliza a fila: cada item vira {model, base_url, api_key, extra_body}."""
    gw = cfg.get("gateway") or {}
    base_url = gw.get("base_url", "http://127.0.0.1:20127/v1")
    api_key = gw.get("api_key", "local-gateway")
    default_extra = (cfg.get(brain) or {}).get("extra_body") or {}
    links = []
    for item in (cfg.get(brain) or {}).get("chain") or []:
        if isinstance(item, str):
            item = {"model": item}
        if not isinstance(item, dict) or not item.get("model"):
            continue
        # Opt-in rollout: a local endpoint must never stall the live chain until
        # its llama-server has been started and the GPU verified.
        enabled_env = item.get("enabled_env")
        if enabled_env and os.getenv(str(enabled_env), "0").strip().lower() not in ("1", "true", "on", "yes"):
            continue
        link = {
            "model": str(item["model"]),
            "base_url": item.get("base_url") or base_url,
            "api_key": item.get("api_key") or api_key,
            "extra_body": item.get("extra_body", default_extra) or {},
        }
        # max_tokens por elo (opcional): sem a chave, vale o max_tokens da fila.
        # Permite teto curto no Groq sem cortar o raciocinio do Gemini/OpenRouter.
        if item.get("max_tokens"):
            link["max_tokens"] = int(item["max_tokens"])
        if item.get("context_tokens"):
            link["context_tokens"] = int(item["context_tokens"])
        if item.get("ttft_sec"):
            link["ttft_sec"] = float(item["ttft_sec"])
        if item.get("local_gpu_job"):
            link["local_gpu_job"] = True
        links.append(link)
    return links
