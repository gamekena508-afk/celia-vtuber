"""
This module contains the pydantic model for the configurations of
different types of agents.
"""

from pydantic import BaseModel, Field
from typing import Dict, ClassVar, Optional, Literal, List
from .i18n import I18nMixin, Description
from .stateless_llm import StatelessLLMConfigs

# ======== Configurations for different Agents ========


class BasicMemoryAgentConfig(I18nMixin, BaseModel):
    """Configuration for the basic memory agent."""
    llm_provider: Literal[
        "stateless_llm_with_template",
        "openai_compatible_llm",
        "claude_llm",
        "llama_cpp_llm",
        "ollama_llm",
        "lmstudio_llm",
        "openai_llm",
        "gemini_llm",
        "zhipu_llm",
        "deepseek_llm",
        "groq_llm",
        "mistral_llm",
    ] = Field(..., alias="llm_provider")
    faster_first_response: Optional[bool] = Field(True, alias="faster_first_response")
    segment_method: Literal["regex", "pysbd"] = Field("pysbd", alias="segment_method")
    use_mcpp: Optional[bool] = Field(False, alias="use_mcpp")
    mcp_enabled_servers: Optional[List[str]] = Field([], alias="mcp_enabled_servers")
    tool_agent_enabled: Optional[bool] = Field(False, alias="tool_agent_enabled")
    tool_agent_base_url: Optional[str] = Field(
        "https://api.groq.com/openai/v1", alias="tool_agent_base_url"
    )
    tool_agent_api_keys: Optional[str] = Field("", alias="tool_agent_api_keys")
    tool_agent_model: Optional[str] = Field(
        "qwen/qwen3-32b", alias="tool_agent_model"
    )
    tool_agent_fallback_model: Optional[str] = Field(
        "", alias="tool_agent_fallback_model"
    )
    tool_agent_temperature: Optional[float] = Field(
        0.2, alias="tool_agent_temperature"
    )
    tool_agent_max_tokens: Optional[int] = Field(512, alias="tool_agent_max_tokens")
    tool_agent_rate_limit_cooldown_sec: Optional[int] = Field(
        60, alias="tool_agent_rate_limit_cooldown_sec"
    )
    tool_agent_auth_cooldown_sec: Optional[int] = Field(
        900, alias="tool_agent_auth_cooldown_sec"
    )
    bridge_enabled: Optional[bool] = Field(False, alias="bridge_enabled")
    bridge_url: Optional[str] = Field(
        "http://localhost:12394/bridge/process", alias="bridge_url"
    )
    bridge_timeout: Optional[int] = Field(30, alias="bridge_timeout")
    memory_book_enabled: Optional[bool] = Field(True, alias="memory_book_enabled")
    memory_book_max_lines: Optional[int] = Field(100, alias="memory_book_max_lines")
    memory_book_max_chars: Optional[int] = Field(12000, alias="memory_book_max_chars")
    memory_book_compact_every_n_updates: Optional[int] = Field(
        20, alias="memory_book_compact_every_n_updates"
    )
    memory_book_prompt_top_items: Optional[int] = Field(
        8, alias="memory_book_prompt_top_items"
    )
    memory_book_anti_repeat_window: Optional[int] = Field(
        8, alias="memory_book_anti_repeat_window"
    )
    memory_book_lang: Optional[str] = Field("en", alias="memory_book_lang")
    memory_book_global_scope: Optional[bool] = Field(
        True, alias="memory_book_global_scope"
    )
    max_history: Optional[int] = Field(25, alias="max_history")
    memory_book_decay_half_life_days: Optional[float] = Field(30.0, alias="memory_book_decay_half_life_days")
    memory_book_embeddings_enabled: Optional[bool] = Field(False, alias="memory_book_embeddings_enabled")
    memory_book_embeddings_model: Optional[str] = Field(
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        alias="memory_book_embeddings_model",
    )
    memory_book_semantic_weight: Optional[float] = Field(0.5, alias="memory_book_semantic_weight")
    memory_book_summary_via_llm: Optional[bool] = Field(False, alias="memory_book_summary_via_llm")
    memory_book_summary_timeout: Optional[float] = Field(3.0, alias="memory_book_summary_timeout")
    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "llm_provider": Description(
            en="LLM provider to use for this agent",
            zh="Basic Memory Agent LLM provider",
        ),
        "faster_first_response": Description(
            en="Whether to respond as soon as encountering a comma in the first sentence to reduce latency (default: True)",
            zh="Whether to respond quickly in first sentence",
        ),
        "segment_method": Description(
            en="Method for segmenting sentences: 'regex' or 'pysbd' (default: 'pysbd')",
            zh="Sentence segmentation method",
        ),
        "use_mcpp": Description(
            en="Whether to use MCP (Model Context Protocol) for the agent",
            zh="Whether to enable MCP",
        ),
        "mcp_enabled_servers": Description(
            en="List of MCP servers to enable for the agent",
            zh="Enabled MCP server list",
        ),
        "memory_book_enabled": Description(
            en="Enable persistent memory book across histories",
            zh="Enable persistent memory book across histories",
        ),
        "memory_book_max_lines": Description(
            en="Max memory book item count after compaction",
            zh="Max memory book item count after compaction",
        ),
        "memory_book_max_chars": Description(
            en="Max total memory book chars after compaction",
            zh="Max total memory book chars after compaction",
        ),
        "memory_book_compact_every_n_updates": Description(
            en="Run memory compaction every N updates",
            zh="Run memory compaction every N updates",
        ),
        "memory_book_prompt_top_items": Description(
            en="How many memory items to inject into prompt",
            zh="How many memory items to inject into prompt",
        ),
        "memory_book_anti_repeat_window": Description(
            en="Recent response window for anti-repetition",
            zh="Recent response window for anti-repetition",
        ),
        "memory_book_lang": Description(
            en="Preferred summary language for memory book items",
            zh="Preferred summary language for memory book items",
        ),
        "memory_book_global_scope": Description(
            en="Whether memory book is global for the character",
            zh="Whether memory book is global for the character",
        ),
        "max_history": Description(
            en="Max messages kept in short-term RAM memory (pairs of user+assistant)",
            zh="Max messages kept in short-term RAM memory",
        ),
    }

