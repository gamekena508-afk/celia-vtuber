from __future__ import annotations

import os
import sys
import time
import atexit
import asyncio
import argparse
import subprocess
import threading
import ipaddress
import tempfile
from pathlib import Path

# Add project root and src to sys.path for IDE support and local imports
project_root = Path(__file__).parent.absolute()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
src_root = project_root / "src"
if src_root.exists() and str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))

logger = None

def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}

def sanitize_broken_proxy_env() -> bool:
    """
    Disable known-broken local proxy values that block outbound API calls.
    Specific to 127.0.0.1:9 which is a common misconfiguration.
    """
    proxy_vars = [
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
        "GIT_HTTP_PROXY", "GIT_HTTPS_PROXY",
    ]

    broken_values = {
        "http://127.0.0.1:9", "https://127.0.0.1:9",
        "http://localhost:9", "https://localhost:9",
        "socks5://127.0.0.1:9", "socks5://localhost:9",
    }

    has_broken_proxy = False
    for var in proxy_vars:
        value = os.environ.get(var, "").strip().lower()
        if not value:
            continue
        if value in broken_values or "127.0.0.1:9" in value or "localhost:9" in value:
            has_broken_proxy = True
            break

    if not has_broken_proxy:
        return False

    for var in proxy_vars:
        if var in os.environ:
            logger.debug(f"Removendo proxy quebrado do ambiente: {var}={os.environ[var]}")
            os.environ.pop(var, None)

    # Ensure NO_PROXY includes localhost to avoid local loopback issues
    no_proxy_key = "NO_PROXY"
    no_proxy = os.environ.get(no_proxy_key, "")
    required_hosts = ["localhost", "127.0.0.1", "::1"]
    current_hosts = [h.strip() for h in no_proxy.split(",") if h.strip()]
    for host in required_hosts:
        if host not in current_hosts:
            current_hosts.append(host)
    os.environ[no_proxy_key] = ",".join(current_hosts)
    
    return True


def get_version() -> str:
    try:
        try:
            import tomllib as tomli
        except ImportError:
            import tomli
        with open(project_root / "pyproject.toml", "rb") as f:
            pyproject = tomli.load(f)
        return pyproject["project"]["version"]
    except Exception:
        return "unknown"


def init_logger(console_log_level: str = "INFO", llm_debug: bool = False, *, offline: bool = False) -> None:
    logger.remove()
    
    def console_filter(record):
        if not llm_debug:
            return True
        name = record["name"].lower()
        if any(x in name for x in ["asr", "tts", "audio", "live2d", "speech"]):
            return False
        return True

    logger.add(
        sys.stderr,
        level=console_log_level,
        filter=console_filter,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "{message}"
        ),
        colorize=True,
    )

    if offline:
        log_root = Path(os.environ["CELIA_STATE_DIR"]) / "application" / "logs"
        log_root.mkdir(parents=True, exist_ok=True)
        log_target = str(log_root / "offline_{time:YYYY-MM-DD}.log")
    else:
        log_target = "logs/debug_{time:YYYY-MM-DD}.log"
    logger.add(
        log_target,
        rotation="10 MB",
        retention="30 days",
        level="DEBUG",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
            "{level: <8} | "
            "{name}:{function}:{line} | "
            "{message} | "
            "{extra}"
        ),
        backtrace=True,
        diagnose=True,
    )


def check_frontend_submodule(lang=None):
    # lang era resolvido por upgrade_manager.lang; o parametro nunca foi usado
    # no corpo desta funcao e upgrade_codes/ foi para quarentena em 20260811.
    frontend_path = Path(__file__).parent / "frontend" / "index.html"
    if not frontend_path.exists():
        logger.warning("Frontend files missing.")
        if not _enabled("CELIA_INIT_FRONTEND_SUBMODULES"):
            logger.warning("Automatic submodule initialization is disabled; set CELIA_INIT_FRONTEND_SUBMODULES=1 to opt in.")
            return
        logger.warning("Attempting explicit submodule initialization...")
        try:
            subprocess.run(["git", "submodule", "update", "--init", "--recursive"], check=True)
            if not frontend_path.exists():
                logger.critical("Submodule initialization failed. Frontend is still missing.")
        except Exception as e:
            logger.critical(f"Git submodule initialization failed: {e}")


