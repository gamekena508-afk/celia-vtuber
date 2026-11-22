"""Unprompted content validation through Célia's existing ASR gateway."""

import re
import unicodedata
from pathlib import Path

from num2words import num2words
import requests

from .production_voice import sha256_file


def normalize_spoken_text(text: str) -> str:
    text = re.sub(r"\b\d+\b", lambda m: num2words(int(m[0]), lang="pt_BR"), str(text).casefold())
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = " ".join(re.sub(r"[^\w\s]", " ", text).split())
    # Whisper writes the spoken contraction "pra" as "para". Preserve the raw
    # transcript; recognize only this explicit equivalent, never broad similarity.
    text = re.sub(r"\bpra\b", "para", text)
    text = re.sub(r"\bta\b", "esta", text)
    return re.sub(r"\bto\b", "estou", text)


def content_equivalence(expected: str, actual: str) -> str | None:
    """Exact normalized words, or the documented Portuguese cem/sem homophone.

    The numeral 100 and 'cem' are pronounced exactly like 'sem'; the raw ASR
    spelling is retained. 'Sim', missing words and broad semantic similarity
    never qualify. This equivalence cannot validate the old 'Sim' waveform.
    """
    wanted, heard = normalize_spoken_text(expected).split(), normalize_spoken_text(actual).split()
    if wanted and wanted == heard:
        return "normalized_text"
    if (wanted and len(wanted) == len(heard)
            and all(a == b or (a == "cem" and b == "sem") for a,b in zip(wanted,heard))):
        return "portuguese_cem_sem_homophone"
    return None


def _transcribe_content(path: Path, endpoint: str, model: str) -> tuple[str, dict]:
    with path.open("rb") as audio:
        response = requests.post(
            endpoint,
            files={"file": (path.name, audio, "audio/wav")},
            data={"model": model, "language": "pt", "temperature": "0", "response_format": "json"},
            timeout=90,
        )
    response.raise_for_status()
    payload = response.json()
    return str(payload.get("text", "")).strip(), payload


def validate_audio_content(path: Path, expected_text: str) -> dict:
    """Portão de conteúdo com dois ASR (Groq Whisper Turbo + TAGARELA :8011); sem prompt, sem fallback escondido."""
    digest = sha256_file(path)
    primary_model = "groq/whisper-large-v3-turbo"
    local_model = "tagarela-q2-int8"
    primary_text, primary_payload = _transcribe_content(
        path, "http://127.0.0.1:20127/v1/audio/transcriptions", primary_model
    )
    local_text, local_payload = _transcribe_content(
        path, "http://127.0.0.1:8011/v1/audio/transcriptions", local_model
    )
    primary_eq = content_equivalence(expected_text, primary_text)
    local_eq = content_equivalence(expected_text, local_text)
    approved = primary_eq is not None and local_eq is not None
    normalized_agree = normalize_spoken_text(primary_text) == normalize_spoken_text(local_text)
    if approved:
        status = "match_dual_consensus"
    elif normalized_agree:
        status = "review_required_consensus_mismatch"
    else:
        status = "review_required_backend_disagreement"
    return {
        "expected_text": expected_text,
        "asr_text": primary_text,
        "asr_status": status,
        "asr_equivalence": primary_eq if approved else None,
        "asr_audio_sha256": digest,
        "asr_model": primary_model,
        "asr_prompt": None,
        "asr_policy": "ASR-2026-10-04",
        "asr_primary": {"model": primary_model, "text": primary_text, "equivalence": primary_eq, "response": primary_payload},
        "asr_local_judge": {"model": local_model, "text": local_text, "equivalence": local_eq, "response": local_payload},
        "asr_backends_agree": normalized_agree,
    }
