"""
Bridge Routes - API REST para o Servidor de Tools
===================================================
Endpoints HTTP que permitem ao servidor principal (porta 12393)
chamar ferramentas MCP hospedadas neste servidor (porta 12394).

Endpoints:
  GET  /bridge/health   - Health check
  POST /bridge/process  - Processa texto com ferramentas MCP
  POST /bridge/raw      - Versão debug com todos os chunks
"""

import asyncio
import ast
import base64
import json
import hmac
import os
import re
import shutil
import sys
import time
import traceback
import uuid
import wave
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from .agent.input_types import BatchInput, ImageData, ImageSource, TextData, TextSource
from .agent.output_types import AudioOutput, DisplayText, SentenceOutput
from .celia_singleton import CeliaSingleton
from .chat_history_manager import store_message
from .core.ingress_security import evaluate_text_async, new_context
from .live_mode import gate as live_gate
from .core.live_comment_readback import read_live_comment
from .core.raizes import CACHE, RAIZ, VAR
from .core import social_score
from .core.obs_subtitles import obs_subtitles
from .core.observability import observability, observed_async_turn
from .core.track_emotion_client import publish_emotion, register_lipsync_envelope
from .core.continuous_behavior import PROFILE_NAMES, canonical_profile, continuous_behavior
from .bridge.schemas import BridgeEventV2, EventReceiptV2, EventType
from .core.event_priority import AdmissionError, classify_priority, expiry_deadline_mono
from .core.turn_arbiter import ScheduledTurn, VerifiedActor
from .core.speech_coordinator import speech_coordinator
from .integrations.discord.relationship import relationship_store
from .service_context import ServiceContext
from .tts.tts_factory import TTSFactory
from .utils.stream_audio import prepare_audio_payload

# ═══════════════════════════════════════════════════════════════


def _serve_generated_audio(audio_path: str) -> str:
    """Promote legacy relative TTS output into the cache mounted at `/cache`."""
    path = Path(audio_path)
    if not path.is_absolute():
        path = (RAIZ / path).resolve()
    try:
        path.relative_to(CACHE.resolve())
        return str(path)
    except ValueError:
        pass
    legacy_cache = (RAIZ / "cache").resolve()
    try:
        path.relative_to(legacy_cache)
    except ValueError:
        return str(path)
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / path.name
    shutil.move(str(path), target)
    return str(target)


def _bridge_approved_event(text: str) -> dict[str, Any]:
    """Build the public approval event consumed by the roteiro live view."""
    return {
        "type": "celia.text.approved",
        "name": "Célia",
        "text": str(text or "").strip(),
        "source": "bridge.process",
    }
# Coordenação de fala (cooldown global + prioridade)
# ═══════════════════════════════════════════════════════════════
# Várias fontes podem pedir fala (Game Mind, alert_watcher, conversa).
# Para não falarem por cima, /bridge/speak aplica um cooldown global:
# crítico sempre passa; os demais só passam se o cooldown expirou OU se
# têm prioridade maior que a última fala.
_SPEAK_COOLDOWN_S = float(os.environ.get("CELIA_SPEAK_COOLDOWN_S", "6"))
_PRIO_RANK = {"low": 0, "normal": 1, "high": 2, "critical": 3}
_speak_state = {"ts": 0.0, "prio": 0}

# Dispositivo de saida onde a voz do Codex (external-turn) tambem toca, alem do
# broadcast pro browser. Default: alto-falantes reais do PC ("High Definition
# Audio Device" e substring unico — as saidas NVIDIA sao "High Definition Audio"
# sem "Device"). Defina como "" para desativar o playback server-side.
_CODEX_AUDIO_DEVICE = os.environ.get(
    "CELIA_CODEX_AUDIO_DEVICE", "High Definition Audio Device"
)

_PUBLIC_EMOTION_TAG = re.compile(r"\[[A-Za-z][A-Za-z0-9_-]{0,31}\]")


def _play_audio_to_device(audio_path: str, device_names: str | list[str]) -> None:
    """Delay sounddevice initialization until a route actually requests playback."""
    from .utils.audio_playback import play_audio_to_device

    play_audio_to_device(audio_path, device_names)


async def _generate_bridge_audio(tts_engine, text: str, file_name: str, emotion: str | None):
    """Call the shared TTS contract, preserving legacy engines without emotion."""
    if getattr(tts_engine, "fixed_voice_profile", False) is True:
        from .utils.tts_preprocessor import sanitize_production_tts_text
        return await tts_engine.async_generate_audio(
            text=sanitize_production_tts_text(text), file_name_no_ext=file_name,
        )
    tts_kwargs = {"text": text, "file_name_no_ext": file_name}
    if emotion:
        tts_kwargs["emotion"] = emotion
    if continuous_behavior.profile == "roteiro":
        tts_kwargs["fast"] = True
    try:
        return await tts_engine.async_generate_audio(**tts_kwargs)
    except TypeError as error:
        if "fast" in tts_kwargs:
            tts_kwargs.pop("fast", None)
            try:
                return await tts_engine.async_generate_audio(**tts_kwargs)
            except TypeError as retry_error:
                error = retry_error
        if emotion and "unexpected keyword argument 'emotion'" in str(error):
            tts_kwargs.pop("emotion", None)
            return await tts_engine.async_generate_audio(**tts_kwargs)
        raise


def _speak_allowed(priority: str | None) -> tuple[bool, float]:
    """Decide se uma fala pode tocar agora. Retorna (permitido, segundos_restantes)."""
    prio = _PRIO_RANK.get((priority or "normal").lower(), 1)
    now = time.time()
    elapsed = now - _speak_state["ts"]
    # Crítico nunca é bloqueado; não-crítico respeita cooldown e preempção
    if prio < _PRIO_RANK["critical"]:
        if elapsed < _SPEAK_COOLDOWN_S and prio <= _speak_state["prio"]:
            return False, _SPEAK_COOLDOWN_S - elapsed
    _speak_state["ts"] = now
    _speak_state["prio"] = prio
    return True, 0.0


# Serializa a fala em vez de jogá-la fora. Medido em 2026-08-24: 18 de 20 falas
# enviadas em sequência foram descartadas em 1 ms com "cooldown global", sem
# erro visível — em jogo, com comentário por lance, a Célia emudecia sozinha. O
# cooldown continua existindo para não falarem por cima; o que muda é que quem
# chega dentro da janela ESPERA a vez em vez de sumir.
_speak_queue_lock = asyncio.Lock()


async def _aguardar_vez_de_falar(priority: str | None) -> tuple[bool, float, float]:
    """Espera o cooldown e qualquer playback ativo antes de liberar nova fala.

    Só desiste quando a espera passaria do próprio tamanho da janela — aí a fala
    já perdeu o contexto e insistir seria pior do que avisar.
    """
    inicio = time.time()
    async with _speak_queue_lock:
        # Cooldown por relógio sozinho não impede sobreposição: uma fala longa
        # pode continuar tocando depois da janela e uma nova /bridge/speak pode
        # substituir/cortar o áudio no browser. Aguarde a evidência real de fim
        # do playback. Interrupções humanas explícitas usam outras rotas.
        playback_deadline = time.monotonic() + 35.0
        while (
            continuous_behavior.active_speech_id
            and continuous_behavior.playback_state in {"playing", "cancel_requested"}
            and time.monotonic() < playback_deadline
        ):
            await asyncio.sleep(0.1)

        permitido, restante = _speak_allowed(priority)
        if permitido:
            return True, 0.0, time.time() - inicio
        if restante > _SPEAK_COOLDOWN_S:
            return False, restante, time.time() - inicio
        await asyncio.sleep(restante)
        permitido, restante = _speak_allowed(priority)
        return permitido, restante, time.time() - inicio


def _speech_text_error(text: str | None) -> str | None:
    """Reject machine-shaped payloads before they can enter the TTS boundary."""
    if not isinstance(text, str) or not text.strip():
        return "Texto de fala vazio ou inválido."

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, (dict, list)):
        return "Texto estrutural não pode ser sintetizado. Envie somente fala limpa."

    stripped = text.strip()
    if stripped[:1] in {"{", "["}:
        try:
            literal = ast.literal_eval(stripped)
        except (SyntaxError, ValueError):
            technical_fields = ("action", "emotion", "commentary", "decision", "speak")
            if any(
                f'"{field}"' in stripped or f"'{field}'" in stripped
                for field in technical_fields
            ):
                return "Texto estrutural não pode ser sintetizado. Envie somente fala limpa."
            return None
        if isinstance(literal, (dict, list)):
            return "Texto estrutural não pode ser sintetizado. Envie somente fala limpa."
    return None


# ═══════════════════════════════════════════════════════════════
# Request/Response Models
# ═══════════════════════════════════════════════════════════════


class BridgeRequest(BaseModel):
    """Request body para /bridge/process e /bridge/raw"""

    text: str
    history: list[dict[str, str]] | None = None
    # Fatia 8 (claude, 2026-08-11): quem declara fonte externa passa pelo
    # guardião com bloqueio de verdade. Quem não declara passa em modo
    # observação — o guardião registra o que BLOQUEARIA, sem cortar.
    # Motivo de não bloquear por padrão: esta rota também serve chamador
    # interno hoje, e derrubar um fluxo do Pai às 3h da manhã pra "endurecer"
    # é pior do que medir primeiro. O log dá ao Max o número real de falso
    # positivo antes de virar a chave.
    fonte: str | None = None
    autor: str | None = None
    autor_id: str | None = None
    guild_id: str | None = None
    guild_name: str | None = None
    channel_id: str | None = None
    channel_name: str | None = None
    is_dm: bool = False
    message_id: str | None = None
    interaction_kind: str | None = None
    images: list[str] | None = None
    synthesize_audio: bool = False
    discord_voice_deadline: float | None = None
    prepare_only: bool = False  # prepara texto/WAV sem publicar fala, legenda ou histórico
    read_aloud_input: bool = False


def _bridge_agent_metadata(request: BridgeRequest) -> dict[str, Any]:
    """Attach scene data without allowing machine contracts into speech judging."""
    origin = (request.fonte or "bridge").strip().lower()
    is_game = origin in {"games", "game", "minecraft"}
    return {
        "origin": origin,
        "scene_profile": "react" if origin == "react_video" else ("gameplay" if is_game else "chat"),
        "machine_response": not bool(request.synthesize_audio) and is_game,
        "recent_dialogue": request.history or [],
        "discord_author_id": request.autor_id or "",
        "discord_guild_id": request.guild_id or "",
        "discord_guild_name": request.guild_name or "",
        "discord_channel_id": request.channel_id or "",
        "discord_channel_name": request.channel_name or "",
        "discord_is_dm": bool(request.is_dm),
        "discord_message_id": request.message_id or "",
        "discord_interaction_kind": request.interaction_kind or "",
    }


class SpeakRequest(BaseModel):
    """Request body para /bridge/speak.

    Use `text` para sintetizar uma fala com o `tts_engine` carregado. Na configuração atual de produção, esse engine é o OmniVoice V2.
    Use `audio_path` para reproduzir um WAV já pronto (ex.: alerta pré-gerado),
    sem re-sintetizar. Pelo menos um dos dois é obrigatório.

    `priority` coordena quem fala quando há várias fontes (Game Mind, alertas):
    low | normal | high | critical. Crítico sempre fala; os demais respeitam o
    cooldown global, salvo se tiverem prioridade maior que a última fala.
    """

    text: str | None = None
    audio_path: str | None = None
    priority: str | None = "normal"
    emotion: str | None = None
    emotion_source: str | None = None
    emotion_priority: int | None = None
    subtitle_text: str | None = None
    record_context: bool = False
    context_text: str | None = None
    context_name: str | None = "Célia"
    # Quando True, injeta `context_text` como observação de jogo (role=user, "Jogo"),
    # não como fala da Célia. Usado por alert_watcher/game_mind para passar a cena
    # rica ao LLM sem poluir o histórico como se ela tivesse falado.
    context_as_observation: bool = False
    # Quando True, a resposta HTTP só libera o chamador após o ACK real do
    # player (ou fallback temporal quando não existe player conectado).
    wait_for_playback: bool = False
    # 23/10 (falas roteirizadas da série): com record_context, grava a fala como turno DELA só DEPOIS que o áudio tocou
    # (o LLM passa a ter a fala no contexto quando o Criador comentar). Cancelada antes de tocar = não grava; cortada = grava marcada.
    record_after_playback: bool = False

    # Autoridade central de fala. Chamadores antigos podem omitir tudo e seguem
    # como "bridge"; produtores novos declaram a origem para permitir preempção,
    # dedupe por evento e descarte de fala atrasada.
    speech_source: str = "bridge"
    speech_event_key: str | None = None
    speech_ttl_ms: int = 8000
    speech_take_id: str | None = None
    speech_match_id: str | None = None
    speech_board_revision: int | None = None
    speech_context_revision: int | None = None
    speech_interruptible: bool = True


class EmotionRequest(BaseModel):
    emotion: str
    source: str = "minecraft"
    priority: int = 40
    ttl_sec: float = 4
    persistent: bool = False
    metadata: dict[str, Any] | None = None


class BehaviorProfileRequest(BaseModel):
    profile: str


class BehaviorProfileSettingsRequest(BaseModel):
    settings: dict[str, Any]
    expected_revision: int | None = Field(default=None, ge=0)


class BehaviorScriptRequest(BaseModel):
    script: dict[str, Any]


class BehaviorSessionResetRequest(BaseModel):
    clear_script: bool = True


def _discord_env_file() -> Path:
    """Arquivo de segredos onde so se checa se a variavel do bot esta preenchida (bool; nunca o valor)."""
    return Path(RAIZ) / "secrets" / ".env"


def _require_loopback(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host.lower() not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="Local behavior control only")


_CORPOS_VALIDOS = ("2d", "3d")


class BodyChoice(BaseModel):
    """Corpo escolhido na interface: so '2d' ou '3d' (validado no handler)."""

    corpo: str = Field(..., max_length=8)


def _corpo_arquivo() -> Path:
    return VAR / "celia_body.json"


class ModeChoice(BaseModel):
    """Modo de superficie escolhido na interface: 'pet' (so o Pet) ou 'janela' (so a interface)."""

    modo: str = Field(..., max_length=8)


_MODOS_VALIDOS = ("pet", "janela")


def _modo_arquivo() -> Path:
    return VAR / "celia_mode.json"


class CorpoOpcoes(BaseModel):
    """Opcoes da aba Corpo (oculos, slime) e a abertura de boca do slime. Todos opcionais: cada POST manda so o que mudou."""

    oculos: bool | None = None
    slime: bool | None = None
    # HUD Minecraft: UMA interface, UM booleano. Voz ("ligar mapa"...) e o interruptor do Corpo escrevem este mesmo campo.
    hud_minecraft: bool | None = None
    # Modo Minecraft (2026-10-25): desligado = live so de conversa; a serie do Minecraft deixa de calar a fala espontanea e o jogo sai do pensamento.
    modo_minecraft: bool | None = None
    # Enquete da live no modo mobile (2026-10-25): so liga/desliga a exibicao; o placar mora em core/enquete.py.
    enquete_mobile: bool | None = None
    chat_tiktok: bool | None = None
    boca: float | None = Field(default=None, ge=0.0, le=1.0)


def _opcoes_arquivo() -> Path:
    return VAR / "celia_corpo_opcoes.json"


def ler_opcoes_corpo() -> dict:
    """Opcoes persistidas do Corpo. Campo ausente (arquivo antigo) herda o padrao: nada se perde na migracao."""
    saida = {"oculos": False, "slime": False, "hud_minecraft": False, "modo_minecraft": True, "enquete_mobile": False, "chat_tiktok": True}
    try:
        dados = json.loads(_opcoes_arquivo().read_text(encoding="utf-8"))
        if isinstance(dados, dict):
            for k in saida:
                if isinstance(dados.get(k), bool):
                    saida[k] = dados[k]
    except (OSError, ValueError):
        pass
    return saida


