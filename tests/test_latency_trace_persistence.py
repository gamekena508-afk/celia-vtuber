import json
import time

from celia.core.latencia import Cronometro
from celia.core.latency_trace import LatencyTraceRecorder


def test_cronometro_keeps_turn_ids_for_max_and_p95():
    clock = Cronometro(janela=16)
    samples = [("turn-a", 100.0), ("turn-b", 200.0), ("turn-c", 900.0), ("turn-d", 400.0)]
    for turn_id, value in samples:
        clock.marcar(turn_id, "stage", value)

    summary = clock.snapshot()["stage"]
    assert summary["max"] == 900.0
    assert summary["max_turn_id"] == "turn-c"
    assert summary["p95_turn_id"] in {"turn-c", "turn-d"}
    assert clock.estagios_do_turno("turn-b")["stage"] == 200.0


def test_latency_trace_persists_events_and_slow_dump(tmp_path, monkeypatch):
    monkeypatch.setenv("CELIA_LATENCY_TRACE_ENABLED", "1")
    monkeypatch.setenv("CELIA_LATENCY_TRACE_DIR", str(tmp_path))
    monkeypatch.setenv("CELIA_LATENCY_SLOW_TURN_MS", "1")

    recorder = LatencyTraceRecorder(max_turns=16)
    recorder.mark("turn-1", "fim_fala", source="test")
    # Simulate an old speech-end timestamp without sleeping the test process.
    recorder._turns["turn-1"]["events"][0]["monotonic_ns"] -= 2_000_000_000
    recorder.mark("turn-1", "asr_start", provider="fake", model="fake")
    background_call = recorder.llm_begin(
        role="cognitive_loop", model="fake-brain", endpoint="http://gateway.local"
    )
    recorder.llm_end(background_call)
    recorder.mark("turn-1", "audio_sent", speech_id="turn-1:s0", phrase_index=0)
    assert recorder.flush()

    files = list(tmp_path.glob("celia-latency-*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert any(row.get("event") == "fim_fala" for row in rows)
    assert any(row.get("event") == "audio_sent" for row in rows)
    slow = next(row for row in rows if row.get("type") == "slow_turn_dump")
    assert any(call.get("role") == "cognitive_loop" for call in slow["llm_calls_overlapping_turn"])

    turn = recorder.snapshot_turn("turn-1")
    assert [event["event"] for event in turn["events"]][:2] == ["fim_fala", "asr_start"]


def test_latency_trace_flag_disables_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv("CELIA_LATENCY_TRACE_ENABLED", "0")
    monkeypatch.setenv("CELIA_LATENCY_TRACE_DIR", str(tmp_path))

    recorder = LatencyTraceRecorder()
    recorder.mark("turn-off", "fim_fala")
    assert list(tmp_path.iterdir()) == []


def test_trace_disk_io_is_off_critical_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CELIA_LATENCY_TRACE_ENABLED", "1")
    monkeypatch.setenv("CELIA_LATENCY_TRACE_DIR", str(tmp_path))

    recorder = LatencyTraceRecorder()
    original = recorder._write_record_sync

    def slow_write(record):
        time.sleep(0.1)
        return original(record)

    monkeypatch.setattr(recorder, "_write_record_sync", slow_write)

    started = time.perf_counter()
    recorder.mark("turn-async-io", "fim_fala")
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < 50
    assert recorder.flush(timeout_sec=2.0)
    stats = recorder.overhead_snapshot()
    assert stats["enqueue_count"] >= 1
    assert stats["write_count"] >= 1


def test_gpu_sampling_is_off_critical_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CELIA_LATENCY_TRACE_ENABLED", "1")
    monkeypatch.setenv("CELIA_LATENCY_TRACE_DIR", str(tmp_path))

    recorder = LatencyTraceRecorder()

    def slow_gpu():
        time.sleep(0.1)
        return {"gpu_measurement": "fake", "gpu_util_percent": 12}

    monkeypatch.setattr(recorder, "_gpu_snapshot_sync", slow_gpu)

    started = time.perf_counter()
    recorder.request_gpu_snapshot("turn-gpu", "tts_start", phrase_index=0)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < 50
    assert recorder.flush(timeout_sec=2.0)
    rows = []
    for path in tmp_path.glob("celia-latency-*.jsonl"):
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    gpu = next(row for row in rows if row.get("event") == "gpu_snapshot")
    assert gpu["gpu_measurement"] == "fake"
    assert gpu["stage"] == "tts_start"
