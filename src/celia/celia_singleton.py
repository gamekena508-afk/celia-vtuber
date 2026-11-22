"""
CeliaSingleton — Instância única compartilhada da Célia.

- Uma única instância da Célia (agent_engine, history_uid) para todo o servidor
- Broadcast: toda resposta é enviada para TODAS as conexões abertas
- Buffer de lotes: inputs externos acumulam enquando ela responde,
  processados em grupos de até 5 (merge em 1 input por lote)
- Interrupção: apenas conexões com role='browser' podem interromper
- Histórico persistente: singleton_state.json restaura o history_uid entre reinicializações
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from fastapi import WebSocket
from loguru import logger

from .chat_history_manager import create_new_history, get_latest_history_uid
from .service_context import ServiceContext
from .conversations.single_conversation import process_single_conversation
from .conversations.conversation_utils import EMOJI_LIST
from .core.cognitive_loop import cognitive_loop
from .core.continuous_behavior import continuous_behavior
from .core.recording_reply_shape import shape_emotion_prompt
from .core.conversation_event_queue import ConversationEventQueue
from .core.turn_arbiter import TurnArbiter
from .core.ingress_security import GuardianEvidence, evaluate_text_async, evidence_matches, new_context
from .live_mode import gate as live_gate
from .core.observability import observability
from .core.memoria import Memoria, normalizar_texto, registrar_turno_seguro
from .core.roteador import Roteador
from .core.raizes import MEMORY_STATE
from .celia_emotions import CeliaEmotions
import numpy as np

SINGLETON_STATE_FILE = "singleton_state.json"
BATCH_SIZE = 5  # Máximo de inputs por lote
SINGLETON_CONTEXT_PATIENCE_SEC = 300.0


class CeliaSingleton:
    """
    Instância única da Célia.
    Criada uma vez no startup do servidor e compartilhada entre todas as conexões.
    """

    _instance: Optional["CeliaSingleton"] = None

    @classmethod
    def get_instance(cls) -> "CeliaSingleton":
        if cls._instance is None:
            cls._instance = CeliaSingleton()
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Para testes ou reload — força recriação do singleton."""
        cls._instance = None

    def __init__(self):
        # Contexto de serviço compartilhado (agent, tts, asr, etc.)
        self.context: Optional[ServiceContext] = None

        # Motor de emoções — penaliza repetição, gera prompt emocional
        self.emotions = CeliaEmotions(anti_repeat_window=3)

        # Conexões ativas: client_uid → (WebSocket, role)
        self._connections: Dict[str, tuple[WebSocket, str]] = {}
        # Exactly one browser owns media playback. The native Pet has priority;
        # if it is absent, the oldest regular browser becomes the fallback owner.
        self._playback_clients: Dict[str, str] = {}
        self._playback_owner_uid: str | None = None
        # Navegadores que receberam a voz e não a tocaram (lane_claim_timeout): perdem a vez para outro.
        self._playback_failed: set[str] = set()

        # Fila conversacional unica. Filas internas de TTS/transporte nao
        # representam turnos; todo ingresso social converge aqui.
        self._event_queue = ConversationEventQueue()
        self._turn_arbiter = TurnArbiter(self._event_queue)
        self._turn_consumer_claim = None

        # F2/F3 observability is lazy so a storage problem cannot delay boot.
        self.memoria: Memoria | None = None
        self.roteador: Roteador | None = None

        # Lock serializa o processamento
        self._lock = asyncio.Lock()

        # Task de processamento ativa
        self._current_task: Optional[asyncio.Task] = None

        # Flag: há processamento em andamento?
        self._processing = False
        # Browser mic and the server VoiceListener can observe the same utterance.
        # Suppress only an identical owner turn inside this short handoff window.
        self._recent_human_inputs: dict[str, float] = {}
        self._human_input_dedupe_window_s = 2.5
        # Browser ASR and VoiceListener can publish the same final transcript.
        # Deduplicate at the canonical broadcast boundary so every client and
        # the Roteiro Live view observe one utterance only.
        self._recent_transcription_events: dict[str, float] = {}
        self._transcription_dedupe_window_s = 2.5

        # External Codex playback coordination. The browser ACK is resolved by
        # routes.py while /bridge/external-turn awaits the same Future.
        self.codex_playback_lock = asyncio.Lock()
        self.codex_playback_active = False
        self._active_codex_speech_id: str | None = None
        self._codex_ack_future: asyncio.Future[str] | None = None

        # Generic playback ACK waiters used by flows that must continue only
        # after the browser has really finished (for example, game suspense).
        self._playback_waiters: dict[str, asyncio.Future[str]] = {}
        self._playback_wait_owners: dict[str, str | None] = {}
        self._playback_statuses: dict[str, tuple[str, float]] = {}

        # Cooldown de interrupção: 1ª passa sempre; próximas são bloqueadas por
        # _interrupt_block_window_s. Se a Célia falar _interrupt_reset_speak_s
        # ininterruptos depois de ser interrompida, o contador zera.
        self._last_interrupt_at: float = 0.0
        self._speak_start_at: float = 0.0
        self._interrupt_block_window_s: float = 8.0
        self._interrupt_reset_speak_s: float = 6.0

    # ─────────────────────────────────────────────────────────────────────────
    # Inicialização
    # ─────────────────────────────────────────────────────────────────────────

    def _ensure_bank_router(self, personagem: str = "celia_001") -> Roteador | None:
        """Load local F2 storage lazily; this is never a speech prerequisite."""
        try:
            if self.memoria is None:
                self.memoria = Memoria(MEMORY_STATE)
            if self.roteador is None or self.roteador.personagem != personagem:
                self.roteador = Roteador(self.memoria, personagem=personagem, modo="shadow")
            return self.roteador
        except Exception as exc:  # noqa: BLE001 - memory is observational
            observability.record_error("memory.bank_router", exc)
            logger.warning(f"Roteador de memoria indisponivel: {type(exc).__name__}")
            return None

    def observar_banco(self, entrada: str, contexto: Any = "conversa") -> Any:
        """Activate reuse only for recording; all other profiles remain shadow."""
        router = self._ensure_bank_router()
        if router is None:
            return None
        try:
            router.modo = self.bank_mode
            return router.decidir(entrada, contexto=contexto)
        except Exception as exc:  # noqa: BLE001 - memory is observational
            observability.record_error("memory.bank_router", exc)
            logger.warning(f"Decisao de memoria ignorada: {type(exc).__name__}")
            return None

    @property
    def bank_mode(self) -> str:
        config = getattr(self.context, "config", None)
        configured = getattr(config, "bank_router_mode", "shadow")
        return "ativo" if continuous_behavior.profile == "roteiro" and configured == "ativo" else "shadow"

    def registrar_turno_operacional(
        self,
        *,
        turn_id: str,
        entrada: str,
        resposta: str,
        personagem: str = "celia_001",
        modelo: str | None = None,
        tts_engine: str | None = None,
        contexto: Any = "conversa",
        fonte: str = "owner",
        decisao: Any = None,
        fallback_generation: bool | None = None,
    ) -> str | None:
        """Append the real turn log while swallowing every persistence error."""
        try:
            if self.memoria is None:
                self.memoria = Memoria(MEMORY_STATE)
            latencias = continuous_behavior.latency.estagios_do_turno(turn_id)
            regra = getattr(decisao, "regra", None) if decisao is not None else None
            resultado = getattr(decisao, "resultado", None) if decisao is not None else None
            candidatos = getattr(decisao, "candidatos", []) if decisao is not None else []
            restricoes = getattr(decisao, "restricoes", {}) if decisao is not None else {}
            confianca = getattr(decisao, "confianca", 0.0) if decisao is not None else 0.0
            razao = getattr(decisao, "razao", "") if decisao is not None else ""
            duracao = getattr(decisao, "duracao_ms", 0.0) if decisao is not None else 0.0
            ativo_id = getattr(decisao, "ativo_id", None) if decisao is not None else None
            fields = {
                "turn_id": turn_id,
                "personagem": personagem,
                "input": entrada,
                "input_normalizado": normalizar_texto(entrada),
                "resposta": resposta,
                "contexto": contexto,
                "fonte": fonte,
                "modelo": modelo,
                "tts_engine": tts_engine,
                "latencias": latencias,
                "intencao": regra.get("intencao") if isinstance(regra, dict) else None,
                "bank_resultado": resultado,
                "bank_ativo_id": ativo_id,
                "bank_candidatos": candidatos,
                "bank_regra": regra,
                "bank_confianca": confianca,
                "bank_restricoes": restricoes,
                "bank_razao": razao,
                "bank_duracao_ms": duracao,
                "bank_hit": resultado == "BANK_HIT",
                "bank_miss": resultado in {None, "BANK_MISS"},
                "bank_rejected_low_confidence": resultado == "BANK_REJECTED_LOW_CONFIDENCE",
                "bank_rejected_context": resultado == "BANK_REJECTED_CONTEXT",
                "fallback_generation": fallback_generation if fallback_generation is not None else not bool(getattr(decisao, "aplicar", False)),
            }
            rid = registrar_turno_seguro(self.memoria, **fields)
            if rid is None:
                observability.emit("memory_write", "memory.f2", "error", {"turn_id": turn_id})
            else:
                observability.emit("memory_write", "memory.f2", "ok", {"turn_id": turn_id})
            return rid
        except Exception as exc:  # noqa: BLE001 - conversation must continue
            observability.record_error("memory.f2", exc)
            logger.warning(f"Log de memoria ignorado: {type(exc).__name__}")
            return None

    async def initialize(self, context: ServiceContext) -> None:
        """
        Liga o singleton ao ServiceContext carregado do conf.yaml.
        Restaura ou cria o histórico persistente.
        Deve ser chamado UMA VEZ no startup do servidor.
        """
        self.context = context

        # Define client_uid do singleton
        self.context.client_uid = "singleton"

        # Conecta o broadcast como send_text do contexto
        self.context.send_text = self._broadcast_str

        # Reconstrói MCPClient e ToolExecutor com o send_text correto
        self._rebuild_mcp_with_broadcast()

        # Carrega persona no motor de emoções (lê conf via context)
        self.emotions.load_from_context(self.context)
        logger.info(
            f"Motor de emocoes carregado: personagem={self.emotions.character_name}, "
            f"humano={self.emotions.human_name}"
        )

        # Carrega ou cria histórico persistente
        await self._load_or_create_history()

        # Liga o monólogo interno (Plano 4 — Agente Cognitivo).
        try:
            cognitive_loop.start()
        except Exception as e:
            logger.warning(f"Cognitive loop nao iniciou: {e}")

        # Monitor de alertas críticos (RAM, GPU, ASR, serviços essenciais). Ver core/avisos.py.
        try:
            from .core import avisos

            avisos.iniciar_monitor()
        except Exception as e:
            logger.warning(f"Monitor de avisos nao iniciou: {e}")

        # Liga o rosto (Fatia 6). Sem isto, emoção publicada por evento
        # (Minecraft, interrupção, backchannel, motor de emoção) morre no
        # barramento sem chegar no navegador.
        try:
            self._iniciar_rosto()
        except Exception as e:
            logger.warning(f"Barramento de emocao nao iniciou: {e}")

        logger.info(
            f"CeliaSingleton inicializado | history_uid={self.context.history_uid}"
        )

    # ── Rosto (Fatia 6, agente: claude | 2026-08-11) ────────────────────
    def _iniciar_rosto(self) -> None:
        """Liga o barramento de emoção ao navegador.

        O barramento é síncrono de propósito (alertas do Minecraft publicam de
        uma thread própria). O transporte é assíncrono. A ponte entre os dois
        é o laço de eventos capturado aqui, uma vez, no boot.
        """
        from .core.emotion_bus import barramento

        laco = asyncio.get_running_loop()
        self._rosto_barramento = barramento
        self._rosto_ultimo_indice: int | None = None

        def ao_mudar(evento) -> None:
            # Chamado de qualquer thread. Nunca pode levantar: quem publica
            # emoção é um alerta de jogo, não um caminho crítico.
            try:
                laco.call_soon_threadsafe(
                    lambda: asyncio.create_task(self._enviar_rosto(evento))
                )
            except RuntimeError:
                pass  # laço já fechado (desligando)

        barramento.ao_mudar(ao_mudar)
        self._rosto_task = asyncio.create_task(self._vigia_rosto())
        logger.info("[Rosto] 🎭 barramento de emocao ligado ao navegador.")

    def _indice_da_emocao(self, emocao: str) -> int | None:
        modelo = getattr(self.context, "live2d_model", None)
        mapa = getattr(modelo, "emo_map", None) if modelo else None
        if not mapa:
            return None
        return self._rosto_barramento.indice_expressao(emocao, mapa)

    async def _enviar_rosto(self, evento) -> None:
        """Entrega a emoção visual e, quando necessário, troca a expressão."""
        try:
            # O Motion Director precisa da emoção nominal mesmo quando duas
            # emoções compartilham o mesmo índice de expressão (happy/funny,
            # por exemplo). Por isso este evento NÃO é deduplicado por índice.
            await self.broadcast({
                "type": "celia-emotion",
                "emotion": evento.emocao,
                "source": evento.origem,
                "ttl_sec": evento.ttl_s,
                "persistent": bool(evento.persistente),
                "metadata": dict(evento.metadados or {}),
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[Rosto] emotion broadcast falhou: {type(exc).__name__}")

        indice = self._indice_da_emocao(evento.emocao)
        if indice is None:
            return
        self._rosto_ultimo_indice = indice
        try:
            await self.broadcast({
                "type": "celia-face",
                "emotion": evento.emocao,
                "expression": indice,
                "source": evento.origem,
                "ttl_sec": evento.ttl_s,
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[Rosto] face broadcast falhou: {type(exc).__name__}")

    async def _sincronizar_rosto(self, websocket) -> None:
        """Manda o rosto atual só para quem acabou de conectar."""
        barramento = getattr(self, "_rosto_barramento", None)
        if barramento is None:
            return
        atual = barramento.valendo()
        emocao = atual.emocao if atual else "neutral"
        indice = self._indice_da_emocao(emocao)
        if indice is None:
            return
        try:
            await websocket.send_text(json.dumps({
                "type": "celia-emotion",
                "emotion": emocao,
                "source": "sincronizacao",
                "ttl_sec": atual.ttl_s if atual else 0,
                "persistent": bool(atual.persistente) if atual else False,
                "metadata": dict(atual.metadados or {}) if atual else {},
            }, ensure_ascii=False))
            await websocket.send_text(json.dumps({
                "type": "celia-face",
                "emotion": emocao,
                "expression": indice,
                "source": "sincronizacao",
                "ttl_sec": atual.ttl_s if atual else 0,
            }, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[Rosto] sincronizacao falhou: {type(exc).__name__}")

    async def _vigia_rosto(self) -> None:
        """Expire only transient reactions and restore the persistent affect."""
        while True:
            try:
                await asyncio.sleep(0.5)
                if self._rosto_barramento.expirou_agora() is None:
                    continue
                restored = self._rosto_barramento.valendo()
                if restored is not None:
                    await self._enviar_rosto(restored)
                    continue
                await self.broadcast({
                    "type": "celia-emotion",
                    "emotion": "neutral",
                    "source": "expirou",
                    "ttl_sec": 0,
                    "persistent": False,
                    "metadata": {},
                })
                indice = self._indice_da_emocao("neutral")
                if indice is None:
                    continue
                self._rosto_ultimo_indice = indice
                await self.broadcast({
                    "type": "celia-face",
                    "emotion": "neutral",
                    "expression": indice,
                    "source": "expirou",
                    "ttl_sec": 0,
                })
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[Rosto] vigia: {type(exc).__name__}")

    def _rebuild_mcp_with_broadcast(self) -> None:
        """
        Reconstrói MCPClient e ToolExecutor usando self._broadcast_str como send_text.
        Garante que status de tools chegue a todos os clientes conectados.
        """
        ctx = self.context
        if not ctx.mcp_server_registery or not ctx.tool_manager:
            # MCP não está habilitado — nada a fazer
            return

        try:
            from .mcpp.mcp_client import MCPClient
            from .mcpp.tool_executor import ToolExecutor

            # Fecha o MCPClient antigo se existir
            # (não esperamos await aqui pois é sync — o close será lazy)
            ctx.mcp_client = MCPClient(
                ctx.mcp_server_registery,
                self._broadcast_str,  # ← broadcast em vez de None
                "singleton",
            )
            ctx.tool_executor = ToolExecutor(ctx.mcp_client, ctx.tool_manager)
            logger.info("🔧 MCPClient reconstruído com broadcast para o singleton.")
        except Exception as e:
            logger.error(f"Erro ao reconstruir MCPClient: {e}")


    async def _load_or_create_history(self) -> None:
        """Restaura contexto recente ou cria um novo histórico após a janela de paciência."""
        conf_uid = self.context.character_config.conf_uid

        # 1. Restaura do singleton_state.json se ainda estiver dentro da janela de reconexão.
        saved_state = self._load_state()
        saved_uid = saved_state.get("history_uid") if saved_state else None
        saved_conf_uid = saved_state.get("conf_uid") if saved_state else None
        if saved_uid and saved_conf_uid == conf_uid and self._is_state_recent(saved_state):
            logger.info(f"📖 Histórico restaurado dentro da janela de paciência: {saved_uid}")
            self.context.history_uid = saved_uid
            self.context.agent_engine.set_memory_from_history(
                conf_uid=conf_uid,
                history_uid=saved_uid,
            )
            return

        # 2. Fallback: se o state sumiu, ainda aceitamos o histórico mais recente
        # apenas quando ele também é recente o bastante para parecer uma reconexão.
        latest = get_latest_history_uid(conf_uid)
        if latest and self._history_uid_is_recent(latest):
            logger.info(f"📖 Histórico recente encontrado para reconexão: {latest}")
            self.context.history_uid = latest
            self.context.agent_engine.set_memory_from_history(
                conf_uid=conf_uid,
                history_uid=latest,
            )
            self._save_state(latest, conf_uid)
            return

        # 3. Fora da janela de paciência: começa conversa nova.
        new_uid = create_new_history(conf_uid)
        logger.info(f"🆕 Novo histórico criado: {new_uid}")
        self.context.history_uid = new_uid
        self._save_state(new_uid, conf_uid)

    def _load_state(self) -> Optional[dict]:
        """Lê singleton_state.json e retorna o estado salvo (ou None)."""
        if not os.path.exists(SINGLETON_STATE_FILE):
            return None
        try:
            with open(SINGLETON_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return None
            return data
        except Exception as e:
            logger.warning(f"Não foi possível ler singleton_state.json: {e}")
            return None

    def _is_state_recent(self, state: Optional[dict]) -> bool:
        if not isinstance(state, dict):
            return False

        saved_at = state.get("saved_at")
        if saved_at and self._iso_timestamp_is_recent(saved_at):
            return True

        history_uid = state.get("history_uid")
        return self._history_uid_is_recent(history_uid)

    @staticmethod
    def _iso_timestamp_is_recent(timestamp_str: str, max_age_sec: float = SINGLETON_CONTEXT_PATIENCE_SEC) -> bool:
        if not timestamp_str:
            return False
        try:
            normalized = timestamp_str.replace("Z", "+00:00")
            saved_at = datetime.fromisoformat(normalized)
            if saved_at.tzinfo is None:
                saved_at = saved_at.replace(tzinfo=timezone.utc)
            age_sec = (datetime.now(timezone.utc) - saved_at.astimezone(timezone.utc)).total_seconds()
            return 0 <= age_sec <= max_age_sec
        except Exception:
            return False

    @staticmethod
    def _history_uid_is_recent(history_uid: Optional[str], max_age_sec: float = SINGLETON_CONTEXT_PATIENCE_SEC) -> bool:
        if not history_uid:
            return False
        try:
            parts = str(history_uid).split("_", 2)
            if len(parts) < 2:
                return False
            created_at = datetime.strptime(
                f"{parts[0]}_{parts[1]}",
                "%Y-%m-%d_%H-%M-%S",
            ).replace(tzinfo=timezone.utc)
            age_sec = (datetime.now(timezone.utc) - created_at).total_seconds()
            return 0 <= age_sec <= max_age_sec
        except Exception:
            return False

    def _save_state(self, history_uid: str, conf_uid: str) -> None:
        """Grava singleton_state.json com o history_uid atual (escrita atomica).

        Antes usava open(w) direto: se o arquivo estivesse aberto/locked (outro
        processo lendo, antivirus, sync de pasta) dava 'Permission denied' e a
        Celia esquecia a conversa ao reiniciar. Agora grava num .tmp e troca via
        os.replace (atomico) com retry curto para o PermissionError do Windows.
        """
        payload = {
            "history_uid": history_uid,
            "conf_uid": conf_uid,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp = f"{SINGLETON_STATE_FILE}.{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            for attempt in range(3):
                try:
                    os.replace(tmp, SINGLETON_STATE_FILE)
                    break
                except PermissionError:
                    if attempt == 2:
                        raise
                    time.sleep(0.1)
        except Exception as e:
            logger.error(f"Erro ao salvar singleton_state.json: {e}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    def _touch_state(self) -> None:
        ctx = self.context
        if not ctx or not ctx.history_uid:
            return
        self._save_state(ctx.history_uid, ctx.character_config.conf_uid)

    # ─────────────────────────────────────────────────────────────────────────
    # Gerenciamento de conexões
    # ─────────────────────────────────────────────────────────────────────────

    def _select_playback_owner(self) -> str | None:
        current = self._playback_owner_uid
        pets = [
            uid for uid, kind in self._playback_clients.items()
            if kind == "pet" and uid in self._connections
        ]
        browsers = [
            uid for uid, kind in self._playback_clients.items()
            if kind == "browser" and uid in self._connections
        ]
        scenes = [
            uid for uid, kind in self._playback_clients.items()
            if kind == "scene" and uid in self._connections
        ]
        if scenes and live_gate.live_active():
            # Modo Live ligado (ordem do Pai, 28/10): só a cena da bancada toca a voz; a interface e o Pet não.
            selected = scenes[-1]   # a cena aberta/recarregada por último; uma cena velha esquecida não fica com a voz
        elif current in pets:
            selected = current
        elif pets:
            selected = pets[-1]
        elif current in browsers:
            selected = current
        elif browsers:
            # O mais recente: com o mais antigo, recarregar a página que tocava passava a voz para
            # uma aba velha que não toca (28/10: 40 falas canceladas por lane_claim_timeout).
            selected = browsers[-1]
        elif scenes:
            selected = scenes[-1]
        else:
            selected = None
        if selected != current:
            logger.info(
                f"playback-owner changed: {current or 'none'} -> {selected or 'none'}"
            )
            self._playback_owner_uid = selected
        return self._playback_owner_uid

    def report_playback_failure(self, client_uid: str) -> None:
        """O dono recebeu a voz e não começou a tocar: passa a voz para outro navegador.

        Prefere quem ainda não falhou (o mais recente); se todos falharam, gira para o próximo
        para que cada página tenha nova chance. O Pet nunca é rebaixado por aqui.
        """
        if not client_uid or self._playback_owner_uid != client_uid:
            return
        if self._playback_clients.get(client_uid) != "browser":
            return
        self._playback_failed.add(client_uid)
        others = [
            uid for uid, kind in self._playback_clients.items()
            if kind == "browser" and uid in self._connections and uid != client_uid
        ]
        if not others:
            return
        fresh = [uid for uid in others if uid not in self._playback_failed]
        selected = fresh[-1] if fresh else others[0]
        logger.info(f"playback-owner changed: {client_uid} -> {selected} (dono não tocou a voz)")
        self._playback_owner_uid = selected

    def report_playback_started(self, client_uid: str) -> None:
        self._playback_failed.discard(client_uid)

    def is_playback_owner(self, client_uid: str) -> bool:
        return bool(client_uid) and self._select_playback_owner() == client_uid

    def stop_playback_for_owner(self, client_uid: str) -> list[str]:
        """Synchronously arbitrate STOP from the server-selected playback owner."""
        if not self.is_playback_owner(client_uid):
            return []
        from .core.speech_coordinator import speech_coordinator

        return speech_coordinator.owner_stop()

    @property
    def playback_owner_uid(self) -> str | None:
        return self._select_playback_owner()

    async def register(
        self,
        client_uid: str,
        websocket: WebSocket,
        role: str,
        playback_client: str = "browser",
    ) -> None:
        """
        Registra nova conexão WebSocket.
        role = 'browser' (pode interromper) ou 'source' (só envia inputs)
        """
        self._connections[client_uid] = (websocket, role)
        if role == "browser":
            self._playback_clients[client_uid] = (
                playback_client if playback_client in ("pet", "scene") else "browser"
            )
            current = self._playback_owner_uid
            if self._playback_clients[client_uid] == "browser" and self._playback_clients.get(current) not in ("pet", "scene"):
                # A página aberta/recarregada agora é a que o dono está olhando: ela toca a voz.
                # O Pet continua com prioridade (aqui e em _select_playback_owner).
                if current != client_uid:
                    logger.info(f"playback-owner changed: {current or 'none'} -> {client_uid} (navegador mais recente)")
                self._playback_owner_uid = client_uid
        else:
            self._playback_clients[client_uid] = "source"
        self._select_playback_owner()
        self._touch_state()
        logger.info(
            f"Conexão registrada: {client_uid} role={role} "
            f"playback={self._playback_clients[client_uid]} "
            f"owner={self._playback_owner_uid or 'none'} "
            f"| total={len(self._connections)}"
        )
        # 2026-08-11 (claude, Fatia 6): quem chega agora recebe o rosto que
        # está valendo. Sem isto, o servidor deduplica por índice e um
        # navegador recém-aberto fica com a expressão padrão enquanto ela já
        # está brava — bug encontrado testando, não em revisão de código.
        await self._sincronizar_rosto(websocket)

    async def unregister(self, client_uid: str) -> None:
        """
        Remove conexão. NÃO limpa histórico — a conversa continua existindo.
        """
        # Release only waits owned by the player that actually disconnected.
        # Source sockets and non-owner browser tabs must not end another player's
        # playback wait.
        owned_waiters = [
            speech_id for speech_id, owner_uid in self._playback_wait_owners.items()
            if owner_uid == client_uid
        ]
        for speech_id in owned_waiters:
            self.resolve_playback_wait(speech_id, "disconnected")
            from .core.speech_coordinator import speech_coordinator

            speech_coordinator.mark_finished(speech_id, "disconnected")
        self._connections.pop(client_uid, None)
        self._playback_clients.pop(client_uid, None)
        self._playback_failed.discard(client_uid)
        if self._playback_owner_uid == client_uid:
            self._playback_owner_uid = None
        self._select_playback_owner()
        logger.info(
            f"🔌 Conexão removida: {client_uid} "
            f"| restantes={len(self._connections)}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Broadcast
    # ─────────────────────────────────────────────────────────────────────────

    async def broadcast(self, message: dict) -> None:
        """Envia um dict JSON para TODAS as conexões abertas."""
        if self._is_duplicate_transcription(message):
            return
        text = json.dumps(message, ensure_ascii=False)
        await self._broadcast_str(text)

    def _is_duplicate_transcription(self, message: dict) -> bool:
        """Suppress duplicate final ASR events without touching partials."""
        if not isinstance(message, dict) or message.get("type") != "user-input-transcription":
            return False
        text = " ".join(str(message.get("text") or "").casefold().split())
        if not text:
            return False
        now = time.monotonic()
        utterance_id = " ".join(str(message.get("utterance_id") or "").split())
        source = " ".join(str(message.get("source") or "").casefold().split())
        if not utterance_id and source not in {"microphone", "owner", "browser"}:
            # Chat/proxy broadcasts without provenance are not assumed to be
            # the owner's microphone and must remain independently visible.
            return False
        keys = [f"text:{text}"]
        if utterance_id:
            # Keep the text key as well: the server-side VoiceListener does not
            # have the browser utterance id but can still echo the same words.
            keys.append(f"utterance:{utterance_id}")
        recent = self._recent_transcription_events
        cutoff = now - self._transcription_dedupe_window_s
        duplicate = any(recent.get(key, 0.0) >= cutoff for key in keys)
        for key in keys:
            recent[key] = now
        self._recent_transcription_events = {
            key: timestamp for key, timestamp in recent.items() if timestamp >= cutoff
        }
        if duplicate:
            logger.info(f"Duplicate transcription suppressed: {text[:80]}")
        return duplicate

    async def _broadcast_str(self, text: str) -> None:
        """Broadcast state to all clients, but media to one owner only."""
        payload = None
        try:
            payload = json.loads(text)
        except Exception:
            payload = None

        # O painel de roteiro observa o mesmo caminho usado por áudio e texto;
        # `_broadcast_str` é o ponto comum entre `broadcast()` e o TTS manager.
        try:
            from .core.roteiro_live import roteiro_live

            if isinstance(payload, dict):
                await roteiro_live.observe_message(payload)
        except Exception as exc:  # O teleprompter nunca pode quebrar a conversa.
            logger.debug("Roteiro Live ignorado: %s", type(exc).__name__)

        owner_uid = self.playback_owner_uid
        observer_text = None
        is_media = (
            isinstance(payload, dict)
            and payload.get("type") == "audio"
            and bool(payload.get("audio"))
        )
        if is_media and owner_uid:
            observer_payload = dict(payload)
            observer_payload["audio"] = None
            observer_payload["volumes"] = []
            observer_payload["playback_observer"] = True
            observer_text = json.dumps(observer_payload, ensure_ascii=False)

        dead: list[str] = []
        for uid, (ws, _role) in list(self._connections.items()):
            try:
                outbound = (
                    observer_text
                    if is_media and owner_uid and uid != owner_uid
                    else text
                )
                # Timeout curto no send_text evita que uma conexão lenta/zombie
                # segure o broadcast para todos os outros clientes
                await asyncio.wait_for(ws.send_text(outbound), timeout=2.0)
            except Exception:
                dead.append(uid)
        for uid in dead:
            self._connections.pop(uid, None)
            self._playback_clients.pop(uid, None)
            if self._playback_owner_uid == uid:
                self._playback_owner_uid = None
        if dead:
            self._select_playback_owner()

    # ─────────────────────────────────────────────────────────────────────────
    # Input de texto — sistema de lotes
    # ─────────────────────────────────────────────────────────────────────────

    async def submit_input(
        self,
        text: str,
        role: str = "source",
        speaker_name: str | None = None,
        origin: str = "owner",
        guardian_evidence: GuardianEvidence | None = None,
        behavior_observed: bool = False,
        turn_id: str | None = None,
        scripted_reply: dict[str, str] | None = None,
        asr_refinement: dict[str, object] | None = None,
    ) -> bool:
        """
        Recebe um input de qualquer fonte.

        Comportamento:
        - Se Célia está livre: processa imediatamente (cria lote de 1)
        - Se Célia está processando: acumula no lote atual (até BATCH_SIZE)
          Quando lote atinge BATCH_SIZE, inicia próximo lote automaticamente
        """
        if not text or not text.strip():
            return False

        text = text.strip()
        speaker_name = (speaker_name or "").strip()
        origin = (origin or "owner").strip().lower() or "owner"
        if not live_gate.submit_input_allowed(origin):  # modo Live ativo: so o chat da live conversa
            logger.info("[Live] submit_input recusado (origin={}): modo Live ativo", origin)
            return False
        begin_turn = getattr(continuous_behavior, "begin_turn", None)
        turn_id = (
            begin_turn(str(turn_id or ""))
            if callable(begin_turn)
            else str(turn_id or "")
        )
        external_sources = {
            "chat": "youtube_live_chat",
            "discord": "discord",
            "external_turn": "external_turn",
        }
        if origin in {"owner", "microphone"}:
            fingerprint = " ".join(text.casefold().split())
            now = time.monotonic()
            recent_inputs = getattr(self, "_recent_human_inputs", None)
            dedupe_window = getattr(self, "_human_input_dedupe_window_s", 2.5)
            if recent_inputs is not None:
                previous = recent_inputs.get(fingerprint)
                if previous is not None and now - previous < dedupe_window:
                    logger.info(
                        f"Duplicate owner input suppressed within {dedupe_window:.1f}s: {text[:80]}"
                    )
                    return False
                recent_inputs[fingerprint] = now
                cutoff = now - dedupe_window
                self._recent_human_inputs = {
                    key: timestamp
                    for key, timestamp in recent_inputs.items()
                    if timestamp >= cutoff
                }
        if origin in {"owner", "microphone"} and scripted_reply is None:
            try:
                from .games_routes import request_explicit_game_play

                game_play = request_explicit_game_play(text)
            except Exception as exc:
                logger.warning("Explicit game play routing failed: {}", type(exc).__name__)
                game_play = None
            if game_play is not None:
                try:
                    await self.broadcast({
                        "type": "game-control-status",
                        "turn_id": turn_id,
                        **game_play,
                    })
                except Exception:
                    pass
                if game_play.get("status") == "queued":
                    logger.info(
                        "Explicit game play command queued once: game={} match={}",
                        game_play.get("game"),
                        game_play.get("match_id"),
                    )
                    return True
                reply = str(game_play.get("reply") or "").strip()
                if reply:
                    scripted_reply = {
                        "text": reply,
                        "tts_text": reply,
                        "trusted_local": True,
                        "reason": f"game_play:{game_play.get('status')}",
                    }

        if origin in external_sources:
            source = external_sources[origin]
            if not evidence_matches(guardian_evidence, text, source):
                guardian_evidence = await evaluate_text_async(
                    text, speaker_name, new_context(source, external=True)
                )
            observability.emit(
                "guardian_checked", "guardiao", guardian_evidence.decision,
                {"source": source, "guardian_verdict": guardian_evidence.decision,
                 "guardian_category": guardian_evidence.category,
                 "policy_mode": guardian_evidence.context.policy_mode,
                 "request_id": guardian_evidence.context.request_id},
            )
            if guardian_evidence.decision == "blocked":
                logger.warning("External ingress blocked before singleton processing: %s", source)
                return False
        local_owner_context = None
        if origin in {"owner", "microphone"} and guardian_evidence is None:
            local_owner_context = new_context(
                "local_owner",
                external=False,
                trusted_user_id=speaker_name or None,
                authenticated=True,
                authorization_scope=(
                    "owner.full", "image.generate", "discord.write", "memory.write",
                    "overlay.write", "poll.write", "minecraft.write", "vision.write",
                ),
            )
            from .core.tool_authorization import bind_direct_browser_command, bind_direct_folder_command
            local_owner_context = bind_direct_browser_command(local_owner_context, text)
            local_owner_context = bind_direct_folder_command(local_owner_context, text)
        entry = {
            "text": text,
            "speaker_name": speaker_name,
            "origin": origin,
            "turn_id": turn_id,
            "trusted_context": guardian_evidence.context if guardian_evidence else local_owner_context,
            "scripted_reply": dict(scripted_reply) if isinstance(scripted_reply, dict) else None,
            "asr_refinement": dict(asr_refinement) if isinstance(asr_refinement, dict) else None,
        }
        # The behavior layer only keeps bounded, in-RAM context and safe metrics.
        # It never bypasses the Guardian decision above or creates another reply path.
        if not behavior_observed:
            behavior_source = {
                "chat": "youtube_chat",
                "discord": "discord",
                "external_turn": "external_turn",
                "owner": "browser",
            }.get(origin, origin if origin in {"microphone", "game", "tool", "system"} else "untrusted_source")
            speaker_role = {
                "chat": "audience",
                "discord": "participant",
                "external_turn": "external_participant",
                "owner": "host",
                "microphone": "host",
                "game": "system",
                "tool": "system",
            }.get(origin, "unknown")
            continuous_behavior.observe(
                "speech.final",
                source=behavior_source,
                speaker=speaker_name,
                speaker_role=speaker_role,
                speaker_confidence=1.0 if origin in {"owner", "microphone"} else 0.0,
                text=text,
                utterance_id=turn_id,
            )
        self._touch_state()

        # Alimenta o monólogo interno com o buffer do chat (Plano 4).
        if origin == "chat":
            try:
                cognitive_loop.register_chat(speaker_name, text)
            except Exception:
                pass

        priority = {"microphone": 95, "owner": 90, "external_turn": 85, "game": 80, "tool": 70, "discord": 55, "chat": 45, "system": 35}.get(origin, 50)
        ttl_sec = {"system": 12.0, "game": 20.0, "chat": 60.0, "discord": 60.0, "tool": 60.0, "external_turn": 90.0}.get(origin)
        snapshot_fn = getattr(continuous_behavior, "safe_snapshot", None)
        behavior_snapshot = snapshot_fn() if callable(snapshot_fn) else {}
        revision = int(behavior_snapshot.get("conversation_revision") or 0)
        queued_event = self._event_queue.put(
            entry,
            source=origin,
            speaker=speaker_name,
            priority=priority,
            ttl_sec=ttl_sec,
            conversation_revision=revision,
        )
        entry["event_id"] = queued_event.event_id
        logger.info(
            f"📥 evento enfileirado source={origin} priority={priority} "
            f"queue={self._event_queue.qsize()} event={queued_event.event_id[:8]}"
        )
        observability.emit(
            "conversation_event_enqueued", "conversation.queue", "queued",
            {"source": origin, "turn_id": turn_id, "event_id": queued_event.event_id,
             "queue_size": self._event_queue.qsize(), "priority": priority},
        )
        if not self._processing:
            await self._trigger_processing()

        return True

    def clear_pending_inputs(self) -> None:
        """Discard queued inputs without touching conversation memory."""
        cleared = self._event_queue.clear()
        logger.info(f"🧹 fila conversacional limpa: {cleared} evento(s)")

    def begin_codex_playback(self, speech_id: str) -> asyncio.Future[str]:
        """Start an external Codex turn and return the browser ACK Future."""
        if not speech_id:
            raise ValueError("speech_id is required")
        if self.codex_playback_active:
            raise RuntimeError("Codex playback is already active")

        loop = asyncio.get_running_loop()
        self.codex_playback_active = True
        self._active_codex_speech_id = speech_id
        self._codex_ack_future = loop.create_future()
        logger.info(f"Codex playback hold started: speech_id={speech_id}")
        return self._codex_ack_future

    def resolve_codex_playback_ack(self, speech_id: str, status: str) -> bool:
        """Resolve the active external playback ACK once, ignoring stale ACKs."""
        future = self._codex_ack_future
        if (
            not self.codex_playback_active
            or not speech_id
            or speech_id != self._active_codex_speech_id
            or future is None
            or future.done()
        ):
            logger.debug(f"Ignoring stale Codex playback ACK: speech_id={speech_id}")
            return False

        future.set_result((status or "ended").strip() or "ended")
        logger.info(f"Codex playback ACK resolved: speech_id={speech_id} status={status}")
        return True

    def end_codex_playback(self, speech_id: str) -> None:
        """Invalidate the active speech ID and release the external playback hold."""
        if speech_id != self._active_codex_speech_id:
            return

        future = self._codex_ack_future
        if future is not None and not future.done():
            future.cancel()
        self._codex_ack_future = None
        self._active_codex_speech_id = None
        self.codex_playback_active = False
        logger.info(f"Codex playback hold ended: speech_id={speech_id}")

    def set_playback_status(self, speech_id: str, status: str) -> None:
        """Record bounded public lifecycle state for one browser speech id."""
        speech_id = str(speech_id or "").strip()
        if not speech_id:
            return
        normalized = str(status or "pending").strip().lower() or "pending"
        previous = self._playback_statuses.get(speech_id)
        terminal = {
            "ended", "finished", "complete", "completed", "ok", "success",
            "cancelled", "canceled", "disconnected", "error", "timeout",
        }
        if previous and previous[0] in terminal:
            return
        self._playback_statuses[speech_id] = (normalized, time.monotonic())
        cutoff = time.monotonic() - 180.0
        if len(self._playback_statuses) > 256:
            self._playback_statuses = {
                key: value
                for key, value in self._playback_statuses.items()
                if value[1] >= cutoff
            }
            while len(self._playback_statuses) > 256:
                self._playback_statuses.pop(next(iter(self._playback_statuses)))

    def playback_status(self, speech_id: str) -> str | None:
        speech_id = str(speech_id or "").strip()
        value = self._playback_statuses.get(speech_id)
        return value[0] if value else None

    def begin_playback_wait(self, speech_id: str) -> asyncio.Future[str]:
        """Register a one-shot browser playback waiter for one speech id."""
        speech_id = str(speech_id or "").strip()
        if not speech_id:
            raise ValueError("speech_id is required")
        previous = self._playback_waiters.pop(speech_id, None)
        if previous is not None and not previous.done():
            previous.cancel()
        self._playback_wait_owners.pop(speech_id, None)
        self.set_playback_status(speech_id, "pending")
        future = asyncio.get_running_loop().create_future()
        self._playback_waiters[speech_id] = future
        self._playback_wait_owners[speech_id] = self.playback_owner_uid
        return future

    def resolve_playback_wait(self, speech_id: str, status: str) -> bool:
        """Record lifecycle state and resolve a generic waiter once."""
        speech_id = str(speech_id or "").strip()
        if not speech_id:
            return False
        normalized = (status or "ended").strip().lower() or "ended"
        future = self._playback_waiters.get(speech_id)
        current = self.playback_status(speech_id)
        terminal = {
            "ended", "finished", "complete", "completed", "ok", "success",
            "cancelled", "canceled", "disconnected", "error", "timeout",
        }
        if (future is not None and future.done()) or current in terminal:
            return False
        self.set_playback_status(speech_id, normalized)
        if future is None:
            return True
        future.set_result(normalized)
        return True

    def end_playback_wait(self, speech_id: str) -> None:
        """Remove a waiter after success, timeout or cancellation."""
        speech_id = str(speech_id or "").strip()
        future = self._playback_waiters.pop(speech_id, None)
        self._playback_wait_owners.pop(speech_id, None)
        if future is not None and not future.done():
            future.cancel()

    async def _trigger_processing(self) -> None:
        """Inicia o loop de processamento de lotes se não estiver rodando."""
        if self._processing:
            return
        # Marca como processando SÍNCRONAMENTE para fechar a race condition
        self._processing = True
        self._speak_start_at = time.monotonic()  # cooldown de interrupcao
        self._current_task = asyncio.create_task(self._process_batches())

    async def _process_batches(self) -> None:
        """
        Consome os lotes sequencialmente.
        - Gera prompt emocional para guiar o LLM
        - Envia fallback de expressão para garantir reação visual mesmo sem tag
        """
        try:
            if self._turn_consumer_claim is None:
                self._turn_consumer_claim = self._turn_arbiter.claim_consumer()
            while self._event_queue.qsize():
                queued_group = await self._event_queue.get_group(
                    BATCH_SIZE, consumer_token=self._turn_consumer_claim
                )
                queue_event_ids = [event.event_id for event in queued_group]
                batch = [event.payload for event in queued_group]
                logger.info(
                    f"📤 consumindo {len(batch)} evento(s) da fila "
                    f"restantes={self._event_queue.qsize()}"
                )
                executor = (
                    self._turn_arbiter.executor_for(queue_event_ids[0])
                    if len(queue_event_ids) == 1 else None
                )
                if executor is not None:
                    try:
                        result = await self._turn_arbiter.execute_claimed(queue_event_ids[0])
                    except asyncio.CancelledError:
                        self._turn_arbiter.complete_queue_events(
                            queue_event_ids, {"status": "cancelled"}
                        )
                    except Exception as exc:
                        self._turn_arbiter.complete_queue_events(queue_event_ids, error=exc)
                        logger.warning("Bridge process turn failed: {}", type(exc).__name__)
                    else:
                        self._turn_arbiter.complete_queue_events(queue_event_ids, result)
                    continue
                merged = self._merge_batch(batch)
                batch_speaker = self._speaker_from_batch(batch)
                batch_origin = self._origin_from_batch(batch)
                batch_turn_id = self._turn_id_from_batch(batch)
                trusted_context = self._trusted_context_from_batch(batch)
                scripted_reply = self._scripted_reply_from_batch(batch)
                asr_refinement = self._asr_refinement_from_batch(batch)
                current_speaker = (
                    batch_speaker
                    or self.context.character_config.human_name
                    if self.context
                    else ""
                )

                # ── 1. Detecta emoção (com anti-repetição) ───────────────────
                emotion_prompt = self.emotions.process_input(merged)
                emotion_prompt = emotion_prompt.replace(
                    "Inicios possiveis: Hmm..., Deixa eu ver..., Peraí..., Como assim?",
                    "Inicios possiveis: Olha, Mano, Cara, Entendi",
                ).replace(
                    "Inicios possiveis: Hmm..., Deixa eu ver..., Peraí..., Como assim?",
                    "Inicios possiveis: Olha, Mano, Cara, Entendi",
                ).replace(
                    "Estilo de resposta: questionadora, pensativa, tentando entender",
                    "Estilo de resposta: opinativa, direta, tentando conectar as ideias sem ficar interrogando",
                ).replace(
                    "Pontuacao preferida: ?",
                    "Pontuacao preferida: .",
                ).replace(
                    "Inicios possiveis: Ah é?, Claro..., Sei..., Tipo assim,",
                    "Inicios possiveis: Olha, Cara, Sei, Tipo assim,",
                ).replace(
                    "Inicios possiveis: Ah é?, Claro..., Sei..., Tipo assim,",
                    "Inicios possiveis: Olha, Cara, Sei, Tipo assim,",
                )
                # Recording: an action/statement asks for a short reaction, not a
                # 15-50 word invented reply (decision 4, 2026-10-02).
                emotion_prompt = shape_emotion_prompt(
                    emotion_prompt, merged, continuous_behavior.profile,
                    self._origin_from_batch(batch),
                )
                if current_speaker:
                    emotion_prompt = emotion_prompt.replace(
                        "Chame o usuario de Criador",
                        f"Chame o usuario de {current_speaker}",
                    )
                emotion_state = self.emotions.state
                logger.info(
                    f"Emocao detectada: {emotion_state.emotion.upper()} "
                    f"(confianca: {emotion_state.confidence:.0%}) "
                    f"| lote {len(batch)} input(s)"
                )

                # Mapeia emoção interna para tag/indice Live2D de fallback
                emotion_live2d_tag = self.emotions.get_live2d_tag(
                    emotion_state.emotion
                )
                fallback_expression = None
                if self.context and self.context.live2d_model is not None:
                    fallback_expression = self.context.live2d_model.emo_map.get(
                        emotion_live2d_tag
                    )

                # ── 2. Broadcast da emoção para monitoramento externo ─────────
                # (tipo "emotion-state" — não é TTS'd, scripts externos podem ler)
                await self.broadcast({
                    "type": "emotion-state",
                    "emotion": emotion_state.emotion,
                    "live2d_tag": emotion_live2d_tag,
                    "confidence": round(emotion_state.confidence, 2),
                    "intensity": emotion_state.intensity,
                    "fallback_expression": fallback_expression,
                })

                # Sem prefixar texto no websocket: expressao vai via actions/fallback.

                logger.info(
                    f"Processando: {merged[:80]}{'...' if len(merged) > 80 else ''}"
                )

                try:
                    await self.broadcast({"type": "celia.state", "state": "pensando"})
                    async with self._lock:
                        await process_single_conversation(
                            context=self.context,
                            websocket_send=self._broadcast_str,
                            client_uid="singleton",
                            user_input=merged,
                            images=None,
                            session_emoji=np.random.choice(EMOJI_LIST),
                            metadata={
                                "emotion_prompt": emotion_prompt,
                                "fallback_expression": fallback_expression,
                                "fallback_live2d_tag": emotion_live2d_tag,
                                "speaker_name": batch_speaker
                                or self.context.character_config.human_name,
                                "origin": batch_origin,
                                "proactive_speak": batch_origin == "system",
                                "skip_memory": batch_origin == "system",
                                "skip_history": batch_origin == "system",
                                "trusted_context": trusted_context,
                                "turn_id": batch_turn_id,
                                "scripted_reply": scripted_reply,
                                "asr_refinement": asr_refinement,
                            },
                        )
                    self._turn_arbiter.complete_queue_events(queue_event_ids, {"status": "completed"})
                    # Decision 3 (2026-10-02): after a direct-address reply has been
                    # generated (TTS included), start the next screen analysis.
                    try:
                        from .core.screen_vision import screen_vision as _sv
                        _sv.refresh_if_requested()
                    except Exception as exc:
                        logger.debug(f"screen refresh after reply unavailable: {type(exc).__name__}")
                except asyncio.CancelledError:
                    self._turn_arbiter.complete_queue_events(queue_event_ids, {"status": "cancelled"})
                    logger.info("Lote interrompido pelo browser")
                    self._event_queue.clear()
                    raise
                except Exception as e:
                    self._turn_arbiter.complete_queue_events(queue_event_ids, error=e)
                    logger.error(f"Erro ao processar lote: {e}")

        finally:
            self._processing = False
            self._current_task = None
            # A candidate may have become ready while this turn was speaking.
            # Retry only after the canonical conversation path is fully idle.
            try:
                asyncio.get_running_loop().create_task(
                    cognitive_loop.try_dispatch_pending()
                )
            except Exception as exc:
                logger.debug(f"cognitive pending dispatch unavailable: {type(exc).__name__}")


    def _entry_text(self, entry) -> str:
        if isinstance(entry, dict):
            return str(entry.get("text") or "")
        return str(entry or "")

    def _speaker_from_batch(self, batch: list) -> str:
        for entry in batch:
            if isinstance(entry, dict):
                speaker = str(entry.get("speaker_name") or "").strip()
                if speaker:
                    # Fix: strip @ do handle do YouTube (ex: @pedrovidal -> pedrovidal)
                    return speaker.lstrip("@").strip() or ""
        return ""

    def _origin_from_batch(self, batch: list) -> str:
        """Origem mais restritiva do lote: se QUALQUER input veio do chat, o lote e 'chat'."""
        origins = {
            str(entry.get("origin") or "").strip().lower()
            for entry in batch
            if isinstance(entry, dict)
        }
        if origins == {"system"}:
            return "system"
        for entry in batch:
            if isinstance(entry, dict):
                if str(entry.get("origin") or "").strip().lower() == "chat":
                    return "chat"
        return "owner"

    def _turn_id_from_batch(self, batch: list) -> str:
        for entry in batch:
            if isinstance(entry, dict):
                value = str(entry.get("turn_id") or "").strip()
                if value:
                    return value
        return continuous_behavior.begin_turn()

    def _scripted_reply_from_batch(self, batch: list) -> dict[str, str] | None:
        """A deterministic recording reply is valid only for its one triggering turn."""
        if len(batch) != 1 or not isinstance(batch[0], dict):
            return None
        reply = batch[0].get("scripted_reply")
        return dict(reply) if isinstance(reply, dict) and reply.get("text") else None

    def _asr_refinement_from_batch(self, batch: list) -> dict[str, object] | None:
        """Propaga incerteza ASR só quando ela pertence a um único turno de microfone."""
        if len(batch) != 1 or not isinstance(batch[0], dict):
            return None
        refinement = batch[0].get("asr_refinement")
        return dict(refinement) if isinstance(refinement, dict) else None

    def _trusted_context_from_batch(self, batch: list):
        """Only propagate one infrastructure-created context for a homogeneous batch."""
        contexts = [entry.get("trusted_context") for entry in batch if isinstance(entry, dict)]
        contexts = [context for context in contexts if context is not None]
        if len(contexts) == 1 and len(batch) == 1:
            return contexts[0]
        return None

    def _merge_batch(self, batch: list) -> str:
        """Funde uma lista de inputs em 1 texto coerente, removendo cópias exatas."""
        unique_batch = []
        for entry in batch:
            text = self._entry_text(entry).strip()
            if not text:
                continue
            if text not in unique_batch:
                unique_batch.append(text)
        if len(unique_batch) == 1:
            return unique_batch[0]
        # Separa os inputs com quebra de linha para contexto claro
        return "\n".join(unique_batch)

    # ─────────────────────────────────────────────────────────────────────────
    # Interrupção (apenas browser)
    # ─────────────────────────────────────────────────────────────────────────

    async def interrupt(self, heard_response: str = "", role: str = "source", force: bool = False) -> bool:
        """
        Interrompe o processamento atual.
        Apenas role='browser' pode interromper.

        Returns:
            True se interrompeu, False se role não permitido ou nada estava rodando.
        """
        if role != "browser":
            logger.debug(f"Interrupção ignorada: role={role} não tem permissão")
            return False

        # POLÍTICA DE INTERRUPÇÃO (Criador, jun/2026): a Célia só é cortada por:
        #   1) voz "calma celia"/"calada" (voice_listener → force=True)
        #   2) hotkey/comando Ctrl+P (force=True)
        #   3) eventos urgentes do jogo (interrupt_for_external_playback → force=True)
        #   4) Codex (force=True)
        # O VAD automático do MIC DO BROWSER manda interrupt SEM force a cada som —
        # cortava a fala dela toda hora (20x/sessão no log). BLOQUEAR esses.
        # Override por env caso queira religar o auto-interrupt do browser.
        if not force and os.environ.get("CELIA_ALLOW_BROWSER_AUTO_INTERRUPT", "").strip().lower() not in {"1", "true", "yes", "on", "sim"}:
            logger.info(f"🚫 Interrupção do browser BLOQUEADA (auto-VAD, sem force). heard={heard_response[:30]!r}")
            return False

        # Cooldown de interrupcao: 1a passa sempre; proximas bloqueadas por
        # _interrupt_block_window_s. Se a Celia falar _interrupt_reset_speak_s
        # sem ser interrompida, o cooldown zera. Codex passa com force=True.
        if not force and role == "browser":
            now = time.monotonic()
            if self._last_interrupt_at and self._speak_start_at:
                if (now - self._speak_start_at) >= self._interrupt_reset_speak_s:
                    self._last_interrupt_at = 0.0
            if self._last_interrupt_at and (now - self._last_interrupt_at) < self._interrupt_block_window_s:
                remaining = self._interrupt_block_window_s - (now - self._last_interrupt_at)
                logger.info(f"Interrupcao bloqueada ({remaining:.1f}s restantes do cooldown)")
                try:
                    await self.broadcast({"type": "interrupt-blocked", "remaining_s": remaining})
                except Exception:
                    pass
                return False
            self._last_interrupt_at = now

        if not self._processing or not self._current_task:
            logger.debug("Nada sendo processado para interromper")
            return False

        # heard_response is a legacy parameter name. In the browser ASR path
        # it is the NEW HUMAN speech, not Celia's heard response.
        new_user_speech = str(heard_response or "")
        played_response = ""
        try:
            played_response = continuous_behavior.played_speech_text()
        except Exception:
            played_response = ""

        # Descarta fila conversacional antes de cancelar.
        self._event_queue.clear()

        # Cancela a task ativa. O receiver aguarda o cleanup antes de ler o
        # proximo text-input, para que o novo lote nao seja limpo pelo finally
        # da conversa interrompida.
        active_task = self._current_task
        active_task.cancel()
        logger.info(
            "Browser interrupt: new_user={!r} played_assistant={!r}",
            new_user_speech[:50],
            played_response[:80],
        )

        # Memory receives only the assistant fragment that was actually already
        # playing. The human transcript continues through the normal ASR turn.
        try:
            if self.context and self.context.agent_engine:
                self.context.agent_engine.handle_interrupt(played_response)
        except Exception as e:
            logger.error(f"Erro no handle_interrupt do agent: {e}")

        # Broadcast da interrupção para todos os clientes
        await self.broadcast({"type": "interrupt-signal", "text": "conversation-interrupted"})
        await asyncio.gather(active_task, return_exceptions=True)

        # Adaptação de ritmo (Plano 4): interrupção real = "momento de foco".
        try:
            cognitive_loop.note_interrupt()
        except Exception:
            pass

        return True

    async def interrupt_for_external_playback(self) -> None:
        """Interrupt the current turn and wait until its cleanup has completed."""
        active_task = self._current_task
        await self.interrupt(role="browser", force=True)
        if active_task is not None:
            await asyncio.gather(active_task, return_exceptions=True)
        self.clear_pending_inputs()

    # ─────────────────────────────────────────────────────────────────────────
    # Gerenciamento de histórico (chamado por routes.py)
    # ─────────────────────────────────────────────────────────────────────────

    def switch_history(self, history_uid: str) -> None:
        """
        Troca o histórico ativo do singleton e persiste a escolha.
        Carrega as mensagens no agent_engine.
        Se a VTuber estiver processando uma resposta, bloqueia a troca (Trava de Resposta).
        """
        if self.is_processing:
            logger.warning(f"🚫 Troca de histórico ({history_uid}) ignorada: VTuber está processando (Trava de Resposta ativada).")
            return

        ctx = self.context
        if not ctx:
            return
        ctx.history_uid = history_uid
        ctx.agent_engine.set_memory_from_history(
            conf_uid=ctx.character_config.conf_uid,
            history_uid=history_uid,
        )
        self._save_state(history_uid, ctx.character_config.conf_uid)
        logger.info(f"Singleton: histórico trocado para {history_uid}")

    def create_fresh_history(self) -> str:
        """
        Cria um novo histórico vazio e o define como ativo.
        Retorna o novo history_uid, ou '' em caso de erro.
        Trava de Resposta: Não cria se a VTuber estiver processando.
        """
        if self.is_processing:
            logger.warning("🚫 Pedido de novo histórico ignorado: VTuber está processando (Trava de Resposta ativada).")
            return ""

        ctx = self.context
        if not ctx:
            return ""

        new_uid = create_new_history(ctx.character_config.conf_uid)
        if new_uid:
            self.switch_history(new_uid)
            try:
                if ctx.agent_engine:
                    ctx.agent_engine.on_new_history(new_uid)
            except Exception as e:
                logger.warning(f"MemoryBook new-history hook failed: {e}")
            logger.info(f"Singleton: novo histórico criado {new_uid}")
        return new_uid


    @property
    def active_connections(self) -> int:
        return len(self._connections)

    @property
    def is_processing(self) -> bool:
        return self._processing

    @property
    def pending_batches(self) -> int:
        # Compatibility name for API clients; now reports canonical event queue size.
        return self._event_queue.qsize()

    @property
    def conversation_queue_snapshot(self) -> dict[str, Any]:
        return self._event_queue.snapshot()

    def get_connection_role(self, client_uid: str) -> Optional[str]:
        entry = self._connections.get(client_uid)
        return entry[1] if entry else None

    async def send_initial_state(self, websocket: WebSocket) -> None:
        """
        Envia mensagens iniciais para um cliente recém conectado,
        replicando o comportamento do _send_initial_messages original.
        """
        ctx = self.context
        if not ctx:
            return

        try:
            # 1. Confirmação de conexão
            await websocket.send_text(
                json.dumps({"type": "full-text", "text": "Connection established"})
            )

            # 2. Modelo Live2D e configuração do personagem
            #    Usa model_info diretamente do live2d_model (igual ao handler original)
            if ctx.live2d_model and ctx.live2d_model.model_info:
                model_info = ctx.live2d_model.model_info
            else:
                # Sem modelo Live2D configurado — manda fallback mínimo
                model_info = {"name": "none"}

            await websocket.send_text(
                json.dumps(
                    {
                        "type": "set-model-and-conf",
                        "model_info": model_info,
                        "conf_name": ctx.character_config.conf_name,
                        "conf_uid": ctx.character_config.conf_uid,
                        "client_uid": "singleton",
                    }
                )
            )

            # 3. Status de grupo vazio (singleton não usa grupos)
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "group-update",
                        "group_id": "",
                        "members": [],
                        "client_uid": "singleton",
                    }
                )
            )

            # 4. Inicia microfone
            await websocket.send_text(
                json.dumps({"type": "control", "text": "start-mic"})
            )

        except Exception as e:
            logger.error(f"Erro ao enviar estado inicial: {e}")
