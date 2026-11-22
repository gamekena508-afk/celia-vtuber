#!/usr/bin/env python3
"""
MCP Server - System Automation
Automatiza tarefas do sistema (Windows/Linux)
Versão especial para Célia VTuber
"""

import asyncio
import json
import sys
import os
import shutil
import subprocess
import tempfile
import webbrowser
import platform
from pathlib import Path
from datetime import datetime, timedelta, timezone as fixed_timezone
from typing import Optional
from zoneinfo import ZoneInfo

from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

server = Server("system-automation-celia")

# Detectar sistema operacional
IS_WINDOWS = platform.system() == 'Windows'

# Configurações de segurança
if IS_WINDOWS:
    ALLOWED_COMMANDS = [
        'dir', 'type', 'cd', 'date', 'time', 'echo', 'mkdir', 'copy',
        'move', 'del', 'find', 'findstr', 'whoami', 'tasklist', 'taskkill',
        'where', 'curl', 'powershell', 'cmd', 'start'
    ]
    BLOCKED_PATTERNS = [
        'format ',
        'del /f /s /q c:\\',
        'rd /s /q c:\\',
        'rmdir /s /q c:\\',
        'deltree',
        ':(){:|:&};:',  # fork bomb
    ]
else:
    ALLOWED_COMMANDS = [
        'ls', 'cat', 'pwd', 'date', 'echo', 'mkdir', 'touch',
        'cp', 'mv', 'rm', 'find', 'grep', 'which', 'whoami',
        'df', 'du', 'free', 'ps', 'top', 'kill', 'wget', 'curl'
    ]
    BLOCKED_PATTERNS = [
        'rm -rf /',
        'dd if=',
        '> /dev/',
        'mkfs',
        'fdisk',
        'format',
        ':(){ :|:& };:',  # fork bomb
    ]


def is_safe_command(command: str) -> tuple[bool, str]:
    """Valida se comando é seguro para executar"""
    command_lower = command.lower().strip()

    # Verifica padrões bloqueados
    for pattern in BLOCKED_PATTERNS:
        if pattern in command_lower:
            return False, f"Comando bloqueado (padrão perigoso: {pattern})"

    # Extrai primeiro comando
    first_cmd = command_lower.split()[0] if command_lower.split() else ""

    # Verifica se está na whitelist
    if first_cmd not in ALLOWED_COMMANDS:
        return False, f"Comando '{first_cmd}' não está na lista permitida"

    return True, "Comando seguro"


FIXED_TIMEZONES = {
    "UTC": fixed_timezone.utc,
    "Etc/UTC": fixed_timezone.utc,
    "America/Sao_Paulo": fixed_timezone(timedelta(hours=-3), "America/Sao_Paulo"),
    "America/Sao Paulo": fixed_timezone(timedelta(hours=-3), "America/Sao_Paulo"),
    "Sao_Paulo": fixed_timezone(timedelta(hours=-3), "America/Sao_Paulo"),
    "Sao Paulo": fixed_timezone(timedelta(hours=-3), "America/Sao_Paulo"),
    "Europe/London": fixed_timezone.utc,
}


def load_timezone(timezone_name: str):
    """Carrega timezone IANA; no Windows sem tzdata usa fallbacks essenciais."""
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        fallback = FIXED_TIMEZONES.get(timezone_name)
        if fallback is not None:
            return fallback
        raise


def get_current_time(timezone: str = "America/Sao_Paulo") -> str:
    """Retorna data/hora atual em timezone específica (IANA)."""
    try:
        now = datetime.now(load_timezone(timezone))
    except Exception:
        return (
            f"[ERR] Timezone invalida: '{timezone}'. "
            "Use timezone IANA, ex: America/Sao_Paulo, UTC, Europe/London."
        )

    return (
        "{\n"
        f'  "timezone": "{timezone}",\n'
        f'  "datetime": "{now.isoformat()}",\n'
        f'  "day_of_week": "{now.strftime("%A")}",\n'
        f'  "is_dst": {str(bool(now.dst())).lower()}\n'
        "}"
    )