class Mem0VectorStoreConfig(I18nMixin, BaseModel):
    """Configuration for Mem0 vector store."""

    provider: str = Field(..., alias="provider")
    config: Dict = Field(..., alias="config")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "provider": Description(
            en="Vector store provider (e.g., qdrant)", zh="å‘é‡å­˜å‚¨æä¾›è€…ï¼ˆå¦‚ qdrantï¼‰"
        ),
        "config": Description(
            en="Provider-specific configuration", zh="æä¾›è€…ç‰¹å®šé…ç½®"
        ),
    }


class Mem0LLMConfig(I18nMixin, BaseModel):
    """Configuration for Mem0 LLM."""

    provider: str = Field(..., alias="provider")
    config: Dict = Field(..., alias="config")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "provider": Description(en="LLM provider name", zh="è¯­è¨€æ¨¡åž‹æä¾›è€…åç§°"),
        "config": Description(
            en="Provider-specific configuration", zh="æä¾›è€…ç‰¹å®šé…ç½®"
        ),
    }


class Mem0EmbedderConfig(I18nMixin, BaseModel):
    """Configuration for Mem0 embedder."""

    provider: str = Field(..., alias="provider")
    config: Dict = Field(..., alias="config")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "provider": Description(en="Embedder provider name", zh="åµŒå…¥æ¨¡åž‹æä¾›è€…åç§°"),
        "config": Description(
            en="Provider-specific configuration", zh="æä¾›è€…ç‰¹å®šé…ç½®"
        ),
    }


class Mem0Config(I18nMixin, BaseModel):
    """Configuration for Mem0."""

    vector_store: Mem0VectorStoreConfig = Field(..., alias="vector_store")
    llm: Mem0LLMConfig = Field(..., alias="llm")
    embedder: Mem0EmbedderConfig = Field(..., alias="embedder")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "vector_store": Description(en="Vector store configuration", zh="å‘é‡å­˜å‚¨é…ç½®"),
        "llm": Description(en="LLM configuration", zh="è¯­è¨€æ¨¡åž‹é…ç½®"),
        "embedder": Description(en="Embedder configuration", zh="åµŒå…¥æ¨¡åž‹é…ç½®"),
    }


# =================================


class HumeAIConfig(I18nMixin, BaseModel):
    """Configuration for the Hume AI agent."""

    api_key: str = Field(..., alias="api_key")
    host: str = Field("api.hume.ai", alias="host")
    config_id: Optional[str] = Field(None, alias="config_id")
    idle_timeout: int = Field(15, alias="idle_timeout")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "api_key": Description(
            en="API key for Hume AI service", zh="Hume AI æœåŠ¡çš„ API å¯†é’¥"
        ),
        "host": Description(
            en="Host URL for Hume AI service (default: api.hume.ai)",
            zh="Hume AI æœåŠ¡çš„ä¸»æœºåœ°å€ï¼ˆé»˜è®¤ï¼šapi.hume.aiï¼‰",
        ),
        "config_id": Description(
            en="Configuration ID for EVI settings", zh="EVI é…ç½® ID"
        ),
        "idle_timeout": Description(
            en="Idle timeout in seconds before disconnecting (default: 15)",
            zh="ç©ºé—²è¶…æ—¶æ–­å¼€è¿žæŽ¥çš„ç§’æ•°ï¼ˆé»˜è®¤ï¼š15ï¼‰",
        ),
    }


