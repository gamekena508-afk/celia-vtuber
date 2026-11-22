from celia.core.observability import _safe_metadata


def test_safe_metadata_preserves_latency_scalars_and_drops_payload_content():
    safe = _safe_metadata({"ttft_ms": 123, "chars": 45})
    assert safe == {"ttft_ms": 123, "chars": 45}

    redacted = _safe_metadata({"texto": "conteudo secreto"})
    assert redacted == {}
