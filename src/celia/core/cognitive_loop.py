"""
Agente Cognitivo / Monólogo Interno da Célia (Brain Layer — Plano 4).

A Célia deixa de ser só um processador de mensagens: ela mantém um
"monólogo interno" contínuo, rodando num loop assíncrono paralelo,
independente de estar falando ou não.

Fluxo (Cognitive Loop):
  1. Ingestão  — falas recentes, chat, Minecraft e resumo visual efêmero.
  2. Cognição  — 🧠 BrainRouter consulta Luna primeiro, depois Grok e Gemini
                 se necessário; a corrida dos pares é opcional.
  3. Frescor   — 💭🧟 descarta pensamento se a conversation_revision mudou.
  4. Decisão   — should_speak vira 🎟️ CandidateIntent e passa pelo ⚖️ árbitro;
                 o cérebro nunca é o gerador final da fala.

Visão:
  `screen_vision.py` usa 👁️ BrainRouter em frames relevantes (5.6 Luna como
  primeira opção) e injeta um resumo curto. A imagem não entra no log do monólogo.

Variáveis principais:
  CELIA_BRAIN_ROUTER_URL        9router (default: http://127.0.0.1:20127/v1)
  CELIA_BRAIN_PROVIDER_ORDER    ordem sequencial dos três pares
  CELIA_BRAIN_RACE_REMOTE       corrida concorrente (default: 0)
  CELIA_BRAIN_LUNA_SERVICE_TIER tier pedido ao gateway (fast para Luna, vazio para nano)
  CELIA_BRAIN_LUNA_VISION_MODEL modelo usado por Luna quando há imagem (default: 5.6 Luna)
  CELIA_BRAIN_LUNA_MODEL        aceita openrouter/openai/gpt-5-nano ou gpt-4.1-nano para comparação
  CELIA_COGNITIVE_INTERVAL      segundos entre pensamentos (default: 22)
  CELIA_COGNITIVE_TIMEOUT       corte background (default: 5.5)
  CELIA_MC_SNAPSHOT_URL         snapshot do Minecraft (default: dashboard 8789)
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Optional

from loguru import logger

# Reusa o log de contexto já mantido pelo orquestrador (falas do Criador/Célia).
from .orchestrator import orchestrator
from .training_trace import training_trace
from .brain_router import brain_router
from .observability import observability

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_LOG_DIR = _PROJECT_ROOT / "logs"
_MONOLOGUE_LOG = _LOG_DIR / "celia_internal_monologue.jsonl"
_RHYTHM_PROFILE = _LOG_DIR / "celia_rhythm_profile.json"



def describe_hud_for_thought(hud: dict) -> str:
    """One short line of REAL game facts from the daemon's HUD payload; empty when the game data is unavailable (never a guess)."""
    if not isinstance(hud, dict) or hud.get("available") is not True:
        return ""
    celia = hud.get("celia") or {}
    parts: list[str] = []
    health, hunger = celia.get("health"), celia.get("hunger")
    if isinstance(health, (int, float)):
        parts.append(f"vida {health:g}/20")
    if isinstance(hunger, (int, float)):
        parts.append(f"fome {hunger:g}/20")
    held = celia.get("held")
    if isinstance(held, str) and held:
        # Never infer a tool from its appearance: retain the actual Minecraft ID.
        labels = {"poppy": "papoula (flor)", "dandelion": "dente-de-leão (flor)",
                  "iron_pickaxe": "picareta de ferro", "iron_axe": "machado de ferro",
                  "iron_sword": "espada de ferro", "wooden_pickaxe": "picareta de madeira"}
        label = labels.get(held)
        parts.append(f"segurando {held.replace('_', ' ')}" + (f" ({label})" if label else ""))
    inv = hud.get("inventory")
    if isinstance(inv, dict):
        items = [f"{name.replace('_', ' ')} x{qty}" for name, qty in list(inv.items())[:5]
                 if isinstance(name, str) and name.replace("_", "").isalnum()
                 and isinstance(qty, int) and not isinstance(qty, bool) and qty > 0]
        if items:
            parts.append("inventário: " + ", ".join(items))
    threats = hud.get("threats")
    if isinstance(threats, list):
        mobs = [f"{mob['name'].replace('_', ' ')} a {mob['distance']:g} blocos"
                for mob in threats[:3] if isinstance(mob, dict)
                and isinstance(mob.get("name"), str) and mob["name"].replace("_", "").isalnum()
                and isinstance(mob.get("distance"), (int, float))]
        if mobs:
            parts.append("ameaças: " + ", ".join(mobs))
    near = hud.get("nearby_blocks")
    if isinstance(near, list):
        blocks = [name.replace("_", " ") for name in near[:3]
                  if isinstance(name, str) and name.replace("_", "").isalnum()]
        if blocks:
            parts.append("blocos próximos: " + ", ".join(blocks))
    clock = hud.get("clock") or {}
    phases = {"day": "dia", "dusk": "entardecer", "night": "noite", "dawn": "amanhecer"}
    if isinstance(clock.get("hour"), int) and isinstance(clock.get("minute"), int):
        phase = phases.get(str(clock.get("phase")), "")
        parts.append(f"{clock['hour']:02d}:{clock['minute']:02d}" + (f" ({phase})" if phase else ""))
    criador = hud.get("criador") or {}
    pos, me = criador.get("position"), celia.get("position")
    if isinstance(pos, dict) and isinstance(me, dict):
        try:
            distance = ((pos["x"] - me["x"]) ** 2 + (pos["z"] - me["z"]) ** 2) ** 0.5
            parts.append(f"Criador a {distance:.0f} blocos")
        except (KeyError, TypeError):
            pass
    elif criador.get("online") is True:
        parts.append("Criador fora do meu campo de visão")
    return " | ".join(parts)




