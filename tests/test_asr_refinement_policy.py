from celia.core.orchestrator import (
    _safe_local_correction,
    detect_celia_vocative,
)
from celia.conversations.conversation_utils import create_batch_input


def test_probable_celia_vocative_does_not_rewrite_normal_seria():
    assert detect_celia_vocative("Célia, olha isso.")
    assert detect_celia_vocative("Séria, olha isso.")
    assert detect_celia_vocative("Ei, séria, fala alguma coisa.")
    assert detect_celia_vocative("Né, séria?")
    assert detect_celia_vocative("séria, séria.")

    assert not detect_celia_vocative("Essa situação é séria.")
    assert not detect_celia_vocative("Eu estava falando séria.")
    assert not detect_celia_vocative("Pegava a palavra séria.")
    assert not detect_celia_vocative("Ela parece séria hoje.")


def test_refiner_only_accepts_local_corrections():
    assert _safe_local_correction(
        "Mas ainda tem muito o que marar.",
        "Mas ainda tem muito o que melhorar.",
    )
    assert not _safe_local_correction(
        "No ISP Na série, tá indo pro blog dela agora",
        "O Whisper da Célia está enviando a transcrição para os logs agora.",
    )


def test_uncertain_asr_is_internal_brain_context_not_user_rewrite():
    raw = "No ISP Na série, tá indo pro blog dela agora"
    batch = create_batch_input(
        input_text=raw,
        images=None,
        from_name="Criador",
        metadata={
            "asr_refinement": {
                "status": "UNCERTAIN",
                "text": raw,
                "reason": "ambiguous",
            }
        },
    )
    content = batch.texts[0].content
    assert "[NOTA INTERNA DE ASR:" in content
    assert raw in content

    clean = create_batch_input(
        input_text="Tá saindo às vezes errado a transcrição.",
        images=None,
        from_name="Criador",
        metadata={"asr_refinement": {"status": "KEEP"}},
    )
    assert "[NOTA INTERNA DE ASR:" not in clean.texts[0].content


def test_local_correction_cannot_flip_negation_or_numbers():
    assert not _safe_local_correction(
        "Eu não gostei disso.",
        "Eu gostei disso.",
    )
    assert not _safe_local_correction(
        "Foram 15 segundos.",
        "Foram 50 segundos.",
    )


def _run_refiner(monkeypatch, payload=None, *, exc=None, timeout="0.05"):
    import asyncio
    import json
    import urllib.request
    from celia.core.orchestrator import maybe_refine_transcript

    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("CELIA_GEMMA_TIMEOUT", timeout)

    if exc is not None:
        def fail(*args, **kwargs):
            raise exc
        monkeypatch.setattr(urllib.request, "urlopen", fail)
    else:
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self):
                body = {"choices": [{"message": {"content": json.dumps(payload)}}]}
                return json.dumps(body).encode("utf-8")
        monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: Response())

    return asyncio.run(
        maybe_refine_transcript(
            "Mas ainda tem muito o que marar.",
            context_summary="Criador estava falando de transcrição.",
        )
    )


def test_refiner_keep_never_rewrites_user_text(monkeypatch):
    result = _run_refiner(monkeypatch, {
        "status": "KEEP",
        "text": "Texto inventado que deve ser ignorado.",
        "reason": "plausible",
    })
    assert result.status == "KEEP"
    assert result.text == "Mas ainda tem muito o que marar."
    assert result.transport_status == "ok"


def test_refiner_correct_accepts_only_local_change(monkeypatch):
    result = _run_refiner(monkeypatch, {
        "status": "CORRECT",
        "text": "Mas ainda tem muito o que melhorar.",
        "reason": "local acoustic correction",
    })
    assert result.status == "CORRECT"
    assert result.text == "Mas ainda tem muito o que melhorar."


def test_refiner_uncertain_preserves_raw_whisper(monkeypatch):
    result = _run_refiner(monkeypatch, {
        "status": "UNCERTAIN",
        "text": "Uma frase bonita inventada.",
        "reason": "ambiguous",
    })
    assert result.status == "UNCERTAIN"
    assert result.text == "Mas ainda tem muito o que marar."


def test_refiner_transport_error_fails_open_to_whisper(monkeypatch):
    result = _run_refiner(monkeypatch, exc=OSError("offline"))
    assert result.status == "KEEP"
    assert result.text == "Mas ainda tem muito o que marar."
    assert result.transport_status == "error"
    assert result.reason == "OSError"


def test_refiner_timeout_fails_open_to_whisper(monkeypatch):
    import time
    import urllib.request

    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("CELIA_GEMMA_TIMEOUT", "0.01")

    class SlowResponse:
        def __enter__(self):
            time.sleep(0.25)
            return self
        def __exit__(self, *args): return False
        def read(self): return b"{}"

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: SlowResponse())

    import asyncio
    from celia.core.orchestrator import maybe_refine_transcript

    result = asyncio.run(maybe_refine_transcript("Texto original."))
    assert result.status == "KEEP"
    assert result.text == "Texto original."
    assert result.transport_status == "timeout"
