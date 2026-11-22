#!/usr/bin/env python3
"""celia-minecraft-mundos — persistent Minecraft worlds for the Célia series (plan V2).

Runs with the project interpreter:  .venv/bin/python bin/celia-minecraft-mundos.py <command> [options]

World commands act on the managed Minecraft server directly. Nothing here deletes a world or a backup, never touches
Célia/the Pet/the Jev, and anything that restarts the Minecraft server needs an explicit --confirm-restart.
Series commands (prepare/play/pause/episode-end/series-status) go through the authenticated Jev daemon, which owns the
physical body and therefore must acknowledge a stop before the world is frozen.

Exit codes: 0 ok · 1 failed · 2 usage error · 3 preflight refused (nothing changed) · 4 needs confirmation (nothing changed).
Global options (--json, --request-id, --server-dir ...) go BEFORE the command.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(os.getenv("CELIA_WORKSPACE", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / "integrations" / "minecraft"))

from celia_minecraft.series_client import SERIES_INTENTS, command as series_command, request as series_request  # noqa: E402
from celia_minecraft.server_control import ManagedJavaServer, RconOnlyServer, ServerControlError, ServerDescriptor, describe_running_server  # noqa: E402
from celia_minecraft.world_manager import (  # noqa: E402
    NeedsConfirmation, PreflightFailed, TempServerGenerator, WorldError, WorldManager,
)

DEFAULT_JAVA = Path(os.getenv("CELIA_MC_JAVA", Path.home() / ".local/share/PrismLauncher/java/java-runtime-delta/bin/java"))


def build_manager(args: argparse.Namespace) -> tuple[WorldManager, Path]:
    server_dir = Path(args.server_dir).resolve()
    state_dir = Path(args.state_dir).resolve()
    descriptor_path = state_dir / "managed-server.json"
    controller = None
    if descriptor_path.is_file():
        controller = ManagedJavaServer(ServerDescriptor.load(descriptor_path))
    elif (server_dir / "server.properties").is_file():
        controller = RconOnlyServer(server_dir / "server.properties")  # saves/backups/status work; stop/start are refused until adopt-server
    generator = TempServerGenerator(java=DEFAULT_JAVA, jar=Path(args.server_jar).resolve() if args.server_jar else server_dir / "server.jar", workdir=Path(args.worldgen_dir).resolve(),
                                    eula_source=server_dir / "eula.txt")
    manager = WorldManager(server_dir, registry_path=state_dir / "world_registry.json", backups_dir=state_dir / "world_backups",
                           audit_path=state_dir / "world-manager.jsonl", controller=controller, generator=generator,
                           margin_bytes=int(float(args.margin_gb) * 1024 ** 3))
    return manager, descriptor_path


def emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    for key, value in payload.items():
        print(f"{key}: {value if not isinstance(value, (dict, list)) else json.dumps(value, ensure_ascii=False, default=str)}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="celia-minecraft-mundos", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server-dir", default=os.getenv("CELIA_MC_SERVER_DIR", str(ROOT / "var/runtime/minecraft-test-1.21.10")))
    p.add_argument("--state-dir", default=os.getenv("CELIA_MC_STATE_DIR", str(ROOT / "var/minecraft")))
    p.add_argument("--server-jar", default=os.getenv("CELIA_MC_SERVER_JAR"), help="vanilla server.jar used ONLY by the isolated world generator (default: <server-dir>/server.jar)")
    p.add_argument("--worldgen-dir", default=os.getenv("CELIA_MC_WORLDGEN_DIR", str(ROOT / "var/runtime/minecraft-worldgen")))
    p.add_argument("--margin-gb", default=os.getenv("CELIA_MC_MARGIN_GB", "2"), help="free-space margin kept on top of any copy (default 2)")
    p.add_argument("--json", action="store_true", help="typed JSON output")
    p.add_argument("--request-id", default=None, help="idempotency key (a repeated id returns the first result)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list recognised worlds (legacy worlds appear as external, never migrated)")
    c = sub.add_parser("create", help="create a NEW series world (never overwrites)")
    c.add_argument("--name", required=True)
    c.add_argument("--series", required=True, help="series id like s01 (world id becomes celia-serie-s01)")
    c.add_argument("--seed", type=int, default=None)
    c.add_argument("--yes", action="store_true", help="explicit confirmation to generate the world")
    c.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("select", help="durable selection of the series world (does not restart anything)")
    s.add_argument("world_id")
    s.add_argument("--allow-external", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    w = sub.add_parser("switch", help="make the managed Minecraft server run another world (restarts ONLY that server)")
    w.add_argument("world_id")
    w.add_argument("--confirm-restart", action="store_true", help="explicit authorisation to stop and restart the Minecraft server")
    w.add_argument("--allow-external", action="store_true")
    w.add_argument("--dry-run", action="store_true")
    k = sub.add_parser("snapshot", help="save the running world (save-all flush) and record a verifiable checkpoint")
    k.add_argument("--label", default="checkpoint")
    k.add_argument("--with-backup", action="store_true")
    b = sub.add_parser("backup", help="verified backup of a world (cold if stopped, save-off+flush if running)")
    b.add_argument("world_id")
    b.add_argument("--label", default="manual")
    b.add_argument("--dry-run", action="store_true")
    sub.add_parser("status", help="selected/active world, server health, identity check, space, last backup")
    sub.add_parser("recover", help="settle an unfinished switch after a crash (never deletes, never starts a world on its own)")
    for name, text in (("prepare", "prepare the introduction of the series world (Célia stopped, day fixed, world frozen)"),
                       ("play", "start gameplay: unfreeze, daylight back, confirm time advances"),
                       ("pause", "stop the body to talk; the world keeps running (use --freeze-world to freeze it too)"),
                       ("resume", "leave the talk pause"), ("end-episode", "stop, save, checkpoint (+ backup unless --no-backup)"),
                       ("series-recover", "settle a BLOCKED series against the real server"), ("return", "bring Célia back next to the owner"),
                       ("stop", "emergency stop of the body (no world change)")):
        sp = sub.add_parser(name, help=text + " [through the Jev daemon]")
        sp.add_argument("--dry-run", action="store_true")
        if name == "prepare":
            sp.add_argument("--world-id", default=None)
            sp.add_argument("--confirm-restart", action="store_true", help="authorise restarting the Minecraft server if another world is active")
            sp.add_argument("--allow-day-only", action="store_true", help="if tick freeze is unsupported, freeze only the daylight (reported as degraded)")
        if name == "play":
            sp.add_argument("--goal", choices=["build", "progression"], default=None)
        if name == "pause":
            sp.add_argument("--freeze-world", action="store_true")
        if name == "end-episode":
            sp.add_argument("--no-backup", action="store_true")
    sub.add_parser("series-status", help="recording state, companion state and worlds, as the Jev daemon sees them")
    a = sub.add_parser("adopt-server", help="register the running Minecraft server (cwd+argv read from /proc) as the managed one")
    a.add_argument("--yes", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    manager, descriptor_path = build_manager(args)
    rid = args.request_id
    try:
        if args.cmd == "list":
            emit(manager.list_worlds(), args.json)
        elif args.cmd == "create":
            emit(manager.create(name=args.name, series=args.series, seed=args.seed, dry_run=args.dry_run, confirm=args.yes, request_id=rid), args.json)
        elif args.cmd == "select":
            emit(manager.select(args.world_id, allow_external=args.allow_external, dry_run=args.dry_run, request_id=rid), args.json)
        elif args.cmd == "switch":
            emit(manager.switch(args.world_id, confirm_restart=args.confirm_restart, allow_external=args.allow_external,
                                dry_run=args.dry_run, request_id=rid), args.json)
        elif args.cmd == "snapshot":
            emit(manager.checkpoint(label=args.label, with_backup=args.with_backup, request_id=rid), args.json)
        elif args.cmd == "backup":
            emit(manager.backup(args.world_id, label=args.label, dry_run=args.dry_run, request_id=rid), args.json)
        elif args.cmd == "status":
            emit(manager.status(), args.json)
        elif args.cmd == "recover":
            emit(manager.recover(), args.json)
        elif args.cmd == "series-status":
            payload = series_request("GET", "/series", None, workspace=ROOT, timeout=5.0)
            emit(payload, args.json)
            return 0 if payload.get("ok") else 1
        elif args.cmd in SERIES_INTENTS:
            params: dict = {}
            if args.cmd == "prepare":
                params = {k: v for k, v in (("world_id", args.world_id), ("allow_day_only", True if args.allow_day_only else None)) if v is not None}
            elif args.cmd == "play":
                params = {"goal": args.goal} if args.goal else {}
            elif args.cmd == "pause":
                params = {"freeze_world": bool(args.freeze_world)}
            elif args.cmd == "end-episode":
                params = {"with_backup": not args.no_backup}
            payload = series_command(args.cmd, params, workspace=ROOT, request_id=rid, confirmed=bool(getattr(args, "confirm_restart", False)), dry_run=args.dry_run)
            emit(payload, args.json)
            if payload.get("needs_confirmation"):
                return 4
            return 0 if payload.get("ok") or payload.get("status") == "validated_dry_run" else 1
        elif args.cmd == "adopt-server":
            found = describe_running_server(Path(args.server_dir) / "server.properties")
            if found is None:
                emit({"status": "failed", "error": "no running server found for this server.properties"}, args.json)
                return 1
            descriptor, server_pid = found
            payload = {"status": "validated_dry_run" if not args.yes else "ready", "descriptor": descriptor.as_json(), "path": str(descriptor_path)}
            if args.yes:
                descriptor_path.parent.mkdir(parents=True, exist_ok=True)
                descriptor_path.write_text(json.dumps(descriptor.as_json(), indent=2))
                os.chmod(descriptor_path, 0o600)
                descriptor.pid_file.write_text(str(server_pid))  # lets a later graceful stop confirm that exact process exited
            emit(payload, args.json)
    except NeedsConfirmation as exc:
        emit({"status": "needs_confirmation", "reason": str(exc), "plan": exc.plan, "request_id": rid or str(uuid.uuid4())[:8]}, args.json)
        return 4
    except PreflightFailed as exc:
        emit({"status": "preflight_failed", "code": exc.code, "error": str(exc)}, args.json)
        return 3
    except (WorldError, ServerControlError) as exc:
        emit({"status": "failed", "code": getattr(exc, "code", "error"), "error": str(exc)}, args.json)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
