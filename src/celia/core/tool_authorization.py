"""Independent authorization gate for MCP effects.

The LLM proposes a tool and arguments. It never supplies identity, capability,
approval, or a bypass marker; those facts come only from ``TrustedContext``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re
import unicodedata
from typing import Any

from .ingress_security import TrustedContext


RESERVED_AUTHORITY_FIELDS = frozenset({
    "user_id", "owner", "is_admin", "authenticated", "approved",
    "permission_level", "authorization_scope", "guardian_checked",
    "trusted_source", "trusted_user_id", "trusted_session_id",
})


LOW_RISK_TOOLS = frozenset({
    "get_current_time", "convert_timezone", "status_minecraft_bot",
    "minecraft_observar", "minecraft_eventos", "ler_memoria",
    "vision_extract_text", "vision_list_options", "vision_describe_visual",
    "status_geradores_imagem", "discord_status_bot",
    # Jogos locais so resolvem uma allowlist fixa de paginas loopback; trafego
    # externo continua bloqueado antes desta allowlist pelo gate publico.
    "listar_jogos", "abrir_jogo", "fechar_jogo",
    "search", "fetch_content", "expand_link", "resultado_enquete",
    "status_servico", "take_screenshot", "vision_understand_context",
    "vision_custom_analysis", "vision_reconhecer_se_mesma", "vision_identify_character",
})

MEDIUM_CAPABILITIES = {
    "salvar_memoria": "memory.write",
    "esquecer_memoria": "memory.write",
    "atualizar_legenda": "overlay.write",
    "limpar_legenda": "overlay.write",
    "criar_enquete": "poll.write",
    "registrar_voto": "poll.write",
    "fechar_enquete": "poll.write",
    "minecraft_acao": "minecraft.write",
    "minecraft_objetivo": "minecraft.write",
    "minecraft_parar": "minecraft.write",
    "gerar_imagem": "image.generate",
    "editar_imagem": "image.generate",
    "criar_ou_editar_imagem": "image.generate",
    "discord_ligar_bot": "discord.write",
    "discord_postar_mensagem": "discord.write",
    "discord_postar_imagem": "discord.write",
    "registrar_pessoa_na_tela": "vision.write",
}

HIGH_RISK_TOOLS = frozenset({
    "open_browser", "open_folder", "create_file", "execute_command",
    "run_python", "open_terminal", "ligar_minecraft_bot", "ligar_servico",
    "desligar_servico", "call_opencode_agent",
})

# External turns expose only bounded public tools; this does not promote a
# caller to owner. Search is query-only: never expose arbitrary URL fetching,
# link expansion, private memory or PC control to external accounts.
PUBLIC_EXTERNAL_CAPABILITIES = frozenset({"image.generate", "poll.write"})
PUBLIC_EXTERNAL_READ_TOOLS = frozenset({
    "resultado_enquete", "search", "get_current_time", "convert_timezone",
})
PUBLIC_EXTERNAL_TOOL_NAMES = frozenset({
    "gerar_imagem", "editar_imagem", "criar_ou_editar_imagem",
    "criar_enquete", "registrar_voto", "fechar_enquete",
}) | PUBLIC_EXTERNAL_READ_TOOLS

_CELIA_ROOT = Path(__file__).resolve().parents[3]
_DIRECT_FOLDER_TARGETS = {
    "instalacao": _CELIA_ROOT,
    "instalacao da celia": _CELIA_ROOT,
    "raiz do projeto": _CELIA_ROOT,
    "docs": _CELIA_ROOT / "docs",
    "documentos": _CELIA_ROOT / "docs",
    "documentacao": _CELIA_ROOT / "docs",
    "logs": _CELIA_ROOT / "logs",
    "roteiros": _CELIA_ROOT / "roteiros",
    "provas": _CELIA_ROOT / "vault" / "docs" / "provas",
}


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    risk: str
    capability: str
    reason: str


def bind_direct_browser_command(context: TrustedContext, text: str) -> TrustedContext:
    """Bind the exact browser destination explicitly requested on trusted owner ingress."""
    normalized = "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                         if not unicodedata.combining(c))
    match = re.fullmatch(
        r"\s*(?:celia[\s,:!.-]+)?(?:(?:por favor|pode)\s+)?"
        r"(?:open|abr[ae]|abrir)\s+(?:(?:o|a)\s+)?"
        r"(?P<target>google|youtube|gmail|github|navegador|browser|chrome|firefox|edge|https?://\S+)"
        r"(?:\s+(?:agora|entao|pra mim|para mim))*[.!?\s]*",
        normalized,
    )
    if not (context.authenticated and context.trusted_source in {"local_owner", "local_owner_bridge"} and match):
        return context
    target = match.group("target").rstrip(".,;!?)")
    url_map = {
        "google": "https://www.google.com", "navegador": "https://www.google.com",
        "browser": "https://www.google.com", "chrome": "https://www.google.com",
        "firefox": "https://www.google.com", "edge": "https://www.google.com",
        "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com",
        "github": "https://github.com",
    }
    requested_url = url_map.get(target, target)
    return replace(context, authorization_scope=(*context.authorization_scope, f"browser.open.url:{requested_url}"))


def resolve_direct_folder_command(text: str) -> Path | None:
    """Resolve one explicit folder command at the end of a trusted utterance."""
    original_sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    normalized_sentences = re.split(
        r"(?<=[.!?])\s+",
        "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                if not unicodedata.combining(c)).strip(),
    )
    original_command = original_sentences[-1] if original_sentences else ""
    normalized_command = normalized_sentences[-1] if normalized_sentences else ""
    command_pattern = (
        r"\s*(?:celia[\s,:!.\-]+)?(?:(?:por favor|pode)\s+)?"
        r"(?:abre|abra|abrir|mostra|mostre|acessa|acesse)\s+(?:(?:a|o)\s+)?"
        r"(?:(?:pasta|diretorio)\s+(?:(?:de|do|da)\s+)?)?"
        r"(?P<target>.+?)"
        r"(?:\s+(?:agora|entao|pra mim|para mim))*[.!?\s]*"
    )
    match = re.fullmatch(command_pattern, normalized_command)
    original_match = re.fullmatch(command_pattern, original_command, flags=re.IGNORECASE)
    if not match:
        return None
    target = match.group("target").strip().rstrip(".,;!?)").strip()
    raw_target = (
        original_match.group("target").strip().rstrip(".,;!?)").strip()
        if original_match else target
    ).strip("\"'")
    path = _DIRECT_FOLDER_TARGETS.get(target)
    if path is None:
        candidate = Path(raw_target).expanduser()
        if not candidate.is_absolute():
            candidate = _CELIA_ROOT / candidate
        try:
            path = candidate.resolve(strict=True)
        except OSError:
            return None
        if not Path(raw_target).expanduser().is_absolute() and not path.is_relative_to(_CELIA_ROOT):
            return None
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_dir() else None


def bind_direct_folder_command(context: TrustedContext, text: str) -> TrustedContext:
    """Grant one canonical folder only for an explicit authenticated owner command."""
    if not (
        context.authenticated
        and context.trusted_source in {"local_owner", "local_owner_bridge"}
    ):
        return context
    path = resolve_direct_folder_command(text)
    if path is None:
        return context
    grant = f"folder.open.path:{path}"
    return replace(context, authorization_scope=(*context.authorization_scope, grant))


def _requested_folder_path(raw_path: Any) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = _CELIA_ROOT / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_dir() else None


def authorize_tool(
    tool_name: str,
    tool_input: Any,
    context: TrustedContext | None,
) -> AuthorizationDecision:
    """Fail closed for missing context, forged authority, and high-risk effects."""
    if isinstance(tool_input, dict) and RESERVED_AUTHORITY_FIELDS.intersection(tool_input):
        return AuthorizationDecision(False, "HIGH", "", "forged_authority_field")

    # No TrustedContext means that owner identity was never established.
    # Public, stateless reads may proceed; private/PC tools must fail closed.
    if not isinstance(context, TrustedContext):
        if tool_name in PUBLIC_EXTERNAL_READ_TOOLS:
            return AuthorizationDecision(True, "LOW", "public.read", "public_read_without_identity")
        return AuthorizationDecision(False, "HIGH", "", "missing_trusted_context")

    # Only selected public reads (including search) and image/poll operations
    # are exposed to external callers. All other tools remain deny-by-default
    # even when they appear in the general LOW_RISK_TOOLS catalog.
    if (
        isinstance(context, TrustedContext)
        and context.policy_mode == "enforce"
        and "owner.full" not in context.authorization_scope
    ):
        capability = MEDIUM_CAPABILITIES.get(tool_name, "")
        if tool_name in PUBLIC_EXTERNAL_READ_TOOLS:
            capability = {
                "resultado_enquete": "poll.read",
                "search": "web.search",
                "get_current_time": "public.time",
                "convert_timezone": "public.time",
            }[tool_name]
            return AuthorizationDecision(True, "LOW", capability, "external_public_read_allowlist")
        if (
            capability in PUBLIC_EXTERNAL_CAPABILITIES
            and capability in context.authorization_scope
        ):
            return AuthorizationDecision(True, "MEDIUM", capability, "external_public_allowlist")
        return AuthorizationDecision(False, "MEDIUM", capability or "external.denied", "external_public_denied")

    if tool_name in LOW_RISK_TOOLS:
        return AuthorizationDecision(True, "LOW", "read", "low_risk_allow")

    if tool_name in HIGH_RISK_TOOLS:
        if (
            tool_name == "open_folder"
            and isinstance(context, TrustedContext)
            and context.authenticated
            and context.trusted_source in {"local_owner", "local_owner_bridge"}
            and isinstance(tool_input, dict)
            and not (set(tool_input) - {"path"})
        ):
            requested_path = _requested_folder_path(tool_input.get("path"))
            grants = {
                scope.removeprefix("folder.open.path:")
                for scope in context.authorization_scope
                if scope.startswith("folder.open.path:")
            }
            if requested_path is not None and str(requested_path) in grants:
                return AuthorizationDecision(True, "HIGH", "folder.open.direct", "owner_direct_command")
        if (tool_name == "open_browser" and isinstance(context, TrustedContext)
                and context.authenticated and context.trusted_source in {"local_owner", "local_owner_bridge"}
                and isinstance(tool_input, dict)
                and not (set(tool_input) - {"url", "navegador"})
                and tool_input.get("navegador", "padrao") in ("padrao", "chrome", "firefox", "edge")):
            raw_url = tool_input.get("url")
            if raw_url is not None and not isinstance(raw_url, str):
                return AuthorizationDecision(False, "HIGH", "approval.required", "approval_required")
            requested = (raw_url if isinstance(raw_url, str) and raw_url else "https://www.google.com").rstrip("/")
            grants = {scope.removeprefix("browser.open.url:").rstrip("/") for scope in context.authorization_scope if scope.startswith("browser.open.url:")}
            if requested in grants:
                return AuthorizationDecision(True, "HIGH", "browser.open.direct", "owner_direct_command")
        return AuthorizationDecision(False, "HIGH", "approval.required", "approval_required")

    capability = MEDIUM_CAPABILITIES.get(tool_name, "unknown")
    if context is None:
        return AuthorizationDecision(False, "MEDIUM", capability, "missing_trusted_context")
    if not context.authenticated:
        return AuthorizationDecision(False, "MEDIUM", capability, "unauthenticated_context")
    if capability not in context.authorization_scope:
        return AuthorizationDecision(False, "MEDIUM", capability, "capability_denied")
    return AuthorizationDecision(True, "MEDIUM", capability, "capability_allow")
