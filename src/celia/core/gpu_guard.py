"""Bounded NVIDIA GPU preflight helpers for local-brain experiments only.

This module is intentionally not imported by production routing. It exists for
manual startup scripts and benchmark probes so a missing/crowded/hot GPU fails
before a model server is started.
"""
from __future__ import annotations

import argparse
import subprocess
from typing import Any


def gpu_status() -> dict[str, int]:
    out = subprocess.check_output(
        [
            "nvidia-smi",
            "--query-gpu=memory.used,memory.total,temperature.gpu,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        text=True,
        timeout=5,
    )
    first = next((line for line in out.splitlines() if line.strip()), "")
    if not first:
        raise RuntimeError("nvidia-smi nao retornou GPU")
    parts = [part.strip() for part in first.split(",")]
    if len(parts) != 4:
        raise RuntimeError(f"saida inesperada do nvidia-smi: {first!r}")
    used, total, temp, util = (int(value) for value in parts)
    return {
        "used": used,
        "total": total,
        "free": total - used,
        "temp": temp,
        "util": util,
    }


def preflight(
    need_mb: int,
    *,
    margin_mb: int = 1500,
    max_temp: int = 78,
) -> dict[str, int]:
    status = gpu_status()
    if status["free"] < int(need_mb) + int(margin_mb):
        raise RuntimeError(
            "VRAM insuficiente: "
            f"livre={status['free']}MB, preciso={int(need_mb)}+{int(margin_mb)}MB"
        )
    if status["temp"] > int(max_temp):
        raise RuntimeError(
            f"GPU quente ({status['temp']}C > {int(max_temp)}C); nao iniciar modelo"
        )
    return status


def _main() -> int:
    parser = argparse.ArgumentParser(description="Preflight leve da GPU da Celia")
    parser.add_argument("need_mb", type=int)
    parser.add_argument("--margin-mb", type=int, default=1500)
    parser.add_argument("--max-temp", type=int, default=78)
    args = parser.parse_args()
    status: dict[str, Any] = preflight(
        args.need_mb,
        margin_mb=args.margin_mb,
        max_temp=args.max_temp,
    )
    print(
        "GPU_OK "
        f"used={status['used']}MB free={status['free']}MB "
        f"total={status['total']}MB temp={status['temp']}C util={status['util']}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
