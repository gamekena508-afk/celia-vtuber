"""Torna o pacote `celia` (src-layout) importavel pelos testes.

Sem isto, `pytest tests/` falha na coleta de 4 modulos com
`ModuleNotFoundError: No module named 'celia'`, porque src/ nao entra
no sys.path e o pacote nao esta instalado em modo editavel.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
for _path in (_ROOT, _ROOT / "src"):
    if _path.exists() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


import os

# Teste não escreve no dataset nem fala com o jogo vivo. Medido em 2026-10-21: os pytest rodaram junto de uma gravação e ficaram no trace de treino real
# (chamadas com system vazio e saídas "pronta"/"resposta"/"5 ok"), e uma ficha de teste ("alguém", "Perfil roteiro") se misturou à do turno real.
# Definido ANTES de qualquer import de celia: os gravadores leem estas variáveis ao nascer.
os.environ.setdefault("CELIA_TRAINING_TRACE_ENABLED", "0")
os.environ.setdefault("CELIA_LATENCY_TRACE_ENABLED", "0")
os.environ.setdefault("CELIA_EPISODE_CONTEXT", "0")  # sem leitura do daemon vivo (teste injeta o retrato)

import pytest


@pytest.fixture(autouse=True)
def _sessoes_de_roteiro_fora_do_var_de_producao(tmp_path, monkeypatch):
    """Teste não escreve no histórico de gravação do dono.

    `RoteiroLiveRuntime.activate()` grava uma sessão em `var/roteiro_sessions/`
    toda vez que é chamado — inclusive quando o objeto nasce dentro de um teste.
    Medido em 2026-09-30: uma sessão de trabalho deixou 180 arquivos de sessão
    falsos ali, e os antigos já somavam 1415 para o `ia-vs-criador` contra 4
    TOMADA BOA. Esses números apareciam na interface como "histórico de
    gravação" e não eram gravação nenhuma.

    Redireciona o diretório para o `tmp_path` do próprio teste. Quem quiser
    testar a escrita do log continua podendo apontar para onde quiser.
    """
    try:
        from celia.core import roteiro_live
    except Exception:  # noqa: BLE001 - coleta não pode depender deste import
        return
    monkeypatch.setattr(
        roteiro_live, "SESSION_LOG_DIR", tmp_path / "roteiro_sessions", raising=False
    )
