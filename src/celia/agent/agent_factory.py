from typing import Type, Literal
from loguru import logger

from .agents.agent_interface import AgentInterface
from .agents.basic_memory_agent import BasicMemoryAgent
from .stateless_llm_factory import LLMFactory as StatelessLLMFactory
from .stateless_llm.groq_rotating_llm import GroqRotatingLLM

from ..mcpp.tool_manager import ToolManager
from ..mcpp.tool_executor import ToolExecutor
from typing import Optional


def _chain_or_none(brain: str):
    """Fila de fallback de config/model_chains.yaml, se ligada.

    Qualquer problema na fila (config inválida, import) NUNCA derruba a Célia:
    registra o erro e devolve None, e o sistema antigo segue valendo.
    """
    try:
        from .stateless_llm.chained_llm import build_chained_llm

        chained = build_chained_llm(brain)
        if chained is not None:
            logger.info(f"Fila de fallback '{brain}' LIGADA (config/model_chains.yaml).")
        return chained
    except Exception as exc:  # pragma: no cover - proteção de boot
        logger.error(f"Fila de fallback '{brain}' ignorada por erro: {exc}")
        return None


class AgentFactory:
    @staticmethod
    def create_agent(
        conversation_agent_choice: str,
        agent_settings: dict,
        llm_configs: dict,
        system_prompt: str,
        live2d_model=None,
        tts_preprocessor_config=None,
        **kwargs,
    ) -> Type[AgentInterface]:
        """Create an agent based on the configuration.

        Args:
            conversation_agent_choice: The type of agent to create
            agent_settings: Settings for different types of agents
            llm_configs: Pool of LLM configurations
            system_prompt: The system prompt to use
            live2d_model: Live2D model instance for expression extraction
            tts_preprocessor_config: Configuration for TTS preprocessing
            **kwargs: Additional arguments
        """
        logger.info(f"Initializing agent: {conversation_agent_choice}")

        if conversation_agent_choice == "basic_memory_agent":
            # Get the LLM provider choice from agent settings
            basic_memory_settings: dict = agent_settings.get("basic_memory_agent", {})
            llm_provider: str = basic_memory_settings.get("llm_provider")

            if not llm_provider:
                raise ValueError("LLM provider not specified for basic memory agent")

            # Get the LLM config for this provider
            llm_config: dict = dict(llm_configs.get(llm_provider) or {})
            interrupt_method: Literal["system", "user"] = llm_config.pop(
                "interrupt_method", "user"
            )

            if not llm_config:
                raise ValueError(
                    f"Configuration not found for LLM provider: {llm_provider}"
                )

            # Create the stateless LLM
            llm = StatelessLLMFactory.create_llm(
                llm_provider=llm_provider, system_prompt=system_prompt, **llm_config
            )
            # Fila de fallback da Célia (personagem). Desligada = segue o llm acima.
            celia_chain = _chain_or_none("celia")
            if celia_chain is not None:
                llm = celia_chain
            # Fila do Jev (seletor de tool). Desligada = tool agent antigo abaixo.
            tool_llm = _chain_or_none("jev")
            if tool_llm is None and basic_memory_settings.get("tool_agent_enabled", False):
                tool_llm = GroqRotatingLLM(
                    model=basic_memory_settings.get(
                        "tool_agent_model", "qwen/qwen3-32b"
                    ),
                    api_keys=basic_memory_settings.get("tool_agent_api_keys", ""),
                    base_url=basic_memory_settings.get(
                        "tool_agent_base_url", "https://api.groq.com/openai/v1"
                    ),
                    temperature=basic_memory_settings.get(
                        "tool_agent_temperature", 0.2
                    ),
                    max_tokens=basic_memory_settings.get("tool_agent_max_tokens", 512),
                    rate_limit_cooldown_sec=basic_memory_settings.get(
                        "tool_agent_rate_limit_cooldown_sec", 60
                    ),
                    auth_cooldown_sec=basic_memory_settings.get(
                        "tool_agent_auth_cooldown_sec", 900
                    ),
                    fallback_model=basic_memory_settings.get(
                        "tool_agent_fallback_model", ""
                    ),
                    request_budget_sec=basic_memory_settings.get(
                        "tool_agent_request_budget_sec", 8.0
                    ),
                    hedge_delay_sec=basic_memory_settings.get(
                        "tool_agent_hedge_delay_sec", 4.0
                    ),
                    breaker_failures=basic_memory_settings.get(
                        "tool_agent_breaker_failures", 2
                    ),
                    breaker_cooldown_sec=basic_memory_settings.get(
                        "tool_agent_breaker_cooldown_sec", 30.0
                    ),
                )

            tool_prompts = kwargs.get("system_config", {}).get("tool_prompts", {})

            # Extract MCP components/data needed by BasicMemoryAgent from kwargs
            tool_manager: Optional[ToolManager] = kwargs.get("tool_manager")
            tool_executor: Optional[ToolExecutor] = kwargs.get("tool_executor")
            mcp_prompt_string: str = kwargs.get("mcp_prompt_string", "")
            human_name: str = kwargs.get("human_name", "")
            known_people = kwargs.get("known_people")

            # Create the agent with the LLM and live2d_model
            return BasicMemoryAgent(
                llm=llm,
                tool_llm=tool_llm,
                system=system_prompt,
                live2d_model=live2d_model,
                human_name=human_name,
                known_people=known_people,
                tts_preprocessor_config=tts_preprocessor_config,
                faster_first_response=basic_memory_settings.get(
                    "faster_first_response", True
                ),
                segment_method=basic_memory_settings.get("segment_method", "pysbd"),
                use_mcpp=basic_memory_settings.get("use_mcpp", False),
                interrupt_method=interrupt_method,
                tool_prompts=tool_prompts,
                tool_manager=tool_manager,
                tool_executor=tool_executor,
                mcp_prompt_string=mcp_prompt_string,
                bridge_enabled=basic_memory_settings.get("bridge_enabled", False),
                bridge_url=basic_memory_settings.get("bridge_url", "http://localhost:12394/bridge/process"),
                bridge_timeout=basic_memory_settings.get("bridge_timeout", 30),
                memory_book_enabled=basic_memory_settings.get("memory_book_enabled", True),
                memory_book_max_lines=basic_memory_settings.get("memory_book_max_lines", 100),
                memory_book_max_chars=basic_memory_settings.get("memory_book_max_chars", 12000),
                memory_book_compact_every_n_updates=basic_memory_settings.get(
                    "memory_book_compact_every_n_updates", 20
                ),
                memory_book_prompt_top_items=basic_memory_settings.get(
                    "memory_book_prompt_top_items", 8
                ),
                memory_book_anti_repeat_window=basic_memory_settings.get(
                    "memory_book_anti_repeat_window", 8
                ),
                memory_book_lang=basic_memory_settings.get("memory_book_lang", "en"),
                memory_book_global_scope=basic_memory_settings.get(
                    "memory_book_global_scope", True
                ),
                max_history=basic_memory_settings.get("max_history", 25),
                memory_book_decay_half_life_days=basic_memory_settings.get(
                    "memory_book_decay_half_life_days", 30.0
                ),
                memory_book_embeddings_enabled=basic_memory_settings.get(
                    "memory_book_embeddings_enabled", False
                ),
                memory_book_embeddings_model=basic_memory_settings.get(
                    "memory_book_embeddings_model",
                    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                ),
                memory_book_semantic_weight=basic_memory_settings.get(
                    "memory_book_semantic_weight", 0.5
                ),
                memory_book_summary_via_llm=basic_memory_settings.get(
                    "memory_book_summary_via_llm", False
                ),
                memory_book_summary_timeout=basic_memory_settings.get(
                    "memory_book_summary_timeout", 3.0
                ),
            )

        elif conversation_agent_choice == "mem0_agent":
            from .agents.mem0_llm import LLM as Mem0LLM

            mem0_settings = agent_settings.get("mem0_agent", {})
            if not mem0_settings:
                raise ValueError("Mem0 agent settings not found")

            # Validate required settings
            required_fields = ["base_url", "model", "mem0_config"]
            for field in required_fields:
                if field not in mem0_settings:
                    raise ValueError(
                        f"Missing required field '{field}' in mem0_agent settings"
                    )

            return Mem0LLM(
                user_id=kwargs.get("user_id", "default"),
                system=system_prompt,
                live2d_model=live2d_model,
                **mem0_settings,
            )

        elif conversation_agent_choice == "hume_ai_agent":
            from .agents.hume_ai import HumeAIAgent

            settings = agent_settings.get("hume_ai_agent", {})
            return HumeAIAgent(
                api_key=settings.get("api_key"),
                host=settings.get("host", "api.hume.ai"),
                config_id=settings.get("config_id"),
                idle_timeout=settings.get("idle_timeout", 15),
            )

        elif conversation_agent_choice == "letta_agent":
            from .agents.letta_agent import LettaAgent

            settings = agent_settings.get("letta_agent", {})
            return LettaAgent(
                live2d_model=live2d_model,
                id=settings.get("id"),
                tts_preprocessor_config=tts_preprocessor_config,
                faster_first_response=settings.get("faster_first_response"),
                segment_method=settings.get("segment_method"),
                host=settings.get("host"),
                port=settings.get("port"),
            )

        else:
            raise ValueError(f"Unsupported agent type: {conversation_agent_choice}")