def gravar_opcoes_corpo(mudancas: dict) -> dict:
    """Grava so as chaves booleanas conhecidas que mudaram (disco, rename atomico) e devolve o estado lido de volta."""
    atual = ler_opcoes_corpo()
    novo = {**atual, **{k: v for k, v in mudancas.items() if k in atual and isinstance(v, bool)}}
    if novo != atual or not _opcoes_arquivo().exists():
        alvo = _opcoes_arquivo()
        alvo.parent.mkdir(parents=True, exist_ok=True)
        tmp = alvo.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(novo), encoding="utf-8")
        os.replace(tmp, alvo)
        observability.emit("corpo_opcoes", "interface", json.dumps(novo))
    return ler_opcoes_corpo()


def definir_hud_minecraft(ligada: bool) -> dict:
    """Unico ponto que muda a visibilidade da HUD por voz. Idempotente; confirma so depois de ler o estado de volta."""
    antes = ler_opcoes_corpo()["hud_minecraft"]
    depois = gravar_opcoes_corpo({"hud_minecraft": bool(ligada)})["hud_minecraft"]
    return {"hud_minecraft": depois, "changed": antes != depois, "confirmed": depois is bool(ligada)}


# Boca do slime vem do microfone da interface e e so para o Pet (que nao ouve o microfone): fica so em memoria, com carimbo.
_BOCA_SLIME = {"v": 0.0, "em": 0.0}
_BOCA_VALIDADE_S = 0.6


