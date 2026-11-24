import os
import json
import asyncio
import time
from uuid import uuid4
import numpy as np
from datetime import datetime
from fastapi import APIRouter, WebSocket, UploadFile, File, Response, Request
from starlette.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect
from loguru import logger
from .service_context import ServiceContext
from .websocket_handler import WebSocketHandler
from .proxy_handler import ProxyHandler
from .live_mode import gate as live_gate
from .core.obs_subtitles import obs_subtitles
from .core.track_emotion_client import start_lipsync_envelope, publish_emotion
from .core.orchestrator import orchestrator
from .core.cognitive_loop import cognitive_loop
from .core.continuous_behavior import continuous_behavior
from .core import direct_address
from .core.speech_coordinator import speech_coordinator
from .core.training_trace import training_trace
from .core.latency_trace import latency_trace
from .core.flow_ab_probe import flow_ab_probe
from .core.local_brain import local_brain_runtime
from .core.interruption_intent import is_explicit_roteiro_stop
from .core.profile_switch_intent import apply_profile_switch, detect_profile_switch_intent
from .core.hud_intent import detect_hud_intent
from .core.minecraft_series import get_series_center, verified_report_reply, run_in_background as run_series_command
from .possession_routes import dispatch_local_owner_body_command, mirror_owner_text as possession_mirror
from .core.asr_normalization import (
    evaluate_asr_acoustic_gate,
    is_protected_celia_call,
    normalize_asr_transcript,
)
from .core.asr_fragment_scheduler import AsrFragmentScheduler
from .core.asr_turn_gate import (
    AsrTurnAssembler,
    AsrTurnFragment,
    AsrTurnGateResult,
    has_language_shape_mismatch,
    has_measured_suspicious_asr,
    secondary_min_similarity,
    secondary_verification_reason,
    transcript_similarity,
)

