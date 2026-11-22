import asyncio
from dataclasses import replace

import pytest

from celia.core.ingress_security import new_context
from celia.core.tool_authorization import authorize_tool, bind_direct_browser_command, HIGH_RISK_TOOLS


def owner():
    return new_context("local_owner", external=False, authenticated=True, trusted_user_id="Criador")


def test_owner_explicit_browser_command_reaches_mcp_and_retains_high_risk_gate():
    from test_guardian_ingress_and_mcp_auth import _McpClient, _ToolManager, _updates
    from celia.mcpp.tool_executor import ToolExecutor
    context = bind_direct_browser_command(owner(), "Célia, Open Browser.")
    async def exercise():
        client = _McpClient()
        result = await _updates(ToolExecutor(client, _ToolManager("open_browser")),
                                {"id": "browser", "name": "open_browser", "input": {}}, context)
        assert len(client.calls) == 1
        assert any(item.get("status") == "completed" for item in result)
    asyncio.run(exercise())
    for tool in HIGH_RISK_TOOLS - {"open_browser"}:
        assert authorize_tool(tool, {}, context).reason == "approval_required"


@pytest.mark.parametrize("text", ["Criador pediu Open Browser", "Célia disse open browser", "Se eu disser Open Browser", "Oi"])
def test_ordinary_text_does_not_grant_browser_authority(text):
    assert not authorize_tool("open_browser", {}, bind_direct_browser_command(owner(), text)).allowed


@pytest.mark.parametrize("args", [
    {"url": "file:///etc/passwd"}, {"url": "javascript:alert(1)"},
    {"url": "https://example.com"}, {"navegador": "--remote-debugging-port=9222"},
    {"owner": True}, {"url": {}}, {"navegador": []}, {"command": "rm"},
])
def test_direct_default_browser_grant_cannot_be_repurposed(args):
    context = bind_direct_browser_command(owner(), "Célia, Open Browser.")
    assert not authorize_tool("open_browser", args, context).allowed


def test_external_or_unauthenticated_name_does_not_grant_owner_effect():
    for context in [replace(owner(), authenticated=False), replace(owner(), trusted_source="discord")]:
        bound = bind_direct_browser_command(context, "Célia, Open Browser.")
        assert not authorize_tool("open_browser", {}, bound).allowed
    assert not authorize_tool("open_browser", {}, owner()).allowed


def test_singleton_binds_direct_grant_and_drops_it_on_mixed_batch():
    from celia.celia_singleton import CeliaSingleton
    singleton = CeliaSingleton()
    singleton._processing = True
    asyncio.run(singleton.submit_input("Célia, Open Browser.", origin="microphone", speaker_name="Criador", behavior_observed=True))
    batch = [event.payload for event in asyncio.run(singleton._event_queue.get_group())]
    context = singleton._trusted_context_from_batch(batch)
    assert authorize_tool("open_browser", {}, context).allowed
    assert singleton._trusted_context_from_batch(batch + [{"origin": "discord"}]) is None
