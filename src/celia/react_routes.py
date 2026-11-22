from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import yaml
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from bots.react.worker_process import IndependentWorkerProxy, default_react_root
from .react.store import ReactStore
from .react.worker import download_youtube, now, plan_segments

ROOT = Path(__file__).resolve().parents[2]


def _config() -> dict:
    path = ROOT / "config/react_v2.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


def _local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def _worker_connected(request: Request) -> bool:
    manager = getattr(request.app.state, "bot_process_manager", None)
    if manager is None:
        return False
    try:
        status = manager.status("react")
    except (KeyError, AttributeError):
        return False
    return bool(status.connected)


def _trim_media_cache(store: ReactStore, keep: Path, budget: int) -> None:
    files = [p for p in store.media.iterdir() if p.is_file() and re.fullmatch(r"v-[a-f0-9]{16}\.mp4", p.name)]
    total = sum(p.stat().st_size for p in files)
    for old in sorted((p for p in files if p != keep), key=lambda p: p.stat().st_atime):
        if total <= budget:
            break
        size = old.stat().st_size
        old.unlink(missing_ok=True)
        total -= size
    if total > budget:
        keep.unlink(missing_ok=True)
        raise RuntimeError("cache de mídia excede o teto de 4 GiB")


class VideoRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    mode: str = "duo"


