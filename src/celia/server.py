"""
Célia Server
========================
This module contains the WebSocket server for Célia, which handles
the WebSocket connections, serves static files, and manages the web tool.
It uses FastAPI for the server and Starlette for static file serving.
"""

import contextlib
import logging
import os
from .core.raizes import CACHE, LOGS_APP, RAIZ, garantir
import shutil

from fastapi import FastAPI, Request, WebSocket
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.staticfiles import StaticFiles as StarletteStaticFiles
from loguru import logger

from .routes import init_client_ws_route, init_webtool_routes, init_proxy_route
from .bridge_routes import init_bridge_routes
from .possession_routes import init_possession_routes
from .audio_lab_routes import init_audio_lab_routes
from .games_routes import init_games_routes
from .observability_routes import init_observability_routes
from .roteiro_routes import init_roteiro_routes
from .mobile_routes import init_mobile_routes, LanMobileBoundary
from .live_output_routes import init_live_output_routes
from .live_mode import gate as live_gate
from .live_mode.routes import init_live_routes
from .service_context import ServiceContext
from .config_manager.utils import Config
from .celia_singleton import CeliaSingleton
from .bridge.registry import BotRegistry
from .bridge.bot_admin_routes import init_bot_admin_routes
from .core.bot_process_manager import BotLaunchSpec, BotProcessManager
from .core.obs_subtitles import obs_subtitles
from .core.live_chat_watcher import LiveChatWatcher
from .core.voice_listener import VoiceListener
from .core.continuous_behavior import continuous_behavior


class OfflineOperationGuard:
    """Keep the real UI available while refusing operational requests offline."""

    BLOCKED_PREFIXES = (
        "/asr", "/tts", "/audio", "/audio-lab", "/bridge", "/games",
        "/possession", "/live", "/live-output", "/react", "/cache",
        "/mcp", "/image-generation", "/obs_subtitles",
    )
    BODY = b'{"status":"offline","detail":"This operation is disabled in offline UI mode."}'

    def __init__(self, app, *, enabled: bool):
        self.app = app
        self.enabled = enabled

    @classmethod
    def blocks_path(cls, path: str) -> bool:
        normalized = path.rstrip("/").lower() or "/"
        if normalized == "/bridge/v2/bots" or normalized.startswith("/bridge/v2/bots/"):
            return normalized.endswith("/heartbeat")
        return any(
            normalized == prefix or normalized.startswith(prefix + "/")
            for prefix in cls.BLOCKED_PREFIXES
        )

    async def __call__(self, scope, receive, send):
        if not self.enabled:
            await self.app(scope, receive, send)
            return

        scope_type = scope.get("type")
        if scope_type == "websocket":
            await send({
                "type": "websocket.http.response.start",
                "status": 503,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(self.BODY)).encode("ascii"))],
            })
            await send({"type": "websocket.http.response.body", "body": self.BODY})
            return

        if scope_type == "http":
            method = scope.get("method", "GET").upper()
            path = scope.get("path", "/")
            if method not in {"GET", "HEAD", "OPTIONS"} or self.blocks_path(path):
                headers = [(b"content-type", b"application/json"), (b"content-length", str(len(self.BODY)).encode("ascii"))]
                await send({"type": "http.response.start", "status": 503, "headers": headers})
                await send({"type": "http.response.body", "body": b"" if method == "HEAD" else self.BODY})
                return

        await self.app(scope, receive, send)


