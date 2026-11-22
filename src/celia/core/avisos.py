"""Avisos ao Criador: o que a Célia diz, o que vai só pro chat e o que fica escondido.

Política (definida em 2026-10-03). A Célia NÃO narra a própria infraestrutura.

  🟢 Problema interno normal (troca de API, fallback, retry, timeout recuperado,
     troca de modelo, fila, oscilação): resolvido sozinho. Nenhuma mensagem.
  🟡 Cota: só uma linha no CHAT (fica no histórico), sem voz, sem interromper a fala.
     Provedor com número (daily_limit): avisos em 80%, 95% e ao esgotar.
     Provedor sem número (Gemini): só ao esgotar de verdade. Nunca inventa percentual.
     Troca de provedor, fallback e retry NÃO viram mensagem.
  🔴 Crítico (voz + chat, curto: o que aconteceu + qual o impacto, sem inventar solução):
     todas as rotas de IA fora · TTS fora · ASR fora · RAM crítica · GPU crítica ·
     serviço essencial morto · gravação comprometida · ação pedida sem nenhum recurso.

Tudo parte de eventos que já existem: o QuotaLedger (cota, FIM), o hook de falha do
tts_manager e um monitor leve iniciado no CeliaSingleton. Chat = store_message + payload
silencioso; voz = /bridge/speak critical (que já espera a Célia terminar de falar).
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from typing import Any, Awaitable, Callable

from loguru import logger

NOME = "Aviso"  # nome no chat; o agente ignora estas linhas ao recarregar o histórico

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "voz": True,                      # vermelho fala; com false vai só pro chat
    "speak_url": "http://127.0.0.1:12393/bridge/speak",
    "cooldown_sec": 900,              # mesmo vermelho não se repete antes disso
    "monitor_interval_sec": 20,
    "confirmacoes": 3,                # leituras seguidas ruins antes de avisar (ignora oscilação)
    "ram_disponivel_min_pct": 4.0,    # RAM livre abaixo disso = crítico
    "swap_max_pct": 92.0,             # swap acima disso E RAM livre < 12% = crítico
    "gpu_vram_max_pct": 97.0,
    "gpu_temp_max_c": 90,
    "tts_falhas_seguidas": 3,
    "perfis_gravacao": ["gravacao", "roteiro"],
    "servicos": [   # essenciais: se parar, o impacto é o que está aqui
        {"nome": "gateway de IA", "url": "http://127.0.0.1:20127/v1/models",
         "impacto": "fico sem acesso aos modelos de IA"},
    ],
}
LABELS = {"cloudflare": "Cloudflare", "openrouter-free": "OpenRouter (free)", "gemini": "Gemini"}

_cfg: dict[str, Any] = dict(DEFAULTS)
_loop: asyncio.AbstractEventLoop | None = None
_tasks: set[Any] = set()
_ativos: set[str] = set()             # vermelhos em curso (não repetem enquanto durar)
_ultimo: dict[str, float] = {}        # chave → quando foi o último aviso vermelho
_contadores: dict[str, int] = {}


def configurar(secao: dict[str, Any] | None) -> None:
    """Aplica a seção `alertas:` do config/model_chains.yaml sobre os padrões."""
    global _cfg
    _cfg = {**DEFAULTS, **(secao or {})}


def cfg() -> dict[str, Any]:
    return _cfg


def resetar() -> None:
    """Zera o estado em memória (testes)."""
    _ativos.clear()
    _ultimo.clear()
    _contadores.clear()


# ── envio: chat e voz (trocáveis nos testes) ──
async def _esperar_silencio(limite: float = 30.0) -> None:
    """Aviso de chat nunca atropela a fala em andamento: espera o playback terminar."""
    try:
        from .continuous_behavior import continuous_behavior as cb
    except Exception:
        return
    fim = time.monotonic() + limite
    while (getattr(cb, "active_speech_id", None) and getattr(cb, "playback_state", "") in {"playing", "cancel_requested"}
           and time.monotonic() < fim):
        await asyncio.sleep(0.25)


async def _chat(texto: str, persistir: bool = True, ao_vivo: bool = True) -> bool:
    from ..live_mode.gate import live_active
    if live_active():
        logger.warning('[avisos] diagnóstico retido fora da conversa pública: {}', texto)
        return False
    from ..celia_singleton import CeliaSingleton

    s = CeliaSingleton.get_instance()
    ctx = getattr(s, "context", None)
    if ctx is None:
        return False
    if persistir and ctx.history_uid:
        from ..chat_history_manager import store_message

        store_message(ctx.character_config.conf_uid, ctx.history_uid, "ai", texto, name=NOME)
    if ao_vivo:
        from ..agent.output_types import DisplayText
        from ..utils.stream_audio import prepare_audio_payload

        await _esperar_silencio()
        await s.broadcast(prepare_audio_payload(None, display_text=DisplayText(text=texto, name=NOME)))
    return True


async def _voz(texto: str, chave: str) -> bool:
    from ..live_mode.gate import live_active
    if live_active():
        logger.warning('[avisos] voz de diagnóstico suprimida na live: {}', chave)
        return False
    import httpx

    body = {"text": texto, "priority": "critical", "speech_source": "alerta_sistema",
            "speech_event_key": f"{chave}:{int(time.time())}", "speech_ttl_ms": 60000}
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(str(_cfg["speak_url"]), json=body)
    return bool((resp.json() or {}).get("ok"))


ENVIO: dict[str, Callable[..., Awaitable[bool]]] = {"chat": _chat, "voz": _voz}


def bind_loop(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Loop principal do servidor: avisos vindos de threads são agendados nele."""
    global _loop
    _loop = loop or asyncio.get_running_loop()