def _obs_subtitles_watchdog(project_root: Path) -> None:
    """Após 10s verifica se OBS Subtitles (8788) está respondendo.
    Se o servidor embedded não subiu, inicia o standalone como fallback."""
    time.sleep(10)
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:8788/obs_subtitles?theme=neuro", timeout=2)
        logger.info("OBS Subtitles já respondendo em 8788 — nenhuma ação necessária.")
        return
    except Exception:
        pass
    obs_script = project_root / "tools" / "scripts" / "run_obs_subtitles.py"
    if not obs_script.exists():
        logger.warning(f"OBS Subtitles standalone não encontrado: {obs_script}")
        return
    logger.info("OBS Subtitles não respondeu — iniciando standalone em 8788...")
    proc = subprocess.Popen(
        [sys.executable, str(obs_script)],
        cwd=str(project_root),
    )
    atexit.register(proc.terminate)
    logger.info(f"OBS Subtitles standalone iniciado (PID {proc.pid}).")


def parse_args():
    parser = argparse.ArgumentParser(description="Célia Server")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logs")
    parser.add_argument("--hf_mirror", action="store_true", help="Use Hugging Face mirror")
    parser.add_argument("--llm-debug", action="store_true", help="Enable LLM detailed logs")
    parser.add_argument("--check", action="store_true", help="Run side-effect-free Windows preflight")
    parser.add_argument("--config", action="store_true", help="Show effective bind and config paths only")
    parser.add_argument("--offline", action="store_true", help="Serve the existing UI locally without initializing engines or operational APIs")
    return parser.parse_args()


def safe_preflight(show_config: bool = False) -> int:
    """Check local V02 paths and print only sanitized bind/path metadata."""
    try:
        from launcher_config import preflight

        report = preflight(project_root)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"V02 preflight failed: {exc}", file=sys.stderr)
        return 2
    if show_config:
        import json

        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("V02 preflight OK (no app imports, sockets, or services started).")
        print(f"Project: {report['project_root']}")
        print(f"Python:  {report['python']}")
        print(f"Bind:    {report['bind_host']}:{report['port']}")
    return 0


def prepare_offline_state() -> Path:
    """Require an isolated temp state root before importing the application."""
    raw_state = os.environ.get("CELIA_STATE_DIR", "").strip()
    if not raw_state:
        raise ValueError("Offline mode requires CELIA_STATE_DIR to point to an isolated temporary directory.")
    state_root = Path(raw_state).expanduser().resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    try:
        state_root.relative_to(temp_root)
    except ValueError as exc:
        raise ValueError("Offline CELIA_STATE_DIR must be inside the system temporary directory.") from exc
    protected_root = (Path.home() / "Documents" / "Celia" / "Codigo" / "Celia").resolve()
    try:
        state_root.relative_to(protected_root)
    except ValueError:
        pass
    else:
        raise ValueError("Offline state cannot point into the original Celia project.")
    existing_var = os.environ.get("CELIA_VAR_DIR", "").strip()
    if existing_var and Path(existing_var).expanduser().resolve() != state_root:
        raise ValueError("Offline mode rejects a CELIA_VAR_DIR that differs from CELIA_STATE_DIR.")
    state_root.mkdir(parents=True, exist_ok=True)
    # The legacy alias takes precedence in raizes.py; align it before app imports.
    os.environ["CELIA_STATE_DIR"] = str(state_root)
    os.environ["CELIA_VAR_DIR"] = str(state_root)
    return state_root


def require_loopback_bind(host: str) -> None:
    try:
        is_loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise ValueError("Offline mode requires a loopback bind host (localhost, 127.0.0.1, or ::1).")


async def start_server(config: Config, console_log_level: str, *, offline: bool = False):
    """
    Main async entry point to ensure initialization and uvicorn run in the same loop.
    """
    server_config = config.system_config
    
    # Initialize WebSocket server
    server = WebSocketServer(config=config, offline=offline)
    
    logger.info("Initializing server components...")
    try:
        await server.initialize()
        logger.info("Server components initialized successfully.")
    except Exception as e:
        logger.error(f"Critical failure during server initialization ({type(e).__name__}); details redacted.")
        raise RuntimeError("Server initialization failed; details redacted") from None

    # Prepare Uvicorn configuration
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["default"]["fmt"] = "%(asctime)s - %(levelname)s - %(message)s"
    
    uv_config = uvicorn.Config(
        app=server.app,
        host=server_config.host,
        port=server_config.port,
        log_level=console_log_level.lower(),
        loop="asyncio",
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )
    uv_server = uvicorn.Server(uv_config)
    
    logger.info(f"Starting uvicorn server at http://{server_config.host}:{server_config.port}")
    try:
        await uv_server.serve()
    finally:
        logger.info("Server shutdown complete.")


