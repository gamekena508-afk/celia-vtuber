"""Botão "TOCAR" (play_expected) tem de tocar a fala da Célia mesmo quando o criador avançou na mão.

Defeito medido em 2026-10-18 na sessão do roteiro XTTS (`control.recovery action=play_expected ok=false`, 12:49:00):
`planned_celia_reply()` só devolve a fala quando o ASR casou o texto do criador com o bloco (`criador_match.matched`).
Avanço manual (`forward_line`) pinta as linhas como concluídas, inclusive a da própria Célia, mas não casa nada;
logo o botão respondia "RECUPERAÇÃO INDISPONÍVEL". Não era o TTS: o V9-D sintetiza o texto (provado fora do runtime).
Gatilho automático continua exigindo o casamento; só o clique explícito do dono ignora esse portão.
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

from celia.core.roteiro_live import RoteiroLiveRuntime

ROTEIRO = Path(__file__).resolve().parents[1] / "roteiros" / "roteiro_youtube_xtts_windows.json"


def _runtime():
    runtime = RoteiroLiveRuntime()
    runtime._write_session_log = lambda *a, **k: None  # não escreve em var/roteiro_sessions real
    return runtime


async def _ate_o_fim_do_bloco_02(runtime):
    await runtime.activate("gravacao", script=json.loads(ROTEIRO.read_text(encoding="utf-8")))
    while runtime.current_beat_id != "02_o_que_e_o_xtts":
        await runtime.control({"action": "forward_line"})
    for _ in range(4):  # AÇÃO, Criador, Criador, Célia — como o dono tocou na tela
        await runtime.control({"action": "forward_line"})


def test_avanco_manual_marca_a_fala_da_celia_mas_nao_casa_o_asr():
    runtime = _runtime()
    asyncio.run(_ate_o_fim_do_bloco_02(runtime))
    assert runtime.current_beat_id == "02_o_que_e_o_xtts"
    assert "02_o_que_e_o_xtts:3" in runtime.completed_lines
    assert runtime.criador_match.get("matched") is False
    # o portão automático continua fechado (não fala sozinha sem o criador ter dito o bloco)
    assert runtime.planned_celia_reply() is None


def test_play_expected_explicito_toca_a_fala_da_celia_do_bloco():
    runtime = _runtime()
    asyncio.run(_ate_o_fim_do_bloco_02(runtime))
    runtime._synthesize_recovery_payload = AsyncMock(return_value={"audio": "x", "display_text": {}})
    runtime._broadcast_recovery_payloads = AsyncMock(return_value=True)

    resposta = asyncio.run(runtime.control({"action": "play_expected"}))

    assert runtime.last_event["ok"] is True, resposta.get("status")
    texto = runtime._synthesize_recovery_payload.await_args.args[0]
    assert texto.startswith("E lembra: voz parecida não significa")
    runtime._broadcast_recovery_payloads.assert_awaited_once()


def test_automatico_sem_clique_nao_fala_antes_da_hora():
    runtime = _runtime()
    asyncio.run(runtime.activate("gravacao", script=json.loads(ROTEIRO.read_text(encoding="utf-8"))))
    runtime._synthesize_recovery_payload = AsyncMock(return_value={"audio": "x"})
    runtime._broadcast_recovery_payloads = AsyncMock(return_value=True)
    # chamada interna, sem o clique explícito: continua recusando
    assert asyncio.run(runtime._play_expected_celia(regenerate=False)) is False
    runtime._synthesize_recovery_payload.assert_not_awaited()


def test_sem_fala_da_celia_no_bloco_diz_o_motivo():
    runtime = _runtime()
    asyncio.run(runtime.activate("gravacao", script=json.loads(ROTEIRO.read_text(encoding="utf-8"))))
    # bloco 01 tem fala da Célia (índice 4?) -> usa um bloco sem Célia: o 04
    while runtime.current_beat_id != "04_mostrar_a_interface_de_voz":
        asyncio.run(runtime.control({"action": "forward_line"}))
    runtime._synthesize_recovery_payload = AsyncMock(return_value={"audio": "x"})
    asyncio.run(runtime.control({"action": "play_expected"}))
    assert runtime.last_event["ok"] is False
    assert runtime.last_event["reason"] == "no_celia_line_in_beat"
