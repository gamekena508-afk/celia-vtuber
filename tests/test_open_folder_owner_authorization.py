from pathlib import Path

from celia.agent.agents.basic_memory_agent import BasicMemoryAgent
from celia.core.ingress_security import new_context
from celia.core.tool_authorization import (
    authorize_tool,
    bind_direct_folder_command,
    resolve_direct_folder_command,
)


ROOT = Path(__file__).resolve().parents[1]


def _owner(source="local_owner"):
    return new_context(
        source,
        external=False,
        authenticated=True,
        trusted_user_id="Criador",
        authorization_scope=("owner.full",),
    )


def test_installation_folder_command_routes_exact_path_and_authorizes_it():
    text = "Cadê Célia? Abre a pasta de instalação."
    target = resolve_direct_folder_command(text)
    context = bind_direct_folder_command(_owner(), text)
    call = BasicMemoryAgent.__new__(BasicMemoryAgent)._forced_system_tool_call(text)

    assert target == ROOT
    assert call == {
        "id": "forced_open_folder_0",
        "name": "open_folder",
        "input": {"path": str(ROOT)},
        "_direct_tool": True,
    }
    decision = authorize_tool("open_folder", call["input"], context)
    assert decision.allowed
    assert decision.reason == "owner_direct_command"


def test_docs_alias_opens_only_the_project_docs_folder():
    text = "Abre a pasta docs."
    context = bind_direct_folder_command(_owner(), text)
    target = resolve_direct_folder_command(text)

    assert target == ROOT / "docs"
    assert authorize_tool("open_folder", {"path": "docs"}, context).allowed
    assert not authorize_tool("open_folder", {"path": str(ROOT / "logs")}, context).allowed
    assert not authorize_tool("open_folder", {"path": ""}, context).allowed


def test_explicit_project_relative_folder_path_is_supported_but_cannot_escape_root():
    text = "Abre a pasta src/celia."
    target = ROOT / "src" / "celia"
    context = bind_direct_folder_command(_owner(), text)
    assert resolve_direct_folder_command(text) == target
    assert authorize_tool("open_folder", {"path": str(target)}, context).allowed
    assert resolve_direct_folder_command("Abre a pasta ../") is None


def test_vague_or_retrospective_folder_mentions_do_not_grant_access():
    for text in (
        "Abre aí, tu tá vendo os logs, né?",
        "Quando pedi para abrir a pasta docs, ela negou.",
        "Mostra a pasta",
    ):
        context = bind_direct_folder_command(_owner(), text)
        assert resolve_direct_folder_command(text) is None
        assert not authorize_tool("open_folder", {"path": str(ROOT / "docs")}, context).allowed


def test_external_owner_lookalike_and_other_tools_keep_their_existing_denials():
    text = "Abre a pasta docs."
    public = _owner("youtube_chat")
    public_context = bind_direct_folder_command(public, text)
    assert not authorize_tool("open_folder", {"path": str(ROOT / "docs")}, public_context).allowed

    local_context = bind_direct_folder_command(_owner(), text)
    assert not authorize_tool("open_folder", {"path": str(ROOT / "logs")}, local_context).allowed
    assert not authorize_tool("create_file", {"filepath": str(ROOT / "docs/new.md")}, local_context).allowed
