from typing import Union, List, Dict, Any, Optional
import asyncio
import json
import re
import wave
from loguru import logger
import numpy as np

from .conversation_utils import (
    create_batch_input,
    process_agent_output,
    send_conversation_start_signals,
    process_user_input,
    finalize_conversation_turn,
    cleanup_conversation,
    EMOJI_LIST,
)
from .types import WebSocketSend
from .tts_manager import TTSTaskManager
from ..chat_history_manager import store_message
from ..service_context import ServiceContext
from ..core.track_emotion_client import publish_emotion
from ..core.orchestrator import orchestrator
from ..core.observability import observability, observed_async_turn
from ..core.continuous_behavior import continuous_behavior
from ..tts.tts_interface import identify_tts_engine
from ..utils.sentence_divider import juntar_pedacos

# Import necessary types from agent outputs
from ..agent.output_types import SentenceOutput, AudioOutput, Actions, DisplayText

TAG_PATTERN = re.compile(r"\[[A-Za-z0-9_-]+\]")
TAG_LABEL_PATTERN = re.compile(r"\[([A-Za-z0-9_-]+)\]")
_PROVIDER_FAILURE_MARKERS = (
    "error calling the chat endpoint:",
    "rate limit exceeded",
    "internal agent error:",
)


def is_provider_failure_output(*texts: str) -> bool:
    """Provider diagnostics are logs, never character dialogue or TTS."""
    normalized = " ".join(str(text or "") for text in texts).casefold()
    return any(marker in normalized for marker in _PROVIDER_FAILURE_MARKERS)


def publish_turn_detector_emotion(emotion: str | None, *, turn_id: str):
    """Publish the detector fallback once before the response stream starts."""
    normalized = str(emotion or "").strip().lower()
    if not normalized:
        return None
    result = publish_emotion(
        normalized,
        source="turn_detector",
        priority=70,
        ttl_sec=None,
        persistent=True,
        metadata={"turn_id": turn_id},
    )
    logger.info(
        "turn emotion fallback={} accepted={} changed={}",
        normalized,
        bool(result and result.get("ok")),
        bool((result or {}).get("bus", {}).get("mudou")),
    )
    return result


def publish_llm_emotion_override(
    emotion: str | None,
    *,
    turn_id: str,
    detector_fallback: str | None,
):
    """An explicit LLM tag overrides the detector fallback for this turn."""
    normalized = str(emotion or "").strip().lower()
    if not normalized:
        return None
    result = publish_emotion(
        normalized,
        source="llm_output_tag",
        priority=80,
        ttl_sec=None,
        persistent=True,
        metadata={"turn_id": turn_id},
    )
    logger.info(
        "llm emotion override={} previous={} accepted={} changed={}",
        normalized,
        str(detector_fallback or "").strip().lower() or "none",
        bool(result and result.get("ok")),
        bool((result or {}).get("bus", {}).get("mudou")),
    )
    return result


def eligible_bank_audio(singleton, decision, bank_context, revision):
    """Only published, intact, script-bound audio may bypass generation."""
    if (continuous_behavior.profile != "roteiro" or not continuous_behavior.script.beats
            or continuous_behavior.revision != revision
            or not decision or decision.resultado != "BANK_HIT"
            or not decision.aplicar or not decision.audio_path):
        return None
    restrictions = (decision.regra or {}).get("restricoes") or {}
    required_keys = ["script_id", "script_version", "beat_id"]
    if bank_context.get("line_id"):
        required_keys.append("line_id")
    if not all(key in restrictions and restrictions[key] == bank_context.get(key)
               for key in required_keys):
        return None
    try:
        asset = singleton.memoria.ativo(decision.ativo_id)
        if (not asset or asset.get("estado") != "publicado" or not asset.get("texto")
                or asset.get("audio_path") != decision.audio_path
                or not singleton.memoria.verificar_audio(decision.ativo_id)):
            return None
        with wave.open(decision.audio_path, "rb") as audio:
            if audio.getnframes() <= 0:
                return None
        return asset
    except (OSError, ValueError, EOFError, wave.Error):
        return None


