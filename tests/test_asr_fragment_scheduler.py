"""Agrupamento de fragmentos de ASR com tempo (tentativa de 2026-10-21): relógio virtual, intervalos REAIS.

Fixture: 55 fragmentos reais (texto, duração, voz, rms, pico e instante) da tentativa de 14:21-14:25; o instante vem do arquivo do ASR (µs).
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from celia.core.asr_fragment_scheduler import AsrFragmentScheduler  # noqa: E402
from celia.core.asr_turn_gate import CONTINUATION_WINDOW_MS, AsrTurnAssembler, AsrTurnFragment  # noqa: E402

REAL = json.load(open(Path(__file__).parent / "fixtures" / "asr_fragmentos_1421_1425.json", encoding="utf-8"))


class Virtual:
    """Relógio e call_later determinísticos: o tempo só anda quando o teste manda."""

    def __init__(self):
        self.now, self.fila, self.seq = 0.0, [], 0

    def clock(self):
        return self.now

    def call_later(self, delay, cb, *args):
        self.seq += 1
        handle = type("H", (), {"cancelled": False})()
        handle.cancel = lambda: setattr(handle, "cancelled", True)
        self.fila.append((self.now + delay, self.seq, cb, args, handle))
        return handle

    def advance_to(self, t):
        while True:
            vencidos = sorted((x for x in self.fila if x[0] <= t and not x[4].cancelled), key=lambda x: (x[0], x[1]))
            if not vencidos:
                break
            quando, _, cb, args, _h = vencidos[0]
            self.fila.remove(vencidos[0])
            self.now = quando
            cb(*args)
        self.now = max(self.now, t)


class Rig:
    def __init__(self, **kw):
        self.v, self.turnos, self.expirados, self.cancelados, self.descartados = Virtual(), [], [], [], []
        self.asm = AsrTurnAssembler(**kw)
        self.sch = AsrFragmentScheduler(
            self.asm, clock=self.v.clock, call_later=self.v.call_later,
            on_release=lambda r: self.turnos.append((round(self.v.now, 3), r.reason, r.fragment.text, r.fragment.utterance_id)),
            on_expire=lambda r: self.expirados.append((round(self.v.now, 3), r.fragment.text)),
        )

    def fala(self, uid, texto, t, *, voiced=900, rms=0.04, peak=0.4, dur=1400):
        self.v.advance_to(t)
        r = self.asm.push(AsrTurnFragment(uid, texto, texto, dur, voiced, rms, peak, t))
        if r.released_fragment is not None:
            self.turnos.append((round(self.v.now, 3), "released_by_race", r.released_fragment.text, r.released_fragment.utterance_id))
        if r.dropped_fragment is not None:
            self.descartados.append(r.dropped_fragment.text)
        if r.cancelled_by_urgent:
            self.cancelados.append(r.dropped_fragment.text)
        if r.action == "SUBMIT":
            self.turnos.append((round(self.v.now, 3), r.reason, r.fragment.text, r.fragment.utterance_id))
        self.sch.sync()
        return r

    def textos(self):
        return [x[2] for x in self.turnos]


# ----------------------------------------------------------------------------------------------------------------- sintético, relógio virtual ---
def test_unfinished_fragment_is_released_by_time_without_any_other_speech():
    rig = Rig()
    rig.fala("a", "Fala pessoal, estamos de volta pra um vídeo de...", t=10.0, voiced=2000)
    assert rig.turnos == [] and rig.asm.pending is not None
    rig.v.advance_to(10.0 + CONTINUATION_WINDOW_MS / 1000 + 0.01)           # ninguém falou mais nada
    assert [x[1] for x in rig.turnos] == ["released_after_continuation_window"]
    assert rig.turnos[0][0] == pytest.approx(10.0 + CONTINUATION_WINDOW_MS / 1000, abs=0.02), "tem de sair NO prazo, não na próxima fala"
    assert rig.asm.pending is None
    rig.v.advance_to(100.0)
    assert len(rig.turnos) == 1, "o prazo já cumprido não pode disparar de novo"


def test_neighbour_inside_the_window_merges_once_and_cancels_the_timer():
    rig = Rig()
    rig.fala("a", "Tandam dentro", t=0.0, voiced=800)
    r = rig.fala("b", "do Minecraft", t=1.7, voiced=800)                     # intervalo real: 1,7 s
    assert r.reason == "merged_pending_fragment" and r.fragment.text == "Tandam dentro do Minecraft"
    rig.v.advance_to(60.0)
    assert rig.textos() == ["Tandam dentro do Minecraft"], "emitido uma vez só; o prazo antigo não libera o fragmento de novo"


def test_the_real_continuation_gaps_would_have_been_missed_by_the_old_1_4_second_window():
    # intervalos reais entre inacabado e continuação: 1,1; 1,2; 1,7; 2,0; 2,4 s. A janela antiga (1,4 s) pegaria 2 de 5; a medida (3,0 s) pega as 5.
    ok_novo = ok_antigo = 0
    for gap in (1.1, 1.2, 1.7, 2.0, 2.4):
        for janela, nome in ((CONTINUATION_WINDOW_MS, "novo"), (1400, "antigo")):
            rig = Rig(continuation_window_ms=janela)
            rig.fala("a", "Estamos de volta para um vídeo de...", t=0.0, voiced=2000)
            r = rig.fala("b", "A série", t=gap, voiced=800)
            fundiu = r.reason == "merged_pending_fragment"
            ok_novo += fundiu and nome == "novo"
            ok_antigo += fundiu and nome == "antigo"
    assert (ok_novo, ok_antigo) == (5, 2)


def test_neighbour_after_the_window_but_before_the_timer_ran_still_releases_exactly_once():
    rig = Rig()
    rig.fala("a", "Estamos de volta para um vídeo de...", t=0.0, voiced=2000)
    rig.fala("b", "Outra frase completa e longa aqui", t=3.05, voiced=900)  # corrida: chegou ANTES do callback do prazo ser executado
    textos = rig.textos()
    assert textos.count("Estamos de volta para um vídeo de...") == 1 and "Outra frase completa e longa aqui" in textos
    rig.v.advance_to(30.0)
    assert rig.textos().count("Estamos de volta para um vídeo de...") == 1


def test_urgent_stop_passes_immediately_and_cancels_the_pending_fragment_for_good():
    rig = Rig()
    rig.fala("a", "Zera Minecraft, vou fazer a série de...", t=0.0, voiced=2000)
    r = rig.fala("b", "Para tudo agora", t=0.8, voiced=900)                    # 0,8 s depois: dentro da janela, mas NUNCA se funde com parada
    assert r.action == "SUBMIT" and r.reason == "urgent_stop" and r.fragment.text == "Para tudo agora"
    assert r.cancelled_by_urgent and rig.cancelados == ["Zera Minecraft, vou fazer a série de..."]
    rig.v.advance_to(60.0)
    assert rig.textos() == ["Para tudo agora"], "o fragmento cancelado não pode executar nada depois da parada"


@pytest.mark.parametrize("parada", ["Para.", "Pare", "Célia, para", "Fica parada", "Para agora"])
def test_the_stop_command_passes_at_once_even_with_nothing_pending(parada):
    rig = Rig()
    r = rig.fala("s", parada, t=5.0, voiced=400)
    assert r.action == "SUBMIT"


def test_short_noise_expires_silently_and_a_short_real_phrase_is_released():
    rig = Rig()
    rig.fala("n", "Cabe.", t=0.0, voiced=234, rms=0.039, peak=0.53)                       # 1 palavra: ruído
    rig.fala("p", "Tá preparado?", t=20.0, voiced=341, rms=0.062, peak=0.72)              # fala de verdade que ficou 9 ms abaixo de 350 ms
    rig.v.advance_to(60.0)
    assert [x[2] for x in rig.turnos] == ["Tá preparado?"] and [x[1] for x in rig.turnos] == ["released_short_fragment"]
    assert rig.expirados == [] or all(x[1] == "Cabe." for x in rig.expirados)


def test_an_interjection_alone_never_becomes_a_turn():
    rig = Rig()
    for i, f in enumerate(["Hum.", "Ahhh.", "uh", "Hã?", "Ai, ai, ai."]):
        rig.fala(f"i{i}", f, t=i * 10.0, voiced=900)
    rig.v.advance_to(100.0)
    assert rig.turnos == []


def test_chain_of_unfinished_fragments_is_bounded():
    rig = Rig()
    rig.fala("a", "eu vou fazer uma série de", t=0.0, voiced=900)
    rig.fala("b", "minecraft que vai ser com", t=1.5, voiced=900)
    r = rig.fala("c", "a ajuda da Célia e de", t=3.0, voiced=900)
    assert r.action == "SUBMIT" and r.reason == "merged_chain_limit", "a cadeia não pode segurar a fala para sempre"


def test_the_same_utterance_never_becomes_a_turn_twice():
    rig = Rig()
    a = rig.fala("x1", "Célia, pega madeira pra nossa casa", t=0.0, voiced=1200)
    b = rig.fala("x1", "Célia, pega madeira pra nossa casa", t=0.4, voiced=1200)     # reentrega do mesmo id
    assert a.action == "SUBMIT" and b.action == "REJECT" and b.reason == "duplicate_utterance"
    assert rig.textos().count("Célia, pega madeira pra nossa casa") == 1


# ------------------------------------------------------------------------------------------------------------------- replay dos intervalos reais ---
def _replay_real():
    rig = Rig()
    for i, f in enumerate(REAL):
        rig.fala(f"u{i}", f["texto"], f["t"], voiced=f["voiced_ms"], rms=f["rms"], peak=f["peak"], dur=f["duration_ms"])
    rig.v.advance_to(REAL[-1]["t"] + 30.0)
    return rig


def test_real_replay_joins_the_real_continuations_into_single_turns():
    textos = _replay_real().textos()
    assert any("Tandam dentro do Minecraft" in t for t in textos)
    assert any("O objetivo é fazer a série... Zera Minecraft." in t for t in textos)
    for solto in ("Tandam dentro", "do Minecraft", "Zera Minecraft."):
        assert solto not in textos, f"{solto!r} não pode virar turno separado do que ele completa"


def test_real_replay_releases_pending_fragments_by_time_and_recovers_the_lost_question():
    rig = _replay_real()
    liberado = next(x for x in rig.turnos if x[2] == "Tá se fazendo a meu...")
    capturado = next(f["t"] for f in REAL if f["texto"] == "Tá se fazendo a meu...")
    assert liberado[0] == pytest.approx(capturado + CONTINUATION_WINDOW_MS / 1000, abs=0.02), "saiu no prazo, sem depender de outra fala"
    assert any(x[2] == "Tá preparado?" for x in rig.turnos), "a pergunta do Criador ficou 9 ms abaixo do corte e era descartada"


def test_real_replay_never_emits_an_interjection_and_never_repeats_a_fragment():
    rig = _replay_real()
    for texto in rig.textos():
        assert texto.strip().lower().strip(".?!… ") not in {"hum", "ahhh", "uh", "hã", "ai, ai, ai", "né", "ups"}
    ids = [x[3] for x in rig.turnos]
    assert len(ids) == len(set(ids)), "um id de fala nunca vira dois turnos"
