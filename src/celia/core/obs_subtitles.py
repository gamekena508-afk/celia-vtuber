from __future__ import annotations

import asyncio
import html
import json
import os
import re
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from loguru import logger

from ..utils.tts_preprocessor import (
    EMOTION_TAGS,
    remove_control_markup_tags,
    remove_unbracketed_emotion_tags,
)


_GENERIC_CONTROL_TAG_RE = re.compile(r"(?:\[\s*[A-Za-z][A-Za-z0-9_-]{0,31}\s*\]|<\s*/?\s*[A-Za-z][A-Za-z0-9_-]{0,31}\s*>)")
_EMOTION_HASH_RE = re.compile(
    r"(?i)(?<!\w)#(?:" + "|".join(re.escape(tag) for tag in sorted(EMOTION_TAGS, key=len, reverse=True)) + r")\b"
)
_INTERNAL_TAG_DIRECTIVE_RE = re.compile(
    r"(?i)\b(?:responda|responde)\s+com\s+(?:1|uma)\s+emoç(?:ão|ao)\s*[.!:]?\s*tags?\s*:\s*[^\wÀ-ÿ]*"
)


def _sanitize_subtitle_text(text: str) -> str:
    """Keep control metadata out of the human-facing subtitle surface."""
    cleaned = remove_control_markup_tags(str(text or ""))
    cleaned = _GENERIC_CONTROL_TAG_RE.sub(" ", cleaned)
    cleaned = _EMOTION_HASH_RE.sub(" ", cleaned)
    cleaned = remove_unbracketed_emotion_tags(cleaned)
    cleaned = _INTERNAL_TAG_DIRECTIVE_RE.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _duration_seconds(audio_path: str) -> float:
    with wave.open(audio_path, "rb") as wav:
        frames = wav.getnframes()
        rate = wav.getframerate() or 1
        return max(0.1, frames / rate)


# Fonte APROVADA da legenda: Chewy (docs/obs_legenda_real.md). Ela vive DENTRO do projeto
# (assets/fonts, Apache 2.0) para Windows e Linux usarem o mesmo arquivo. Sem ela o canvas cai
# em "Arial Black" — foi o que aconteceu no Windows até 13/11/2026 (a rota devolvia 500).
_REPO_FONT = Path(__file__).resolve().parents[3] / "assets" / "fonts" / "Chewy-Regular.ttf"


