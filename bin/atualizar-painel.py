#!/usr/bin/env python3
"""Reescreve o painel da tela principal do cofre da Celia.

agente: claude | 2026-08-10

Le o estado real dos servicos e o que mudou no cofre, e substitui o bloco
entre os marcadores CELIA-PAINEL-INICIO e CELIA-PAINEL-FIM em CÉLIA.md.

Nao inventa estado: servico que nao responde vira "fora do ar", e o texto diz
qual porta falhou. Somente leitura em rede; a unica escrita e a nota.

Uso (a partir da raiz do repositório):
    python3 bin/atualizar-painel.py
"""

from __future__ import annotations

import json
import pathlib
import time
import urllib.error
import urllib.request
from datetime import datetime

COFRE = pathlib.Path(__file__).resolve().parents[1]
NOTA = COFRE / "vault" / "CÉLIA.md"
INICIO = "%% CELIA-PAINEL-INICIO"
FIM = "%% CELIA-PAINEL-FIM %%"
TIMEOUT = 4.0

SERVICOS = [
    ("nucleo", "http://127.0.0.1:12393/", 12393),
    ("legendas", "http://127.0.0.1:8788/obs_subtitles/state", 8788),
    ("voz", "http://127.0.0.1:8000/health", 8000),
    ("ouvido (TAGARELA)", "http://127.0.0.1:8011/health", 8011),
    ("roteador", "http://127.0.0.1:20127/", 20127),
]

IGNORADOS = (
    "_archive/",   # imports, backups e quarentena
    "var/",        # logs, state, runtime, track: dado mutavel
    "services/", "host/", "secrets/", ".contract-backup/",
    ".obsidian/", "skills/pending/",
)


def buscar(url: str) -> tuple[int | None, str]:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            return r.status, r.read(2048).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return None, ""


def estado_servicos() -> tuple[list[str], bool]:
    linhas, nucleo_vivo = [], False
    for nome, url, porta in SERVICOS:
        codigo, _ = buscar(url)
        vivo = codigo is not None and codigo < 500
        if nome == "nucleo":
            nucleo_vivo = vivo
        marca = "no ar" if vivo else "fora do ar"
        detalhe = f"HTTP {codigo}" if codigo is not None else "sem resposta"
        linhas.append(f"> - **{nome}** `:{porta}` — {marca} ({detalhe})")
    return linhas, nucleo_vivo


def o_que_estou_fazendo() -> str:
    codigo, corpo = buscar("http://127.0.0.1:8788/obs_subtitles/state")
    if codigo != 200:
        return "não sei dizer: o canal de legendas não respondeu"
    try:
        dado = json.loads(corpo)
    except json.JSONDecodeError:
        return "não sei dizer: o canal de legendas respondeu algo que não é JSON"
    tipo = dado.get("type", "desconhecido")
    traducao = {
        "idle": "parada, esperando você falar",
        "speaking": "falando",
        "thinking": "pensando",
        "listening": "ouvindo",
    }
    texto = traducao.get(tipo, f"em estado `{tipo}`")
    quando = dado.get("server_time")
    if isinstance(quando, (int, float)):
        idade = max(0, int(time.time() - quando))
        return f"{texto} (leitura de {idade}s atrás)"
    return texto


def notas_recentes(quantas: int = 5) -> list[tuple[pathlib.Path, float]]:
    achadas = []
    for p in COFRE.rglob("*.md"):
        rel = p.relative_to(COFRE).as_posix()
        if rel == "vault/CÉLIA.md" or any(rel.startswith(x) for x in IGNORADOS):
            continue
        achadas.append((p, p.stat().st_mtime))
    achadas.sort(key=lambda x: x[1], reverse=True)
    return achadas[:quantas]


def montar() -> str:
    servicos, nucleo_vivo = estado_servicos()
    cabeca = "online" if nucleo_vivo else "offline"
    recentes = notas_recentes()
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")

    b = [
        f"{INICIO} %%",
        "",
        f"> [!estado] Estado da Célia: {cabeca}",
        *servicos,
        f"> - medido em {agora}",
        "",
        "> [!fazendo] O que estou fazendo",
        f"> {o_que_estou_fazendo()}",
        "",
        "> [!atividades] Últimas atividades",
    ]
    if recentes:
        for p, m in recentes:
            quando = datetime.fromtimestamp(m).strftime("%d/%m %H:%M")
            b.append(f"> - {quando} — [[{p.stem}]]")
    else:
        b.append("> - nada mudou no cofre")

    b += ["", "> [!retomar] Continuar de onde parei"]
    if recentes:
        b.append(f"> A última coisa que mexi foi [[{recentes[0][0].stem}]].")
    else:
        b.append("> Não há de onde retomar.")
    b += ["", FIM]
    return "\n".join(b)


def main() -> int:
    texto = NOTA.read_text(encoding="utf-8")
    i = texto.find(INICIO)
    j = texto.find(FIM)
    if i == -1 or j == -1:
        print(f"marcadores nao encontrados em {NOTA}; nada foi escrito")
        return 1
    novo = texto[:i] + montar() + texto[j + len(FIM):]
    if novo == texto:
        print("painel ja estava igual; nada escrito")
        return 0
    NOTA.write_text(novo, encoding="utf-8")
    print(f"painel atualizado: {NOTA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
