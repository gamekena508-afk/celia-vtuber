"""Raiz única da árvore da Célia — uma conta só, num lugar só.

agente: claude | 2026-08-11 | Fatia 5 do plano travado

O PROBLEMA MEDIDO
-----------------
Os caminhos de runtime não podem depender da profundidade do arquivo. O corpo
Minecraft é resolvido aqui como `integrations/minecraft/mineflayer`, e o MCP
separado usa o mesmo valor em `mcp/celia_paths.py`.

Este módulo é a conta única. Mover a árvore passa a ser mexer aqui.

POR QUE NÃO MEXE NO `celia_paths.py` QUE JÁ EXISTIA
--------------------------------------------------
`mcp/celia_paths.py` já fazia isto certo — mas mora em `mcp/`, que não é
pacote importável de dentro de `src/celia/`. Os dois coexistem de
propósito: os MCPs (processos separados, com `sys.path` próprio) continuam
usando aquele; o pacote usa este. Ambos derivam do mesmo lugar e respeitam
as mesmas variáveis de ambiente, então não podem divergir em silêncio.

CONTRATO
--------
Toda raiz aceita variável de ambiente. Isso não é enfeite: é como a Fatia 3
moveu o spool de emoção pra `var/track/` sem tocar em código nenhum.
"""

from __future__ import annotations

import os
from pathlib import Path
from .state_store import default_state_root

# 2026-08-11, arquitetura nova (ordem do Pai): `Célia/` perdeu o
# nome e VIROU a raiz. Antes o código morava três níveis abaixo do workspace
# (`Célia/celia/Célia/`) e as duas raízes eram lugares diferentes.
# Agora são o MESMO lugar, e é isso que simplifica a conta inteira.
#
# Este arquivo mora em:
#   <raiz>/src/celia/core/raizes.py
#         3      2            1     0        <- parents[N]
# Uma conta só, escrita uma vez, conferível a olho contra a linha acima.
_ESTE = Path(__file__).resolve()
PACOTE = _ESTE.parents[1]          # src/celia
SRC = _ESTE.parents[2]             # src
RAIZ = _ESTE.parents[3]            # a raiz: código E árvore, no mesmo lugar

# Mantidos por compatibilidade: quem importava `VTUBER` continua funcionando.
# Depois do achatamento os dois nomes apontam pro mesmo lugar, de propósito —
# não são duas verdades, é a mesma verdade com dois nomes herdados.
VTUBER = RAIZ


def _raiz(env: str, padrao: Path) -> Path:
    valor = os.getenv(env, "").strip()
    return Path(valor).expanduser().resolve() if valor else padrao


# `CELIA_WORKSPACE` é o mesmo nome que `mcp/celia_paths.py` já usava —
# trocar por outro criaria duas verdades.
WORKSPACE = _raiz("CELIA_WORKSPACE", RAIZ)

MINDCRAFT = _raiz("CELIA_MINECRAFT_BODY_DIR", RAIZ / "integrations" / "minecraft" / "mineflayer")
REACT = _raiz("CELIA_REACT_DIR", WORKSPACE / "extensions" / "react")
EXTENSOES = _raiz("CELIA_EXTENSIONS_DIR", WORKSPACE / "extensions")
WEB = _raiz("CELIA_WEB_DIR", WORKSPACE / "web")
SEGREDOS = _raiz("CELIA_SECRETS_DIR", WORKSPACE / "secrets")

# `track/` tem duas variáveis por herança: `CELIA_TRACK_ROOT` é a que
# `bin/ligar.sh` exporta hoje (apontando pra var/track), e o código antigo já
# a lia. Mantida como primeira opção pra não quebrar o que já funciona.
TRACK = _raiz("CELIA_TRACK_ROOT", _raiz("CELIA_TRACK_DIR", WORKSPACE / "track"))

# Estado mutável da V02 fora da árvore de código. `CELIA_STATE_DIR` define
# a raiz nova; `CELIA_VAR_DIR` continua tendo precedência e mantém o layout
# antigo quando foi explicitamente configurado.
STATE_ROOT = _raiz("CELIA_STATE_DIR", default_state_root())
_VAR_LEGACY_CONFIGURED = bool(os.getenv("CELIA_VAR_DIR", "").strip())
VAR = _raiz("CELIA_VAR_DIR", STATE_ROOT)

# Arquivos concretos que mais de um módulo procurava por conta própria.
MINECRAFT_BODY = MINDCRAFT
MODOS = RAIZ / ".modos"


def diagnostico() -> dict[str, str]:
    """Todas as raízes e se existem. Serve pra conferir em vez de confiar."""
    itens = {
        "WORKSPACE": WORKSPACE, "RAIZ": RAIZ, "MINDCRAFT": MINDCRAFT,
        "REACT": REACT, "EXTENSOES": EXTENSOES, "VAR": VAR, "WEB": WEB,
        "SEGREDOS": SEGREDOS, "TRACK": TRACK, "MINECRAFT_BODY": MINECRAFT_BODY,
        "MODOS": MODOS,
    }
    return {k: f"{v} {'[ok]' if v.exists() else '[NAO EXISTE]'}" for k, v in itens.items()}


# ── Estado de execução (2026-08-11) ──────────────────────────────────────
# Antes daqui, cada módulo montava o caminho de estado por conta própria, com
# string relativa ao diretório de trabalho: `os.path.join("chat_history", …)`,
# `directory="cache"`, `"logs/live_chat_*.jsonl"`. Isso é a mesma função
# escrita em cinco lugares — e obrigava um symlink na raiz para cada pasta,
# só para o `cwd` continuar encontrando.
#
# Agora o caminho vem daqui. Os symlinks de remendo saíram da raiz.
if _VAR_LEGACY_CONFIGURED:
    # Compatibilidade explícita com instalações que ainda exportam
    # CELIA_VAR_DIR: os subcaminhos existentes permanecem iguais.
    CACHE = VAR / "cache"
    CHAT_HISTORY = VAR / "chat_history"
    MEMORY_STATE = VAR / "memoria"
    MEMORY_BOOK = VAR / "memory_book"
    OUTPUT = VAR / "output"
    LOGS_APP = VAR / "logs-app"
    SESSIONS = VAR / "sessions"
    APPLICATION = VAR / "application"
    RUNTIME = VAR / "runtime"
else:
    CHAT_HISTORY = VAR / "history"
    MEMORY_STATE = VAR / "memory"
    MEMORY_BOOK = MEMORY_STATE / "book"
    SESSIONS = VAR / "sessions"
    APPLICATION = VAR / "application"
    RUNTIME = VAR / "runtime"
    CACHE = RUNTIME / "cache"
    OUTPUT = APPLICATION / "output"
    LOGS_APP = APPLICATION / "logs"


def garantir(*dirs) -> None:
    """Cria os diretórios de estado se faltarem. Idempotente, barato."""
    for d in (dirs or (CHAT_HISTORY, MEMORY_STATE, MEMORY_BOOK, SESSIONS, APPLICATION, CACHE, OUTPUT, LOGS_APP)):
        d.mkdir(parents=True, exist_ok=True)
