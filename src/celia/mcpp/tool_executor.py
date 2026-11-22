import json
import datetime
from loguru import logger
from typing import (
    Dict,
    Any,
    List,
    Literal,
    Union,
    AsyncIterator,
)

from .types import ToolCallObject
from .mcp_client import MCPClient
from .tool_manager import ToolManager
from ..core.observability import observability
from ..core.training_trace import training_trace
from ..core.tool_authorization import AuthorizationDecision, authorize_tool
from ..core import turn_tool_guard
from ..core.ingress_security import TrustedContext


class ToolExecutor:
    # Fase 4.1 (jun/2026): cache de tool calls read-only com TTL curto.
    # Quando o user pergunta 2x seguidas "como ta a celia?" em 10s,
    # minecraft_observar é chamado 2x. Cachear elimina a 2ª (~1.5s).
    # Whitelist: SÓ tools sem side-effect e cujo resultado pode mudar pouco em 5s.
    _CACHE_TTL_S = 5.0
    _CACHEABLE_TOOLS = frozenset({
        "minecraft_observar",
        "minecraft_eventos",
        "status_minecraft_bot",
    })
    _TRANSIENT_ERROR_MARKERS = (
        "timeout", "timed out", "connection", "temporarily unavailable",
        "temporary failure", "rate limit", "429", "502", "503", "504",
        "reset by peer", "broken pipe",
    )
    _PARAMETER_ERROR_MARKERS = (
        "invalid argument", "invalid parameter", "validation error",
        "must be", "expected type", "extra field", "unexpected field",
    )

    def __init__(
        self,
        mcp_client: MCPClient,
        tool_manager: ToolManager,
    ):
        self._mcp_client = mcp_client
        self._tool_manager = tool_manager
        # cache: {(tool_name, input_json): (timestamp, (is_error, text, metadata, items))}
        self._cache: dict = {}

    @classmethod
    def _classify_tool_error(cls, text: str) -> str:
        normalized = str(text or "").casefold()
        if any(marker in normalized for marker in cls._TRANSIENT_ERROR_MARKERS):
            return "transient"
        if any(marker in normalized for marker in cls._PARAMETER_ERROR_MARKERS):
            return "parameters"
        return "terminal"

    def _repair_tool_input(self, tool_name: str, tool_input: Any) -> dict[str, Any] | None:
        """Apply only schema-safe coercions; never invent authority or required values."""
        if not isinstance(tool_input, dict):
            return None
        tool_info = self._tool_manager.get_tool(tool_name)
        schema = getattr(tool_info, "input_schema", None) or {}
        properties = schema.get("properties", {})
        repaired = dict(tool_input)
        if schema.get("additionalProperties") is False:
            repaired = {key: value for key, value in repaired.items() if key in properties}
        for key, spec in properties.items():
            if key not in repaired:
                continue
            value = repaired[key]
            try:
                if spec.get("type") == "integer" and not isinstance(value, int):
                    repaired[key] = int(value)
                elif spec.get("type") == "number" and not isinstance(value, (int, float)):
                    repaired[key] = float(value)
                elif spec.get("type") == "boolean" and isinstance(value, str):
                    lowered = value.casefold().strip()
                    if lowered in {"true", "1", "yes", "sim"}:
                        repaired[key] = True
                    elif lowered in {"false", "0", "no", "nao", "não"}:
                        repaired[key] = False
            except (TypeError, ValueError):
                return None
        return repaired if repaired != tool_input else None

    def _cache_lookup(self, tool_name: str, tool_input: Any):
        """Retorna resultado cacheado se válido e dentro do TTL, senão None."""
        if tool_name not in self._CACHEABLE_TOOLS:
            return None
        import time, json as _json
        try:
            key = (tool_name, _json.dumps(tool_input or {}, sort_keys=True))
        except (TypeError, ValueError):
            return None
        entry = self._cache.get(key)
        if not entry:
            return None
        ts, result = entry
        if time.time() - ts > self._CACHE_TTL_S:
            del self._cache[key]
            return None
        return result

    def _cache_store(self, tool_name: str, tool_input: Any, result):
        if tool_name not in self._CACHEABLE_TOOLS:
            return
        import time, json as _json
        try:
            key = (tool_name, _json.dumps(tool_input or {}, sort_keys=True))
        except (TypeError, ValueError):
            return
        self._cache[key] = (time.time(), result)
        # cap simples pra não crescer indefinido
        if len(self._cache) > 50:
            oldest = min(self._cache.items(), key=lambda kv: kv[1][0])[0]
            del self._cache[oldest]

    def parse_tool_call(self, call: Union[Dict[str, Any], ToolCallObject]) -> tuple:
        """Parse tool call from different formats.

        Returns:
            tuple: (tool_name, tool_id, tool_input, is_error, result_content, parse_error)
        """
        tool_name: str = ""
        tool_id: str = ""
        tool_input: Any = None
        is_error: bool = False
        result_content: str | dict = ""
        parse_error: bool = False

        if isinstance(call, ToolCallObject):
            tool_name = call.function.name
            tool_id = call.id
            try:
                arguments = call.function.arguments
                tool_input = json.loads(arguments) if arguments and arguments.strip() else {}
            except json.JSONDecodeError:
                logger.error(
                    f"Failed to decode OpenAI tool arguments for '{tool_name}'"
                )
                result_content = (
                    f"Error: Invalid arguments format for tool '{tool_name}'."
                )
                is_error = True
                parse_error = True
        elif isinstance(call, dict):
            tool_id = call.get("id")
            tool_name = call.get("name")
            tool_input = call.get("input", call.get("args"))

            if tool_input is None:
                logger.warning(
                    f"Empty input for tool '{tool_name}' (ID: {tool_id}). Using empty object."
                )
                tool_input = {}

            if not tool_id or not tool_name:
                logger.error(f"Invalid Dict tool call structure: {call}")
                result_content = "Error: Invalid tool call structure from LLM."
                is_error = True
                parse_error = True
        else:
            logger.error(f"Unsupported tool call type: {type(call)}")
            result_content = "Error: Unsupported tool call type."
            is_error = True
            parse_error = True

        return tool_name, tool_id, tool_input, is_error, result_content, parse_error

    def format_tool_result(
        self,
        caller_mode: Literal["Claude", "OpenAI", "Prompt"],
        tool_id: str,
        result_content: str,
        is_error: bool,
    ) -> Dict[str, Any] | None:
        """Format tool result for LLM API."""
        if caller_mode == "Claude":
            # Claude expects content as a list of blocks or a simple string
            # We will return a list if there are multiple items or non-text items
            if isinstance(result_content, list):
                # Already formatted as list of blocks
                content_to_send = result_content
            elif isinstance(result_content, str) and result_content:
                # Simple text result
                content_to_send = result_content
            elif not result_content and is_error:
                # Error case, send error message as string
                content_to_send = "Error occurred during tool execution."
            else:
                # Fallback for empty or unexpected content
                content_to_send = ""

            return {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": content_to_send,
                "is_error": is_error,
            }
        elif caller_mode == "OpenAI":
            # DeepSeek V4 Flash não suporta role: "tool".
            # Usa role: "user" com conteúdo embutido como alternativa.
            return {
                "role": "user",
                "content": f"Resultado da tool call (id: {tool_id}): {str(result_content)}",
            }
        elif caller_mode == "Prompt":
            # Prompt mode also expects a string content for now
            return {
                "tool_id": tool_id,
                "content": str(result_content),
                "is_error": is_error,
            }
        return None

    def process_tool_from_prompt_json(
        self, data: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Process tool data from JSON in prompt mode."""
        parsed_tools = []
        for item in data:
            server = item.get("mcp_server")
            tool_name = item.get("tool")
            arguments_val = item.get("arguments", item.get("args"))
            if server and tool_name and arguments_val is not None:
                try:
                    if isinstance(arguments_val, dict):
                        args_dict = arguments_val
                    else:
                        args_dict = json.loads(str(arguments_val))
                    parsed_tools.append(
                        {
                            "name": tool_name,
                            "server": server,
                            "args": args_dict,
                            "id": f"prompt_tool_{len(parsed_tools)}",
                        }
                    )
                    logger.info(f"Parsed tool call from prompt JSON: {tool_name}")
                except json.JSONDecodeError:
                    logger.error(
                        "Failed to decode arguments JSON in prompt mode tool call"
                    )
                except Exception as e:
                    logger.error(f"Error processing prompt mode tool dict: {e}")
            else:
                logger.warning("Skipping invalid tool structure in prompt mode JSON")
        return parsed_tools

    async def execute_tools(
        self,
        tool_calls: Union[List[Dict[str, Any]], List[ToolCallObject]],
        caller_mode: Literal["Claude", "OpenAI", "Prompt"],
        trusted_context: TrustedContext | None = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Execute tools and yield status updates."""
        tool_results_for_llm = []

        logger.info(f"Executing {len(tool_calls)} tool(s) for {caller_mode} caller.")
        for call in tool_calls:
            (
                tool_name,
                tool_id,
                tool_input,
                is_error,
                result_content,
                parse_error,
            ) = self.parse_tool_call(call)

            logger.info(f"Executing tool: {call}")
            training_trace.capture(
                "tool_call",
                {"tool_name": tool_name, "tool_id": tool_id, "args": tool_input, "caller_mode": caller_mode},
                layer="tool",
                tool_id=str(tool_id or ""),
            )

            if parse_error:
                logger.warning(
                    f"Skipping tool call due to parsing error: {result_content}"
                )
                status_update = {
                    "type": "tool_call_status",
                    "tool_id": tool_id
                    or f"parse_error_{datetime.datetime.now(datetime.timezone.utc).isoformat()}",
                    "tool_name": tool_name or "Unknown Tool",
                    "status": "error",
                    "content": result_content,
                    "timestamp": datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat()
                    + "Z",
                }
                yield status_update
                # Even on parse error, we might need to format a result for the LLM
                # Use dummy values or the error message
                formatted_result = self.format_tool_result(
                    caller_mode,
                    tool_id
                    or f"parse_error_{datetime.datetime.now(datetime.timezone.utc).isoformat()}",
                    result_content,
                    True,  # is_error
                )
                if formatted_result:
                    tool_results_for_llm.append(formatted_result)
                continue  # Skip execution logic for this call

            decision = authorize_tool(tool_name, tool_input, trusted_context)
            if decision.allowed:
                # Pré-filtro de turno de voz: só RESTRINGE (nunca concede). Fragmento ambíguo / ordem repetida na mesma fala não executa.
                guard_reason = turn_tool_guard.check(tool_name, tool_input)
                if guard_reason:
                    decision = AuthorizationDecision(False, decision.risk, decision.capability, f"turn_guard:{guard_reason}")
            training_trace.capture(
                "tool_authorization",
                {"tool_name": tool_name, "allowed": decision.allowed, "reason": decision.reason},
                layer="tool",
                trainable=False,
                tool_id=str(tool_id or ""),
            )
            observability.emit(
                "tool_authorization",
                "mcp.authorization_gate",
                "allowed" if decision.allowed else "denied",
                {
                    "tool_name": tool_name,
                    "reason": decision.reason,
                    "final_effect": "allow" if decision.allowed else "deny",
                },
            )
            if not decision.allowed:
                result_content = f"Denied by MCP authorization gate: {decision.reason}."
                status_update = {
                    "type": "tool_call_status",
                    "tool_id": tool_id,
                    "tool_name": tool_name,
                    "status": "denied",
                    "content": result_content,
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat() + "Z",
                }
                yield status_update
                formatted_result = self.format_tool_result(
                    caller_mode, tool_id, result_content, True
                )
                if formatted_result:
                    tool_results_for_llm.append(formatted_result)
                continue

            # Yield 'running' status before execution
            yield {
                "type": "tool_call_status",
                "tool_id": tool_id,
                "tool_name": tool_name,
                "status": "running",
                "content": f"Input: {json.dumps(tool_input)}",
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
                + "Z",
            }

            # Execute the tool
            (
                is_error,
                text_content,
                metadata,
                content_items,
            ) = await self.run_single_tool(tool_name, tool_id, tool_input)

            # One bounded recovery attempt. Authorization denials never reach
            # this branch, unknown tools are terminal, and no loop is possible.
            if is_error and tool_name not in {
                "criar_ou_editar_imagem", "gerar_imagem", "editar_imagem",
                "minecraft_acao", "minecraft_objetivo", "minecraft_parar",
            }:
                error_class = self._classify_tool_error(text_content)
                retry_input = tool_input if error_class == "transient" else None
                if error_class == "parameters":
                    retry_input = self._repair_tool_input(tool_name, tool_input)
                if retry_input is not None:
                    logger.warning(
                        "Recoverable tool failure: %s class=%s; retrying once",
                        tool_name,
                        error_class,
                    )
                    training_trace.capture(
                        "tool_retry",
                        {"tool_name": tool_name, "error_class": error_class, "attempt": 1},
                        layer="tool",
                        trainable=False,
                        tool_id=str(tool_id or ""),
                    )
                    yield {
                        "type": "tool_call_status",
                        "tool_id": tool_id,
                        "tool_name": tool_name,
                        "status": "retrying",
                        "content": f"Recoverable {error_class} failure; retry 1/1.",
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat() + "Z",
                    }
                    tool_input = retry_input
                    (
                        is_error,
                        text_content,
                        metadata,
                        content_items,
                    ) = await self.run_single_tool(tool_name, tool_id, tool_input)

            training_trace.capture(
                "tool_result",
                {
                    "tool_name": tool_name,
                    "tool_id": tool_id,
                    "is_error": is_error,
                    "text": text_content,
                    "metadata": metadata,
                    "content_types": [item.get("type") for item in (content_items or [])],
                },
                layer="tool",
                tool_id=str(tool_id or ""),
            )

            # Determine content for status update and LLM result format
            status_content = text_content  # Default to text content
            llm_formatted_content = text_content  # Default to text content for LLM

            if content_items:
                image_items = [
                    item for item in content_items if item.get("type") == "image"
                ]
                if image_items:
                    num_images = len(image_items)
                    status_content = (
                        f"{text_content}\n[Tool returned {num_images} image(s)]".strip()
                    )

                    if caller_mode == "Claude":
                        # Format for Claude: list of blocks
                        claude_blocks = []
                        if text_content:
                            claude_blocks.append({"type": "text", "text": text_content})
                        for item in content_items:
                            if (
                                item.get("type") == "image"
                                and "data" in item
                                and "mimeType" in item
                            ):
                                claude_blocks.append(
                                    {
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": item["mimeType"],
                                            "data": item["data"],
                                        },
                                    }
                                )
                            # Add other non-text types here
                        llm_formatted_content = (
                            claude_blocks if claude_blocks else ""
                        )  # Use blocks or empty string
                    elif caller_mode in ["OpenAI", "Prompt"]:
                        llm_formatted_content = status_content

            # Prepare and yield tool call status update
            status_update = {
                "type": "tool_call_status",
                "tool_id": tool_id,
                "tool_name": tool_name,
                "status": "error" if is_error else "completed",
                "content": status_content
                if not is_error
                else f"Error: {text_content}",  # Use descriptive content or error message
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
                + "Z",
            }

            # For stagehand_navigate tool, include browser view links if available
            if tool_name == "stagehand_navigate" and not is_error:
                live_view_data = metadata.get("liveViewData", {})
                if live_view_data:
                    logger.info(
                        f"Found live view data for stagehand_navigate: {live_view_data}"
                    )
                    status_update["browser_view"] = live_view_data

            yield status_update

            # Jev: registra o resultado real (e a visão) como fato com idade.
            # Observação pura: não altera resultado, autorização ou execução.
            try:
                from ..core.jev import observe_tool_result

                observe_tool_result(
                    str(tool_name or ""),
                    text_content if is_error else status_content,
                    is_error=bool(is_error),
                )
            except Exception:  # noqa: BLE001
                pass

            # Format result for LLM and add to list
            formatted_result = self.format_tool_result(
                caller_mode, tool_id, llm_formatted_content, is_error
            )
            if formatted_result:
                tool_results_for_llm.append(formatted_result)

        logger.info(
            f"Finished executing tools with {len(tool_results_for_llm)} results."
        )
        yield {"type": "final_tool_results", "results": tool_results_for_llm}

    async def run_single_tool(
        self, tool_name: str, tool_id: str, tool_input: Any
    ) -> tuple[bool, str, Dict[str, Any], List[Dict[str, Any]]]:
        """Run a single tool using MCPClient.

        Returns:
            tuple: (is_error, text_content, metadata, content_items)
        """
        logger.info(f"Executing tool: {tool_name} (ID: {tool_id})")

        # Some models hallucinate a "respond" tool. Treat it as a no-op that simply
        # returns the provided text so the conversation doesn't stall.
        if tool_name == "respond":
            if isinstance(tool_input, dict):
                text_content = (
                    tool_input.get("text")
                    or tool_input.get("message")
                    or tool_input.get("content")
                    or ""
                )
            else:
                text_content = "" if tool_input is None else str(tool_input)
            return False, str(text_content), {}, [{"type": "text", "text": str(text_content)}]

        # Fase 4.1: cache hit pra tool read-only em janela curta
        cached = self._cache_lookup(tool_name, tool_input)
        if cached is not None:
            logger.info(f"Cache HIT ({self._CACHE_TTL_S}s TTL): {tool_name}")
            return cached

        tool_info = self._tool_manager.get_tool(tool_name)

        is_error = False
        text_content = ""
        metadata = {}
        content_items = []

        if tool_input is None:
            tool_input = {}

        # ── Sanitize integer fields ──────────────────────────────────────────
        # Some LLMs (e.g. Llama-4-Scout on Groq) send integer parameters as
        # strings (e.g. max_results="10"), which causes Groq's schema validator
        # to reject the entire tool-call request with a 400 error.
        # We coerce any field declared as "integer" in the tool's JSON schema.
        if tool_info and isinstance(tool_input, dict):
            try:
                # FormattedTool stores the raw schema in input_schema
                raw_schema = getattr(tool_info, "input_schema", None) or {}
                schema_props = raw_schema.get("properties", {})
                for field_name, spec in schema_props.items():
                    if spec.get("type") == "integer" and field_name in tool_input:
                        raw = tool_input[field_name]
                        if not isinstance(raw, int):
                            try:
                                tool_input[field_name] = int(raw)
                                logger.debug(
                                    f"Coerced '{field_name}' from {type(raw).__name__} "
                                    f"to int for tool '{tool_name}'"
                                )
                            except (ValueError, TypeError):
                                logger.warning(
                                    f"Could not coerce '{field_name}'={raw!r} to int "
                                    f"for tool '{tool_name}'"
                                )
            except Exception as coerce_err:
                logger.warning(f"Type sanitizer failed for '{tool_name}': {coerce_err}")
        # ────────────────────────────────────────────────────────────────────

        if not tool_info:
            logger.error(f"Tool '{tool_name}' not found in ToolManager.")
            text_content = f"Error: Tool '{tool_name}' is not available."
            content_items = [{"type": "error", "text": text_content}]
            is_error = True
        elif not tool_info.related_server:
            logger.error(f"Tool '{tool_name}' does not have a related server defined.")
            text_content = f"Error: Configuration error for tool '{tool_name}'. No server specified."
            content_items = [{"type": "error", "text": text_content}]
            is_error = True
        else:
            try:
                result_dict = await self._mcp_client.call_tool(
                    server_name=tool_info.related_server,
                    tool_name=tool_name,
                    tool_args=tool_input,
                )

                metadata = result_dict.get("metadata", {})
                content_items = result_dict.get("content_items", [])

                # Check if the first content item is an error reported by MCPClient
                if content_items and content_items[0].get("type") == "error":
                    is_error = True
                    text_content = content_items[0].get(
                        "text", "Unknown error from tool execution."
                    )
                elif content_items and content_items[0].get("type") == "text":
                    text_content = content_items[0].get("text", "")
                # If no text item is first, text_content remains ""

                if not is_error:
                    logger.info(f"Tool '{tool_name}' executed successfully.")
                    if content_items:
                        logger.info(f"Content items from tool '{tool_name}':")
                        for item in content_items:
                            item_type = item.get("type", "unknown")
                            logger.info(f"  Type: {item_type}")
                            for key, value in item.items():
                                if (
                                    key != "type" and key != "data"
                                ):  # Avoid logging large data
                                    log_value = (
                                        f"(length: {len(value)})"
                                        if isinstance(value, str) and len(value) > 100
                                        else value
                                    )
                                    logger.info(f"    {key}: {log_value}")

            except (ValueError, RuntimeError, ConnectionError) as e:
                logger.exception(f"Error executing tool '{tool_name}': {e}")
                text_content = f"Error executing tool '{tool_name}': {e}"
                content_items = [{"type": "error", "text": text_content}]
                is_error = True
            except Exception as e:
                logger.exception(f"Unexpected error executing tool '{tool_name}': {e}")
                text_content = f"Unexpected error executing tool '{tool_name}': {e}"
                content_items = [{"type": "error", "text": text_content}]
                is_error = True

        # Fase 4.1: salva no cache se for tool read-only e a execução foi OK
        if not is_error:
            self._cache_store(tool_name, tool_input, (is_error, text_content, metadata, content_items))
        return is_error, text_content, metadata, content_items