def _modo_minecraft_ligado() -> bool:
    """Interruptor \"Modo Minecraft\" da aba Corpo (2026-10-25). Desligado: live so de conversa. Sem leitura possivel, fica ligado (comportamento antigo)."""
    try:
        from ..bridge_routes import ler_opcoes_corpo

        return ler_opcoes_corpo().get("modo_minecraft", True) is not False
    except Exception:
        return True


async def _series_mutes_cohost() -> bool:
    """R14: the series is recording (or just said goodbye): no spontaneous co-host lines. CELIA_SERIES_COHOST=1 turns them back on.
    Asks the daemon (cached 0.5 s by the series center): right after a restart the center had no state yet and one line slipped out (ensaio 07:33)."""
    if os.getenv("CELIA_SERIES_COHOST", "").strip() == "1":
        return False
    if not _modo_minecraft_ligado():
        return False  # Modo Minecraft desligado: a serie nao cala a Celia
    try:
        from . import minecraft_series as ms

        state = await ms.get_series_center().series_state()
    except Exception:
        return False
    return state in (ms.SERIES_LIVE_STATES | {"EPISODE_END"})

class CognitiveLoop:
    """Monólogo interno contínuo da Célia."""

    _instance: Optional["CognitiveLoop"] = None
    # Juntar wakes de tela repetidos: um frame relevante = um pensamento.
    _last_screen_wake: float = 0.0
    _coalesced_wakes: int = 0
    _last_coalesced_emit: float = 0.0

    @classmethod
    def get_instance(cls) -> "CognitiveLoop":
        if cls._instance is None:
            cls._instance = CognitiveLoop()
        return cls._instance

    def __init__(self) -> None:
        self._interval = float(os.getenv("CELIA_COGNITIVE_INTERVAL", "22"))
        self._timeout = float(os.getenv("CELIA_COGNITIVE_TIMEOUT", "5.5"))
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._enabled = False
        self._tick_task: Optional[asyncio.Task] = None
        self._wake_event = asyncio.Event()
        self._wake_reason = ""
        self._pending_candidate_id = ""

        # Contexto visual é efêmero: frame velho não pode contaminar um tópico novo.
        self._screen_context: str = ""
        self._screen_context_ts: float = 0.0
        self._stale_thoughts = 0

        # Último pensamento gerado (para os gatilhos de introspecção).
        self._last_thought: dict = {}
        self._last_thought_ts: float = 0.0

        # Buffers isolados (separados do histórico de chat da conversa).
        self._chat_buffer: deque[str] = deque(maxlen=12)
        self._mc_context: str = ""
        self._mc_event_context: str = ""
        self._mc_event_key: str = ""
        self._mc_event_expires: float = 0.0

        # Perfil de ritmo do criador (adaptação dinâmica).
        self._rhythm = self._load_rhythm()

    # ── Ciclo de vida ────────────────────────────────────────────────────────
    def start(self) -> None:
        """Inicia a task de cognição (idempotente)."""
        if self._running:
            return
        if not brain_router.providers():
            logger.info("[Cognitive] 🔌 BrainRouter sem providers habilitados — monólogo interno dormente.")
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("[Cognitive] sem event loop ativo — start adiado.")
            return
        self._running = True
        from .continuous_behavior import continuous_behavior
        continuous_behavior.subscribe_profile(self._profile_changed)
        self._profile_changed(continuous_behavior.profile)
        self._task = loop.create_task(self._run())
        logger.info(f"[Cognitive] 🧠 Monólogo interno ligado (intervalo={self._interval}s).")

    async def stop(self) -> None:
        self._running = False
        self._enabled = False
        if self._tick_task:
            self._tick_task.cancel()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    def _profile_changed(self, profile: str) -> None:
        self._enabled = profile in {"conversation_autonomous", "roteiro", "gravacao", "cohost", "autonomous"}
        if self._tick_task and not self._tick_task.done():
            self._tick_task.cancel()
        self._pending_candidate_id = ""
        self._last_thought = {}
        self._wake_reason = ""
        self._wake_event.set()
        logger.info(f"[Cognitive] profile={profile} suspended={not self._enabled}")

    # ── Ingestão externa ─────────────────────────────────────────────────────
    def register_chat(self, author: str, message: str) -> None:
        message = (message or "").strip()
        if message:
            self._chat_buffer.append(f"{author or 'chat'}: {message}")

    def set_minecraft_context(self, text: str) -> None:
        self._mc_context = (text or "").strip()

    def note_minecraft_event(self, event_key: str, facts: dict, ttl_sec: float = 20.0) -> bool:
        """Make one deduplicated, factual gameplay event available to the existing thinker."""
        key = str(event_key or "")[:160]
        if not key or key == self._mc_event_key:
            return False
        allowed = {"events", "health", "inventory_delta", "donor", "world_id", "action", "action_id", "status", "effect",
                   "item", "count", "collector", "evidence"}
        safe = {str(k)[:32]: v for k, v in facts.items() if k in allowed}
        self._mc_event_key = key
        self._mc_event_context = f"game event {key}: {json.dumps(safe, ensure_ascii=False, separators=(',', ':'))[:480]}"
        self._mc_event_expires = time.monotonic() + max(1.0, min(float(ttl_sec), 60.0))
        return True

    def set_screen_context(self, text: str) -> None:
        """Recebe apenas o resumo semântico do frame; nunca guarda imagem aqui."""
        self._screen_context = (text or "").strip()[:600]
        self._screen_context_ts = time.monotonic() if self._screen_context else 0.0

    def clear_screen_context(self) -> None:
        """A fonte de visão foi desligada: o resumo visual não pode sobreviver a ela."""
        self._screen_context = ""
        self._screen_context_ts = 0.0

    def reset_session_context(self) -> None:
        """Descarta contexto efêmero entre gravações sem tocar memória/persona."""
        self._screen_context = ""
        self._screen_context_ts = 0.0
        self._pending_candidate_id = ""
        self._last_thought = {}
        self._last_thought_ts = 0.0
        self._wake_reason = ""
        self._chat_buffer.clear()
        self._mc_context = ""
        self._mc_event_context = ""
        self._mc_event_key = ""
        self._mc_event_expires = 0.0
        self._stale_thoughts = 0

    def _fresh_screen_context(self) -> str:
        if not self._screen_context or not self._screen_context_ts:
            return ""
        ttl = float(os.getenv("CELIA_SCREEN_CONTEXT_TTL", "15"))
        if time.monotonic() - self._screen_context_ts > max(2.0, ttl):
            self._screen_context = ""
            self._screen_context_ts = 0.0
            return ""
        return self._screen_context

    def note_interrupt(self) -> None:
        """Criador interrompeu — marca 'momento de foco' (adaptação de ritmo)."""
        self._rhythm["interrupts"] = int(self._rhythm.get("interrupts", 0)) + 1
        self._rhythm["focus_moments"] = int(self._rhythm.get("focus_moments", 0)) + 1
        self._save_rhythm()

    def request_think(self, reason: str = "external_event") -> bool:
        """Wake the existing cognitive loop; this never creates another LLM loop."""
        if not self._running or not self._enabled:
            return False
        reason = str(reason or "external_event")[:64]
        if reason.startswith(("screen", "vision")):
            now = time.monotonic()
            try:
                gap = float(os.getenv("CELIA_COGNITIVE_SCREEN_WAKE_GAP", "6"))
            except ValueError:
                gap = 6.0
            if now - self._last_screen_wake < gap:
                self._coalesced_wakes += 1
                if now - self._last_coalesced_emit >= 5.0:
                    self._last_coalesced_emit = now
                    observability.emit_ambient(
                        "cognitive.wake", "coalesced",
                        {"reason": reason, "count": self._coalesced_wakes},
                    )
                    self._coalesced_wakes = 0
                return False
            self._last_screen_wake = now
        self._wake_reason = reason
        observability.emit_ambient("cognitive.wake", "ok", {"reason": reason})
        self._wake_event.set()
        return True

    # ── Introspecção (gatilho "por que você não fala?") ──────────────────────
    def introspection_prompt(self) -> Optional[str]:
        """
        Resumo do monólogo interno, formatado como input para a Célia responder
        com personalidade quando o criador a provoca ("cadê você?").
        Retorna None se não há pensamento recente.
        """
        thought = (self._last_thought or {}).get("internal_thought", "").strip()
        if not thought:
            return None
        age = time.time() - self._last_thought_ts
        recency = "agora há pouco" if age < 60 else "nos últimos minutos"
        return (
            "[RELATÓRIO DO SEU MONÓLOGO INTERNO]\n"
            f"O que você estava processando {recency}: {thought}\n\n"
            "O criador acabou de te perguntar o que você está pensando / por que está "
            "calada. Responda no seu jeito (acolhedora, bem-humorada e espontânea), contando de verdade "
            "o que passou pela sua cabeça — não dê resposta genérica."
        )

    def last_thought(self) -> dict:
        return dict(self._last_thought)

    def debug_snapshot(self) -> dict:
        """Small loopback-safe wiring view; never exposes API keys."""
        age_ms = None
        if self._last_thought_ts:
            age_ms = max(0, int((time.time() - self._last_thought_ts) * 1000))
        provider = str((self._last_thought or {}).get("provider") or "none")
        screen_age_ms = None
        if self._screen_context_ts:
            screen_age_ms = max(0, int((time.monotonic() - self._screen_context_ts) * 1000))
        return {
            "running": self._running and self._enabled,
            "started": self._running,
            "suspended": self._running and not self._enabled,
            "interval_sec": self._interval,
            "provider_last": provider,
            "brain_router": brain_router.debug_snapshot(),
            "last_thought_age_ms": age_ms,
            "last_should_speak": bool((self._last_thought or {}).get("should_speak", False)),
            "pending_candidate_id": self._pending_candidate_id or None,
            "wake_reason": self._wake_reason or None,
            "screen_context_age_ms": screen_age_ms,
            "screen_context_present": bool(self._fresh_screen_context()),
            "stale_thoughts": self._stale_thoughts,
        }

    # ── Loop de cognição ─────────────────────────────────────────────────────
    async def _run(self) -> None:
        # Espalha o primeiro tick para não competir com o boot do servidor.
        await asyncio.sleep(min(self._interval, 8.0))
        while self._running:
            if not self._enabled:
                self._wake_event.clear()
                await self._wake_event.wait()
                continue
            try:
                self._tick_task = asyncio.create_task(self._think_once())
                await self._tick_task
            except asyncio.CancelledError:
                if not self._running:
                    break
                continue
            except Exception as exc:
                logger.debug(f"[Cognitive] tick falhou: {exc}")
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=self._next_wait())
                self._wake_event.clear()
            except asyncio.TimeoutError:
                pass

    def _next_wait(self) -> float:
        """Normally one tick per interval. In Conversa Autonoma the next tick is the exact moment the shared silence reaches the threshold."""
        from .continuous_behavior import continuous_behavior
        until = continuous_behavior.seconds_until_initiative()
        if until == float("inf"):
            return self._interval  # somebody is talking (or this profile has no initiative): the next event wakes the loop anyway
        return max(0.2, min(self._interval, until + 0.05))

    @staticmethod
    def _celia_busy() -> bool:
        try:
            from ..celia_singleton import CeliaSingleton
            singleton = CeliaSingleton.get_instance()
            return bool(singleton.is_processing or singleton.pending_batches)
        except Exception:
            return True  # when we cannot tell, assume she is busy: never talk over a turn that may be running

    async def _think_once(self) -> None:
        from .continuous_behavior import continuous_behavior
        from .possession import is_silenced
        if continuous_behavior.profile == "chat" or is_silenced():
            return  # the owner asked for silence during a possession: the persona's own initiative stays quiet until he speaks again
        falas = orchestrator.context_log.recent_summary(max_chars=700)
        chat = "\n".join(self._chat_buffer)
        if _modo_minecraft_ligado():
            await self._refresh_minecraft_context()
        else:
            self._mc_context = ""  # Modo Minecraft desligado: o jogo nao entra no pensamento
        mc = self._mc_context
        screen = self._fresh_screen_context()
        from ..live_mode import gate as live_gate
        if live_gate.owner_input_blocked():
            if not live_gate.proactive_allowed():
                return  # modo Live sem fala proativa: o portão recusaria a fala, então nem chama a LLM
            if not falas and not chat:
                falas = "(live no ar; o chat está quieto agora)"

        # Sem nenhum sinal de contexto, não há o que pensar — economiza chamada.
        if not falas and not chat and not mc and not screen:
            return
        if continuous_behavior.profile == "conversation_autonomous":
            # Initiative is only a chance: it opens with REAL shared silence, a fresh context, the cooldown and the budget. No LLM call otherwise.
            opened, why = continuous_behavior.initiative_status(celia_busy=self._celia_busy(), has_context=True)
            if not opened:
                observability.emit_ambient("cognitive.think", "held", {"reason": why})
                return

        from .continuous_behavior import continuous_behavior
        snapshot = continuous_behavior.safe_snapshot()
        social = (
            f"profile={snapshot.get('profile')} state={snapshot.get('state')} "
            f"turn_end={snapshot.get('turn_end_probability')} "
            f"talk_celia={(snapshot.get('talk_ratio') or {}).get('celia', 0)} "
            f"speaker={snapshot.get('active_speaker_role')} "
            f"playback={(snapshot.get('active_speech') or {}).get('playback_state')}"
        )
        prompt = self._build_prompt(falas, chat, mc, screen, social)
        prompt_revision = snapshot.get("conversation_revision")
        _t0 = time.monotonic()
        observability.emit_ambient(
            "cognitive.think", "start", {"reason": (self._wake_reason or "interval")[:64]}
        )
        thought = await self._semantic_think(prompt)
        _ms = int((time.monotonic() - _t0) * 1000)
        if not thought:
            observability.emit_ambient("cognitive.think", "empty", duration_ms=_ms)
            return
        latest_revision = continuous_behavior.safe_snapshot().get("conversation_revision")
        if prompt_revision != latest_revision:
            self._stale_thoughts += 1
            observability.emit_ambient("cognitive.think", "stale", duration_ms=_ms)
            logger.info(
                f"[Cognitive] 💭🧟 pensamento stale descartado "
                f"rev={prompt_revision}->{latest_revision}"
            )
            return

        thought["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        thought["wake_reason"] = self._wake_reason or "interval"
        self._wake_reason = ""
        self._last_thought = thought
        self._last_thought_ts = time.time()
        self._append_log(thought)
        logger.info(
            f"[Cognitive] 💭 {thought.get('internal_thought', '')[:80]} "
            f"(provider={thought.get('provider')}, fala={thought.get('should_speak')})"
        )
        observability.emit_ambient(
            "cognitive.think", "ok",
            {"provider": str(thought.get("provider") or "")[:40],
             "should_speak": bool(thought.get("should_speak"))},
            duration_ms=_ms,
        )
        await self._publish_thought_candidate(thought)

    def _build_prompt(self, falas: str, chat: str, mc: str, screen: str, social: str) -> str:
        from .continuous_behavior import continuous_behavior
        from ..live_mode import gate as live_gate
        if live_gate.proactive_allowed():
            return (
                "Você é a CONSCIÊNCIA INTERNA da Célia na LIVE dela no TikTok: ela conversa com quem está no chat. "
                "Houve um silêncio. Decida o que ela puxa agora: uma curiosidade interessante, uma pergunta pro chat "
                "ou um comentário sobre o que já foi falado. Varie o assunto e não repita as falas recentes. "
                "Nunca peça presente, seguidor, curtida ou compartilhamento. Não invente fatos nem acontecimentos da live. "
                "NÃO escreva a fala final.\n\n"
                f"[ESTADO SOCIAL]\n{social}\n\n[FALAS RECENTES]\n{falas or '(silêncio)'}\n\n[CHAT]\n{chat or '(quieto)'}\n\n"
                "Responda SOMENTE JSON compacto:\n"
                '{"internal_thought":"...","live2d_expression":"neutral|think|joy|curious|smug|bored",'
                '"should_speak":true,"confidence":0.0,"urgency":0.0,"social_value":0.0}'
            )
        if continuous_behavior.profile == "roteiro":
            return (
                "Voce e a CONSCIENCIA INTERNA da Celia, diretora metodica de roteiro. "
                "Acompanhe beat atual, proximos beats, fatos obrigatorios e omissoes. "
                "Prepare completamento ou correcao factual; cancele se o criador ja completou. "
                "Nao invente beats ausentes. Nao escreva a fala final.\n"
                + continuous_behavior.build_prompt_context()
                + f"\n[FALAS RECENTES]\n{falas}\n[ESTADO]\n{social}\n"
                + 'Responda JSON: {"internal_thought":"...","should_speak":false,'
                '"confidence":0.0,"urgency":0.0,"social_value":0.0}'
            )
        if continuous_behavior.profile == "gravacao":
            return (
                "Voce e a CONSCIENCIA INTERNA da Celia em MODO GRAVACAO. "
                "Combine roteiro flexivel, tela real e timing social. O criador e o professor principal, "
                "mas voce pode virar co-professora por alguns segundos se ele pedir ajuda, esquecer "
                "um conceito, repetir um erro ou ficar claramente travado. Nao leia roteiro nem "
                "invente resultado. Humor e deboche sao permitidos quando combinam com a cena. "
                "Nao escreva a fala final.\n"
                + continuous_behavior.build_prompt_context()
                + f"\n[FALAS RECENTES]\n{falas or '(silencio)'}\n"
                + f"[TELA — RESUMO VISUAL RECENTE]\n{screen or '(sem mudanca visual relevante)'}\n"
                + f"[ESTADO]\n{social}\n"
                + "Decida se existe uma contribuicao curta que melhora a gravacao. "
                "Se o criador esta explicando bem, deixe ele conduzir. "
                "Responda SOMENTE JSON compacto: "
                '{"internal_thought":"...","live2d_expression":"neutral|think|joy|curious|smug|bored",'
                '"should_speak":false,"confidence":0.0,"urgency":0.0,"social_value":0.0}'
            )
        if continuous_behavior.profile == "conversation_autonomous":
            return (
                "Você é a CONSCIÊNCIA INTERNA da Célia na CONVERSA AUTÔNOMA: ela conversa com o criador e joga Minecraft ao lado dele. "
                "Houve um silêncio de verdade. Decida se existe algo REAL e pertinente para ela puxar agora: o que acabou de acontecer no jogo, "
                "o que ele estava dizendo, uma provocação amigável no tom dela. Silêncio também é resposta: se não há nada bom, não fale. "
                "Use os IDs reais do inventário e das ameaças; picareta, machado e espada são ferramentas diferentes. "
                "Ameaça real ganha prioridade sobre provocação. Quando o criador fizer pegadinha, prefira humor seco e observação factual, "
                "nunca invente vitória, memória de morte anterior ou item que os sensores não confirmaram. "
                "NÃO escreva a fala final e NÃO use frase pronta.\n\n"
                f"[ESTADO SOCIAL]\n{social}\n\n[FALAS RECENTES]\n{falas or '(silêncio)'}\n\n[JOGO]\n{mc or '(desconhecido)'}\n\n"
                f"[TELA]\n{screen or '(sem mudança visual relevante)'}\n\n"
                "Se o criador está concentrado em algo arriscado (lava, ponte, combate), só fale se for um aviso útil. Não invente fatos. "
                "Responda SOMENTE JSON compacto:\n"
                '{"internal_thought":"...","live2d_expression":"neutral|think|joy|curious|smug|bored",'
                '"should_speak":false,"confidence":0.0,"urgency":0.0,"social_value":0.0}'
            )
        mc_pref = self._rhythm.get("prefers_short_in_minecraft", True)
        ritmo = (
            "Quando estiver jogando/minerando, prefira contribuição curta."
            if mc_pref else ""
        )
        return (
            "Você é a CONSCIÊNCIA INTERNA da Célia, co-host da conversa. "
            "Você decide se existe algo relevante para ela querer dizer; NÃO escreve a fala final.\n\n"
            f"[ESTADO SOCIAL]\n{social}\n\n"
            f"[FALAS RECENTES]\n{falas or '(silêncio)'}\n\n"
            f"[CHAT]\n{chat or '(vazio)'}\n\n"
            f"[JOGO]\n{mc or '(desconhecido)'}\n\n"
            f"[TELA — RESUMO VISUAL RECENTE]\n{screen or '(sem mudança visual relevante)'}\n\n"
            f"{ritmo}\n"
            "Se state=human_speaking, normalmente should_speak=false. "
            "Só marque true quando a contribuição realmente melhora o momento e existe abertura social. "
            "Não invente fatos. Pense em 1 frase curta.\n"
            "Responda SOMENTE JSON compacto:\n"
            '{"internal_thought":"...","live2d_expression":"neutral|think|joy|curious|smug|bored",'
            '"should_speak":false,"confidence":0.0,"urgency":0.0,"social_value":0.0}'
        )

    async def _semantic_think(self, prompt: str) -> Optional[dict]:
        """Use the single peer BrainRouter; no hidden provider fallback chain."""
        max_tokens = int(os.getenv("CELIA_COGNITIVE_MAX_TOKENS", "128"))
        routed = await brain_router.route(
            prompt,
            self._parse_thought,
            max_tokens=max_tokens,
            trace_role="cognitive_loop",
        )
        if not routed:
            return None
        thought = routed.value
        thought["provider"] = routed.provider
        training_trace.capture(
            "llm_call_complete",
            {
                "role": "cognitive_orchestrator",
                "model_used": routed.model,
                "provider": routed.provider,
                "prompt": prompt,
                "output": thought,
                "latency_ms": routed.latency_ms,
            },
            layer="llm", source="cognitive_loop", trainable=True,
        )
        return thought

    @staticmethod
    def _clamp01(value, default: float) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return default

    @classmethod
    def _parse_thought(cls, raw: str) -> Optional[dict]:
        if not raw:
            return None
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {
                "internal_thought": raw[:200],
                "live2d_expression": "neutral",
                "should_speak": False,
                "confidence": 0.5,
                "urgency": 0.2,
                "social_value": 0.4,
            }
        try:
            obj = json.loads(raw[start : end + 1])
        except Exception:
            return None
        should_speak = obj.get("should_speak", False)
        if isinstance(should_speak, str):
            should_speak = should_speak.strip().lower() in {"1", "true", "yes", "sim"}
        expression = str(obj.get("live2d_expression", "neutral")).strip() or "neutral"
        if expression not in {"neutral", "think", "joy", "curious", "smug", "bored"}:
            expression = "neutral"
        return {
            "internal_thought": str(obj.get("internal_thought", "")).strip()[:240],
            "live2d_expression": expression,
            "should_speak": bool(should_speak),
            "confidence": cls._clamp01(obj.get("confidence"), 0.72),
            "urgency": cls._clamp01(obj.get("urgency"), 0.45),
            "social_value": cls._clamp01(obj.get("social_value"), 0.72),
        }

    async def _publish_thought_candidate(self, thought: dict) -> None:
        if not thought.get("should_speak"):
            return
        internal = str(thought.get("internal_thought") or "").strip()
        if not internal:
            return
        from .continuous_behavior import continuous_behavior

        if continuous_behavior.profile not in {"conversation_autonomous", "gravacao", "cohost", "roteiro", "autonomous"}:
            return
        scores = {
            "intent_type": "cognitive_contribution",
            "semantic_intent": internal,
            "confidence": self._clamp01(thought.get("confidence"), 0.72),
            "priority": min(0.92, 0.64 + self._clamp01(thought.get("urgency"), 0.45) * 0.22),
            "urgency": self._clamp01(thought.get("urgency"), 0.45),
            "novelty": 0.72,
            "social_value": self._clamp01(thought.get("social_value"), 0.72),
            "interruption_cost": 0.12,
            "risk": 0.0,
            "ttl_sec": 12,
            "draft": internal,
            "reason": ("cognitive_should_speak:" + self._mc_event_key[-32:]) if self._mc_event_context and time.monotonic() < self._mc_event_expires else "cognitive_should_speak",
        }
        placeholder = next((
            item for item in continuous_behavior.candidates.values()
            if item.intent_type == "participation_opportunity"
            and item.status in {"WAITING", "READY"}
            and item.context_revision == continuous_behavior.revision
        ), None)
        if placeholder is not None:
            candidate = continuous_behavior.update_candidate(placeholder.id, **scores)
        else:
            candidate = continuous_behavior.create_candidate(**scores)
        if candidate is None:
            return
        self._pending_candidate_id = candidate.id
        logger.info(
            f"[Cognitive] 💭→🗳️ candidate={candidate.id[:8]} "
            f"profile={continuous_behavior.profile}"
        )
        await self.try_dispatch_pending()

    async def try_dispatch_pending(self) -> bool:
        """Route one cognitive candidate through the canonical arbiter and singleton queue."""
        candidate_id = self._pending_candidate_id
        if not candidate_id:
            return False
        from .continuous_behavior import continuous_behavior
        candidate = continuous_behavior.candidates.get(candidate_id)
        if candidate is None or candidate.status not in {"WAITING", "READY"}:
            self._pending_candidate_id = ""
            return False
        try:
            from ..celia_singleton import CeliaSingleton
            singleton = CeliaSingleton.get_instance()
        except Exception:
            return False
        if singleton.context is None or singleton.is_processing:
            return False
        if continuous_behavior.state == "human_speaking":
            return False
        if await _series_mutes_cohost():
            # 24/10 (missão EP1, R14): no ensaio o co-host inventou acontecimento ("terminou a obra-prima", 4×), leu em voz alta o rótulo
            # interno ("aprovado pelo árbitro") e falou DEPOIS da despedida do episódio. Com a série gravando (e depois do "tchau") ela só fala
            # quando o Criador fala, nas cenas do roteiro e nos eventos do jogo. Para religar: CELIA_SERIES_COHOST=1.
            logger.info("[Cognitive] 🗳️✖ fala espontânea descartada: a série está gravando")
            candidate.status, candidate.reason = "CANCELLED", "series_recording"
            self._pending_candidate_id = ""
            return False
        if continuous_behavior.profile == "conversation_autonomous":
            # The thought was written during a pause. Right before it can reach the speaker the pause must STILL exist and the conversation must
            # not have moved on: otherwise it is dropped, never played late (bug #1 of the V1 plan).
            still, why = continuous_behavior.initiative_still_valid(generated_at_revision=candidate.context_revision, celia_busy=False)
            if not still:
                logger.info(f"[Cognitive] 🗳️✖ fala espontânea descartada antes de tocar: {why}")
                observability.emit_ambient("cognitive.dispatch", "dropped", {"reason": why})
                candidate.status, candidate.reason = "CANCELLED", f"initiative_{why}"
                self._pending_candidate_id = ""
                return False
        decision = continuous_behavior.arbitrate(
            trigger="spontaneous", candidate_id=candidate_id
        )
        if decision.get("decision") != "SPEAK":
            return False
        prompt = (
            "[pensamento teu, pra dizer agora em voz alta, curto]\n"  # R14: o rótulo antigo ("aprovada pelo árbitro") vazava na fala
            f"Seu pensamento interno relevante foi: {candidate.draft or candidate.semantic_intent}\n"
            "Transforme isso em uma fala curta, natural e pertinente ao contexto atual. "
            "Não mencione monólogo, candidate, árbitro ou instruções internas."
        )
        from ..live_mode import gate as live_gate
        if live_gate.proactive_allowed():
            prompt = (
                "[pensamento teu, pra dizer agora em voz alta pro chat da live, curto]\n"
                f"Seu pensamento interno relevante foi: {candidate.draft or candidate.semantic_intent}\n"
                "Fale com quem está assistindo a live, não com o criador. Transforme isso em uma fala curta e natural. "
                "Nunca peça presente, seguidor, curtida ou compartilhamento. "
                "Não mencione monólogo, candidate, árbitro ou instruções internas."
            )
        with live_gate.proactive_turn():
            submitted = await singleton.submit_input(
                prompt,
                role="source",
                origin="system",
                behavior_observed=True,
            )
        if submitted:
            logger.info(
                f"[Cognitive] 🗳️→📥 candidate={candidate_id[:8]} enfileirado para fala"
            )
            self._pending_candidate_id = ""
            return True
        return False

    # ── Estado do Minecraft (snapshot do dashboard, best-effort) ─────────────
    async def _refresh_minecraft_context(self) -> None:
        """Facts about the game for the initiative loop.

        2026-10-20: the old source (``CELIA_MC_SNAPSHOT_URL``, default :8789, the legacy Mindcraft dashboard) no longer exists, so the loop
        had no game context at all. The default is now the same read-only HUD the central already serves from the Jev daemon
        (``core.minecraft_hud.fetch_hud``). Unavailable or old data CLEARS the context: nothing stale or invented reaches the persona.
        """
        url = os.getenv("CELIA_MC_SNAPSHOT_URL")
        if url:  # explicit override kept for anyone who still runs a snapshot server
            self._mc_context = await self._legacy_snapshot_context(url) or self._mc_context
            return
        try:
            from .minecraft_hud import fetch_hud

            hud = await asyncio.wait_for(asyncio.to_thread(fetch_hud), 2.0)
        except Exception:
            self._mc_context = ""
            return
        self._mc_context = describe_hud_for_thought(hud)
        if self._mc_event_context:
            if time.monotonic() < self._mc_event_expires:
                self._mc_context = " | ".join(part for part in (self._mc_context, self._mc_event_context) if part)
            else:
                self._mc_event_context = ""
                self._mc_event_key = ""

    async def _legacy_snapshot_context(self, url: str) -> str:
        import urllib.request

        def _call() -> Optional[dict]:
            try:
                with urllib.request.urlopen(url, timeout=0.8) as resp:
                    return json.loads(resp.read())
            except Exception:
                return None

        try:
            data = await asyncio.wait_for(asyncio.to_thread(_call), timeout=1.2)
        except Exception:
            return ""
        if not isinstance(data, dict):
            return ""
        bot = data.get("bot") or data.get("state") or data
        try:
            hp = bot.get("health") or bot.get("hp")
            action = bot.get("action") or bot.get("current_action")
            pos = bot.get("position") or bot.get("pos")
            parts = []
            if hp is not None:
                parts.append(f"HP {hp}")
            if action:
                parts.append(f"ação: {action}")
            if pos:
                parts.append(f"pos {pos}")
            return " | ".join(str(p) for p in parts)
        except Exception:
            return ""

    # ── Persistência ─────────────────────────────────────────────────────────
    def _append_log(self, thought: dict) -> None:
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(_MONOLOGUE_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(thought, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug(f"[Cognitive] falha ao gravar monólogo: {exc}")

    def _load_rhythm(self) -> dict:
        try:
            if _RHYTHM_PROFILE.exists():
                with open(_RHYTHM_PROFILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {"interrupts": 0, "focus_moments": 0, "prefers_short_in_minecraft": True}

    def _save_rhythm(self) -> None:
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(_RHYTHM_PROFILE, "w", encoding="utf-8") as f:
                json.dump(self._rhythm, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.debug(f"[Cognitive] falha ao salvar ritmo: {exc}")


# Singleton global
cognitive_loop = CognitiveLoop.get_instance()
