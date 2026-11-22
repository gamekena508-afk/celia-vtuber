"""O chunk 0 entra no engine primeiro — a primeira fala não espera a segunda.

INCIDENTE REAL, turno de 05:30:33 em 2026-10-01:

    05:30:35  texto pronto (61 chars)
    05:30:38  OmniVoice gerou o PRIMEIRO áudio  -> speech d4c71203  (seq 1)
    05:30:40  OmniVoice gerou o SEGUNDO áudio   -> speech b59ae4ec  (seq 0)
    05:30:40  só então os dois AUDIO_SENT, e o AUDIO_STARTED do seq 0

O chunk gerado primeiro tocou por último. O seq 1 venceu a corrida pelo engine
e o seq 0 — o único que pode começar a falar — só terminou 1,5 s depois,
segurando a fila de envio, que é ordenada de propósito.
`first_audio_playback_started` ficou em 7.278 ms para um texto pronto em 2 s.

A corrida existia porque `speak()` cria uma task por chunk, mas o engine de
produção serializa tudo num lock global de módulo
(`src/celia/tts/omnivoice_tts.py:29`). Não havia paralelismo — havia sorteio.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from celia.conversations.tts_manager import TTSTaskManager  # noqa: E402


def _novo_manager() -> TTSTaskManager:
    return TTSTaskManager.__new__(TTSTaskManager)


def _preparar(manager: TTSTaskManager) -> None:
    manager._next_sequence_to_send = 0
    manager._synthesis_turn = {}
    manager._synthesis_turn_lock = asyncio.Lock()


def test_chunk_zero_nunca_espera_ninguem():
    """A primeira fala do turno não pode depender de nada."""
    manager = _novo_manager()

    async def cenario():
        _preparar(manager)
        # Sem timeout: se o seq 0 esperasse, isto penduraria.
        await asyncio.wait_for(manager._wait_for_synthesis_turn(0), timeout=1.0)
        return True

    assert asyncio.run(cenario()) is True


def test_chunk_um_fica_bloqueado_ate_o_zero_liberar():
    """A propriedade que importa: o portão do seq 1 BLOQUEIA de verdade.

    Medir só a ordem de entrada seria um teste fraco — sem portão nenhum a
    ordem de entrada também sairia [0, 1], porque as tasks nascem nessa ordem.
    O que o incidente mostrou é outra coisa: o seq 1 chegava ao engine e
    TERMINAVA antes do seq 0. Então aqui a asserção é sobre bloqueio real.
    """
    manager = _novo_manager()

    async def cenario():
        _preparar(manager)
        chegou = asyncio.Event()

        async def chunk_um():
            await manager._wait_for_synthesis_turn(1)
            chegou.set()

        tarefa = asyncio.create_task(chunk_um())
        # Dá folga de sobra para o seq 1 rodar, se ele puder.
        await asyncio.sleep(0.05)
        bloqueado_antes = not chegou.is_set()

        await manager._release_synthesis_turn(0)
        await asyncio.wait_for(tarefa, timeout=1.0)
        return bloqueado_antes, chegou.is_set()

    bloqueado_antes, passou_depois = asyncio.run(cenario())
    assert bloqueado_antes, "o chunk 1 entrou no engine sem o chunk 0 ter liberado"
    assert passou_depois, "o chunk 1 não andou nem depois da liberação"


def test_sem_o_portao_a_ordem_de_termino_inverte():
    """CONTROLE POSITIVO: sem portão, o chunk rápido termina antes do lento.

    É exatamente o que os logs registraram — e é o que a correção impede.
    Este teste NÃO usa o portão: ele existe para mostrar que o defeito é real
    e que a asserção do teste anterior não é trivialmente verdadeira.
    """
    ordem_de_termino: list[int] = []

    async def cenario():
        async def chunk(seq: int, demora: float):
            await asyncio.sleep(demora)  # "síntese", sem portão
            ordem_de_termino.append(seq)

        await asyncio.gather(chunk(0, 0.05), chunk(1, 0.001))

    asyncio.run(cenario())
    assert ordem_de_termino == [1, 0], (
        "sem portão o chunk rápido deveria terminar primeiro; se não terminou, "
        "este controle parou de reproduzir o defeito"
    )


def test_chunk_um_nao_fica_preso_depois_que_o_zero_libera():
    """O portão não pode virar serialização eterna: 1 anda assim que 0 libera."""
    manager = _novo_manager()

    async def cenario():
        _preparar(manager)
        await manager._release_synthesis_turn(0)  # seq 0 entrou no engine
        inicio = asyncio.get_running_loop().time()
        await asyncio.wait_for(manager._wait_for_synthesis_turn(1), timeout=1.0)
        return asyncio.get_running_loop().time() - inicio

    espera = asyncio.run(cenario())
    assert espera < 0.05, f"o chunk 1 esperou {espera:.3f}s depois de liberado"


def test_falha_do_chunk_anterior_nao_emudece_o_resto_do_turno():
    """CONTROLE NEGATIVO: o envelope libera o portão mesmo quando a task morre.

    `_process_tts` tem vários retornos antecipados. Se um deles deixasse o
    portão fechado, o turno inteiro emudeceria até o timeout.
    """
    manager = _novo_manager()

    async def cenario():
        _preparar(manager)

        async def explode(**_kwargs):
            raise RuntimeError("síntese falhou")

        manager._process_tts = explode
        try:
            await manager._process_tts_releasing_turn(sequence_number=0)
        except RuntimeError:
            pass
        # O seq 1 tem de passar mesmo com o seq 0 tendo explodido.
        await asyncio.wait_for(manager._wait_for_synthesis_turn(1), timeout=1.0)
        return True

    assert asyncio.run(cenario()) is True


def test_portao_tem_teto_e_nao_pendura_o_turno_para_sempre():
    """Se o antecessor some sem liberar, o portão desiste em vez de emudecer."""
    manager = _novo_manager()
    assert manager.SYNTHESIS_TURN_TIMEOUT_S > 0
    assert manager.SYNTHESIS_TURN_TIMEOUT_S <= 60, (
        "teto alto demais vira silêncio longo quando um chunk morre"
    )

    async def cenario():
        _preparar(manager)
        manager.SYNTHESIS_TURN_TIMEOUT_S = 0.05
        inicio = asyncio.get_running_loop().time()
        await manager._wait_for_synthesis_turn(1)  # ninguém vai liberar
        return asyncio.get_running_loop().time() - inicio

    espera = asyncio.run(cenario())
    assert 0.02 < espera < 1.0, f"o portão não desistiu no teto: {espera:.3f}s"


def test_a_entrega_continua_ordenada_por_sequence():
    """A ordem audível nunca pode inverter — o motivo de a fila existir."""
    fonte = (
        Path(__file__).resolve().parents[1]
        / "src" / "celia" / "conversations" / "tts_manager.py"
    ).read_text(encoding="utf-8")
    assert "while self._next_sequence_to_send in buffered_payloads:" in fonte, (
        "a entrega ordenada sumiu; isto permitiria fala fora de ordem"
    )
    assert "self._next_sequence_to_send += 1" in fonte


# ---------------------------------------------------------------------------
# BUG 2 — resposta curta virando duas sínteses.
#
# No incidente, 61 caracteres viraram DOIS speech_ids e duas chamadas ao
# OmniVoice (3 s + 2 s). O engine tem custo fixo alto por chamada, então dois
# pedaços curtos custam quase o dobro de um pedaço só.
# ---------------------------------------------------------------------------

TEXTO_CURTO = "Pai, para de enrolar. Conta logo essa historia ai pra gente."
TEXTO_LONGO = (
    "Essa aqui foi a primeira tentativa que eu fiz no Neural quatro e o resultado "
    "nao me agradou nada. Depois eu trouxe pecas da Meshy e comecei a montar tudo "
    "dentro do Blender com a ajuda dos agentes. No fim deu bem mais trabalho."
)


def _pedacos(texto: str, minimo):
    from celia.utils.sentence_divider import SentenceDivider, SentenceWithTags

    async def correr():
        divisor = SentenceDivider(
            faster_first_response=False, segment_method="pysbd", min_tts_chars=minimo
        )

        async def fonte():
            for caractere in texto:
                yield caractere

        saida = []
        async for item in divisor.process_stream(fonte()):
            if isinstance(item, SentenceWithTags) and item.text.strip():
                saida.append(item.text.strip())
        return saida

    return asyncio.run(correr())


def test_resposta_curta_vira_uma_sintese_so():
    """CONTROLE POSITIVO + NEGATIVO no mesmo caso real."""
    assert len(TEXTO_CURTO) < 70
    sem_agrupar = _pedacos(TEXTO_CURTO, 0)
    agrupado = _pedacos(TEXTO_CURTO, 60)

    assert len(sem_agrupar) == 2, (
        "o controle parou de reproduzir o defeito: a resposta curta já não "
        f"se divide em duas ({sem_agrupar})"
    )
    assert len(agrupado) == 1, f"a resposta curta continua em {len(agrupado)} pedaços"
    # Nada de texto perdido nem duplicado ao juntar.
    assert agrupado[0] == " ".join(sem_agrupar)


def test_resposta_longa_continua_em_streaming():
    """O agrupamento não pode segurar a primeira fala de uma resposta grande."""
    pedacos = _pedacos(TEXTO_LONGO, 60)
    assert len(pedacos) >= 2, "a resposta longa virou um bloco só; streaming morreu"
    assert len(pedacos[0]) >= 60
    assert " ".join(pedacos) == " ".join(_pedacos(TEXTO_LONGO, 0)), (
        "o conteúdo falado mudou ao agrupar"
    )


def test_nada_fica_presa_no_fim_do_stream():
    """Um pedaço curto sem próximo tem de sair mesmo assim — ou vira silêncio."""
    pedacos = _pedacos("Oi.", 60)
    assert pedacos == ["Oi."], f"a fala curta se perdeu: {pedacos}"


def test_zero_desliga_e_restaura_o_comportamento_anterior():
    """Rollback sem editar código."""
    assert _pedacos(TEXTO_CURTO, 0) == _pedacos(TEXTO_CURTO, 0)
    assert len(_pedacos(TEXTO_CURTO, 0)) == 2


def test_variavel_de_ambiente_controla_o_padrao(monkeypatch):
    from celia.utils import sentence_divider as sd

    monkeypatch.setenv("CELIA_MIN_TTS_CHARS", "0")
    assert sd._default_min_tts_chars() == 0
    monkeypatch.setenv("CELIA_MIN_TTS_CHARS", "lixo")
    assert sd._default_min_tts_chars() == sd.DEFAULT_MIN_TTS_CHARS
    monkeypatch.delenv("CELIA_MIN_TTS_CHARS")
    assert sd._default_min_tts_chars() == sd.DEFAULT_MIN_TTS_CHARS
