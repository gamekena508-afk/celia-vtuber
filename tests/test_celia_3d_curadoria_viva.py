"""Rodada 9 (2026-10-06, Kurt) — a curadoria V / ? / X da bancada 3D em navegador de verdade (Chrome headless).
Pulado (nao verde) se nao houver servidor :12393, Chrome ou playwright. Interpretador: o .venv da raiz.
O 2o teste e o CONTROLE: com a gravacao do navegador sabotada em silencio o roteiro TEM de falhar (senao ele nao enxerga persistencia)."""
import os
import socket
import subprocess
import sys
from pathlib import Path
import pytest

ROTEIRO = Path(__file__).with_name("curadoria_ui_roteiro.py")
CHROME = "/usr/bin/google-chrome-stable"


def _servidor():
    try:
        with socket.create_connection(("127.0.0.1", 12393), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not (_servidor() and os.path.exists(CHROME)), reason="sem servidor :12393 ou sem Chrome")
pytest.importorskip("playwright.sync_api")


def _rodar(**env):
    e = dict(os.environ); e.update(env)
    return subprocess.run([sys.executable, str(ROTEIRO)], capture_output=True, text=True, timeout=600, env=e)


def test_curadoria_ui_passa_inteira():
    r = _rodar()
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-500:]
    assert "0 falharam" in r.stdout


def test_curadoria_ui_morde_quando_a_gravacao_e_sabotada():
    r = _rodar(SABOTAGEM="1")
    assert r.returncode != 0 and "FALHOU recarregar" in r.stdout, r.stdout[-800:]
