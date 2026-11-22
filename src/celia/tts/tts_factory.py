from .tts_interface import TTSInterface


class TTSFactory:
    @staticmethod
    def get_tts_engine(engine_type, **kwargs) -> TTSInterface:
        if engine_type == "omnivoice":
            from .omnivoice_tts import TTSEngine as OmniVoiceTTSEngine

            from .production_voice import validate_production_config

            return OmniVoiceTTSEngine(**validate_production_config(kwargs))

        if engine_type == "piper_v9d":
            from .piper_v9d_tts import TTSEngine as PiperV9DTTSEngine

            return PiperV9DTTSEngine(**kwargs)

        if engine_type == "azure_tts":
            from .azure_tts import TTSEngine as AzureTTSEngine

            return AzureTTSEngine(
                kwargs.get("api_key"),
                kwargs.get("region"),
                kwargs.get("voice"),
                kwargs.get("pitch"),
                kwargs.get("rate"),
            )

        if engine_type == "edge_tts":
            from .edge_tts import TTSEngine as EdgeTTSEngine

            return EdgeTTSEngine(kwargs.get("voice"))

        if engine_type == "pyttsx3_tts":
            from .pyttsx3_tts import TTSEngine as Pyttsx3TTSEngine

            return Pyttsx3TTSEngine()

        if engine_type == "gpt_sovits_tts":
            from .gpt_sovits_tts import TTSEngine as GSVEngine

            return GSVEngine(
                api_url=kwargs.get("api_url"),
                text_lang=kwargs.get("text_lang"),
                ref_audio_path=kwargs.get("ref_audio_path"),
                prompt_lang=kwargs.get("prompt_lang"),
                prompt_text=kwargs.get("prompt_text"),
                text_split_method=kwargs.get("text_split_method"),
                batch_size=kwargs.get("batch_size"),
                media_type=kwargs.get("media_type"),
                streaming_mode=kwargs.get("streaming_mode"),
            )

        if engine_type == "openai_tts":
            from .openai_tts import TTSEngine as OpenAITTSEngine

            return OpenAITTSEngine(
                model=kwargs.get("model"),
                voice=kwargs.get("voice"),
                api_key=kwargs.get("api_key"),
                base_url=kwargs.get("base_url"),
                file_extension=kwargs.get("file_extension"),
            )

        if engine_type == "elevenlabs_tts":
            from .elevenlabs_tts import TTSEngine as ElevenLabsTTSEngine

            return ElevenLabsTTSEngine(
                api_key=kwargs.get("api_key"),
                voice_id=kwargs.get("voice_id"),
                model_id=kwargs.get("model_id", "eleven_multilingual_v2"),
                output_format=kwargs.get("output_format", "mp3_44100_128"),
                stability=kwargs.get("stability", 0.5),
                similarity_boost=kwargs.get("similarity_boost", 0.5),
                style=kwargs.get("style", 0.0),
                use_speaker_boost=kwargs.get("use_speaker_boost", True),
            )

        raise ValueError(f"Unknown TTS engine type: {engine_type}")