def convert_timezone(
    source_timezone: str,
    target_timezone: str,
    datetime_str: Optional[str] = None,
) -> str:
    """
    Converte horário entre timezones IANA.

    datetime_str aceita ISO 8601 (ex: 2026-04-13T15:30:00).
    Se vazio, usa o horário atual.
    """
    try:
        source_tz = load_timezone(source_timezone)
    except Exception:
        return f"[ERR] Timezone de origem invalida: '{source_timezone}'"

    try:
        target_tz = load_timezone(target_timezone)
    except Exception:
        return f"[ERR] Timezone de destino invalida: '{target_timezone}'"

    try:
        if datetime_str:
            parsed = datetime.fromisoformat(datetime_str)
        else:
            parsed = datetime.now()
    except Exception:
        return (
            "[ERR] datetime_str invalido. Use formato ISO 8601, "
            "ex: 2026-04-13T15:30:00"
        )

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=source_tz)
    else:
        parsed = parsed.astimezone(source_tz)

    converted = parsed.astimezone(target_tz)

    return (
        "{\n"
        f'  "source_timezone": "{source_timezone}",\n'
        f'  "target_timezone": "{target_timezone}",\n'
        f'  "source_datetime": "{parsed.isoformat()}",\n'
        f'  "converted_datetime": "{converted.isoformat()}"\n'
        "}"
    )


# ============================================================================
# FERRAMENTAS DO SISTEMA
# ============================================================================

# ============================================================================
# SERVIÇOS DO SISTEMA
# ============================================================================

import socket as _socket

_CELIA_ROOT = Path(__file__).parent.parent


def _external_script(env_name: str) -> Path | None:
    """Return an explicitly configured script from an optional sibling app."""
    configured = os.getenv(env_name)
    return Path(configured).expanduser() if configured else None


# Alice/Luna are separate projects, not children of Célia. Their locations must
# be explicitly supplied instead of inheriting the path of the machine where
# this MCP was first written.
_ALICE_BACKEND_SCRIPT = _external_script("CELIA_ALICE_BACKEND_SCRIPT")
_LUNA_DASHBOARD_SCRIPT = _external_script("CELIA_LUNA_DASHBOARD_SCRIPT")
_MINECRAFT_DIR = _CELIA_ROOT / "integrations" / "minecraft"
_MINECRAFT_BODY_DIR = _MINECRAFT_DIR / "mineflayer"
_MINECRAFT_ADAPTER_DIR = _MINECRAFT_DIR
_MINECRAFT_PYTHON = _CELIA_ROOT / ".venv" / "bin" / "python"


def _minecraft_body_pids() -> list[int]:
    """PIDs for the named Mineflayer body and adapter, never a generic Node match."""
    try:
        import psutil
    except ImportError:
        return []
    found = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        cmd = " ".join(proc.info.get("cmdline") or [])
        if "body_server.js" in cmd or "-m celia_minecraft" in cmd:
            try:
                if str(_MINECRAFT_DIR) in str(proc.cwd()):
                    found.append(proc.info["pid"])
            except Exception:  # noqa: BLE001 - process vanished / permission denied
                continue
    return found


def _run_minecraft_skill_posix(action: str, player: str | None = None) -> str:
    """Start or inspect the body; central MCP exclusively owns gameplay decisions."""
    if not _MINECRAFT_BODY_DIR.is_dir():
        return f"[ERR] Corpo Mineflayer não encontrado: {_MINECRAFT_BODY_DIR}"
    node = shutil.which("node")
    if not node:
        return "[ERR] Node.js nao esta no PATH — necessario para o bot Minecraft."

    if action in ("status",):
        pids = _minecraft_body_pids()
        return json.dumps({"ports": {"minecraft_body_8080": _porta_ativa(8080)}, "celia_spawned": bool(pids), "pids": pids})

    if action in ("stop", "parar", "desligar"):
        pids = _minecraft_body_pids()
        if not pids:
            return "[OFF] Corpo Mineflayer já estava parado"
        import signal
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception as exc:  # noqa: BLE001
                return f"[ERR] Falha ao parar PID {pid}: {exc}"
        return f"[OK] Corpo Mineflayer parado (PID {', '.join(map(str, pids))})"

    if action in ("start", "ligar", "iniciar"):
        if _minecraft_body_pids():
            return "[OK] Corpo Mineflayer já está rodando"
        if not _MINECRAFT_PYTHON.exists():
            return "[ERR] Ambiente Python oficial da Célia indisponível"
        if not (_MINECRAFT_BODY_DIR / "node_modules").is_dir():
            return "[ERR] Dependências Mineflayer não instaladas"
        if not _porta_ativa(25565):
            return "[ERR] Servidor Minecraft local não iniciado na porta 25565"
        python = _MINECRAFT_PYTHON
        log_path = Path(tempfile.gettempdir()) / "celia-minecraft-body.log"
        env = os.environ.copy()
        env["CELIA_WORKSPACE"] = str(_CELIA_ROOT)
        env["PYTHONPATH"] = str(_MINECRAFT_ADAPTER_DIR) + os.pathsep + env.get("PYTHONPATH", "")
        with open(log_path, "ab") as log:
            process = subprocess.Popen([str(python), str(_MINECRAFT_DIR / "start_body.py")], cwd=str(_CELIA_ROOT), env=env, stdout=log, stderr=log, start_new_session=True)
        return f"[STARTING] Corpo Mineflayer iniciado (PID {process.pid}; log: {log_path}); entrada no mundo ainda não confirmada"

    return f"[ERR] Acao desconhecida para Minecraft: {action}"