def _chewy_font_path() -> Path | None:
    candidates = [os.getenv("CELIA_OBS_FONT_PATH"), str(_REPO_FONT),
                  str(Path.home() / ".local" / "share" / "fonts" / "Chewy-Regular.ttf"),
                  str(Path(os.getenv("WINDIR", r"C:\Windows")) / "Fonts" / "Chewy-Regular.ttf"),
                  str(Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts" / "Chewy-Regular.ttf")]
    for candidate in candidates:
        if candidate and Path(candidate).expanduser().is_file():
            return Path(candidate).expanduser()
    return None


def _wrap_words(text: str, max_chars: int) -> list[str]:
    words = text.replace("\n", " ").split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        if not current:
            current = word
            continue
        if len(current) + 1 + len(word) <= max_chars:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _build_pages(text: str, duration_sec: float, max_chars: int) -> list[dict[str, Any]]:
    lines = _wrap_words(text, max_chars)
    if not lines:
        return []

    total_chars = max(1, sum(len(line) for line in lines))
    cursor = 0.0
    result: list[dict[str, Any]] = []
    for line in lines:
        page_chars = max(1, len(line))
        page_duration = max(0.25, duration_sec * (page_chars / total_chars))
        chars = list(line)
        char_cursor = 0.0
        char_timings: list[dict[str, Any]] = []
        for char_index, char in enumerate(chars):
            if char_index == len(chars) - 1:
                char_duration = max(0.02, page_duration - char_cursor)
            else:
                char_duration = max(0.02, page_duration / max(1, len(chars)))
            char_timings.append(
                {
                    "text": char,
                    "start": char_cursor,
                    "duration": char_duration,
                }
            )
            char_cursor += char_duration
        result.append(
            {
                "lines": [line, ""],
                "chars": char_timings,
                "start": cursor,
                "duration": page_duration,
            }
        )
        cursor += page_duration

    if result:
        drift = duration_sec - (result[-1]["start"] + result[-1]["duration"])
        result[-1]["duration"] = max(0.25, result[-1]["duration"] + drift)
    return result


class ObsSubtitleService:
    def __init__(self) -> None:
        self.enabled = _env_bool("CELIA_OBS_SUBTITLES_ENABLED", True)
        self.port = int(os.getenv("CELIA_OBS_SUBTITLE_PORT", "8788"))
        self.max_chars = int(os.getenv("CELIA_OBS_SUBTITLE_MAX_CHARS_PER_LINE", "42"))
        self.clear_after_sec = float(os.getenv("CELIA_OBS_SUBTITLE_CLEAR_AFTER_SEC", "0.4"))
        self.start_delay_ms = int(os.getenv("CELIA_OBS_SUBTITLE_START_DELAY_MS", "140"))
        # Strict synchronization: a generated/queued WAV is not evidence of
        # playback.  Keep fallback opt-in for diagnostics only; production waits
        # for the browser's audio-playback-started ACK.
        self.fallback_wait_sec = float(os.getenv("CELIA_OBS_SUBTITLE_FALLBACK_WAIT_SEC", "0"))
        self.default_theme = os.getenv("CELIA_OBS_SUBTITLE_THEME", "neuro")
        self._lock = threading.RLock()
        self._clients: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = False
        self._fallback_timers: dict[str, threading.Timer] = {}
        self._clear_timer: threading.Timer | None = None
        self._state: dict[str, Any] = {"type": "idle"}
        self._pending_states: dict[str, dict[str, Any]] = {}
        self._seq: int = 0

    def start_server(self) -> None:
        if not self.enabled or self._started:
            return
        self._started = True

        def run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            app = self._create_app()
            config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
            server = uvicorn.Server(config)
            logger.info(f"OBS subtitle overlay listening on http://127.0.0.1:{self.port}/obs_subtitles")
            self._loop.run_until_complete(server.serve())

        self._thread = threading.Thread(target=run, name="obs-subtitles", daemon=True)
        self._thread.start()

    def _create_app(self) -> FastAPI:
        app = FastAPI(title="Celia OBS Subtitles")

        @app.get("/obs_subtitles/fonts/chewy.woff2")
        async def chewy_font():
            font_path = _chewy_font_path()
            if font_path is None:
                raise FileNotFoundError("Chewy font not found")
            return FileResponse(str(font_path), media_type="font/ttf")

        @app.get("/obs_subtitles")
        async def page(theme: str | None = None) -> HTMLResponse:
            return HTMLResponse(self._html(theme or self.default_theme))

        @app.websocket("/obs_subtitles/ws")
        async def websocket_endpoint(websocket: WebSocket) -> None:
            await websocket.accept()
            with self._lock:
                self._clients.add(websocket)
                state = self._state.copy()
            await websocket.send_json(self._snapshot(state))
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            finally:
                with self._lock:
                    self._clients.discard(websocket)

        @app.get("/obs_subtitles/state")
        async def state() -> JSONResponse:
            with self._lock:
                return JSONResponse(self._snapshot(self._state.copy()))

        @app.post("/obs_subtitles/test")
        async def test() -> JSONResponse:
            speech_id = self.start_subtitle(
                "Teste da legenda da Celia com duas linhas e preenchimento progressivo no OBS.",
                duration_sec=5.0,
                speech_id="obs-test",
                wait_for_playback=False,
            )
            return JSONResponse({"ok": True, "speech_id": speech_id})

        return app

    def start_subtitle(
        self,
        text: str | None,
        *,
        audio_path: str | None = None,
        duration_sec: float | None = None,
        speech_id: str | None = None,
        wait_for_playback: bool = True,
    ) -> str | None:
        if not self.enabled or not text or not text.strip():
            return None
        subtitle_text = _sanitize_subtitle_text(text)
        if not subtitle_text:
            return None
        if duration_sec is None:
            if not audio_path:
                return None
            try:
                duration_sec = _duration_seconds(audio_path)
            except Exception as exc:
                logger.warning(f"OBS subtitle skipped; could not read duration for {audio_path}: {exc}")
                return None

        pages = _build_pages(subtitle_text, duration_sec, self.max_chars)
        if not pages:
            return None
        speech_id = speech_id or str(uuid.uuid4())
        now = time.time()
        with self._lock:
            self._seq += 1
            state = {
                "type": "subtitle",
                "status": "pending" if wait_for_playback else "playing",
                "speech_id": speech_id,
                "text": subtitle_text,
                "duration": duration_sec,
                "pages": pages,
                "created_at": now,
                "started_at": None if wait_for_playback else now,
                "seq": self._seq,
            }
            if wait_for_playback:
                self._pending_states[speech_id] = state
                self._arm_next_fallback_locked()
            else:
                self._cancel_timers_locked()
                self._pending_states.clear()
                self._state = state
                self._schedule_clear_locked(speech_id, duration_sec)
                self._broadcast_state(state.copy())
        return speech_id

    def mark_playback_started(self, speech_id: str | None) -> None:
        if not self.enabled or not speech_id:
            return
        with self._lock:
            pending = self._pending_states.pop(speech_id, None)
            if pending:
                self._state = pending
            elif self._state.get("speech_id") != speech_id:
                return
            if self._state.get("status") == "playing":
                return
            self._state["status"] = "playing"
            self._state["started_at"] = time.time()
            timer = self._fallback_timers.pop(speech_id, None)
            if timer:
                timer.cancel()
            self._schedule_clear_locked(speech_id, float(self._state.get("duration") or 0.1))
            self._arm_next_fallback_locked()
            state = self._state.copy()
        self._broadcast_state(state)

    def clear(self, speech_id: str | None = None, *, _from_playback_end: bool = False) -> None:
        with self._lock:
            if speech_id and self._state.get("speech_id") != speech_id:
                return
            if _from_playback_end:
                if self._clear_timer:
                    self._clear_timer.cancel()
                    self._clear_timer = None
            else:
                self._cancel_timers_locked()
                self._pending_states.clear()
            self._state = {"type": "idle"}
            state = self._state.copy()
        self._broadcast_state(state)

    def _fallback_start(self, speech_id: str) -> None:
        with self._lock:
            state = self._pending_states.pop(speech_id, None)
            if not state:
                return
            self._state = state
            self._state["status"] = "playing"
            self._state["started_at"] = time.time() + (self.start_delay_ms / 1000.0)
            self._fallback_timers.pop(speech_id, None)
            self._schedule_clear_locked(speech_id, float(self._state.get("duration") or 0.1))
            self._arm_next_fallback_locked()
            state = self._state.copy()
        self._broadcast_state(state)

    def _arm_next_fallback_locked(self) -> None:
        """Arm only the oldest pending fallback, after the current audio ends."""
        if self.fallback_wait_sec <= 0 or self._fallback_timers or not self._pending_states:
            return

        pending = min(self._pending_states.values(), key=lambda item: item["seq"])
        delay = self.fallback_wait_sec
        if self._state.get("type") == "subtitle" and self._state.get("status") == "playing":
            started_at = float(self._state.get("started_at") or time.time())
            duration = float(self._state.get("duration") or 0.1)
            delay += max(0.0, started_at + duration - time.time())

        timer = threading.Timer(delay, self._fallback_start, args=(pending["speech_id"],))
        timer.daemon = True
        self._fallback_timers[pending["speech_id"]] = timer
        timer.start()

    def _clear_after_playback(self, speech_id: str) -> None:
        self.clear(speech_id, _from_playback_end=True)

    def _schedule_clear_locked(self, speech_id: str, duration_sec: float) -> None:
        if self._clear_timer:
            self._clear_timer.cancel()
        delay = max(0.1, duration_sec + self.clear_after_sec + (self.start_delay_ms / 1000.0))
        self._clear_timer = threading.Timer(delay, self._clear_after_playback, args=(speech_id,))
        self._clear_timer.daemon = True
        self._clear_timer.start()

    def _cancel_timers_locked(self) -> None:
        for timer in self._fallback_timers.values():
            timer.cancel()
        self._fallback_timers.clear()
        if self._clear_timer:
            self._clear_timer.cancel()
        self._clear_timer = None

    def _snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("type") != "subtitle":
            return {"type": "idle", "server_time": time.time()}
        return {**state, "server_time": time.time()}

    def _broadcast_state(self, state: dict[str, Any]) -> None:
        if not self._loop or not self._clients:
            return
        payload = self._snapshot(state)
        asyncio.run_coroutine_threadsafe(self._broadcast(payload), self._loop)

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for client in list(self._clients):
            try:
                await client.send_json(payload)
            except Exception:
                dead.append(client)
        if dead:
            with self._lock:
                for client in dead:
                    self._clients.discard(client)

    def _html(self, theme: str) -> str:
        theme = html.escape(theme or self.default_theme)
        return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Celia OBS Subtitles</title>
<style>
@font-face {{
  font-family: "Chewy";
  src: url("/obs_subtitles/fonts/chewy.woff2") format("truetype");
  font-display: swap;
}}
/* Fonte local: Chewy no Windows (se instalada), fallback Arial */

:root {{
  --base: #ffffff;
  --fill-b: #a8ffff;
  --stroke: #13254e;
}}
body.clean {{ --fill-b:#ffd84d; }}
body.danger {{ --fill-b:#ff6b2c; }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{
  width: 1920px;
  height: 1080px;
  background: transparent;
  overflow: hidden;
}}
canvas {{
  position: absolute;
  bottom: 0;
  left: 0;
  opacity: 0;
  transition: opacity .12s linear;
}}
canvas.active {{ opacity: 1; }}
</style>
</head>
<body class="{theme}">
<canvas id="kara" width="1920" height="430"></canvas>
<script>
const canvas = document.getElementById("kara");
const ctx = canvas.getContext("2d");
const W = canvas.width;
const H = canvas.height;

const BASE_FONT_SIZE = 64;
const LINE_GAP = 88;
const BOTTOM_BASELINE = H - 38;
const MAX_STACK_LINES = 3;
const IDLE_CLEAR_SECONDS = 5.0;
const FONT_FAMILY = '"Chewy", "Arial Black", Arial, sans-serif';
const COLOR_FUTURE = "#FFFFFF";
const STROKE_COLOR = "#13254e";
const STROKE_WIDTH = 7;

let state = {{type:"idle"}};
let serverOffset = 0;
let stack = [];
let activeLineId = null;
let lastPageKey = "";
let idleSince = null;
let activeSpeechId = null;
let lastSeq = -1;
let lastRenderedSpeechId = null;

function notifyParent(eventName, payload = {{}}) {{
  if (window.parent === window) return;
  let targetOrigin = "*";
  try {{
    if (document.referrer) targetOrigin = new URL(document.referrer).origin;
  }} catch (_) {{ }}
  window.parent.postMessage({{
    channel: "celia-subtitle",
    event: eventName,
    ...payload,
  }}, targetOrigin);
}}

function cssVar(name, fallback) {{
  const value = getComputedStyle(document.body).getPropertyValue(name).trim();
  return value || fallback;
}}
function clamp(value, min, max) {{
  return Math.max(min, Math.min(max, value));
}}
function setFont(size) {{
  ctx.font = `bold ${{size}}px ${{FONT_FAMILY}}`;
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.direction = "ltr";
}}

function glyphsOf(text) {{
  return Array.from(text || "");
}}

function tokensOf(text) {{
  return (text || "").match(/\\S+|\\s+/g) || [];
}}

function easeOutBack(t) {{
  const c1 = 1.70158;
  const c3 = c1 + 1;
  return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
}}

function fitFontSize(text) {{
  let size = BASE_FONT_SIZE;
  setFont(size);
  const maxWidth = W * 0.90;
  while (ctx.measureText(text).width > maxWidth && size > 20) {{
    size -= 2;
    setFont(size);
  }}
  return size;
}}

function clearCanvas() {{
  ctx.clearRect(0, 0, W, H);
  canvas.classList.remove("active");
}}

function drawToken(text, x, y, width, color, scale, size) {{
  const colorStroke = cssVar("--stroke", STROKE_COLOR);
  ctx.save();
  ctx.translate(x + width / 2, y - size * 0.38);
  ctx.scale(scale, scale);
  ctx.translate(-(x + width / 2), -(y - size * 0.38));
  ctx.lineJoin = "round";
  ctx.lineWidth = STROKE_WIDTH;
  ctx.strokeStyle = colorStroke;
  ctx.strokeText(text, x, y);
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
  ctx.restore();
}}

function drawLine(text, progress, y) {{
  const colorDone = cssVar("--fill-b", "#a8ffff");
  const fontSize = fitFontSize(text);
  setFont(fontSize);
  const totalWidth = ctx.measureText(text).width;
  const xStart = (W - totalWidth) / 2;
  const tokens = tokensOf(text);
  const wordCount = Math.max(1, tokens.filter(token => token.trim()).length);
  const wordCursor = clamp(progress, 0, 1) * wordCount;
  let xCursor = xStart;
  let wordIndex = 0;

  for (const token of tokens) {{
    const tokenWidth = ctx.measureText(token).width;
    if (!token.trim()) {{
      xCursor += tokenWidth;
      continue;
    }}

    const tokenProgress = clamp(wordCursor - wordIndex, 0, 1);
    if (tokenProgress <= 0) {{
      drawToken(token, xCursor, y, tokenWidth, COLOR_FUTURE, 1, fontSize);
    }} else if (tokenProgress >= 1) {{
      drawToken(token, xCursor, y, tokenWidth, colorDone, 1, fontSize);
    }} else {{
      const pop = easeOutBack(tokenProgress);
      const scale = 0.72 + pop * 0.28;
      drawToken(token, xCursor, y, tokenWidth, colorDone, scale, fontSize);
    }}
    wordIndex++;
    xCursor += tokenWidth;
  }}
}}

function pushStackLine(id, text, startTime, duration) {{
  if (!text || stack.some(line => line.id === id)) return;
  // Tres linhas formam um bloco fechado. A quarta NAO substitui a primeira:
  // ela limpa o bloco inteiro e vira a primeira linha da proxima escadinha.
  if (stack.length >= MAX_STACK_LINES) stack = [];
  stack.push({{ id, text, startTime, duration }});
}}

function drawStack() {{
  ctx.clearRect(0, 0, W, H);
  const now = nowServer();
  if (!stack.length) {{
    canvas.classList.remove("active");
    return;
  }}
  canvas.classList.add("active");
  const visible = stack.slice(0, MAX_STACK_LINES);
  for (let index = 0; index < visible.length; index++) {{
    const line = visible[index];
    const y = BOTTOM_BASELINE - (visible.length - 1 - index) * LINE_GAP;
    const progress = line.id === activeLineId
      ? clamp((now - line.startTime) / Math.max(0.1, line.duration), 0, 1)
      : 1;
    drawLine(line.text, progress, y);
  }}
  if (activeSpeechId && lastRenderedSpeechId !== activeSpeechId) {{
    lastRenderedSpeechId = activeSpeechId;
    notifyParent("rendered", {{ speech_id: activeSpeechId }});
  }}
}}

function handleIdle() {{
  activeLineId = null;
  lastPageKey = "";
  if (stack.length) {{
    if (!idleSince) idleSince = nowServer();
    if (nowServer() - idleSince > IDLE_CLEAR_SECONDS) {{
      stack = [];
      clearCanvas();
    }} else {{
      drawStack();
    }}
  }} else {{
    if (!idleSince) idleSince = nowServer();
    if (nowServer() - idleSince > IDLE_CLEAR_SECONDS) {{
      clearCanvas();
    }}
  }}
}}

function nowServer() {{ return Date.now() / 1000 + serverOffset; }}
function render() {{
  if (!state || state.type !== "subtitle" || state.status !== "playing" || !state.started_at) {{
    handleIdle();
    requestAnimationFrame(render);
    return;
  }}
  const speechId = state.speech_id || "speech";
  if (speechId !== activeSpeechId) {{
    activeSpeechId = speechId;
    lastPageKey = "";
  }}
  idleSince = null;
  const elapsed = nowServer() - state.started_at;
  const pages = state.pages || [];
  let current = pages[pages.length - 1];
  for (const page of pages) {{
    if (elapsed < page.start + page.duration) {{ current = page; break; }}
  }}
  if (!current || elapsed > state.duration + 0.8) {{
    handleIdle();
  }} else {{
    const line = current.lines ? (current.lines[0] || "") : "";
    const pageKey = `${{speechId}}:${{current.start}}:${{line}}`;
    if (pageKey !== lastPageKey) {{
      lastPageKey = pageKey;
      pushStackLine(pageKey, line, state.started_at + current.start, current.duration);
    }}
    activeLineId = pageKey;
    drawStack();
  }}
  requestAnimationFrame(render);
}}
function connect() {{
  if (!("WebSocket" in window)) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${{proto}}://${{location.host}}/obs_subtitles/ws`);
  ws.onopen = () => notifyParent("ws", {{ connected: true }});
  ws.onmessage = event => {{
    const msg = JSON.parse(event.data);
    if (msg.seq !== undefined && msg.seq < lastSeq) return;
    if (msg.seq !== undefined) lastSeq = msg.seq;
    state = msg;
    if (state.server_time) serverOffset = state.server_time - Date.now() / 1000;
    notifyParent("state", {{
      state: {{
        type: state.type || "idle",
        status: state.status || "",
        speech_id: state.speech_id || "",
        text: state.text || "",
        duration: Number(state.duration || 0),
        started_at: state.started_at || null,
        server_time: state.server_time || null,
        seq: state.seq,
      }},
    }});
  }};
  ws.onclose = () => {{
    notifyParent("ws", {{ connected: false }});
    setTimeout(connect, 500);
  }};
}}
const DEMO_LINES = [
  "Teste da legenda da Celia no OBS.",
  "A nova frase entra embaixo.",
  "As antigas vao subindo juntas.",
  "No maximo quatro linhas aparecem.",
  "A quinta limpa o bloco e recomeca de uma.",
  "A sexta sobe junto com a quinta.",
  "A setima continua a nova escadinha.",
  "A oitava fecha o segundo bloco.",
  "A nona limpa tudo e comeca outra vez.",
];
let demoIndex = 0;

function demoState() {{
  const text = DEMO_LINES[demoIndex % DEMO_LINES.length];
  demoIndex++;
  const glyphs = glyphsOf(text);
  const duration = 1.45;
  return {{
    type: "subtitle",
    status: "playing",
    speech_id: `demo-${{demoIndex}}`,
    duration,
    started_at: nowServer(),
    pages: [{{
      lines: [text, ""],
      start: 0,
      duration,
      chars: glyphs.map((text, index) => ({{
        text,
        start: index * duration / Math.max(1, glyphs.length),
        duration: duration / Math.max(1, glyphs.length),
      }})),
    }}],
  }};
}}
notifyParent("ready", {{ version: 1 }});
if (new URLSearchParams(location.search).get("demo") === "1") {{
  state = demoState();
  setInterval(() => {{ state = demoState(); }}, 1700);
}} else {{
  connect();
}}
render();
</script>
</body>
</html>"""


obs_subtitles = ObsSubtitleService()