def main():
    args = parse_args()
    if args.check and args.config:
        print("Use either --check or --config, not both.", file=sys.stderr)
        return 2
    if args.check or args.config:
        return safe_preflight(show_config=args.config)
    preflight_status = safe_preflight(show_config=False)
    if preflight_status:
        return preflight_status

    if args.offline:
        try:
            prepare_offline_state()
            os.environ["PYTHON_DOTENV_DISABLED"] = "1"
            from launcher_config import preflight

            require_loopback_bind(preflight(project_root)["bind_host"])
        except (ValueError, OSError, FileNotFoundError) as exc:
            print(f"Offline mode refused: {exc}", file=sys.stderr)
            return 2

    # Import application modules only for a real server startup. The preflight
    # and --help paths above remain free of server imports and service effects.
    global logger, uvicorn, WebSocketServer, start_track_process
    global Config, read_yaml, validate_config
    os.environ["HF_HOME"] = str(project_root / "models")
    os.environ["MODELSCOPE_CACHE"] = str(project_root / "models")
    try:
        import tomllib as tomli
    except ImportError:
        import tomli
    import uvicorn
    from loguru import logger
    from src.celia.server import WebSocketServer
    from src.celia.config_manager import Config, read_yaml, validate_config
    from src.celia.core.track_process import start_track_process

    console_log_level = "DEBUG" if args.verbose else "INFO"
    
    # 1. Initialize Logger
    init_logger(console_log_level, args.llm_debug, offline=args.offline)
    
    # 2. Sanitize Proxy
    proxy_sanitized = sanitize_broken_proxy_env()
    if proxy_sanitized:
        logger.warning("Ambiente de rede corrigido: proxy inválido (127.0.0.1:9) desativado.")
        
    logger.info(f"Célia v{get_version()} starting up...")

    # 3. Handle Hugging Face mirror
    if args.hf_mirror:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        logger.info("Using Hugging Face mirror: https://hf-mirror.com")

    # 4. Sync Config and Check Assets
    if not args.offline:
        check_frontend_submodule()
    # upgrade_manager.sync_user_config() removido: upgrade_codes/ esta em
    # _archive/_quarantine/20260811T125221Z e a copia arquivada ainda importa
    # src.Célia, nome de pacote extinto no commit 2323311.

    # 5. Register cleanup handler
    if not args.offline and _enabled("CELIA_CLEAR_CACHE_ON_EXIT"):
        atexit.register(WebSocketServer.clean_cache)

    # 6. Load Config
    try:
        config_data = read_yaml(str(project_root / "conf.yaml"))
        bind_host, bind_port = effective_bind(config_data)
        if args.offline:
            try:
                require_loopback_bind(bind_host)
            except ValueError:
                logger.error("Offline mode requires a loopback bind host (localhost, 127.0.0.1, or ::1).")
                return 2
        config_data.setdefault("system_config", {})["host"] = bind_host
        config_data["system_config"]["port"] = bind_port
        config: Config = validate_config(config_data)
    except Exception as e:
        logger.critical(f"Failed to load conf.yaml ({type(e).__name__}); details redacted.")
        sys.exit(1)

    # 7. OBS Subtitles watchdog (daemon — morre com o processo principal)
    if not args.offline and _enabled("CELIA_AUTOSTART_OBS_SUBTITLES"):
        threading.Thread(
            target=_obs_subtitles_watchdog,
            args=(project_root,),
            daemon=True,
            name="obs-subtitles-watchdog",
        ).start()

    # 8. Run Async Main
    track_manager = (
        start_track_process(project_root)
        if not args.offline and _enabled("CELIA_AUTOSTART_TRACKER")
        else None
    )
    try:
        asyncio.run(start_server(config, console_log_level, offline=args.offline))
    except KeyboardInterrupt:
        logger.info("Processo interrompido pelo usuário.")
    except Exception as e:
        logger.error(f"Processo encerrado por erro fatal ({type(e).__name__}); detalhes redigidos.")
        return 1
    finally:
        if track_manager is not None:
            track_manager.stop()


def effective_bind(config_data: dict) -> tuple[str, int]:
    """Use sanitized YAML bind defaults, overridden only by explicit env values."""
    from launcher_config import effective_bind as resolve_bind

    return resolve_bind(config_data)


if __name__ == "__main__":
    raise SystemExit(main())