def _boot_enabled(name: str) -> bool:
    """Autostart remains off unless the V02 environment opts in explicitly."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _react_launch_allowlist(server_config) -> dict[str, BotLaunchSpec]:
    """Fixed React launcher, present only when its dedicated runtime and token exist."""
    bot_root = RAIZ / "bots" / "react"
    executable = bot_root / ".venv" / "Scripts" / "python.exe"
    token = os.environ.get("CELIA_BOT_HEARTBEAT_TOKEN_REACT", "").strip()
    if not executable.is_file() or len(token) < 32:
        return {}
    port = int(getattr(server_config.system_config, "port", 12393))
    spec = BotLaunchSpec(
        bot_id="react",
        cwd=bot_root.resolve(),
        executable=executable.resolve(),
        args=("worker_process.py",),
        startup_timeout_s=30.0,
        stop_timeout_s=5.0,
        restart_policy="on-failure",
        heartbeat_token=token,
        heartbeat_url=f"http://127.0.0.1:{port}/bridge/v2/bots/{{bot_id}}/heartbeat",
    )
    return {"react-worker-v02": spec}


def _platform_launch_allowlist(server_config, project_root=None) -> dict[str, BotLaunchSpec]:
    """Build fixed Twitch/YouTube launch specs only from complete explicit opt-ins."""
    root = (project_root or RAIZ).resolve()
    try:
        port = int(getattr(server_config.system_config, "port", 12393))
    except (AttributeError, TypeError, ValueError):
        return {}
    if not 1 <= port <= 65535:
        return {}

    required_by_bot = {
        "twitch": (
            "CELIA_TWITCH_ENABLED",
            (
                "TWITCH_CLIENT_ID",
                "TWITCH_USER_ACCESS_TOKEN",
                "TWITCH_BROADCASTER_ID",
                "TWITCH_USER_ID",
                "CELIA_BRIDGE_V2_TOKEN_TWITCH_LIVE_CHAT",
            ),
        ),
        "youtube": (
            "CELIA_YOUTUBE_ENABLED",
            (
                "YOUTUBE_API_KEY",
                "YOUTUBE_LIVE_CHAT_ID",
                "CELIA_BRIDGE_V2_TOKEN_YOUTUBE_LIVE_CHAT",
            ),
        ),
    }
    specs: dict[str, BotLaunchSpec] = {}
    for bot_id, (enable_name, credential_names) in required_by_bot.items():
        if os.environ.get(enable_name, "").strip().lower() not in {"1", "true", "yes", "on"}:
            continue
        heartbeat_token = os.environ.get(f"CELIA_BOT_HEARTBEAT_TOKEN_{bot_id.upper()}", "").strip()
        if len(heartbeat_token) < 32:
            continue
        credentials = {
            name: os.environ.get(name, "").strip()
            for name in credential_names
        }
        if any(not value for value in credentials.values()):
            continue

        bot_root = root / "bots" / bot_id
        executable_candidate = bot_root / ".venv" / "Scripts" / "python.exe"
        runner_candidate = bot_root / "run.py"
        try:
            bots_root = (root / "bots").resolve(strict=True)
            resolved_root = bot_root.resolve(strict=True)
            resolved_root.relative_to(bots_root)
            if resolved_root != bots_root / bot_id:
                continue
            executable = executable_candidate.resolve(strict=True)
            runner = runner_candidate.resolve(strict=True)
            executable.relative_to(resolved_root)
            runner.relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        if not resolved_root.is_dir() or not executable.is_file() or not runner.is_file():
            continue

        env = {
            enable_name: "true",
            "CELIA_BRIDGE_BASE_URL": f"http://127.0.0.1:{port}",
            **credentials,
        }
        spec = BotLaunchSpec(
            bot_id=bot_id,
            cwd=resolved_root,
            executable=executable,
            args=(str(runner), "--connect"),
            startup_timeout_s=30.0,
            stop_timeout_s=5.0,
            restart_policy="on-failure",
            heartbeat_token=heartbeat_token,
            heartbeat_url=f"http://127.0.0.1:{port}/bridge/v2/bots/{{bot_id}}/heartbeat",
            launch_env=env,
        )
        specs[f"{bot_id}-live-chat-v02"] = spec
    return specs


# ── Suprime 404s cosméticos do Live2D (/undefined/...) ─────────────────────────
class _SuppressUndefinedFilter(logging.Filter):
    """Filtra silenciosamente o GET /undefined/... que o frontend Live2D
    faz antes do WebSocket entregar o nome do modelo (race condition cosmética)."""

    def filter(self, record: logging.LogRecord) -> bool:  # True = deixa passar
        msg = record.getMessage()
        return "/undefined/" not in msg


# Aplica o filtro ao logger de acesso do uvicorn
logging.getLogger("uvicorn.access").addFilter(_SuppressUndefinedFilter())
# ──────────────────────────────────────────────────────────────────────────────


# Create a custom StaticFiles class that adds CORS headers
class CORSStaticFiles(StarletteStaticFiles):
    """
    Static files handler that adds CORS headers to all responses.
    Needed because Starlette StaticFiles might bypass standard middleware.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)

        # Add CORS headers to all responses
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"

        if path.endswith(".js"):
            response.headers["Content-Type"] = "application/javascript"

        return response