def _agendar(coro: Any) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        if _loop is not None and _loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(coro, _loop)
            _tasks.add(fut)
            fut.add_done_callback(_tasks.discard)
        else:
            coro.close()
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ── 🟡 amarelo: cota, só no chat ──
async def amarelo(texto: str) -> None:
    if not _cfg["enabled"]:
        return
    from ..live_mode.gate import live_active
    if live_active():
        logger.warning('[avisos] alerta amarelo apenas no diagnóstico da live: {}', texto)
        return
    try:
        await ENVIO["chat"]("🟡 " + texto, True, True)
    except Exception as exc:  # noqa: BLE001 - aviso nunca derruba nada
        logger.debug("[avisos] chat amarelo falhou: {}", type(exc).__name__)


# ── 🔴 vermelho: voz + chat ──
def _gravando() -> bool:
    try:
        from .continuous_behavior import continuous_behavior as cb

        return cb.get_profile() in set(_cfg["perfis_gravacao"])
    except Exception:
        return False


async def _emitir_vermelho(chave: str, texto: str) -> None:
    from ..live_mode.gate import live_active
    if live_active():
        logger.warning('[avisos] alerta vermelho apenas no diagnóstico da live: {}', chave)
        return
    if _gravando():
        texto += " Isso compromete a gravação."
    voz_ok = False
    if _cfg["voz"] and chave != "tts":  # com o TTS fora não há como falar
        try:
            voz_ok = bool(await ENVIO["voz"](texto, chave))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[avisos] voz do alerta falhou: {}", type(exc).__name__)
    try:  # com voz, o /speak já mostrou a fala ao vivo; aqui só grava no histórico
        await ENVIO["chat"]("🔴 " + texto, True, not voz_ok)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[avisos] chat vermelho falhou: {}", type(exc).__name__)


def vermelho(chave: str, texto: str) -> bool:
    """Avisa um crítico (voz + chat). Não repete enquanto durar nem antes do cooldown."""
    if not _cfg["enabled"] or chave in _ativos:
        return False
    agora = time.time()
    if agora - _ultimo.get(chave, 0.0) < float(_cfg["cooldown_sec"]):
        _ativos.add(chave)
        return False
    _ativos.add(chave)
    _ultimo[chave] = agora
    logger.warning("[avisos] 🔴 {}: {}", chave, texto)
    _agendar(_emitir_vermelho(chave, texto))
    return True


def resolvido(chave: str) -> None:
    """A condição voltou ao normal: o próximo problema desse tipo volta a avisar."""
    _ativos.discard(chave)


def _seguidas(chave: str, ruim: bool) -> bool:
    """True quando a condição ficou ruim `confirmacoes` leituras seguidas (ignora oscilação)."""
    if not ruim:
        _contadores[chave] = 0
        return False
    _contadores[chave] = _contadores.get(chave, 0) + 1
    return _contadores[chave] >= int(_cfg["confirmacoes"])


