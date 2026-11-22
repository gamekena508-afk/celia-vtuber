"""Contas Cloudflare separadas: 3 só para LLM (texto) e 3 só para ASR (Whisper da Workers AI).

As 6 contas vivem no 9router (`~/.9router/db/data.sqlite`, provedor `cloudflare-ai`). A divisão
é por NOME da conexão, na seção `cloudflare_contas:` do config/model_chains.yaml. O 9router só
reveza entre as conexões ativas, então `tools/cloudflare_separar.py` deixa ativas as de LLM e
desativa as de ASR; o ASR chama a Cloudflare direto com as credenciais lidas daqui.

Credenciais são lidas em memória, em modo somente leitura, e nunca vão para log nem para o
registro da cota. Aqui só circulam nomes de conexão.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote

from loguru import logger

DEFAULT_DB = "~/.9router/db/data.sqlite"
GRUPOS = ("llm", "asr")


def secao(cfg: dict[str, Any] | None) -> dict[str, Any]:
    return (cfg or {}).get("cloudflare_contas") or {}


def caminho_db(cfg: dict[str, Any] | None) -> Path:
    # CELIA_ROUTER_DB (mesma variável de tools/celia_cota.py e celia_rota.py) vence o YAML.
    return Path(os.path.expanduser(os.environ.get("CELIA_ROUTER_DB") or str(secao(cfg).get("router_db") or DEFAULT_DB)))


def nomes(cfg: dict[str, Any] | None, grupo: str) -> list[str]:
    return [str(n) for n in (secao(cfg).get(grupo) or [])]


def validar_separacao(cfg: dict[str, Any] | None) -> None:
    """Uma conta nunca pode servir texto e transcrição ao mesmo tempo."""
    comuns = set(nomes(cfg, "llm")) & set(nomes(cfg, "asr"))
    if comuns:
        raise ValueError("conta Cloudflare em LLM e ASR ao mesmo tempo: " + ", ".join(sorted(comuns)))


def carregar_contas(cfg: dict[str, Any] | None, grupo: str) -> list[dict[str, str]]:
    """Contas do grupo, na ordem da config: [{nome, link, account_id, token}]."""
    if grupo not in GRUPOS:
        raise ValueError(f"grupo desconhecido: {grupo}")
    validar_separacao(cfg)
    db = caminho_db(cfg)
    try:
        con = sqlite3.connect(f"file:{quote(str(db))}?mode=ro", uri=True, timeout=3)
    except sqlite3.Error as exc:
        logger.warning("[Cloudflare contas] não abriu o banco do 9router ({})", type(exc).__name__)
        return []
    try:
        linhas = dict(con.execute("select name, data from providerConnections where provider = 'cloudflare-ai'"))
    except sqlite3.Error as exc:
        logger.warning("[Cloudflare contas] leitura falhou ({})", type(exc).__name__)
        return []
    finally:
        con.close()
    contas = []
    for i, nome in enumerate(nomes(cfg, grupo), 1):
        try:
            dados = json.loads(linhas[nome])
        except (KeyError, ValueError):
            logger.warning("[Cloudflare contas] conexão '{}' não encontrada no 9router", nome)
            continue
        account_id = str((dados.get("providerSpecificData") or {}).get("accountId") or "")
        token = str(dados.get("apiKey") or "")
        if account_id and token:
            contas.append({"nome": nome, "link": f"cf-{grupo}/conta{i}", "account_id": account_id, "token": token})
        else:
            logger.warning("[Cloudflare contas] conexão '{}' sem credencial completa", nome)
    return contas
