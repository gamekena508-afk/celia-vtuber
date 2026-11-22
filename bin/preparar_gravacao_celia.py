#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.getenv("CELIA_BRIDGE_URL", "http://127.0.0.1:12393").rstrip("/")
FRAME_MAX_AGE_MS = int(os.getenv("CELIA_VISION_FRAME_MAX_AGE_MS", "15000"))
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = {
    "discord": ROOT / "roteiros" / "discord_tutorial_gravacao.json",
    # "3d" continua apontando para o roteiro original — não foi substituído.
    "3d": ROOT / "roteiros" / "celia_3d_gravacao.json",
    # Versão explicativa passo a passo: mesmos 11 beats e mesmos ids do "3d",
    # mas com title, falas (lines) e, em cada beat, uma linha OLHAR dizendo o
    # que a Célia deve observar no Blender. O roteiro "3d" só tem text/required,
    # que o comportamento entende mas o teleprompter não renderiza como fala.
    "3d-passo": ROOT / "roteiros" / "celia_3d_passo_a_passo.json",
    "family-time": ROOT / "roteiros" / "family_time_gravacao.json",
}


def request(method: str, path: str, payload=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepara uma sessão limpa da Célia para gravação."
    )
    parser.add_argument("video", choices=sorted(SCRIPTS))
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Só mostra o estado atual; não altera perfil/roteiro.",
    )
    args = parser.parse_args()

    try:
        if not args.status_only:
            script_path = SCRIPTS[args.video]
            script = json.loads(script_path.read_text(encoding="utf-8"))

            request("POST", "/bridge/behavior/session/reset", {"clear_script": True})
            request("POST", "/bridge/behavior/script", {"script": script})
            request("POST", "/bridge/behavior/profile", {"profile": "gravacao"})

        state = request("GET", "/bridge/debug/state")
        frame = None
        try:
            frame = request("GET", "/bridge/vision/frame")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        print(f"ERRO: não consegui preparar a Célia: {exc}", file=sys.stderr)
        return 1

    behavior = state.get("behavior") or {}
    vision = state.get("vision") or {}
    cognitive = state.get("cognitive") or {}
    script = behavior.get("script") or {}
    metrics = behavior.get("metrics") or {}
    vision_metrics = vision.get("metrics") or {}

    print(f"vídeo: {args.video}")
    print(f"perfil: {behavior.get('profile')}")
    print(f"roteiro carregado: {script.get('loaded')}")
    print(f"próximos beats: {', '.join(script.get('upcoming_beats') or []) or 'nenhum'}")
    print(
        "fala acumulada: "
        f"Célia={((behavior.get('talk_ratio') or {}).get('celia', 0)):.3f} "
        f"Criador={((behavior.get('talk_ratio') or {}).get('human', 0)):.3f}"
    )
    print(
        "visão: "
        f"screen_events={metrics.get('screen_events', 0)} "
        f"requested={vision_metrics.get('requested', 0)} "
        f"completed={vision_metrics.get('completed', 0)} "
        f"stale={vision_metrics.get('stale_dropped', 0)} "
        f"context={cognitive.get('screen_context_present', False)}"
    )
    frame_age = frame.get("age_ms") if frame else None
    frame_fresh = bool(
        frame
        and isinstance(frame_age, int)
        and frame_age <= FRAME_MAX_AGE_MS
    )
    if frame_fresh:
        print(f"frame do Screen: ATIVO, age_ms={frame_age}")
    elif frame:
        print(f"frame do Screen: STALE, age_ms={frame_age} (teto={FRAME_MAX_AGE_MS})")
    else:
        print("frame do Screen: AUSENTE")

    if not frame_fresh:
        print("AVISO: o Screen não está alimentando a visão agora. Ligue Screen e escolha a janela correta.")
    elif not cognitive.get("screen_context_present", False):
        print("AVISO: o frame está chegando, mas ainda não há resumo visual recente. Faça uma mudança visível curta.")
    else:
        print("OK: frame ativo e contexto visual recente estão presentes.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
