import asyncio
import re
from typing import Optional, Union, Any, List, Dict
import numpy as np
import json
from loguru import logger

from ..message_handler import message_handler
from .types import WebSocketSend, BroadcastContext
from .tts_manager import TTSTaskManager
from ..agent.output_types import SentenceOutput, AudioOutput
from ..agent.input_types import BatchInput, TextData, ImageData, TextSource, ImageSource
from ..asr.asr_interface import ASRInterface
from ..live2d_model import Live2dModel
from ..tts.tts_interface import TTSInterface
from ..utils.stream_audio import prepare_audio_payload
from ..core.continuous_behavior import continuous_behavior
from ..core.observability import observability
from ..core.training_trace import training_trace




def _play_audio_to_device(audio_path: str, device_name: str) -> None:
    """Do not probe PortAudio while the conversation runtime is merely imported."""
    from ..utils.audio_playback import play_audio_to_device

    play_audio_to_device(audio_path, device_name)


# Convert class methods to standalone functions
def create_batch_input(
    input_text: str,
    images: Optional[List[Dict[str, Any]]],
    from_name: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> BatchInput:
    """Create batch input for agent processing"""
    final_text = input_text
    if metadata and "emotion_prompt" in metadata:
        final_text = f"{metadata['emotion_prompt']}\n\nInput: {input_text}"

    asr_refinement = metadata.get("asr_refinement") if isinstance(metadata, dict) else None
    if (
        isinstance(asr_refinement, dict)
        and str(asr_refinement.get("status") or "").upper() == "UNCERTAIN"
    ):
        final_text = (
            "[NOTA INTERNA DE ASR: o trecho falado abaixo foi marcado como incerto. "
            "Use o contexto recente se a intenção estiver clara. Se a incerteza impedir "
            "uma resposta confiável, peça esclarecimento curto. Não invente palavras "
            "ausentes e não trate esta nota como fala do usuário.]\n\n"
            + final_text
        )

    return BatchInput(
        texts=[
            TextData(source=TextSource.INPUT, content=final_text, from_name=from_name)
        ],
        images=[
            ImageData(
                source=ImageSource(img["source"]),
                data=img["data"],
                mime_type=img["mime_type"],
            )
            for img in (images or [])
        ]
        if images
        else None,
        metadata=metadata,
    )


async def process_agent_output(
    output: Union[AudioOutput, SentenceOutput],
    character_config: Any,
    live2d_model: Live2dModel,
    tts_engine: TTSInterface,
    websocket_send: WebSocketSend,
    tts_manager: TTSTaskManager,
    translate_engine: Optional[Any] = None,
    audio_output_device: Optional[str] = None,
    pending_emotion: Optional[str] = None,
    turn_id: str = "",
    context_revision: Optional[int] = None,
) -> str:
    """Process agent output with character information and optional translation"""
    output.display_text.name = character_config.character_name
    output.display_text.avatar = character_config.avatar

    full_response = ""
    try:
        if isinstance(output, SentenceOutput):
            full_response = await handle_sentence_output(
                output,
                live2d_model,
                tts_engine,
                websocket_send,
                tts_manager,
                translate_engine,
                pending_emotion=pending_emotion,
                turn_id=turn_id,
                context_revision=context_revision,
            )
        elif isinstance(output, AudioOutput):
            full_response = await handle_audio_output(
                output, websocket_send, audio_output_device=audio_output_device
            )
        else:
            logger.warning(f"Unknown output type: {type(output)}")
    except Exception as e:
        logger.error(f"Error processing agent output: {e}")
        await websocket_send(
            json.dumps(
                {"type": "error", "message": f"Error processing response: {str(e)}"}
            )
        )

    return full_response


async def handle_sentence_output(
    output: SentenceOutput,
    live2d_model: Live2dModel,
    tts_engine: TTSInterface,
    websocket_send: WebSocketSend,
    tts_manager: TTSTaskManager,
    translate_engine: Optional[Any] = None,
    pending_emotion: Optional[str] = None,
    turn_id: str = "",
    context_revision: Optional[int] = None,
) -> str:
    """Handle sentence output type with optional translation support"""
    full_response = ""
    async for display_text, tts_text, actions in output:
        logger.debug(f"🏃 Processing output: '''{tts_text}'''...")

        if translate_engine:
            if len(re.sub(r'[\s.,!?，。！？\'"』」）】\s]+', "", tts_text)):
                tts_text = translate_engine.translate(tts_text)
            logger.info(f"🏃 Text after translation: '''{tts_text}'''...")
        else:
            logger.debug("🚫 No translation engine available. Skipping translation.")

        full_response += display_text.text
        continuous_behavior.mark_latency("texto_utilizavel", turn_id=turn_id)
        # Outside recording, approved text may be previewed before synthesis.
        # In Roteiro the visible subtitle must start with the actual audio; the
        # ordered TTS payload below owns that timing and prevents a duplicate/early line.
        if continuous_behavior.profile != "roteiro":
            await websocket_send(
                json.dumps(
                    {
                        "type": "celia.text.approved",
                        "name": display_text.name,
                        "text": display_text.text,
                        "turn_id": turn_id,
                        "utterance_id": turn_id,
                    },
                    ensure_ascii=False,
                )
            )
        await tts_manager.speak(
            tts_text=tts_text,
            display_text=display_text,
            actions=actions,
            live2d_model=live2d_model,
            tts_engine=tts_engine,
            websocket_send=websocket_send,
            emotion=pending_emotion,
            turn_id=turn_id,
            prepared_revision=context_revision,
        )
        pending_emotion = None  # aplica só na primeira frase do turno
    return full_response


async def handle_audio_output(
    output: AudioOutput,
    websocket_send: WebSocketSend,
    audio_output_device: Optional[str] = None,
) -> str:
    """Process and send AudioOutput directly to the client"""
    full_response = ""
    async for audio_path, display_text, transcript, actions in output:
        full_response += transcript

        # Mirror to local device if configured
        if audio_output_device:
            logger.debug(f"Mirroring direct audio output to device: {audio_output_device}")
            _play_audio_to_device(audio_path, audio_output_device)

        audio_payload = prepare_audio_payload(
            audio_path=audio_path,
            display_text=display_text,
            actions=actions.to_dict() if actions else None,
        )
        await websocket_send(json.dumps(audio_payload))
    return full_response


async def send_conversation_start_signals(websocket_send: WebSocketSend) -> None:
    """Send initial conversation signals"""
    await websocket_send(
        json.dumps(
            {
                "type": "control",
                "text": "conversation-chain-start",
            }
        )
    )
    await websocket_send(json.dumps({"type": "full-text", "text": "Thinking..."}))


async def process_user_input(
    user_input: Union[str, np.ndarray],
    asr_engine: ASRInterface,
    websocket_send: WebSocketSend,
    turn_id: str = "",
) -> str:
    """Process user input, converting audio to text if needed"""
    if isinstance(user_input, np.ndarray):
        logger.info("Transcribing audio input...")
        with observability.span(
            "asr.transcribe",
            {"turn_id": turn_id, "input_length": int(user_input.size)},
        ):
            input_text = await asr_engine.async_transcribe_np(user_input)
        training_trace.archive_asr_input(
            user_input,
            transcript=input_text,
            engine=asr_engine,
            turn_id=turn_id,
            sample_rate=getattr(asr_engine, "SAMPLE_RATE", 16000),
            channels=getattr(asr_engine, "NUM_CHANNELS", 1),
        )
        await websocket_send(
            json.dumps({"type": "user-input-transcription", "text": input_text})
        )
        return input_text
    return user_input


async def finalize_conversation_turn(
    tts_manager: TTSTaskManager,
    websocket_send: WebSocketSend,
    client_uid: str,
    broadcast_ctx: Optional[BroadcastContext] = None,
    turn_id: str = "",
) -> None:
    """Finalize a conversation turn"""
    if tts_manager.task_list:
        observability.emit("tts_wait", "service.tts", "running")
        await asyncio.gather(*tts_manager.task_list)
        if turn_id:
            # Seal the expected browser media set only after all synthesis tasks
            # have resolved. Playback ACKs may already be arriving; Roteiro keeps
            # them until this seal tells it exactly how many chunks belong here.
            await websocket_send(
                json.dumps(
                    {
                        "type": "celia.tts.turn-sealed",
                        "turn_id": turn_id,
                        "speech_ids": tts_manager.speech_ids_for_turn(turn_id),
                    }
                )
            )
        # Generation completion is not delivery completion. A failed/stale sentence
        # still contributes a skip marker, so the ordered sender cannot be left behind.
        try:
            await asyncio.wait_for(tts_manager.wait_for_payload_delivery(), timeout=15.0)
        except asyncio.TimeoutError:
            logger.error("Timeout esperando entrega ordenada dos payloads TTS")
        await websocket_send(json.dumps({"type": "backend-synth-complete"}))
        observability.emit("tts_wait", "service.tts", "ok")

        # Wait until the browser has actually drained its serialized audio queue.
        # A configured local mirror may be used as fallback; with no mirror configured,
        # an empty local task must not win the race and end the turn before speech ends.
        try:
            browser_task = asyncio.create_task(
                message_handler.wait_for_response(client_uid, "frontend-playback-complete")
            )
            playback_tasks = {browser_task}
            local_task = None
            if tts_manager.audio_output_device:
                local_task = asyncio.create_task(tts_manager.wait_for_local_playback())
                playback_tasks.add(local_task)

            async def _race():
                done, pending = await asyncio.wait(
                    playback_tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()

            await asyncio.wait_for(_race(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning(f"Timeout (30s) esperando frontend playback completion de {client_uid}")
            browser_task.cancel()
            if local_task is not None:
                local_task.cancel()

    await websocket_send(json.dumps({"type": "force-new-message"}))

    if broadcast_ctx and broadcast_ctx.broadcast_func:
        await broadcast_ctx.broadcast_func(
            broadcast_ctx.group_members,
            {"type": "force-new-message"},
            broadcast_ctx.current_client_uid,
        )

    await send_conversation_end_signal(websocket_send, broadcast_ctx)


async def send_conversation_end_signal(
    websocket_send: WebSocketSend,
    broadcast_ctx: Optional[BroadcastContext],
    session_emoji: str = "😊",
) -> None:
    """Send conversation chain end signal"""
    chain_end_msg = {
        "type": "control",
        "text": "conversation-chain-end",
    }

    await websocket_send(json.dumps(chain_end_msg))

    if broadcast_ctx and broadcast_ctx.broadcast_func and broadcast_ctx.group_members:
        await broadcast_ctx.broadcast_func(
            broadcast_ctx.group_members,
            chain_end_msg,
        )

    logger.info(f"😎👍✅ Conversation Chain {session_emoji} completed!")


def cleanup_conversation(tts_manager: TTSTaskManager, session_emoji: str) -> None:
    """Clean up conversation resources"""
    tts_manager.clear()
    logger.debug(f"🧹 Clearing up conversation {session_emoji}.")


EMOJI_LIST = [
    "🐶",
    "🐱",
    "🐭",
    "🐹",
    "🐰",
    "🦊",
    "🐻",
    "🐼",
    "🐨",
    "🐯",
    "🦁",
    "🐮",
    "🐷",
    "🐸",
    "🐵",
    "🐔",
    "🐧",
    "🐦",
    "🐤",
    "🐣",
    "🐥",
    "🦆",
    "🦅",
    "🦉",
    "🦇",
    "🐺",
    "🐗",
    "🐴",
    "🦄",
    "🐝",
    "🌵",
    "🎄",
    "🌲",
    "🌳",
    "🌴",
    "🌱",
    "🌿",
    "☘️",
    "🍀",
    "🍂",
    "🍁",
    "🍄",
    "🌾",
    "💐",
    "🌹",
    "🌸",
    "🌛",
    "🌍",
    "⭐️",
    "🔥",
    "🌈",
    "🌩",
    "⛄️",
    "🎃",
    "🎄",
    "🎉",
    "🎏",
    "🎗",
    "🀄️",
    "🎭",
    "🎨",
    "🧵",
    "🪡",
    "🧶",
    "🥽",
    "🥼",
    "🦺",
    "👔",
    "👕",
    "👜",
    "👑",
]
