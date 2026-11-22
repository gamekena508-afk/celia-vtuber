"""Validated, read-only registry for V02 bot manifests.

The YAML inventory never supplies a command line. Launch specifications are
trusted code configuration passed separately to ``BotRegistry.load``; a bot
must also be marked ``launchable`` and name a launcher identifier before that
allowlist entry can be used. Loading this module never starts a bot.
"""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, TypeVar

import yaml


T = TypeVar("T")


class RegistryError(ValueError):
    """Invalid or unsafe registry data."""


@dataclass(frozen=True)
class BotEntry:
    bot_id: str
    enabled: bool
    root: Path
    launcher: str | None
    launchable: bool
    manifest_path: Path
    health: str
    restart_policy: str
    startup_timeout_s: float
    readiness: str
    phase: str

    @property
    def manually_launchable(self) -> bool:
        # enabled controls autostart preference; launchable is the explicit
        # manual allow/deny gate. No automatic starts are ever performed here.
        return self.launchable and self.launcher is not None


def _safe_child(root: Path, relative: str, *, label: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or not rel.parts or any(part in {".", ".."} for part in rel.parts):
        raise RegistryError(f"{label} must be a safe relative path")
    candidate = root.joinpath(rel)
    cursor = root
    for part in rel.parts:
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except FileNotFoundError as exc:
            raise RegistryError(f"{label} does not exist") from exc
        attrs = getattr(info, "st_file_attributes", 0)
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(info.st_mode) or attrs & reparse:
            raise RegistryError(f"{label} cannot traverse symlinks or reparse points")
    resolved_root = root.resolve(strict=True)
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise RegistryError(f"{label} escapes the project root") from exc
    return resolved


class BotRegistry:
    """Loads bot identities and gates fixed, code-owned launch specs."""

    def __init__(self, project_root: Path, entries: Mapping[str, BotEntry], launch_allowlist: Mapping[str, T] | None = None):
        self.project_root = Path(project_root).resolve()
        self._entries = dict(entries)
        self._launch_allowlist = dict(launch_allowlist or {})

    @classmethod
    def load(
        cls,
        project_root: str | Path,
        *,
        config_path: str | Path | None = None,
        launch_allowlist: Mapping[str, T] | None = None,
    ) -> "BotRegistry":
        root = Path(project_root).resolve(strict=True)
        raw_path = Path(config_path) if config_path is not None else Path("config/bots.yaml")
        if raw_path.is_absolute():
            try:
                raw_path = raw_path.relative_to(root)
            except ValueError as exc:
                raise RegistryError("registry config must be inside the V02 project root") from exc
        path = _safe_child(root, raw_path.as_posix(), label="registry config")

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise RegistryError("registry YAML is invalid") from exc
        if not isinstance(data, dict):
            raise RegistryError("registry root must be a mapping")
        if data.get("schema_version") != 1 or data.get("default_enabled") is not False:
            raise RegistryError("registry schema or default policy is unsupported")
        if data.get("startup_policy") != "manual_only":
            raise RegistryError("automatic bot startup is not supported")
        bots = data.get("bots")
        # Um mapeamento vazio é válido: o repositório público não distribui nenhum bot.
        if not isinstance(bots, dict):
            raise RegistryError("registry bots must be a mapping")

        entries: dict[str, BotEntry] = {}
        allowlist = dict(launch_allowlist or {})
        for bot_id, value in bots.items():
            if not isinstance(bot_id, str) or not bot_id or not bot_id.replace("-", "").replace("_", "").isalnum():
                raise RegistryError("bot id is invalid")
            if not isinstance(value, dict):
                raise RegistryError(f"bot {bot_id} must be a mapping")
            if value.get("enabled") is not False:
                raise RegistryError("P5 registry entries must stay disabled by default")
            if value.get("health") != "heartbeat":
                raise RegistryError(f"bot {bot_id} has unsupported health policy")
            restart = value.get("restart")
            if restart not in {"on-failure", "never"}:
                raise RegistryError(f"bot {bot_id} has unsupported restart policy")
            timeout = value.get("startup_timeout_sec")
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.1 <= timeout <= 120:
                raise RegistryError(f"bot {bot_id} has invalid startup timeout")
            bot_root_rel = value.get("root")
            manifest_rel = value.get("manifest")
            if not isinstance(bot_root_rel, str) or not isinstance(manifest_rel, str):
                raise RegistryError(f"bot {bot_id} requires root and manifest paths")
            bot_root = _safe_child(root, bot_root_rel, label=f"bot {bot_id} root")
            manifest_path = _safe_child(root, manifest_rel, label=f"bot {bot_id} manifest")
            try:
                manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                raise RegistryError(f"bot {bot_id} manifest YAML is invalid") from exc
            if not isinstance(manifest, dict) or manifest.get("bot_id") != bot_id:
                raise RegistryError(f"bot {bot_id} manifest identity mismatch")
            if manifest.get("enabled") is not False:
                raise RegistryError("P5 bot manifests must stay disabled")
            launcher = value.get("launcher")
            if launcher is not None and (not isinstance(launcher, str) or not launcher.strip()):
                raise RegistryError(f"bot {bot_id} launcher must be a non-empty identifier or null")
            launchable = value.get("launchable")
            if not isinstance(launchable, bool):
                raise RegistryError(f"bot {bot_id} launchable must be boolean")
            launch_policy = manifest.get("launch_policy", "")
            if launchable and not (
                isinstance(launch_policy, str)
                and launch_policy.lower().startswith("manual;")
            ):
                raise RegistryError(f"bot {bot_id} manifest does not authorize manual launch")
            if launchable and (launcher is None or launcher not in allowlist):
                raise RegistryError(f"bot {bot_id} launchable requires a code allowlist launcher")
            if launcher is not None and launchable and launcher not in allowlist:
                raise RegistryError(f"bot {bot_id} launcher is not allowlisted")
            entries[bot_id] = BotEntry(
                bot_id=bot_id,
                enabled=False,
                root=bot_root,
                launcher=launcher,
                launchable=launchable,
                manifest_path=manifest_path,
                health="heartbeat",
                restart_policy=restart,
                startup_timeout_s=float(timeout),
                readiness=str(value.get("readiness", "unknown")),
                phase=str(value.get("phase", "unknown")),
            )
        return cls(root, entries, allowlist)

    def get(self, bot_id: str) -> BotEntry | None:
        return self._entries.get(bot_id)

    def list_entries(self) -> list[BotEntry]:
        return [self._entries[key] for key in sorted(self._entries)]

    def launch_spec(self, bot_id: str) -> T | None:
        entry = self.get(bot_id)
        if entry is None or not entry.manually_launchable:
            return None
        return self._launch_allowlist.get(entry.launcher or "")

    def autostart_ids(self) -> tuple[str, ...]:
        """P5 deliberately has no automatic launches."""
        return ()