def _run_minecraft_skill(action: str, player: str | None = None) -> str:
    return _run_minecraft_skill_posix(action, player)


def _status_minecraft_bot() -> str:
    return _run_minecraft_skill("status")


def _send_minecraft_command(agent: str, player: str, command: str) -> str:
    return "[DENIED] Comandos livres não alcançam o corpo Minecraft. Use minecraft_acao tipada."


def _ativar_minecraft_autonomia_livre() -> str:
    return "[OK] Autonomia é decidida pelo runtime central da Célia, não pelo corpo."


def _ligar_minecraft_bot() -> str:
    return _run_minecraft_skill("start")


_SERVICOS = {
    "celia": {
        "desc": "Célia VTuber (WebSocket Server)",
        "porta": 12393,
        "start_cmd": ["python", "run_server.py"],
        "start_cwd": None,  # usa CELIA_ROOT
        "proc_name": "run_server",
    },
    "alice_backend": {
        "desc": "Backend da Alice (FastAPI)",
        "porta": 8010,
        "start_cmd": [sys.executable, str(_ALICE_BACKEND_SCRIPT)] if _ALICE_BACKEND_SCRIPT else [],
        "start_cwd": str(_ALICE_BACKEND_SCRIPT.parent) if _ALICE_BACKEND_SCRIPT else None,
        "required_path": _ALICE_BACKEND_SCRIPT,
        "required_env": "CELIA_ALICE_BACKEND_SCRIPT",
        "proc_name": "alice_agent_bridge",
    },
    "luna_dashboard": {
        "desc": "Dashboard Luna",
        "porta": 8765,
        "start_cmd": [sys.executable, str(_LUNA_DASHBOARD_SCRIPT)] if _LUNA_DASHBOARD_SCRIPT else [],
        "start_cwd": str(_LUNA_DASHBOARD_SCRIPT.parent) if _LUNA_DASHBOARD_SCRIPT else None,
        "required_path": _LUNA_DASHBOARD_SCRIPT,
        "required_env": "CELIA_LUNA_DASHBOARD_SCRIPT",
        "proc_name": "luna_app",
    },
    "ollama": {
        "desc": "Ollama (modelos locais)",
        "porta": 11434,
        "start_cmd": ["ollama", "serve"],
        "start_cwd": None,
        "proc_name": "ollama",
    },
    "minecraft": {
        "desc": "Corpo Mineflayer + adapter da Célia",
        "porta": 8080,
        "start_cmd": ["node", "src/body_server.js"],
        "start_cwd": str(_MINECRAFT_BODY_DIR),
        "proc_name": "body_server.js",
    },
}


def _porta_ativa(porta: int) -> bool:
    try:
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", porta)) == 0
    except Exception:
        return False


def _proc_rodando(nome: str) -> bool:
    try:
        import psutil
        for p in psutil.process_iter(["cmdline"]):
            try:
                cmdline = " ".join(p.info.get("cmdline") or [])
                if nome in cmdline:
                    return True
            except Exception:
                pass
        return False
    except ImportError:
        return False