MENSAGENS = {
    "ia": "Todas as rotas de IA estão indisponíveis. Não consigo responder agora.",
    "sem_recurso": "Não consegui fazer o que você pediu: nenhuma ferramenta está disponível agora.",
    "tts": "A voz está indisponível. Só consigo responder por texto no chat.",
    "asr": "O reconhecimento de voz está indisponível. Não consigo te ouvir agora.",
    "ram": "A memória do computador está em nível crítico. Posso travar ou ficar lenta.",
    "gpu": "A placa de vídeo está em nível crítico. Voz e visão podem falhar.",
}


# ── 🟡 cota: formatação e gatilhos (vêm do QuotaLedger) ──
def _label(ledger: Any, provider: str) -> str:
    return str((ledger.providers.get(provider) or {}).get("label") or LABELS.get(provider) or provider)


def _numero(valor: float) -> str:
    return f"{valor:,.0f}".replace(",", ".")


def _hora(ts: float) -> str:
    dt = datetime.fromtimestamp(ts)
    return f"às {dt:%H:%M}" if dt.date() == datetime.now().date() else f"amanhã às {dt:%H:%M}"


def _unidade(uso: dict[str, Any]) -> str:
    return "neurons, estimativa local" if uso["kind"] == "neurons" else "chamadas"


def proxima_rota(ledger: Any, brain: str, provider: str, config: dict[str, Any] | None = None) -> bool:
    """Existe, na fila deste cérebro, outro provedor que pode atender agora?"""
    from .model_chain import chain_links, load_config

    try:
        links = chain_links(config if config is not None else load_config(), brain)
    except Exception:
        return False
    return any(ledger.provider_of(link["model"]) != provider and ledger.can_use(link["model"], brain)[0]
               for link in links)


def texto_cota(ledger: Any, provider: str, nivel: str, brain: str = "", config: dict[str, Any] | None = None) -> str:
    """Linha amarela de cota. nivel: p80 | p95 | esgotou | sem_saldo."""
    uso = ledger.usage(provider)
    nome = _label(ledger, provider)
    volta = f" Volta {_hora(uso['blocked_until'] if 0 < uso['blocked_until'] < 3.9e9 else uso['next_reset'])}."
    if nivel in ("p80", "p95"):
        pct = 80 if nivel == "p80" else 95
        return (f"Cota — {nome} em {pct}% do limite diário "
                f"({_numero(uso['used'])} de {_numero(uso['limit'])} {_unidade(uso)}). "
                f"Zera {_hora(uso['next_reset'])}.")
    if nivel == "sem_saldo":
        texto = f"Cota — {nome} sem saldo."
    else:
        numeros = f" ({_numero(uso['used'])} de {_numero(uso['limit'])} {_unidade(uso)})" if uso["limit"] else ""
        texto = f"Cota — {nome} esgotada{numeros}." + volta
    if brain and proxima_rota(ledger, brain, provider, config):
        texto += " Usando a próxima rota."
    return texto


CHAVE_FIM = {"celia": "ia", "jev": "sem_recurso", "asr": "asr"}  # quem esgotou → qual vermelho


def observar_ledger(ledger: Any, brain: str, provider: str, code: str, event: dict[str, Any]) -> None:
    """Chamado pelo ledger a cada evento. Só decide se vira aviso; nada de fallback/retry."""
    if not _cfg["enabled"]:
        return
    chave_fim = CHAVE_FIM.get(brain)
    if code == "FIM":  # fila inteira sem elo
        if chave_fim:
            vermelho(chave_fim, MENSAGENS[chave_fim])
        return
    if code == "OK" and chave_fim:
        resolvido(chave_fim)
    if not provider or provider == "-":
        return
    uso = ledger.usage(provider)
    nivel = ""
    if code == "CTD":
        nivel = "esgotou"
    elif code == "SLD":
        nivel = "sem_saldo"
    elif uso["limit"] > 0 and code in ("OK", "ERR", "LNT", "JSN", "VAZ"):  # só quem tem número
        pct = uso["used"] / uso["limit"] * 100.0
        if pct >= 100.0 and uso["hard"]:
            nivel = "esgotou"
        elif pct >= 95.0:
            ledger.mark_aviso(provider, "p80")  # cruzou direto: o de 80% fica implícito
            nivel = "p95"
        elif pct >= 80.0:
            nivel = "p80"
    if nivel and ledger.mark_aviso(provider, nivel):
        _agendar(amarelo(texto_cota(ledger, provider, nivel, brain)))


