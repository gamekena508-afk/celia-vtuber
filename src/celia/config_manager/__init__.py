"""
Configuration management package for Célia.

This package provides configuration management functionality through Pydantic models
and utility functions for loading and saving configurations.
"""

from .main import Config
from .system import SystemConfig
from .character import CharacterConfig
from .live import BiliBiliLiveConfig, LiveConfig
from .stateless_llm import ClaudeConfig, LlamaCppConfig, OpenAICompatibleConfig
from .asr import (
    ASRConfig,
    AzureASRConfig,
    FasterWhisperConfig,
    FunASRConfig,
    GroqWhisperASRConfig,
    SherpaOnnxASRConfig,
    WhisperConfig,
    WhisperCPPConfig,
)
from .tts import (
    AzureTTSConfig,
    EdgeTTSConfig,
    ElevenLabsTTSConfig,
    GPTSoVITSConfig,
    OpenAITTSConfig,
    TTSConfig,
)
from .vad import VADConfig
from .tts_preprocessor import DeepLXConfig, TTSPreprocessorConfig, TranslatorConfig
from .i18n import Description, I18nMixin, MultiLingualString
from .agent import (
    AgentConfig,
    AgentSettings,
    BasicMemoryAgentConfig,
    Mem0Config,
    Mem0EmbedderConfig,
    Mem0LLMConfig,
    Mem0VectorStoreConfig,
    StatelessLLMConfigs,
)
from .utils import (
    read_yaml,
    save_config,
    scan_bg_directory,
    scan_config_alts_directory,
    validate_config,
)

__all__ = [
    "Config",
    "SystemConfig",
    "CharacterConfig",
    "LiveConfig",
    "BiliBiliLiveConfig",
    "OpenAICompatibleConfig",
    "ClaudeConfig",
    "LlamaCppConfig",
    "AgentConfig",
    "AgentSettings",
    "StatelessLLMConfigs",
    "BasicMemoryAgentConfig",
    "Mem0Config",
    "Mem0VectorStoreConfig",
    "Mem0LLMConfig",
    "Mem0EmbedderConfig",
    "ASRConfig",
    "AzureASRConfig",
    "FasterWhisperConfig",
    "WhisperCPPConfig",
    "WhisperConfig",
    "FunASRConfig",
    "SherpaOnnxASRConfig",
    "GroqWhisperASRConfig",
    "TTSConfig",
    "AzureTTSConfig",
    "EdgeTTSConfig",
    "GPTSoVITSConfig",
    "OpenAITTSConfig",
    "ElevenLabsTTSConfig",
    "VADConfig",
    "TTSPreprocessorConfig",
    "TranslatorConfig",
    "DeepLXConfig",
    "I18nMixin",
    "Description",
    "MultiLingualString",
    "read_yaml",
    "validate_config",
    "save_config",
    "scan_config_alts_directory",
    "scan_bg_directory",
]