def _status_servico(nome: str) -> str:
    if nome:
        nome = nome.lower().strip()
        if nome not in _SERVICOS:
            disponiveis = ", ".join(_SERVICOS.keys())
            return f"[ERR] Serviço '{nome}' desconhecido. Disponíveis: {disponiveis}"
        alvo = {nome: _SERVICOS[nome]}
    else:
        alvo = _SERVICOS
    linhas = []
    for n, svc in alvo.items():
        ativo = _porta_ativa(svc["porta"])
        required_path = svc.get("required_path")
        if required_path is None and svc.get("required_env"):
            estado = "N/A"
        else:
            estado = "ON" if ativo else "OFF"
        linhas.append(f"[{estado}] {n} — {svc['desc']} (porta {svc['porta']})")
    return "\n".join(linhas)


def _ligar_servico(nome: str) -> str:
    nome = nome.lower().strip()
    if nome not in _SERVICOS:
        disponiveis = ", ".join(_SERVICOS.keys())
        return f"[ERR] Serviço '{nome}' desconhecido. Disponíveis: {disponiveis}"
    if nome == "minecraft":
        return _ligar_minecraft_bot()
    svc = _SERVICOS[nome]
    if _porta_ativa(svc["porta"]):
        return f"[OK] {svc['desc']} já está rodando na porta {svc['porta']}"
    required_path = svc.get("required_path")
    if svc.get("required_env") and required_path is None:
        return (
            f"[INDISPONÍVEL] {svc['desc']} não foi configurado. "
            f"Defina {svc['required_env']} com o caminho do script."
        )
    if required_path is not None and not required_path.is_file():
        return f"[ERR] Script do serviço não existe: {required_path}"
    cwd = svc["start_cwd"] or str(_CELIA_ROOT)
    if not Path(cwd).is_dir():
        return f"[ERR] Diretório do serviço não existe: {cwd}"
    env = os.environ.copy()
    env.update(svc.get("start_env", {}))
    # On POSIX, detach into a new session so the service outlives this MCP server.
    spawn = (
        {"creationflags": subprocess.CREATE_NEW_CONSOLE}
        if IS_WINDOWS and hasattr(subprocess, "CREATE_NEW_CONSOLE")
        else {"start_new_session": True}
    )
    try:
        subprocess.Popen(svc["start_cmd"], cwd=cwd, env=env, **spawn)
        return f"[OK] {svc['desc']} iniciado (porta {svc['porta']})"
    except Exception as e:
        return f"[ERR] Falha ao iniciar {nome}: {e}"


def _desligar_servico(nome: str) -> str:
    nome = nome.lower().strip()
    if nome not in _SERVICOS:
        disponiveis = ", ".join(_SERVICOS.keys())
        return f"[ERR] Serviço '{nome}' desconhecido. Disponíveis: {disponiveis}"
    svc = _SERVICOS[nome]
    proc_name = svc["proc_name"]
    try:
        import psutil
        mortos = 0
        for p in psutil.process_iter(["cmdline", "pid"]):
            try:
                cmdline = " ".join(p.info.get("cmdline") or [])
                if proc_name in cmdline:
                    p.terminate()
                    mortos += 1
            except Exception:
                pass
        if mortos:
            return f"[OK] {svc['desc']} encerrado ({mortos} processo(s))"
        return f"[WRN] {svc['desc']} não estava rodando"
    except ImportError:
        return "[ERR] psutil não instalado. Execute: pip install psutil"
    except Exception as e:
        return f"[ERR] Falha ao desligar {nome}: {e}"


# Candidates are tried in order through PATH, so Flatpak/Nix/custom installs
# remain usable without embedding platform-specific filesystem locations.
_BROWSER_PATHS = {
    "chrome": [
        "google-chrome-stable", "google-chrome", "chromium", "chromium-browser",
    ],
    "firefox": [
        "firefox",
    ],
    "edge": [
        "microsoft-edge-stable", "microsoft-edge",
    ],
}


