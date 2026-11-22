"""LAN-only recording teleprompter routes on the official Célia server."""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from .core import roteiro_catalogo
from .core.continuous_behavior import continuous_behavior
from .core.roteiro_live import SESSION_LOG_DIR, roteiro_live
from .roteiro_auth import SESSION_COOKIE, roteiro_auth

ROTEIRO_FRONTEND = Path("frontend/roteiro-live")

# O seletor do celular opera SEMPRE neste perfil. `roteiro` é o perfil que
# recarrega o script canônico do disco e destrói a escolha do dono.
PERFIL_GRAVACAO = "gravacao"

_LOGIN_HTML = """<!doctype html>
<html lang="pt-BR">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
    <meta name="theme-color" content="#000000" />
    <title>Célia — Roteiro Live</title>
    <style>
      :root { color-scheme: dark; font-family: system-ui, sans-serif; }
      * { box-sizing: border-box; }
      body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #000; color: #fff; padding: 24px; }
      main { width: min(100%, 380px); }
      h1 { margin: 0 0 8px; font-size: clamp(2rem, 9vw, 3rem); letter-spacing: -.04em; }
      p { color: #a9a9a9; margin: 0 0 28px; }
      label { display: block; margin-bottom: 8px; font-size: .9rem; color: #d5d5d5; }
      input, button { width: 100%; min-height: 52px; border-radius: 10px; font: inherit; }
      input { border: 1px solid #444; background: #111; color: #fff; padding: 0 14px; }
      input:focus { outline: 2px solid #fff; outline-offset: 2px; }
      button { margin-top: 14px; border: 0; background: #fff; color: #000; font-weight: 700; cursor: pointer; }
      button:disabled { opacity: .6; cursor: wait; }
      #message { min-height: 1.4em; margin-top: 14px; color: #ff9d9d; }
    </style>
  </head>
  <body>
    <main>
      <h1>CÉLIA</h1>
      <p>Roteiro Live</p>
      <form id="login" novalidate>
        <label for="password">Senha</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required autofocus />
        <button id="submit" type="submit">ENTRAR</button>
        <div id="message" role="alert" aria-live="polite"></div>
      </form>
    </main>
    <script>
      (() => {
        const form = document.getElementById("login");
        const field = document.getElementById("password");
        const button = document.getElementById("submit");
        const message = document.getElementById("message");
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          if (!field.value) return;
          button.disabled = true;
          message.textContent = "";
          try {
            const response = await fetch("/roteiro/login", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              credentials: "same-origin",
              body: JSON.stringify({ password: field.value }),
            });
            if (response.ok) {
              window.location.replace("/roteiro/app");
              return;
            }
            message.textContent = response.status === 429
              ? "Tente novamente em alguns segundos."
              : "Senha incorreta ou acesso indisponível.";
            field.select();
          } catch (_) {
            message.textContent = "Não foi possível conectar à Célia.";
          } finally {
            button.disabled = false;
          }
        });
      })();
    </script>
  </body>
</html>
"""


def _is_loopback(request: Request) -> bool:
    host = (request.client.host if request.client else "").lower()
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def _require_loopback(request: Request) -> None:
    if not _is_loopback(request):
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="local_control_only")


def _client_ip(request: Request) -> str:
    return (request.client.host if request.client else "unknown").strip() or "unknown"


def _require_session(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if not roteiro_auth.valid_session(token):
        raise HTTPException(
            status_code=401,
            detail="roteiro_auth_required",
            headers={"Cache-Control": "no-store"},
        )


def _secure_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }


async def _login_password(request: Request) -> str:
    body = await request.body()
    if len(body) > 4096:
        return ""
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    try:
        if content_type == "application/json":
            payload = json.loads(body.decode("utf-8"))
            return str(payload.get("password") or "") if isinstance(payload, dict) else ""
        values = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        return str(values.get("password", [""])[0] or "")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return ""


def _catalogo_atual() -> dict:
    ativo = (roteiro_live.snapshot().get("script") or {}).get("id")
    return roteiro_catalogo.catalogo(ativo_id=ativo)


