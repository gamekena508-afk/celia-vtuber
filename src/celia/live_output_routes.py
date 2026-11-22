"""Loopback-only, receive-only AV transport for the owner's Windows VM.

Reuses the existing publisher hub. No owner APIs, device capture, control
messages, password sessions, or external network binding are added here.
"""
import asyncio
import json
import ipaddress
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from .mobile_routes import ASSETS, headers, is_local, mobile_hub



async def resolve_mdns_candidate(message):
    """Resolve publisher host candidates for a NAT guest without mDNS multicast."""
    data = message.get('data', {})
    candidate = data.get('candidate', {}) if isinstance(data, dict) else {}
    if not isinstance(candidate, dict) or not isinstance(candidate.get('candidate'), str):
        return message
    parts = candidate['candidate'].split()
    if len(parts) < 8 or parts[6:8] != ['typ', 'host']:
        return message
    hostname = parts[4]
    if not re.fullmatch(r'[a-fA-F0-9-]{36}\.local', hostname):
        return message
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            '/usr/bin/avahi-resolve-host-name', '-4', hostname,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2)
        fields = stdout.decode().split()
        if proc.returncode != 0 or len(fields) != 2 or fields[0] != hostname:
            return message
        address = ipaddress.ip_address(fields[1])
        if address.version != 4 or not address.is_private or address.is_loopback:
            return message
        parts[4] = str(address)
        return {**message, 'data': {**data, 'candidate': {
            **candidate, 'candidate': ' '.join(parts)}}}
    except (OSError, ValueError, asyncio.TimeoutError):
        return message
    finally:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()


class LocalReceiverSocket:
    """Only this loopback receiver gets resolved candidates; mobile stays unchanged."""
    def __init__(self, ws):
        self.ws = ws

    def __getattr__(self, name):
        return getattr(self.ws, name)

    async def send_json(self, message):
        await self.ws.send_json(await resolve_mdns_candidate(message))

def init_live_output_routes():
    router = APIRouter()

    @router.get('/live-output/{asset}')
    async def asset(asset: str, request: Request):
        if not is_local(request.client):
            raise HTTPException(403, 'receiver_loopback_only')
        filename = {'receiver': 'live-receiver.html', 'receiver.js': 'live-receiver.js'}.get(asset)
        if filename is None:
            raise HTTPException(404, 'asset_not_found')
        return FileResponse(ASSETS / filename, headers={**headers(),
            'Permissions-Policy': 'camera=(), microphone=(), display-capture=()'})

    @router.websocket('/live-output/ws')
    async def receiver(ws: WebSocket):
        origin = ws.headers.get('origin')
        parsed = urlsplit(origin) if origin else None
        same_origin = not parsed or (parsed.scheme in {'http', 'https'} and parsed.netloc == ws.headers.get('host'))
        if not is_local(ws.client) or not same_origin:
            await ws.close(code=4403)
            return
        receiver_socket = LocalReceiverSocket(ws)
        ident = await mobile_hub.join(receiver_socket, studio=False)
        if ident is None:
            await ws.close(code=4409)
            return
        try:
            while True:
                raw = await ws.receive_text()
                if len(raw) > 65536:
                    await ws.close(code=1009)
                    break
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                # A receiver may answer negotiation only; never forward controls
                # or let a guest masquerade as a publisher.
                if isinstance(message, dict) and message.get('type') == 'signal':
                    data = message.get('data')
                    if isinstance(data, dict) and data.get('type') in {'answer', 'candidate'}:
                        await mobile_hub.handle(ident, message)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            await mobile_hub.leave(receiver_socket, ident)
    return router