def open_browser(url: Optional[str] = None, navegador: str = "padrao") -> str:
    """Abre navegador (Windows/Linux). navegador: chrome, firefox, edge, padrao"""
    target_url = url or "https://www.google.com"
    nav = navegador.lower().strip()
    try:
        if nav in _BROWSER_PATHS:
            for candidate in _BROWSER_PATHS[nav]:
                # Bare names resolve through PATH; absolute paths are checked on disk.
                resolved = shutil.which(candidate) if "/" not in candidate and "\\" not in candidate else (
                    candidate if Path(candidate).exists() else None
                )
                if resolved:
                    subprocess.Popen([resolved, target_url], start_new_session=not IS_WINDOWS)
                    return f"[OK] {nav} aberto com: {target_url}"
            # fallback se não encontrou o executável
            webbrowser.open(target_url)
            return f"[OK] Navegador padrão (fallback) aberto com: {target_url}"
        else:
            webbrowser.open(target_url)
            return f"[OK] Navegador padrão aberto com: {target_url}"
    except Exception as e:
        return f"[ERR] Erro ao abrir navegador: {e}"


def open_folder(path: Optional[str] = None) -> str:
    """Abre pasta no explorador de arquivos (Windows/Linux)"""
    try:
        if not path:
            path = str(Path.home())

        path_obj = Path(path).resolve()

        if not path_obj.exists():
            return f"[ERR] Pasta nao existe: {path_obj}"

        if IS_WINDOWS:
            os.startfile(str(path_obj))
        else:
            subprocess.Popen(['xdg-open', str(path_obj)])

        return f"[OK] Pasta aberta: {path_obj}"
    except Exception as e:
        return f"[ERR] Erro ao abrir pasta: {e}"


def create_file(filepath: str, content: str = "") -> str:
    """Cria arquivo de texto (Windows/Linux)"""
    try:
        filepath_obj = Path(filepath).resolve()

        # Cria diretórios se necessário
        filepath_obj.parent.mkdir(parents=True, exist_ok=True)

        # Escreve arquivo
        filepath_obj.write_text(content, encoding='utf-8')

        return f"[OK] Arquivo criado: {filepath_obj}"
    except Exception as e:
        return f"[ERR] Erro ao criar arquivo: {e}"


def execute_command(command: str) -> str:
    """Executa comando shell seguro (Windows/Linux)"""
    try:
        # Valida segurança
        is_safe, msg = is_safe_command(command)
        if not is_safe:
            return f"[DENIED] {msg}"

        # Executa comando
        if IS_WINDOWS:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10,
                encoding='cp850'  # Windows console encoding
            )
        else:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10
            )

        output = result.stdout + result.stderr
        return f"[CMD] Resultado:\n{output}" if output else "[OK] Comando executado (sem saida)"

    except subprocess.TimeoutExpired:
        return "[TIME] Timeout: comando demorou mais de 10 segundos"
    except Exception as e:
        return f"[ERR] Erro ao executar comando: {e}"


def run_python(filepath: str, args: str = "") -> str:
    """Executa arquivo Python diretamente em background
    
    Args:
        filepath: Caminho do arquivo Python (ex: "mcp/vision_mcp_server.py")
        args: Argumentos opcionais para o script
    """
    try:
        filepath_obj = Path(filepath).resolve()
        
        if not filepath_obj.exists():
            return f"[ERR] Arquivo nao encontrado: {filepath_obj}"
        
        if not filepath_obj.suffix == '.py':
            return f"[ERR] Arquivo nao e Python: {filepath_obj}"
        
        # Comando para rodar Python
        if args:
            cmd = f'python "{filepath_obj}" {args}'
        else:
            cmd = f'python "{filepath_obj}"'
        
        # Roda em background (não bloqueia)
        if IS_WINDOWS:
            # Windows: usar CREATE_NEW_CONSOLE para abrir novo terminal visível
            subprocess.Popen(
                cmd,
                shell=True,
                creationflags=subprocess.CREATE_NEW_CONSOLE if hasattr(subprocess, 'CREATE_NEW_CONSOLE') else 0
            )
        else:
            # Linux: usar nohup para background
            subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        return f"[OK] Script Python iniciado: {filepath_obj.name}"
    
    except Exception as e:
        return f"[ERR] Erro ao executar Python: {e}"