# ── 🔴 TTS: hooks do tts_manager ──
def tts_falhou() -> None:
    _contadores["tts"] = _contadores.get("tts", 0) + 1
    if _contadores["tts"] >= int(_cfg["tts_falhas_seguidas"]):
        vermelho("tts", MENSAGENS["tts"])


def tts_ok() -> None:
    _contadores["tts"] = 0
    resolvido("tts")


# ── 🔴 monitor leve: RAM, GPU, serviços essenciais, ASR ──
def _checar_ram() -> bool:
    try:
        import psutil
    except Exception:
        return False
    mem, swap = psutil.virtual_memory(), psutil.swap_memory()
    livre = mem.available / mem.total * 100.0
    return livre < float(_cfg["ram_disponivel_min_pct"]) or (
        swap.total > 0 and swap.percent >= float(_cfg["swap_max_pct"]) and livre < 12.0)


def _checar_gpu() -> bool:
    from .gpu_guard import gpu_status

    try:
        st = gpu_status()
    except Exception:
        return False  # sem nvidia-smi não é alerta de GPU
    return (st["used"] / max(1, st["total"]) * 100.0 >= float(_cfg["gpu_vram_max_pct"])
            or st["temp"] >= int(_cfg["gpu_temp_max_c"]))


async def _checar_servico(url: str) -> bool:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            return (await client.get(url)).status_code < 500
    except Exception:
        return False


def _asr_fora() -> bool:
    """O ASR único (TAGARELA :8011) não responde? Sem reserva: caiu = a Célia está surda."""
    try:
        from ..celia_singleton import CeliaSingleton

        asr = CeliaSingleton.get_instance().context.asr_engine
        snap = asr.health_snapshot()
    except Exception:
        return False  # sem ASR carregado ou sem health: não inventa alerta
    return not (snap.get("primary") is True or snap.get("fallback") is True)


async def ciclo_monitor() -> None:
    """Uma leitura de tudo. Cada condição só vira aviso depois de `confirmacoes` leituras ruins."""
    ram = await asyncio.to_thread(_checar_ram)
    if _seguidas("ram", ram):
        vermelho("ram", MENSAGENS["ram"])
    elif not ram:
        resolvido("ram")
    gpu = await asyncio.to_thread(_checar_gpu)
    if _seguidas("gpu", gpu):
        vermelho("gpu", MENSAGENS["gpu"])
    elif not gpu:
        resolvido("gpu")
    asr = await asyncio.to_thread(_asr_fora)
    if _seguidas("asr", asr):
        vermelho("asr", MENSAGENS["asr"])
    elif not asr:
        resolvido("asr")
    for svc in _cfg["servicos"] or []:
        chave = f"servico:{svc.get('nome')}"
        fora = not await _checar_servico(str(svc.get("url")))
        if _seguidas(chave, fora):
            vermelho(chave, f"O {svc.get('nome')} parou, e {svc.get('impacto') or 'parte do sistema não funciona'}.")
        elif not fora:
            resolvido(chave)


async def monitor() -> None:
    bind_loop()
    while True:
        try:
            await ciclo_monitor()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("[avisos] ciclo do monitor falhou: {}", type(exc).__name__)
        await asyncio.sleep(max(5.0, float(_cfg["monitor_interval_sec"])))


_monitor_task: asyncio.Task | None = None


def iniciar_monitor() -> None:
    """Liga o monitor uma vez (chamado no CeliaSingleton.initialize). Desliga com alertas.enabled: false."""
    global _monitor_task
    if os.environ.get("CELIA_AVISOS") == "0":
        return
    from .model_chain import load_config

    configurar(load_config().get("alertas"))
    if not _cfg["enabled"] or (_monitor_task is not None and not _monitor_task.done()):
        return
    _monitor_task = asyncio.get_running_loop().create_task(monitor())
    logger.info("[avisos] monitor de alertas críticos ligado (RAM, GPU, ASR, serviços essenciais)")
