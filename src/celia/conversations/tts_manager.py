import asyncio
import os
import json
import re
import threading
import time
import uuid
import weakref
from datetime import datetime
from typing import List, Optional, Dict
from loguru import logger

_emotion_lock = threading.Lock()
_pending_speech_emotions: dict[str, str] = {}


def register_speech_emotion(speech_id: str, emotion: str) -> None:
    with _emotion_lock:
        _pending_speech_emotions[speech_id] = emotion


def pop_speech_emotion(speech_id: str) -> Optional[str]:
    with _emotion_lock:
        return _pending_speech_emotions.pop(speech_id, None)

from ..agent.output_types import DisplayText, Actions
from ..live2d_model import Live2dModel
from ..tts.tts_interface import TTSInterface
from ..utils.stream_audio import prepare_audio_payload


def _play_audio_to_device(audio_path: str, device_name: str, remove_after: bool) -> None:
    """Load the host-audio backend only when a configured mirror is used."""
    from ..utils.audio_playback import play_audio_to_device

    play_audio_to_device(audio_path, device_name, remove_after=remove_after)
from ..core.obs_subtitles import obs_subtitles
from ..core.track_emotion_client import register_lipsync_envelope
from ..core.continuous_behavior import continuous_behavior
from ..core.speech_coordinator import speech_coordinator
from ..core.observability import observability
from ..core.audio_lifecycle import record_audio_lifecycle
from ..core.training_trace import training_trace
from ..core.latency_trace import latency_trace
from ..core.gpu_scheduler import gpu_scheduler
from .types import WebSocketSend


_ACTIVE_TTS_MANAGERS = weakref.WeakSet()

def tts_queue_snapshot():
    managers = list(_ACTIVE_TTS_MANAGERS)
    return {
        "unfinished_tasks": sum(not task.done() for m in managers for task in m.task_list),
        "queued_payloads": sum(m._payload_queue.qsize() for m in managers),
    }