class AvatarStaticFiles(CORSStaticFiles):
    """
    Avatar files handler with security restrictions and CORS headers
    """

    async def get_response(self, path: str, scope):
        allowed_extensions = (".jpg", ".jpeg", ".png", ".gif", ".svg")
        if not any(path.lower().endswith(ext) for ext in allowed_extensions):
            return Response("Forbidden file type", status_code=403)
        response = await super().get_response(path, scope)
        return response


class WebSocketServer:
    """
    API server for Célia. This contains the websocket endpoint for the client, hosts the web tool, and serves static files.

    Creates and configures a FastAPI app, registers all routes
    (WebSocket, web tools, proxy) and mounts static assets with CORS.

    Args:
        config (Config): Application configuration containing system settings.
        default_context_cache (ServiceContext, optional):
            Pre‑initialized service context for sessions' service context to reference to.
            **If omitted, `initialize()` method needs to be called to load service context.**

    Notes:
        - If default_context_cache is omitted, call `await initialize()` to load service context cache.
        - Use `clean_cache()` to clear and recreate the local cache directory.
    """

    def __init__(self, config: Config, default_context_cache: ServiceContext = None, *, offline: bool = False):
        self.offline = bool(offline)
        self.live_chat_watcher: LiveChatWatcher | None = None
        self.voice_listener: VoiceListener | None = None
        # The React API/player may mount without starting the analyzer process.
        # P5's manager owns explicit manual process starts; there is no React autostart.
        self.react_enabled = not self.offline
        self.react_worker = None
        if self.react_enabled:
            try:
                from .react_routes import init_react_routes

                react_router, self.react_store, self.react_worker = init_react_routes()
            except ImportError as exc:
                # O recurso "react" depende de bots/react, que não faz parte do repositório público.
                logger.warning(f"Recurso react desativado (módulo ausente: {exc.name}).")
                self.react_enabled = False
                react_router = None
                self.react_store = None
        else:
            react_router = None
            self.react_store = None

        @contextlib.asynccontextmanager
        async def lifespan(_app: FastAPI):
            if not self.offline:
                # Restaura o teleprompter somente no boot operacional.
                from .core.roteiro_live import roteiro_live

                await roteiro_live.restore_from_behavior(continuous_behavior)
            if self.react_worker is not None:
                await self.react_worker.start()
            yield
            if self.react_worker is not None:
                await self.react_worker.stop()
            if self.live_chat_watcher is not None:
                await self.live_chat_watcher.stop()
            if self.voice_listener is not None:
                await self.voice_listener.stop()
            await self.bot_process_manager.shutdown()
            if not self.offline:
                singleton = CeliaSingleton.get_instance()
                agent = getattr(getattr(singleton, "context", None), "agent_engine", None)
                if agent and hasattr(agent, "flush_pending_memory_updates"):
                    await agent.flush_pending_memory_updates(timeout=10.0)

        self.app = FastAPI(title="Célia Server", lifespan=lifespan)
        self.app.state.offline = self.offline
        self.config = config
        # The profile matrix affects timing/cost only; tool authorization remains separate.
        continuous_behavior.configure_profiles(config.character_config.continuous_behavior)
        self.default_context_cache = (
            default_context_cache or ServiceContext()
        )  # Use provided context or initialize a new empty one waiting to be loaded
        self.bot_registry = BotRegistry.load(
            RAIZ,
            launch_allowlist={
                **_react_launch_allowlist(config),
                **_platform_launch_allowlist(config),
            },
        )
        self.bot_process_manager = BotProcessManager(
            self.bot_registry,
            log_dir=LOGS_APP / "bots",
        )
        self.app.state.bot_process_manager = self.bot_process_manager
        # It will be populated during the initialize method call

        # Add global CORS middleware
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        # LAN cannot bypass the mobile password through /client-ws or /bridge.
        # /roteiro retains its own existing LAN authentication.
        self.app.add_middleware(LanMobileBoundary)
        self.app.add_middleware(OfflineOperationGuard, enabled=self.offline)

        @self.app.get("/offline/status", include_in_schema=False)
        async def offline_status():
            return {"mode": "offline-ui" if self.offline else "normal", "operations_available": not self.offline}

        self.singleton = CeliaSingleton.get_instance()

        # Include routes, passing the singleton instance
        # The singleton will be populated during the initialize step
        self.app.include_router(
            init_client_ws_route(
                default_context_cache=self.default_context_cache,
                singleton=self.singleton,
            ),
        )
        self.app.include_router(
            init_webtool_routes(default_context_cache=self.default_context_cache),
        )

        # Include bridge routes for tool server functionality
        self.app.include_router(
            init_bridge_routes(default_context_cache=self.default_context_cache),
        )
        self.app.include_router(init_bot_admin_routes(self.bot_process_manager))
        self.app.include_router(init_audio_lab_routes(self.default_context_cache))
        self.app.include_router(init_games_routes())
        self.app.include_router(init_possession_routes())
        self.app.include_router(init_observability_routes(self.default_context_cache))
        # Roteiro Live reuses this process and the singleton broadcast bus.
        self.app.include_router(init_roteiro_routes())
        # Password-gated portrait receiver + PC-only WebRTC publisher.
        self.app.include_router(init_mobile_routes())
        self.app.include_router(init_live_output_routes())
        # Modo Live TikTok: modulo isolado, DESLIGADO por padrao; o leitor so sobe por /bridge/live/reader/start.
        self.app.include_router(init_live_routes(live_gate.get_manager()))
        if react_router is not None:
            self.app.include_router(react_router)
        # Cena e player na mesma origem: gestos normais liberam áudio nos iframes.
        from fastapi.responses import FileResponse
        from pathlib import Path
        scene_root = Path(__file__).resolve().parents[2]
        react_frontend = scene_root / "frontend" / "react-player"
        if self.react_enabled and react_frontend.is_dir():
            self.app.mount("/react", CORSStaticFiles(directory=str(react_frontend), html=True), name="react-player")
        @self.app.get('/live-scene')
        async def live_scene():
            return FileResponse(scene_root / 'tools/mode-lab/ui/index.html')
        for url, folder in (
            ('/lab', 'tools/mode-lab/ui'), ('/fixtures', 'tools/mode-lab/fixtures'),
            ('/models/celia', 'web/live2d-models/celia_vts'),
            ('/models/slime', 'web/live2d-models/tamaneko_vts'),
            ('/models/ursinho', 'web/live2d-models/ursinho_celia_vts')):
            # No repositório público algumas pastas não existem (modelos não publicados, laboratório):
            # montar só o que está presente evita falha na inicialização do servidor.
            if (scene_root / folder).is_dir():
                self.app.mount(url, CORSStaticFiles(directory=str(scene_root / folder)))
        if not self.offline and _boot_enabled("CELIA_AUTOSTART_OBS_SUBTITLES"):
            obs_subtitles.start_server()

        # Add a catch-all WebSocket route to prevent unmatched WS requests 
        # from hitting StaticFiles mounts (which causes AssertionError)
        @self.app.websocket("/{path_name:path}")
        async def catch_all_websocket(websocket: WebSocket, path_name: str):
            logger.warning(f"Unmatched WebSocket request to: /{path_name}")
            await websocket.close(code=1000)

        # Initialize and include proxy routes if proxy is enabled
        system_config = config.system_config
        if hasattr(system_config, "enable_proxy") and system_config.enable_proxy:
            # Construct the server URL for the proxy
            host = system_config.host
            port = system_config.port
            server_url = f"ws://{host}:{port}/client-ws"
            self.app.include_router(
                init_proxy_route(server_url=server_url),
            )

        # Mount cache directory first (to ensure audio file access)
        garantir()
        self.app.mount(
            "/cache",
            CORSStaticFiles(directory=str(CACHE)),
            name="cache",
        )

        # Mount static files with CORS-enabled handlers
        if Path("web/live2d-models").is_dir():  # pastas ausentes (ex.: partes não publicadas) são ignoradas
            self.app.mount(
                "/live2d-models",
                CORSStaticFiles(directory="web/live2d-models"),
                name="live2d-models",
            )
        if Path("web/backgrounds").is_dir():  # pastas ausentes (ex.: partes não publicadas) são ignoradas
            self.app.mount(
                "/bg",
                CORSStaticFiles(directory="web/backgrounds"),
                name="backgrounds",
            )
        if Path("web/avatars").is_dir():  # pastas ausentes (ex.: partes não publicadas) são ignoradas
            self.app.mount(
                "/avatars",
                AvatarStaticFiles(directory="web/avatars"),
                name="avatars",
            )
        if Path("web/assets").is_dir():  # pastas ausentes (ex.: partes não publicadas) são ignoradas
            self.app.mount(
                "/assets",
                CORSStaticFiles(directory="web/assets"),
                name="assets",
            )
        # Games are served by the existing Célia FastAPI process, never by a
        # second local server. Their adapters call the standard bridge routes.
        if Path("integrations/games").is_dir():  # pastas ausentes (ex.: partes não publicadas) são ignoradas
            self.app.mount(
                "/games",
                CORSStaticFiles(directory="integrations/games", html=True),
                name="games",
            )
        if Path("integrations/audio-lab").is_dir():  # pastas ausentes (ex.: partes não publicadas) são ignoradas
            self.app.mount(
                "/audio-lab",
                CORSStaticFiles(directory="integrations/audio-lab", html=True),
                name="audio-lab",
            )

        # Mount web tool directory separately from frontend
        if Path("web/web_tool").is_dir():  # pastas ausentes (ex.: partes não publicadas) são ignoradas
            self.app.mount(
                "/web-tool",
                CORSStaticFiles(directory="web/web_tool", html=True),
                name="web_tool",
            )

        # Mount main frontend last (as catch-all)
        self.app.mount(
            "/",
            CORSStaticFiles(directory="frontend", html=True),
            name="frontend",
        )

    async def initialize(self):
        """Asynchronously load the service context from config and initialize the singleton."""
        await self.default_context_cache.load_from_config(self.config, offline=self.offline)
        # Initialize the singleton with the loaded context
        if self.offline:
            self.default_context_cache.client_uid = "singleton"
            self.default_context_cache.send_text = self.singleton._broadcast_str
            self.singleton.context = self.default_context_cache
            logger.info("Offline UI initialized; operational services remain stopped.")
            return
        await self.singleton.initialize(self.default_context_cache)
        logger.info("CeliaSingleton initialized and ready.")
        if _boot_enabled("CELIA_AUTOSTART_LIVE_CHAT"):
            self.live_chat_watcher = LiveChatWatcher(self.singleton)
            self.live_chat_watcher.start()
        else:
            logger.info("Live chat fica desligado no boot; habilite CELIA_AUTOSTART_LIVE_CHAT=1 para opt-in.")
        # Um microfone -> uma captura. O proprietario da fonte fisica principal e
        # o navegador/frontend. O VoiceListener continua apenas pelas hotkeys
        # locais (ctrl+m, ctrl+p), que sao responsabilidade independente.
        if _boot_enabled("CELIA_AUTOSTART_VOICE_LISTENER"):
            self.voice_listener = VoiceListener(self.singleton)
            self.voice_listener.start()
        else:
            logger.info("VoiceListener fica desligado no boot; habilite CELIA_AUTOSTART_VOICE_LISTENER=1 para opt-in.")
        logger.info("Microfone: captura Python não inicia automaticamente; navegador/frontend mantém o caminho existente.")

    @staticmethod
    def clean_cache():
        """Clean the cache directory by removing its contents. Robust against locks on Windows."""
        cache_dir = "cache"
        if os.path.exists(cache_dir):
            try:
                # Tenta remover o diretorio inteiro
                shutil.rmtree(cache_dir, ignore_errors=False)
            except (PermissionError, OSError):
                # Se falhar (comum no Windows se arquivo aberto), tenta deletar arquivos individualmente
                for filename in os.listdir(cache_dir):
                    file_path = os.path.join(cache_dir, filename)
                    try:
                        if os.path.isfile(file_path) or os.path.islink(file_path):
                            os.unlink(file_path)
                        elif os.path.isdir(file_path):
                            shutil.rmtree(file_path, ignore_errors=True)
                    except Exception:
                        continue
                logger.debug("Limpeza parcial do cache concluida (alguns arquivos podem estar em uso).")
                return
            
            os.makedirs(cache_dir, exist_ok=True)
