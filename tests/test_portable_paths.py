import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_primary_runtime_configuration_has_no_fixed_user_paths():
    files = (
        "bin/atualizar-painel.py",
        "bin/automacao-celia.sh",
        "conf.yaml",
        "mcp_servers.json",
        "mcp/edicao_de_imagem.py",
        "mcp/celia_paths.py",
        "mcp/mcp_system_automation.py",
        "src/celia/bridge_routes.py",
        "src/celia/core/obs_subtitles.py",
        "integrations/discord/celia_discord_bot.py",
        "run_server.py",
        "tools/minecraft_debug_dashboard.py",
        "docs/arquitetura_memoria.md",
        "docs/celia_lore.md",
        "extensions/react/doc/plano/nova-vision.md",
    )

    for relative_path in files:
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "/home/<usuario>/" not in content
        assert "C:/Users/" not in content
        assert "C:\\Users\\" not in content


def test_mcp_config_uses_the_running_interpreter_and_project_relative_scripts():
    config = json.loads((ROOT / "mcp_servers.json").read_text(encoding="utf-8"))

    for name, server in config["mcp_servers"].items():
        if server["command"] != "python":
            continue
        assert not Path(server["args"][0]).is_absolute()


def test_optional_external_services_are_explicitly_configured():
    source = (ROOT / "mcp/mcp_system_automation.py").read_text(encoding="utf-8")

    assert 'CELIA_ALICE_BACKEND_SCRIPT' in source
    assert 'CELIA_LUNA_DASHBOARD_SCRIPT' in source
    assert 'INDISPONÍVEL' in source