def open_terminal(command: Optional[str] = None) -> str:
    """Abre novo terminal (Windows/Linux)

    Args:
        command: Comando opcional para executar no terminal
                 Ex: "python vision_mcp_server.py"
    """
    try:
        if IS_WINDOWS:
            # Tenta Windows Terminal primeiro, fallback para CMD
            try:
                if command:
                    # Abre Windows Terminal e executa comando
                    subprocess.Popen(['wt', '-p', 'Command Prompt', 'cmd', '/k', command])
                else:
                    subprocess.Popen(['wt'])
            except FileNotFoundError:
                # Fallback para CMD normal
                if command:
                    subprocess.Popen(['start', 'cmd', '/k', command], shell=True)
                else:
                    subprocess.Popen(['start', 'cmd'], shell=True)

            if command:
                return f"[OK] Terminal aberto executando: {command}"
            else:
                return "[OK] Terminal aberto"
        else:
            # Linux: gnome-terminal
            if command:
                subprocess.Popen(['gnome-terminal', '--', 'bash', '-c', f'{command}; exec bash'])
                return f"[OK] Terminal aberto executando: {command}"
            else:
                subprocess.Popen(['gnome-terminal'])
                return "[OK] Terminal aberto"

    except Exception as e:
        return f"[ERR] Erro ao abrir terminal: {e}"


def _desktop_session_env() -> dict[str, str]:
    """Recover GUI session variables when Célia was launched from a headless shell."""
    env = os.environ.copy()
    if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"):
        return env
    wanted = ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "XAUTHORITY")
    uid = os.getuid() if hasattr(os, "getuid") else None
    preferred = []
    fallback = []
    proc_root = Path("/proc")
    try:
        for proc in proc_root.iterdir():
            if not proc.name.isdigit():
                continue
            try:
                if uid is not None and proc.stat().st_uid != uid:
                    continue
                raw = (proc / "environ").read_bytes().split(b"\0")
                values = {}
                for item in raw:
                    if b"=" not in item:
                        continue
                    k, v = item.split(b"=", 1)
                    key = k.decode(errors="ignore")
                    if key in wanted:
                        values[key] = v.decode(errors="ignore")
                if not (values.get("DISPLAY") or values.get("WAYLAND_DISPLAY")):
                    continue
                cmd = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore").lower()
                target = preferred if any(x in cmd for x in ("plasmashell", "xwayland", "gnome-shell")) else fallback
                target.append(values)
            except (OSError, PermissionError):
                continue
    except OSError:
        return env
    candidates = preferred + fallback
    if candidates:
        for key, value in candidates[0].items():
            if value:
                env[key] = value
    return env


def take_screenshot(filename: Optional[str] = None) -> str:
    """Tira screenshot (Windows/Linux), including KDE/Wayland sessions."""
    try:
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"screenshot_{timestamp}.png"
        elif "." not in filename:
            filename = f"{filename}.png"

        filepath = Path(filename).resolve()

        if IS_WINDOWS:
            # Windows: usar PIL ImageGrab
            try:
                from PIL import ImageGrab
                screenshot = ImageGrab.grab()
                screenshot.save(str(filepath))
                return f"[OK] Screenshot salvo: {filepath}"
            except ImportError:
                return "[ERR] PIL nao instalado. Execute: pip install pillow"
        else:
            env = _desktop_session_env()
            attempts = []
            if shutil.which("spectacle"):
                attempts.append(["spectacle", "-b", "-n", "-o", str(filepath)])
            if shutil.which("flameshot"):
                attempts.append(["flameshot", "full", "-p", str(filepath)])
            if shutil.which("scrot"):
                attempts.append(["scrot", str(filepath)])
            if shutil.which("import"):
                attempts.append(["import", "-window", "root", str(filepath)])
            errors = []
            for command in attempts:
                try:
                    subprocess.run(command, check=True, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15)
                    if filepath.exists() and filepath.stat().st_size > 0:
                        return f"[OK] Screenshot salvo: {filepath}"
                except Exception as exc:
                    errors.append(f"{command[0]}: {type(exc).__name__}")
            detail = ", ".join(errors) if errors else "nenhuma ferramenta de screenshot instalada"
            return f"[ERR] Não consegui tirar screenshot ({detail})"

    except Exception as e:
        return f"[ERR] Erro ao tirar screenshot: {e}"


# ============================================================================
# REGISTRO DE FERRAMENTAS MCP
# ============================================================================

