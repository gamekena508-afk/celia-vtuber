"""Constructs prompts for servers and tools, formats tool information for OpenAI API."""

from typing import Dict, Optional, List, Tuple, Any
from loguru import logger

from .types import FormattedTool
from .mcp_client import MCPClient
from .server_registry import ServerRegistry


class ToolAdapter:
    """Dynamically fetches tool information from enabled MCP servers and formats it."""

    def __init__(self, server_registery: Optional[ServerRegistry] = None) -> None:
        """Initialize with an ServerRegistry."""
        self.server_registery = server_registery or ServerRegistry()

    async def get_server_and_tool_info(
        self, enabled_servers: List[str]
    ) -> Tuple[Dict[str, Dict[str, str]], Dict[str, FormattedTool]]:
        """Fetch tool information from specified enabled MCP servers."""
        servers_info: Dict[str, Dict[str, str]] = {}
        formatted_tools: Dict[str, FormattedTool] = {}

        if not enabled_servers:
            logger.warning(
                "MC: No enabled MCP servers specified. Cannot fetch tool info."
            )
            return servers_info, formatted_tools

        logger.debug(f"MC: Fetching tool info for enabled servers: {enabled_servers}")

        try:
            async with MCPClient(self.server_registery) as client:

                async def fetch_server_tools(server_name: str):
                    if server_name not in self.server_registery.servers:
                        logger.warning(
                            f"MC: Enabled server '{server_name}' not found in Server Manager. Skipping."
                        )
                        return None

                    try:
                        tools = await client.list_tools(server_name)
                        logger.debug(
                            f"MC: Found {len(tools)} tools on server '{server_name}'"
                        )
                        server_tools = {}
                        for tool in tools:
                            server_tools[tool.name] = {
                                "description": tool.description,
                                "parameters": tool.inputSchema.get("properties", {}),
                                "required": tool.inputSchema.get("required", []),
                            }
                            # Also prepare the FormattedTool object
                            f_tool = FormattedTool(
                                input_schema=tool.inputSchema,
                                related_server=server_name,
                                description=tool.description,
                                generic_schema=None,
                            )
                            formatted_tools[tool.name] = f_tool
                        return server_name, server_tools
                    except (ValueError, RuntimeError, ConnectionError) as e:
                        logger.error(
                            f"MC: Failed to get info for server '{server_name}': {e}"
                        )
                        return server_name, {}
                    except Exception as e:
                        logger.error(
                            f"MC: Unexpected error for server '{server_name}': {e}"
                        )
                        return server_name, {}

                # Execute all fetches in parallel
                # results = await asyncio.gather(
                #     *[fetch_server_tools(name) for name in enabled_servers]
                # )
                # WAIT: I added a lock, so gather will run them sequentially.
                # If I want true parallelism without errors, I'd need separate clients.
                # But sequential is fine for now as it's more stable.
                results = []
                for name in enabled_servers:
                    results.append(await fetch_server_tools(name))

                for result in results:
                    if result:
                        name, tools_dict = result
                        servers_info[name] = tools_dict
        except Exception as e:
            logger.warning(f"MC: Error during MCPClient cleanup/operation: {e}")
            # We still return whatever we got in formatted_tools/servers_info

        logger.debug(
            f"MC: Finished fetching tool info. Found {len(formatted_tools)} tools across enabled servers."
        )
        return servers_info, formatted_tools

    def construct_mcp_prompt_string(
        self, servers_info: Dict[str, Dict[str, str]]
    ) -> str:
        """Build a single prompt string describing enabled servers and their tools."""
        full_prompt_content = (
            "!!! IMPORTANT INSTRUCTION FOR TOOL USE !!!\n"
            "You have access to the following system tools. To use a tool, you MUST output ONLY a valid JSON object matching the required format.\n"
            "DO NOT fake the execution of the tool or write normal text simulating the action. DO NOT include any text outside the JSON block.\n"
            "Format the JSON exactly like this:\n"
            "{\n"
            '  "mcp_server": "<server_name>",\n'
            '  "tool": "<tool_name>",\n'
            '  "args": { "param_name": "param_value" }\n'
            "}\n"
            "If a tool has no parameters, pass an empty object: \"args\": {}\n"
            "Your ONLY function is to output this JSON object if you want to use a tool.\n"
            "--------------------------------------------------\n"
        )
        if not servers_info:
            logger.warning(
                "MC: Cannot construct MCP prompt string, servers_info is empty."
            )
            return full_prompt_content

        logger.debug(
            f"MC: Constructing MCP prompt string for {len(servers_info)} server(s)."
        )

        for server_name, tools in servers_info.items():
            if not tools:  # Skip servers where info couldn't be fetched
                logger.warning(
                    f"MC: No tool info available for server '{server_name}', skipping in prompt."
                )
                continue

            prompt_content = f"Server: {server_name}\n"
            prompt_content += "    Tools:\n"
            for tool_name, tool_info in tools.items():
                prompt_content += f"        {tool_name}:\n"
                # Ensure description is handled correctly (might be None)
                description = tool_info.get("description", "No description available.")
                prompt_content += f"            Description: {description}\n"
                parameters = tool_info.get("parameters", {})
                if parameters:
                    prompt_content += "            Parameters:\n"
                    for param_name, param_info in parameters.items():
                        param_desc = param_info.get("description") or param_info.get(
                            "title", "No description provided."
                        )
                        param_type = param_info.get(
                            "type", "string"
                        )  # Default to string if type missing
                        prompt_content += f"                {param_name}:\n"
                        prompt_content += f"                    Type: {param_type}\n"
                        prompt_content += (
                            f"                    Description: {param_desc}\n"
                        )
                required = tool_info.get("required", [])
                if required:
                    prompt_content += f"            Required: {', '.join(required)}\n"
            full_prompt_content += prompt_content + "\n"  # Add newline between servers

        logger.debug("MC: Finished constructing MCP prompt string.")
        return full_prompt_content.strip()  # Remove trailing newline

    def format_tools_for_api(
        self, formatted_tools_dict: Dict[str, FormattedTool]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Format tools to OpenAI and Claude function-calling compatible schemas."""
        openai_tools = []
        claude_tools = []

        if not formatted_tools_dict:
            logger.warning(
                "MC: Cannot format tools for API, input dictionary is empty."
            )
            return openai_tools, claude_tools

        logger.debug(f"MC: Formatting {len(formatted_tools_dict)} tools for API usage.")

        for tool_name, data_object in formatted_tools_dict.items():
            if not isinstance(data_object, FormattedTool):
                logger.warning(f"MC: Skipping invalid tool format for '{tool_name}'")
                continue

            input_schema = data_object.input_schema
            properties: Dict[str, Dict[str, str]] = input_schema.get("properties", {})
            raw_description = data_object.description or "No description provided."
            # Trunca descrições longas — docstrings enormes confundem Groq/Llama-Scout
            # Pega só a primeira linha não-vazia (antes de "Args:", "Returns:", etc.)
            first_line = raw_description.split("\n")[0].strip()
            tool_description = first_line[:200] if first_line else raw_description[:200]
            required_params = input_schema.get("required", [])

            # Format for OpenAI
            openai_function_params = {
                "type": "object",
                "properties": {},
            }
            # Só inclui 'required' quando há parâmetros obrigatórios — Groq rejeita required: []
            if required_params:
                openai_function_params["required"] = required_params
            for param_name, param_info in properties.items():
                param_type = param_info.get("type", "string")

                # Groq/Llama rejeitam anyOf em schemas de tool — normaliza tudo para string simples
                # Tipos complexos (anyOf, oneOf, allOf no param_info) também são achatados
                if param_type not in ("string", "number", "boolean", "array", "object"):
                    param_type = "string"

                # Se o schema original já tem anyOf (ex: Content-Length), usa string simples
                if "anyOf" in param_info or "oneOf" in param_info:
                    param_type = "string"

                param_schema: Dict[str, Any] = {
                    "type": param_type,
                    "description": param_info.get("description")
                    or param_info.get("title", "No description provided."),
                }

                # Add enum if present
                if "enum" in param_info:
                    param_schema["enum"] = param_info["enum"]
                # Handle array type correctly
                if param_type == "array" and "items" in param_info:
                    param_schema["items"] = param_info["items"]
                elif param_type == "array" and "items" not in param_info:
                    param_schema["items"] = {"type": "string"}

                openai_function_params["properties"][param_name] = param_schema

            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": tool_description,
                        "parameters": openai_function_params,
                    },
                }
            )

            # Format for Claude
            claude_input_schema = {
                "type": "object",
                "properties": properties,
                "required": required_params,
            }
            claude_tools.append(
                {
                    "name": tool_name,
                    "description": tool_description,
                    "input_schema": claude_input_schema,
                }
            )

        logger.debug(
            f"MC: Finished formatting tools. OpenAI: {len(openai_tools)}, Claude: {len(claude_tools)}."
        )
        return openai_tools, claude_tools

    async def get_tools(
        self, enabled_servers: List[str]
    ) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run the dynamic fetching and formatting process."""
        logger.info(
            f"MC: Running dynamic tool construction for servers: {enabled_servers}"
        )
        servers_info, formatted_tools_dict = await self.get_server_and_tool_info(
            enabled_servers
        )
        mcp_prompt_string = self.construct_mcp_prompt_string(servers_info)
        openai_tools, claude_tools = self.format_tools_for_api(formatted_tools_dict)
        logger.info("MC: Dynamic tool construction complete.")
        return mcp_prompt_string, openai_tools, claude_tools
