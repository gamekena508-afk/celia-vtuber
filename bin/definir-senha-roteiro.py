#!/usr/bin/env python3
"""Set the server-side Roteiro Live password without exposing it in argv."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from getpass import getpass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "secrets" / ".env"
sys.path.insert(0, str(ROOT / "src"))

from celia.roteiro_auth import hash_password  # noqa: E402


def _replace_env_value(text: str, value: str) -> str:
    lines = text.splitlines(keepends=True)
    replacement = f"CELIA_ROTEIRO_PASSWORD_HASH={value}\n"
    for index, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == "CELIA_ROTEIRO_PASSWORD_HASH":
            lines[index] = replacement
            return "".join(lines)
    suffix = "" if not text or text.endswith("\n") else "\n"
    return text + suffix + replacement


def main() -> int:
    first = getpass("Nova senha do Roteiro Live: ")
    second = getpass("Confirme a senha: ")
    if not first or first != second:
        print("Senha vazia ou confirmações diferentes.", file=sys.stderr)
        return 2
    if not ENV_PATH.is_file():
        print(f"Arquivo ausente: {ENV_PATH}", file=sys.stderr)
        return 1

    updated = _replace_env_value(ENV_PATH.read_text(encoding="utf-8"), hash_password(first))
    fd, temporary = tempfile.mkstemp(prefix=".env.roteiro.", dir=ENV_PATH.parent)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, ENV_PATH)
        os.chmod(ENV_PATH, stat.S_IRUSR | stat.S_IWUSR)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"Senha atualizada em {ENV_PATH}; reinicie a Célia para carregar o novo hash.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