# ── Pagina HTML de Presets (Celia) ──────────────────────────────────────
PRESETS_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>Celia - Voz</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#0a0a0f;color:#eee;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:20px;gap:24px}
h1{font-size:1.6rem;font-weight:600;color:#fff;margin-bottom:4px}
.status{font-size:.85rem;color:#888;margin-bottom:16px}
.cards{display:flex;gap:14px;flex-wrap:wrap;justify-content:center;max-width:700px}
.card{border-radius:16px;padding:28px 22px;width:200px;cursor:pointer;text-align:center;transition:all .2s;border:2px solid transparent;background:#14141f;display:flex;flex-direction:column;gap:8px}
.card:hover{transform:translateY(-2px)}
.card.active{border-color:#fff;box-shadow:0 0 24px rgba(255,255,255,.15)}
.card .name{font-size:1.2rem;font-weight:600;margin-bottom:2px}
.card .desc{font-size:.75rem;color:#999;line-height:1.4}
.card .params{font-size:.65rem;color:#666;margin-top:4px;font-family:monospace}
.card.natural .name{color:#4ade80}
.card.natural.active{background:#0f1a10;border-color:#4ade80;box-shadow:0 0 24px rgba(74,222,128,.25)}
.card.expressivo .name{color:#facc15}
.card.expressivo.active{background:#1a180f;border-color:#facc15;box-shadow:0 0 24px rgba(250,204,21,.25)}
.card.controlado .name{color:#f87171}
.card.controlado.active{background:#1a0f0f;border-color:#f87171;box-shadow:0 0 24px rgba(248,113,113,.25)}
.toast{position:fixed;bottom:40px;background:#1a1a2e;color:#a0a0b0;padding:12px 24px;border-radius:10px;font-size:.85rem;opacity:0;transition:opacity .3s;pointer-events:none}
.toast.show{opacity:1}
@media(max-width:480px){.card{width:160px;padding:20px 14px}.card .name{font-size:1rem}}
</style>
</head>
<body>
<h1>Celia Voz</h1>
<div class="status" id="status">preset ativo: carregando...</div>
<div class="cards" id="cards"></div>
<div class="toast" id="toast"></div>
<script>
const API = location.origin;
let activeKey = '';

const presets = {
  var6: {key:'var6', name:'Natural', cls:'natural', desc:'Calma, natural, sem trava. Conversa e narracao.', params:'temp=0.85 top_k=60 top_p=0.92 rep=1.05'},
  var3: {key:'var3', name:'Expressivo', cls:'expressivo', desc:'Solta, emotiva. Ideal pra cenas emotivas.', params:'temp=0.88 top_k=55 top_p=0.90 rep=1.00'},
  var1: {key:'var1', name:'Controlado', cls:'controlado', desc:'Seria, limpa sem eco. Tensao e drama.', params:'temp=0.72 top_k=45 top_p=0.80 rep=1.20'}
};

function render() {
  const c = document.getElementById('cards');
  c.innerHTML = Object.values(presets).map(p =>
    '<div class="card '+p.cls+' '+(p.key===activeKey?'active':'')+'" data-key="'+p.key+'">'+
      '<div class="name">'+p.name+'</div>'+
      '<div class="desc">'+p.desc+'</div>'+
      '<div class="params">'+p.params+'</div>'+
    '</div>'
  ).join('');
  document.querySelectorAll('.card').forEach(el => {
    el.onclick = () => setPreset(el.dataset.key);
  });
  if (activeKey) {
    document.getElementById('status').textContent = 'preset ativo: '+presets[activeKey].name;
  }
}

async function loadState() {
  try {
    const r = await fetch(API+'/tts/preset');
    const d = await r.json();
    activeKey = d.active;
  } catch(e) { activeKey = 'var6'; }
  render();
}

async function setPreset(key) {
  try {
    const r = await fetch(API+'/tts/preset', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({preset: key})
    });
    const d = await r.json();
    if (d.error) { showToast('erro: '+d.error); return; }
    activeKey = d.active;
    render();
    showToast(presets[key].name+' ativado!');
  } catch(e) { showToast('erro de conexao'); }
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2000);
}

loadState();
</script>
</body>
</html>"""


def init_client_ws_route(
    default_context_cache: ServiceContext,
    singleton=None,  # CeliaSingleton opcional — ativa modo singleton
) -> APIRouter:
    """
    Cria e retorna as rotas WebSocket para `/client-ws`.

    Se `singleton` for fornecido, usa o modo singleton (uma Célia compartilhada,
    broadcast total, histórico persistente).

    Se `singleton` for None, usa o modo legado (uma Célia por conexão).

    Query params suportados (modo singleton):
        role=browser  → pode interromper a Célia
        role=source   → só envia inputs (padrão)
    """
    router = APIRouter()

    @router.get("/debug/local-brain-config")
    async def local_brain_config_runtime():
        return JSONResponse(local_brain_runtime.brain.runtime_config_snapshot())

    from .core.speech_activity import SpeechActivity
    visual_speech = SpeechActivity()
    from .core.microphone_gate import MicrophoneGate
    microphone_gate = MicrophoneGate()
    microphone_clients = {}

    async def publish_microphone_state():
        for uid, ws in list(microphone_clients.items()):
            try:
                await ws.send_json(microphone_gate.snapshot(uid))
            except Exception:
                pass
    logger.info("[ASR PIPELINE] deterministic-chat-20260921; refinement=disabled")

    if singleton is not None:
        # ── MODO SINGLETON ────────────────────────────────────────────────
        @router.websocket("/client-ws")
        async def websocket_singleton_endpoint(websocket: WebSocket):
            """WebSocket endpoint no modo singleton"""
            await websocket.accept()
            client_uid = str(uuid4())

            # Input authority and media playback ownership are independent.
            role = websocket.query_params.get("role", "browser")
            if role not in ("browser", "source"):
                role = "browser"
            playback_client = websocket.query_params.get("playback_client", "browser")
            if playback_client not in ("browser", "pet", "scene"):
                playback_client = "browser"

            logger.info(
                f"Nova conexão singleton: {client_uid} role={role} "
                f"playback={playback_client}"
            )

            # Buffer de áudio por cliente (mic-audio-data)
            audio_buffer = np.array([], dtype=np.float32)
            capture_revision = microphone_gate.revision

            try:
                from .message_handler import message_handler
                from .chat_history_manager import (
                    get_history_list,
                    get_history,
                )
                from .memory_commands import (
                    handle_memory_book_list,
                    handle_memory_book_edit,
                    handle_memory_book_delete,
                    handle_memory_book_clear,
                    handle_memory_book_compact,
                    handle_memory_book_purge_trivial,
                    handle_delete_history,
                )

                # Registra no singleton e envia estado inicial
                await singleton.register(client_uid, websocket, role, playback_client)
                await singleton.send_initial_state(websocket)
                if role == "browser":
                    microphone_gate.register(client_uid, playback_client)
                    microphone_clients[client_uid] = websocket

                # SINCRONIA: Envia o histórico atual para o novo cliente
                # assim ele já "entra" na conversa que está rolando sem criar uma nova.
                ctx = singleton.context
                if ctx and ctx.history_uid:
                    # 1. Envia a lista para alimentar o sidebar
                    histories = get_history_list(ctx.character_config.conf_uid)
                    current_uid = ctx.history_uid
                    if current_uid not in {h["uid"] for h in histories}:
                        histories.insert(0, {"uid": current_uid, "latest_message": None, "timestamp": None})
                    await websocket.send_text(json.dumps({"type": "history-list", "histories": histories}))

                    # 2. Envia as mensagens atuais para o chat
                    messages = [
                        msg for msg in get_history(ctx.character_config.conf_uid, current_uid)
                        if msg["role"] != "system"
                    ]
                    await websocket.send_text(json.dumps({"type": "history-data", "messages": messages}))
                    logger.info(f"🔄 Cliente {client_uid} sincronizado com histórico {current_uid}")

                # Queue for messages to be processed
                message_queue = asyncio.Queue()
                connected_at = time.monotonic()
                ignored_auto_history_create = False
                last_client_activity_at = time.monotonic()
                partial_asr_task = None
                partial_asr_next_at = 0.0
                partial_asr_sequence = 0
                active_utterance_id = ""
                active_utterance_started_at = 0.0
                active_vad_source = "unknown"
                active_client_hint = playback_client
                overlap_audio_snapshots: dict[str, dict] = {}
                asr_turn_assembler = AsrTurnAssembler(
                    merge_window_ms=max(100, int(os.getenv("CELIA_ASR_MERGE_WINDOW_MS", "1400"))),
                    reject_below_ms=max(0, int(os.getenv("CELIA_ASR_TURN_REJECT_BELOW_MS", "180"))),
                    hold_below_ms=max(0, int(os.getenv("CELIA_ASR_TURN_HOLD_BELOW_MS", "350"))),
                    continuation_window_ms=max(100, int(os.getenv("CELIA_ASR_CONTINUATION_WINDOW_MS", "3000"))),
                )
                # Fragmento pendente vence POR TEMPO (janela de continuação medida: 3,0 s), sem depender de outra fala: a mensagem original (com o áudio)
                # fica guardada e, no prazo, volta para a fila do processador com ``_release_pending`` (só pula a decisão do montador; o resto do
                # caminho é o de sempre). Parada urgente cancela o pendente; um id de fala nunca vira dois turnos (ver ``AsrTurnAssembler``).
                asr_replay_messages: dict[str, dict] = {}

                def _asr_release_by_time(result: AsrTurnGateResult) -> None:
                    replay = asr_replay_messages.pop(result.fragment.utterance_id, None) if result.fragment is not None else None
                    if replay is None:
                        return
                    replay["_release_pending"] = True
                    message_queue.put_nowait(replay)
                    logger.info("[ASR TURN] fragmento liberado por tempo id={} text={!r}", result.fragment.utterance_id or "-", result.fragment.text[:160])

                def _asr_expire(result: AsrTurnGateResult) -> None:
                    if result.fragment is not None:
                        asr_replay_messages.pop(result.fragment.utterance_id, None)

                asr_scheduler = AsrFragmentScheduler(
                    asr_turn_assembler,
                    on_release=_asr_release_by_time,
                    on_expire=_asr_expire,
                )

                def mic_speaker_for_context(context):
                    return os.getenv("CELIA_MIC_SPEAKER_NAME", "").strip() or (
                        context.character_config.human_name
                        if context and context.character_config
                        else ""
                    )

                async def emit_asr_trace(
                    utterance_id,
                    decision,
                    *,
                    stage,
                    raw_text="",
                    corrected_text="",
                    rejected=False,
                    uncertain=False,
                    reason="",
                    rules=(),
                    acoustic=None,
                    route_status="",
                    submitted_to_brain=False,
                ):
                    if live_gate.owner_input_blocked():  # modo Live: a fala do dono nao vira legenda/trace/transmissao
                        live_gate.note_mic_blocked()
                        return
                    canonical_event = stage == "post_asr"
                    event_type = "user-input-transcription" if canonical_event else "asr-trace"
                    if rejected:
                        canonical_decision = "REJECT"
                    elif decision == "NORMALIZED":
                        canonical_decision = "CORRECT"
                    elif canonical_event:
                        canonical_decision = "KEEP"
                    else:
                        canonical_decision = ""
                    payload = {
                        "type": event_type,
                        "source": "microphone",
                        "utterance_id": utterance_id,
                        "text": corrected_text,
                        "raw_text": raw_text,
                        "corrected_text": corrected_text,
                        "asr_stage": stage,
                        "asr_event": decision,
                        "asr_decision": canonical_decision,
                        "asr_normalization_rules": list(rules or ()),
                        "asr_rejected": bool(rejected),
                        "asr_reject_reason": reason,
                        "asr_refinement_status": "DISABLED",
                        "asr_uncertain": bool(uncertain),
                        "asr_route_status": route_status,
                        "asr_submitted_to_brain": bool(submitted_to_brain),
                    }
                    if acoustic is not None:
                        payload["asr_acoustic"] = acoustic
                    await singleton.broadcast(payload)
                    logger.info(
                        "[ASR TRACE] id={} stage={} decision={} reason={} route={} submitted={}",
                        utterance_id or "-",
                        stage,
                        decision,
                        reason or "-",
                        route_status or "-",
                        submitted_to_brain,
                    )

                async def emit_partial_asr(audio_snapshot, utterance_id, sequence, speaker):
                    """One debounced ASR revision for the current microphone utterance."""
                    context = singleton.context
                    if not context or not context.asr_engine:
                        return
                    if live_gate.owner_input_blocked():
                        return
                    revision = capture_revision
                    if not microphone_gate.accepts(revision, client_uid):
                        return
                    started = time.monotonic()
                    try:
                        transcript = await context.asr_engine.async_transcribe_np(audio_snapshot)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.debug("partial ASR unavailable: %s", type(exc).__name__)
                        return
                    if not microphone_gate.accepts(revision, client_uid):
                        return
                    if not transcript or not transcript.strip() or utterance_id != active_utterance_id:
                        return
                    if continuous_behavior.probable_self_echo(transcript):
                        logger.info(
                            f"🔇 eco parcial da própria Célia suprimido: {transcript[:70]!r}"
                        )
                        return
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    continuous_behavior.observe(
                        "speech.partial",
                        source="microphone",
                        speaker=speaker,
                        speaker_role="host",
                        speaker_confidence=1.0,
                        text=transcript,
                        duration_ms=elapsed_ms,
                        utterance_id=utterance_id,
                        partial_sequence=sequence,
                    )
                    # A partial "Celia, para" may still become "para mim ...".
                    if continuous_behavior.profile != "roteiro":
                        await handle_human_interruption(transcript)
                    await singleton.broadcast({
                        "type": "asr-partial",
                        "utterance_id": utterance_id,
                        "revision": sequence,
                        "text": transcript,
                    })

                async def cancel_active_browser_playback(reason):
                    speech_id = continuous_behavior.request_playback_cancel(reason)
                    if not speech_id:
                        return False
                    await singleton.broadcast({
                        "type": "playback.cancel",
                        "speech_id": speech_id,
                        "reason": reason,
                    })
                    return True

                async def handle_human_interruption(transcript):
                    """Yield only after transcript evidence distinguishes speech from a backchannel."""
                    # Recording is walkie-talkie style: ordinary mic/VAD speech never
                    # cuts a scripted Célia line. Explicit stop/hotkey paths live outside
                    # this automatic ASR handler and remain available.
                    if continuous_behavior.profile == "roteiro":
                        if not is_explicit_roteiro_stop(transcript):
                            logger.debug("[Roteiro] fala comum ignorada como interrupcao: %r", transcript[:80])
                            return False
                        logger.info("[Roteiro] comando explicito de corte aceito: %r", transcript[:80])
                        await cancel_active_browser_playback("explicit_voice_stop")
                        continuous_behavior.interrupt("persistent_interruption")
                        await singleton.interrupt(transcript, role="browser", force=True)
                        return True
                    kind = continuous_behavior.classify_interruption(transcript)
                    if kind in {"noise", "backchannel"}:
                        continuous_behavior.interrupt(kind)
                        return False
                    if (
                        not continuous_behavior.active_speech_id
                        or continuous_behavior.playback_state != "playing"
                    ):
                        return False
                    if not continuous_behavior.interrupt(kind):
                        return False
                    await cancel_active_browser_playback("human_interruption")
                    await singleton.interrupt(transcript, role="browser", force=True)
                    return True

                # Cleanup de zombies: monitoramos o heartbeat do frontend (a cada 10s).
                # Se não recebermos nada por ZOMBIE_TIMEOUT_S, matamos a conexão.
                # Conexoes role="source" (alert_watcher, game_mind) não têm timeout
                # de inatividade, ficam vivas enquanto o processo existir.
                ZOMBIE_TIMEOUT_S = 25.0
                SERVER_PING_INTERVAL_S = 10.0

                async def receiver():
                    """Task to receive messages from WebSocket and handle heartbeats/interrupts immediately"""
                    nonlocal audio_buffer, ignored_auto_history_create, last_client_activity_at
                    nonlocal capture_revision
                    nonlocal partial_asr_task, partial_asr_next_at, partial_asr_sequence, active_utterance_id
                    nonlocal active_utterance_started_at, active_vad_source, active_client_hint
                    try:
                        while True:
                            data = await websocket.receive_json()
                            last_client_activity_at = time.monotonic()
                            msg_type = data.get("type", "")
                            
                            # Shared desktop microphone gate: browser and Electron Pet
                            # have separate JS contexts/storage, but one server authority.
                            if msg_type == "microphone-state-get":
                                await websocket.send_json(microphone_gate.snapshot(client_uid))
                                continue
                            if msg_type == "microphone-state-set":
                                if role != "browser" or type(data.get("enabled")) is not bool:
                                    await websocket.send_json(microphone_gate.snapshot(client_uid))
                                    continue
                                microphone_gate.set_enabled(data["enabled"])
                                logger.info("[MIC] enabled={} revision={} client={}",
                                            microphone_gate.enabled, microphone_gate.revision, client_uid)
                                await publish_microphone_state()
                                continue
                            if capture_revision != microphone_gate.revision:
                                audio_buffer = np.array([], dtype=np.float32)
                                overlap_audio_snapshots.clear()
                                active_utterance_id = ""
                                active_utterance_started_at = 0.0
                                if partial_asr_task and not partial_asr_task.done():
                                    partial_asr_task.cancel()
                                capture_revision = microphone_gate.revision
                            if msg_type in {"speech.started", "mic-audio-data", "mic-audio-end"}:
                                if not microphone_gate.enabled or not microphone_gate.client_allowed(client_uid):
                                    continue
                                data["_mic_revision"] = microphone_gate.revision

                            # IMMEDIATE HANDLE: Heartbeats should never wait in queue
                            if msg_type == "audio-lifecycle":
                                from .core.audio_lifecycle import record_audio_lifecycle
                                record_audio_lifecycle(data)
                                lifecycle_event = str(data.get("event") or "")
                                if lifecycle_event == "AUDIO_STARTED":
                                    singleton.report_playback_started(client_uid)
                                elif lifecycle_event == "AUDIO_CANCELLED" and data.get("reason") == "lane_claim_timeout":
                                    singleton.report_playback_failure(client_uid)
                                continue

                            if msg_type == "heartbeat":
                                try:
                                    await websocket.send_json({"type": "heartbeat-ack"})
                                except Exception as exc:
                                    logger.warning(
                                        f"heartbeat ack failed for {client_uid}: {exc}"
                                    )
                                    raise WebSocketDisconnect(code=1006)
                                continue
                            
                            # IMMEDIATE HANDLE: Interrupt signals should be as fast as possible.
                            # In Roteiro this browser message is an automatic VAD signal,
                            # not an explicit owner stop command, so it must not cut playback.
                            if msg_type == "interrupt-signal":
                                heard = data.get("text", "")
                                if continuous_behavior.profile == "roteiro":
                                    logger.debug("[Roteiro] auto interrupt-signal ignored during recording")
                                    continue
                                interrupted = continuous_behavior.interrupt(
                                    continuous_behavior.classify_interruption(heard)
                                )
                                if interrupted:
                                    await cancel_active_browser_playback("interrupt_signal")
                                    await singleton.interrupt(heard, role=role, force=True)
                                continue

                            if msg_type == "screen.changed":
                                try:
                                    relevance = float(data.get("relevance", 0.0))
                                except (TypeError, ValueError):
                                    relevance = 0.0
                                screen_result = continuous_behavior.screen_changed(relevance=relevance)
                                if screen_result.get("action") == "microaction":
                                    try:
                                        publish_emotion(
                                            screen_result["emotion"],
                                            source="screen_change",
                                            priority=35,
                                            ttl_sec=1.5,
                                            lock_sec=0,
                                        )
                                    except Exception as exc:
                                        logger.debug("screen microaction unavailable: %s", type(exc).__name__)
                                continue
                            if msg_type == "audio-playback-started":
                                speech_id = str(data.get("speech_id") or "").strip()
                                if not singleton.is_playback_owner(client_uid):
                                    logger.info(
                                        "playback-start ignored from non-owner: "
                                        f"client={client_uid} speech_id={speech_id}"
                                    )
                                    continue
                                player_id = str(data.get("player_id") or "").strip()
                                if not visual_speech.start(speech_id, client_uid, player_id):
                                    continue
                                singleton.set_playback_status(speech_id, "playing")
                                speech_coordinator.mark_playing(speech_id)
                                trace_correlation = latency_trace.speech_correlation(speech_id)
                                canonical_turn_id = (
                                    continuous_behavior.turn_id_for_speech(speech_id)
                                    or str(trace_correlation.get("turn_id") or "")
                                )
                                claimed_turn_id = str(data.get("turn_id") or "").strip()
                                correlated_turn_id = canonical_turn_id or claimed_turn_id
                                canonical_phrase_index = trace_correlation.get("phrase_index")
                                try:
                                    claimed_phrase_index = (
                                        int(data.get("phrase_index"))
                                        if data.get("phrase_index") is not None
                                        else None
                                    )
                                except (TypeError, ValueError):
                                    claimed_phrase_index = None
                                phrase_index = (
                                    canonical_phrase_index
                                    if canonical_phrase_index is not None
                                    else claimed_phrase_index
                                )
                                latency_trace.mark(
                                    correlated_turn_id,
                                    "playback_started",
                                    speech_id=speech_id,
                                    phrase_index=phrase_index,
                                    claimed_phrase_index=claimed_phrase_index,
                                    canonical_phrase_index=canonical_phrase_index,
                                    player_id=player_id,
                                    claimed_turn_id=claimed_turn_id,
                                    canonical_turn_id=canonical_turn_id,
                                    correlation="speech_id_authoritative" if canonical_turn_id else "frontend_claim_only",
                                )
                                continuous_behavior.observe(
                                    "celia.tts_started",
                                    utterance_id=speech_id,
                                    turn_id=correlated_turn_id,
                                )
                                obs_subtitles.mark_playback_started(speech_id)
                                await singleton.broadcast({
                                    "type": "audio-playback-started", "speech_id": speech_id,
                                    "player_id": player_id,
                                })
                                try:
                                    result = start_lipsync_envelope(speech_id)
                                    if not result.get("ok"):
                                        logger.debug(
                                            "lipsync envelope not started: "
                                            f"speech_id={speech_id} result={result}"
                                        )
                                    else:
                                        logger.info(
                                            "lipsync envelope started: "
                                            f"speech_id={speech_id}"
                                        )
                                except Exception as exc:
                                    logger.debug(
                                        f"lipsync envelope start failed for {speech_id}: {exc}"
                                    )
                                continue

                            if msg_type == "external-playback-complete":
                                if not singleton.is_playback_owner(client_uid):
                                    logger.info(
                                        "external playback ACK ignored from non-owner: "
                                        f"client={client_uid} speech_id={data.get('speech_id', '')}"
                                    )
                                    continue
                                singleton.resolve_codex_playback_ack(
                                    speech_id=data.get("speech_id", ""),
                                    status=data.get("status", "ended"),
                                )
                                continue

                            if msg_type == "speech.started":
                                new_utterance_id = str(data.get("utterance_id") or "").strip()
                                if (
                                    active_utterance_id
                                    and new_utterance_id
                                    and new_utterance_id != active_utterance_id
                                ):
                                    overlap_audio_snapshots[active_utterance_id] = {
                                        "audio": audio_buffer.copy(),
                                        "started_at": active_utterance_started_at,
                                        "vad_source": active_vad_source,
                                        "client_hint": active_client_hint,
                                    }
                                    logger.warning(
                                        "[ASR] overlapping speech.started preserved old={} new={} samples={}",
                                        active_utterance_id,
                                        new_utterance_id,
                                        len(audio_buffer),
                                    )
                                active_utterance_id = new_utterance_id
                                active_utterance_started_at = time.monotonic()
                                active_vad_source = str(data.get("vad_source") or "unknown").strip() or "unknown"
                                active_client_hint = str(data.get("client_hint") or playback_client).strip() or playback_client
                                partial_asr_sequence = 0
                                partial_asr_next_at = time.monotonic() + 1.1
                                audio_buffer = np.array([], dtype=np.float32)
                                logger.info(
                                    "[ASR] utterance start "
                                    f"id={active_utterance_id or '-'} client={client_uid} "
                                    f"playback={playback_client} hint={active_client_hint} "
                                    f"vad={active_vad_source}"
                                )
                                context = singleton.context
                                continuous_behavior.observe(
                                    "speech.started",
                                    source="microphone",
                                    speaker=mic_speaker_for_context(context),
                                    speaker_role="host",
                                    speaker_confidence=1.0,
                                    utterance_id=active_utterance_id,
                                )
                                disposable_speech_id = speech_coordinator.human_started()
                                if (
                                    disposable_speech_id
                                    and disposable_speech_id == continuous_behavior.active_speech_id
                                ):
                                    await cancel_active_browser_playback(
                                        "human_started_over_spontaneous_speech"
                                    )
                                # The existing utterance id is also the recording
                                # take boundary. No audio/transcript is duplicated;
                                # Roteiro only records which take owns this capture.
                                # `profile` deixa a faixa de voz do browser esperar o
                                # humano; em "roteiro" (e sem o campo) ela nunca segura.
                                await singleton.broadcast({
                                    "type": "speech.started",
                                    "source": "microphone",
                                    "utterance_id": active_utterance_id,
                                    "profile": continuous_behavior.profile,
                                })
                                continue

                            if msg_type == "audio-playback-finished":
                                speech_id = str(data.get("speech_id") or "").strip()
                                if not singleton.is_playback_owner(client_uid):
                                    logger.info(
                                        "playback-finish ignored from non-owner: "
                                        f"client={client_uid} speech_id={speech_id}"
                                    )
                                    continue
                                player_id = str(data.get("player_id") or "").strip()
                                if visual_speech.finish(speech_id, client_uid, player_id):
                                    playback_status = str(data.get("status") or "ended")
                                    singleton.resolve_playback_wait(speech_id, playback_status)
                                    speech_coordinator.mark_finished(speech_id, playback_status)
                                    continuous_behavior.observe("celia.tts_finished", utterance_id=speech_id)
                                    await singleton.broadcast({
                                        "type": "audio-playback-finished", "speech_id": speech_id,
                                        "player_id": player_id,
                                        "status": playback_status,
                                    })
                                continue

                            if msg_type == "frontend-playback-cancelled":
                                speech_id = str(data.get("speech_id") or "").strip()
                                if not singleton.is_playback_owner(client_uid):
                                    logger.info(
                                        "playback-cancel ignored from non-owner: "
                                        f"client={client_uid} speech_id={speech_id}"
                                    )
                                    continue
                                player_id = str(data.get("player_id") or "").strip()
                                cancelled = visual_speech.finish(speech_id, client_uid, player_id)
                                # The playback owner is already authenticated by
                                # the server-side ownership check above, and this
                                # speech/player pair was accepted by visual_speech.
                                # Apply STOP synchronously; never queue it behind a turn.
                                owner_cancel_ids = (
                                    singleton.stop_playback_for_owner(client_uid)
                                    if cancelled else []
                                )
                                continuous_behavior.playback_cancelled(speech_id)
                                speech_coordinator.mark_finished(speech_id, "cancelled")
                                obs_subtitles.clear(speech_id)
                                if cancelled:
                                    singleton.resolve_playback_wait(speech_id, "cancelled")
                                for pending_speech_id in owner_cancel_ids:
                                    if pending_speech_id != speech_id:
                                        singleton.resolve_playback_wait(
                                            pending_speech_id, "cancelled"
                                        )
                                        await singleton.broadcast({
                                            "type": "playback.cancel",
                                            "speech_id": pending_speech_id,
                                            "reason": "authenticated_owner_stop",
                                        })
                                await singleton.broadcast({
                                    "type": "frontend-playback-cancelled", "speech_id": speech_id,
                                })
                                continue

                            if singleton.codex_playback_active and msg_type in (
                                "text-input",
                                "mic-audio-data",
                                "mic-audio-end",
                            ):
                                audio_buffer = np.array([], dtype=np.float32)
                                logger.info(
                                    "suppressed_during_codex_playback: "
                                    f"type={msg_type} role={role}"
                                )
                                continue

                            # O microfone continua capturando durante geração/playback.
                            # Auto-eco é filtrado pelo transcript contra o speech_id/texto
                            # realmente em reprodução; fala diferente do Criador permanece audível.

                            # IMMEDIATE HANDLE: Audio data collection (fast)
                            if msg_type == "mic-audio-data":
                                chunk = data.get("audio", [])
                                if chunk:
                                    audio_buffer = np.append(
                                        audio_buffer,
                                        np.array(chunk, dtype=np.float32),
                                    )
                                    context = singleton.context
                                    sample_rate = getattr(getattr(context, "asr_engine", None), "SAMPLE_RATE", 16000) or 16000
                                    now = time.monotonic()
                                    if (
                                        active_utterance_id
                                        and len(audio_buffer) >= int(sample_rate * 0.9)
                                        and now >= partial_asr_next_at
                                        and (partial_asr_task is None or partial_asr_task.done())
                                    ):
                                        partial_asr_sequence += 1
                                        partial_asr_next_at = now + 1.1
                                        partial_asr_task = asyncio.create_task(
                                            emit_partial_asr(
                                                audio_buffer.copy(),
                                                active_utterance_id,
                                                partial_asr_sequence,
                                                mic_speaker_for_context(context),
                                            )
                                        )
                                continue

                            # Sela o áudio no receiver antes de enfileirar o fim. Isso evita
                            # que um novo speech.started apague o buffer anterior enquanto o ASR
                            # ainda está processando a utterance recém-finalizada (ex.: corte máx.).
                            if msg_type == "mic-audio-end":
                                ending_utterance_id = str(
                                    data.get("utterance_id") or active_utterance_id or ""
                                ).strip()
                                preserved = overlap_audio_snapshots.pop(
                                    ending_utterance_id, None
                                )
                                if preserved is not None:
                                    data["_audio_snapshot"] = preserved["audio"]
                                    preserved_started_at = float(
                                        preserved.get("started_at") or 0.0
                                    )
                                    data["_capture_elapsed_ms"] = (
                                        int(
                                            max(
                                                0.0,
                                                time.monotonic() - preserved_started_at,
                                            )
                                            * 1000
                                        )
                                        if preserved_started_at
                                        else 0
                                    )
                                    data["_start_vad_source"] = preserved.get(
                                        "vad_source", "unknown"
                                    )
                                    data["_start_client_hint"] = preserved.get(
                                        "client_hint", playback_client
                                    )
                                    logger.info(
                                        "[ASR] sealed preserved overlap id={} samples={}",
                                        ending_utterance_id,
                                        len(data["_audio_snapshot"]),
                                    )
                                else:
                                    data["_audio_snapshot"] = audio_buffer.copy()
                                    data["_capture_elapsed_ms"] = (
                                        int(max(0.0, time.monotonic() - active_utterance_started_at) * 1000)
                                        if active_utterance_started_at else 0
                                    )
                                    data["_start_vad_source"] = active_vad_source
                                    data["_start_client_hint"] = active_client_hint
                                    if ending_utterance_id == active_utterance_id:
                                        audio_buffer = np.array([], dtype=np.float32)
                                data["_capture_ended_at"] = time.monotonic()

                            # Put everything else (text-input, mic-audio-end, etc) in queue
                            await message_queue.put(data)
                    except WebSocketDisconnect:
                        # Signal processor to stop
                        await message_queue.put(None)
                        raise
                    except Exception as e:
                        logger.error(f"Error in singleton receiver: {e}")
                        await message_queue.put(None)

                async def processor():
                    """Task to process complex messages (ASR, LLM submit) from the queue"""
                    nonlocal audio_buffer, ignored_auto_history_create, partial_asr_task, active_utterance_id
                    nonlocal active_utterance_started_at, active_vad_source, active_client_hint
                    while True:
                        data = await message_queue.get()
                        if data is None: # Stop signal
                            break
                        
                        try:
                            msg_type = data.get("type", "")
                            if msg_type == "mic-audio-end" and not microphone_gate.accepts(data.get("_mic_revision"), client_uid):
                                continue
                            queued_audio_snapshot = data.pop("_audio_snapshot", None)
                            queued_capture_elapsed_ms = data.pop("_capture_elapsed_ms", 0)
                            queued_capture_ended_at = float(
                                data.pop("_capture_ended_at", time.monotonic())
                            )
                            queued_start_vad_source = data.pop("_start_vad_source", "unknown")
                            queued_start_client_hint = data.pop("_start_client_hint", playback_client)
                            if singleton.codex_playback_active and msg_type in (
                                "text-input",
                                "mic-audio-end",
                            ):
                                if msg_type != "mic-audio-end" or queued_audio_snapshot is None:
                                    audio_buffer = np.array([], dtype=np.float32)
                                logger.info(
                                    "suppressed_during_codex_playback: "
                                    f"queued type={msg_type} role={role}"
                                )
                                continue

                            # Repassa ao message_handler
                            message_handler.handle_message(client_uid, data)
                            
                            # SINCRONIA SINGLETON: 
                            # Se for playback complete, forçamos o ID "None" para o alias "singleton"
                            # pois o waiter em conversation_utils.py não conhece o GUID original e espera um match limpo.
                            if msg_type in ("frontend-playback-complete", "playback-completed"):
                                message_handler.handle_message("singleton", {**data, "request_id": None})
                            else:
                                message_handler.handle_message("singleton", data)

                            if msg_type == "text-input" and live_gate.owner_input_blocked():
                                live_gate.note_mic_blocked()
                                logger.info("[Live] texto digitado do dono isolado (modo Live ativo)")
                                continue
                            if msg_type == "text-input":
                                text = data.get("text", "")
                                if text:
                                    speaker_name = (
                                        data.get("speaker_name")
                                        or data.get("from_name")
                                        or data.get("name")
                                        or ""
                                    )
                                    # Broadcast status
                                    for uid, (ws, _) in singleton._connections.items():
                                        if uid != client_uid:
                                            try:
                                                await ws.send_text(json.dumps({
                                                    "type": "user-input-transcription",
                                                    "text": text,
                                                }))
                                            except: pass
                                    profile_intent = detect_profile_switch_intent(text)
                                    if profile_intent.should_switch:
                                        snapshot = await apply_profile_switch(
                                            profile_intent.target, singleton=singleton
                                        )
                                        logger.info(
                                            "[Profile] troca explícita por texto: perfil={} regra={!r}",
                                            snapshot.get("profile"),
                                            profile_intent.matched_text,
                                        )
                                        continue
                                    if profile_intent.action == "mention":
                                        logger.debug(
                                            "[Profile] nome de perfil sem intenção de troca: {!r}", text[:120]
                                        )

                                    # HUD Minecraft: uma interface, um estado. So o navegador do dono digita este comando.
                                    hud_local = role == "browser" and websocket.client is not None and websocket.client.host in ("127.0.0.1", "::1")
                                    hud_intent = detect_hud_intent(text) if hud_local else None
                                    if hud_intent is not None and hud_intent.should_switch:
                                        from .bridge_routes import definir_hud_minecraft

                                        hud_result = definir_hud_minecraft(hud_intent.enabled)
                                        logger.info(
                                            "[HUD] comando por texto: ligada={} mudou={} confirmada={} regra={!r}",
                                            hud_result["hud_minecraft"], hud_result["changed"],
                                            hud_result["confirmed"], hud_intent.matched_text,
                                        )
                                        continue

                                    # Possessão: um controlador externo é a mente da Célia; o texto do dono vai para ele, não para o modelo dela.
                                    if hud_local and await dispatch_local_owner_body_command(
                                        text, source="browser_text", authenticated_owner=True, self_echo=False,
                                        utterance_id=str(data.get("utterance_id") or data.get("id") or "") or None,
                                        persona_submit=(lambda brief: singleton.submit_input(brief, role="source", speaker_name="Corpo no jogo", origin="system")) if singleton else None,
                                    ):
                                        continue
                                    if hud_local and await possession_mirror(
                                        text, source="browser_text", authenticated_owner=True, self_echo=False,
                                        utterance_id=str(data.get("utterance_id") or data.get("id") or "") or None,
                                    ):
                                        continue

                                    # Série (Minecraft) por texto digitado no navegador do dono (loopback): mesma central, mesmo portão.
                                    if hud_local:
                                        series_center = get_series_center()
                                        series_triage = await series_center.triage(
                                            text, utterance_id=str(data.get("utterance_id") or data.get("id") or "") or None, source="browser_text",
                                            authenticated_owner=True, self_echo=False,
                                        )
                                        if series_triage.consume:
                                            logger.info("[Serie] comando por texto consumido: motivo={} comandos={}", series_triage.reason,
                                                        [item.intent for item, _ in series_triage.commands])
                                            asyncio.create_task(run_series_command(
                                                series_center, series_triage, None,
                                                lambda brief: singleton.submit_input(brief, role="source", speaker_name="Corpo no jogo", origin="system", scripted_reply=verified_report_reply(brief)),
                                            ))
                                            continue

                                    # Orquestrador: acumula contexto e verifica intenção
                                    turn_id = continuous_behavior.begin_turn()
                                    router_started = time.monotonic()
                                    intent, payload = orchestrator.process_criador_utterance(text)
                                    continuous_behavior.mark_latency(
                                        "router",
                                        int((time.monotonic() - router_started) * 1000),
                                        turn_id=turn_id,
                                    )
                                    if flow_ab_probe.enabled:
                                        asyncio.create_task(
                                            flow_ab_probe.observe(
                                                turn_id=turn_id,
                                                text=text,
                                                orchestrator_intent=intent,
                                                case_id=data.get("case_id"),
                                            )
                                        )
                                    if intent == "INTROSPECTION":
                                        input_for_celia = cognitive_loop.introspection_prompt() or text
                                        await singleton.submit_input(
                                            input_for_celia, role=role, speaker_name=speaker_name,
                                            turn_id=turn_id,
                                        )
                                    elif intent in {"GREEN_LIGHT", "DIRECT_COMMAND"}:
                                        input_for_celia = payload if payload else text
                                        candidate = continuous_behavior.create_candidate(
                                            intent_type="answer",
                                            semantic_intent="direct_typed_turn",
                                            confidence=1.0,
                                            priority=1.0,
                                            urgency=1.0,
                                            social_value=1.0,
                                            interruption_cost=0.0,
                                            ttl_sec=12,
                                            reason="typed_orchestrator_trigger",
                                        )
                                        decision = continuous_behavior.arbitrate(
                                            trigger="direct_invitation", candidate_id=candidate.id
                                        )
                                        if decision["decision"] in {"SPEAK", "INTERRUPT"}:
                                            await singleton.submit_input(
                                                input_for_celia, role=role, speaker_name=speaker_name,
                                                turn_id=turn_id,
                                            )
                                    else:
                                        direct = continuous_behavior.prepare_direct_turn(
                                            text, source="browser"
                                        )
                                        if direct and direct["decision"] in {"SPEAK", "INTERRUPT"}:
                                            await singleton.submit_input(
                                                text, role=role, speaker_name=speaker_name,
                                                turn_id=turn_id,
                                            )
                                        elif continuous_behavior.profile in {"cohost", "autonomous"}:
                                            opportunity = continuous_behavior.participation_opportunity()
                                            if opportunity is not None:
                                                cognitive_loop.request_think("low_participation_after_typed_turn")

                            elif msg_type == "mic-audio-end":
                                ctx = singleton.context
                                utterance_id = str(data.get("utterance_id") or active_utterance_id or "").strip()
                                latency_trace.mark(
                                    utterance_id,
                                    "fim_fala",
                                    source="mic-audio-end",
                                    endpoint_reason=str(data.get("endpoint_reason") or "legacy_or_unknown"),
                                )
                                has_snapshot = queued_audio_snapshot is not None
                                end_audio_buffer = (
                                    np.asarray(queued_audio_snapshot, dtype=np.float32)
                                    if has_snapshot else audio_buffer
                                )
                                if (
                                    active_utterance_id
                                    and utterance_id != active_utterance_id
                                    and not has_snapshot
                                ):
                                    logger.debug("stale microphone utterance ignored: %s", utterance_id)
                                    await emit_asr_trace(
                                        utterance_id,
                                        "IGNORED_STALE_UTTERANCE",
                                        stage="capture",
                                        rejected=True,
                                        reason="utterance_id_no_longer_active",
                                    )
                                    continue
                                if (
                                    utterance_id == active_utterance_id
                                    and partial_asr_task
                                    and not partial_asr_task.done()
                                ):
                                    partial_asr_task.cancel()
                                end_vad_source = str(
                                    data.get("vad_source") or queued_start_vad_source or "unknown"
                                ).strip() or "unknown"
                                end_client_hint = str(
                                    data.get("client_hint") or queued_start_client_hint or playback_client
                                ).strip() or playback_client
                                endpoint_reason = str(data.get("endpoint_reason") or "legacy_or_unknown").strip() or "legacy_or_unknown"
                                try:
                                    hard_silence_ms = int(data.get("hard_silence_ms") or 400)
                                except (TypeError, ValueError):
                                    hard_silence_ms = 400
                                n_samples = len(end_audio_buffer)
                                sr = (getattr(ctx.asr_engine, "SAMPLE_RATE", 16000) or 16000) if ctx and ctx.asr_engine else 16000
                                try:
                                    elapsed_capture_ms = max(0, int(queued_capture_elapsed_ms or 0))
                                except (TypeError, ValueError):
                                    elapsed_capture_ms = 0
                                logger.info(
                                    "[ASR] utterance end "
                                    f"id={utterance_id or '-'} client={client_uid} playback={playback_client} "
                                    f"hint={end_client_hint} vad={end_vad_source} reason={endpoint_reason} "
                                    f"samples={n_samples} reported={data.get('captured_samples')} "
                                    f"voiced={data.get('voiced_samples')} preroll={data.get('preroll_samples')} "
                                    f"hard_silence_ms={hard_silence_ms} capture_ms={elapsed_capture_ms}"
                                )
                                continuous_behavior.observe("silence.started", source="microphone", duration_ms=hard_silence_ms)
                                continuous_behavior.observe(
                                    "speech.ended", source="microphone", utterance_id=utterance_id
                                )
                                if ctx and ctx.asr_engine and n_samples > 0:
                                    audio_duration_ms = int(n_samples * 1000 / max(1, sr))
                                    pre_asr_audio = np.asarray(end_audio_buffer, dtype=np.float64)
                                    audio_rms = float(np.sqrt(np.mean(np.square(pre_asr_audio)))) if pre_asr_audio.size else 0.0
                                    audio_peak = float(np.max(np.abs(pre_asr_audio))) if pre_asr_audio.size else 0.0
                                    voiced_samples = data.get("voiced_samples")
                                    if voiced_samples is not None:
                                        try:
                                            voiced_samples = max(0, int(voiced_samples))
                                        except (TypeError, ValueError):
                                            voiced_samples = 0
                                    voiced_ms = (
                                        int(int(voiced_samples) * 1000 / max(1, sr))
                                        if voiced_samples is not None else None
                                    )
                                    min_voiced_ms = max(
                                        0, int(os.getenv("CELIA_ASR_MIN_VOICED_MS", "180"))
                                    )
                                    acoustic_gate = evaluate_asr_acoustic_gate(
                                        duration_ms=audio_duration_ms,
                                        rms=audio_rms,
                                        peak=audio_peak,
                                        voiced_ms=voiced_ms,
                                        min_voiced_ms=min_voiced_ms,
                                    )
                                    acoustic_meta = {
                                        "duration_ms": audio_duration_ms,
                                        "rms": round(audio_rms, 6),
                                        "peak": round(audio_peak, 6),
                                        "voiced_ms": voiced_ms,
                                    }
                                    if not acoustic_gate.allow_transcription:
                                        logger.info(
                                            "[ASR ACOUSTIC] id={} decision={} reason={} voiced_ms={} rms={:.4f} peak={:.4f}",
                                            utterance_id or "-",
                                            acoustic_gate.decision,
                                            acoustic_gate.reason,
                                            voiced_ms,
                                            audio_rms,
                                            audio_peak,
                                        )
                                        await emit_asr_trace(
                                            utterance_id,
                                            acoustic_gate.decision,
                                            stage="pre_asr",
                                            rejected=True,
                                            reason=acoustic_gate.reason,
                                            rules=acoustic_gate.rules,
                                            acoustic=acoustic_meta,
                                        )
                                        if not has_snapshot:
                                            audio_buffer = np.array([], dtype=np.float32)
                                        continue
                                    if acoustic_gate.reason == "short_voiced_but_acoustically_strong":
                                        logger.info(
                                            "[ASR ACOUSTIC] chamada curta protegida id={} voiced_ms={} rms={:.4f} peak={:.4f}",
                                            utterance_id or "-", voiced_ms, audio_rms, audio_peak,
                                        )
                                    # Cooldown ativo? avisa o browser e descarta sem chamar a API.
                                    in_cooldown_fn = getattr(ctx.asr_engine, "_in_cooldown", None)
                                    if callable(in_cooldown_fn) and in_cooldown_fn():
                                        remaining_fn = getattr(ctx.asr_engine, "_cooldown_remaining", lambda: 0.0)
                                        reason_fn = getattr(ctx.asr_engine, "_cooldown_reason", lambda: "")
                                        await singleton.broadcast({
                                            "type": "asr-status",
                                            "state": "cooldown",
                                            "reason": reason_fn(),
                                            "remaining_s": remaining_fn(),
                                        })
                                        await emit_asr_trace(
                                            utterance_id,
                                            "ASR_COOLDOWN",
                                            stage="pre_asr",
                                            rejected=True,
                                            reason=str(reason_fn() or "provider_cooldown")[:120],
                                            acoustic=acoustic_meta,
                                        )
                                        if not has_snapshot:
                                            audio_buffer = np.array([], dtype=np.float32)
                                        continue
                                    if live_gate.owner_input_blocked():  # ASR segue ligado; so nao encaminhamos o que o dono fala
                                        live_gate.note_mic_blocked()
                                        if not has_snapshot:
                                            audio_buffer = np.array([], dtype=np.float32)
                                        continue
                                    try:
                                        logger.info(f"ASR: transcrevendo {n_samples} amostras")
                                        # Use to_thread if ASR is CPU bound and synchronous (depends on engine)
                                        final_started = time.monotonic()
                                        asr_input = end_audio_buffer.copy()
                                        latency_trace.mark(
                                            utterance_id,
                                            "asr_start",
                                            provider=type(ctx.asr_engine).__name__,
                                            model=str(getattr(ctx.asr_engine, "model", "") or ""),
                                        )
                                        transcribed = await ctx.asr_engine.async_transcribe_np(asr_input)
                                        if not microphone_gate.accepts(data.get("_mic_revision"), client_uid):
                                            continue
                                        final_latency_ms = int((time.monotonic() - final_started) * 1000)
                                        latency_trace.mark(
                                            utterance_id,
                                            "asr_end",
                                            provider=type(ctx.asr_engine).__name__,
                                            model=str(getattr(ctx.asr_engine, "model", "") or ""),
                                            duration_ms=final_latency_ms,
                                        )
                                        training_trace.archive_asr_input(
                                            asr_input,
                                            transcript=transcribed or "",
                                            engine=ctx.asr_engine,
                                            turn_id=utterance_id,
                                            sample_rate=sr,
                                            channels=getattr(ctx.asr_engine, "NUM_CHANNELS", 1),
                                        )
                                        if not has_snapshot:
                                            audio_buffer = np.array([], dtype=np.float32)

                                        if transcribed and transcribed.strip():
                                            raw_text = transcribed.strip()
                                            audio_duration_ms = int(n_samples * 1000 / max(1, sr))
                                            audio_rms = float(
                                                np.sqrt(np.mean(np.square(asr_input.astype(np.float64))))
                                            ) if asr_input.size else 0.0
                                            audio_peak = float(np.max(np.abs(asr_input))) if asr_input.size else 0.0
                                            voiced_ms = (
                                                int(int(voiced_samples) * 1000 / max(1, sr))
                                                if voiced_samples is not None else None
                                            )
                                            refinement_context = orchestrator.context_log.recent_summary(
                                                max_chars=700
                                            )
                                            normalization = normalize_asr_transcript(
                                                raw_text,
                                                context_summary=refinement_context,
                                                duration_ms=audio_duration_ms,
                                                rms=audio_rms,
                                                peak=audio_peak,
                                                voiced_ms=voiced_ms,
                                            )
                                            turn_rules = normalization.rules
                                            logger.info(
                                                "[ASR RAW] {!r} duration_ms={} rms={:.4f} peak={:.4f} voiced_ms={}",
                                                raw_text[:220], audio_duration_ms, audio_rms, audio_peak, voiced_ms,
                                            )
                                            if normalization.rejected:
                                                logger.info(
                                                    "[ASR HALLUCINATION] reject reason={} raw={!r}",
                                                    normalization.reason, raw_text[:180],
                                                )
                                                await emit_asr_trace(
                                                    utterance_id,
                                                    "REJECTED_HALLUCINATION_GATE",
                                                    stage="post_asr",
                                                    raw_text=raw_text,
                                                    corrected_text="",
                                                    rejected=True,
                                                    reason=normalization.reason,
                                                    rules=turn_rules,
                                                    acoustic=acoustic_meta,
                                                )
                                                continue

                                            normalized_text = normalization.text
                                            if normalization.decision == "CORRECT":
                                                logger.info(
                                                    "[ASR NORMALIZE] rules={} raw={!r} corrected={!r}",
                                                    ",".join(turn_rules) or "-",
                                                    raw_text[:180],
                                                    normalized_text[:180],
                                                )

                                            secondary_text = ""
                                            secondary_method = getattr(
                                                ctx.asr_engine,
                                                "async_secondary_transcribe_np",
                                                None,
                                            )
                                            verification_reason = secondary_verification_reason(
                                                raw_text, voiced_ms=voiced_ms
                                            )
                                            measured_suspicious = has_measured_suspicious_asr(raw_text)
                                            language_shape_mismatch = has_language_shape_mismatch(raw_text)
                                            verification_must_succeed = verification_reason in {
                                                "measured_pattern",
                                                "language_shape_mismatch",
                                            }
                                            if callable(secondary_method) and verification_reason:
                                                secondary_raw = (
                                                    await secondary_method(asr_input)
                                                )
                                                if secondary_raw and secondary_raw.strip():
                                                    secondary_normalization = normalize_asr_transcript(
                                                        secondary_raw.strip(),
                                                        context_summary=refinement_context,
                                                        duration_ms=audio_duration_ms,
                                                        rms=audio_rms,
                                                        peak=audio_peak,
                                                        voiced_ms=voiced_ms,
                                                    )
                                                    secondary_text = secondary_normalization.text
                                                    similarity = transcript_similarity(
                                                        normalized_text,
                                                        secondary_text,
                                                    )
                                                    secondary_suspicious = has_measured_suspicious_asr(
                                                        secondary_raw
                                                    )
                                                    secondary_language_shape = has_language_shape_mismatch(
                                                        secondary_raw
                                                    )
                                                    configured_min_agreement = float(
                                                        os.getenv(
                                                            "CELIA_ASR_SECONDARY_MIN_SIMILARITY",
                                                            "0.58",
                                                        )
                                                    )
                                                    min_agreement = secondary_min_similarity(
                                                        verification_reason,
                                                        configured_min_agreement,
                                                    )
                                                    shared_measured_error = (
                                                        measured_suspicious
                                                        and secondary_suspicious
                                                    )
                                                    shared_language_mismatch = (
                                                        language_shape_mismatch
                                                        and secondary_language_shape
                                                    )
                                                    if (
                                                        secondary_normalization.rejected
                                                        or similarity < min_agreement
                                                        or shared_measured_error
                                                        or shared_language_mismatch
                                                    ):
                                                        if shared_measured_error:
                                                            reason = "primary_fallback_repeat_measured_error"
                                                        elif shared_language_mismatch:
                                                            reason = "primary_fallback_repeat_language_shape"
                                                        else:
                                                            reason = "primary_fallback_divergence"
                                                        logger.warning(
                                                            "[ASR VERIFY] id={} UNCERTAIN reason={} similarity={:.3f} primary={!r} fallback={!r}",
                                                            utterance_id or "-",
                                                            reason,
                                                            similarity,
                                                            normalized_text[:160],
                                                            secondary_text[:160],
                                                        )
                                                        await emit_asr_trace(
                                                            utterance_id,
                                                            "SECONDARY_UNCERTAIN",
                                                            stage="verification",
                                                            raw_text=raw_text,
                                                            corrected_text=normalized_text,
                                                            rejected=True,
                                                            uncertain=True,
                                                            reason=reason,
                                                            rules=turn_rules,
                                                            acoustic={
                                                                **acoustic_meta,
                                                                "secondary_similarity": round(
                                                                    similarity, 4
                                                                ),
                                                                "secondary_text": secondary_text,
                                                                "secondary_verification_reason": verification_reason,
                                                            },
                                                            route_status="UNCERTAIN",
                                                        )
                                                        continue
                                                    logger.info(
                                                        "[ASR VERIFY] id={} agreement={:.3f}",
                                                        utterance_id or "-",
                                                        similarity,
                                                    )
                                                elif verification_must_succeed:
                                                    logger.warning(
                                                        "[ASR VERIFY] id={} UNCERTAIN reason={} without secondary",
                                                        utterance_id or "-",
                                                        verification_reason,
                                                    )
                                                    await emit_asr_trace(
                                                        utterance_id,
                                                        "SECONDARY_UNAVAILABLE_UNCERTAIN",
                                                        stage="verification",
                                                        raw_text=raw_text,
                                                        corrected_text=normalized_text,
                                                        rejected=True,
                                                        uncertain=True,
                                                        reason=f"{verification_reason}_without_secondary",
                                                        rules=turn_rules,
                                                        acoustic={
                                                            **acoustic_meta,
                                                            "secondary_verification_reason": verification_reason,
                                                        },
                                                        route_status="UNCERTAIN",
                                                    )
                                                    continue
                                                else:
                                                    logger.info(
                                                        "[ASR VERIFY] id={} secondary unavailable; keeping deterministic gate",
                                                        utterance_id or "-",
                                                    )
                                            elif verification_must_succeed:
                                                logger.warning(
                                                    "[ASR VERIFY] id={} UNCERTAIN reason={} but engine has no secondary method",
                                                    utterance_id or "-",
                                                    verification_reason,
                                                )
                                                await emit_asr_trace(
                                                    utterance_id,
                                                    "SECONDARY_METHOD_MISSING_UNCERTAIN",
                                                    stage="verification",
                                                    raw_text=raw_text,
                                                    corrected_text=normalized_text,
                                                    rejected=True,
                                                    uncertain=True,
                                                    reason=f"{verification_reason}_without_secondary_method",
                                                    rules=turn_rules,
                                                    acoustic={
                                                        **acoustic_meta,
                                                        "secondary_verification_reason": verification_reason,
                                                    },
                                                    route_status="UNCERTAIN",
                                                )
                                                continue

                                            protected_call = is_protected_celia_call(normalized_text)
                                            if (
                                                not protected_call
                                                and continuous_behavior.probable_self_echo(normalized_text)
                                            ):
                                                logger.info(
                                                    f"🔇 eco final da própria Célia suprimido: {normalized_text[:90]!r}"
                                                )
                                                await emit_asr_trace(
                                                    utterance_id,
                                                    "SUPPRESSED_SELF_ECHO",
                                                    stage="post_asr",
                                                    raw_text=raw_text,
                                                    corrected_text=normalized_text,
                                                    rejected=True,
                                                    reason="probable_self_echo_during_playback",
                                                    rules=turn_rules,
                                                    acoustic=acoustic_meta,
                                                )
                                                continue
                                            if protected_call:
                                                logger.info(
                                                    "[ASR] chamada curta da Célia protegida contra self-echo id={}",
                                                    utterance_id or "-",
                                                )

                                            turn_fragment = AsrTurnFragment(
                                                utterance_id=utterance_id,
                                                raw_text=raw_text,
                                                text=normalized_text,
                                                duration_ms=audio_duration_ms,
                                                voiced_ms=voiced_ms,
                                                rms=audio_rms,
                                                peak=audio_peak,
                                                captured_at=queued_capture_ended_at,
                                                rules=tuple(turn_rules),
                                            )
                                            if data.get("_release_pending"):
                                                # Prazo vencido sem vizinho: o montador JÁ tirou o pendente; esta é a reentrega da própria fala.
                                                turn_gate = AsrTurnGateResult("SUBMIT", "released_after_continuation_window", turn_fragment)
                                            else:
                                                turn_gate = asr_turn_assembler.push(turn_fragment)
                                                asr_scheduler.sync()
                                                if turn_gate.released_fragment is not None:  # corrida: a fala seguinte chegou antes do callback do prazo
                                                    _asr_release_by_time(AsrTurnGateResult("SUBMIT", "released_by_race", turn_gate.released_fragment))
                                                if turn_gate.dropped_fragment is not None:
                                                    asr_replay_messages.pop(turn_gate.dropped_fragment.utterance_id, None)

                                            if turn_gate.dropped_fragment is not None:
                                                dropped = turn_gate.dropped_fragment
                                                await emit_asr_trace(
                                                    dropped.utterance_id,
                                                    "DROPPED_STALE_FRAGMENT",
                                                    stage="turn_gate",
                                                    raw_text=dropped.raw_text,
                                                    corrected_text=dropped.text,
                                                    rejected=True,
                                                    reason="held_fragment_exceeded_merge_window",
                                                    rules=dropped.rules,
                                                    acoustic={
                                                        "duration_ms": dropped.duration_ms,
                                                        "rms": round(dropped.rms, 6),
                                                        "peak": round(dropped.peak, 6),
                                                        "voiced_ms": dropped.voiced_ms,
                                                    },
                                                    route_status="DROPPED_STALE_FRAGMENT",
                                                )

                                            if turn_gate.action == "REJECT":
                                                await emit_asr_trace(
                                                    utterance_id,
                                                    "REJECTED_TURN_GATE",
                                                    stage="turn_gate",
                                                    raw_text=raw_text,
                                                    corrected_text=normalized_text,
                                                    rejected=True,
                                                    reason=turn_gate.reason,
                                                    rules=turn_rules,
                                                    acoustic=acoustic_meta,
                                                    route_status="REJECTED_TURN_GATE",
                                                )
                                                logger.info(
                                                    "[ASR TURN] id={} action=REJECT reason={} voiced_ms={} text={!r}",
                                                    utterance_id or "-",
                                                    turn_gate.reason,
                                                    voiced_ms,
                                                    normalized_text[:160],
                                                )
                                                continue

                                            if turn_gate.action == "HOLD":
                                                held = turn_gate.fragment or turn_fragment
                                                replay_message = {
                                                    **data,
                                                    "_audio_snapshot": queued_audio_snapshot,
                                                    "_capture_elapsed_ms": queued_capture_elapsed_ms,
                                                    "_capture_ended_at": queued_capture_ended_at,
                                                    "_start_vad_source": queued_start_vad_source,
                                                    "_start_client_hint": queued_start_client_hint,
                                                }
                                                if turn_gate.merged_from:
                                                    older_replay = asr_replay_messages.pop(turn_gate.merged_from[0], None)
                                                    if older_replay is not None and older_replay.get("_audio_snapshot") is not None and queued_audio_snapshot is not None:
                                                        replay_message["_audio_snapshot"] = np.concatenate([
                                                            np.asarray(older_replay["_audio_snapshot"], dtype=np.float32),
                                                            np.asarray(queued_audio_snapshot, dtype=np.float32),
                                                        ])
                                                        replay_message["_capture_elapsed_ms"] = int(older_replay.get("_capture_elapsed_ms") or 0) + int(queued_capture_elapsed_ms or 0)
                                                asr_replay_messages[held.utterance_id] = replay_message
                                                await emit_asr_trace(
                                                    utterance_id,
                                                    "HELD_FRAGMENT",
                                                    stage="turn_gate",
                                                    raw_text=held.raw_text,
                                                    corrected_text=held.text,
                                                    rejected=False,
                                                    reason=turn_gate.reason,
                                                    rules=held.rules,
                                                    acoustic={
                                                        "duration_ms": held.duration_ms,
                                                        "rms": round(held.rms, 6),
                                                        "peak": round(held.peak, 6),
                                                        "voiced_ms": held.voiced_ms,
                                                    },
                                                    route_status="HELD_FRAGMENT",
                                                )
                                                logger.info(
                                                    "[ASR TURN] id={} action=HOLD reason={} voiced_ms={} text={!r}",
                                                    utterance_id or "-",
                                                    turn_gate.reason,
                                                    held.voiced_ms,
                                                    held.text[:160],
                                                )
                                                continue

                                            approved = turn_gate.fragment or turn_fragment
                                            if turn_gate.merged_from:
                                                for merged_id in turn_gate.merged_from[:-1]:
                                                    await emit_asr_trace(
                                                        merged_id,
                                                        "MERGED_FRAGMENT",
                                                        stage="turn_gate",
                                                        raw_text="",
                                                        corrected_text="",
                                                        rejected=False,
                                                        reason=f"merged_into:{approved.utterance_id}",
                                                        route_status="MERGED_INTO_NEXT",
                                                    )
                                                logger.info(
                                                    "[ASR TURN] merged={} canonical={} text={!r}",
                                                    ",".join(turn_gate.merged_from),
                                                    approved.utterance_id,
                                                    approved.text[:180],
                                                )
                                            utterance_id = approved.utterance_id
                                            raw_text = approved.raw_text
                                            normalized_text = approved.text
                                            turn_rules = approved.rules
                                            audio_duration_ms = approved.duration_ms
                                            voiced_ms = approved.voiced_ms
                                            audio_rms = approved.rms
                                            audio_peak = approved.peak
                                            acoustic_meta = {
                                                "duration_ms": audio_duration_ms,
                                                "rms": round(audio_rms, 6),
                                                "peak": round(audio_peak, 6),
                                                "voiced_ms": voiced_ms,
                                                "turn_gate": turn_gate.reason,
                                            }

                                            # O corretor Gemma saiu do caminho crítico. O texto canônico
                                            # é exclusivamente RAW + regras determinísticas auditáveis.
                                            transcribed = normalized_text
                                            final_decision = (
                                                "CORRECT"
                                                if turn_rules or transcribed != raw_text
                                                else "KEEP"
                                            )
                                            refinement_meta = {
                                                "status": final_decision,
                                                "text": transcribed,
                                                "latency_ms": 0,
                                                "asr_latency_ms": final_latency_ms,
                                                "transport_status": "disabled",
                                                "reason": "deterministic_asr_pipeline",
                                                "raw_text": raw_text,
                                                "normalization_text": normalized_text,
                                                "normalization_rules": list(turn_rules),
                                                "normalization_decision": normalization.decision,
                                                "final_decision": final_decision,
                                                "verification_reason": verification_reason or "",
                                                "measured_suspicious": bool(measured_suspicious),
                                                "language_shape_mismatch": bool(language_shape_mismatch),
                                                "secondary_text": secondary_text,
                                                "acoustic": acoustic_meta,
                                            }
                                            logger.info(
                                                "[ASR FINAL] decision={} raw={!r} corrected={!r}",
                                                final_decision, raw_text[:180], transcribed[:180],
                                            )
                                            await emit_asr_trace(
                                                utterance_id,
                                                "NORMALIZED" if final_decision == "CORRECT" else "ASR_ACCEPTED",
                                                stage="post_asr",
                                                raw_text=raw_text,
                                                corrected_text=transcribed,
                                                rejected=False,
                                                reason=(
                                                    "deterministic_context"
                                                    if turn_rules else "unchanged"
                                                ),
                                                rules=turn_rules,
                                                acoustic=acoustic_meta,
                                                route_status="ASR_ACCEPTED",
                                            )

                                            if not microphone_gate.accepts(data.get("_mic_revision"), client_uid):
                                                continue
                                            # Fala por voz (mic) é sempre o dono do PC.
                                            mic_speaker = mic_speaker_for_context(ctx)
                                            from .core.audio_source import follow_intent, configure
                                            if (role == "browser" and websocket.client is not None
                                                    and websocket.client.host in ("127.0.0.1", "::1")
                                                    and not continuous_behavior.probable_self_echo(transcribed)):
                                                follow = follow_intent(transcribed)
                                                if follow is not None:
                                                    configure(follow=follow)
                                                    await websocket.send_json({"type": "audio-follow-state", "follow": follow})
                                                    logger.info("[Discord] local owner follow={}", follow)
                                                    continue
                                            profile_intent = detect_profile_switch_intent(transcribed)
                                            if profile_intent.should_switch:
                                                snapshot = await apply_profile_switch(
                                                    profile_intent.target, singleton=singleton
                                                )
                                                logger.info(
                                                    "[Profile] troca explícita por voz: perfil={} regra={!r}",
                                                    snapshot.get("profile"),
                                                    profile_intent.matched_text,
                                                )
                                                await emit_asr_trace(
                                                    utterance_id,
                                                    "PROFILE_COMMAND_CONSUMED",
                                                    stage="routing",
                                                    raw_text=raw_text,
                                                    corrected_text=transcribed,
                                                    reason=profile_intent.matched_text or "explicit_profile_switch",
                                                    rules=turn_rules,
                                                    acoustic=acoustic_meta,
                                                    route_status="PROFILE_COMMAND_CONSUMED",
                                                )
                                                continue
                                            if profile_intent.action == "mention":
                                                logger.debug(
                                                    "[Profile] nome de perfil sem intenção de troca: {!r}",
                                                    transcribed[:120],
                                                )

                                            # HUD Minecraft por voz do dono (microfone): mesma regra e mesmo estado do interruptor do Corpo.
                                            hud_intent = detect_hud_intent(transcribed)
                                            if hud_intent.should_switch:
                                                from .bridge_routes import definir_hud_minecraft

                                                hud_result = definir_hud_minecraft(hud_intent.enabled)
                                                logger.info(
                                                    "[HUD] comando por voz: ligada={} mudou={} confirmada={} regra={!r}",
                                                    hud_result["hud_minecraft"], hud_result["changed"],
                                                    hud_result["confirmed"], hud_intent.matched_text,
                                                )
                                                await emit_asr_trace(
                                                    utterance_id,
                                                    "HUD_COMMAND_CONSUMED",
                                                    stage="routing",
                                                    raw_text=raw_text,
                                                    corrected_text=transcribed,
                                                    reason=hud_intent.matched_text or "explicit_hud_command",
                                                    rules=turn_rules,
                                                    acoustic=acoustic_meta,
                                                    route_status="HUD_COMMAND_CONSUMED",
                                                )
                                                continue

                                            # Possessão: o controlador externo decide por ela. A fala do dono ainda corta a fala em andamento
                                            # (mesma coordenação de turno), mas não chega ao modelo da Célia nem aos comandos da série.
                                            if await dispatch_local_owner_body_command(
                                                transcribed, source="microphone",
                                                authenticated_owner=websocket.client is not None and websocket.client.host in ("127.0.0.1", "::1"),
                                                self_echo=continuous_behavior.probable_self_echo(transcribed), utterance_id=utterance_id,
                                                persona_submit=(lambda brief: singleton.submit_input(brief, role="source", speaker_name="Corpo no jogo", origin="system")) if singleton else None,
                                            ):
                                                await emit_asr_trace(
                                                    utterance_id, "POSSESSION_LOCAL_BODY_COMMAND", stage="routing", raw_text=raw_text,
                                                    corrected_text=transcribed, reason="local_exactly_once_body_dispatch", rules=turn_rules,
                                                    acoustic=acoustic_meta, route_status="POSSESSION_LOCAL_BODY_COMMAND",
                                                )
                                                continue
                                            if await possession_mirror(
                                                transcribed, source="microphone",
                                                authenticated_owner=websocket.client is not None and websocket.client.host in ("127.0.0.1", "::1"),
                                                self_echo=continuous_behavior.probable_self_echo(transcribed), utterance_id=utterance_id,
                                            ):
                                                continuous_behavior.observe(
                                                    "speech.final", source="microphone", speaker=mic_speaker or "", speaker_role="host",
                                                    speaker_confidence=1.0, text=transcribed, duration_ms=final_latency_ms, utterance_id=utterance_id,
                                                )
                                                speech_coordinator.human_committed()
                                                taken_cancel = speech_coordinator.human_committed_playback()
                                                if taken_cancel and taken_cancel == continuous_behavior.active_speech_id:
                                                    await cancel_active_browser_playback("human_turn_committed")
                                                await emit_asr_trace(
                                                    utterance_id, "POSSESSION_CONSUMED", stage="routing", raw_text=raw_text, corrected_text=transcribed,
                                                    reason="possessed_by_external_controller", rules=turn_rules, acoustic=acoustic_meta,
                                                    route_status="POSSESSION_CONSUMED",
                                                )
                                                continue

                                            # Série (Minecraft): so a transcricao FINAL do microfone do dono, em loopback. A execucao pode levar um
                                            # minuto (trocar de mundo reinicia o servidor), entao roda em segundo plano e o laco do microfone segue.
                                            series_center = get_series_center()
                                            series_triage = await series_center.triage(
                                                transcribed, utterance_id=utterance_id, source="microphone",
                                                authenticated_owner=websocket.client is not None and websocket.client.host in ("127.0.0.1", "::1"),
                                                self_echo=continuous_behavior.probable_self_echo(transcribed),
                                            )
                                            if series_triage.consume:
                                                logger.info("[Serie] comando por voz consumido: motivo={} comandos={}", series_triage.reason,
                                                            [item.intent for item, _ in series_triage.commands])
                                                await emit_asr_trace(
                                                    utterance_id,
                                                    "SERIES_COMMAND_CONSUMED",
                                                    stage="routing",
                                                    raw_text=raw_text,
                                                    corrected_text=transcribed,
                                                    reason=series_triage.reason,
                                                    rules=turn_rules,
                                                    acoustic=acoustic_meta,
                                                    route_status="SERIES_COMMAND_CONSUMED",
                                                )
                                                asyncio.create_task(run_series_command(
                                                    series_center, series_triage, utterance_id,
                                                    lambda brief: singleton.submit_input(brief, role="source", speaker_name="Corpo no jogo", origin="system", scripted_reply=verified_report_reply(brief)),
                                                ))
                                                continue

                                            continuous_behavior.observe(
                                                "speech.final", source="microphone",
                                                speaker=mic_speaker or "", speaker_role="host",
                                                speaker_confidence=1.0, text=transcribed,
                                                duration_ms=final_latency_ms,
                                                utterance_id=utterance_id,
                                            )
                                            speech_coordinator.human_committed()
                                            committed_cancel = (
                                                speech_coordinator.human_committed_playback()
                                            )
                                            if (
                                                committed_cancel
                                                and committed_cancel
                                                == continuous_behavior.active_speech_id
                                            ):
                                                await cancel_active_browser_playback(
                                                    "human_turn_committed"
                                                )
                                            stopped = await handle_human_interruption(transcribed)
                                            if stopped and continuous_behavior.profile == "roteiro":
                                                await emit_asr_trace(
                                                    utterance_id,
                                                    "ROTEIRO_INTERRUPTION_CONSUMED",
                                                    stage="routing",
                                                    raw_text=raw_text,
                                                    corrected_text=transcribed,
                                                    reason="explicit_roteiro_stop",
                                                    rules=turn_rules,
                                                    acoustic=acoustic_meta,
                                                    route_status="ROTEIRO_INTERRUPTION_CONSUMED",
                                                )
                                                continue
                                            # Roteiro: a linha do Criador pode completar um beat que
                                            # exige uma resposta da Célia. O teleprompter já observou
                                            # o broadcast acima; neste caso o turno entra no caminho
                                            # conversacional normal. BANK_HIT reaproveita WAV; no MISS,
                                            # a linha aprovada do roteiro vai direto ao TTS, sem o LLM
                                            # precisar adivinhar a fala. Fala nova continua usando o agente.
                                            if continuous_behavior.profile == "roteiro":
                                                from .core.roteiro_live import roteiro_live
                                                from .agent.bridge_client import needs_tool
                                                planned_reply = roteiro_live.planned_celia_reply()
                                                if needs_tool(transcribed):
                                                    planned_reply = None
                                                if planned_reply is not None:
                                                    logger.info(
                                                        "[Roteiro] resposta roteirizada pendente "
                                                        f"beat={planned_reply['beat_id']} line={planned_reply['line_id']}"
                                                    )
                                                elif not needs_tool(transcribed):
                                                    # Measured 2026-09-09: with no scripted reply the
                                                    # free agent ran, every provider was rate-limited,
                                                    # and the small local fallback echoed the director
                                                    # prompt as dialogue ("Responde a Criador."), which
                                                    # reached AUDIO_FINISHED. While recording a script,
                                                    # an unscripted beat must stay silent instead of
                                                    # improvising. Tool commands still reach the agent.
                                                    logger.info(
                                                        "[Roteiro] fala imprevista sem linha roteirizada: "
                                                        "silêncio (nenhuma fala gerada por LLM)"
                                                    )
                                                    await emit_asr_trace(
                                                        utterance_id,
                                                        "ROUTING_SUPPRESSED",
                                                        stage="routing",
                                                        raw_text=raw_text,
                                                        corrected_text=transcribed,
                                                        reason="roteiro_unscripted_silence",
                                                        rules=turn_rules,
                                                        acoustic=acoustic_meta,
                                                        route_status="ROUTING_SUPPRESSED",
                                                    )
                                                    continue
                                                else:
                                                    logger.info("[Roteiro] fala imprevista com comando: agente + TTS fixo")
                                                if not microphone_gate.accepts(data.get("_mic_revision"), client_uid):
                                                    continue
                                                continuous_behavior.invalidate_inflight_reply("owner_turn_submitted")
                                                await singleton.submit_input(
                                                    transcribed, role=role,
                                                    speaker_name=mic_speaker or None,
                                                    origin="microphone", behavior_observed=True,
                                                    turn_id=utterance_id,
                                                    scripted_reply=planned_reply,
                                                    asr_refinement=refinement_meta,
                                                )
                                                await emit_asr_trace(
                                                    utterance_id,
                                                    "SUBMITTED_TO_BRAIN",
                                                    stage="routing",
                                                    raw_text=raw_text,
                                                    corrected_text=transcribed,
                                                    reason="roteiro_or_tool_submission",
                                                    rules=turn_rules,
                                                    acoustic=acoustic_meta,
                                                    route_status="SUBMITTED_TO_BRAIN",
                                                    submitted_to_brain=True,
                                                )
                                                continue
                                            # Orquestrador: acumula contexto + verifica intenção
                                            router_started = time.monotonic()
                                            intent, payload = orchestrator.process_criador_utterance(transcribed)
                                            continuous_behavior.mark_latency(
                                                "router",
                                                int((time.monotonic() - router_started) * 1000),
                                                turn_id=utterance_id,
                                            )

                                            if intent == "INTROSPECTION":
                                                # Gatilho do monólogo interno (Plano 4)
                                                input_for_celia = cognitive_loop.introspection_prompt() or transcribed
                                                if not microphone_gate.accepts(data.get("_mic_revision"), client_uid):
                                                    continue
                                                continuous_behavior.invalidate_inflight_reply("owner_turn_submitted")
                                                await singleton.submit_input(
                                                    input_for_celia,
                                                    role=role,
                                                    speaker_name=mic_speaker or None,
                                                    # The final ASR event above is the canonical behavior event.
                                                    behavior_observed=True,
                                                    turn_id=utterance_id,
                                                    asr_refinement=refinement_meta,
                                                )
                                                await emit_asr_trace(
                                                    utterance_id,
                                                    "SUBMITTED_TO_BRAIN",
                                                    stage="routing",
                                                    raw_text=raw_text,
                                                    corrected_text=transcribed,
                                                    reason="introspection_submission",
                                                    rules=turn_rules,
                                                    acoustic=acoustic_meta,
                                                    route_status="SUBMITTED_TO_BRAIN",
                                                    submitted_to_brain=True,
                                                )
                                            elif intent in ("GREEN_LIGHT", "DIRECT_COMMAND"):
                                                input_for_celia = payload if payload else transcribed
                                                continuous_behavior.create_candidate(
                                                    intent_type="answer",
                                                    semantic_intent="direct_human_turn",
                                                    confidence=1.0,
                                                    priority=1.0,
                                                    urgency=1.0,
                                                    social_value=1.0,
                                                    interruption_cost=0.0,
                                                    ttl_sec=12,
                                                    reason="existing_orchestrator_trigger",
                                                )
                                                decision = continuous_behavior.arbitrate(
                                                    trigger="direct_invitation"
                                                )
                                                if decision["decision"] not in {"SPEAK", "INTERRUPT"}:
                                                    logger.info("[Behavior] turn held: %s", decision["reason"])
                                                    await emit_asr_trace(
                                                        utterance_id,
                                                        "ROUTING_SUPPRESSED",
                                                        stage="routing",
                                                        raw_text=raw_text,
                                                        corrected_text=transcribed,
                                                        reason=str(decision.get("reason") or "behavior_held")[:120],
                                                        rules=turn_rules,
                                                        acoustic=acoustic_meta,
                                                        route_status="ROUTING_SUPPRESSED",
                                                    )
                                                    continue
                                                if not microphone_gate.accepts(data.get("_mic_revision"), client_uid):
                                                    continue
                                                continuous_behavior.invalidate_inflight_reply("owner_turn_submitted")
                                                await singleton.submit_input(
                                                    input_for_celia,
                                                    role=role,
                                                    speaker_name=mic_speaker or None,
                                                    # Preserve the microphone source without counting this turn twice.
                                                    behavior_observed=True,
                                                    turn_id=utterance_id,
                                                    asr_refinement=refinement_meta,
                                                )
                                                await emit_asr_trace(
                                                    utterance_id,
                                                    "SUBMITTED_TO_BRAIN",
                                                    stage="routing",
                                                    raw_text=raw_text,
                                                    corrected_text=transcribed,
                                                    reason="direct_or_green_light_submission",
                                                    rules=turn_rules,
                                                    acoustic=acoustic_meta,
                                                    route_status="SUBMITTED_TO_BRAIN",
                                                    submitted_to_brain=True,
                                                )
                                            else:
                                                # No perfil privado, o microfone é uma conversa direta:
                                                # até uma saudação curta como "iai" precisa chegar ao
                                                # mesmo árbitro/LLM central. Roteiro/co-host continuam
                                                # seletivos e usam apenas a micro-reação de fundo aqui.
                                                direct_decision = continuous_behavior.prepare_direct_turn(
                                                    transcribed, source="microphone"
                                                )
                                                if direct_decision and direct_decision["decision"] in {"SPEAK", "INTERRUPT"}:
                                                    if not microphone_gate.accepts(data.get("_mic_revision"), client_uid):
                                                        continue
                                                    continuous_behavior.invalidate_inflight_reply("owner_turn_submitted")
                                                    await singleton.submit_input(
                                                        transcribed,
                                                        role=role,
                                                        speaker_name=mic_speaker or None,
                                                        behavior_observed=True,
                                                        turn_id=utterance_id,
                                                        asr_refinement=refinement_meta,
                                                    )
                                                    await emit_asr_trace(
                                                        utterance_id,
                                                        "SUBMITTED_TO_BRAIN",
                                                        stage="routing",
                                                        raw_text=raw_text,
                                                        corrected_text=transcribed,
                                                        reason="direct_turn_submission",
                                                        rules=turn_rules,
                                                        acoustic=acoustic_meta,
                                                        route_status="SUBMITTED_TO_BRAIN",
                                                        submitted_to_brain=True,
                                                    )
                                                    continue

                                                # Decision 3 (2026-10-02): in gravacao, an OFF-script question or
                                                # "tá vendo / viu / olha / que isso" goes straight to Célia with the
                                                # last screen analysis. Scripted lines never fire. Measured: "Tá vendo
                                                # o site?" was routed ROUTING_CONTEXT_ONLY and never reached her.
                                                address, address_reason = direct_address.should_address_celia(
                                                    transcribed,
                                                    continuous_behavior.profile,
                                                    direct_address.criador_script_lines(continuous_behavior.script.beats),
                                                )
                                                if address:
                                                    if not microphone_gate.accepts(data.get("_mic_revision"), client_uid):
                                                        continue
                                                    from .core.screen_vision import screen_vision as _sv
                                                    _last, _age = _sv.last_analysis()
                                                    _sv.request_refresh_after_reply()
                                                    continuous_behavior.invalidate_inflight_reply("owner_turn_submitted")
                                                    await singleton.submit_input(
                                                        transcribed,
                                                        role=role,
                                                        speaker_name=mic_speaker or None,
                                                        behavior_observed=True,
                                                        turn_id=utterance_id,
                                                        asr_refinement=refinement_meta,
                                                    )
                                                    await emit_asr_trace(
                                                        utterance_id,
                                                        "SUBMITTED_TO_BRAIN",
                                                        stage="routing",
                                                        raw_text=raw_text,
                                                        corrected_text=transcribed,
                                                        reason=address_reason,
                                                        rules=turn_rules,
                                                        acoustic=acoustic_meta,
                                                        route_status="SUBMITTED_TO_BRAIN",
                                                        submitted_to_brain=True,
                                                    )
                                                    continue

                                                # SILENT_LOG: em roteiro/co-host, uma fala sem convite
                                                # atualiza contexto. No co-host/autônomo, baixa participação
                                                # acorda o MESMO cognitive loop; ele enriquece o placeholder
                                                # e ainda precisa passar pelo árbitro antes de falar.
                                                if continuous_behavior.profile in {"cohost", "autonomous"}:
                                                    opportunity = continuous_behavior.participation_opportunity()
                                                    if opportunity is not None:
                                                        cognitive_loop.request_think("low_participation_after_human_turn")
                                                bc = orchestrator.maybe_backchannel(transcribed)
                                                if bc:
                                                    try:
                                                        publish_emotion(
                                                            bc["expression"], source="backchannel",
                                                            priority=40, ttl_sec=2, lock_sec=0,
                                                        )
                                                    except Exception:
                                                        pass
                                                    try:
                                                        await singleton.broadcast({
                                                            "type": "celia-backchannel",
                                                            "text": bc["text"],
                                                            "expression": bc["expression"],
                                                        })
                                                    except Exception:
                                                        pass
                                                    logger.info(f"[Orchestrator] 💬 backchannel: {bc['text']}")
                                                else:
                                                    logger.debug(f"[Orchestrator] Fala ignorada (SILENT_LOG): {transcribed}")
                                                # Meta+N (KDE global shortcut) can send this line to Célia later.
                                                direct_address.remember_unanswered(transcribed, utterance_id)
                                                await emit_asr_trace(
                                                    utterance_id,
                                                    "ROUTING_CONTEXT_ONLY",
                                                    stage="routing",
                                                    raw_text=raw_text,
                                                    corrected_text=transcribed,
                                                    reason=(
                                                        "backchannel_only" if bc else "context_only_no_response"
                                                    ),
                                                    rules=turn_rules,
                                                    acoustic=acoustic_meta,
                                                    route_status="ROUTING_CONTEXT_ONLY",
                                                )
                                        else:
                                            await emit_asr_trace(
                                                utterance_id,
                                                "ASR_EMPTY_RESULT",
                                                stage="post_asr",
                                                rejected=True,
                                                reason="whisper_returned_empty_text",
                                                acoustic=acoustic_meta,
                                            )
                                    except Exception as e:
                                        logger.error(f"Erro no ASR: {e}")
                                        # Avisa o browser e registra o estágio; erro também não pode
                                        # virar um buraco negro de diagnóstico.
                                        try:
                                            await emit_asr_trace(
                                                utterance_id,
                                                "ASR_ERROR",
                                                stage="error",
                                                rejected=True,
                                                reason=str(e)[:120],
                                                acoustic=locals().get("acoustic_meta"),
                                            )
                                        except Exception:
                                            pass
                                        try:
                                            await singleton.broadcast({
                                                "type": "asr-status",
                                                "state": "error",
                                                "reason": str(e)[:120],
                                            })
                                        except Exception:
                                            pass
                                        if not has_snapshot:
                                            audio_buffer = np.array([], dtype=np.float32)
                                    finally:
                                        if not has_snapshot:
                                            audio_buffer = np.array([], dtype=np.float32)
                                        if utterance_id == active_utterance_id:
                                            active_utterance_id = ""
                                            active_utterance_started_at = 0.0
                                            active_vad_source = "unknown"
                                            active_client_hint = playback_client
                                elif not ctx or not ctx.asr_engine:
                                    await emit_asr_trace(
                                        utterance_id,
                                        "ASR_UNAVAILABLE",
                                        stage="pre_asr",
                                        rejected=True,
                                        reason="asr_engine_unavailable",
                                    )
                                    if not has_snapshot:
                                        audio_buffer = np.array([], dtype=np.float32)
                                    if utterance_id == active_utterance_id:
                                        active_utterance_id = ""
                                        active_utterance_started_at = 0.0
                                        active_vad_source = "unknown"
                                        active_client_hint = playback_client
                                elif n_samples <= 0:
                                    await emit_asr_trace(
                                        utterance_id,
                                        "REJECTED_EMPTY_AUDIO",
                                        stage="pre_asr",
                                        rejected=True,
                                        reason="empty_audio_buffer",
                                    )
                                    if not has_snapshot:
                                        audio_buffer = np.array([], dtype=np.float32)
                                    if utterance_id == active_utterance_id:
                                        active_utterance_id = ""
                                        active_utterance_started_at = 0.0
                                        active_vad_source = "unknown"
                                        active_client_hint = playback_client

                            elif msg_type == "ai-speak-signal":
                                # Proactive speak: o frontend (ProactiveSpeakProvider)
                                # manda este sinal apos `idleSecondsToSpeak` segundos de
                                # ociosidade quando o usuario ativa "allowProactiveSpeak"
                                # nas configuracoes. O modo singleton nao tratava o sinal,
                                # entao a auto-resposta por tempo estava morta. Carregamos
                                # o prompt proativo e disparamos um turno (so se livre).
                                if continuous_behavior.profile == "conversation_autonomous":
                                    # Conversa Autonoma: UM relogio de silencio, o compartilhado do backend (zera quando qualquer um comeca a falar).
                                    # O timer de ociosidade do frontend nao conhece a fala da Celia e e a causa do bug "falou quando o Criador voltou a falar".
                                    logger.debug("ai-speak-signal ignorado: o perfil usa o silencio compartilhado do backend")
                                elif singleton.is_processing:
                                    logger.debug("ai-speak-signal ignorado: Celia ocupada")
                                else:
                                    try:
                                        idle_seconds = float(data.get("idle_time") or 0.0)
                                        candidate = continuous_behavior.prepare_proactive_candidate(idle_seconds)
                                        if candidate is None:
                                            logger.debug("ai-speak-signal ignorado: proatividade desativada no perfil")
                                            continue
                                        decision = continuous_behavior.arbitrate(trigger="spontaneous")
                                        if decision["decision"] != "SPEAK":
                                            logger.debug("[Behavior] proactive turn held: %s", decision["reason"])
                                            continue
                                        from celia.prompts import prompt_loader
                                        ctx = singleton.context
                                        prompt_file = (
                                            ctx.system_config.tool_prompts.get(
                                                "proactive_speak_prompt"
                                            )
                                            if ctx and ctx.system_config
                                            else None
                                        )
                                        proactive_text = (
                                            prompt_loader.load_util(prompt_file)
                                            if prompt_file
                                            else "Puxa um assunto curto e natural pra quebrar o silencio."
                                        )
                                        await singleton.submit_input(
                                            proactive_text,
                                            role=role,
                                            origin="system",
                                            behavior_observed=True,
                                        )
                                    except Exception as e:
                                        logger.error(f"Erro no proactive speak: {e}")

                            elif msg_type == "fetch-history-list":
                                ctx = singleton.context
                                histories = get_history_list(ctx.character_config.conf_uid)
                                current_uid = ctx.history_uid
                                known_uids = {h["uid"] for h in histories}
                                if current_uid and current_uid not in known_uids:
                                    histories.insert(0, {"uid": current_uid, "latest_message": None, "timestamp": None})
                                await websocket.send_text(json.dumps({"type": "history-list", "histories": histories}))

                            elif msg_type == "fetch-and-set-history":
                                history_uid = data.get("history_uid")
                                if history_uid and singleton.context:
                                    singleton.switch_history(history_uid)
                                    messages = [msg for msg in get_history(singleton.context.character_config.conf_uid, history_uid) if msg["role"] != "system"]
                                    await singleton.broadcast({"type": "history-data", "messages": messages})

                            elif msg_type == "create-new-history":
                                if (
                                    not ignored_auto_history_create
                                    and time.monotonic() - connected_at < 2.0
                                ):
                                    ignored_auto_history_create = True
                                    logger.info(
                                        f"Ignorando create-new-history automatico na conexao inicial de {client_uid}"
                                    )
                                    continue

                                new_uid = singleton.create_fresh_history()
                                if new_uid:
                                    await singleton.broadcast({"type": "new-history-created", "history_uid": new_uid})

                            elif msg_type == "memory-book-list":
                                resp = handle_memory_book_list(singleton.context.agent_engine, data)
                                await websocket.send_text(json.dumps(resp))

                            elif msg_type == "memory-book-edit":
                                resp = handle_memory_book_edit(singleton.context.agent_engine, data)
                                await websocket.send_text(json.dumps(resp))

                            elif msg_type == "memory-book-delete":
                                resp = handle_memory_book_delete(singleton.context.agent_engine, data)
                                await websocket.send_text(json.dumps(resp))

                            elif msg_type == "memory-book-clear":
                                resp = handle_memory_book_clear(singleton.context.agent_engine, data)
                                await websocket.send_text(json.dumps(resp))

                            elif msg_type == "memory-book-compact":
                                resp = handle_memory_book_compact(singleton.context.agent_engine, data)
                                await websocket.send_text(json.dumps(resp))

                            elif msg_type == "memory-book-purge-trivial":
                                resp = handle_memory_book_purge_trivial(singleton.context.agent_engine, data)
                                await websocket.send_text(json.dumps(resp))

                            elif msg_type == "delete-history":
                                ctx = singleton.context
                                if data.get("history_uid") and ctx:
                                    if data.get("history_uid") == ctx.history_uid and singleton.is_processing:
                                        continue
                                    resp = handle_delete_history(
                                        ctx.character_config.conf_uid,
                                        data,
                                        on_current_active={"agent": ctx.agent_engine},
                                    )
                                    await singleton.broadcast(resp)
                                    if resp.get("success") and data.get("history_uid") == ctx.history_uid:
                                        new_uid = singleton.create_fresh_history()
                                        if new_uid:
                                            await singleton.broadcast({"type": "new-history-created", "history_uid": new_uid})

                        except Exception as e:
                            logger.error(f"Error in singleton processor for {msg_type}: {e}")
                        finally:
                            message_queue.task_done()

                async def server_pinger():
                    """Keepalive e detector de zombies.
                    Dupla funcao:
                    1) Envia server-ping para manter TCP/JS timers acordados.
                    2) Monitora last_client_activity_at. Se o frontend não mandar nada 
                       (incluindo heartbeats de 10s) por ZOMBIE_TIMEOUT_S, mata a conexão.
                    Só roda para conexoes browser."""
                    if role != "browser":
                        return
                    try:
                        while True:
                            await asyncio.sleep(SERVER_PING_INTERVAL_S)
                            
                            # Verifica timeout de atividade
                            now = time.monotonic()
                            if now - last_client_activity_at > ZOMBIE_TIMEOUT_S:
                                logger.warning(f"Conexão zombie detectada para {client_uid} (sem atividade há {now - last_client_activity_at:.1f}s). Encerrando.")
                                receiver_task.cancel()
                                break
                                
                            try:
                                await websocket.send_json({"type": "server-ping"})
                            except Exception:
                                # Send falhou = conexao TCP morta
                                receiver_task.cancel()
                                break
                    except asyncio.CancelledError:
                        pass

                # Run tasks concurrently
                receiver_task = asyncio.create_task(receiver())
                processor_task = asyncio.create_task(processor())
                pinger_task = asyncio.create_task(server_pinger())

                try:
                    await asyncio.gather(receiver_task)
                except (WebSocketDisconnect, asyncio.CancelledError):
                    processor_task.cancel()
                    pinger_task.cancel()
                    raise
                finally:
                    asr_scheduler.cancel()  # conexão encerrada: nenhum prazo de fragmento pode disparar depois
                    asr_replay_messages.clear()
                    if not processor_task.done():
                        processor_task.cancel()
                    if not pinger_task.done():
                        pinger_task.cancel()

            except WebSocketDisconnect:
                logger.info(f"Cliente singleton {client_uid} desconectado")
            except Exception as e:
                logger.error(f"Erro na conexão singleton {client_uid}: {e}")
            finally:
                microphone_clients.pop(client_uid, None)
                microphone_gate.unregister(client_uid)
                await singleton.unregister(client_uid)
                message_handler.cleanup_client(client_uid)

    else:
        # ── MODO LEGADO (por conexão) ─────────────────────────────────────
        ws_handler = WebSocketHandler(default_context_cache)

        @router.websocket("/client-ws")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket endpoint legado — uma Célia por conexão"""
            await websocket.accept()
            client_uid = str(uuid4())

            try:
                await ws_handler.handle_new_connection(websocket, client_uid)
                await ws_handler.handle_websocket_communication(websocket, client_uid)
            except WebSocketDisconnect:
                await ws_handler.handle_disconnect(client_uid)
            except Exception as e:
                logger.error(f"Error in WebSocket connection: {e}")
                await ws_handler.handle_disconnect(client_uid)
                raise

    return router


def init_proxy_route(server_url: str) -> APIRouter:
    """
    Create and return API routes for handling proxy connections.

    Args:
        server_url: The WebSocket URL of the actual server

    Returns:
        APIRouter: Configured router with proxy WebSocket endpoint
    """
    router = APIRouter()
    proxy_handler = ProxyHandler(server_url)

    @router.websocket("/proxy-ws")
    async def proxy_endpoint(websocket: WebSocket):
        """WebSocket endpoint for proxy connections"""
        try:
            await proxy_handler.handle_client_connection(websocket)
        except Exception as e:
            logger.error(f"Error in proxy connection: {e}")
            raise

    return router


def init_webtool_routes(default_context_cache: ServiceContext) -> APIRouter:
    """
    Create and return API routes for handling web tool interactions.

    Args:
        default_context_cache: Default service context cache for new sessions.

    Returns:
        APIRouter: Configured router with WebSocket endpoint.
    """

    router = APIRouter()

    @router.get("/web-tool")
    async def web_tool_redirect():
        """Redirect /web-tool to /web_tool/index.html"""
        return Response(status_code=302, headers={"Location": "/web-tool/index.html"})

    @router.get("/web_tool")
    async def web_tool_redirect_alt():
        """Redirect /web_tool to /web_tool/index.html"""
        return Response(status_code=302, headers={"Location": "/web-tool/index.html"})

    @router.get("/live2d-models/info")
    async def get_live2d_folder_info():
        """Get information about available Live2D models"""
        live2d_dir = "live2d-models"
        if not os.path.exists(live2d_dir):
            return JSONResponse(
                {"error": "Live2D models directory not found"}, status_code=404
            )

        valid_characters = []
        supported_extensions = [".png", ".jpg", ".jpeg"]

        for entry in os.scandir(live2d_dir):
            if entry.is_dir():
                folder_name = entry.name.replace("\\", "/")
                model3_file = os.path.join(
                    live2d_dir, folder_name, f"{folder_name}.model3.json"
                ).replace("\\", "/")

                if os.path.isfile(model3_file):
                    # Find avatar file if it exists
                    avatar_file = None
                    for ext in supported_extensions:
                        avatar_path = os.path.join(
                            live2d_dir, folder_name, f"{folder_name}{ext}"
                        )
                        if os.path.isfile(avatar_path):
                            avatar_file = avatar_path.replace("\\", "/")
                            break

                    valid_characters.append(
                        {
                            "name": folder_name,
                            "avatar": avatar_file,
                            "model_path": model3_file,
                        }
                    )
        return JSONResponse(
            {
                "type": "live2d-models/info",
                "count": len(valid_characters),
                "characters": valid_characters,
            }
        )

    @router.post("/asr")
    async def transcribe_audio(file: UploadFile = File(...)):
        """
        Endpoint for transcribing audio using the ASR engine
        """
        logger.info(f"Received audio file for transcription: {file.filename}")

        try:
            contents = await file.read()

            # Validate minimum file size
            if len(contents) < 44:  # Minimum WAV header size
                raise ValueError("Invalid WAV file: File too small")

            # Decode the WAV header and get actual audio data
            wav_header_size = 44  # Standard WAV header size
            audio_data = contents[wav_header_size:]

            # Validate audio data size
            if len(audio_data) % 2 != 0:
                raise ValueError("Invalid audio data: Buffer size must be even")

            # Convert to 16-bit PCM samples to float32
            try:
                audio_array = (
                    np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
                    / 32768.0
                )
            except ValueError as e:
                raise ValueError(
                    f"Audio format error: {str(e)}. Please ensure the file is 16-bit PCM WAV format."
                )

            # Validate audio data
            if len(audio_array) == 0:
                raise ValueError("Empty audio data")

            text = await default_context_cache.asr_engine.async_transcribe_np(
                audio_array
            )
            logger.info(f"Transcription result: {text}")
            return {"text": text}

        except ValueError as e:
            logger.error(f"Audio format error: {e}")
            return Response(
                content=json.dumps({"error": str(e)}),
                status_code=400,
                media_type="application/json",
            )
        except Exception as e:
            logger.error(f"Error during transcription: {e}")
            return Response(
                content=json.dumps(
                    {"error": "Internal server error during transcription"}
                ),
                status_code=500,
                media_type="application/json",
            )

    @router.websocket("/tts-ws")
    async def tts_endpoint(websocket: WebSocket):
        """WebSocket endpoint for TTS generation"""
        await websocket.accept()
        logger.info("TTS WebSocket connection established")

        try:
            while True:
                data = await websocket.receive_json()
                text = data.get("text")
                if not text:
                    continue

                logger.info(f"Received text for TTS: {text}")

                # Split text into sentences
                sentences = [s.strip() for s in text.split(".") if s.strip()]

                try:
                    # Generate and send audio for each sentence
                    for sentence in sentences:
                        sentence = sentence + "."  # Add back the period
                        file_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid4())[:8]}"
                        audio_path = (
                            await default_context_cache.tts_engine.async_generate_audio(
                                text=sentence, file_name_no_ext=file_name
                            )
                        )
                        logger.info(
                            f"Generated audio for sentence: {sentence} at: {audio_path}"
                        )

                        await websocket.send_json(
                            {
                                "status": "partial",
                                "audioPath": audio_path,
                                "text": sentence,
                            }
                        )

                    # Send completion signal
                    await websocket.send_json({"status": "complete"})

                except Exception as e:
                    logger.error(f"Error generating TTS: {e}")
                    await websocket.send_json({"status": "error", "message": str(e)})

        except WebSocketDisconnect:
            logger.info("TTS WebSocket client disconnected")
        except Exception as e:
            logger.error(f"Error in TTS WebSocket connection: {e}")
            await websocket.close()

    @router.get("/tts/presets")
    async def list_tts_presets():
        """Lista todos os presets de voz disponiveis com seus parametros."""
        tts_engine = default_context_cache.tts_engine
        if tts_engine is None:
            return JSONResponse({"error": "TTS engine not initialized"}, status_code=503)
        return JSONResponse(tts_engine.list_presets())

    @router.get("/tts/preset")
    async def get_tts_preset():
        """Retorna o preset ativo atualmente."""
        tts_engine = default_context_cache.tts_engine
        if tts_engine is None:
            return JSONResponse({"error": "TTS engine not initialized"}, status_code=503)
        return JSONResponse(tts_engine.get_active_preset())

    @router.post("/tts/preset")
    async def set_tts_preset(request: Request):
        """Ativa um preset de voz. Body: {"preset": "expressivo"}"""
        tts_engine = default_context_cache.tts_engine
        if tts_engine is None:
            return JSONResponse({"error": "TTS engine not initialized"}, status_code=503)
        try:
            body = await request.json()
            preset_name = body.get("preset", "")
            if not preset_name:
                return JSONResponse({"error": "Missing 'preset' in body"}, status_code=400)
            tts_engine.set_preset(preset_name)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(tts_engine.get_active_preset())

    @router.get("/presets")
    async def presets_page():
        """Pagina HTML com interface de presets pra Celia."""
        from starlette.responses import HTMLResponse
        return HTMLResponse(content=PRESETS_HTML)

    return router