def _pet_vivo(proc_dir: Path | None = None) -> bool | None:
    """True se o app Electron do Pet esta rodando (somente leitura de /proc).

    O processo principal leva o diretorio do app como argumento: `.../tools/pet-electron`.
    `None` quando /proc nao pode ser lido (sem prova nem para um lado nem para o outro).
    """
    base = proc_dir or Path("/proc")
    try:
        entradas = [p for p in base.iterdir() if p.name.isdigit()]
    except OSError:
        return None
    meu = str(os.getpid())
    for entrada in entradas:
        if entrada.name == meu:
            continue
        try:
            argumentos = (entrada / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        for arg in argumentos:
            if arg.endswith(b"/tools/pet-electron") and not arg.startswith(b"--"):
                return True
    return False


class SpeakResponse(BaseModel):
    """Response body de /bridge/speak"""

    ok: bool
    error: str | None = None
    clients: int = 0
    skipped: bool = False  # True quando suprimido pelo cooldown global
    audio_duration_sec: float = 0.0  # duração do WAV gerado/tocado (usado por chamadores que querem esperar terminar)
    speech_id: str | None = None
    playback_status: str | None = None
    timed_out: bool = False
    recorded: str | None = None  # record_after_playback: "spoken" | "interrupted" | None (não gravou)


_PLAYED = {"ended", "finished", "complete", "completed", "ok", "success", "local_duration_elapsed", "timeout"}


def _series_scene_token_ok(http_request: Any) -> bool:
    """24/10 (missão EP1, risco R2): a fala roteirizada da série vem do Jev com a credencial do dono (X-Jev-Token). Sem ela, nada muda."""
    headers = getattr(http_request, "headers", None) or {}
    supplied = headers.get("x-jev-token") or headers.get("X-Jev-Token") or ""
    if not supplied:
        return False
    try:
        from .core.minecraft_series import _token

        expected = _token()
    except Exception:
        return False
    import hmac

    return bool(expected) and hmac.compare_digest(str(supplied), str(expected))


def _speak_guard_context(request: Any, http_request: Any = None):
    """Contexto do guardião para record_context do /speak. Em 21/10 o revisor GPT barrou 4 falas inofensivas do Jev como prompt injection e a
    fala nem tocou; as falas do diretor de cena são texto fixo do repositório, então com a credencial do dono passam só pelo guardião local."""
    if getattr(request, "speech_source", None) == "series_scene" and _series_scene_token_ok(http_request):
        return new_context("bridge_speak_series_scene", external=False)
    return new_context("bridge_speak_context", external=True)


def _record_spoken_line(singleton: Any, text: str, name: str, playback_status: str | None) -> str | None:
    """Fala roteirizada que REALMENTE tocou vira turno dela (histórico em disco + memória do agente).

    timeout = o áudio foi transmitido mas o navegador não confirmou: conta como falada. Cancelada pelo navegador = ela
    começou e foi cortada: entra marcada, para ela não "lembrar" de uma frase inteira que ninguém ouviu inteira.
    Qualquer outro estado (pendente, desconhecido) não grava nada.
    """
    status = str(playback_status or "")
    if status in _PLAYED:
        line, kind = text, "spoken"
    elif status == "cancelled":
        line, kind = f"{text} (fui interrompida)", "interrupted"
    else:
        return None
    context = singleton.context
    if context is None:
        return None
    if context.history_uid:
        store_message(context.character_config.conf_uid, context.history_uid, "ai", line, name=name)
    agent = context.agent_engine
    if agent is not None and hasattr(agent, "_add_message"):
        agent._add_message(line, "assistant", display_text=DisplayText(text=line, name=name))
    return kind


class ExternalTurnRequest(BaseModel):
    """A named Codex turn played by the browser before reaching Celia's LLM."""

    text: str
    speaker_name: str = "Codex"
    interrupt_current: bool = True


class ExternalTurnResponse(BaseModel):
    ok: bool
    speech_id: str | None = None
    playback_status: str | None = None
    timed_out: bool = False
    submitted: bool = False
    audio_duration: float | None = None
    audio_path: str | None = None
    clients: int = 0
    error: str | None = None


class ToolInfo(BaseModel):
    """Info sobre uma tool executada"""

    type: str = "tool_result"
    name: str
    result: str


class BridgeResponse(BaseModel):
    """Response body de /bridge/process"""

    response: str
    tool_used: bool
    tools: list[ToolInfo]
    error: str | None = None
    elapsed_ms: int | None = None
    audio_paths: list[str] = Field(default_factory=list)
    speech_id: str | None = None
    # The existing Célia emotion engine selected for this turn, when the
    # singleton is available. External adapters may translate it to a body.
    emotion: str | None = None


class BridgeHealthResponse(BaseModel):
    """Response body de /bridge/health"""

    status: str
    agent_loaded: bool
    mcp_loaded: bool
    service: str = "tool-bridge"
    port: int = 12393


class BridgeRawChunk(BaseModel):
    """Um chunk individual do stream"""

    type: str  # "text", "tool_call", "tool_result", "error"
    content: str
    metadata: dict[str, Any] | None = None


class BridgeRawResponse(BaseModel):
    """Response body de /bridge/raw"""

    chunks: list[BridgeRawChunk]
    full_response: str
    elapsed_ms: int
    error: str | None = None


# ═══════════════════════════════════════════════════════════════
# Router Factory
# ═══════════════════════════════════════════════════════════════

# Fontes que são, por definição, texto de estranho na internet. Ordem do Pai
# (decisão 10): tudo que vem daqui passa pelo guardião ANTES de chegar nela.
FONTES_EXTERNAS = (
    "discord",
    "youtube",
    "chat",
    "twitch",
    "minecraft",
    "vrchat",
    "externa",
    "publico",
    "react_video",
)
_MAX_BRIDGE_IMAGES = 12
_MAX_BRIDGE_IMAGE_BYTES = 3 * 1024 * 1024
_PROVIDER_ERROR_MARKERS = (
    "error calling the chat endpoint:",
    "rate limit exceeded",
    "internal agent error:",
)


def _public_bridge_response(text: str, *, discord: bool = False) -> str:
    """Never publish provider errors as if they were Célia's response."""
    from .core.dialogue_hygiene import is_infrastructure_text, remove_mixed_script_words
    normalized = re.sub(r"\s+", " ", _PUBLIC_EMOTION_TAG.sub("", text or "")).strip()
    if discord:
        cleaned = remove_mixed_script_words(normalized)
        if cleaned != normalized:
            logger.warning("Discord response contained mixed-script words; removed before playback")
        normalized = cleaned
    if is_infrastructure_text(normalized) or any(marker in normalized.lower() for marker in _PROVIDER_ERROR_MARKERS):
        return ""
    # The normal browser conversation extracts emotion tags before display and
    # TTS. `/bridge/process` collects the stream directly, so apply the same
    # boundary here; `[happy]` is control metadata, never Célia's spoken text.
    return normalized


def _bridge_emotion_context(text: str) -> tuple[str, str | None]:
    """Use the singleton's existing emotion engine for externally routed turns."""
    singleton = CeliaSingleton.get_instance()
    if singleton.context is None:
        return "", None
    prompt = singleton.emotions.process_input(text)
    return prompt, singleton.emotions.state.emotion


def _bridge_images(data_urls: list[str] | None) -> list[ImageData]:
    """Validate image data URLs before passing public media to the agent."""
    if not data_urls:
        return []
    if len(data_urls) > _MAX_BRIDGE_IMAGES:
        raise ValueError(f"Limite de {_MAX_BRIDGE_IMAGES} imagens por turno excedido.")

    images: list[ImageData] = []
    for data_url in data_urls:
        if not isinstance(data_url, str) or not data_url.startswith(
            "data:image/jpeg;base64,"
        ):
            raise ValueError("A bridge aceita somente JPEGs codificados como data URL.")
        try:
            encoded = data_url.split(",", 1)[1]
            decoded = base64.b64decode(encoded, validate=True)
        except (IndexError, ValueError) as exc:
            raise ValueError("Imagem da bridge invalida.") from exc
        if not decoded or len(decoded) > _MAX_BRIDGE_IMAGE_BYTES:
            raise ValueError("Imagem da bridge excede o limite permitido.")
        images.append(
            ImageData(source=ImageSource.UPLOAD, data=data_url, mime_type="image/jpeg")
        )
    return images


def _is_verified_discord_owner(request: BridgeRequest, http_request: Request) -> bool:
    """Trust Discord owner identity only when it came through the local adapter."""
    source = (request.fonte or "").strip().lower()
    host = http_request.client.host if http_request.client else ""
    author_id = str(request.autor_id or "").strip()
    return (
        source == "discord"
        and host.lower() in {"127.0.0.1", "::1", "localhost", "testclient"}
        and bool(author_id)
        and author_id in relationship_store.owner_ids
    )


async def _guardar_entrada(request) -> tuple[str | None, str, str]:
    """Passa o texto pelo guardião. Devolve motivo quando BLOQUEIA, senão None.

    Duas posturas, de propósito:
      - fonte externa declarada  -> bloqueia de verdade
      - fonte não declarada      -> só observa e registra o que bloquearia

    A postura de observação existe pra o Max medir falso positivo com tráfego
    real antes de virar a chave. Não é permissividade: é medir antes de
    endurecer, e está escrito no log a cada ocorrência.
    """
    try:
        fonte = (getattr(request, "fonte", None) or "").strip().lower()
        externa = any(f in fonte for f in FONTES_EXTERNAS)
        evidence = await evaluate_text_async(
            getattr(request, "text", "") or "",
            getattr(request, "autor", None) or "",
            new_context(fonte or "nao_declarada", external=externa),
        )
        if evidence.decision == "accepted":
            return None, "accepted", ""
        motivo = evidence.category
        if externa:
            logger.warning("[Guardião] blocked external ingress from %s", fonte)
            return f"bloqueado_pelo_guardiao:{motivo}", "blocked", motivo
        logger.warning(
            f"[Guardião] 👁️ OBSERVANDO — bloquearia se a fonte fosse declarada "
            f"externa | categoria={motivo}"
        )
        # O texto passa, mas o veredito foi NEGATIVO: quem observa precisa ver
        # "would_block", nunca "accepted". Instrumento que mente esconde o portão.
        return None, "would_block", motivo
    except Exception as exc:  # noqa: BLE001
        # Guardião indisponível não pode virar porta aberta para fonte externa.
        fonte = (getattr(request, "fonte", None) or "").strip().lower()
        if any(f in fonte for f in FONTES_EXTERNAS):
            logger.error(f"[Guardião] indisponível, BLOQUEANDO externa: {exc}")
            return "guardiao_indisponivel", "blocked", "guardiao_indisponivel"
        logger.error(f"[Guardião] indisponível: {exc}")
        return None, "unavailable", "guardiao_indisponivel"


def _guardian_policy_mode(source: str | None) -> str:
    """Expose the actual source policy, independent of the verdict."""
    normalized = (source or "").lower()
    return "enforce" if any(item in normalized for item in FONTES_EXTERNAS) else "observation"


def _emit_guardian_decision(
    decision: str,
    reason: str,
    *,
    source_declared: bool,
    route: str,
    policy_mode: str,
) -> None:
    """Record a content decision without exposing a payload fragment."""
    observability.emit(
        "guardian_checked",
        "guardiao",
        "ok" if decision == "accepted" else decision,
        {
            "reason": decision,
            "guardian_verdict": decision,
            "guardian_category": ":".join((reason or "none").split(":")[:2]),
            "source_declared": source_declared,
            "route": route,
            "policy_mode": policy_mode,
            "final_effect": "deny" if decision == "blocked" else "allow",
        },
    )


def diagnostico_recusa(
    *,
    modelo: str | None,
    legais: list[str],
    bruto: str,
    prompt_chars: int,
    tentativas: int,
) -> dict:
    """Evidencia da recusa 422 em /bridge/game/action.

    Prova cabe em log, despejo nao: carrega amostras limitadas e NUNCA o
    tabuleiro. Fica em nivel de MODULO de proposito — o instrumento do juiz
    (`tests/prova_recusa_jogo.py`) importa esta funcao direto, sem servidor.
    """
    bruto = str(bruto or "")
    if len(bruto) > 300:
        bruto = bruto[:300] + "…"
    return {
        "erro": "acao_estrutural_ausente",
        "modelo": modelo if modelo else "nenhum",
        "n_legais": len(legais or []),
        "legais_amostra": list((legais or [])[:8]),
        "bruto_amostra": bruto,
        "prompt_chars": int(prompt_chars),
        "tentativas": int(tentativas),
    }


def init_bridge_routes(default_context_cache: ServiceContext) -> APIRouter:
    """
    Cria e retorna as rotas da Bridge API.

    Args:
        default_context_cache: ServiceContext compartilhado com o agente carregado.

    Returns:
        APIRouter com os endpoints /bridge/*
    """
    router = APIRouter(prefix="/bridge", tags=["bridge"])

    @router.post("/v2/events", response_model=EventReceiptV2, status_code=202)
    async def submit_v2_event(event: BridgeEventV2, request: Request, response: Response):
        """Admit one normalized bot event using its dedicated per-type token."""
        bot_id = event.bot_id
        token_name = f"CELIA_BRIDGE_V2_TOKEN_{bot_id.upper()}_{event.event_type.upper()}"
        expected_token = os.environ.get(token_name, "")
        authorization = request.headers.get("authorization", "")
        supplied_token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        if not expected_token or not supplied_token or not hmac.compare_digest(supplied_token, expected_token):
            raise HTTPException(status_code=401, detail="bridge_event_authentication_failed")

        scope_by_type = {
            "ambient": "events:ambient",
            "live_chat": "events:live_chat",
            "live_selected": "events:live_selected",
            "game_reaction": "events:game_reaction",
            "game_critical_speech": "events:game_critical_speech",
            "discord_direct": "events:discord_direct",
        }
        scope = scope_by_type.get(event.event_type)
        actor = VerifiedActor(
            actor_id=f"bot:{bot_id}",
            bot_id=bot_id,
            is_owner=False,
            scopes=frozenset({scope}) if scope else frozenset(),
        )
        try:
            priority = classify_priority(event.event_type, actor, declared_bot_id=event.bot_id)
        except AdmissionError as exc:
            response.status_code = 422
            return EventReceiptV2(
                event_id=event.event_id,
                accepted=False,
                status="rejected",
                reason=exc.code.value,
            )

        singleton = CeliaSingleton.get_instance()
        now = time.monotonic()
        deadline = expiry_deadline_mono(now, event.ttl_ms, priority)
        payload = dict(event.payload)
        payload["origin"] = "discord" if event.event_type == "discord_direct" else "chat"
        payload["speaker_name"] = str(payload.get("speaker_name") or bot_id)
        payload["event_type"] = event.event_type
        turn = ScheduledTurn(
            event_id=event.event_id,
            source=bot_id,
            session_id=event.session_id,
            priority=int(priority),
            received_mono=now,
            expires_mono=deadline,
            payload=payload,
            actor=actor,
        )
        handle = await singleton._turn_arbiter.submit(turn)
        if handle.receipt is None:
            return EventReceiptV2(event_id=event.event_id, accepted=True, status="queued")
        response.status_code = 202 if handle.receipt.status == "queued" else 409
        return handle.receipt

    def _create_codex_tts_engine(singleton: CeliaSingleton):
        context = singleton.context
        if context is None or context.character_config is None:
            raise RuntimeError("Contexto nao carregado ainda.")

        tts_config = context.character_config.tts_config
        if tts_config.omnivoice is None:
            raise RuntimeError("A fala externa do Codex exige o bloco omnivoice na config.")
        return TTSFactory.get_tts_engine("omnivoice", **tts_config.omnivoice.model_dump())

    def _wav_duration_sec(audio_path: str) -> float:
        with wave.open(audio_path, "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            if frame_rate <= 0:
                raise ValueError("WAV sem sample rate valido.")
            return wav_file.getnframes() / float(frame_rate)

    def _porta_do_servidor() -> int:
        try:
            from .config_manager.utils import read_yaml, validate_config

            return int(validate_config(read_yaml("conf.yaml")).system_config.port)
        except Exception:
            return BridgeHealthResponse.model_fields["port"].default

    @router.get("/health", response_model=BridgeHealthResponse)
    async def bridge_health():
        """Health check - retorna status do servidor de tools."""
        agent_loaded = default_context_cache.agent_engine is not None
        mcp_loaded = (
            default_context_cache.tool_manager is not None
            and default_context_cache.tool_executor is not None
        )

        return BridgeHealthResponse(
            status="ok" if agent_loaded else "initializing",
            agent_loaded=agent_loaded,
            mcp_loaded=mcp_loaded,
            service="tool-bridge",
            # 2026-08-19: era 12394 fixo, porta em que este servidor nunca
            # escutou. A porta real vem do conf.yaml (system_config.port).
            port=_porta_do_servidor(),
        )

    # ── Motor dedicado da rota estrutural de jogo ────────────────────────────
    # Ordem do Pai (2026-08-24): Cloudflare SO na rota estrutural. O modelo de
    # CONVERSA da Celia NAO muda — a identidade dela continua no Gemini.
    #
    # Medido no mesmo gateway, mesmo dia:
    #   gemini/gemini-3.1-flash-lite  n=15  p95 17.636 ms  max 29.388 ms
    #   cf/@cf/meta/llama-3.1-70b     n=8   297-762 ms, sem nenhum estouro
    # O diagnostico do Codex mostrou que ~94% do p95 e do upstream Google, e que
    # o fallback do conf.yaml so dispara em ERRO antes do primeiro token — nunca
    # por lentidao. Esta rota nao carrega personalidade nenhuma: ela devolve
    # {"action": "..."} validado contra legal_actions. Trocar o modelo aqui nao
    # toca na voz dela.
    # Cadeia de modelos, nao um so. Medido em 2026-08-25: sob volume de teste, o
    # Cloudflare devolveu `413 ... (reset after 1m 28s)` em 8 ms e o qwen `429`.
    # Com um unico modelo, a rota virava 422 em 58 de 58 chamadas e TODO lance
    # caia no caminho conversacional, que satura o singleton. Um provedor
    # estrangulado nao pode parar o comando de jogo — ele passa para o proximo.
    # A acao continua validada contra `legal_actions`: failover nao e chute.
    # Cada elo pode trazer o proprio endereco: "modelo|base_url".
    # O ultimo e LOCAL de proposito. Medido em 2026-08-25: com a cota do
    # Cloudflare esgotada (413 persistente), sobrava so o Gemini, que carrega a
    # cauda do upstream. O llama-server local com qwen2.5-3b em :8085 devolveu
    # 6 de 6 acoes validas com p50 de 569 ms — sem cota, sem cauda, sem rede.
    # Comando de jogo nao precisa de modelo grande: precisa escolher UMA acao de
    # uma lista curta, e a escolha continua validada contra `legal_actions`.
    _PADRAO_GATEWAY = os.environ.get(
        "CELIA_GAME_ACTION_BASE_URL", "http://127.0.0.1:20127/v1"
    )
    _MODELOS_ACAO = [
        m.strip()
        for m in os.environ.get(
            "CELIA_GAME_ACTION_MODELS",
            "cf/@cf/meta/llama-3.1-70b-instruct-fp8-fast,"
            "gemini/gemini-3.1-flash-lite-preview,"
            "local|http://127.0.0.1:8085/v1",
        ).split(",")
        if m.strip()
    ]
    _motor_acao_cache: dict = {}

    def _motor_de_acao(elo: str):
        """Cliente sem estado, um por elo, so para o comando de jogo."""
        if elo not in _motor_acao_cache:
            from .agent.stateless_llm_factory import LLMFactory

            modelo, _, endereco = elo.partition("|")
            _motor_acao_cache[elo] = LLMFactory.create_llm(
                "openai_compatible_llm",
                model=modelo,
                base_url=endereco or _PADRAO_GATEWAY,
                llm_api_key="local-gateway",
                organization_id=None,
                project_id=None,
                temperature=0.0,   # comando nao improvisa
                max_tokens=48,     # {"action":"..."} nao precisa de mais
            )
            logger.info(f"Rota estrutural de jogo: motor pronto para {elo}")
        return _motor_acao_cache[elo]

    class GameActionRequest(BaseModel):
        """Comando estrutural de jogo. NAO e conversa: nao passa por memoria,
        persona nem juiz de saida."""

        game: str
        state: dict
        event: str | None = None

    @router.post("/game/action")
    async def game_action(request: Request, body: GameActionRequest):
        """Devolve SOMENTE a acao do motor, como estrutura confiavel.

        Medido em 2026-08-24: pelo caminho conversacional da Celia, 0 de 40
        respostas vieram em JSON — todas em fala livre, e a execucao dependia do
        `extractAction()` pescar a acao do texto. O MESMO modelo, com o MESMO
        prompt e SEM o pipeline de identidade, devolveu JSON valido.

        A separacao e de responsabilidade, nao de personalidade: a ACAO sai por
        aqui, deterministicamente validada contra `legal_actions`; o COMENTARIO
        continua saindo pela Celia inteira, por /bridge/process. Ela nao perde
        identidade nenhuma — ela deixa de ter que ser um parser.
        """
        _require_loopback(request)
        legais = [str(a) for a in (body.state or {}).get("legal_actions") or []]
        if not legais:
            raise HTTPException(status_code=422, detail="state.legal_actions vazio")

        contexto = default_context_cache
        if contexto is None or contexto.agent_engine is None:
            raise HTTPException(status_code=503, detail="contexto nao carregado")

        # O prompt leva uma PROJECAO do estado, nao o estado cru.
        #
        # Medido em 2026-08-24: o estado da dama serializa em 3.636 chars contra
        # ~200 do jogo da velha, com os MESMOS 7 lances legais — a diferenca e o
        # tabuleiro em objetos aninhados. Com o estado cru no prompt, um lance de
        # dama levou 58.814 ms pela rota. O teste de partida completa espera 60 s
        # por lance, entao a partida nunca terminava.
        #
        # A lista de acoes legais vai SEMPRE inteira: e ela que decide a jogada.
        # O tabuleiro entra compactado, e some se ainda assim passar do teto.
        def _compacto(valor, limite: int = 900) -> str:
            texto = json.dumps(valor, ensure_ascii=False, separators=(",", ":"))
            return texto if len(texto) <= limite else texto[:limite] + "…"

        essencial = {
            chave: body.state[chave]
            for chave in ("turn", "current_player", "board")
            if chave in body.state
        }
        prompt = (
            f"Jogo: {body.game}. Evento: {body.event or 'sem evento'}.\n"
            f"Estado: {_compacto(essencial)}\n"
            f"Escolha UMA acao da lista legal: {json.dumps(legais, ensure_ascii=False)}.\n"
            'Responda SOMENTE com JSON: {"action":"<uma da lista>"}. '
            "Sem texto antes ou depois."
        )

        # o motor e escolhido dentro do laco, com failover

        async def _pedir(texto: str, modelo: str) -> str:
            motor = _motor_de_acao(modelo)
            partes: list[str] = []
            async for pedaco in motor.chat_completion(
                [{"role": "user", "content": texto}], ""
            ):
                if isinstance(pedaco, str):
                    partes.append(pedaco)
            return "".join(partes)

        inicio = time.time()
        bruto = ""
        acao = None
        # Duas tentativas: a segunda aperta o contrato. Nao ha terceira — se o
        # modelo nao entregar estrutura, quem chamou recebe erro EXPLICITO e
        # decide, em vez de receber uma acao adivinhada.
        modelo_usado = None
        # Uma passada por modelo; dentro de cada modelo, duas tentativas — a
        # segunda aperta o contrato. Provedor que falha nao interrompe a cadeia.
        tentativas = [(m, t) for m in _MODELOS_ACAO for t in range(2)]
        for modelo, tentativa in tentativas:
            try:
                bruto = await _pedir(
                    prompt if tentativa == 0
                    else prompt + '\nA resposta anterior nao era JSON. Responda exatamente {"action":"..."}.',
                    modelo,
                )
                modelo_usado = modelo
            except Exception as exc:  # noqa: BLE001 - tenta o proximo provedor
                logger.warning(
                    f"game/action: {modelo} indisponivel ({type(exc).__name__}); "
                    "seguindo para o proximo da cadeia"
                )
                continue
            candidato = None
            achado = re.search(r"\{.*?\}", bruto, re.S)
            if achado:
                try:
                    candidato = json.loads(achado.group(0)).get("action")
                except (json.JSONDecodeError, AttributeError):
                    candidato = None
            if candidato is not None and str(candidato) in legais:
                acao = str(candidato)
                break

        elapsed = int((time.time() - inicio) * 1000)
        if acao is None:
            # Erro explicito: a integracao NAO deve executar acao adivinhada.
            # Recusar continua sendo o certo; o que muda aqui e que a recusa
            # passa a CARREGAR PROVA (CORRECAO-CELIA.md C12: ninguem investigou
            # a causa porque a recusa nao guardava dado nenhum).
            evidencia = diagnostico_recusa(
                modelo=modelo_usado,
                legais=legais,
                bruto=bruto,
                prompt_chars=len(prompt),
                tentativas=len(tentativas),
            )
            observability.emit(
                "game_action_invalid", "bridge.game", "rejected", metadata=evidencia
            )
            # loguru usa {}, nao %s — placeholders %s ja sairam literais no log
            # desta arvore.
            logger.warning("game/action: recusa 422 com evidencia {}", evidencia)
            raise HTTPException(status_code=422, detail=evidencia)
        observability.emit("game_action", "bridge.game", "accepted")
        return {"action": acao, "elapsed_ms": elapsed, "estrutural": True,
                "modelo": modelo_usado}

    @router.post("/desktop-pet/open")
    async def open_desktop_pet(request: Request):
        """Request the one existing Electron Pet from the local browser UI.

        This is deliberately a named, loopback-only action: the client cannot
        supply a command, argument, path, or environment value to the process.
        """
        _require_loopback(request)
        windows = sys.platform == "win32"
        launcher = (RAIZ / "tools" / ("run_pet.ps1" if windows else "run_pet.sh")).resolve()
        try:
            launcher.relative_to(RAIZ.resolve())
        except ValueError as exc:  # Defensive invariant for a repository path.
            raise HTTPException(status_code=500, detail="desktop_pet_launcher_invalid") from exc
        if not launcher.is_file() or (not windows and not os.access(launcher, os.X_OK)):
            raise HTTPException(status_code=503, detail="desktop_pet_launcher_unavailable")
        command = (
            ("powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(launcher))
            if windows else (str(launcher),)
        )
        try:
            await asyncio.create_subprocess_exec(
                *command,
                cwd=str(RAIZ),
                start_new_session=True,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            logger.warning("desktop Pet launch request failed: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="desktop_pet_launcher_unavailable") from exc
        observability.emit("desktop_pet_launch_requested", "desktop_pet", "accepted")
        return {"ok": True, "action": "desktop_pet_launch_requested"}

    @router.get("/social/scores")
    async def social_scores_list(request: Request):
        """Debug (loopback): afinidade/tensao/estado de cada pessoa."""
        _require_loopback(request)
        return {"enabled": social_score.enabled(), "people": social_score.social_scores.snapshot()}

    @router.post("/social/scores/{pessoa}/reset")
    async def social_scores_reset(pessoa: str, request: Request):
        """Debug (loopback): volta uma pessoa (chave ou nome) ao estado inicial."""
        _require_loopback(request)
        result = social_score.social_scores.reset(pessoa)
        if result is None:
            raise HTTPException(status_code=404, detail="pessoa_desconhecida")
        return {"ok": True, "person": result}

    @router.get("/desktop-pet/state")
    async def desktop_pet_state(request: Request):
        """Somente leitura: o Pet esta LIGADO (`enabled` em var/desktop_pet.json)?

        `enabled` e o proprio estado do launcher: true ao abrir, false quando o
        usuario fecha o Pet (x nativo / Ctrl+Esc). E a intencao de restauracao,
        nao uma prova de que a janela existe neste instante. `null` = sem leitura.
        """
        _require_loopback(request)
        enabled = None
        try:
            dados = json.loads((VAR / "desktop_pet.json").read_text(encoding="utf-8"))
            if isinstance(dados, dict) and isinstance(dados.get("enabled"), bool):
                enabled = dados["enabled"]
        except (OSError, ValueError):
            enabled = None
        # `alive` e a prova (processo do Electron existe agora); `enabled` e so a intencao de restauracao.
        return {"enabled": enabled, "alive": _pet_vivo()}

    @router.get("/mode")
    async def get_mode(request: Request):
        """Ultimo modo pedido pela interface ('pet' | 'janela') e quando (ms). O Pet le daqui para se fechar."""
        _require_loopback(request)
        modo, em = None, None
        try:
            dados = json.loads(_modo_arquivo().read_text(encoding="utf-8"))
            if isinstance(dados, dict) and dados.get("modo") in _MODOS_VALIDOS:
                modo = dados["modo"]
                em = dados.get("em") if isinstance(dados.get("em"), (int, float)) else None
        except (OSError, ValueError):
            modo, em = None, None
        return {"modo": modo, "em": em}

    @router.post("/mode")
    async def set_mode(request: Request, escolha: ModeChoice):
        """Grava o modo pedido ('pet' ou 'janela') com carimbo de tempo; so loopback."""
        _require_loopback(request)
        if escolha.modo not in _MODOS_VALIDOS:
            raise HTTPException(status_code=422, detail="modo_invalido")
        alvo = _modo_arquivo()
        alvo.parent.mkdir(parents=True, exist_ok=True)
        em = int(time.time() * 1000)
        tmp = alvo.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"modo": escolha.modo, "em": em}), encoding="utf-8")
        os.replace(tmp, alvo)
        observability.emit("surface_mode", "interface", escolha.modo)
        return {"modo": escolha.modo, "em": em}

    _ler_opcoes = ler_opcoes_corpo

    @router.get("/corpo-opcoes")
    async def get_corpo_opcoes(request: Request):
        """Opcoes do corpo (oculos/slime/HUD Minecraft) + boca do slime. O Pet Electron nao divide o localStorage da interface: le daqui."""
        _require_loopback(request)
        fresca = (time.time() - _BOCA_SLIME["em"]) < _BOCA_VALIDADE_S
        return {**_ler_opcoes(), "boca": _BOCA_SLIME["v"] if fresca else 0.0}

    @router.post("/corpo-opcoes")
    async def set_corpo_opcoes(request: Request, op: CorpoOpcoes):
        """Grava oculos/slime (disco, rename atomico) e/ou a boca do slime (so memoria). Somente loopback."""
        _require_loopback(request)
        mudou = {k: v for k, v in (("oculos", op.oculos), ("slime", op.slime), ("hud_minecraft", op.hud_minecraft), ("modo_minecraft", op.modo_minecraft), ("enquete_mobile", op.enquete_mobile), ("chat_tiktok", op.chat_tiktok)) if v is not None}
        if mudou:
            gravar_opcoes_corpo(mudou)
        if op.boca is not None:
            _BOCA_SLIME["v"] = op.boca
            _BOCA_SLIME["em"] = time.time()
        fresca = (time.time() - _BOCA_SLIME["em"]) < _BOCA_VALIDADE_S
        return {**_ler_opcoes(), "boca": _BOCA_SLIME["v"] if fresca else 0.0}

    # ---- Discord: qual bot (primary/secondary) a Célia conecta; um por vez ----
    def _discord_choice_module():
        import importlib.util
        import sys

        nome = "celia_discord_bot_choice"
        if nome in sys.modules:
            return sys.modules[nome]
        caminho = Path(RAIZ) / "integrations" / "discord" / "bot_choice.py"
        spec = importlib.util.spec_from_file_location(nome, caminho)
        modulo = importlib.util.module_from_spec(spec)
        sys.modules[nome] = modulo
        spec.loader.exec_module(modulo)
        return modulo

    class DiscordBotChoice(BaseModel):
        model_config = ConfigDict(extra="forbid")
        bot: str = Field(..., max_length=16)

    @router.get("/discord/bot")
    async def get_discord_bot(request: Request):
        """Escolha do bot do Discord, se a variavel do escolhido esta preenchida (bool) e se esta conectado. Somente loopback; nunca devolve token."""
        _require_loopback(request)
        bc = _discord_choice_module()
        return bc.describe(var_dir=Path(VAR), env_file=_discord_env_file())

    @router.post("/discord/bot")
    async def set_discord_bot(request: Request, escolha: DiscordBotChoice):
        """Grava a escolha ('primary' ou 'secondary', disco, rename atomico). Vale na proxima verificacao do bot (~4 s). Somente loopback."""
        _require_loopback(request)
        bc = _discord_choice_module()
        if bc.normalize_bot(escolha.bot) is None:
            raise HTTPException(status_code=422, detail="bot_invalido")
        origem = request.headers.get("x-celia-source", "http")
        bc.write_choice(escolha.bot, origem if origem in ("ui", "cli", "agent") else "http", var_dir=Path(VAR))
        return bc.describe(var_dir=Path(VAR), env_file=_discord_env_file())

    class DiscordCallSettings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        threshold: int | None = Field(default=None, ge=1, le=8)
        silence_seconds: int | None = Field(default=None, ge=0, le=60)
        chat_replies_during_call: bool | None = None
        vad_engine: str | None = Field(default=None, pattern="^(webrtcvad|auditok|numpy_rms)$")
        vad_webrtc_mode: int | None = Field(default=None, ge=0, le=3)
        vad_auditok_energy: float | None = Field(default=None, ge=0, le=120)
        vad_numpy_rms: float | None = Field(default=None, ge=0, le=1)
        vad_numpy_zcr_max: float | None = Field(default=None, ge=0, le=1)
        vad_min_segment_ms: int | None = Field(default=None, ge=400, le=5000)
        vad_min_voiced_ms: int | None = Field(default=None, ge=0, le=2000)
        vad_min_rms: float | None = Field(default=None, ge=0, le=1)
        hold: bool | None = None

    def _discord_call_view(bc):
        settings = bc.read_call_settings(var_dir=Path(VAR))
        return {
            "threshold": settings["threshold"],
            "silence_seconds": settings["silence_seconds"],
            "chat_replies_during_call": settings["chat_replies_during_call"],
            **{key: settings[key] for key in (
                "vad_engine", "vad_webrtc_mode", "vad_auditok_energy",
                "vad_numpy_rms", "vad_numpy_zcr_max", "vad_min_segment_ms",
                "vad_min_voiced_ms", "vad_min_rms",
            )},
            "holding": settings["hold_until"] > time.time(),
            "calls": bc.read_call_status(var_dir=Path(VAR)),
        }

    @router.get("/discord/call")
    async def get_discord_call(request: Request):
        _require_loopback(request)
        return _discord_call_view(_discord_choice_module())

    @router.post("/discord/call")
    async def set_discord_call(request: Request, settings: DiscordCallSettings):
        _require_loopback(request)
        bc = _discord_choice_module()
        changes = settings.model_dump(exclude_none=True, exclude={"hold"})
        if settings.hold is not None:
            changes["hold_until"] = time.time() + 30 if settings.hold else 0
        bc.update_call_settings(changes, var_dir=Path(VAR))
        return _discord_call_view(bc)

    class EnquetePresente(BaseModel):
        model_config = ConfigDict(extra="forbid")
        lado: str = Field(..., pattern="^(lula|flavio)$")
        quantidade: int = Field(..., ge=1, le=100000)
        evento_id: str = Field(..., min_length=1, max_length=160)
        presente: str = Field(default="", max_length=80)
        autor: str = Field(default="", max_length=80)

    @router.get("/enquete")
    async def get_enquete(request: Request):
        """Placar da enquete da live + se a exibicao no modo mobile esta ligada (opcao enquete_mobile do Corpo). Somente loopback."""
        _require_loopback(request)
        from .core import enquete

        return {"ativa": _ler_opcoes()["enquete_mobile"], **enquete.ler(VAR)}

    @router.post("/enquete/presente")
    async def post_enquete_presente(request: Request, body: EnquetePresente):
        """Leitor do TikTok -> placar: um presente ja fechado (fim do combo). Idempotente por evento_id. Somente loopback."""
        _require_loopback(request)
        from .core import enquete

        placar = enquete.somar(VAR, body.lado, body.quantidade, body.evento_id)
        if not placar.get("duplicado"):
            logger.info(f"[Enquete] +{body.quantidade} {body.lado} ({body.presente or '?'} de {body.autor or '?'}) -> lula={placar['lula']} flavio={placar['flavio']}")
        return {"ativa": _ler_opcoes()["enquete_mobile"], **placar}

    @router.post("/enquete/zerar")
    async def post_enquete_zerar(request: Request):
        """Rodada nova: placar 0 x 0. Somente loopback."""
        _require_loopback(request)
        from .core import enquete

        placar = enquete.zerar(VAR)
        logger.info(f"[Enquete] zerada: rodada {placar['rodada']}")
        return {"ativa": _ler_opcoes()["enquete_mobile"], **placar}

    @router.get("/body")
    async def get_body(request: Request):
        """Corpo (2d|3d) escolhido na interface.

        O Pet Electron nao compartilha localStorage com o navegador; ele le a
        escolha daqui. `corpo` e null enquanto ninguem escolheu.
        """
        _require_loopback(request)
        corpo = None
        try:
            dados = json.loads(_corpo_arquivo().read_text(encoding="utf-8"))
            if isinstance(dados, dict) and dados.get("corpo") in _CORPOS_VALIDOS:
                corpo = dados["corpo"]
        except (OSError, ValueError):
            corpo = None
        return {"corpo": corpo}

    @router.post("/body")
    async def set_body(request: Request, escolha: BodyChoice):
        """Grava o corpo escolhido (somente loopback, somente '2d' ou '3d')."""
        _require_loopback(request)
        if escolha.corpo not in _CORPOS_VALIDOS:
            raise HTTPException(status_code=422, detail="corpo_invalido")
        alvo = _corpo_arquivo()
        alvo.parent.mkdir(parents=True, exist_ok=True)
        tmp = alvo.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"corpo": escolha.corpo}), encoding="utf-8")
        os.replace(tmp, alvo)
        observability.emit("body_choice", "interface", escolha.corpo)
        return {"corpo": escolha.corpo}

    @router.get("/behavior/profile")
    async def behavior_profile(request: Request):
        """Read the selected behavior profile without exposing conversation text."""
        _require_loopback(request)
        return continuous_behavior.safe_snapshot()

    @router.get("/behavior/profiles/{profile}")
    async def get_behavior_profile_settings(request: Request, profile: str):
        """V6: typed saved settings; never carries credentials or tool grants."""
        _require_loopback(request)
        if canonical_profile(profile) not in PROFILE_NAMES:
            raise HTTPException(status_code=404, detail="unknown_profile")
        return continuous_behavior.profile_settings(profile)

    @router.put("/behavior/profiles/{profile}")
    async def put_behavior_profile_settings(
        request: Request, profile: str, body: BehaviorProfileSettingsRequest
    ):
        _require_loopback(request)
        if canonical_profile(profile) not in PROFILE_NAMES:
            raise HTTPException(status_code=404, detail="unknown_profile")
        try:
            result = continuous_behavior.update_profile_settings(
                profile, body.settings, expected_revision=body.expected_revision
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            await CeliaSingleton.get_instance().broadcast({
                "type": "profile.settings.changed",
                "profile": profile, "revision": result["revision"],
            })
        except Exception:
            pass
        return result

    @router.post("/behavior/profiles/{profile}/reset")
    async def reset_behavior_profile_settings(request: Request, profile: str):
        _require_loopback(request)
        if canonical_profile(profile) not in PROFILE_NAMES:
            raise HTTPException(status_code=404, detail="unknown_profile")
        return continuous_behavior.reset_profile_settings(profile)

    @router.get("/debug/state")
    async def debug_state(request: Request):
        """Canonical loopback wiring snapshot for humans and coding agents."""
        _require_loopback(request)
        singleton = CeliaSingleton.get_instance()
        try:
            from .core.cognitive_loop import cognitive_loop
            cognitive = cognitive_loop.debug_snapshot()
        except Exception as exc:
            cognitive = {"running": False, "error": type(exc).__name__}
        try:
            from .core.screen_vision import screen_vision
            vision = screen_vision.debug_snapshot()
        except Exception as exc:
            vision = {"error": type(exc).__name__}
        tts = getattr(getattr(singleton, "context", None), "tts_engine", None)
        tts_view = {
            "engine": type(tts).__name__ if tts is not None else None,
            "dtype": os.getenv("CELIA_OMNIVOICE_DTYPE", "float16"),
            "steps": int(os.getenv("CELIA_OMNIVOICE_STEPS", "28")),
            "reliable_steps": 32,
        }
        behavior = continuous_behavior.safe_snapshot()
        agent = getattr(getattr(singleton, "context", None), "agent_engine", None)
        resources = {
            "brain": "on" if cognitive.get("running") else "off",
            "script": ("loaded" if behavior["script"].get("loaded") else "on")
                      if behavior["policy"]["script_tracking"] else "off",
            "tools": "on" if (getattr(agent, "_use_mcpp", False)
                                  and getattr(agent, "_tool_executor", None)
                                  and getattr(agent, "_tool_manager", None)) else "off",
            "bank": "active" if getattr(singleton, "bank_mode", "shadow") == "ativo" else "shadow",
        }
        queue_view = getattr(singleton, "conversation_queue_snapshot", None)
        if not isinstance(queue_view, dict):
            queue_view = {
                "pending": getattr(singleton, "pending_batches", 0),
                "expired": 0,
                "superseded": 0,
            }
        return {
            "flow": "🎤→👂→🧠→💭→⚖️→📥→🎙️→🔊",
            "vision_flow": "🖥️→🖼️→👁️→🧠→🎟️→⚖️",
            "processing": singleton.is_processing,
            "queue": queue_view,
            "behavior": behavior,
            "cognitive": cognitive,
            "vision": vision,
            "tts": tts_view,
            "profiles": sorted(PROFILE_NAMES),
            "resources": resources,
        }

    @router.post("/behavior/session/reset")
    async def reset_behavior_session(request: Request, body: BehaviorSessionResetRequest):
        """Start a clean recording/live session without touching durable memory or grants."""
        _require_loopback(request)
        before = continuous_behavior.safe_snapshot()
        active_before = before.get("active_speech") or {}
        if (
            active_before.get("playback_state") == "playing"
            and active_before.get("speech_id") not in {None, "", "NOT AVAILABLE"}
        ):
            try:
                await CeliaSingleton.get_instance().broadcast({
                    "type": "playback.cancel",
                    "speech_id": active_before.get("speech_id"),
                    "reason": "session_reset",
                })
            except Exception as exc:
                logger.debug("session reset playback cancellation unavailable: %s", type(exc).__name__)
        snapshot = continuous_behavior.reset_session(clear_script=body.clear_script)
        try:
            from .core.cognitive_loop import cognitive_loop
            cognitive_loop.reset_session_context()
        except Exception as exc:
            logger.debug("cognitive session reset unavailable: %s", type(exc).__name__)
        try:
            from .core.screen_vision import screen_vision
            screen_vision.reset_session()
        except Exception as exc:
            logger.debug("vision session reset unavailable: %s", type(exc).__name__)
        try:
            _vision_frame_slot.clear()
        except Exception:
            pass
        try:
            await CeliaSingleton.get_instance().broadcast({
                "type": "behavior.session_reset",
                "clear_script": body.clear_script,
                "profile": snapshot.get("profile"),
            })
        except Exception as exc:
            logger.debug("session reset broadcast unavailable: %s", type(exc).__name__)
        return snapshot

    @router.post("/direct-address")
    async def direct_address_gesture(request: Request):
        """Meta+N (KDE global shortcut): "Célia, olha isso" — an explicit visual-attention trigger.

        Owner's decision (2026-10-04). Key: Meta+N, a KDE shortcut registered by
        bin/instalar-atalho-meta-n.sh, which runs bin/celia-olha-isso. Not Ctrl+S
        (Pet pose) nor Ctrl+N (new window in Chrome/Blender).

        It does NOT depend on a pending line. It attaches the cached visual
        observation with the age of the ANALYSIS, never starts a new analysis
        (a stale cache becomes "ask": Célia decides whether to request a new look)
        and lets Célia judge whether it relates to the conversation.
        Loopback only; never grants tools.
        """
        _require_loopback(request)
        from .core import direct_address

        source = "unknown"
        try:
            payload = await request.json()
            if isinstance(payload, dict):
                source = str(payload.get("source") or "unknown")[:40]
        except Exception:
            pass  # the body is optional; the gesture never needs it
        selection = direct_address.select_for_gesture()
        text = direct_address.build_look_request(selection)
        continuous_behavior.invalidate_inflight_reply("owner_turn_submitted")
        submitted = await CeliaSingleton.get_instance().submit_input(
            text,
            role="source",
            speaker_name=None,
            behavior_observed=True,
        )
        logger.info(
            f"[Gesto Meta+N] olha-isso source={source} visao={selection.action} "
            f"idade_analise_ms={selection.age_ms} submitted={submitted}"
        )
        return {
            "ok": bool(submitted),
            "gesture": "look",
            "vision": selection.action,
            "analysis_age_ms": selection.age_ms,
        }

    @router.post("/behavior/profile")
    async def set_behavior_profile(request: Request, body: BehaviorProfileRequest):
        """Persist profile selection only; this never grants tools or capabilities."""
        _require_loopback(request)
        if canonical_profile(body.profile) not in PROFILE_NAMES:
            raise HTTPException(status_code=422, detail="invalid_profile")
        if canonical_profile(body.profile) == "roteiro" and continuous_behavior.profile != "roteiro":
            from .core.roteiro_live import roteiro_live
            try:
                roteiro_live.prepare_operational_script(continuous_behavior)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        snapshot = continuous_behavior.select_profile(body.profile)
        change = snapshot.get("profile_change")
        if change:
            # Reuse the singleton transport: profile selection has no second bus.
            try:
                await CeliaSingleton.get_instance().broadcast({
                    "type": "profile.changed",
                    "profile": snapshot["profile"],
                    "change": change,
                })
            except Exception as exc:  # A profile switch must not depend on a UI client.
                logger.debug("profile change broadcast unavailable: %s", type(exc).__name__)
        active_speech = snapshot.get("active_speech") or {}
        if active_speech.get("playback_state") == "cancel_requested":
            try:
                await CeliaSingleton.get_instance().broadcast({
                    "type": "playback.cancel",
                    "speech_id": active_speech.get("speech_id"),
                    "reason": "profile_changed",
                })
            except Exception as exc:
                logger.debug("profile playback cancellation unavailable: %s", type(exc).__name__)
        return snapshot

    @router.get("/behavior/script")
    async def behavior_script(request: Request):
        """Expose only safe script progress, never raw recording notes."""
        _require_loopback(request)
        return continuous_behavior.safe_snapshot()["script"]

    @router.post("/behavior/script")
    async def set_behavior_script(request: Request, body: BehaviorScriptRequest):
        """Load a bounded local script into the existing behavior runtime."""
        _require_loopback(request)
        try:
            script = continuous_behavior.load_script(body.script)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="invalid_script") from exc
        if continuous_behavior.profile in {"roteiro", "gravacao"}:
            from .core.roteiro_live import roteiro_live
            try:
                live_script = roteiro_live.prepare_operational_script(
                    continuous_behavior,
                    continuous_behavior.script.export(),
                )
                await roteiro_live.activate(
                    continuous_behavior.profile,
                    new_session=True,
                    script=live_script,
                )
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        await CeliaSingleton.get_instance().broadcast({
            "type": "script.position_changed",
            "script": script,
        })
        return script

    @router.post("/process", response_model=BridgeResponse)
    @observed_async_turn("bridge.process")
    async def bridge_process(request: BridgeRequest, http_request: Request):
        """
        Processa o texto do usuário usando o agente com ferramentas MCP.

        Fluxo:
        1. Recebe texto do servidor principal
        2. Passa pelo agente (que tem acesso às tools MCP)
        3. Coleta a resposta completa
        4. Retorna resposta + info das tools usadas
        """
        start_time = time.time()
        observability.emit(
            "input_received",
            "input.bridge",
            "running",
            {
                "source": "bridge",
                "input_length": len(request.text or ""),
                "image_count": len(request.images or []),
                "audio_requested": bool(request.synthesize_audio),
            },
        )

        logger.info(f"🔗 Bridge /process recebeu: '{request.text[:100]}...'")

        # Modo Live ativo: so as fontes da live (allow_sources) conversam; as demais ficam de fora ate desligar o modo.
        if not live_gate.process_source_allowed(request.fonte):
            return BridgeResponse(
                response="",
                tool_used=False,
                tools=[],
                error="live_mode_active_source_not_allowed",
                elapsed_ms=int((time.time() - start_time) * 1000),
            )

        # ── O portão (Fatia 8) ──────────────────────────────────────────
        # O Discord adapter fornece um ID criptograficamente ligado à sessão.
        # Quando esse ID é o owner configurado e a chamada veio do loopback,
        # não trate o próprio dono como tráfego público hostil. Demais usuários
        # continuam passando pelo Guardião normalmente.
        discord_owner_verified = _is_verified_discord_owner(request, http_request)
        if discord_owner_verified:
            bloqueio, guardiao_estado, guardiao_motivo = None, "accepted", "trusted_discord_owner"
            guardiao_policy = "trusted_owner_bypass"
        else:
            bloqueio, guardiao_estado, guardiao_motivo = await _guardar_entrada(request)
            guardiao_policy = _guardian_policy_mode(getattr(request, "fonte", None))
        _emit_guardian_decision(
            guardiao_estado,
            guardiao_motivo,
            source_declared=bool((getattr(request, "fonte", None) or "").strip()),
            route="bridge.process",
            policy_mode=guardiao_policy,
        )
        if bloqueio is not None:
            return BridgeResponse(
                response="",
                tool_used=False,
                tools=[],
                error=bloqueio,
                elapsed_ms=int((time.time() - start_time) * 1000),
            )

        try:
            images = _bridge_images(request.images)
        except ValueError as exc:
            observability.record_error("input.bridge", exc)
            return BridgeResponse(
                response="",
                tool_used=False,
                tools=[],
                error=str(exc),
                elapsed_ms=int((time.time() - start_time) * 1000),
            )

        # Verifica se o agente está carregado
        if default_context_cache.agent_engine is None:
            observability.emit("agent_unavailable", "agent.basic_memory", "unavailable")
            return BridgeResponse(
                response="",
                tool_used=False,
                tools=[],
                error="Agente não carregado. Aguarde a inicialização completa.",
            )

        try:
            # Monta o input para o agente
            turn_id = continuous_behavior.begin_turn()
            emotion_prompt, emotion = _bridge_emotion_context(request.text)
            agent_text = (
                f"{emotion_prompt}\n\nInput: {request.text}"
                if emotion_prompt
                else request.text
            )
            agent_metadata = {**_bridge_agent_metadata(request), "turn_id": turn_id}
            if (request.fonte or "").strip().lower() == "discord":
                relation = relationship_store.update(
                    request.autor_id or "", request.autor or "Visitante", request.text
                )
                discord_owner_verified = discord_owner_verified and bool(relation.get("owner"))
                if not social_score.enabled():  # com o score social ligado ele substitui o prompt de raiva antigo
                    agent_metadata["discord_social_context"] = relationship_store.build_prompt(relation)
                agent_metadata["discord_anger_score"] = relation.get("anger_score", 0.0)
                agent_metadata["discord_social_style"] = relation.get("style", "normal")
                agent_metadata["discord_owner_verified"] = discord_owner_verified
            input_data = BatchInput(
                texts=[
                    TextData(
                        source=TextSource.INPUT,
                        content=agent_text,
                        from_name=(request.autor or "Visitante"),
                    )
                ],
                images=images,
                files=[],
                metadata=agent_metadata,
            )
            # Explicit local-owner bridge calls may use the same bounded write
            # capabilities as the browser/microphone owner path. Never trust the
            # body alone: the TCP peer must also be loopback.
            _host = http_request.client.host if http_request.client else ""
            _origin = (request.fonte or "").strip().lower()
            owner_scopes = (
                "owner.full", "image.generate", "discord.write", "memory.write",
                "overlay.write", "poll.write", "minecraft.write", "vision.write",
            )
            if _host.lower() in {"127.0.0.1", "::1", "localhost", "testclient"} and _origin in {"owner", "local_owner", "microphone"}:
                _owner_ctx = new_context(
                    "local_owner_bridge", external=False,
                    trusted_user_id=(request.autor or "owner"), authenticated=True,
                    authorization_scope=owner_scopes,
                )
                from .core.tool_authorization import bind_direct_browser_command, bind_direct_folder_command
                _owner_ctx = bind_direct_browser_command(_owner_ctx, request.text)
                input_data.metadata["trusted_context"] = bind_direct_folder_command(_owner_ctx, request.text)
            elif _origin == "discord" and discord_owner_verified:
                # Discord is the one external surface where Criador can be
                # cryptographically identified by the configured account ID.
                # The message still crossed the Guardian above; only tool
                # identity/capabilities are upgraded here.
                input_data.metadata["trusted_context"] = new_context(
                    "discord_owner", external=True,
                    trusted_user_id=(request.autor_id or ""), authenticated=True,
                    authorization_scope=owner_scopes,
                )
            elif any(item in _origin for item in FONTES_EXTERNAS):
                input_data.metadata["trusted_context"] = new_context(
                    f"{_origin or 'external'}_public", external=True,
                    trusted_user_id=(request.autor_id or request.autor or None),
                    authenticated=bool(request.autor_id),
                    authorization_scope=("image.generate", "poll.write"),
                )

            singleton = CeliaSingleton.get_instance()
            context = singleton.context
            if context and context.history_uid:
                observability.emit(
                    "history_write",
                    "memory.history",
                    "running",
                    {"history_role": "human"},
                )
                store_message(
                    context.character_config.conf_uid,
                    context.history_uid,
                    "human",
                    request.text,
                    name=(request.autor or "Visitante"),
                )
                observability.emit(
                    "history_write", "memory.history", "ok", {"history_role": "human"}
                )

            await read_live_comment(request, bridge_speak, SpeakRequest)

            # Only the agent-generation phase goes through the common arbiter.
            # The callback and its BatchInput remain in bounded in-memory sidecar
            # state; the queue/trace sees only a small marker payload.
            async def _execute_bridge_generation():
                full_response = ""
                audio_paths: list[str] = []
                tools_used: list[ToolInfo] = []
                tool_detected = False
                agent_span = observability.span("agent.basic_memory", {"source": "bridge"})
                agent_span.__enter__()
                try:
                    agent_stream = default_context_cache.agent_engine.chat(input_data)  # type: ignore[misc]
                    async for output in agent_stream:  # type: ignore[union-attr]
                        if isinstance(output, dict):
                            if output.get("type") == "tool_call_status":
                                tool_name = output.get("tool_name", "unknown")
                                status = str(output.get("status") or "")
                                observability.record_tool_status(tool_name, status)
                                tool_detected = True
                                if status in {"complete", "completed", "error", "denied"}:
                                    tool_result = output.get("result", output.get("content", output.get("message", "")))
                                    tools_used.append(ToolInfo(type="tool_result", name=tool_name, result=str(tool_result)))
                            continue
                        if isinstance(output, SentenceOutput):
                            text_piece = output.display_text.text
                            full_response += text_piece + " "
                            observability.emit("output_chunk", "output.text", "ok", {
                                "output_kind": "text", "output_length": len(text_piece or "")
                            })
                        elif isinstance(output, AudioOutput):
                            text_piece = output.display_text.text
                            full_response += text_piece + " "
                            if output.audio_path:
                                audio_paths.append(output.audio_path)
                            observability.emit("output_chunk", "output.audio", "ok", {
                                "output_kind": "audio", "output_length": len(text_piece or "")
                            })
                    agent_span.finish()
                    return {
                        "full_response": full_response,
                        "audio_paths": audio_paths,
                        "tools_used": tools_used,
                        "tool_detected": tool_detected,
                    }
                except BaseException as exc:
                    agent_span.finish("error", {"error_type": type(exc).__name__})
                    raise

            received_mono = time.monotonic()
            event_id = uuid.uuid4().hex
            bridge_actor = VerifiedActor(
                actor_id="bridge:process",
                bot_id=None,
                is_owner=False,
                scopes=frozenset({"bridge:process"}),
            )
            scheduled = ScheduledTurn(
                event_id=event_id,
                source="bridge",
                session_id=f"process:{event_id}",
                priority=150,
                received_mono=received_mono,
                expires_mono=received_mono + 120.0,
                payload={"kind": "bridge_process_generation", "request_id": event_id},
                actor=bridge_actor,
            )
            turn_arbiter = getattr(singleton, "_turn_arbiter", None)
            if turn_arbiter is None:
                # Explicit compatibility for isolated legacy test adapters that
                # provide no singleton queue. Production CeliaSingleton always has it.
                generation = await _execute_bridge_generation()
            else:
                turn_handle = await turn_arbiter.submit(
                    scheduled, executor=_execute_bridge_generation
                )
                if turn_handle.receipt is not None and turn_handle.receipt.status != "queued":
                    return BridgeResponse(response="", tool_used=False, tools=[],
                                          error=f"bridge_turn_{turn_handle.receipt.status}",
                                          elapsed_ms=int((time.time() - start_time) * 1000))
                if not singleton._processing:
                    await singleton._trigger_processing()
                try:
                    generation = await turn_arbiter.wait_result(turn_handle, 125.0)
                except asyncio.TimeoutError:
                    await turn_arbiter.cancel_for_actor(
                        bridge_actor.actor_id, event_id, "bridge_process_timeout"
                    )
                    return BridgeResponse(response="", tool_used=False, tools=[],
                                          error="bridge_turn_timeout",
                                          elapsed_ms=int((time.time() - start_time) * 1000))
            if not isinstance(generation, dict):
                return BridgeResponse(response="", tool_used=False, tools=[],
                                      error="bridge_turn_not_completed",
                                      elapsed_ms=int((time.time() - start_time) * 1000))
            full_response = generation["full_response"]
            audio_paths = generation["audio_paths"]
            tools_used = generation["tools_used"]
            tool_detected = generation["tool_detected"]

            full_response = _public_bridge_response(full_response, discord=_origin == "discord")

            if (
                _origin == "discord"
                and request.interaction_kind == "discord_call"
                and request.discord_voice_deadline is not None
                and time.time() >= request.discord_voice_deadline
            ):
                logger.info("Discord voice skipped before TTS: turn expired")
                return BridgeResponse(response="", tool_used=False, tools=[], error="discord_voice_expired")

            if not full_response:
                logger.warning("Bridge /process: saída ausente ou técnica retida antes de voz, legenda e histórico.")
                return BridgeResponse(response="", tool_used=tool_detected or bool(tools_used),
                                      tools=tools_used, error="output_suppressed",
                                      elapsed_ms=int((time.time() - start_time) * 1000), emotion=emotion)

            if request.prepare_only:
                if not context or not context.tts_engine:
                    return BridgeResponse(response="",tool_used=False,tools=[],error="tts_not_loaded")
                audio_path = await _generate_bridge_audio(context.tts_engine, full_response,
                                                         f"live_prepared_{uuid.uuid4().hex}", emotion)
                if not audio_path:
                    return BridgeResponse(response="",tool_used=False,tools=[],error="speech_not_generated")
                return BridgeResponse(response=full_response,tool_used=tool_detected or bool(tools_used),
                                      tools=tools_used,audio_paths=[_serve_generated_audio(audio_path)],
                                      elapsed_ms=int((time.time()-start_time)*1000),emotion=emotion)

            speech_id = None
            # Recording subtitles belong to playback, including bridge callers.
            if full_response and continuous_behavior.profile != "roteiro":
                await singleton.broadcast(_bridge_approved_event(full_response))

            if (
                request.synthesize_audio
                and full_response
                and context
                and context.tts_engine
            ):
                tts_started = time.perf_counter()
                try:
                    observability.emit(
                        "tts_start",
                        "service.tts",
                        "running",
                        {"output_length": len(full_response)},
                    )
                    try:
                        audio_path = await context.tts_engine.async_generate_audio(
                            text=full_response,
                            file_name_no_ext=f"bridge_{uuid.uuid4().hex}",
                            emotion=emotion,
                        )
                    except TypeError:
                        # Third-party TTS engines retain the old two-argument contract.
                        audio_path = await context.tts_engine.async_generate_audio(
                            text=full_response,
                            file_name_no_ext=f"bridge_{uuid.uuid4().hex}",
                        )
                    if audio_path:
                        audio_path = _serve_generated_audio(audio_path)
                        audio_paths.append(audio_path)
                        # Discord and Live2D must consume the SAME generated WAV.
                        # Returning only the path lets Discord speak, but leaves
                        # the Pet/Live2D with a metadata-only `tts.ready` event.
                        # Broadcast the normal audio payload here so lipsync and
                        # playback start from the same artifact without a second TTS pass.
                        playback_payload = prepare_audio_payload(
                            audio_path=audio_path,
                            display_text=DisplayText(text=full_response, name="Célia"),
                            actions=None,
                        )
                        speech_id = str(uuid.uuid4())
                        playback_payload["speech_id"] = speech_id
                        playback_payload["speech_source"] = "bridge.process"
                        # 2026-10-25: respostas ao chat do TikTok chegam por aqui. Sem isto a legenda (OBS e estudio
                        # mobile, que le obs_subtitles) sumia nessas falas; mesmo registro que /bridge/speak faz.
                        try:
                            obs_subtitles.start_subtitle(
                                full_response,
                                audio_path=audio_path,
                                speech_id=speech_id,
                                wait_for_playback=True,
                            )
                        except Exception as exc:  # noqa: BLE001 - legenda nunca derruba a fala
                            logger.warning(f"Bridge /process: legenda nao registrada: {type(exc).__name__}")
                        volumes = playback_payload.get("volumes") or []
                        slice_length_ms = int(playback_payload.get("slice_length") or 20)
                        register_lipsync_envelope(
                            speech_id,
                            volumes=volumes,
                            slice_length_ms=slice_length_ms,
                            duration_sec=(len(volumes) * slice_length_ms) / 1000.0,
                        )
                        singleton.set_playback_status(speech_id, "pending")
                        await singleton.broadcast(playback_payload)
                        await singleton.broadcast(
                            {
                                "type": "celia.tts.ready",
                                "source": "bridge.process",
                                "speech_id": speech_id,
                            }
                        )
                    observability.emit(
                        "tts_end",
                        "service.tts",
                        "ok",
                        {"audio_generated": bool(audio_path)},
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Bridge TTS failed: %s", type(exc).__name__)
                    observability.record_error("service.tts", exc)
                    await singleton.broadcast(
                        {
                            "type": "celia.tts.failed",
                            "error": f"{type(exc).__name__}: {exc}",
                            "source": "bridge.process",
                        }
                    )
                finally:
                    if _origin == "discord" and request.interaction_kind == "discord_call":
                        logger.info("Discord voice TTS elapsed_ms={}", round((time.perf_counter() - tts_started) * 1000))

            elapsed_ms = int((time.time() - start_time) * 1000)

            logger.info(
                f"🔗 Bridge /process respondeu em {elapsed_ms}ms: '{full_response[:100]}...'"
            )

            if full_response and context and context.history_uid:
                observability.emit(
                    "history_write", "memory.history", "running", {"history_role": "ai"}
                )
                store_message(
                    context.character_config.conf_uid,
                    context.history_uid,
                    "ai",
                    full_response,
                    name="Célia",
                )
                observability.emit(
                    "history_write", "memory.history", "ok", {"history_role": "ai"}
                )

            return BridgeResponse(
                response=full_response,
                tool_used=tool_detected or len(tools_used) > 0,
                tools=tools_used,
                error=None,
                elapsed_ms=elapsed_ms,
                audio_paths=audio_paths,
                speech_id=speech_id,
                emotion=emotion,
            )

        except Exception as e:
            observability.record_error("bridge.process", e)
            elapsed_ms = int((time.time() - start_time) * 1000)
            error_msg = f"{type(e).__name__}: {e!s}"
            logger.error(f"🔗 Bridge /process erro: {error_msg}")
            logger.error(traceback.format_exc())

            return BridgeResponse(
                response="",
                tool_used=False,
                tools=[],
                error=error_msg,
                elapsed_ms=elapsed_ms,
            )

    @router.post("/raw", response_model=BridgeRawResponse)
    @observed_async_turn("bridge.raw")
    async def bridge_raw(request: BridgeRequest):
        """
        Versão debug - retorna todos os chunks do stream individualmente.
        Útil para debugar o que o agente está gerando.
        """
        start_time = time.time()
        observability.emit(
            "input_received",
            "input.bridge_raw",
            "running",
            {"source": "bridge", "input_length": len(request.text or "")},
        )

        logger.info(f"🔗 Bridge /raw recebeu: '{request.text[:100]}...'")

        bloqueio, guardiao_estado, guardiao_motivo = await _guardar_entrada(request)
        _emit_guardian_decision(
            guardiao_estado,
            guardiao_motivo,
            source_declared=bool((request.fonte or "").strip()),
            route="bridge.raw",
            policy_mode=_guardian_policy_mode(request.fonte),
        )
        if bloqueio is not None:
            return BridgeRawResponse(
                chunks=[
                    BridgeRawChunk(
                        type="error", content="Entrada bloqueada pelo Guardian"
                    )
                ],
                full_response="",
                elapsed_ms=int((time.time() - start_time) * 1000),
                error=bloqueio,
            )

        if default_context_cache.agent_engine is None:
            observability.emit("agent_unavailable", "agent.basic_memory", "unavailable")
            return BridgeRawResponse(
                chunks=[BridgeRawChunk(type="error", content="Agente não carregado")],
                full_response="",
                elapsed_ms=0,
                error="Agente não carregado. Aguarde a inicialização completa.",
            )

        try:
            input_data = BatchInput(
                texts=[TextData(source=TextSource.INPUT, content=request.text)],
                images=[],
                files=[],
            )

            async def _execute_raw_generation():
                chunks: list[BridgeRawChunk] = []
                full_response = ""
                agent_span = observability.span("agent.basic_memory", {"source": "bridge_raw"})
                agent_span.__enter__()
                try:
                    agent_stream = default_context_cache.agent_engine.chat(input_data)  # type: ignore[misc]
                    async for output in agent_stream:  # type: ignore[union-attr]
                        if isinstance(output, dict):
                            observability.record_tool_status(output.get("tool_name"), output.get("status"))
                            chunks.append(BridgeRawChunk(type="tool_call", content=str(output), metadata=output))
                            continue
                        if isinstance(output, SentenceOutput):
                            text_piece = output.display_text.text
                            actions = output.actions
                            chunk_type = "text"
                            metadata: dict[str, Any] = {}
                            if actions and actions.expressions:
                                metadata["expressions"] = actions.expressions
                            if actions and actions.pictures:
                                metadata["pictures"] = actions.pictures
                                chunk_type = "tool_result"
                            if actions and actions.sounds:
                                metadata["sounds"] = actions.sounds
                            chunks.append(BridgeRawChunk(type=chunk_type, content=text_piece,
                                                         metadata=metadata if metadata else None))
                            full_response += text_piece + " "
                            observability.emit("output_chunk", "output.text", "ok", {
                                "output_kind": "text", "output_length": len(text_piece or "")
                            })
                        elif isinstance(output, AudioOutput):
                            text_piece = output.display_text.text
                            chunks.append(BridgeRawChunk(type="audio", content=text_piece,
                                                         metadata={"audio_path": output.audio_path}))
                            full_response += text_piece + " "
                            observability.emit("output_chunk", "output.audio", "ok", {
                                "output_kind": "audio", "output_length": len(text_piece or "")
                            })
                    agent_span.finish()
                    return {"chunks": chunks, "full_response": full_response}
                except BaseException as exc:
                    agent_span.finish("error", {"error_type": type(exc).__name__})
                    raise

            singleton = CeliaSingleton.get_instance()
            arbiter = getattr(singleton, "_turn_arbiter", None)
            if arbiter is None:
                raw_result = await _execute_raw_generation()
            else:
                received_mono = time.monotonic()
                event_id = uuid.uuid4().hex
                actor = VerifiedActor("bridge:process", None, False, frozenset({"bridge:process"}))
                scheduled = ScheduledTurn(
                    event_id, "bridge", f"raw:{event_id}", 150, received_mono,
                    received_mono + 120.0,
                    {"kind": "bridge_raw_generation", "request_id": event_id}, actor,
                )
                handle = await arbiter.submit(scheduled, executor=_execute_raw_generation)
                if handle.receipt is not None and handle.receipt.status != "queued":
                    return BridgeRawResponse(chunks=[BridgeRawChunk(type="error", content=handle.receipt.status)],
                                             full_response="", elapsed_ms=int((time.time()-start_time)*1000),
                                             error=f"bridge_turn_{handle.receipt.status}")
                if not singleton._processing:
                    await singleton._trigger_processing()
                try:
                    raw_result = await arbiter.wait_result(handle, 125.0)
                except asyncio.TimeoutError:
                    await arbiter.cancel_for_actor(actor.actor_id, event_id, "bridge_raw_timeout")
                    return BridgeRawResponse(chunks=[BridgeRawChunk(type="error", content="bridge_turn_timeout")],
                                             full_response="", elapsed_ms=int((time.time()-start_time)*1000),
                                             error="bridge_turn_timeout")
            chunks = raw_result["chunks"]
            full_response = raw_result["full_response"]

            full_response = full_response.strip()
            elapsed_ms = int((time.time() - start_time) * 1000)

            logger.info(
                f"🔗 Bridge /raw respondeu em {elapsed_ms}ms com {len(chunks)} chunks"
            )

            return BridgeRawResponse(
                chunks=chunks,
                full_response=full_response,
                elapsed_ms=elapsed_ms,
                error=None,
            )

        except Exception as e:
            observability.record_error("bridge.raw", e)
            if "agent_span" in locals():
                agent_span.finish("error", {"error_type": type(e).__name__})
            elapsed_ms = int((time.time() - start_time) * 1000)
            error_msg = f"{type(e).__name__}: {e!s}"
            logger.error(f"🔗 Bridge /raw erro: {error_msg}")
            logger.error(traceback.format_exc())

            return BridgeRawResponse(
                chunks=[BridgeRawChunk(type="error", content=error_msg)],
                full_response="",
                elapsed_ms=elapsed_ms,
                error=error_msg,
            )

    @router.get("/playback/{speech_id}/status")
    async def bridge_playback_status(speech_id: str):
        singleton = CeliaSingleton.get_instance()
        return {
            "ok": True,
            "speech_id": speech_id,
            "status": singleton.playback_status(speech_id) or "unknown",
        }

    @router.post("/playback/{speech_id}/cancel")
    async def bridge_cancel_playback(speech_id: str):
        singleton = CeliaSingleton.get_instance()
        singleton.set_playback_status(speech_id, "cancel_requested")
        await singleton.broadcast(
            {
                "type": "playback.cancel",
                "speech_id": speech_id,
                "source": "bridge.playback.cancel",
            }
        )
        return {"ok": True, "speech_id": speech_id, "status": "cancel_requested"}

    @router.post("/speak", response_model=SpeakResponse)
    async def bridge_speak(request: SpeakRequest, http_request: Request = None):
        """
        Faz a Célia FALAR um texto (ou tocar um WAV pronto) no MESMO sistema do
        VTuber: sintetiza com o `tts_engine` carregado (OmniVoice V2 na configuração atual) e faz broadcast
        do payload de áudio pros clientes Live2D — com lip-sync (volumes no payload).

        Usado pela integração Minecraft:
          - fala de conversa do bot  -> {"text": "..."}
          - alerta pré-gerado        -> {"audio_path": ".../000.wav"}  (sem re-sintetizar)
        """
        if not request.text and not request.audio_path:
            return SpeakResponse(ok=False, error="Informe 'text' ou 'audio_path'.")

        for public_text in (request.text, request.subtitle_text, request.context_text):
            if public_text and not _public_bridge_response(public_text):
                return SpeakResponse(ok=False, skipped=True, error="output_suppressed")

        # Validate before the cooldown so an invalid machine payload cannot
        # consume the speech window intended for a later human-facing line.
        if request.text is not None:
            text_error = _speech_text_error(request.text)
            if text_error:
                logger.warning("🔗 Bridge /speak recusou texto estrutural ou vazio.")
                return SpeakResponse(ok=False, error=text_error)

        speech_ticket = speech_coordinator.request(
            source=request.speech_source,
            ttl_sec=max(0.25, float(request.speech_ttl_ms or 8000) / 1000.0),
            event_key=request.speech_event_key or "",
            take_id=request.speech_take_id or "",
            match_id=request.speech_match_id or "",
            board_revision=request.speech_board_revision,
            context_revision=request.speech_context_revision,
            interruptible=request.speech_interruptible,
        )
        if speech_ticket is None:
            return SpeakResponse(
                ok=False,
                skipped=True,
                error="fala duplicada ou evento já consumido",
            )

        # Coordenação global: evita falas sobrepostas entre fontes. A autoridade
        # central decide validade; o lock abaixo continua serializando a síntese.
        allowed, restante, esperou = await _aguardar_vez_de_falar(request.priority)
        if not speech_coordinator.valid(speech_ticket.id):
            return SpeakResponse(
                ok=False,
                skipped=True,
                error="fala cancelada antes da síntese",
            )
        if esperou > 0.05:
            logger.info("🔗 Bridge /speak esperou %.1fs a vez na fila", esperou)
        if not allowed:
            logger.info(
                f"🔗 Bridge /speak suprimido por cooldown "
                f"(prio={request.priority}, {restante:.1f}s restantes)"
            )
            speech_coordinator.cancel_ticket(speech_ticket.id, "cooldown_rejected")
            return SpeakResponse(
                ok=False,
                skipped=True,
                error=f"cooldown global ({restante:.1f}s restantes)",
            )

        singleton = CeliaSingleton.get_instance()
        if singleton.context is None:
            return SpeakResponse(ok=False, error="Contexto não carregado ainda.")

        # NÃO interrompemos a fala/geração da Célia por eventos do jogo, mesmo com
        # priority=critical. Antes (jun/2026) o alert_watcher cortava ela ~1x/min
        # (logs: 17 interrupts em 28min), virou ruído insuportável quando o Criador
        # tava conversando. Eventos críticos agora respeitam a fala em andamento e
        # competem só pelo cooldown global (_speak_allowed). Quem AINDA pode cortar
        # a Célia: voz "calma celia" (voice_listener) + Codex (interrupt_current=True
        # no /codex_turn) + hotkey. Esses são humanos com intenção, não watcher.
        if (request.priority or "").lower() == "critical" and getattr(
            singleton, "_processing", False
        ):
            logger.debug(
                "🔗 Bridge /speak CRITICAL recebido com Célia ocupada — enfileirando, "
                "NÃO interrompendo (política pós-jun/2026)."
            )

        deferred_context = None
        if request.record_after_playback:
            request.wait_for_playback = True  # sem esperar o playback não dá pra saber se ela falou
        try:
            prerecorded_subtitle = bool(request.audio_path) and bool(
                (request.subtitle_text or "").strip()
            )
            audio_path = request.audio_path
            if audio_path is None:
                if not speech_coordinator.valid(speech_ticket.id):
                    return SpeakResponse(
                        ok=False,
                        skipped=True,
                        error="fala cancelada antes da síntese",
                    )
                tts_engine = singleton.context.tts_engine
                if tts_engine is None:
                    return SpeakResponse(ok=False, error="tts_engine não carregado.")
                # Nome único por chamada: evita colisão no cache\temp.wav fixo quando
                # há falas concorrentes (rajada de eventos do Minecraft) -> PermissionError.
                audio_path = await _generate_bridge_audio(
                    tts_engine,
                    request.text,
                    f"speak_{uuid.uuid4().hex}",
                    request.emotion,
                )
                if not audio_path:
                    speech_coordinator.cancel_ticket(speech_ticket.id, "tts_generation_failed")
                    return SpeakResponse(ok=False, error="Falha ao gerar áudio (TTS).")

            speech_coordinator.mark_generated(speech_ticket.id)
            if not speech_coordinator.valid(speech_ticket.id):
                logger.info(
                    "🔇 Bridge /speak descartou áudio pronto porque a intenção ficou obsoleta "
                    "(source=%s event=%s)",
                    request.speech_source,
                    request.speech_event_key or "",
                )
                return SpeakResponse(
                    ok=False,
                    skipped=True,
                    error="fala ficou obsoleta durante a síntese",
                )

            display_text = _public_bridge_response(
                request.text or request.subtitle_text or request.context_text or ""
            )

            if request.record_context:
                context_text = (
                    request.context_text or request.subtitle_text or request.text or ""
                ).strip()
                if context_text:
                    # This route can write directly into agent memory. It is a
                    # privileged internal ingress, so unknown content is enforced.
                    context_evidence = await evaluate_text_async(
                        context_text,
                        request.context_name or "internal_service",
                        _speak_guard_context(request, http_request),
                    )
                    _emit_guardian_decision(
                        context_evidence.decision,
                        context_evidence.category,
                        source_declared=False,
                        route="bridge.speak.context",
                        policy_mode=context_evidence.context.policy_mode,
                    )
                    if context_evidence.decision == "blocked":
                        return SpeakResponse(ok=False, error="bloqueado_pelo_guardiao")
                    agent = singleton.context.agent_engine
                    if request.context_as_observation:
                        # Observação de jogo: NÃO persiste em chat_history nem aparece
                        # como turno na UI. Vai SÓ na memória em RAM do agente como
                        # role="system" — o LLM enxerga como contexto externo sem
                        # virar fala dela nem turno humano (não dispara foi_mencionada
                        # / detecção de interlocutor). Prefixo [cena] no conteúdo
                        # é garantido pelos chamadores (alert_watcher / game_mind).
                        if agent is not None and hasattr(agent, "_add_message"):
                            agent._add_message(
                                context_text,
                                "system",
                                display_text=DisplayText(
                                    text=context_text,
                                    name=request.context_name or "Jogo",
                                ),
                            )
                        if prerecorded_subtitle:
                            # Alertas pré-gravados são fala real da Célia na live:
                            # persistimos a legenda do WAV em disco (chat_history)
                            # para a UI. NÃO duplicamos no _add_message do agente —
                            # a cena já entrou como observation acima; o WAV não veio
                            # do LLM, então não precisa virar role=assistant na RAM.
                            spoken_text = (request.subtitle_text or "").strip()
                            if singleton.context.history_uid:
                                store_message(
                                    singleton.context.character_config.conf_uid,
                                    singleton.context.history_uid,
                                    "ai",
                                    spoken_text,
                                    name="Célia",
                                )
                    elif request.record_after_playback:
                        deferred_context = context_text  # gravado só depois do playback (abaixo)
                    else:
                        # Comportamento legado: grava como fala da Célia.
                        ctx_name = request.context_name or "Célia"
                        if singleton.context.history_uid:
                            store_message(
                                singleton.context.character_config.conf_uid,
                                singleton.context.history_uid,
                                "ai",
                                context_text,
                                name=ctx_name,
                            )
                        if agent is not None and hasattr(agent, "_add_message"):
                            agent._add_message(
                                context_text,
                                "assistant",
                                display_text=DisplayText(
                                    text=context_text,
                                    name=ctx_name,
                                ),
                            )

            payload = prepare_audio_payload(
                audio_path=audio_path,
                display_text=DisplayText(text=display_text, name="Célia"),
                actions=None,
            )
            speech_id = str(uuid.uuid4())
            payload["speech_id"] = speech_id
            speech_coordinator.bind_speech(speech_ticket.id, speech_id)
            payload["speech_ticket_id"] = speech_ticket.id
            payload["speech_source"] = request.speech_source
            volumes = payload.get("volumes") or []
            slice_length_ms = int(payload.get("slice_length") or 20)
            register_lipsync_envelope(
                speech_id,
                volumes=volumes,
                slice_length_ms=slice_length_ms,
                duration_sec=(len(volumes) * slice_length_ms) / 1000.0,
            )
            subtitle_text = _public_bridge_response(request.subtitle_text or display_text)
            if subtitle_text:
                if obs_subtitles.start_subtitle(
                    subtitle_text,
                    audio_path=audio_path,
                    speech_id=speech_id,
                    wait_for_playback=True,
                ):
                    payload["speech_id"] = speech_id
            # Warn quando ninguém vai ouvir no Live2D — voz ainda sai pelo CABLE
            # Input mas o navegador não vai mostrar nada. Abra http://localhost:12393/
            n_clients = singleton.active_connections
            playback_owner_uid = getattr(singleton, "playback_owner_uid", None)
            singleton.set_playback_status(speech_id, "pending")
            playback_future = None
            if request.wait_for_playback and playback_owner_uid:
                # Arm before broadcast: a very short clip may ACK immediately.
                playback_future = singleton.begin_playback_wait(speech_id)
            if not playback_owner_uid:
                logger.warning(
                    f"🔗 Bridge /speak: nenhum playback owner disponível — voz só "
                    f"no playback local. Abra http://localhost:12393/ no navegador. "
                    f"(text='{(display_text or subtitle_text or '')[:50]}...')"
                )
            if not speech_coordinator.valid(speech_ticket.id):
                if playback_future is not None:
                    singleton.end_playback_wait(speech_id)
                return SpeakResponse(
                    ok=False,
                    skipped=True,
                    error="fala cancelada antes do playback",
                    speech_id=speech_id,
                )
            await singleton.broadcast(payload)
            mirror_devices = [
                os.getenv("CELIA_TRACK_AUDIO_MIRROR_DEVICE", "CABLE Input")
            ]
            real_device = os.getenv("CELIA_TRACK_AUDIO_REAL_DEVICE", "").strip()
            if real_device:
                mirror_devices.append(real_device)
            _play_audio_to_device(audio_path, mirror_devices)
            if request.emotion:
                # Emotion attached to Célia's own speech is character affect,
                # not a timed technical reaction. Keep it until a later explicit
                # affect replaces it; transient events still use /bridge/emotion.
                publish_emotion(
                    request.emotion,
                    source=request.emotion_source or "bridge_speak",
                    priority=request.emotion_priority or 50,
                    ttl_sec=None,
                    persistent=True,
                )
            try:
                audio_dur = _wav_duration_sec(audio_path)
            except Exception:
                audio_dur = 0.0
            playback_status = None
            timed_out = False
            if request.wait_for_playback:
                if playback_future is None:
                    # Sem browser não existe ACK. O playback local já foi
                    # disparado; use a duração real do WAV para não jogar antes
                    # da fala que está saindo pelo dispositivo local.
                    if audio_dur > 0:
                        await asyncio.sleep(min(audio_dur, 30.0))
                    playback_status = "local_duration_elapsed"
                else:
                    try:
                        timeout_s = max(2.0, min(30.0, audio_dur + 4.0))
                        playback_status = await asyncio.wait_for(
                            asyncio.shield(playback_future), timeout=timeout_s
                        )
                    except asyncio.TimeoutError:
                        timed_out = True
                        playback_status = "timeout"
                        singleton.resolve_playback_wait(speech_id, "timeout")
                    finally:
                        singleton.end_playback_wait(speech_id)
            if playback_status in {
                "ended", "finished", "complete", "completed", "ok", "success",
                "local_duration_elapsed",
            }:
                speech_coordinator.mark_finished(speech_id, playback_status)
            recorded = None
            if deferred_context:
                recorded = _record_spoken_line(singleton, deferred_context, request.context_name or "Célia", playback_status)
            return SpeakResponse(
                ok=True,
                recorded=recorded,
                clients=len(singleton._connections),
                audio_duration_sec=audio_dur,
                speech_id=speech_id,
                playback_status=playback_status,
                timed_out=timed_out,
            )

        except Exception as e:
            if (
                "playback_future" in locals()
                and playback_future is not None
                and "singleton" in locals()
                and "speech_id" in locals()
            ):
                singleton.resolve_playback_wait(speech_id, "error")
                singleton.end_playback_wait(speech_id)
            if "speech_ticket" in locals() and speech_ticket is not None:
                speech_coordinator.cancel_ticket(speech_ticket.id, "bridge_speak_exception")
            error_msg = f"{type(e).__name__}: {e!s}"
            logger.error(f"🔗 Bridge /speak erro: {error_msg}")
            logger.error(traceback.format_exc())
            return SpeakResponse(ok=False, error=error_msg)

    @router.post("/external-turn", response_model=ExternalTurnResponse)
    @observed_async_turn("bridge.external_turn")
    async def bridge_external_turn(request: ExternalTurnRequest):
        """Play a named Codex turn in the browser, then submit it to Celia."""
        text = (request.text or "").strip()
        speaker_name = (request.speaker_name or "Codex").strip() or "Codex"
        if not text:
            return ExternalTurnResponse(ok=False, error="Informe 'text'.")

        guardian_evidence = await evaluate_text_async(
            text, speaker_name, new_context("external_turn", external=True)
        )
        _emit_guardian_decision(
            guardian_evidence.decision,
            guardian_evidence.category,
            source_declared=False,
            route="bridge.external_turn",
            policy_mode=guardian_evidence.context.policy_mode,
        )
        if guardian_evidence.decision == "blocked":
            return ExternalTurnResponse(ok=False, error="bloqueado_pelo_guardiao")

        singleton = CeliaSingleton.get_instance()
        if singleton.context is None:
            return ExternalTurnResponse(ok=False, error="Contexto nao carregado ainda.")

        speech_id = str(uuid.uuid4())
        audio_duration = None
        audio_path = None
        submitted = False
        timed_out = False
        playback_status = None

        async with singleton.codex_playback_lock:
            ack_future = singleton.begin_codex_playback(speech_id)
            try:
                if request.interrupt_current:
                    await singleton.interrupt_for_external_playback()
                else:
                    singleton.clear_pending_inputs()

                tts_engine = _create_codex_tts_engine(singleton)
                audio_path = await tts_engine.async_generate_audio(text=text)
                if not audio_path:
                    raise RuntimeError("Falha ao gerar audio OmniVoice do Codex.")

                audio_duration = _wav_duration_sec(audio_path)

                # Toca a voz do Codex tambem nos alto-falantes reais do PC
                # (o broadcast abaixo continua indo pro browser p/ lip-sync e ACK).
                if _CODEX_AUDIO_DEVICE:
                    _play_audio_to_device(audio_path, _CODEX_AUDIO_DEVICE)

                payload = prepare_audio_payload(
                    audio_path=audio_path,
                    display_text=DisplayText(text=text, name=speaker_name),
                    actions=None,
                )
                volumes = payload.get("volumes") or []
                slice_length_ms = int(payload.get("slice_length") or 20)
                register_lipsync_envelope(
                    speech_id,
                    volumes=volumes,
                    slice_length_ms=slice_length_ms,
                    duration_sec=(len(volumes) * slice_length_ms) / 1000.0,
                )
                payload.update(
                    {
                        "speech_id": speech_id,
                        "external_playback": True,
                        "audio_duration": audio_duration,
                    }
                )
                await singleton.broadcast({"type": "force-new-message"})
                await singleton.broadcast(
                    {"type": "control", "text": "conversation-chain-start"}
                )
                await singleton.broadcast(payload)

                try:
                    playback_status = await asyncio.wait_for(
                        asyncio.shield(ack_future),
                        timeout=audio_duration + 2.0,
                    )
                except asyncio.TimeoutError:
                    timed_out = True
                    playback_status = "timeout"
                    logger.warning(
                        "Codex playback ACK timeout: "
                        f"speech_id={speech_id} duration={audio_duration:.2f}s"
                    )

                if timed_out or playback_status == "error":
                    await singleton.broadcast(
                        {
                            "type": "interrupt-signal",
                            "text": "external-playback-failed",
                        }
                    )
                await singleton.broadcast({"type": "force-new-message"})
                await singleton.submit_input(
                    text,
                    role="source",
                    speaker_name=speaker_name,
                    origin="external_turn",
                    guardian_evidence=guardian_evidence,
                )
                submitted = True
                return ExternalTurnResponse(
                    ok=True,
                    speech_id=speech_id,
                    playback_status=playback_status,
                    timed_out=timed_out,
                    submitted=submitted,
                    audio_duration=audio_duration,
                    audio_path=audio_path,
                    clients=len(singleton._connections),
                )
            except Exception as e:
                error_msg = f"{type(e).__name__}: {e!s}"
                logger.error(f"Bridge /external-turn erro: {error_msg}")
                logger.error(traceback.format_exc())
                return ExternalTurnResponse(
                    ok=False,
                    speech_id=speech_id,
                    playback_status=playback_status,
                    timed_out=timed_out,
                    submitted=submitted,
                    audio_duration=audio_duration,
                    audio_path=audio_path,
                    clients=len(singleton._connections),
                    error=error_msg,
                )
            finally:
                singleton.end_codex_playback(speech_id)

    @router.get("/external-turn/status")
    async def bridge_external_turn_status():
        singleton = CeliaSingleton.get_instance()
        return {
            "ok": True,
            "context_loaded": singleton.context is not None,
            "codex_playback_active": singleton.codex_playback_active,
            "audio_pending": any(status in {"pending", "playing", "started", "queued"}
                                 for status, _ in singleton._playback_statuses.values()),
            "active_speech_id": singleton._active_codex_speech_id,
            "processing": singleton.is_processing,
            "pending_batches": singleton.pending_batches,
            "clients": singleton.active_connections,
        }

    class JevEventRequest(BaseModel):
        """Pointer to a Jev journal entry. The text is rebuilt from the journal, never taken from this body."""

        event_id: int = Field(..., ge=1)
        mission_id: str = Field(..., max_length=64)
        epoch: int = Field(..., ge=1)
        kind: str = Field("", max_length=32)

    @router.get("/minecraft/hud")
    async def minecraft_hud(request: Request):
        """Fatos da HUD Minecraft (somente leitura, loopback): corpo + etapas do Jev + mapa. Sem dado fresco, diz 'indisponivel'."""
        _require_loopback(request)
        from .core.minecraft_hud import fetch_hud

        return await asyncio.to_thread(fetch_hud)

    class SeriesUiCommand(BaseModel):
        model_config = ConfigDict(extra="forbid")
        intent: str = Field(..., max_length=40)
        params: dict = Field(default_factory=dict)
        confirmed: bool = False

    @router.get("/minecraft/series")
    async def minecraft_series_status(request: Request):
        """Series status for the profile editor (loopback, read-only): recording state, companion, worlds. Offline is said as offline."""
        _require_loopback(request)
        from .core.minecraft_series import daemon_request

        status = await asyncio.to_thread(daemon_request, "GET", "/series", None, 3.0)
        worlds = await asyncio.to_thread(daemon_request, "POST", "/series/command", {"intent": "world.list", "params": {}}, 6.0) if status.get("ok") else {}
        return {"ok": bool(status.get("ok")), "status": status, "worlds": worlds.get("worlds") if worlds.get("ok") else None,
                "active_world_id": worlds.get("active_world_id") if worlds.get("ok") else None,
                "profile": continuous_behavior.profile}

    @router.post("/minecraft/series/command")
    async def minecraft_series_command(request: Request, body: SeriesUiCommand):
        """The profile editor's fallback buttons (loopback): same owner gate and same typed forward as the voice commands."""
        _require_loopback(request)
        from .core.minecraft_series import get_series_center, ui_command

        return await ui_command(get_series_center(), body.intent, body.params, confirmed=body.confirmed)

    @router.post("/minecraft/jev-event")
    async def minecraft_jev_event(request: Request, body: JevEventRequest):
        """Jev -> Célia: speak one factual event as the persona. Loopback only, single use, epoch-checked."""
        _require_loopback(request)
        # Custom V6 trigger policy controls when an event may invite a comment;
        # facts, tools and mission authority remain entirely with Jev.
        trigger_name = {
            "achievement": "achievement", "danger": "danger", "death": "death",
            "succeeded": "goal_complete", "blocked": "goal_failed",
            "escalation": "danger", "paused": "world_change",
            "interrupted": "world_change", "outcome": "world_change",
        }.get(body.kind)
        from .core.jev_narration import build_narration, open_store

        try:
            store = open_store()
            try:
                from .core.possession import get_possession
                from .core.possession_events import publish_jev_event

                try:
                    delivery = publish_jev_event(get_possession(), store, body.event_id, body.mission_id, body.epoch)
                    if delivery.get("queued"):
                        logger.info(f"[Jev] evento {body.event_id} entregue à possessão seq={delivery['seq']}")
                except Exception as exc:
                    # Agent delivery is independent from persona narration; preserve the validated narration path if the queue is unavailable.
                    logger.warning(f"[Jev] evento não entrou na possessão: {type(exc).__name__}: {exc}")
                decision = build_narration(store, body.event_id, body.mission_id, body.epoch)
            finally:
                store.close()
        except Exception as exc:
            logger.warning(f"[Jev] narração indisponível: {type(exc).__name__}: {exc}")
            return {"ok": False, "spoken": False, "reason": "journal_unavailable"}
        if not decision.get("spoken"):
            return {k: decision[k] for k in ("ok", "spoken", "reason")}
        # Only a validated, current journal event may consume trigger cooldown.
        if trigger_name and continuous_behavior.profile_settings(continuous_behavior.profile)["customized"]:
            if not continuous_behavior.profile_trigger_allowed(trigger_name, str(body.event_id)):
                return {"ok": True, "spoken": False, "reason": "v6_trigger_disabled_or_cooldown"}
        singleton = CeliaSingleton.get_instance()
        if singleton.context is None:
            return {"ok": False, "spoken": False, "reason": "context_not_loaded"}
        # Two sheets submitted together are merged into one persona turn (measured: the final success was swallowed
        # by the preceding achievement). Wait for the current turn to finish; the final events wait longer.
        from .core.jev_narration import TERMINAL as _TERMINAL_KINDS

        patience = 40.0 if decision["kind"] in _TERMINAL_KINDS else 8.0
        waited = 0.0
        while (singleton.is_processing or singleton.pending_batches) and waited < patience:
            await asyncio.sleep(0.4)
            waited += 0.4
        if (singleton.is_processing or singleton.pending_batches) and decision["kind"] not in _TERMINAL_KINDS:
            return {"ok": True, "spoken": False, "reason": "persona_busy"}
        accepted = await singleton.submit_input(decision["text"], role="source", speaker_name="Corpo no jogo", origin="system")
        logger.info(f"[Jev] fala da persona pedida para {decision['kind']} (aceita={accepted})")
        return {"ok": True, "spoken": bool(accepted), "reason": "submitted" if accepted else "not_accepted"}

    @router.post("/minecraft/body-event")
    async def minecraft_body_event(request: Request):
        """Queue factual body snapshots; inventory change never implies donor identity."""
        _require_loopback(request)
        try:
            body = await request.json()
            session_id, seq = str(body["session_id"]), int(body["state_seq"])
            observed_at = float(body["observed_at"])
            world_epoch = int(body["world_epoch"])
            kinds = [k for k in body["events"] if k in {"damage", "death", "disconnect", "spawn", "inventory_change", "item_received"}]
        except (KeyError, TypeError, ValueError, AttributeError):
            return {"ok": False, "reason": "invalid_body_event"}
        if not session_id or len(session_id) > 128 or seq < 0 or not kinds:
            return {"ok": False, "reason": "invalid_body_event"}
        from .core.possession import get_possession
        poss = get_possession()
        if not poss.active():
            return {"ok": True, "queued": False, "reason": "possession_inactive"}
        if poss.s.world_epoch is not None and world_epoch < poss.s.world_epoch:
            return {"ok": True, "queued": False, "reason": "stale_world_epoch"}
        if poss.s.world_epoch is not None and world_epoch > poss.s.world_epoch:
            old_epoch = poss.s.world_epoch
            poss.note_world_epoch(world_epoch)
            poss.post_event("world_changed", dedupe_key=f"world:{poss.s.session_id}:{world_epoch}",
                            source="series_epoch", event_key=f"world:{poss.s.session_id}:{world_epoch}",
                            previous_world_epoch=old_epoch, world_epoch=world_epoch,
                            world_id=str(body.get("world_id") or "")[:128], observed_at=observed_at)
        inventory = body.get("inventory") if isinstance(body.get("inventory"), dict) else {}
        counts = {str(n)[:64]: c for n, c in inventory.items() if isinstance(n, str) and isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 4096}
        raw_delta = body.get("inventory_delta") if isinstance(body.get("inventory_delta"), dict) else {}
        delta = {str(n)[:64]: c for n, c in raw_delta.items() if isinstance(n, str) and isinstance(c, int) and not isinstance(c, bool) and -4096 <= c <= 4096 and c != 0}
        facts = {"events": kinds, "health": body.get("health"), "inventory_counts": counts,
                 "inventory_delta": delta, "donor": "unknown", "world_id": str(body.get("world_id") or "")[:128]}
        key = f"body:{poss.s.session_id}:{world_epoch}:{session_id}:{seq}"
        previous_seq = poss.seq
        event_seq = poss.post_event("body_observation", dedupe_key=key, source="mineflayer_snapshot",
                                    event_key=key, body_session_id=session_id, state_seq=seq,
                                    world_epoch=world_epoch, observed_at=observed_at, facts=facts)
        from .core.cognitive_loop import cognitive_loop
        if event_seq > previous_seq:
            cognitive_loop.note_minecraft_event(key, {"events": kinds, "health": facts["health"],
                                                     "inventory_delta": delta, "donor": "unknown", "world_id": facts["world_id"]})
            cognitive_loop.request_think("minecraft_body_event")
        received = body.get("collected_items") if isinstance(body.get("collected_items"), list) else []
        posted_items = []
        for raw in received[:16]:
            if not isinstance(raw, dict):
                continue
            item_id, item_name, amount = raw.get("id"), raw.get("item"), raw.get("count")
            collector = raw.get("collector")
            if (not isinstance(item_id, str) or not item_id or len(item_id) > 64
                    or not isinstance(item_name, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", item_name)
                    or not isinstance(amount, int) or isinstance(amount, bool) or not 1 <= amount <= 64
                    or not isinstance(collector, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,16}", collector)):
                continue
            collection_key = f"pickup:{poss.s.session_id}:{world_epoch}:{session_id}:{item_id}"
            before_collection_seq = poss.seq
            collection_seq = poss.post_event(
                "item_received", dedupe_key=collection_key, source="mineflayer_player_collect",
                event_key=collection_key, body_session_id=session_id, world_epoch=world_epoch,
                observed_at=observed_at,
                facts={"item": item_name, "count": amount, "collector": collector,
                       "donor": "unknown", "evidence": "Mineflayer playerCollect + dropped-item stack"},
            )
            if collection_seq not in posted_items:
                posted_items.append(collection_seq)
            if collection_seq > before_collection_seq:
                cognitive_loop.note_minecraft_event(collection_key, {"events": ["item_received"], "item": item_name,
                                                                      "count": amount, "collector": collector,
                                                                      "donor": "unknown", "evidence": "playerCollect"})
                cognitive_loop.request_think("minecraft_item_received")
        return {"ok": True, "queued": True, "seq": event_seq, "event_key": key, "item_event_seqs": posted_items}

    @router.post("/emotion")
    async def bridge_emotion(request: EmotionRequest):
        """Publishes physical Minecraft events immediately, without requiring speech."""
        return publish_emotion(
            request.emotion,
            source=request.source,
            priority=request.priority,
            ttl_sec=request.ttl_sec,
            persistent=request.persistent,
            metadata=request.metadata,
        )

    # ═══ Visão: o navegador é a única fonte de imagem (decisão 1a do dono) ═══
    # As ferramentas vision_* leem o ÚLTIMO frame enviado pelo Screen da
    # interface; a captura de tela no servidor continua proibida (nada de mss,
    # spectacle, portal XDG ou PIL.ImageGrab). Slot único em memória — o frame
    # mais recente vence, nada vai para disco e nenhuma imagem é versionada.
    # A validação é a MESMA de `_bridge_images()`: somente
    # `data:image/jpeg;base64,` dentro do limite `_MAX_BRIDGE_IMAGE_BYTES`.
    _vision_frame_slot: dict[str, dict[str, Any]] = {}  # um slot POR FONTE ligada

    class VisionFrameRequest(BaseModel):
        image: str
        source: str = "screen"
        relevance: float | None = Field(default=None, ge=0.0, le=1.0)
        unchanged: bool = False

    class VisionStateRequest(BaseModel):
        source: str
        active: bool
        width: int | None = Field(default=None, ge=1, le=16384)
        height: int | None = Field(default=None, ge=1, le=16384)

    @router.post("/vision/state")
    async def post_vision_state(request: Request, body: VisionStateRequest):
        """Sinal explícito do navegador: a fonte (tela/câmera) foi ligada ou desligada.

        Só o clique do Criador liga uma fonte. Desligar limpa o frame, o cache e a
        análise em voo DAQUELA fonte; a outra fonte não é tocada.
        """
        _require_loopback(request)
        from .core.vision_sources import vision_sources

        try:
            result = vision_sources.set_active(
                body.source,
                body.active,
                width=body.width,
                height=body.height,
                reason="signal",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not result["active"]:
            _vision_frame_slot.pop(result["source"], None)
        if result["changed"]:
            logger.info(
                f"👁️ fonte {result['source']} "
                f"{'LIGADA' if result['active'] else 'desligada'} pelo navegador"
            )
        return {"ok": True, **result, "state": vision_sources.snapshot()}

    @router.get("/vision/state")
    async def get_vision_state(request: Request):
        _require_loopback(request)
        from .core.vision_sources import vision_sources

        return vision_sources.snapshot()

    @router.post("/vision/frame")
    async def post_vision_frame(request: Request, body: VisionFrameRequest):
        """Guarda o último frame de cada fonte LIGADA (slot único por fonte)."""
        _require_loopback(request)
        from .core.vision_sources import normalize_source, vision_sources

        source = normalize_source(body.source)
        if source is None:
            raise HTTPException(status_code=422, detail="fonte de visão desconhecida")
        if not vision_sources.is_active(source):
            # Fonte desligada: nada é guardado, medido nem analisado. Se o navegador
            # ainda tiver o stream vivo (ex.: o servidor reiniciou), ele reenvia o
            # sinal "ligada" ao ver esta recusa.
            _vision_frame_slot.pop(source, None)
            return {
                "ok": True,
                "accepted": False,
                "reason": "source_inactive",
                "source": source,
            }
        try:
            _bridge_images([body.image])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        bruto = base64.b64decode(body.image.split(",", 1)[1], validate=True)
        vision_sources.touch(source)
        _vision_frame_slot[source] = {
            "image": body.image,
            "recebido_em": time.monotonic(),
        }
        try:
            from .core.screen_vision import screen_vision as _sv
            _sv.remember_frame(body.image, source)
        except Exception as exc:
            logger.debug("screen vision frame memory unavailable: %s", type(exc).__name__)
        if body.unchanged:
            try:
                from .core.vision_cache import vision_cache
                from .core.screen_vision import screen_vision as _sv_touch

                # A tela parada só "reconfirma" a análise que veio DESTA fonte.
                if _sv_touch._last_result_source in ("", source):
                    vision_cache.touch()
            except Exception as exc:
                logger.debug(
                    "vision cache touch unavailable: %s", type(exc).__name__
                )
        screen_result = None
        action = None
        if body.relevance is not None:
            screen_result = continuous_behavior.screen_changed(
                relevance=body.relevance, source=source
            )
            logger.info(
                f"🖥️ {source}.changed relevance={body.relevance:.3f} "
                f"action={screen_result.get('action')}"
            )
            action = screen_result.get("action")
            if action == "microaction":
                try:
                    publish_emotion(
                        screen_result.get("emotion", "surprise"),
                        source="screen_change",
                        priority=35,
                        ttl_sec=1.5,
                        lock_sec=0,
                    )
                except Exception as exc:
                    logger.debug("screen frame microaction unavailable: %s", type(exc).__name__)
        vision_scheduled = False
        background_cache = os.getenv(
            "CELIA_VISION_CACHE_BACKGROUND", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        # O detector do navegador (grade 16x9) não enxerga uma linha de texto/caixa de diálogo mudando: o servidor mede o quadro
        # (grade 64x36 + gradiente) e, se ele decidir que a mudança merece OCR/visão, agenda mesmo sem `relevance` do navegador.
        server_decision = None
        try:
            from .core.screen_vision import screen_vision as _sv_decide
            server_decision = _sv_decide.decision_for_frame(source)
        except Exception as exc:
            logger.debug("screen vision decision unavailable: %s", type(exc).__name__)
        server_wants = server_decision is not None and server_decision.action != "skip" and not body.unchanged
        if (
            (continuous_behavior.profile != "chat" or background_cache)
            and (body.relevance is not None or server_wants)
            and (screen_result or {}).get("action") != "ignored"
        ):
            try:
                from .core.screen_vision import screen_vision
                vision_scheduled = screen_vision.schedule(
                    body.image,
                    relevance=body.relevance if body.relevance is not None else 0.7,
                    source=source,
                )
            except Exception as exc:
                logger.debug("screen vision schedule unavailable: %s", type(exc).__name__)
        # Um frame relevante = um pensamento. Se a visão foi agendada, é ELA que
        # acorda o loop cognitivo quando terminar (já com o resumo novo); acordar
        # aqui também gastaria uma segunda chamada de LLM com contexto velho.
        if action == "candidate" and not vision_scheduled:
            try:
                from .core.cognitive_loop import cognitive_loop
                cognitive_loop.request_think("screen_change")
            except Exception as exc:
                logger.debug("screen cognitive wake unavailable: %s", type(exc).__name__)
        return {
            "ok": True,
            "accepted": True,
            "source": source,
            "bytes": len(bruto),
            "screen": screen_result,
            "vision_scheduled": vision_scheduled,
        }

    @router.post("/vision/analyze")
    async def post_vision_analyze(request: Request):
        """Análise explícita do quadro atual (OCR + visão + memória da cena) para as tools de visão. Somente loopback."""
        _require_loopback(request)
        from .core.screen_vision import screen_vision

        try:
            payload = await request.json()
        except Exception:
            payload = {}
        source = payload.get("source") if isinstance(payload, dict) else None
        ocr_only = bool(payload.get("ocr_only")) if isinstance(payload, dict) else False
        deadline = time.monotonic() + 20.0
        result = await screen_vision.analyze_now(source if isinstance(source, str) else None, ocr_only=ocr_only)
        if result.get("ok") and not ocr_only:
            result = await screen_vision.identify_scene_candidates(result, deadline)
        return result

    @router.post("/vision/identity")
    async def post_vision_identity(request: Request):
        _require_loopback(request)
        from .core.screen_vision import screen_vision
        from .core.vision_policy import VisionUnavailable
        data = await request.json()
        identity = data.get("identity", "celia")
        if identity not in {"celia", "criador_slime", "criador_human"}:
            raise HTTPException(status_code=422, detail="unknown visual identity")
        try:
            return {"ok": True, **await screen_vision.verify_identity(identity, data.get("source", "screen"))}
        except VisionUnavailable as exc:
            return exc.as_dict()

    @router.get("/vision/scene")
    async def get_vision_scene(request: Request):
        """Diagnóstico somente leitura da memória visual (cena, buffer, última decisão e ciclo). Sem imagens."""
        _require_loopback(request)
        from .core.screen_vision import screen_vision

        return screen_vision.visual_snapshot()

    @router.get("/vision/frame")
    async def get_vision_frame(request: Request, source: str | None = None):
        """Entrega o último frame de uma fonte LIGADA ao `capture_screen()` dos MCPs.

        Sem `source`: o frame mais recente entre as fontes ligadas. Tudo desligado
        responde 404 `visao_desligada` (nunca devolve frame velho de fonte desligada).
        """
        _require_loopback(request)
        from .core.vision_sources import SOURCES, normalize_source, vision_sources

        if source is not None:
            canonical = normalize_source(source)
            if canonical is None:
                raise HTTPException(status_code=422, detail="fonte de visão desconhecida")
            candidatas = [canonical]
        else:
            candidatas = list(SOURCES)
        ligadas = [s for s in candidatas if vision_sources.is_active(s)]
        for s in candidatas:
            if s not in ligadas:
                _vision_frame_slot.pop(s, None)
        if not ligadas:
            raise HTTPException(status_code=404, detail="visao_desligada")
        com_frame = [
            (s, _vision_frame_slot[s])
            for s in ligadas
            if _vision_frame_slot.get(s, {}).get("image")
        ]
        if not com_frame:
            raise HTTPException(status_code=404, detail="sem_frame")
        escolhida, slot = max(com_frame, key=lambda item: item[1].get("recebido_em", 0.0))
        recebido_em = slot.get("recebido_em", time.monotonic())
        return {
            "image": slot["image"],
            "age_ms": int((time.monotonic() - recebido_em) * 1000),
            "source": escolhida,
        }

    return router
