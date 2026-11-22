"""Enquete da live (overlay do modo mobile, pedido do Pai em 2026-10-25).

Placar por presentes do TikTok: cada presente mapeado para um lado soma 1 ponto por unidade.
O leitor externo (~/celia-tiktok-chat/leitor.py) manda cada presente JA FECHADO (fim do combo),
com um evento_id; o mesmo evento_id nunca soma duas vezes (reconexao do leitor, reenvio).
O placar mora em var/celia_enquete.json (rename atomico) e sobrevive a reinicios da central.
Ligar/desligar a exibicao NAO mora aqui: e a opcao `enquete_mobile` do Corpo (celia_corpo_opcoes.json).
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from pathlib import Path

LADOS = ("lula", "flavio")
_lock = threading.Lock()
_vistos: deque[str] = deque(maxlen=4000)
_vistos_set: set[str] = set()


def _arquivo(var: Path) -> Path:
    return Path(var) / "celia_enquete.json"


def ler(var: Path) -> dict:
    """Placar persistido. Arquivo ausente ou corrompido = placar zerado (nunca levanta)."""
    saida = {"lula": 0, "flavio": 0, "rodada": 1, "atualizado_em": 0}
    try:
        dados = json.loads(_arquivo(var).read_text(encoding="utf-8"))
        if isinstance(dados, dict):
            for k in saida:
                v = dados.get(k)
                if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                    saida[k] = v
    except (OSError, ValueError):
        pass
    return saida


def _gravar(var: Path, estado: dict) -> None:
    alvo = _arquivo(var)
    alvo.parent.mkdir(parents=True, exist_ok=True)
    tmp = alvo.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(estado), encoding="utf-8")
    os.replace(tmp, alvo)


def _lembrar(evento_id: str) -> None:
    if len(_vistos) == _vistos.maxlen:
        _vistos_set.discard(_vistos[0])
    _vistos.append(evento_id)
    _vistos_set.add(evento_id)


def somar(var: Path, lado: str, quantidade: int, evento_id: str) -> dict:
    """Soma `quantidade` pontos a `lado`. Idempotente por evento_id. Devolve o placar lido de volta."""
    if lado not in LADOS:
        raise ValueError("lado_invalido")
    quantidade = int(quantidade)
    if quantidade < 1:
        raise ValueError("quantidade_invalida")
    with _lock:
        if evento_id in _vistos_set:
            return {**ler(var), "duplicado": True}
        estado = ler(var)
        estado[lado] += quantidade
        estado["atualizado_em"] = int(time.time() * 1000)
        _gravar(var, estado)
        _lembrar(evento_id)
        return {**ler(var), "duplicado": False}


def zerar(var: Path) -> dict:
    """Comeca uma rodada nova: placar 0 x 0. Os evento_id vistos sao esquecidos junto."""
    with _lock:
        estado = ler(var)
        novo = {"lula": 0, "flavio": 0, "rodada": estado["rodada"] + 1, "atualizado_em": int(time.time() * 1000)}
        _gravar(var, novo)
        _vistos.clear()
        _vistos_set.clear()
        return ler(var)
