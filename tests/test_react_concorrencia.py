"""Perda de atualização entre o worker e o WebSocket de playback (medida em 14/11/2026, Opus).

Antes: o WS lia a sessão ao conectar e regravava essa cópia velha a cada evento de playback;
o READY que o worker tinha gravado no meio voltava a PROCESSING (no player: trecho cinza para sempre).
"""
import asyncio
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from celia.react.store import ReactStore  # noqa: E402
from celia.react.worker import SegmentWorker, plan_segments  # noqa: E402


def _sessao(store, sid="s-corrida01", vid="v-corrida01"):
    s = {"contract": "1.0.0", "mode": "duo",
         "video": {"video_id": vid, "source_url": "https://youtu.be/x", "title": "t", "channel": None,
                   "thumbnail_url": None, "duration_ms": 300000, "media_url": "/m", "revision": 1},
         "segments": plan_segments(vid, 1, 300000),
         "playback": {"session_id": sid, "video_id": vid, "revision": 1, "position_ms": 0, "paused": True,
                      "rate": 1.0, "played_ranges": [], "pause_reason": None, "sequence": 0}}
    store.save(s)
    return s


def test_playback_depois_do_ready_nao_apaga_o_ready(tmp_path):
    store = ReactStore(tmp_path)
    w = SegmentWorker(store, analyzer="real")
    sessao_da_conexao = copy.deepcopy(_sessao(store))  # o que o WS tinha em mãos
    seg_id = sessao_da_conexao["segments"][0]["segment_id"]

    async def roteiro():
        w._set_segment("s-corrida01", seg_id, status="PROCESSING")
        w._set_segment("s-corrida01", seg_id, status="READY", checksum="a" * 64)
        # o WS continua recebendo eventos com a cópia antiga em mãos
        await w.playback_event(sessao_da_conexao, "playback.play", {"position_ms": 0})
        await w.playback_event("s-corrida01", "playback.timeupdate", {"position_ms": 250, "rate": 1})

    asyncio.run(roteiro())
    disco = store.get("s-corrida01")
    assert disco["segments"][0]["status"] == "READY"
    assert disco["segments"][0]["checksum"] == "a" * 64
    assert disco["playback"]["played_ranges"] == [{"start_ms": 0, "end_ms": 250}]


def test_emit_nao_regrava_copia_velha(tmp_path):
    store = ReactStore(tmp_path)
    w = SegmentWorker(store, analyzer="real")
    velha = copy.deepcopy(_sessao(store))
    seg_id = velha["segments"][1]["segment_id"]
    w._set_segment("s-corrida01", seg_id, status="READY", checksum="b" * 64)
    asyncio.run(w.emit(velha, "segment.queued", {"segment": velha["segments"][1]}))
    disco = store.get("s-corrida01")
    assert disco["segments"][1]["status"] == "READY"
    assert disco["playback"]["sequence"] == 1 and velha["playback"]["sequence"] == 1


def test_play_em_trecho_cinza_nao_vira_assistido(tmp_path):
    store = ReactStore(tmp_path)
    w = SegmentWorker(store, analyzer="real")
    s = _sessao(store)
    w._set_segment("s-corrida01", s["segments"][0]["segment_id"], status="READY")  # S1 0–150 s

    async def roteiro():
        await w.playback_event("s-corrida01", "playback.seek", {"position_ms": 149500})
        await w.playback_event("s-corrida01", "playback.play", {"position_ms": 149500})
        await w.playback_event("s-corrida01", "playback.timeupdate", {"position_ms": 150400, "rate": 1})

    asyncio.run(roteiro())
    assert store.get("s-corrida01")["playback"]["played_ranges"] == [{"start_ms": 149500, "end_ms": 150000}]


def test_reabrir_reenvia_so_o_que_ja_foi_liberado(tmp_path):
    import json
    store = ReactStore(tmp_path)
    w = SegmentWorker(store, analyzer="real")
    s = _sessao(store)
    seg = s["segments"][0]
    w._set_segment("s-corrida01", seg["segment_id"], status="READY")
    pasta = store.analysis / "v-corrida01" / "1" / "s-corrida01"
    pasta.mkdir(parents=True)
    evs = [{"event_id": f"e{i}", "segment_id": seg["segment_id"], "kind": "speech", "start_ms": i * 1000,
            "end_ms": i * 1000 + 500, "speaker": None, "text": f"fala {i}", "confidence": 0.9, "basis": "heard"} for i in range(5)]
    (pasta / f"{seg['segment_id']}-events.json").write_text(json.dumps(evs), encoding="utf-8")
    assert w.released_snapshot("s-corrida01") is None
    store.save_state("s-corrida01", {**store.get_state("s-corrida01"), "released_event_ids": ["e1", "e0"]})
    snap = w.released_snapshot("s-corrida01")
    assert [e["event_id"] for e in snap["events"]] == ["e0", "e1"]


def test_start_limpa_assistido_fora_de_ready(tmp_path):
    store = ReactStore(tmp_path)
    s = _sessao(store)
    s["segments"][0]["status"] = "READY"
    s["playback"]["played_ranges"] = [{"start_ms": 0, "end_ms": 200000}]
    store.save(s)
    w = SegmentWorker(store, analyzer="real")

    async def roteiro():
        await w.start(); await w.stop()

    asyncio.run(roteiro())
    assert store.get("s-corrida01")["playback"]["played_ranges"] == [{"start_ms": 0, "end_ms": s["segments"][0]["end_ms"]}]


def test_celia_recebe_lotes_acumulados_e_resposta_vai_para_o_historico(tmp_path, monkeypatch):
    store = ReactStore(tmp_path)
    w = SegmentWorker(store, analyzer="real")
    w.adapter_interval_s = 0.2
    _sessao(store)
    enviados = []

    async def falso(session, events, endpoint=None):
        enviados.append([e["event_id"] for e in events])
        return "resposta da Célia"

    monkeypatch.setattr(w, "_send_to_celia", falso)
    ev = lambda i: {"event_id": f"e{i}", "start_ms": i, "end_ms": i + 1}

    async def roteiro():
        for i in range(6):  # 6 liberações seguidas, como no play
            w._queue_for_celia("s-corrida01", [ev(i)])
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.6)

    asyncio.run(roteiro())
    # 1º lote sai na hora; os 5 seguintes esperam a janela e vão juntos (nunca 6 turnos da Célia)
    assert enviados == [["e0"], [f"e{i}" for i in range(1, 6)]]
    mem = store.get_state("s-corrida01")["react_session_memory"]
    assert mem[-1]["response"] == "resposta da Célia" and mem[-1]["voice"] is False


def test_start_nao_enfileira_sessao_provisoria(tmp_path):
    store = ReactStore(tmp_path)
    s = _sessao(store, "s-prov0001", "v-prov0001")
    s["video"]["duration_ms"] = 1
    s["segments"] = plan_segments("v-prov0001", 1, 1)
    store.save(s)
    w = SegmentWorker(store, analyzer="real")

    async def roteiro():
        await w.start()
        n = w.queue.qsize()
        await w.stop()
        return n

    assert asyncio.run(roteiro()) == 0