def normalize_single_tag(
    text: str, keep_first_tag: bool, allowed_tags: Optional[set[str]] = None
) -> tuple[str, Optional[str], bool]:
    """
    Remove all bracket tags from the visible text to prevent them from showing in UI.
    Extract only the first allowed tag label for control/state sync.
    Returns (cleaned_text, extracted_tag_label, tag_applied_flag).
    """
    if not text:
        return text, None, keep_first_tag

    extracted_tag = None
    if not keep_first_tag:
        # Extract the first allowed tag label if it exists (e.g., "[happy]" -> "happy")
        for match in TAG_LABEL_PATTERN.finditer(text):
            tag = match.group(1).lower()
            if allowed_tags is None or tag in allowed_tags:
                extracted_tag = tag
                keep_first_tag = True
                break

    stripped = TAG_PATTERN.sub("", text)
    stripped = re.sub(r"\s+", " ", stripped).strip()

    return stripped, extracted_tag, keep_first_tag


@observed_async_turn("browser.conversation")
async def process_single_conversation(
    context: ServiceContext,
    websocket_send: WebSocketSend,
    client_uid: str,
    user_input: Union[str, np.ndarray],
    images: Optional[List[Dict[str, Any]]] = None,
    session_emoji: str = np.random.choice(EMOJI_LIST),
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Process a single-user conversation turn

    Args:
        context: Service context containing all configurations and engines
        websocket_send: WebSocket send function
        client_uid: Client unique identifier
        user_input: Text or audio input from user
        images: Optional list of image data
        session_emoji: Emoji identifier for the conversation
        metadata: Optional metadata for special processing flags

    Returns:
        str: Complete response text
    """
    metadata = dict(metadata or {})
    turn_id = continuous_behavior.begin_turn(str(metadata.get("turn_id") or ""))
    metadata["turn_id"] = turn_id
    input_text = ""
    bank_decision = None
    bank_asset = None
    operational_singleton = None

    # Create TTSTaskManager for this conversation
    tts_manager = TTSTaskManager(
        audio_output_device=context.system_config.audio_output_device
    )
    full_response = ""  # Initialize full_response here
    response_parts: list[str] = []
    fallback_expression = (
        metadata.get("fallback_expression")
        if isinstance(metadata, dict)
        else None
    )
    fallback_live2d_tag = (
        metadata.get("fallback_live2d_tag")
        if isinstance(metadata, dict)
        else None
    )
    fallback_applied = False
    single_expression_applied = False
    single_tag_applied = False
    provider_failure_suppressed = False
    allowed_tags = (
        set(context.live2d_model.emo_map.keys()) if context.live2d_model else None
    )
    if allowed_tags is not None and continuous_behavior.profile == "roteiro":
        from ..tts.omnivoice_tts import EMOTION_PRESETS, EMOTION_ALIASES
        allowed_tags |= set(EMOTION_PRESETS) | set(EMOTION_ALIASES)

    # The detector is only a candidate while the model is thinking. The face
    # keeps the previous response's persistent affect until the new response
    # actually starts. An explicit LLM tag may still override this candidate.
    response_emotion_started = False

    try:
        # Send initial signals
        await send_conversation_start_signals(websocket_send)
        logger.info(f"New Conversation Chain {session_emoji} started!")

        # Process user input
        input_text = await process_user_input(
            user_input, context.asr_engine, websocket_send, turn_id=turn_id
        )
        # Active playback remains specific to a loaded recording script.
        from ..celia_singleton import CeliaSingleton

        operational_singleton = CeliaSingleton.get_instance()
        bank_context = metadata.get("contexto") or (
            "sistema" if metadata.get("origin") == "system" else "conversa"
        )
        bank_revision = continuous_behavior.revision
        # Owner replies become obsolete only through turn_revision (new
        # submitted turn, real profile switch, script change, reset, real
        # interruption). Speech routed ROUTING_CONTEXT_ONLY moves `revision`
        # only, and must not silence a valid reply. System-origin turns
        # (spontaneous contributions approved by the arbiter) keep the stricter
        # context semantics. See PLANO-CORRECAO-STALE-CONTEXT-ONLY.md.
        reply_uses_turn_revision = metadata.get("origin") != "system"
        prepared_turn_revision = continuous_behavior.turn_revision
        if reply_uses_turn_revision:
            tts_manager._reply_turn_revision = prepared_turn_revision

        def reply_is_stale() -> bool:
            if reply_uses_turn_revision:
                return continuous_behavior.turn_revision != prepared_turn_revision
            return continuous_behavior.revision != bank_revision

        scripted_reply = metadata.get("scripted_reply") if isinstance(metadata, dict) else None
        bank_lookup_text = input_text
        if continuous_behavior.profile == "roteiro":
            script = continuous_behavior.script
            beat = script.beats[max(0, script.position)] if script.beats else {}
            bank_context = {
                "contexto": "roteiro", "script_id": script.script_id,
                "script_version": script.version, "beat_id": beat.get("id"),
            }
            if isinstance(scripted_reply, dict) and scripted_reply.get("line_id"):
                bank_context["line_id"] = str(scripted_reply["line_id"])
                # The ScriptTracker already decided which response is due. Select
                # prepared audio by that stable line id instead of fragile raw ASR text.
                bank_lookup_text = f"roteiro-line:{scripted_reply['line_id']}"
        if continuous_behavior.profile == "roteiro" and isinstance(scripted_reply, dict):
            from ..core.roteiro_audio_pack import roteiro_audio_pack
            bank_asset = roteiro_audio_pack.lookup(
                script_id=script.script_id, script_version=script.version,
                beat_id=bank_context["beat_id"], line_id=scripted_reply.get("line_id"),
                text=scripted_reply.get("text"),
            )
        if bank_asset is None and continuous_behavior.profile != "roteiro":
            bank_decision = operational_singleton.observar_banco(
                bank_lookup_text, contexto=bank_context
            )
            bank_asset = eligible_bank_audio(operational_singleton, bank_decision, bank_context, bank_revision)
        if bank_asset and getattr(context.tts_engine, "fixed_voice_profile", False) is True:
            from ..tts.production_voice import production_descriptor
            if bank_asset.get("profile_fingerprint") != production_descriptor(bank_asset.get("texto", bank_asset.get("text", "")))["profile_fingerprint"]:
                bank_asset = None
        from ..agent.bridge_client import needs_tool
        # R32: Minecraft mode enables tools for every turn. A read-only report
        # already verified by the command center needs no second tool/model pass.
        verified_inventory_report = bool(
            metadata.get("origin") == "system"
            and isinstance(scripted_reply, dict)
            and scripted_reply.get("trusted_local") is True
            and scripted_reply.get("reason") == "verified_inventory_report"
        )
        tool_or_visual_input = bool(images or (not verified_inventory_report and needs_tool(input_text)))
        if verified_inventory_report:
            bank_asset = None
        if tool_or_visual_input:
            bank_asset = None
        observability.emit(
            "input_received",
            "input.browser",
            "running",
            {
                "source": "browser",
                "input_kind": "audio" if isinstance(user_input, np.ndarray) else "text",
                "input_length": len(input_text or ""),
                "image_count": len(images or []),
                "turn_id": turn_id,
            },
        )

        speaker_name = context.character_config.human_name
        if isinstance(metadata, dict):
            for key in ("speaker_name", "from_name", "name"):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    speaker_name = value.strip()
                    break

        # Create batch input
        batch_input = create_batch_input(
            input_text=input_text,
            images=images,
            from_name=speaker_name,
            metadata=metadata,
        )

        # Store user message (check if we should skip storing to history)
        skip_history = metadata and metadata.get("skip_history", False)
        if context.history_uid and not skip_history:
            observability.emit("history_write", "memory.history", "running", {"history_role": "human"})
            store_message(
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="human",
                content=input_text,
                name=speaker_name,
            )
            observability.emit("history_write", "memory.history", "ok", {"history_role": "human"})

        if skip_history:
            logger.debug("Skipping storing user input to history (proactive speak)")

        logger.info(f"User input: {input_text}")
        if images:
            logger.info(f"With {len(images)} images")

        # Add timeout wrapper to detect LLM hangs
        response_timeout_seconds = 60  # Max 60 seconds for LLM response
        logger.info(f"Starting agent output stream processing (timeout: {response_timeout_seconds}s)")

        try:
            # agent.chat yields Union[SentenceOutput, Dict[str, Any]]. A scripted
            # recording reply is already creator-approved content: on BANK_MISS
            # synthesize that exact line instead of asking the LLM to guess it.
            if continuous_behavior.revision != bank_revision:
                bank_asset = None
            scripted_reply = metadata.get("scripted_reply") if isinstance(metadata, dict) else None
            trusted_local_reply = bool(
                isinstance(scripted_reply, dict) and scripted_reply.get("trusted_local") is True
            )
            scripted_text = (
                " ".join(str(scripted_reply.get("text") or "").split())
                if (
                    isinstance(scripted_reply, dict)
                    and (continuous_behavior.profile == "roteiro" or trusted_local_reply)
                    and not tool_or_visual_input
                )
                else ""
            )

            async def fixed_output(text: str, tts_text: str | None = None):
                if reply_is_stale():
                    logger.info(
                        "Discarding stale fixed reply before memory/TTS: turn=%s",
                        turn_id,
                    )
                    return
                # Keep the one agent's RAM memory coherent even when generation
                # is bypassed by a prepared WAV or an approved script line.
                agent = context.agent_engine
                if hasattr(agent, "_add_message") and not skip_history:
                    agent._add_message(input_text, "user")
                    agent._add_message(text, "assistant")
                yield SentenceOutput(
                    display_text=DisplayText(text=text, name=context.character_config.character_name),
                    tts_text=tts_text or text, actions=Actions(),
                )

            if bank_asset:
                agent_output_stream = fixed_output(bank_asset["texto"])
            elif scripted_text:
                logger.info(
                    "[Fixed reply] generation bypassed; live TTS: {}",
                    scripted_text[:120],
                )
                scripted_tts_text = " ".join(str(scripted_reply.get("tts_text") or scripted_text).split())
                agent_output_stream = fixed_output(scripted_text, scripted_tts_text)
            else:
                agent_output_stream = context.agent_engine.chat(batch_input)
            observability.emit("bank_route", "memory.bank_router", "ok", {
                "bank_resultado": "PACK_HIT" if bank_asset and bank_asset.get("line_id") else getattr(bank_decision, "resultado", "BANK_MISS"),
                "bank_ativo_id": bank_asset.get("line_id") if bank_asset else getattr(bank_decision, "ativo_id", None),
                "audio_reused": bool(bank_asset), "tts_avoided": bool(bank_asset),
                "latency_saved_ms": "unavailable", "turn_id": turn_id,
            })
            agent_span = observability.span("agent.basic_memory", {"source": "browser", "turn_id": turn_id})
            agent_span.__enter__()

            async for output_item in agent_output_stream:
                if reply_is_stale():
                    logger.info(
                        "Discarding stale agent output before UI/TTS: turn={} origin={} "
                        "prepared_turn_revision={} current_turn_revision={} "
                        "prepared_revision={} current_revision={}",
                        turn_id,
                        metadata.get("origin"),
                        prepared_turn_revision,
                        continuous_behavior.turn_revision,
                        bank_revision,
                        continuous_behavior.revision,
                    )
                    break
                if (
                    isinstance(output_item, dict)
                    and output_item.get("type") == "tool_call_status"
                ):
                    # Handle tool status event: send WebSocket message
                    output_item["name"] = context.character_config.character_name
                    logger.debug(f"Sending tool status update: {output_item}")

                    observability.record_tool_status(output_item.get("tool_name"), output_item.get("status"))
                    if output_item.get("tool_name") in {
                        "criar_ou_editar_imagem", "gerar_imagem", "editar_imagem"
                    }:
                        # Image dialogue uses normal TTS; keep raw paths/JSON in diagnostics.
                        continue
                    await websocket_send(json.dumps(output_item))

                    # Fatia 7 (claude, 2026-08-11): a mente que FALA narra o
                    # que a mente que FAZ está executando. A fila de eventos
                    # já existia aqui; faltava a voz. Nunca derruba a conversa:
                    # narração é acessório, resposta é o produto.
                    try:
                        from ..core.narrador import obter_narrador

                        await obter_narrador(websocket_send).evento(output_item)
                    except Exception as _exc:  # noqa: BLE001
                        logger.debug(f"[Narrador] ignorado: {type(_exc).__name__}")

                elif isinstance(output_item, (SentenceOutput, AudioOutput)):
                    display_candidate = (
                        output_item.display_text.text
                        if output_item.display_text and output_item.display_text.text
                        else ""
                    )
                    tts_candidate = (
                        output_item.tts_text
                        if isinstance(output_item, SentenceOutput) and output_item.tts_text
                        else ""
                    )
                    if (
                        provider_failure_suppressed
                        or is_provider_failure_output(display_candidate, tts_candidate)
                    ):
                        if not provider_failure_suppressed:
                            logger.error(
                                "Provider failure output suppressed before display/TTS "
                                f"(turn_id={turn_id})"
                            )
                        provider_failure_suppressed = True
                        continue
                    extracted_tag = None
                    # Keep only one expression action for the whole response
                    if output_item.actions and output_item.actions.expressions:
                        if not single_expression_applied:
                            output_item.actions.expressions = [
                                output_item.actions.expressions[0]
                            ]
                            single_expression_applied = True
                        else:
                            output_item.actions.expressions = None

                    # Tags remain metadata; extract at most one emotion per turn.
                    if output_item.display_text and output_item.display_text.text:
                        (
                            output_item.display_text.text,
                            extracted_tag,
                            single_tag_applied,
                        ) = normalize_single_tag(
                            output_item.display_text.text,
                            single_tag_applied,
                            allowed_tags=allowed_tags,
                        )
                        if extracted_tag:
                            publish_llm_emotion_override(
                                extracted_tag,
                                turn_id=turn_id,
                                detector_fallback=fallback_live2d_tag,
                            )
                            response_emotion_started = True

                    # Bracket tags are control metadata, never spoken words. The
                    # visible text was already cleaned above; clean the TTS channel
                    # independently so tags such as [excited] cannot leak to audio.
                    if isinstance(output_item, SentenceOutput) and output_item.tts_text:
                        if not extracted_tag and not single_tag_applied:
                            _, extracted_tag, single_tag_applied = normalize_single_tag(
                                output_item.tts_text, False, allowed_tags=allowed_tags,
                            )
                            if extracted_tag:
                                publish_llm_emotion_override(
                                    extracted_tag,
                                    turn_id=turn_id,
                                    detector_fallback=fallback_live2d_tag,
                                )
                                response_emotion_started = True
                        output_item.tts_text = re.sub(
                            r"\s+", " ", TAG_PATTERN.sub("", output_item.tts_text)
                        ).strip()

                    # Commit the detector candidate only when a real response
                    # chunk exists. Human input/transcription never preempts the
                    # affect that belongs to the previous response.
                    if not response_emotion_started:
                        publish_turn_detector_emotion(
                            fallback_live2d_tag,
                            turn_id=turn_id,
                        )
                        response_emotion_started = True

                    if (
                        not fallback_applied
                        and fallback_expression is not None
                        and not single_expression_applied
                        and (
                            output_item.actions is None
                            or not output_item.actions.expressions
                        )
                    ):
                        output_item.actions = output_item.actions or Actions()
                        output_item.actions.expressions = [fallback_expression]
                        single_expression_applied = True
                        fallback_applied = True

                    # Handle SentenceOutput or AudioOutput
                    logger.debug(f"Processing agent output item (type: {type(output_item).__name__})")
                    if bank_asset:
                        await tts_manager.speak(
                            tts_text=output_item.tts_text, display_text=output_item.display_text,
                            actions=output_item.actions, live2d_model=context.live2d_model,
                            tts_engine=context.tts_engine, websocket_send=websocket_send,
                            turn_id=turn_id, prepared_audio=bank_asset["audio_path"],
                            prepared_revision=bank_revision,
                            # Prepared bank audio is immutable; visual emotion
                            # travels independently through the Emotion Bus.
                            emotion=bank_asset.get("emotion"),
                        )
                        response_part = output_item.tts_text
                    else:
                        response_part = await process_agent_output(
                            output=output_item,
                            character_config=context.character_config,
                            live2d_model=context.live2d_model,
                            tts_engine=context.tts_engine,
                            websocket_send=websocket_send,  # Pass websocket_send for audio/tts messages
                            tts_manager=tts_manager,
                            translate_engine=context.translate_engine,
                            audio_output_device=context.system_config.audio_output_device,
                            # Emotion already went through the visual bus above.
                            # Never feed the LLM tag into acoustic synthesis.
                            pending_emotion=None,
                            turn_id=turn_id,
                            context_revision=bank_revision,
                        )
                    # Reassemble at chunk boundaries so stripped sentence pieces do not collide.
                    response_part_str = (
                        str(response_part) if response_part is not None else ""
                    )
                    response_parts.append(response_part_str)
                    full_response = juntar_pedacos(response_parts)
                    observability.emit(
                        "output_chunk",
                        "output.audio" if isinstance(output_item, AudioOutput) else "output.text",
                        "ok",
                        {"output_kind": type(output_item).__name__, "output_length": len(response_part_str), "turn_id": turn_id},
                    )
                    logger.debug(f"Agent output processed successfully ({len(response_part_str)} chars)")
                else:
                    logger.warning(
                        f"Received unexpected item type from agent chat stream: {type(output_item)}"
                    )
                    logger.debug(f"Unexpected item content: {output_item}")

            agent_span.finish()

        except Exception as e:
            observability.record_error("agent.basic_memory", e)
            if "agent_span" in locals():
                agent_span.finish("error", {"error_type": type(e).__name__})
            logger.exception(
                f"Error processing agent response stream: {e}"
            )  # Log with stack trace
            await websocket_send(
                json.dumps(
                    {
                        "type": "error",
                        "message": f"Error processing agent response: {str(e)}",
                    }
                )
            )
            # full_response will contain partial response before error
        # --- End processing agent response ---

        logger.info(f"Agent output stream completed. Full response length: {len(full_response)} chars")
        if metadata.get("origin") == "system" and full_response:
            # Evidence of what the persona said from a Jev fact sheet (the turn is not stored in chat history).
            logger.info("[Jev→Célia] fala: {}", " ".join(full_response.split())[:400])

        # finalize_conversation_turn is the single owner of waiting for TTS,
        # ordered payload delivery and browser playback completion.
        await finalize_conversation_turn(
            tts_manager=tts_manager,
            websocket_send=websocket_send,
            client_uid=client_uid,
            turn_id=turn_id,
        )

        if "__API_NOT_SUPPORT_TOOLS__" in full_response:
            logger.warning(
                "Filtered internal __API_NOT_SUPPORT_TOOLS__ token from final response."
            )
            full_response = full_response.replace("__API_NOT_SUPPORT_TOOLS__", "").strip()

        if context.history_uid and full_response and not skip_history:  # Proactive prompts stay out of durable history
            observability.emit("history_write", "memory.history", "running", {"history_role": "ai"})
            store_message(
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="ai",
                content=full_response,
                name=context.character_config.character_name,
                avatar=context.character_config.avatar,
            )
            observability.emit("history_write", "memory.history", "ok", {"history_role": "ai"})
            # Mostra nome da IA na resposta
            ai_name = context.character_config.character_name if context.character_config.character_name else "AI"
            logger.info(f"{ai_name}: {full_response}")

        # Só chega aqui depois de finalize_conversation_turn confirmar entrega/playback.
        # Inclusive falas proativas (skip_history=True) precisam alimentar o contexto
        # social, senão o orquestrador esquece o que a própria Célia acabou de dizer.
        if full_response:
            orchestrator.process_celia_utterance(full_response)

        return full_response  # Return accumulated full_response

    except asyncio.CancelledError:
        observability.emit("turn_cancelled", "celia.core", "cancelled", {"reason": "interrupted"})
        logger.info(f"🤡👍 Conversation {session_emoji} cancelled because interrupted.")
        raise
    except Exception as e:
        observability.record_error("browser.conversation", e)
        logger.error(f"Error in conversation chain: {e}")
        await websocket_send(
            json.dumps({"type": "error", "message": f"Conversation error: {str(e)}"})
        )
        raise
    finally:
        if input_text and operational_singleton is not None:
            try:
                character_config = context.character_config
                agent_config = getattr(character_config, "agent_config", None)
                agent_settings = getattr(agent_config, "agent_settings", None)
                basic_agent = getattr(agent_settings, "basic_memory_agent", None)
                operational_singleton.registrar_turno_operacional(
                    turn_id=turn_id,
                    entrada=input_text,
                    resposta=full_response,
                    personagem=getattr(character_config, "conf_uid", "celia_001"),
                    modelo=(
                        getattr(agent_config, "llm_provider", None)
                        or getattr(basic_agent, "llm_provider", None)
                    ),
                    tts_engine=identify_tts_engine(context.tts_engine),
                    contexto=bank_context,
                    fonte=str(metadata.get("origin") or "owner"),
                    decisao=bank_decision,
                    fallback_generation=not bool(bank_asset),
                )
            except Exception as exc:  # noqa: BLE001 - observability must not break cleanup
                logger.warning(f"Operational memory log ignored: {type(exc).__name__}")
        cleanup_conversation(tts_manager, session_emoji)
