# config_manager/tts.py
from typing import ClassVar, Dict, Literal

from pydantic import Field, model_validator

from .i18n import Description, I18nMixin


def _production_default(name: str) -> str:
    from ..tts import production_voice  # só biblioteca padrão; sem import circular

    return getattr(production_voice, name)


class AzureTTSConfig(I18nMixin):
    api_key: str = Field(..., alias="api_key")
    region: str = Field(..., alias="region")
    voice: str = Field(..., alias="voice")
    pitch: str | int | float = Field(..., alias="pitch")
    rate: str | int | float = Field(..., alias="rate")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "api_key": Description(en="API key for Azure TTS service", zh="Azure TTS API key"),
        "region": Description(en="Azure region", zh="Azure region"),
        "voice": Description(en="Voice name to use for Azure TTS", zh="Azure voice"),
        "pitch": Description(en="Pitch adjustment percentage", zh="Pitch"),
        "rate": Description(en="Speaking rate adjustment", zh="Rate"),
    }


class EdgeTTSConfig(I18nMixin):
    voice: str = Field(..., alias="voice")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "voice": Description(en="Voice name to use for Edge TTS", zh="Edge TTS voice"),
    }


class OmniVoiceConfig(I18nMixin):
    # Sem valor no conf.yaml, vale o caminho de production_voice.py
    # (CELIA_OMNIVOICE_MODEL_DIR / CELIA_VOICE_REFERENCE_WAV): nada pessoal no conf.
    model_path: str = Field(default_factory=lambda: _production_default("MODEL_PATH"), alias="model_path")
    ref_audio: str = Field(default_factory=lambda: _production_default("REFERENCE_PATH"), alias="ref_audio")
    ref_text: str = Field(..., alias="ref_text")
    language: str = Field("pt", alias="language")
    emotion_tags_enabled: bool = Field(False, alias="emotion_tags_enabled")
    voice_profile_id: str = "CELIA_VOICE_OFICIAL_V1"
    seed: int | None = None
    speed: float = 0.88
    num_step: int = 10
    guidance_scale: float = 2.0
    normalize_text: bool = True
    postprocess_output: bool = True
    position_temperature: float = 5.0
    class_temperature: float = 0.0


class GPTSoVITSConfig(I18nMixin):
    api_url: str = Field(..., alias="api_url")
    text_lang: str = Field(..., alias="text_lang")
    ref_audio_path: str = Field(..., alias="ref_audio_path")
    prompt_lang: str = Field(..., alias="prompt_lang")
    prompt_text: str = Field(..., alias="prompt_text")
    text_split_method: str = Field(..., alias="text_split_method")
    batch_size: str = Field(..., alias="batch_size")
    media_type: str = Field(..., alias="media_type")
    streaming_mode: str = Field(..., alias="streaming_mode")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "api_url": Description(en="URL of the GPT-SoVITS API endpoint", zh="GPT-SoVITS URL"),
        "text_lang": Description(en="Language of the input text", zh="Text language"),
        "ref_audio_path": Description(en="Path to reference audio file", zh="Reference audio"),
        "prompt_lang": Description(en="Language of the prompt", zh="Prompt language"),
        "prompt_text": Description(en="Prompt text", zh="Prompt text"),
        "text_split_method": Description(en="Method for splitting text", zh="Split method"),
        "batch_size": Description(en="Batch size for processing", zh="Batch size"),
        "media_type": Description(en="Output media type", zh="Media type"),
        "streaming_mode": Description(en="Enable streaming mode", zh="Streaming mode"),
    }


class OpenAITTSConfig(I18nMixin):
    model: str | None = Field(None, alias="model")
    voice: str | None = Field(None, alias="voice")
    api_key: str | None = Field(None, alias="api_key")
    base_url: str | None = Field(None, alias="base_url")
    file_extension: Literal["mp3", "wav"] = Field("mp3", alias="file_extension")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "model": Description(en="Model name for the TTS server", zh="Model"),
        "voice": Description(en="Voice name for the TTS server", zh="Voice"),
        "api_key": Description(en="API key if required by the TTS server", zh="API key"),
        "base_url": Description(en="Base URL of the TTS server", zh="Base URL"),
        "file_extension": Description(en="Audio file format", zh="File format"),
    }


