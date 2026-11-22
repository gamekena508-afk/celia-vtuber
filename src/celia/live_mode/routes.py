"""Rotas /bridge/live/* do modo Live. Tudo local (loopback); nenhuma rota conecta ao TikTok sozinha."""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .manager import Job, LiveModeManager
from .reader_ctl import ReaderController, ReaderError
from .voices import AUDIO_DIR
from celia.core.dialogue_hygiene import is_infrastructure_text

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
PLAYBACK_DONE = {"ended", "finished", "complete", "completed", "ok", "success"}
PLAYBACK_FAIL = {"error", "cancelled", "interrupted", "dropped"}


def _loopback(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host.lower() not in LOOPBACK:
        raise HTTPException(status_code=403, detail="somente loopback")


def make_celia_reply(base_url_getter, timeout: float = 240.0):
    """Resposta da Célia pelo pipeline existente (/bridge/process: Guardião, persona, memória, voz e lip sync dela)."""
    async def prepare(job: Job) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=base_url_getter(), timeout=timeout) as http:
            r = await http.post('/bridge/process', json={
                'text':job.text,'fonte':'tiktok_live_chat' if job.origin=='real' else 'live_test',
                'autor':job.author,'autor_id':job.user_id,'message_id':job.message_id,
                'synthesize_audio':True,'read_aloud_input':False,'prepare_only':True})
            data = r.json()
            ok = r.status_code==200 and not data.get('error') and bool(data.get('audio_paths')) and not is_infrastructure_text(data.get('response'))
            return {'ok':ok,**data}

    async def celia_reply(job: Job) -> dict[str, Any]:
        base = base_url_getter()
        async with httpx.AsyncClient(base_url=base, timeout=timeout) as http:
            if job.prepared_result is not None:
                ready=job.prepared_result
                r=await http.post('/bridge/speak',json={'text':ready['response'],'audio_path':ready['audio_paths'][0],
                    'emotion':ready.get('emotion'),'wait_for_playback':True,'record_context':True,'record_after_playback':True,
                    'speech_source':'tiktok_live_chat','speech_event_key':job.message_id})
                data=r.json()
                if data.get('timed_out') and data.get('speech_id'):
                    # A espera inicial pode vencer enquanto o player está na fila.
                    # Não liberar outro personagem enquanto a Célia ainda toca.
                    deadline=time.monotonic()+min(45,float(data.get('audio_duration_sec') or 0)+8)
                    while time.monotonic()<deadline:
                        state=(await http.get('/bridge/playback/'+data['speech_id']+'/status',timeout=5)).json().get('status')
                        if state in PLAYBACK_DONE or state in PLAYBACK_FAIL:
                            data['playback_status']=state
                            break
                        await asyncio.sleep(.2)
                return {'ok':r.status_code==200 and data.get('ok') is True and data.get('playback_status') in PLAYBACK_DONE,
                        'text':ready['response'],'speech_id':data.get('speech_id'),
                        'error':data.get('error') or ('playback_'+str(data.get('playback_status')) if data.get('playback_status') not in PLAYBACK_DONE else None)}
            if job.reason == 'test':
                r = await http.post('/bridge/speak', json={'text': job.text, 'wait_for_playback': True})
                data = r.json() if r.headers.get('content-type', '').startswith('application/json') else {}
                return {'ok': r.status_code == 200 and data.get('ok') is True,
                        'text': job.text, 'speech_id': data.get('speech_id'), 'error': data.get('error')}
            r = await http.post("/bridge/process", json={
                "text": job.text, "fonte": "tiktok_live_chat" if job.origin == "real" else "live_test",
                "autor": job.author, "autor_id": job.user_id, "message_id": job.message_id,
                "synthesize_audio": True, "read_aloud_input": False})
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code != 200 or data.get("error"):
                return {"ok": False, "error": str(data.get("error") or r.status_code)[:120]}
            if is_infrastructure_text(data.get("response")):
                return {"ok": False, "error": "output_suppressed"}
            speech_id = data.get("speech_id")
            if speech_id:
                deadline = time.monotonic() + 120
                start_deadline = time.monotonic() + 8
                playback_started = False
                while time.monotonic() < deadline:
                    s = (await http.get(f"/bridge/playback/{speech_id}/status", timeout=5)).json().get("status")
                    if s in PLAYBACK_DONE:
                        break
                    if s in PLAYBACK_FAIL:
                        return {"ok": False, "error": "playback_" + str(s), "text": data.get("response"), "speech_id": speech_id}
                    playback_started = playback_started or s in ('started', 'playing')
                    if not playback_started and time.monotonic() >= start_deadline:
                        await http.post(f"/bridge/playback/{speech_id}/cancel", json={})
                        return {"ok": False, "error": "playback_start_timeout", "speech_id": speech_id}
                    await asyncio.sleep(0.25)
                else:
                    return {"ok": False, "error": "playback_unconfirmed", "text": data.get("response"), "speech_id": speech_id}
            elif data.get("response"):
                st = (await http.get("/bridge/external-turn/status", timeout=5)).json()
                if st.get("clients", 0) < 1:
                    return {"ok": False, "error": "no_audio_client", "text": data.get("response")}
                return {"ok": False, "error": "speech_not_generated"}
            return {"ok": bool(data.get("response")), "text": data.get("response"), "speech_id": speech_id}
    async def record(job):
        from celia.celia_singleton import CeliaSingleton
        from celia.bridge_routes import _record_spoken_line
        _record_spoken_line(CeliaSingleton.get_instance(),job.reply,'Célia','ended')
    celia_reply.record = record
    celia_reply.shared_player = True
    celia_reply.prepare = prepare
    return celia_reply


class EnableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(..., pattern="^(test|live)$")
    room_id: str | None = Field(None, max_length=32)


class SimChat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(..., min_length=1, max_length=64)
    nickname: str = Field("", max_length=60)
    text: str = Field(..., min_length=1, max_length=1000)
    msg_id: str | None = None


class SimGift(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(..., min_length=1, max_length=64)
    nickname: str = Field("", max_length=60)
    gift_name: str = "Rose"
    diamond_count: int = Field(..., ge=1, le=1_000_000)
    repeat_count: int = Field(1, ge=1, le=100_000)
    streakable: bool = False
    repeat_end: bool = True
    group_id: str = "sim"
    msg_id: str | None = None


class TestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character: str = Field(..., pattern="^(celia|slime|ursinho)$")
    text: str = Field("Oi! Estou testando minha voz e minha boca.", min_length=1, max_length=300)


class ReaderStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authorized: bool = False
    room_id: str = ""
    handle: str = Field("", max_length=180)
    live_confirmed: bool = False
    confirm_same_room: bool = False


class AckBody(BaseModel):
    client: str = ""
    attempt: int = 0
    speech_id: str
    status: str = "ended"
    played_ms: int | None = None


def init_live_routes(manager: LiveModeManager | None = None) -> APIRouter:
    router = APIRouter(prefix="/bridge/live", tags=["live-mode"], dependencies=[Depends(_loopback)])
    mgr = manager or LiveModeManager()
    base = {"url": "http://127.0.0.1:12393"}
    mgr._celia_reply = mgr._celia_reply or make_celia_reply(lambda: base["url"])
    mgr.reader = mgr.reader or ReaderController(mgr)
    router.manager = mgr  # type: ignore[attr-defined]

    def remember(request: Request) -> None:
        base["url"] = str(request.base_url).rstrip("/")

    tagarela = {"process": None}
    tagarela_lock = asyncio.Lock()

    @router.post("/tagarela/start")
    async def start_tagarela():
        """Ouvido opcional: somente um pedido manual pode iniciar o serviço."""
        import subprocess
        async with tagarela_lock:
            async with httpx.AsyncClient(timeout=1) as client:
                try:
                    r = await client.get('http://127.0.0.1:8011/openapi.json')
                    if r.is_success:
                        return {"ok": True, "state": "running"}
                except httpx.HTTPError:
                    pass
            proc = tagarela['process']
            if proc is not None and proc.poll() is None:
                return {"ok": True, "state": "starting"}
            root = Path(__file__).resolve().parents[3]
            python = root / '.venv-asr-tagarela/Scripts/python.exe'
            if not python.is_file():
                raise HTTPException(503, 'Tagarela não instalado')
            log = root / 'var/logs/tagarela-manual.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open('ab') as output:
                tagarela['process'] = subprocess.Popen(
                    [str(python), str(root / 'tools/asr_tagarela_server.py')],
                    cwd=root, stdout=output, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return {"ok": True, "state": "starting"}

    @router.get("/status")
    async def status(request: Request):
        remember(request)
        return mgr.snapshot()

    @router.post("/enable")
    async def enable(body: EnableBody, request: Request):
        remember(request)
        return await mgr.enable(body.mode, room_id=body.room_id)

    @router.post("/disable")
    async def disable():
        return await mgr.disable()

    @router.get("/settings")
    async def get_settings():
        return mgr.settings.get()

    @router.post("/settings")
    async def set_settings(patch: dict[str, Any]):
        new = mgr.settings.update(patch)
        if mgr.active:
            mgr._publish_state()
        return new

    @router.post("/ranking/reset")
    async def reset_ranking():
        return mgr.reset_ranking()

    # ---- simulação (claramente marcada, zero rede) ----
    def _require_test() -> None:
        if mgr.mode != "test":
            raise HTTPException(status_code=409, detail="ative o modo de teste (POST /bridge/live/enable {mode:test}) antes de simular")

    @router.post("/simulate/chat")
    async def sim_chat(body: SimChat, request: Request):
        _require_test(); remember(request)
        return await mgr.ingest({"type": "chat", "origin": "simulated", "msg_id": body.msg_id or "sim-" + uuid.uuid4().hex[:10],
                                 "user_id": body.user_id, "display_id": body.user_id, "nickname": body.nickname or body.user_id,
                                 "text": body.text})

    @router.post("/simulate/gift")
    async def sim_gift(body: SimGift):
        _require_test()
        return await mgr.ingest({"type": "gift", "origin": "simulated", "msg_id": body.msg_id or "sim-" + uuid.uuid4().hex[:10],
                                 "user_id": body.user_id, "display_id": body.user_id, "nickname": body.nickname or body.user_id,
                                 "gift_id": body.gift_name, "gift_name": body.gift_name, "diamond_count": body.diamond_count,
                                 "repeat_count": body.repeat_count, "streakable": body.streakable, "repeat_end": body.repeat_end,
                                 "group_id": body.group_id})

    @router.post("/test-character")
    async def test_character(body: TestBody, request: Request):
        remember(request)
        return await mgr.speak_test(body.character, body.text)

    # ---- eventos reais (somente o leitor, com token) ----
    @router.post("/event")
    async def real_event(event: dict[str, Any], request: Request):
        token = request.headers.get("x-live-token")
        reader: ReaderController = mgr.reader
        if not reader.check_token(token):
            raise HTTPException(status_code=401, detail="token do leitor invalido")
        reader.touch()
        if event.get("type") == "status":
            reader.report(str(event.get("state") or ""), str(event.get("message") or ""))
            return {"ok": True}
        event["origin"] = "real"   # origem real só vale para quem tem o token do leitor
        return await mgr.ingest(event)

    @router.post("/reader/start")
    async def reader_start(body: ReaderStart, request: Request):
        remember(request)
        try:
            return await mgr.reader.start(room_id=body.room_id, authorized=body.authorized,
                                          event_url=str(request.base_url).rstrip("/") + "/bridge/live/event",
                                          confirm_same_room=body.confirm_same_room,
                                          handle=body.handle, live_confirmed=body.live_confirmed)
        except ReaderError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/reader/stop")
    async def reader_stop():
        return await mgr.reader.stop()

    @router.get("/reader/status")
    async def reader_status():
        return mgr.reader.status()

    # ---- navegador: eventos, áudio e confirmação de reprodução ----
    @router.get("/stream")
    async def stream(client: str, after: int = 0, wait: float = 15.0):
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", client):
            raise HTTPException(status_code=400, detail="client invalido")
        return await mgr.wait_events(client, after, min(max(wait, 0.0), 25.0))

    @router.post("/ack")
    async def ack(body: AckBody):
        return {"ok": mgr.on_ack(body.model_dump())}

    @router.post("/audio/claim")
    async def audio_claim(body: dict[str, str]):
        client = body.get("client", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", client) or client not in mgr._clients:
            raise HTTPException(status_code=400, detail="cliente_de_audio_desconhecido")
        # A página só solicita isto após gesto explícito e AudioContext em running.
        return mgr.claim_audio(client)

    @router.get("/audio/{name}")
    async def audio(name: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.wav", name):
            raise HTTPException(status_code=400, detail="nome invalido")
        path = Path(AUDIO_DIR) / name
        if not path.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    return router