class TTSTaskManager:
    """Enfileira TTS e entrega ao frontend em ordem.

    A entrega SEMPRE foi ordenada. A síntese não era, e por isso o chunk 0 —
    o único que pode começar a falar — podia perder a corrida pelo engine e
    atrasar a primeira fala do turno inteiro. Ver _wait_for_synthesis_turn().
    """

    # Teto do portão de ordem de síntese. Generoso de propósito: ele existe só
    # para destravar um turno cujo chunk anterior morreu sem liberar, não para
    # competir com o tempo normal de síntese do OmniVoice (3 s medidos).
    SYNTHESIS_TURN_TIMEOUT_S = 30.0

    def __init__(self, audio_output_device: Optional[str] = None) -> None:
        self.task_list: List[asyncio.Task] = []
        self._lock = asyncio.Lock()
        self.audio_output_device = audio_output_device
        # Owner reply: chunks are validated against continuous_behavior.turn_revision
        # instead of the context revision. None keeps the context semantics
        # (spontaneous/system speech, group conversation).
        self._reply_turn_revision: Optional[int] = None
        # Queue to store ordered payloads
        self._payload_queue: asyncio.Queue[tuple[Dict, int]] = asyncio.Queue()
        self._latency_starts: Dict[int, float | None] = {}
        # Task to handle sending payloads in order
        self._sender_task: Optional[asyncio.Task] = None
        # Counter for maintaining order
        self._sequence_counter = 0
        self._next_sequence_to_send = 0
        # ORDEM DE SÍNTESE — ver _wait_for_synthesis_turn().
        # O envio já era ordenado; a SÍNTESE não era, e era aí que a primeira
        # fala atrasava. Um portão por sequence garante que o chunk 0 chegue ao
        # engine antes do 1.
        self._synthesis_turn: Dict[int, asyncio.Event] = {}
        self._synthesis_turn_lock = asyncio.Lock()
        # Mirror playback tasks for local audio device
        self._mirror_tasks: List[asyncio.Task] = []
        # A response may be split into multiple synthesized sentences. Keep the
        # browser speech ids in delivery order so Roteiro can seal the whole
        # turn and advance only after every chunk really finishes.
        self._speech_ids_by_turn: Dict[str, Dict[int, str]] = {}
        _ACTIVE_TTS_MANAGERS.add(self)

    async def speak(
        self,
        tts_text: str,
        display_text: DisplayText,
        actions: Optional[Actions],
        live2d_model: Live2dModel,
        tts_engine: TTSInterface,
        websocket_send: WebSocketSend,
        emotion: Optional[str] = None,
        turn_id: str = "",
        prepared_audio: Optional[str] = None,
        prepared_revision: Optional[int] = None,
    ) -> None:
        """
        Queue a TTS task while maintaining order of delivery.

        Args:
            tts_text: Text to synthesize
            display_text: Text to display in UI
            actions: Live2D model actions
            live2d_model: Live2D model instance
            tts_engine: TTS engine instance
            websocket_send: WebSocket send function
        """
        # Also covers prepared audio; reject before allocating a sequence slot.
        from ..tts.output_filter import speech_blocked
        if speech_blocked(tts_text):
            return

        if len(re.sub(r'[\s.,!?，。！？\'"』」）】\s]+', "", tts_text)) == 0:
            logger.debug("Empty TTS text, sending silent display payload")
            # Get current sequence number for silent payload
            current_sequence = self._sequence_counter
            self._sequence_counter += 1

            # Start sender task if not running
            if not self._sender_task or self._sender_task.done():
                self._sender_task = asyncio.create_task(
                    self._process_payload_queue(websocket_send)
                )

            await self._send_silent_payload(display_text, actions, current_sequence)
            return

        logger.debug(
            f"🏃Queuing TTS task for: '''{tts_text}''' (by {display_text.name})"
        )

        # Get current sequence number
        current_sequence = self._sequence_counter
        self._sequence_counter += 1

        # Start sender task if not running
        if not self._sender_task or self._sender_task.done():
            self._sender_task = asyncio.create_task(
                self._process_payload_queue(websocket_send)
            )

        # Create and queue the TTS task
        task = asyncio.create_task(
            self._process_tts_releasing_turn(
                tts_text=tts_text,
                display_text=display_text,
                actions=actions,
                live2d_model=live2d_model,
                tts_engine=tts_engine,
                sequence_number=current_sequence,
                websocket_send=websocket_send,
                emotion=emotion,
                turn_id=turn_id,
                prepared_audio=prepared_audio,
                prepared_revision=prepared_revision,
            )
        )
        self.task_list.append(task)

    async def _process_payload_queue(self, websocket_send: WebSocketSend) -> None:
        """
        Process and send payloads in correct order.
        Runs continuously until all payloads are processed.
        """
        buffered_payloads: Dict[int, Dict] = {}

        while True:
            try:
                # Get payload from queue
                payload, sequence_number = await self._payload_queue.get()
                buffered_payloads[sequence_number] = payload

                # Send payloads in order
                while self._next_sequence_to_send in buffered_payloads:
                    next_payload = buffered_payloads.pop(self._next_sequence_to_send)
                    latency_started_at = self._latency_starts.pop(self._next_sequence_to_send, None)
                    skip_delivery = bool(next_payload.pop("_skip_delivery", False))
                    subtitle = next_payload.pop("_obs_subtitle", None)
                    cancel_token = next_payload.get("cancel_token") or {}
                    if (
                        not skip_delivery
                        and cancel_token
                        and not continuous_behavior.tts_chunk_valid(cancel_token)
                    ):
                        skip_delivery = True
                        record_audio_lifecycle({"event": "AUDIO_DROPPED", "speech_id": next_payload.get("speech_id"), "reason": "stale_cancel_token"}, source="tts.dispatch")
                        speech_id = str(next_payload.get("speech_id") or "")
                        turn_id = str(next_payload.get("turn_id") or "")
                        logger.info(
                            f"🧟 Discarding stale synthesized speech at final browser dispatch "
                            f"speech_id={speech_id} turn_id={turn_id}"
                        )
                        observability.emit(
                            "stale_speech_dropped",
                            "tts.dispatch",
                            "dropped",
                            {
                                "speech_id": speech_id,
                                "turn_id": turn_id,
                                "reason": "context_revision_changed_before_dispatch",
                                "final_effect": "dropped",
                            },
                        )
                    speech_ticket_id = str(next_payload.get("speech_ticket_id") or "")
                    if (
                        not skip_delivery
                        and speech_ticket_id
                        and not speech_coordinator.valid(
                            speech_ticket_id,
                            context_revision=continuous_behavior.current_revision_for(cancel_token),
                        )
                    ):
                        skip_delivery = True
                        record_audio_lifecycle(
                            {
                                "event": "AUDIO_DROPPED",
                                "speech_id": next_payload.get("speech_id"),
                                "reason": "speech_authority_cancelled",
                            },
                            source="tts.dispatch",
                        )
                    if not skip_delivery:
                        if subtitle:
                            obs_subtitles.start_subtitle(
                                subtitle["text"],
                                duration_sec=subtitle["duration_sec"],
                                speech_id=subtitle["speech_id"],
                                wait_for_playback=True,
                            )
                        # Roteiro intentionally suppresses the early text preview so
                        # captions cannot outrun speech generation. Once the ordered
                        # payload is actually being dispatched, publish the same
                        # display text independently from the audio event. Playback
                        # now defers during listening; this separate text event still
                        # preserves the existing roteiro preview contract.
                        if continuous_behavior.profile == "roteiro":
                            display = next_payload.get("display_text") or {}
                            visual_text = str(display.get("text") or "").strip()
                            if visual_text:
                                await websocket_send(
                                    json.dumps(
                                        {"type": "full-text", "text": visual_text},
                                        ensure_ascii=False,
                                    )
                                )
                        await websocket_send(json.dumps(next_payload))
                        record_audio_lifecycle({"event": "AUDIO_SENT", "speech_id": next_payload.get("speech_id")}, source="tts.dispatch")
                        latency_trace.mark(
                            str(next_payload.get("turn_id") or ""),
                            "audio_sent",
                            speech_id=str(next_payload.get("speech_id") or ""),
                            phrase_index=int(next_payload.get("phrase_index") or self._next_sequence_to_send),
                        )
                        if next_payload.get("bank_playback"):
                            observability.emit("bank_playback", "tts.dispatch", "ok", {
                                "audio_reused": True, "tts_avoided": True,
                                "speech_id": next_payload.get("speech_id"),
                                "turn_id": next_payload.get("turn_id"),
                                "latency_saved_ms": "unavailable",
                            })
                        turn_id = str(next_payload.get("turn_id") or "")
                        if turn_id and next_payload.get("speech_id") and latency_started_at is not None:
                            tts_to_sent_ms = int((time.monotonic() - latency_started_at) * 1000)
                            continuous_behavior.mark_latency(
                                "tts_to_sent_ms", tts_to_sent_ms, turn_id=turn_id
                            )
                            continuous_behavior.mark_latency(
                                "first_audio_sent", turn_id=turn_id
                            )
                    self._next_sequence_to_send += 1

                self._payload_queue.task_done()

            except asyncio.CancelledError:
                break

    async def _send_silent_payload(
        self,
        display_text: DisplayText,
        actions: Optional[Actions],
        sequence_number: int,
    ) -> None:
        """Queue a silent audio payload"""
        audio_payload = prepare_audio_payload(
            audio_path=None,
            display_text=display_text,
            actions=actions,
        )
        self._latency_starts[sequence_number] = None
        await self._payload_queue.put((audio_payload, sequence_number))

    async def _process_tts_releasing_turn(self, **kwargs) -> None:
        """Envelope que SEMPRE libera o portão do próximo chunk.

        `_process_tts` tem vários retornos antecipados (texto vazio, ticket
        cancelado, falha de síntese). Qualquer um deles deixando o portão
        fechado emudeceria o resto do turno até o timeout. `Event.set()` é
        idempotente, então liberar de novo aqui é inofensivo.
        """
        sequence_number = int(kwargs.get("sequence_number", 0))
        try:
            await self._process_tts(**kwargs)
        finally:
            await self._release_synthesis_turn(sequence_number)

    async def _process_tts(
        self,
        tts_text: str,
        display_text: DisplayText,
        actions: Optional[Actions],
        live2d_model: Live2dModel,
        tts_engine: TTSInterface,
        sequence_number: int,
        websocket_send: WebSocketSend,
        emotion: Optional[str] = None,
        turn_id: str = "",
        prepared_audio: Optional[str] = None,
        prepared_revision: Optional[int] = None,
    ) -> None:
        """Process TTS generation and queue the result for ordered delivery"""
        audio_file_path = None
        speech_chunk = continuous_behavior.create_tts_chunk(tts_text, turn_id=turn_id)
        if prepared_revision is not None:
            speech_chunk["context_revision"] = prepared_revision
        reply_turn_revision = getattr(self, "_reply_turn_revision", None)
        if reply_turn_revision is not None:
            speech_chunk["turn_revision"] = int(reply_turn_revision)
            speech_chunk["revision_kind"] = "turn"
        speech_ticket = speech_coordinator.request(
            source="direct_user",
            ttl_sec=45.0,
            event_key=(
                f"conversation:{speech_chunk['turn_id']}:{sequence_number}"
                if speech_chunk.get("turn_id") else ""
            ),
            context_revision=int(
                speech_chunk["turn_revision"]
                if speech_chunk.get("revision_kind") == "turn"
                else speech_chunk["context_revision"]
            ),
            take_id=str(speech_chunk.get("turn_id") or ""),
            interruptible=True,
            owner_reply=speech_chunk.get("revision_kind") == "turn",
        )
        if speech_ticket is None:
            self._latency_starts[sequence_number] = None
            await self._payload_queue.put(({"_skip_delivery": True}, sequence_number))
            return
        speech_coordinator.bind_speech(speech_ticket.id, str(speech_chunk["speech_id"]))
        resolved_turn_id = str(speech_chunk.get("turn_id") or "")
        if resolved_turn_id:
            self._speech_ids_by_turn.setdefault(resolved_turn_id, {})[
                sequence_number
            ] = str(speech_chunk["speech_id"])
        latency_started_at = time.monotonic()
        latency_trace.mark(
            resolved_turn_id,
            "tts_start",
            phrase_index=sequence_number,
            chars=len(tts_text),
            prepared_audio=bool(prepared_audio),
        )
        latency_trace.request_gpu_snapshot(
            resolved_turn_id,
            "tts_start",
            phrase_index=sequence_number,
        )
        try:
            if prepared_audio:
                if not self._speech_ticket_current(speech_ticket.id, speech_chunk):
                    self._queue_skipped_delivery(sequence_number)
                    record_audio_lifecycle(
                        {"event": "AUDIO_DROPPED", "speech_id": speech_chunk["speech_id"], "reason": "speech_ticket_invalid_before_tts"},
                        source="tts",
                    )
                    return
                # The normal delivery lifecycle owns only this disposable copy.
                # Never remove or overwrite an approved bank asset.
                import shutil
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as copy:
                    audio_file_path = copy.name
                shutil.copyfile(prepared_audio, audio_file_path)
            else:
                # O chunk 0 entra no engine primeiro. Sem isto a corrida pelo
                # lock global do OmniVoice decidia quem falava primeiro, e o
                # perdedor segurava a fila de envio (que é ordenada).
                await self._wait_for_synthesis_turn(sequence_number)
                if not self._speech_ticket_current(speech_ticket.id, speech_chunk):
                    self._queue_skipped_delivery(sequence_number)
                    record_audio_lifecycle(
                        {"event": "AUDIO_DROPPED", "speech_id": speech_chunk["speech_id"], "reason": "speech_ticket_invalid_before_tts"},
                        source="tts",
                    )
                    return
                try:
                    async def synthesize_current():
                        return await self._generate_audio(
                            tts_engine, tts_text, emotion
                        )

                    tts_generation = int(
                        speech_chunk.get("context_revision") or 0
                    )
                    gpu_scheduler.invalidate("tts", tts_generation)
                    audio_file_path = await gpu_scheduler.run_job(
                        "tts",
                        synthesize_current,
                        generation=tts_generation,
                        turn_id=resolved_turn_id,
                    )
                finally:
                    # Em finally: síntese que falha não pode emudecer o resto.
                    await self._release_synthesis_turn(sequence_number)
            if audio_file_path:
                # Backend-observed production of the first audio file.
                synthesis_ms = int((time.monotonic() - latency_started_at) * 1000)
                # Separate clocks: first_* is turn E2E; tts_* is engine-local.
                continuous_behavior.mark_latency(
                    "tts_synthesis_ms", synthesis_ms, turn_id=speech_chunk["turn_id"]
                )
                continuous_behavior.mark_latency(
                    "first_audio_generated", turn_id=speech_chunk["turn_id"]
                )
            if not self._speech_ticket_current(speech_ticket.id, speech_chunk):
                logger.info("Discarding stale synthesized speech before browser delivery")
                if audio_file_path and os.path.exists(audio_file_path):
                    os.remove(audio_file_path)
                await websocket_send(
                    json.dumps(
                        {
                            "type": "celia.tts.failed",
                            "speech_id": speech_chunk["speech_id"],
                            "turn_id": speech_chunk["turn_id"],
                            "utterance_id": speech_chunk["turn_id"],
                            "error": "stale_speech",
                        }
                    )
                )
                # Every sequence must reach the ordered queue. Otherwise one stale
                # sentence leaves a permanent hole and all later speech waits forever.
                self._queue_skipped_delivery(sequence_number)
                return
            
            speech_id = speech_chunk["speech_id"]
            if audio_file_path and not prepared_audio:
                training_trace.archive_tts_output(
                    audio_file_path,
                    text=tts_text,
                    speech_id=speech_id,
                    turn_id=speech_chunk["turn_id"],
                    emotion=emotion or "",
                    engine=tts_engine,
                )
            payload = prepare_audio_payload(
                audio_path=audio_file_path,
                display_text=display_text,
                actions=actions,
            )
            payload["speech_id"] = speech_id
            payload["speech_ticket_id"] = speech_ticket.id
            payload["speech_source"] = "direct_user"
            record_audio_lifecycle({"event": "AUDIO_GENERATED", "speech_id": speech_id}, source="tts")
            payload["turn_id"] = speech_chunk["turn_id"]
            payload["utterance_id"] = speech_chunk["turn_id"]
            payload["phrase_index"] = sequence_number
            latency_trace.register_speech_correlation(
                speech_id,
                str(speech_chunk["turn_id"] or ""),
                sequence_number,
            )
            if prepared_audio:
                payload["bank_playback"] = True
            payload["cancel_token"] = {
                "generation": speech_chunk["generation"],
                "context_revision": speech_chunk["context_revision"],
                "turn_revision": speech_chunk.get("turn_revision"),
                "revision_kind": speech_chunk.get("revision_kind", "context"),
            }
            if emotion:
                register_speech_emotion(speech_id, emotion)
            subtitle_text = (display_text.text or "").strip() or tts_text
            volumes = payload.get("volumes") or []
            slice_length_ms = int(payload.get("slice_length") or 20)
            playback_duration_sec = max(
                0.1, (len(volumes) * slice_length_ms) / 1000.0
            )
            latency_trace.mark(
                resolved_turn_id,
                "tts_end",
                phrase_index=sequence_number,
                chars=len(tts_text),
                synthesis_ms=int((time.monotonic() - latency_started_at) * 1000),
                audio_duration_ms=int(playback_duration_sec * 1000),
            )
            latency_trace.request_gpu_snapshot(
                resolved_turn_id,
                "tts_end",
                phrase_index=sequence_number,
            )
            continuous_behavior.register_speech_duration(
                speech_id, playback_duration_sec
            )
            register_lipsync_envelope(
                speech_id,
                volumes=volumes,
                slice_length_ms=slice_length_ms,
                duration_sec=playback_duration_sec,
            )
            # Arm the subtitle only when THIS ordered payload is actually dispatched.
            # Generation may finish in a burst; arming here would let captions outrun audio.
            payload["_obs_subtitle"] = {
                "text": subtitle_text,
                "duration_sec": max(0.1, (len(volumes) * slice_length_ms) / 1000.0),
                "speech_id": speech_id,
            }
            # Queueing preserves order; the sender marks the backend-to-client boundary.
            self._latency_starts[sequence_number] = latency_started_at
            await self._payload_queue.put((payload, sequence_number))
            if audio_file_path:
                from ..core import avisos as _avisos
                _avisos.tts_ok()  # voz funcionando: zera a contagem de falhas

            # Mirror to local device as a background task
            if self.audio_output_device and audio_file_path:
                logger.debug(f"Mirroring audio to device: {self.audio_output_device}")
                # Use to_thread to ensure sd.query_devices() inside play_audio_to_device
                # doesn't block the async loop even for a millisecond.
                # Track the task so wait_for_local_playback() can await all of them.
                mirror_task = asyncio.create_task(
                    asyncio.to_thread(
                        _play_audio_to_device,
                        audio_file_path,
                        self.audio_output_device,
                        True  # remove_after=True
                    )
                )
                self._mirror_tasks.append(mirror_task)

        except Exception as e:
            if "speech_ticket" in locals() and speech_ticket is not None:
                speech_coordinator.cancel_ticket(speech_ticket.id, "tts_processing_exception")
            logger.error(f"Error preparing audio payload: {e}")
            from ..core import avisos as _avisos
            _avisos.tts_falhou()  # só vira aviso vermelho após falhas seguidas
            try:
                await websocket_send(
                    json.dumps(
                        {
                            "type": "celia.tts.failed",
                            "speech_id": speech_chunk["speech_id"],
                            "turn_id": speech_chunk["turn_id"],
                            "utterance_id": speech_chunk["turn_id"],
                            "error": f"{type(e).__name__}: {e}",
                            "text": (
                                (display_text.text if display_text else "")
                                or tts_text
                            ),
                        }
                    )
                )
            except Exception:
                logger.debug("Could not publish Célia TTS failure event")
            # A failed synthesis is not an audio event. Queue a private skip marker
            # so ordering advances without making the UI pretend Célia is speaking.
            self._latency_starts[sequence_number] = None
            await self._payload_queue.put(({"_skip_delivery": True}, sequence_number))
            # Cleanup here since mirroring wasn't started
            if audio_file_path and os.path.exists(audio_file_path):
                try:
                    os.remove(audio_file_path)
                except Exception:
                    pass

    async def _gate_for(self, sequence_number: int) -> asyncio.Event:
        async with self._synthesis_turn_lock:
            gate = self._synthesis_turn.get(sequence_number)
            if gate is None:
                gate = asyncio.Event()
                # O primeiro chunk do turno nunca espera ninguém.
                if sequence_number <= self._next_sequence_to_send:
                    gate.set()
                self._synthesis_turn[sequence_number] = gate
            return gate

    def _speech_ticket_current(self, ticket_id: str, speech_chunk: dict) -> bool:
        return bool(
            continuous_behavior.tts_chunk_valid(speech_chunk)
            and speech_coordinator.valid(
                ticket_id,
                context_revision=continuous_behavior.current_revision_for(speech_chunk),
            )
        )

    def _queue_skipped_delivery(self, sequence_number: int) -> None:
        self._latency_starts[sequence_number] = None
        self._payload_queue.put_nowait(({"_skip_delivery": True}, sequence_number))

    async def _wait_for_synthesis_turn(self, sequence_number: int) -> None:
        """Garante que o chunk N só chama o engine depois que N-1 já chamou.

        CAUSA RAIZ MEDIDA em 2026-10-01, turno de 05:30:33:

          05:30:35  texto pronto (61 chars)
          05:30:38  OmniVoice gerou o PRIMEIRO áudio  -> speech d4c71203 (seq 1)
          05:30:40  OmniVoice gerou o SEGUNDO áudio   -> speech b59ae4ec (seq 0)
          05:30:40  só então AUDIO_SENT dos dois, e AUDIO_STARTED do seq 0

        O chunk que ficou pronto primeiro tocou por último: o seq 1 venceu a
        corrida pelo engine e o seq 0, que é o único que pode começar a falar,
        terminou 1,5 s depois — segurando a fila de envio, que é ordenada de
        propósito. first_audio_playback_started ficou em 7.278 ms.

        Por que a corrida existia: `speak()` cria uma task por chunk e o
        docstring promete "parallel TTS generation", mas o engine de produção
        serializa tudo num lock global de módulo
        (`src/celia/tts/omnivoice_tts.py:29  _INFERENCE_LOCK = threading.Lock()`).
        Não havia paralelismo real — havia uma corrida cujo vencedor era
        indeterminado. Ordenar a entrada no engine não custa throughput nenhum
        aqui; só torna determinístico quem fala primeiro.

        SE UM DIA o engine deixar de serializar (engine remoto, batch em
        GPU), este portão passa a custar paralelismo e deve ser revisto junto —
        é a condição explícita para reverter.
        """
        if sequence_number <= 0:
            return
        gate = await self._gate_for(sequence_number)
        try:
            await asyncio.wait_for(gate.wait(), timeout=self.SYNTHESIS_TURN_TIMEOUT_S)
        except asyncio.TimeoutError:
            # Rede de segurança: um chunk anterior que morreu sem liberar o
            # portão não pode emudecer o resto do turno. O envio continua
            # ordenado pelo _process_payload_queue, então a ordem audível
            # sobrevive mesmo quando esta otimização desiste.
            logger.warning(
                f"[TTS] portão de síntese expirou em seq={sequence_number}; "
                "seguindo sem ordenar a entrada no engine"
            )

    async def _release_synthesis_turn(self, sequence_number: int) -> None:
        """Libera o próximo chunk. Chamado em finally: falha não trava o turno."""
        gate = await self._gate_for(sequence_number + 1)
        gate.set()

    async def _generate_audio(
        self, tts_engine: TTSInterface, text: str, emotion: Optional[str] = None
    ) -> str:
        """Generate audio file from text.

        Fixed OmniVoice receives clean text once; emotion remains playback metadata.
        """
        nome = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:8]}"
        logger.debug(f"🏃Generating audio for '''{text}'''...")
        if getattr(tts_engine, "fixed_voice_profile", False) is True:
            from ..utils.tts_preprocessor import sanitize_production_tts_text
            # Keep emotion for the bus/player metadata, never for acoustics.
            # Let any engine failure reach the explicit celia.tts.failed event.
            return await tts_engine.async_generate_audio(
                text=sanitize_production_tts_text(text), file_name_no_ext=nome,
            )
        fast_recording = continuous_behavior.profile == "roteiro"
        fast_kwargs = {"fast": True} if fast_recording else {}
        if emotion:
            try:
                return await tts_engine.async_generate_audio(
                    text=text, file_name_no_ext=nome, emotion=emotion, **fast_kwargs
                )
            except TypeError:
                logger.debug(
                    f"{type(tts_engine).__name__} nao aceita emocao; usando preset ativo"
                )
                if fast_kwargs:
                    try:
                        return await tts_engine.async_generate_audio(
                            text=text, file_name_no_ext=nome, emotion=emotion
                        )
                    except TypeError:
                        pass
            except Exception as error:
                # Configuration/preset failures may recover through neutral. A
                # waveform engine that already exhausted its own bounded retry
                # budget explicitly opts out so we do not synthesize the same
                # sentence two more times outside the engine.
                if getattr(error, "retry_neutral", True) is False:
                    logger.warning(
                        "Emotional Célia TTS exhausted bounded synthesis (%s); "
                        "skipping outer neutral retry",
                        type(error).__name__,
                    )
                    raise
                logger.warning(
                    "Emotional Célia TTS failed (%s); retrying neutral audio",
                    type(error).__name__,
                )
                try:
                    return await tts_engine.async_generate_audio(
                        text=text, file_name_no_ext=nome, emotion="neutral", **fast_kwargs
                    )
                except TypeError:
                    logger.debug(
                        f"{type(tts_engine).__name__} nao aceita emocao; usando preset ativo"
                    )
                    if fast_kwargs:
                        try:
                            return await tts_engine.async_generate_audio(
                                text=text, file_name_no_ext=nome, emotion="neutral"
                            )
                        except TypeError:
                            pass
        try:
            return await tts_engine.async_generate_audio(
                text=text, file_name_no_ext=nome, **fast_kwargs
            )
        except TypeError:
            if not fast_kwargs:
                raise
            return await tts_engine.async_generate_audio(
                text=text, file_name_no_ext=nome
            )


    def speech_ids_for_turn(self, turn_id: str) -> list[str]:
        """Return generated speech ids for one turn in browser delivery order."""
        indexed = self._speech_ids_by_turn.get(str(turn_id or ""), {})
        return [indexed[index] for index in sorted(indexed)]

    async def wait_for_payload_delivery(self) -> None:
        """Wait until every generated/failed sequence has crossed the ordered sender."""
        await self._payload_queue.join()

    async def wait_for_local_playback(self) -> None:
        """Wait for all local mirror playback tasks to complete.
        Used as fallback when browser tab is in background and can't ack."""
        tasks = [t for t in self._mirror_tasks if not t.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def clear(self) -> None:
        """Clear all pending tasks and reset state"""
        self.task_list.clear()
        for t in self._mirror_tasks:
            t.cancel()
        self._mirror_tasks.clear()
        if self._sender_task:
            self._sender_task.cancel()
        self._sequence_counter = 0
        self._next_sequence_to_send = 0
        self._latency_starts.clear()
        self._speech_ids_by_turn.clear()
        # Create a new queue to clear any pending items
        self._payload_queue = asyncio.Queue()