def _roteiro_por_id(script_id: str) -> dict | None:
    """Carrega do disco o roteiro cujo `id` bate. Id, nunca caminho vindo do cliente."""
    if not script_id:
        return None
    for item in roteiro_catalogo.roteiros_em_disco():
        if item["id"] != script_id:
            continue
        try:
            caminho = roteiro_catalogo.ROTEIROS_DIR / Path(item["arquivo"]).name
            return json.loads(caminho.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


async def _json_body(request: Request) -> dict:
    try:
        body = await request.body()
        payload = json.loads(body) if body else {}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_json") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="invalid_json")
    return payload


async def _broadcast_profile(mudanca: dict) -> None:
    """Reusa o transporte do singleton; troca de perfil não tem segundo barramento."""
    from .celia_singleton import CeliaSingleton

    try:
        await CeliaSingleton.get_instance().broadcast(
            {
                "type": "profile.changed",
                "profile": mudanca.get("profile"),
                "change": mudanca.get("profile_change"),
            }
        )
    except Exception as exc:  # a troca não pode depender de um cliente de UI
        from loguru import logger

        logger.debug("profile change broadcast unavailable: %s", type(exc).__name__)


def _port(request: Request) -> int:
    return int(request.url.port or (443 if request.url.scheme == "https" else 80))


def init_roteiro_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/roteiro", include_in_schema=False)
    async def roteiro_index(request: Request):
        if roteiro_auth.valid_session(request.cookies.get(SESSION_COOKIE)):
            return RedirectResponse("/roteiro/app", status_code=303)
        return HTMLResponse(_LOGIN_HTML, headers=_secure_headers())

    @router.get("/roteiro/", include_in_schema=False)
    async def roteiro_index_slash(request: Request):
        if roteiro_auth.valid_session(request.cookies.get(SESSION_COOKIE)):
            return RedirectResponse("/roteiro/app", status_code=303)
        return HTMLResponse(_LOGIN_HTML, headers=_secure_headers())

    @router.post("/roteiro/login")
    async def roteiro_login(request: Request):
        client_ip = _client_ip(request)
        decision = roteiro_auth.login_allowed(client_ip)
        if not decision.allowed:
            return JSONResponse(
                {"ok": False, "error": "login_cooldown"},
                status_code=429,
                headers={**_secure_headers(), "Retry-After": str(decision.retry_after)},
            )
        password = await _login_password(request)
        if not roteiro_auth.configured:
            return JSONResponse(
                {"ok": False, "error": "auth_not_configured"},
                status_code=503,
                headers=_secure_headers(),
            )
        if not roteiro_auth.authenticate(password, client_ip):
            return JSONResponse(
                {"ok": False, "error": "invalid_credentials"},
                status_code=401,
                headers=_secure_headers(),
            )
        token = roteiro_auth.create_session()
        response = JSONResponse({"ok": True}, headers=_secure_headers())
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=roteiro_auth.session_ttl,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            path="/roteiro",
        )
        return response

    @router.post("/roteiro/logout")
    async def roteiro_logout(request: Request):
        token = request.cookies.get(SESSION_COOKIE)
        roteiro_auth.revoke(token)
        response = JSONResponse({"ok": True}, headers=_secure_headers())
        response.delete_cookie(SESSION_COOKIE, path="/roteiro", samesite="lax")
        return response

    @router.get("/roteiro/app", include_in_schema=False)
    async def roteiro_app(request: Request):
        if not roteiro_auth.valid_session(request.cookies.get(SESSION_COOKIE)):
            return RedirectResponse("/roteiro/", status_code=303)
        return FileResponse(ROTEIRO_FRONTEND / "index.html", headers=_secure_headers())

    @router.get("/roteiro/status")
    async def roteiro_status(request: Request):
        _require_session(request)
        snapshot = roteiro_live.snapshot()
        snapshot["lan_urls"] = roteiro_live.lan_urls(_port(request))
        snapshot["control"] = "LAN websocket /roteiro/ws"
        return JSONResponse(snapshot, headers=_secure_headers())

    @router.get("/roteiro/scripts")
    async def roteiro_scripts(request: Request):
        """Catálogo somente leitura: o que existe, o que está ativo, o que já foi gravado."""
        _require_session(request)
        return JSONResponse(_catalogo_atual(), headers=_secure_headers())

    @router.post("/roteiro/scripts/select")
    async def roteiro_scripts_select(request: Request):
        """Troca o roteiro ativo pelo caminho MEDIDO que preserva.

        Decisão 2(a) do dono: carregar em `continuous_behavior` e ficar no perfil
        `gravacao`. Nunca passar pelo perfil `roteiro`: medido em 2026-09-30, o
        handler de `profile.changed` recarrega `roteiros/ia_vs_criador.json` do
        disco nesse perfil e sobrescreve o roteiro que o dono acabou de escolher.
        """
        _require_session(request)
        payload = await _json_body(request)
        script_id = str(payload.get("id") or "").strip()
        dados = _roteiro_por_id(script_id)
        if dados is None:
            raise HTTPException(status_code=404, detail="roteiro_desconhecido")
        try:
            continuous_behavior.load_script(dados)
            if continuous_behavior.profile != PERFIL_GRAVACAO:
                mudanca = continuous_behavior.select_profile(PERFIL_GRAVACAO)
                await _broadcast_profile(mudanca)
            live_script = roteiro_live.prepare_operational_script(
                continuous_behavior, continuous_behavior.script.export()
            )
            await roteiro_live.activate(
                PERFIL_GRAVACAO, new_session=True, script=live_script
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        roteiro_catalogo.registrar_selecao(script_id)
        return JSONResponse(_catalogo_atual(), headers=_secure_headers())

    @router.post("/roteiro/scripts/gravado")
    async def roteiro_scripts_gravado(request: Request):
        """O botão do dono. Só ele marca — nenhuma heurística de log marca por ele."""
        _require_session(request)
        payload = await _json_body(request)
        script_id = str(payload.get("id") or "").strip()
        gravado = bool(payload.get("gravado"))
        try:
            roteiro_catalogo.marcar_gravado(script_id, gravado)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(_catalogo_atual(), headers=_secure_headers())

    @router.get("/roteiro/{asset_path:path}", include_in_schema=False)
    async def roteiro_asset(asset_path: str, request: Request):
        _require_session(request)
        relative = Path(asset_path)
        if relative.is_absolute() or ".." in relative.parts:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="asset_not_found")
        target = (ROTEIRO_FRONTEND / relative).resolve()
        if ROTEIRO_FRONTEND.resolve() not in target.parents or not target.is_file():
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="asset_not_found")
        return FileResponse(target, headers=_secure_headers())

    @router.websocket("/roteiro/ws")
    async def roteiro_socket(websocket: WebSocket):
        if not roteiro_auth.valid_session(websocket.cookies.get(SESSION_COOKIE)):
            await websocket.close(code=4401, reason="authentication_required")
            return
        await websocket.accept()
        await roteiro_live.connect(websocket)
        try:
            while True:
                payload = await websocket.receive_json()
                if isinstance(payload, dict):
                    await roteiro_live.control(payload)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            roteiro_live.disconnect(websocket)

    @router.post("/bridge/roteiro/start")
    async def roteiro_start(request: Request):
        _require_loopback(request)
        try:
            body = await request.body()
            payload = json.loads(body) if body else {}
            if not isinstance(payload, dict):
                raise ValueError("invalid_script")
            script = roteiro_live.prepare_operational_script(continuous_behavior, payload.get("script"))
            snapshot = await roteiro_live.activate("roteiro", new_session=True, script=script)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if continuous_behavior.profile != "roteiro":
            profile_snapshot = continuous_behavior.select_profile("roteiro")
            from .celia_singleton import CeliaSingleton

            await CeliaSingleton.get_instance().broadcast(
                {
                    "type": "profile.changed",
                    "profile": "roteiro",
                    "change": profile_snapshot.get("profile_change"),
                }
            )
        snapshot["lan_urls"] = roteiro_live.lan_urls(_port(request))
        return JSONResponse(snapshot)

    @router.post("/bridge/roteiro/stop")
    async def roteiro_stop(request: Request):
        _require_loopback(request)
        snapshot = await roteiro_live.deactivate("chat")
        if continuous_behavior.profile == "roteiro":
            profile_snapshot = continuous_behavior.select_profile("chat")
            from .celia_singleton import CeliaSingleton

            await CeliaSingleton.get_instance().broadcast(
                {
                    "type": "profile.changed",
                    "profile": "chat",
                    "change": profile_snapshot.get("profile_change"),
                }
            )
        return JSONResponse(snapshot)

    @router.get("/bridge/roteiro/start-info")
    async def roteiro_start_info(request: Request):
        _require_loopback(request)
        return {
            "profile": "roteiro",
            "script": continuous_behavior.script.safe_view(),
            "lan_urls": roteiro_live.lan_urls(_port(request)),
            "url": "/roteiro/",
        }

    @router.get("/bridge/roteiro/logs")
    async def roteiro_logs(request: Request):
        _require_loopback(request)
        if not SESSION_LOG_DIR.exists():
            return {"logs": []}
        logs = []
        for path in sorted(SESSION_LOG_DIR.glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)[:50]:
            logs.append(
                {
                    "session_id": path.stem,
                    "path": f"var/roteiro_sessions/{path.name}",
                    "bytes": path.stat().st_size,
                    "modified": path.stat().st_mtime,
                }
            )
        return {"logs": logs}

    @router.get("/bridge/roteiro/report/{session_id}")
    async def roteiro_report(session_id: str, request: Request):
        """Relatorio final da sessao gravada — JSONL bruto NAO e relatorio.

        Criado em 2026-08-24. O diagnostico registrou que a gravacao produzia o
        log e parava ali: nao existia nada que transformasse a sequencia de
        eventos em algo legivel. Isto le o proprio arquivo da sessao e devolve
        duracao, cobertura do roteiro, quem falou quanto, os beats alcancados e
        os problemas — sem inventar campo que o log nao tem.
        """
        _require_loopback(request)
        if not re.fullmatch(r"[0-9a-f]{32}", session_id):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="session_not_found")
        caminho = SESSION_LOG_DIR / f"{session_id}.jsonl"
        if not caminho.is_file():
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="session_not_found")

        eventos: list[dict] = []
        corrompidas = 0
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            if not linha.strip():
                continue
            try:
                eventos.append(json.loads(linha))
            except json.JSONDecodeError:
                corrompidas += 1

        if not eventos:
            return JSONResponse(
                {"session_id": session_id, "eventos": 0, "linhas_corrompidas": corrompidas,
                 "aviso": "sessao sem evento legivel"}
            )

        contagem: dict[str, int] = {}
        for evento in eventos:
            nome = str(evento.get("event") or "?")
            contagem[nome] = contagem.get(nome, 0) + 1

        marcas = [float(e.get("timestamp") or 0.0) for e in eventos if e.get("timestamp")]
        duracao = round(max(marcas) - min(marcas), 2) if len(marcas) > 1 else 0.0

        # Cobertura: quais beats a sessao realmente alcancou, sem duplicar.
        beats: list[str] = []
        for evento in eventos:
            beat = evento.get("beat_id")
            if beat and (not beats or beats[-1] != beat):
                beats.append(str(beat))

        def _texto(evento: dict) -> str:
            dados = evento.get("data")
            if isinstance(dados, dict):
                for chave in ("text", "texto", "final", "content"):
                    valor = dados.get(chave)
                    if isinstance(valor, str) and valor.strip():
                        return valor.strip()
            return ""

        falas_criador = [_texto(e) for e in eventos if str(e.get("event", "")).startswith("criador.final")]
        falas_celia = [_texto(e) for e in eventos if str(e.get("event", "")).startswith("celia.")]
        falas_criador = [t for t in falas_criador if t]
        falas_celia = [t for t in falas_celia if t]

        # Problemas: o relatorio existe para mostrar tambem o que deu errado.
        problemas: list[str] = []
        if corrompidas:
            problemas.append(f"{corrompidas} linha(s) ilegivel(is) no log")
        if "session.stopped" not in contagem:
            problemas.append("sessao sem evento de encerramento — pode ter caido")
        vazias = sum(
            1
            for e in eventos
            if str(e.get("event", "")).startswith("celia.") and not _texto(e)
        )
        if vazias:
            problemas.append(f"{vazias} turno(s) da Celia sem texto registrado")
        if not falas_celia:
            problemas.append("nenhuma fala da Celia no log")

        return JSONResponse(
            {
                "session_id": session_id,
                "arquivo": f"var/roteiro_sessions/{caminho.name}",
                "bytes": caminho.stat().st_size,
                "eventos": len(eventos),
                "linhas_corrompidas": corrompidas,
                "duracao_s": duracao,
                "duracao_min": round(duracao / 60, 2),
                "perfil": eventos[0].get("profile"),
                "eventos_por_tipo": contagem,
                "beats_alcancados": beats,
                "beats_distintos": len(set(beats)),
                "falas": {
                    "criador": len(falas_criador),
                    "celia": len(falas_celia),
                    "caracteres_criador": sum(len(t) for t in falas_criador),
                    "caracteres_celia": sum(len(t) for t in falas_celia),
                },
                "problemas": problemas,
                "completa": not problemas,
            }
        )

    @router.get("/bridge/roteiro/logs/{session_id}")
    async def roteiro_log(session_id: str, request: Request):
        _require_loopback(request)
        if not re.fullmatch(r"[0-9a-f]{32}", session_id):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="log_not_found")
        path = SESSION_LOG_DIR / f"{session_id}.jsonl"
        if not path.is_file():
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="log_not_found")
        return FileResponse(path, media_type="application/x-ndjson", filename=path.name)

    return router
