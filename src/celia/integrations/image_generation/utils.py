from __future__ import annotations

import base64
import mimetypes
import os
import shutil
import subprocess
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "assets" / "generated-images"
TEMP_DIR = DEFAULT_OUTPUT_DIR / "temp"


def ensure_dirs() -> None:
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)


def timestamp_name(prefix: str, suffix: str = ".png") -> str:
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns() % 1_000_000:06d}{suffix}"


def file_to_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def save_data_url(data_url: str, output_dir: Path, prefix: str) -> Path:
    header, encoded = data_url.split(",", 1)
    mime = header.split(":", 1)[1].split(";", 1)[0] if ":" in header else "image/png"
    suffix = mimetypes.guess_extension(mime) or ".png"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / timestamp_name(prefix, suffix)
    path.write_bytes(base64.b64decode(encoded))
    return path


def capture_clipboard_image() -> tuple[Path | None, str | None]:
    """Captura imagem do clipboard no Linux/Wayland com fallback para Pillow."""
    ensure_dirs()
    wl_paste = shutil.which("wl-paste")
    wl_env = os.environ.copy()
    wl_env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    wl_env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    if wl_paste:
        try:
            types = subprocess.check_output([wl_paste, "--list-types"], timeout=3, text=True, env=wl_env).splitlines()
            preferred = ["image/png", "image/webp", "image/jpeg"]
            mime = next((m for m in preferred if m in types), None)
            if mime:
                data = subprocess.check_output([wl_paste, "--type", mime], timeout=8, env=wl_env)
                suffix = mimetypes.guess_extension(mime) or ".png"
                path = TEMP_DIR / timestamp_name("clipboard", suffix)
                path.write_bytes(data)
                return path, None
        except Exception as exc:
            wl_error = str(exc)
        else:
            wl_error = "clipboard sem MIME de imagem"
    else:
        wl_error = "wl-paste indisponível"

    try:
        from PIL import Image, ImageGrab
        result = ImageGrab.grabclipboard()
        if isinstance(result, Image.Image):
            path = TEMP_DIR / timestamp_name("clipboard", ".png")
            result.save(path, "PNG")
            return path, None
        if isinstance(result, list):
            for item in result:
                src = Path(str(item))
                if src.exists() and src.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                    dst = TEMP_DIR / timestamp_name("clipboard", src.suffix.lower())
                    shutil.copy2(src, dst)
                    return dst, None
    except Exception as exc:
        return None, f"clipboard indisponível: {wl_error}; Pillow: {exc}"
    return None, f"clipboard sem imagem: {wl_error}"


def open_image(path: Path) -> tuple[bool, str]:
    try:
        if not path.exists():
            return False, f"arquivo não existe: {path}"
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys_platform() == "darwin":
            subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            # MCP subprocesses have a minimal environment without the desktop session.
            env = os.environ.copy()
            runtime = env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
            if Path(runtime, "wayland-0").exists():
                env.setdefault("WAYLAND_DISPLAY", "wayland-0")
            if Path(runtime, "bus").exists():
                env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus")
            if shutil.which("kde-open"):
                env.setdefault("XDG_CURRENT_DESKTOP", "KDE")
            completed = subprocess.run(
                ["xdg-open", str(path.resolve())], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=10, text=True,
            )
            if completed.returncode:
                return False, f"viewer exited {completed.returncode}"
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def sys_platform() -> str:
    import sys
    return sys.platform


def looks_like_clipboard_edit(text: str) -> bool:
    p = (text or "").lower()
    return any(x in p for x in ("clipboard", "área de transferência", "area de transferencia", "essa imagem", "esta imagem"))