class ElevenLabsTTSConfig(I18nMixin):
    api_key: str = Field(..., alias="api_key")
    voice_id: str = Field(..., alias="voice_id")
    model_id: str = Field("eleven_multilingual_v2", alias="model_id")
    output_format: str = Field("mp3_44100_128", alias="output_format")
    stability: float = Field(0.5, alias="stability")
    similarity_boost: float = Field(0.5, alias="similarity_boost")
    style: float = Field(0.0, alias="style")
    use_speaker_boost: bool = Field(True, alias="use_speaker_boost")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "api_key": Description(en="API key for ElevenLabs TTS service", zh="API key"),
        "voice_id": Description(en="Voice ID from ElevenLabs", zh="Voice ID"),
        "model_id": Description(en="Model ID for ElevenLabs", zh="Model ID"),
        "output_format": Description(en="Output audio format", zh="Output format"),
        "stability": Description(en="Voice stability", zh="Stability"),
        "similarity_boost": Description(en="Voice similarity boost", zh="Similarity boost"),
        "style": Description(en="Voice style exaggeration", zh="Style"),
        "use_speaker_boost": Description(en="Enable speaker boost", zh="Speaker boost"),
    }


class PiperV9DConfig(I18nMixin):
    model_path: str = Field(..., alias="model_path")
    config_path: str = Field(..., alias="config_path")
    piper_source_path: str = Field(..., alias="piper_source_path")
    use_cuda: bool = Field(True, alias="use_cuda")


class TTSConfig(I18nMixin):
    tts_model: Literal[
        "azure_tts",
        "edge_tts",
        "pyttsx3_tts",
        "omnivoice",
        "piper_v9d",
        "gpt_sovits_tts",
        "openai_tts",
        "elevenlabs_tts",
    ] = Field(..., alias="tts_model")

    azure_tts: AzureTTSConfig | None = Field(None, alias="azure_tts")
    edge_tts: EdgeTTSConfig | None = Field(None, alias="edge_tts")
    omnivoice: OmniVoiceConfig | None = Field(None, alias="omnivoice")
    piper_v9d: PiperV9DConfig | None = Field(None, alias="piper_v9d")
    gpt_sovits_tts: GPTSoVITSConfig | None = Field(None, alias="gpt_sovits")
    openai_tts: OpenAITTSConfig | None = Field(None, alias="openai_tts")
    elevenlabs_tts: ElevenLabsTTSConfig | None = Field(None, alias="elevenlabs_tts")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "tts_model": Description(en="Text-to-speech model to use", zh="TTS model"),
        "azure_tts": Description(en="Configuration for Azure TTS", zh="Azure TTS"),
        "edge_tts": Description(en="Configuration for Edge TTS", zh="Edge TTS"),
        "gpt_sovits_tts": Description(en="Configuration for GPT-SoVITS", zh="GPT-SoVITS"),
        "openai_tts": Description(en="Configuration for OpenAI-compatible TTS", zh="OpenAI TTS"),
        "elevenlabs_tts": Description(en="Configuration for ElevenLabs TTS", zh="ElevenLabs TTS"),
    }

    @model_validator(mode="after")
    def check_tts_config(self):
        selected_configs = {
            "azure_tts": self.azure_tts,
            "edge_tts": self.edge_tts,
            "omnivoice": self.omnivoice,
            "piper_v9d": self.piper_v9d,
            "gpt_sovits_tts": self.gpt_sovits_tts,
            "openai_tts": self.openai_tts,
            "elevenlabs_tts": self.elevenlabs_tts,
        }
        selected_config = selected_configs.get(self.tts_model)

        if self.tts_model != "pyttsx3_tts" and selected_config is None:
            raise ValueError(f"Missing config for selected TTS model: {self.tts_model}")

        if selected_config is not None:
            type(selected_config).model_validate(selected_config.model_dump())

        return self