# =================================


class LettaConfig(I18nMixin, BaseModel):
    """Configuration for the Letta agent."""

    host: str = Field("localhost", alias="host")
    port: int = Field(8283, alias="port")
    id: str = Field(..., alias="id")
    faster_first_response: Optional[bool] = Field(True, alias="faster_first_response")
    segment_method: Literal["regex", "pysbd"] = Field("pysbd", alias="segment_method")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "host": Description(
            en="Host address for the Letta server", zh="LettaæœåŠ¡å™¨çš„ä¸»æœºåœ°å€"
        ),
        "port": Description(
            en="Port number for the Letta server (default: 8283)",
            zh="LettaæœåŠ¡å™¨çš„ç«¯å£å·ï¼ˆé»˜è®¤ï¼š8283ï¼‰",
        ),
        "id": Description(
            en="Agent instance ID running on the Letta server",
            zh="æŒ‡å®šLettaæœåŠ¡å™¨ä¸Šè¿è¡Œçš„Agentå®žä¾‹id",
        ),
    }


class AgentSettings(I18nMixin, BaseModel):
    """Settings for different types of agents."""

    basic_memory_agent: Optional[BasicMemoryAgentConfig] = Field(
        None, alias="basic_memory_agent"
    )
    mem0_agent: Optional[Mem0Config] = Field(None, alias="mem0_agent")
    hume_ai_agent: Optional[HumeAIConfig] = Field(None, alias="hume_ai_agent")
    letta_agent: Optional[LettaConfig] = Field(None, alias="letta_agent")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "basic_memory_agent": Description(
            en="Configuration for basic memory agent", zh="åŸºç¡€è®°å¿†ä»£ç†é…ç½®"
        ),
        "mem0_agent": Description(en="Configuration for Mem0 agent", zh="Mem0ä»£ç†é…ç½®"),
        "hume_ai_agent": Description(
            en="Configuration for Hume AI agent", zh="Hume AI ä»£ç†é…ç½®"
        ),
        "letta_agent": Description(
            en="Configuration for Letta agent", zh="Letta ä»£ç†é…ç½®"
        ),
    }


class AgentConfig(I18nMixin, BaseModel):
    """This class contains all of the configurations related to agent."""

    conversation_agent_choice: Literal[
        "basic_memory_agent", "mem0_agent", "hume_ai_agent", "letta_agent"
    ] = Field(..., alias="conversation_agent_choice")
    agent_settings: AgentSettings = Field(..., alias="agent_settings")
    llm_configs: StatelessLLMConfigs = Field(..., alias="llm_configs")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "conversation_agent_choice": Description(
            en="Type of conversation agent to use", zh="è¦ä½¿ç”¨çš„å¯¹è¯ä»£ç†ç±»åž‹"
        ),
        "agent_settings": Description(
            en="Settings for different agent types", zh="ä¸åŒä»£ç†ç±»åž‹çš„è®¾ç½®"
        ),
        "llm_configs": Description(
            en="Pool of LLM provider configurations", zh="è¯­è¨€æ¨¡åž‹æä¾›è€…é…ç½®æ± "
        ),
        "faster_first_response": Description(
            en="Whether to respond as soon as encountering a comma in the first sentence to reduce latency (default: True)",
            zh="æ˜¯å¦åœ¨ç¬¬ä¸€å¥å›žåº”æ—¶é‡ä¸Šé€—å·å°±ç›´æŽ¥ç”ŸæˆéŸ³é¢‘ä»¥å‡å°‘é¦–å¥å»¶è¿Ÿï¼ˆé»˜è®¤ï¼šTrueï¼‰",
        ),
        "segment_method": Description(
            en="Method for segmenting sentences: 'regex' or 'pysbd' (default: 'pysbd')",
            zh="åˆ†å‰²å¥å­çš„æ–¹æ³•ï¼š'regex' æˆ– 'pysbd'ï¼ˆé»˜è®¤ï¼š'pysbd'ï¼‰",
        ),
    }
