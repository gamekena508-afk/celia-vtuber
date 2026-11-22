"""Catálogo de roteiros de gravação para o teleprompter do celular.

Responde três perguntas que o painel `/roteiro/` faz e ninguém mais respondia:

1. quais roteiros existem em disco, com id/versão/beats;
2. qual deles o Criador já marcou como gravado, e com que evidência de sessão;
3. em qual a interface deve cair quando ele abre o painel.

Regras decididas pelo dono em `docs/planos/PLANO-SELETOR-ROTEIRO-2026-09-30.md`:

- **decisão 3(b)** a marca de "gravado" é sempre do botão dele. A evidência de log
  aparece ao lado, e não decide nada. Medido em 2026-09-30: `ia-vs-criador` tinha
  1415 sessões e 4 TOMADA BOA — contagem de sessão mede teste, não gravação.
- **decisão 4(a)** a marca mora no servidor, em `var/roteiros_gravados.json`, para
  valer em qualquer celular e sobreviver ao reinício.
- **decisão 5(a)** quando todos estão gravados, a interface fica no último que ele
  selecionou.
- **decisão 7 (ORDEM)** a interface nunca o joga num roteiro já marcado como
  gravado; ele só cai num desses escolhendo na mão.

Este módulo não sobe servidor, não fala com o LLM e não toca no roteiro ativo do
runtime: ele só lê disco e devolve dados.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

from .raizes import RAIZ

ROTEIROS_DIR = RAIZ / "roteiros"
MARCAS_PATH = RAIZ / "var" / "roteiros_gravados.json"
SESSOES_DIR = RAIZ / "var" / "roteiro_sessions"
MARCAS_VERSION = 1


def _ler_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _caminho_legivel(arquivo: Path) -> str:
    """Relativo à raiz quando dá; absoluto quando não dá.

    `relative_to` estoura quando o diretório de roteiros não está sob a raiz —
    é o caso de qualquer árvore de teste e de uma pasta apontada por variável.
    Um catálogo não pode morrer por causa do rótulo de um caminho.
    """
    try:
        return str(arquivo.relative_to(RAIZ))
    except ValueError:
        return str(arquivo)


def roteiros_em_disco() -> list[dict[str, Any]]:
    """Todo `roteiros/*.json` que realmente seja um roteiro, em ordem estável.

    Um arquivo sem `id` ou sem `beats` utilizáveis não é roteiro e fica de fora —
    é o caso medido de `IA_VS_CRIADOR_VOLTA_DO_CANAL.md.rotulo.json`.
    """
    achados: list[dict[str, Any]] = []
    if not ROTEIROS_DIR.is_dir():
        return achados
    for arquivo in sorted(ROTEIROS_DIR.glob("*.json")):
        dados = _ler_json(arquivo)
        if not isinstance(dados, dict):
            continue
        identificador = str(dados.get("id") or "").strip()
        beats = [
            item
            for item in (dados.get("beats") or [])
            if isinstance(item, dict) and item.get("id")
        ]
        if not identificador or not beats:
            continue
        # Beat sem nenhuma fala não é gravável: o teleprompter abre vazio e
        # AVANÇAR FALA não tem o que concluir. Medido em dois arquivos reais.
        com_fala = sum(
            1
            for item in beats
            if any(
                str((linha or {}).get("text") or "").strip()
                for linha in (item.get("lines") or [])
                if isinstance(linha, dict)
            )
        )
        achados.append(
            {
                "id": identificador,
                "titulo": str(dados.get("title") or identificador),
                "subtitulo": str(dados.get("subtitle") or ""),
                "versao": dados.get("version"),
                "beats": len(beats),
                "beats_com_fala": com_fala,
                "rascunho": com_fala == 0,
                "beat_ids": {str(item["id"]) for item in beats},
                "arquivo": _caminho_legivel(arquivo),
            }
        )
    return achados


def evidencia_das_sessoes(beat_ids_por_roteiro: dict[str, set[str]]) -> dict[str, dict[str, Any]]:
    """Conta sessões, tomadas e beats por `script_id`, cruzando com o disco.

    Dois cuidados que vieram de medição, não de gosto:

    - `beats_vistos` conta apenas beats que **existem no roteiro de hoje**. Sem esse
      cruzamento o IA vs Criador aparecia com `24/16`, porque logs antigos carregam
      ids de versões anteriores do roteiro.
    - `script_id` que não casa com nenhum roteiro em disco é devolvido como órfão e
      nunca vira item de catálogo. Medidos: `session` (43 sessões) e
      `discord-tutorial` sem sufixo (3).
    """
    agregado: dict[str, dict[str, Any]] = {}
    if not SESSOES_DIR.is_dir():
        return agregado
    for arquivo in SESSOES_DIR.glob("*.jsonl"):
        script_id = None
        try:
            conteudo = arquivo.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for linha in conteudo.splitlines():
            try:
                evento = json.loads(linha)
            except ValueError:
                continue
            if not isinstance(evento, dict):
                continue
            tipo = evento.get("event")
            dados = evento.get("data") or {}
            if tipo == "session.started":
                script_id = str(dados.get("script_id") or "") or None
                if script_id:
                    item = agregado.setdefault(
                        script_id,
                        {"sessoes": 0, "tomadas_boas": 0, "descartadas": 0,
                         "_beats": set(), "ultima_sessao": 0.0},
                    )
                    item["sessoes"] += 1
            if not script_id:
                continue
            item = agregado[script_id]
            quando = evento.get("timestamp") or 0
            if quando > item["ultima_sessao"]:
                item["ultima_sessao"] = quando
            if tipo == "beat.changed":
                beat = str(dados.get("beat_id") or "")
                if beat:
                    item["_beats"].add(beat)
            elif tipo == "take.marked":
                marca = str(dados.get("mark") or "")
                if marca == "good":
                    item["tomadas_boas"] += 1
                elif marca in {"discarded", "discard"}:
                    item["descartadas"] += 1

    resultado: dict[str, dict[str, Any]] = {}
    for script_id, item in agregado.items():
        beats_atuais = beat_ids_por_roteiro.get(script_id)
        vistos = item["_beats"] & beats_atuais if beats_atuais is not None else set()
        resultado[script_id] = {
            "sessoes": item["sessoes"],
            "tomadas_boas": item["tomadas_boas"],
            "descartadas": item["descartadas"],
            "beats_vistos": len(vistos),
            "ultima_sessao": item["ultima_sessao"] or None,
            "orfao": beats_atuais is None,
        }
    return resultado


def ler_marcas() -> dict[str, Any]:
    dados = _ler_json(MARCAS_PATH)
    if not isinstance(dados, dict):
        return {"version": MARCAS_VERSION, "gravados": {}, "ultimo_selecionado": None}
    gravados = dados.get("gravados")
    return {
        "version": dados.get("version", MARCAS_VERSION),
        "gravados": gravados if isinstance(gravados, dict) else {},
        "ultimo_selecionado": dados.get("ultimo_selecionado") or None,
    }


def _gravar_marcas(marcas: dict[str, Any]) -> None:
    MARCAS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporario = MARCAS_PATH.with_suffix(".tmp")
    temporario.write_text(
        json.dumps(marcas, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporario.replace(MARCAS_PATH)


def marcar_gravado(script_id: str, gravado: bool) -> dict[str, Any]:
    """O botão do dono. Nada além dele escreve aqui — decisão 3(b)."""
    identificador = str(script_id or "").strip()
    if not identificador:
        raise ValueError("script_id_vazio")
    conhecidos = {item["id"] for item in roteiros_em_disco()}
    if identificador not in conhecidos:
        raise ValueError("roteiro_desconhecido")
    marcas = ler_marcas()
    if gravado:
        marcas["gravados"][identificador] = {"em": time.time()}
    else:
        marcas["gravados"].pop(identificador, None)
    _gravar_marcas(marcas)
    logger.info("[Roteiro] marca de gravado: {} -> {}", identificador, gravado)
    return marcas


def registrar_selecao(script_id: str) -> dict[str, Any]:
    """Guarda o último selecionado — é o que a decisão 5(a) usa quando tudo já foi gravado."""
    identificador = str(script_id or "").strip()
    if not identificador:
        raise ValueError("script_id_vazio")
    marcas = ler_marcas()
    marcas["ultimo_selecionado"] = identificador
    _gravar_marcas(marcas)
    return marcas


def sugerir(catalogo: list[dict[str, Any]], ultimo_selecionado: str | None) -> dict[str, Any]:
    """Em qual roteiro a interface deve cair, e por quê.

    ORDEM do dono (decisão 7): nunca sugerir um já marcado como gravado.
    Decisão 5(a): se todos estiverem gravados, fica no último que ele selecionou.
    """
    # Rascunho não é destino: o teleprompter abriria vazio. Ele continua na
    # lista, escolhível na mão, mas a interface nunca cai nele sozinha.
    pendentes = [
        item for item in catalogo if not item["gravado"] and not item.get("rascunho")
    ]
    if pendentes:
        ativo_pendente = next((i for i in pendentes if i["ativo"]), None)
        escolhido = ativo_pendente or pendentes[0]
        return {
            "id": escolhido["id"],
            "motivo": "ativo_e_pendente" if ativo_pendente else "primeiro_pendente",
            "todos_gravados": False,
        }
    if not catalogo:
        return {"id": None, "motivo": "sem_roteiro", "todos_gravados": False}
    ultimo_valido = next(
        (item["id"] for item in catalogo if item["id"] == ultimo_selecionado), None
    )
    return {
        "id": ultimo_valido,
        "motivo": "todos_gravados_fica_no_ultimo" if ultimo_valido else "todos_gravados_sem_ultimo",
        "todos_gravados": True,
    }


def catalogo(ativo_id: str | None = None) -> dict[str, Any]:
    """A resposta completa que o painel do celular consome."""
    encontrados = roteiros_em_disco()
    beats_por_id = {item["id"]: item["beat_ids"] for item in encontrados}
    evidencias = evidencia_das_sessoes(beats_por_id)
    marcas = ler_marcas()
    gravados = marcas["gravados"]

    itens: list[dict[str, Any]] = []
    for item in encontrados:
        marca = gravados.get(item["id"]) or {}
        evidencia = evidencias.get(item["id"]) or {
            "sessoes": 0, "tomadas_boas": 0, "descartadas": 0,
            "beats_vistos": 0, "ultima_sessao": None, "orfao": False,
        }
        itens.append(
            {
                "id": item["id"],
                "titulo": item["titulo"],
                "subtitulo": item["subtitulo"],
                "versao": item["versao"],
                "beats": item["beats"],
                "beats_com_fala": item["beats_com_fala"],
                "rascunho": item["rascunho"],
                "arquivo": item["arquivo"],
                "ativo": item["id"] == ativo_id,
                "gravado": item["id"] in gravados,
                "gravado_em": marca.get("em"),
                "evidencia": evidencia,
            }
        )

    orfaos = sorted(k for k, v in evidencias.items() if v.get("orfao"))
    return {
        "roteiros": itens,
        "ativo": ativo_id,
        "ultimo_selecionado": marcas["ultimo_selecionado"],
        "orfaos": orfaos,
        "sugestao": sugerir(itens, marcas["ultimo_selecionado"]),
    }
