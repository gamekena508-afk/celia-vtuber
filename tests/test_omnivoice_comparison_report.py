import json

from celia.tts.omnivoice_comparison_report import render_comparison_html, write_comparison_html


def _report(tmp_path):
    wav = tmp_path / "C_ram_same_1.wav"
    wav.write_bytes(b"RIFF")
    return {
        "model_device": "cuda:0",
        "audio_tokenizer_device": "cuda:0",
        "memory_after_model_load": {"gpu": {"nvidia_smi": "RTX Test, 10000, 2000"}},
        "reference": {"sha256": "a" * 64},
        "runs": [{
            "mode": "C_ram_prompt",
            "text": "<Célia & teste>",
            "events": [
                {"stage": "generate_total", "elapsed_ms": 1234.0},
                {"stage": "reference_prompt_total", "elapsed_ms": 0.0},
                {"stage": "reference_asr_transcribe", "elapsed_ms": 0.0},
                {"stage": "reference_audio_codec_encode", "elapsed_ms": 0.0},
            ],
            "wav": {"path": str(wav), "seconds": 1.25, "sample_rate": 24000},
        }],
    }


def test_renderer_reports_measured_facts_and_escapes_model_text(tmp_path):
    report = _report(tmp_path)

    rendered = render_comparison_html(report, tmp_path / "comparacao.html")

    assert "cuda:0" in rendered
    assert "1.23 s" in rendered
    assert "C_ram_prompt" in rendered
    assert "&lt;Célia &amp; teste&gt;" in rendered
    assert '<audio controls' in rendered
    assert "Qualidade escutada" in rendered


def test_writer_creates_comparison_beside_report(tmp_path):
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(_report(tmp_path)), encoding="utf-8")

    output = write_comparison_html(report_path)

    assert output == tmp_path / "comparacao.html"
    assert output.is_file()
    assert "OmniVoice" in output.read_text(encoding="utf-8")
