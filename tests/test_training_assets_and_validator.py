from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

from celia.core.training_trace import TrainingTraceRecorder


def _write_wav(path: Path, frames: int = 1600, sample_rate: int = 16000) -> None:
    samples = (np.sin(np.linspace(0, 20, frames)) * 0.2 * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())


def test_tts_asset_is_copied_and_survives_source_deletion(tmp_path, monkeypatch):
    monkeypatch.setenv("CELIA_TRAINING_ASSET_DIR", str(tmp_path / "assets"))
    recorder = TrainingTraceRecorder(base_dir=tmp_path / "traces")
    source = tmp_path / "cache.wav"
    _write_wav(source, sample_rate=16000)
    archived = recorder.archive_tts_output(
        source, text="Teste de voz.", speech_id="speech-1", turn_id="turn-1", emotion="happy"
    )
    assert archived is not None
    target = Path(archived["asset_path"])
    assert target.is_file()
    source.unlink()
    assert target.is_file()
    ficha = json.loads(target.with_suffix(".dataset.json").read_text())
    assert ficha["speech_id"] == "speech-1"
    assert ficha["turn_id"] == "turn-1"
    assert len(ficha["asset_sha256"]) == 64


def test_asr_input_keeps_wav_transcript_and_engine_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("CELIA_TRAINING_ASSET_DIR", str(tmp_path / "assets"))
    recorder = TrainingTraceRecorder(base_dir=tmp_path / "traces")

    class DummyASR:
        model = "whisper-local-test"
        state = "local_only"

    samples = np.linspace(-0.2, 0.2, 3200, dtype=np.float32)
    archived = recorder.archive_asr_input(
        samples,
        transcript="Criador testando o microfone.",
        engine=DummyASR(),
        turn_id="turn-asr-1",
        sample_rate=16000,
        channels=1,
    )
    assert archived is not None
    wav_path = Path(archived["asset_path"])
    assert wav_path.is_file()
    ficha = json.loads(wav_path.with_suffix(".dataset.json").read_text())
    assert ficha["transcript"] == "Criador testando o microfone."
    assert ficha["asr_model"] == "whisper-local-test"
    assert ficha["duration_seconds"] == 0.2
    assert len(ficha["asset_sha256"]) == 64


def _run_validator(source: Path, out: Path, *extra_args: str) -> dict:
    script = Path(__file__).resolve().parents[1] / "tools" / "training" / "validate_dataset.py"
    result = subprocess.run(
        [sys.executable, str(script), str(source), "--kind", "persona", "--out", str(out), *extra_args],
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_validator_control_negative_clean_example_passes(tmp_path):
    source = tmp_path / "clean.jsonl"
    row = {
        "messages": [
            {"role": "system", "content": "Você é Célia."},
            {"role": "user", "content": "Quem te criou?"},
            {"role": "assistant", "content": "[teasing] Meu pai/criador. Não complica o óbvio."},
        ]
    }
    source.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    report = _run_validator(source, tmp_path / "out-clean")
    assert report["validated"] == 1
    assert report["quarantine"] == 0
    assert report["rejected"] == 0
    assert report["training_gate_pass"] is True


def test_validator_control_positive_catches_nested_messages_and_identity_inversion(tmp_path):
    source = tmp_path / "bad.jsonl"
    row = {
        "messages": [
            {"role": "system", "content": "Você é Célia."},
            {"role": "user", "content": "Quem é o criador?", "messages": []},
            {"role": "assistant", "content": "Eu criei o criador. Ele é minha criação."},
        ]
    }
    source.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    report = _run_validator(source, tmp_path / "out-bad")
    assert report["validated"] == 0
    assert report["rejected"] == 1
    issues = report["issue_counts"]
    assert issues["message_1_nested_messages"] == 1
    assert issues["identity_inversion_criador_celia"] == 1
    assert report["training_gate_pass"] is False


def test_validator_bounded_system_policy_is_opt_in(tmp_path):
    source = tmp_path / "bounded.jsonl"
    row = {"messages": [
        {"role": "system", "content": "Você é Célia. INTERLOCUTOR ATUAL: Criador. Criador é seu pai/criador. Responda em PT-BR curto."},
        {"role": "user", "content": "Qual teu nome?"},
        {"role": "assistant", "content": "[neutral] Célia."},
    ]}
    source.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    strict = _run_validator(source, tmp_path / "strict")
    assert strict["rejected"] == 1
    assert strict["issue_counts"]["system_prompt_not_minimal_celia"] == 1
    bounded = _run_validator(source, tmp_path / "bounded-out", "--system-policy", "bounded")
    assert bounded["validated"] == 1
    assert bounded["training_gate_pass"] is True