@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    """Lista todas as ferramentas disponíveis"""
    return [
        Tool(
            name="get_current_time",
            description="Get current time in a specific timezone (IANA format)",
            inputSchema={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "Timezone IANA (ex: America/Sao_Paulo, UTC)"
                    }
                },
                "required": ["timezone"]
            }
        ),
        Tool(
            name="convert_timezone",
            description="Convert datetime from source timezone to target timezone (IANA)",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_timezone": {
                        "type": "string",
                        "description": "Timezone de origem (IANA)"
                    },
                    "target_timezone": {
                        "type": "string",
                        "description": "Timezone de destino (IANA)"
                    },
                    "datetime_str": {
                        "type": "string",
                        "description": "Data/hora opcional em ISO 8601 (ex: 2026-04-13T15:30:00)"
                    }
                },
                "required": ["source_timezone", "target_timezone"]
            }
        ),
        Tool(
            name="open_browser",
            description="Abre navegador web com URL. navegador: chrome, firefox, edge, padrao",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL para abrir (opcional, padrão: Google)"
                    },
                    "navegador": {
                        "type": "string",
                        "description": "Navegador: chrome, firefox, edge, padrao (padrão: padrao)"
                    }
                }
            }
        ),
        Tool(
            name="open_folder",
            description="Abre pasta no explorador de arquivos (Windows/Linux)",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Caminho da pasta (opcional, padrão: home)"
                    }
                }
            }
        ),
        Tool(
            name="create_file",
            description="Cria arquivo de texto (Windows/Linux)",
            inputSchema={
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Caminho do arquivo a criar"
                    },
                    "content": {
                        "type": "string",
                        "description": "Conteúdo do arquivo (opcional)"
                    }
                },
                "required": ["filepath"]
            }
        ),
        Tool(
            name="execute_command",
            description="Executa comando shell seguro (whitelist: dir, type, echo, etc.)",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Comando a executar (validado por whitelist)"
                    }
                },
                "required": ["command"]
            }
        ),
        Tool(
            name="run_python",
            description="Executa arquivo Python em background (ex: vision_mcp_server.py, test.py)",
            inputSchema={
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Caminho do arquivo Python (ex: 'mcp/vision_mcp_server.py' ou 'test.py')"
                    },
                    "args": {
                        "type": "string",
                        "description": "Argumentos opcionais para o script (opcional)"
                    }
                },
                "required": ["filepath"]
            }
        ),
        Tool(
            name="open_terminal",
            description="Abre novo terminal (Windows Terminal/CMD ou gnome-terminal). Use 'command' para executar algo (ex: python vision_mcp_server.py)",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Comando opcional para executar no terminal (ex: 'python vision_mcp_server.py')"
                    }
                }
            }
        ),
        Tool(
            name="take_screenshot",
            description="Tira screenshot da tela (Windows: PIL, Linux: scrot)",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Nome do arquivo (opcional, padrão: screenshot_YYYYMMDD_HHMMSS.png)"
                    }
                }
            }
        ),
        Tool(
            name="status_minecraft_bot",
            description="Verifica Minecraft LAN, MindServer 8080 e se os bots Celia/Codex estao vivos no jogo.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="ligar_minecraft_bot",
            description="Liga o corpo Minecraft da Celia/Codex pelo launcher oficial quando necessário.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="status_servico",
            description="Verifica o status de um serviço (ou todos se nome vazio). Serviços: celia, alice_backend, luna_dashboard, ollama, minecraft",
            inputSchema={
                "type": "object",
                "properties": {
                    "nome": {
                        "type": "string",
                        "description": "Nome do serviço (vazio = todos). Ex: celia, ollama, minecraft"
                    }
                }
            }
        ),
        Tool(
            name="ligar_servico",
            description="Liga um serviço do sistema. Serviços: celia, alice_backend, luna_dashboard, ollama, minecraft",
            inputSchema={
                "type": "object",
                "properties": {
                    "nome": {
                        "type": "string",
                        "description": "Nome do serviço a ligar. Ex: celia, ollama, minecraft"
                    }
                },
                "required": ["nome"]
            }
        ),
        Tool(
            name="desligar_servico",
            description="Desliga um serviço do sistema. Serviços: celia, alice_backend, luna_dashboard, ollama, minecraft",
            inputSchema={
                "type": "object",
                "properties": {
                    "nome": {
                        "type": "string",
                        "description": "Nome do serviço a desligar. Ex: celia, ollama, minecraft"
                    }
                },
                "required": ["nome"]
            }
        ),
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Processa chamadas de ferramentas"""

    try:
        if name == "get_current_time":
            timezone: str = arguments.get("timezone", "America/Sao_Paulo")
            result = get_current_time(timezone)

        elif name == "convert_timezone":
            source_timezone: Optional[str] = arguments.get("source_timezone")
            target_timezone: Optional[str] = arguments.get("target_timezone")
            datetime_str: Optional[str] = arguments.get("datetime_str")
            if not source_timezone or not target_timezone:
                result = "[ERR] Parametros 'source_timezone' e 'target_timezone' sao obrigatorios"
            else:
                result = convert_timezone(
                    source_timezone=source_timezone,
                    target_timezone=target_timezone,
                    datetime_str=datetime_str,
                )

        elif name == "open_browser":
            url: Optional[str] = arguments.get("url")
            navegador: str = arguments.get("navegador", "padrao")
            result = open_browser(url, navegador)

        elif name == "open_folder":
            path: Optional[str] = arguments.get("path")
            result = open_folder(path)

        elif name == "create_file":
            filepath: Optional[str] = arguments.get("filepath")
            content: str = arguments.get("content", "")
            if filepath is None:
                result = "[ERR] Parametro 'filepath' e obrigatorio"
            else:
                result = create_file(filepath, content)

        elif name == "execute_command":
            command: Optional[str] = arguments.get("command")
            if command is None:
                result = "[ERR] Parametro 'command' e obrigatorio"
            else:
                result = execute_command(command)

        elif name == "run_python":
            filepath: Optional[str] = arguments.get("filepath")
            args: str = arguments.get("args", "")
            if filepath is None:
                result = "[ERR] Parametro 'filepath' e obrigatorio"
            else:
                result = run_python(filepath, args)

        elif name == "open_terminal":
            command: Optional[str] = arguments.get("command")
            result = open_terminal(command)

        elif name == "take_screenshot":
            filename: Optional[str] = arguments.get("filename")
            result = take_screenshot(filename)

        elif name == "status_minecraft_bot":
            result = _status_minecraft_bot()

        elif name == "ligar_minecraft_bot":
            result = _ligar_minecraft_bot()

        elif name == "status_servico":
            nome: str = arguments.get("nome", "")
            result = _status_servico(nome)

        elif name == "ligar_servico":
            nome: Optional[str] = arguments.get("nome")
            if nome is None:
                result = "[ERR] Parametro 'nome' e obrigatorio"
            else:
                result = _ligar_servico(nome)

        elif name == "desligar_servico":
            nome: Optional[str] = arguments.get("nome")
            if nome is None:
                result = "[ERR] Parametro 'nome' e obrigatorio"
            else:
                result = _desligar_servico(nome)

        else:
            result = f"[ERR] Ferramenta desconhecida: {name}"

        return [TextContent(type="text", text=result)]

    except Exception as e:
        error_msg = f"[ERR] Erro ao executar {name}: {e}"
        return [TextContent(type="text", text=error_msg)]


async def main():
    """Inicia servidor MCP"""
    print("[BOT] System Automation MCP Server", file=sys.stderr)
    print(f"   OS: {'Windows' if IS_WINDOWS else 'Linux'}", file=sys.stderr)
    print("", file=sys.stderr)
    print("[TOOLS] Ferramentas disponiveis:", file=sys.stderr)
    print("   [TIME] get_current_time - Hora atual por timezone", file=sys.stderr)
    print("   [WORLD] convert_timezone - Converte horario entre timezones", file=sys.stderr)
    print("   [WEB] open_browser - Abre navegador", file=sys.stderr)
    print("   [FOLDER] open_folder - Abre pasta", file=sys.stderr)
    print("   [FILE] create_file - Cria arquivo", file=sys.stderr)
    print("   [CONF] execute_command - Executa comando seguro", file=sys.stderr)
    print("   [PY] run_python - Executa arquivo Python (background)", file=sys.stderr)
    print("   [TERM] open_terminal - Abre terminal (pode executar comando)", file=sys.stderr)
    print("   [IMG] take_screenshot - Tira screenshot", file=sys.stderr)
    print("", file=sys.stderr)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="system-automation-celia",
                server_version="2.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                )
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
