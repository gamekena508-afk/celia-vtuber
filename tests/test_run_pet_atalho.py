"""Ctrl+Espaço morto a partir do 2º lançamento do Pet (medido em 2026-10-18).

O KDE guarda o componente `celia-desktop-pet` com `isActive=false` depois que o Pet fecha. No lançamento seguinte o
portal lista o atalho como "já conhecido", o Chromium não chama `BindShortcuts`, `globalShortcut.register` devolve true e
a tecla nunca chega. `tools/run_pet.sh::limpar_atalho_obsoleto` apaga SÓ o componente obsoleto antes de abrir o Electron.
Prova real (KDE, não stub): com a limpeza, 5 lançamentos seguidos => BindShortcuts + isActive=true + sinal Activated
entregue; sem ela, o 2º lançamento ficou com isActive=false e nenhum Activated.
"""
import os
import re
import stat
import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SCRIPT = RAIZ / "tools" / "run_pet.sh"


def _funcao():
    texto = SCRIPT.read_text(encoding="utf-8")
    achado = re.search(r"^limpar_atalho_obsoleto\(\) \{.*?^\}", texto, re.S | re.M)
    assert achado, "a função limpar_atalho_obsoleto sumiu do run_pet.sh"
    return achado.group(0)


def _rodar(tmp_path, *, ativo, pet_vivo):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "chamadas.log"
    qdbus = bin_dir / "qdbus6"
    qdbus.write_text(
        '#!/usr/bin/env bash\n'
        'echo "$@" >> "%s"\n'
        'case "$*" in *isActive*) echo %s;; esac\n' % (log, ativo)
    )
    pgrep = bin_dir / "pgrep"
    pgrep.write_text("#!/usr/bin/env bash\nexit %d\n" % (0 if pet_vivo else 1))
    for item in (qdbus, pgrep):
        item.chmod(item.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "ROOT": str(RAIZ)}
    subprocess.run(["bash", "-c", _funcao() + "\nlimpar_atalho_obsoleto"], env=env, check=True, capture_output=True)
    return log.read_text() if log.exists() else ""


def test_componente_obsoleto_e_limpo(tmp_path):
    chamadas = _rodar(tmp_path, ativo="false", pet_vivo=False)
    assert "Component.cleanUp" in chamadas


def test_componente_ativo_nao_e_tocado(tmp_path):
    assert "cleanUp" not in _rodar(tmp_path, ativo="true", pet_vivo=False)


def test_pet_vivo_nunca_tem_o_atalho_apagado(tmp_path):
    # 2º lançamento com Pet de pé só recarrega a janela; apagar o componente quebraria o atalho do que está rodando
    assert _rodar(tmp_path, ativo="false", pet_vivo=True) == ""


def test_limpeza_roda_antes_de_abrir_o_electron():
    texto = SCRIPT.read_text(encoding="utf-8")
    assert texto.index("\nlimpar_atalho_obsoleto\n") < texto.index("run_electron() {")
