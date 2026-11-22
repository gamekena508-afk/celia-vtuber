"""Official Célia voice reference used for speech synthesis with OmniVoice."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import MappingProxyType

VOICE_PROFILE_ID = "CELIA_VOICE_OFICIAL_V2"
# Recursos externos (modelo OmniVoice V2 e audio de referencia). Padrao: a arvore
# ~/Documents/IA/Gerador-de-Audio; sobrescreva com as variaveis abaixo (config/caminhos.example.env).
_IA_AUDIO = Path.home() / "Documents" / "Projetos" / "central-ia" / "motores"
# Padrão "official-v1" (k2-fsa/OmniVoice em omnivoicev2/models): é o modelo de TODAS as falas aprovadas pelo
# dono (seis prévias Binance de 12/11 e self-intro V4 de 13/11). O brpt-v15 entrou por engano como padrão em
# eda5fe1d (12/11) sem aprovação de escuta; continua disponível só por CELIA_OMNIVOICE_MODEL_PROFILE=brpt-v15.
MODEL_PROFILE = os.environ.get("CELIA_OMNIVOICE_MODEL_PROFILE", "official-v1").strip().lower()
if MODEL_PROFILE == "brpt-v15":
    MODEL_VARIANT_ID = "edwixx/omnivoice-brpt-v15@79d5c97d0666ff9ad2fc35e87299e47fd4f2e743"
    DEFAULT_MODEL_PATH = str(_IA_AUDIO / "omnivoicev2" / "models-brpt-v15")
    MODEL_SHA256 = "aadecf72a52837bc61d1d35fbb7d4e0d4a1d9d6b577983e69301112bbf8aa4aa"
    MODEL_INSTRUCT = "portuguese accent"
elif MODEL_PROFILE == "official-v1":
    MODEL_VARIANT_ID = "k2-fsa/OmniVoice"
    DEFAULT_MODEL_PATH = str(_IA_AUDIO / "omnivoicev2" / "models")
    MODEL_SHA256 = "730839316de585f4c8298ec0e1712efc10fb19c6fa4e36eb741cb8d51ebcf6aa"
    MODEL_INSTRUCT = None
else:
    raise ValueError(f"unknown_omnivoice_model_profile:{MODEL_PROFILE}")
MODEL_PATH = str(Path(os.environ.get("CELIA_OMNIVOICE_MODEL_DIR") or DEFAULT_MODEL_PATH).resolve())
REFERENCE_PATH = os.environ.get("CELIA_VOICE_REFERENCE_WAV") or str(
    _IA_AUDIO / "coqui-tts-server" / "reference_voices" / "celia-oficial-v2.wav")
REFERENCE_PATH = str(Path(REFERENCE_PATH).resolve())
REFERENCE_SHA256 = "578ff1ec7a7f26c60be866053e8c01d1e023d7e29b44a2c0dfbd5284b6a6b6ba"
REF_TEXT = "Eu percebi uma coisa curiosa enquanto estava observando aquilo. Todo mundo parecia preocupado com o mesmo problema, mas ninguém estava prestando atenção no que causou tudo isso. Às vezes a resposta está justamente no começo."
GENERATION_OPTIONS = MappingProxyType({
    "speed": 0.88, "num_step": 10, "guidance_scale": 2.0,
    "normalize_text": True, "postprocess_output": True,
    "position_temperature": 5.0, "class_temperature": 0.0,
})
PRODUCTION_CONFIG = MappingProxyType({
    "model_path": MODEL_PATH, "ref_audio": REFERENCE_PATH, "ref_text": REF_TEXT,
    "language": "pt", "emotion_tags_enabled": False,
    "voice_profile_id": VOICE_PROFILE_ID, **GENERATION_OPTIONS,
})
ASSET_HASHES = MappingProxyType({
    "model.safetensors": MODEL_SHA256,
    "config.json": "5e359117e13b420c5e0c925d4aba650d624767131f1d1746928f8b850d5dc372",
    "tokenizer.json": "408f669b7e2b045fdf54201d815bd364e6667dbd845115da81239c40bc6dcfd1",
    "audio_tokenizer/model.safetensors": "fe7c5e8785e0a05833e1bfc3e002ec7f55af21e306b2e7154a448c1f54ccfb0d",
    "audio_tokenizer/config.json": "eefb20806f7104e77c9a5277c9df0f9bb8826b08eb1d4e8ab2b9829b6ef9fac1",
})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def production_descriptor(text: str | None = None) -> dict:
    profile = {
        **PRODUCTION_CONFIG, "engine": "omnivoice_v2",
        "model_variant_id": MODEL_VARIANT_ID, "model_instruct": MODEL_INSTRUCT,
        "reference_sha256": REFERENCE_SHA256, "model_sha256": MODEL_SHA256,
        "asset_hashes": dict(ASSET_HASHES), "dtype": "float16", "device": "cuda:0",
    }
    if text is not None and reference_variant(text) == "anchor":
        profile.update(voice_profile_id=ANCHOR_PROFILE_ID, ref_audio=str(ANCHOR_PATH),
                       ref_text=ANCHOR_TEXT, reference_sha256=ANCHOR_SHA256)
    profile["profile_fingerprint"] = hashlib.sha256(
        json.dumps(profile, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return profile


def validate_production_config(values: dict) -> dict:
    """Reject stale settings instead of silently selecting another acoustic voice."""
    for key, expected in PRODUCTION_CONFIG.items():
        actual = values.get(key, expected)
        if key in {"model_path", "ref_audio"}:
            actual = str(Path(actual).expanduser().resolve())
        if actual != expected:
            raise ValueError(f"production_voice_config_mismatch:{key}")
    return dict(PRODUCTION_CONFIG)


def validate_production_assets(model_path: Path, reference: Path, ref_text: str) -> None:
    validate_production_config({"model_path": str(model_path), "ref_audio": str(reference), "ref_text": ref_text})
    if sha256_file(reference) != REFERENCE_SHA256:
        raise ValueError("production_reference_sha256_mismatch")
    for relative, expected in ASSET_HASHES.items():
        if sha256_file(model_path / relative) != expected:
            raise ValueError(f"production_model_sha256_mismatch:{relative}")


# Both references passed 30/30 human listening checks on 2026-09-09.
# Display slots were shuffled: expressive routing is a user-authorized product
# choice, not a measured prosody advantage of the anchor.
ANCHOR_PROFILE_ID = "CELIA_VOICE_ANCHOR01_V1"
ANCHOR_PATH = Path(__file__).resolve().parents[3] / "assets/voices/celia/anchor-01.wav"
ANCHOR_SHA256 = "b33d72454563064774bbdbc9508c78e4e7ffb0764fa8d2136f07512b3d01d913"
ANCHOR_TEXT = "Pai, eu estou pronta para conversar com você agora."
VOICE_ROUTING_ID = "celia_oficial_v2_all_speech"


def reference_variant(text: str) -> str:
    # The owner selected Célia Oficial V2 for every utterance, including exclamations.
    return "original"


def validate_reference_descriptor(profile: dict) -> None:
    expected = production_descriptor("!" if profile.get("voice_profile_id") == ANCHOR_PROFILE_ID else None)
    if profile != expected:
        raise ValueError("production_voice_descriptor_mismatch")
    if sha256_file(Path(profile["ref_audio"])) != profile["reference_sha256"]:
        raise ValueError("production_reference_sha256_mismatch")
