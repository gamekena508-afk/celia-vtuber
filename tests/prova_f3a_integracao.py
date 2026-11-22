"""Prova do acoplamento F3-A ao caminho operacional, sem playback real."""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))
FALHAS: list[str] = []


def checa(nome: str, cond: bool, detalhe: str = "") -> None:
    if cond:
        print(f"  OK    {nome}")
    else:
        print(f"  FALHA {nome}  {detalhe}")
        FALHAS.append(f"{nome} {detalhe}")


class FakeAgent:
    async def chat(self, _batch):
        from celia.agent.output_types import Actions, DisplayText, SentenceOutput

        yield SentenceOutput(
            display_text=DisplayText("Resposta LLM."),
            tts_text="Resposta LLM.",
            actions=Actions(),
        )


class FakeTTS:
    pass


class BrokenMemory:
    def buscar(self, **_kwargs):
        raise OSError("memoria indisponivel")

    def registrar_turno(self, **_kwargs):
        raise OSError("memoria indisponivel")


def contexto_fake() -> SimpleNamespace:
    return SimpleNamespace(
        system_config=SimpleNamespace(audio_output_device=None),
        character_config=SimpleNamespace(
            conf_uid="celia_001",
            character_name="Celia",
            human_name="Criador",
            avatar="",
            agent_config=SimpleNamespace(llm_provider="fake_llm"),
        ),
        asr_engine=None,
        tts_engine=FakeTTS(),
        live2d_model=None,
        translate_engine=None,
        history_uid="",
        agent_engine=FakeAgent(),
    )


async def turno_real(contexto, turn_id: str) -> str:
    from celia.conversations import single_conversation

    enviados: list[str] = []

    async def send(payload: str) -> None:
        enviados.append(payload)

    metadata = {
        "turn_id": turn_id,
        "origin": "microphone",
        "speaker_name": "Criador",
    }
    # The proof reaches the real LLM->conversation boundary but never starts
    # synthesis or a playback device.
    async def no_tts(self, **_kwargs) -> None:
        return None

    with patch.object(single_conversation.TTSTaskManager, "speak", no_tts):
        response = await single_conversation.process_single_conversation(
            context=contexto,
            websocket_send=send,
            client_uid="proof",
            user_input="entrada real",
            metadata=metadata,
        )
    checa("o caminho operacional manteve a resposta LLM", response == "Resposta LLM.")
    return response


def main() -> int:
    print("REGUA F3-A INTEGRACAO - memoria observacional no turno real\n")
    from celia.celia_singleton import CeliaSingleton
    from celia.core.memoria import Memoria

    tmp = Path(tempfile.mkdtemp(prefix="f3a_integracao_"))
    singleton = CeliaSingleton.get_instance()
    singleton.memoria = Memoria(tmp / "memoria")
    singleton.roteador = None
    contexto = contexto_fake()

    print("[a] um turno real registra celia.interacao.log.v1")
    asyncio.run(turno_real(contexto, "real-001"))
    logs = singleton.memoria.logs_do_turno("real-001")
    checa("existe um log para o turno real", len(logs) == 1, f"veio {len(logs)}")
    if logs:
        checa("o log usa o schema correto", logs[0].get("schema") == "celia.interacao.log.v1")
        checa("o log preserva input e resposta", logs[0].get("input") == "entrada real" and logs[0].get("resposta") == "Resposta LLM.")

    print("\n[b] falha da memoria nao derruba a conversa e deixa erro observavel")
    handler_records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            handler_records.append(record.getMessage())

    memory_logger = logging.getLogger("celia.core.memoria")
    handler = Capture()
    memory_logger.addHandler(handler)
    old_memory = singleton.memoria
    singleton.memoria = BrokenMemory()
    singleton.roteador = None
    try:
        response = asyncio.run(turno_real(contexto, "broken-001"))
    finally:
        memory_logger.removeHandler(handler)
        singleton.memoria = old_memory
        singleton.roteador = None
    checa("a conversa continua com memoria quebrada", response == "Resposta LLM.")
    checa("a falha de persistencia foi registrada", any("falha ao persistir memoria" in item for item in handler_records))

    print("\n[c] turnos concorrentes mantem seus estados separados")
    async def registrar_concorrentes() -> None:
        await asyncio.gather(
            asyncio.to_thread(singleton.registrar_turno_operacional, turn_id="conc-a", entrada="entrada A", resposta="resposta A"),
            asyncio.to_thread(singleton.registrar_turno_operacional, turn_id="conc-b", entrada="entrada B", resposta="resposta B"),
        )

    asyncio.run(registrar_concorrentes())
    a = singleton.memoria.logs_do_turno("conc-a")
    b = singleton.memoria.logs_do_turno("conc-b")
    checa("turno concorrente A nao mistura dados", len(a) == 1 and a[0].get("input") == "entrada A" and a[0].get("resposta") == "resposta A")
    checa("turno concorrente B nao mistura dados", len(b) == 1 and b[0].get("input") == "entrada B" and b[0].get("resposta") == "resposta B")

    print()
    if FALHAS:
        print(f"RESULTADO: REPROVADO - {len(FALHAS)} falha(s)")
        return 1
    print("RESULTADO: APROVADO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