def init_react_routes() -> tuple[APIRouter, ReactStore, IndependentWorkerProxy]:
    cfg = _config()
    configured_root = cfg.get("storage_root")
    store_root = (ROOT / configured_root).resolve() if configured_root else default_react_root()
    store = ReactStore(store_root)
    fixture_allowed = os.environ.get("CELIA_REACT_FIXTURE") == "1"
    configured_analyzer = cfg.get("analyzer", "real")
    selected_analyzer = "fixture" if configured_analyzer == "fixture" and fixture_allowed else "real"
    worker = IndependentWorkerProxy(store, analyzer=selected_analyzer,
                                    tagarela_url=cfg.get("tagarela_url", "http://127.0.0.1:8011/v1"))
    adapter_enabled = bool(cfg.get("celia_adapter", False))
    worker.adapter_voice = bool(cfg.get("celia_adapter_voice", False))  # padrão: sem voz
    worker.adapter_interval_s = float(cfg.get("celia_adapter_interval_s", 15))
    router = APIRouter(prefix="/react")

    @router.post("/api/videos")
    async def submit_video(body: VideoRequest, request: Request):
        if not _local(request):
            raise HTTPException(403, "React API disponível somente no loopback nesta fase")
        if body.mode not in {"duo", "solo"}:
            raise HTTPException(422, "mode deve ser duo ou solo")
        if not _worker_connected(request):
            raise HTTPException(503, "worker React não está conectado; nenhum download foi iniciado")
        parsed = urlparse(body.url)
        if parsed.scheme != "https" or parsed.hostname not in {"youtu.be", "www.youtube.com", "youtube.com", "m.youtube.com"}:
            raise HTTPException(422, "somente URLs https do YouTube são aceitas")
        video_id = "v-" + uuid.uuid4().hex[:16]
        session_id = "s-" + uuid.uuid4().hex[:16]
        media_dir = store.media
        target = media_dir / f"{video_id}.mp4"
        max_bytes = int(cfg.get("max_media_bytes", 4 * 1024**3))
        # Return promptly with a provisional session; metadata/download finish in background.
        video = {"video_id": video_id, "source_url": body.url, "title": video_id, "channel": None,
                 "thumbnail_url": None, "duration_ms": 1, "media_url": f"/react/api/media/{video_id}", "revision": 1}
        segments = plan_segments(video_id, 1, 1)
        session = {"contract": "1.0.0", "mode": body.mode, "video": video, "segments": segments,
            "playback": {"session_id": session_id, "video_id": video_id, "revision": 1, "position_ms": 0,
                "paused": True, "rate": 1.0, "played_ranges": [], "pause_reason": None, "sequence": 0}}
        store.save(session)
        await worker.emit(session, "video.submitted", {"video": video})
        async def finish_submission():
          try:
            info = await asyncio.to_thread(download_youtube, body.url, target,
                int(cfg.get("download_timeout_seconds", 1800)), max_bytes)
            actual = Path(info["_media_path"])
            if actual.stat().st_size > max_bytes:
                actual.unlink(missing_ok=True)
                raise RuntimeError("mídia excedeu teto configurado de 4 GiB")
            if actual != target:
                actual.replace(target)
            _trim_media_cache(store, target, max_bytes)
            duration_ms = round(float(info.get("duration") or 0) * 1000)
            if duration_ms <= 0:
                raise RuntimeError("yt-dlp não informou duração válida")
            video.update({"title": info.get("title") or video_id,
                     "channel": info.get("channel") or info.get("uploader"),
                     "thumbnail_url": info.get("thumbnail"), "duration_ms": duration_ms})
            segments = plan_segments(video_id, 1, duration_ms, int(cfg.get("segment_target_ms", 150000)),
                                     int(cfg.get("max_segments", 40)), cfg.get("first_segment_ms"))
            session = {"contract": "1.0.0", "mode": body.mode, "video": video, "segments": segments,
                "playback": {"session_id": session_id, "video_id": video_id, "revision": 1, "position_ms": 0,
                    "paused": True, "rate": 1.0, "played_ranges": [], "pause_reason": None, "sequence": 0}}
            session["segments"] = segments
            await worker.enqueue_session(session)
            await worker.emit(session, "video.ready_to_play", {"session": session})
          except Exception as exc:
            target.unlink(missing_ok=True)
            latest = store.get(session_id)
            if latest:
                await worker.emit(latest, "video.failed", {"reason": str(exc)[:1000]})
        asyncio.create_task(finish_submission(), name=f"react-download-{video_id}")
        return session

    @router.get("/api/sessions")
    async def list_sessions(request: Request):
        """Tela principal: um cartão por sessão, mais recente primeiro (Opus, 14/11)."""
        if not _local(request):
            raise HTTPException(403, "loopback requerido")
        itens = []
        for session in store.all():
            pb, video, segs = session["playback"], session["video"], session["segments"]
            sid = pb["session_id"]
            try:
                mtime = store._path(sid).stat().st_mtime
            except (OSError, ValueError):
                continue
            played = sum(max(0, r["end_ms"] - r["start_ms"]) for r in pb.get("played_ranges") or [])
            itens.append({
                "session_id": sid, "video": video, "mode": session.get("mode", "duo"),
                "ready": sum(1 for s in segs if s["status"] == "READY"), "total": len(segs),
                "failed": sum(1 for s in segs if s["status"] == "FAILED"),
                "fixture": any((s.get("provenance") or {}).get("analyzer") == "fixture" for s in segs if s["status"] == "READY"),
                "played_ms": played, "position_ms": pb.get("position_ms", 0),
                "downloading": video.get("duration_ms", 0) <= 1,
                "media_ok": any(p.is_file() for p in [store.media / f"{video['video_id']}.mp4", *store.media.glob(video["video_id"] + ".*")]),
                "local_thumbnail_url": f"/react/api/sessions/{sid}/thumbnail",
                "updated_at": mtime,
            })
        itens.sort(key=lambda x: x["updated_at"], reverse=True)
        return itens

    @router.get("/api/sessions/{session_id}/thumbnail")
    async def session_thumbnail(session_id: str, request: Request):
        """Miniatura local (um quadro do próprio vídeo), para quando a do YouTube não carrega/offline."""
        if not _local(request):
            raise HTTPException(403, "loopback requerido")
        session = store.get(session_id)
        if session is None:
            raise HTTPException(404, "sessão não encontrada")
        video_id = session["video"]["video_id"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", video_id):
            raise HTTPException(404)
        thumbs = store.root / "thumbs"
        thumbs.mkdir(exist_ok=True)
        out = thumbs / f"{video_id}.jpg"
        if not out.is_file():
            media = next((p for p in [store.media / f"{video_id}.mp4", *store.media.glob(video_id + ".*")] if p.is_file()), None)
            if media is None:
                raise HTTPException(404, "mídia ainda não baixada")
            at = max(0.0, min(30.0, session["video"]["duration_ms"] / 1000 * 0.1))
            # Se o ponto cair depois do fim real da mídia (duração declarada maior), tenta o começo.
            for pos in (at, 0.0):
                r = await asyncio.to_thread(subprocess.run, ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{pos:.2f}", "-i", str(media),
                                            "-frames:v", "1", "-vf", "scale=640:-2", str(out)], capture_output=True, timeout=60)
                if not r.returncode and out.is_file() and out.stat().st_size:
                    break
            if not out.is_file() or not out.stat().st_size:
                out.unlink(missing_ok=True)
                raise HTTPException(500, "não consegui gerar a miniatura")
        return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    @router.get("/api/sessions/{session_id}")
    async def get_session(session_id: str, request: Request):
        if not _local(request):
            raise HTTPException(403, "loopback requerido")
        session = store.get(session_id)
        if session is None:
            raise HTTPException(404, "sessão não encontrada")
        return session

    @router.post("/api/segments/{segment_id}/retry")
    async def retry_segment(segment_id: str, request: Request):
        if not _local(request):
            raise HTTPException(403, "loopback requerido")
        for session in store.all():
            seg = next((s for s in session["segments"] if s["segment_id"] == segment_id), None)
            if seg:
                if seg["status"] != "FAILED":
                    return {"session": session, "idempotent": True}
                sid = session["playback"]["session_id"]
                session, seg = worker._set_segment(sid, segment_id, status="QUEUED", failure_reason=None, checksum=None, provenance=None)
                await worker.enqueue_job(sid, segment_id)
                await worker.emit(session, "segment.queued", {"segment": seg, "retry": True})
                return {"session": session, "idempotent": False}
        raise HTTPException(404, "segmento não encontrado")

    @router.post("/api/sessions/{session_id}/segments/{segment_id}/retry")
    async def retry_session_segment(session_id: str, segment_id: str, request: Request):
        if not _local(request):
            raise HTTPException(403, "loopback requerido")
        session = store.get(session_id)
        if session is None:
            raise HTTPException(404, "sessão não encontrada")
        seg = next((s for s in session["segments"] if s["segment_id"] == segment_id), None)
        if seg is None:
            raise HTTPException(404, "segmento não encontrado nesta sessão")
        if seg["status"] != "FAILED":
            return {"session": session, "idempotent": True}
        session, seg = worker._set_segment(session_id, segment_id, status="QUEUED", failure_reason=None, checksum=None, provenance=None)
        await worker.enqueue_job(session_id, segment_id)
        await worker.emit(session, "segment.queued", {"segment": seg, "retry": True})
        return {"session": session, "idempotent": False}

    @router.get("/api/media/{video_id}")
    async def get_media(video_id: str, request: Request):
        if not _local(request):
            raise HTTPException(403, "loopback requerido")
        if not re.fullmatch(r"v-[a-f0-9]{16}", video_id):
            raise HTTPException(404)
        path = store.media / f"{video_id}.mp4"
        if not path.is_file():
            # Permit yt-dlp's detected final extension for container variants.
            found = list(store.media.glob(video_id + ".*"))
            if not found:
                raise HTTPException(404, "mídia não encontrada")
            path = found[0]
        size = path.stat().st_size
        os.utime(path, ns=(time.time_ns(), path.stat().st_mtime_ns))
        header = request.headers.get("range")
        start, end = 0, size - 1
        status = 200
        if header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
            if not match:
                raise HTTPException(416, "Range inválido")
            a, b = match.groups()
            if a:
                start = int(a)
                end = min(int(b), size - 1) if b else size - 1
            elif b:
                start = max(0, size - int(b))
            if start >= size or end < start:
                raise HTTPException(416, "Range fora da mídia")
            status = 206
        length = end - start + 1
        async def body_iter():
            with path.open("rb") as f:
                f.seek(start)
                remaining = length
                while remaining:
                    chunk = f.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
        headers = {"Accept-Ranges": "bytes", "Content-Length": str(length), "Content-Type": "video/mp4"}
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return StreamingResponse(body_iter(), status_code=status, headers=headers)

    @router.websocket("/ws/{session_id}")
    async def session_ws(websocket: WebSocket, session_id: str):
        try:
            loopback = bool(websocket.client and ipaddress.ip_address(websocket.client.host).is_loopback)
        except ValueError:
            loopback = False
        if not loopback:
            await websocket.close(code=4403)
            return
        session = store.get(session_id)
        if session is None:
            await websocket.close(code=4404)
            return
        await websocket.accept()
        worker.listeners.setdefault(session_id, set()).add(websocket)
        try:
            await websocket.send_json({"v": "1.0.0", "type": "video.ready_to_play", "session_id": session_id,
                "sequence": session["playback"]["sequence"], "at": now(), "payload": {"session": session}})
            # Ao reabrir, o player recebe de novo só o que a Célia JÁ viu (nada novo é liberado aqui).
            snapshot = worker.released_snapshot(session_id)
            if snapshot:
                await websocket.send_json({"v": "1.0.0", "type": "context.released", "session_id": session_id,
                    "sequence": session["playback"]["sequence"], "at": now(), "payload": snapshot})
            while True:
                message = await websocket.receive_json()
                typ = message.get("type", "")
                payload = message.get("payload") or message
                if typ.startswith("playback."):
                    # Passa o id, não a cópia lida na conexão: ela ficava velha e apagava o READY do worker.
                    await worker.playback_event(session_id, typ, payload, adapter=adapter_enabled)
                    if typ in {"playback.seek", "playback.waiting_for_analysis"}:
                        await worker.prioritize_position(session_id, int(payload.get("position_ms", 0)))
                elif message.get("type") == "segment.prioritize":
                    await worker.prioritize(session_id, str(message.get("segment_id", "")))
        except WebSocketDisconnect:
            pass
        finally:
            worker.listeners.get(session_id, set()).discard(websocket)

    return router, store, worker
