"""Side-effect-free Windows launcher preflight for Celia V02.

This module deliberately does not import the application, load dotenv, print
configuration contents, bind a socket, or start child processes. It reads the
sanitized V02 YAML only to extract the system bind host and port.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent
APP_ENTRYPOINT = PROJECT_ROOT / "run_server.py"
APP_CONFIG = PROJECT_ROOT / "conf.yaml"
PYTHON_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"


def effective_bind(config_data: dict, env: dict[str, str] | None = None) -> tuple[str, int]:
    """Resolve YAML values, then apply explicitly configured environment overrides."""
    if not isinstance(config_data, dict):
        raise ValueError("V02 YAML root must be a mapping")
    values = os.environ if env is None else env
    system = config_data.get("system_config", {})
    if system is None:
        system = {}
    if not isinstance(system, dict):
        raise ValueError("system_config must be a mapping")
    yaml_host = system.get("host")
    yaml_port = system.get("port")
    if "CELIA_BIND_HOST" in values:
        host = values["CELIA_BIND_HOST"].strip()
        if not host:
            raise ValueError("CELIA_BIND_HOST is set but empty")
    else:
        if yaml_host is not None and not isinstance(yaml_host, str):
            raise ValueError("system_config.host must be a string")
        host = yaml_host.strip() if yaml_host is not None else "localhost"
    if not host:
        host = "localhost"
    if "CELIA_PORT" in values:
        raw_port = values["CELIA_PORT"].strip()
        if not raw_port:
            raise ValueError("CELIA_PORT is set but empty")
    else:
        raw_port = str(yaml_port) if yaml_port not in (None, "") else "12393"
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("CELIA_PORT must be an integer from 1 to 65535") from exc
    if not 1 <= port <= 65535:
        raise ValueError("CELIA_PORT must be an integer from 1 to 65535")
    return host, port


def preflight(root: Path = PROJECT_ROOT, env: dict[str, str] | None = None) -> dict[str, str | int]:
    """Read only bind fields from local YAML and report paths without app startup."""
    root = root.resolve()
    py = root / ".venv" / "Scripts" / "python.exe"
    entrypoint = root / "run_server.py"
    config = root / "conf.yaml"
    missing = [str(path) for path in (py, entrypoint, config) if not path.is_file()]
    if missing:
        raise FileNotFoundError("V02 preflight missing required files: " + ", ".join(missing))
    try:
        with config.open("r", encoding="utf-8-sig") as stream:
            config_data = yaml.safe_load(stream)
    except yaml.YAMLError:
        raise ValueError("conf.yaml is not valid YAML; parser details redacted") from None
    if config_data is None:
        config_data = {}
    host, port = effective_bind(config_data, env)
    return {
        "project_root": str(root),
        "python": str(py),
        "entrypoint": str(entrypoint),
        "config_path": str(config),
        "bind_host": host,
        "port": port,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Celia V02 launcher preflight")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="check paths and bind overrides")
    mode.add_argument("--config", action="store_true", help="show sanitized effective bind and paths")
    args = parser.parse_args(argv)
    try:
        report = preflight()
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"V02 preflight failed: {exc}", file=sys.stderr)
        return 2
    if args.config:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("V02 preflight OK (no app imports, sockets, or services started).")
        print(f"Project: {report['project_root']}")
        print(f"Python:  {report['python']}")
        print(f"Bind:    {report['bind_host']}:{report['port']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
