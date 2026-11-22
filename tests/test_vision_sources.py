"""Visão por fonte: tela e câmera independentes, só ligam por sinal explícito.

Regras do dono (2026-10-05) cobertas aqui:
  - fonte desligada NUNCA agenda análise nem chama LLM;
  - tela e câmera têm liga/desliga separados (desligar uma não toca na outra);
  - as duas dividem o MESMO teto de chamadas por minuto;
  - desligar limpa só o que veio daquela fonte e cancela a análise em voo dela;
  - um frame relevante acorda UM pensamento, não dois.
Nada aqui toca em rede, GPU ou LLM real: tempo simulado e provider falso.
"""
import asyncio

import pytest

from celia.core.brain_router import BrainResult
from celia.core.cognitive_loop import CognitiveLoop
from celia.core.observability import observability
from celia.core.screen_vision import ScreenVisionAnalyzer
from celia.core.vision_cache import vision_cache
from celia.core.vision_sources import (
    VisionSourceRegistry,
    normalize_source,
    vision_sources,
)

JPEG = "data:image/jpeg;base64,AA=="


# ── registro de fontes ────────────────────────────────────────────────────────
def test_normalize_source_aceita_apelidos_e_recusa_o_resto():
    assert normalize_source("Tela") == "screen"
    assert normalize_source("câmera") == "camera"
    assert normalize_source("webcam") == "camera"
    assert normalize_source("microfone") is None
    assert normalize_source(None) is None


def test_fontes_sao_independentes_e_nada_liga_sozinho():
    reg = VisionSourceRegistry()
    assert reg.active_sources() == []  # recém-criado: tudo desligado
    reg.set_active("screen", True, width=1280, height=720)
    assert reg.is_active("screen") and not reg.is_active("camera")
    reg.set_active("camera", True)
    reg.set_active("screen", False)
    assert reg.active_sources() == ["camera"]
    snap = reg.snapshot()["sources"]
    assert snap["screen"]["active"] is False
    assert snap["camera"]["active"] is True


def test_listener_so_dispara_em_mudanca_real_e_fora_do_lock():
    reg = VisionSourceRegistry()
    seen = []
    reg.subscribe(lambda s, a, r: seen.append((s, a, r)))
    reg.set_active("screen", True)
    reg.set_active("screen", True)  # repetido: não é mudança
    reg.set_active("screen", False, reason="signal")
    reg.set_active("screen", False)  # já desligada
    assert seen == [("screen", True, "signal"), ("screen", False, "signal")]


def test_timeout_de_seguranca_desliga_fonte_sem_sinal_de_vida(monkeypatch):
    monkeypatch.setenv("CELIA_VISION_SOURCE_TIMEOUT", "10")
    agora = [100.0]
    reg = VisionSourceRegistry(clock=lambda: agora[0])
    seen = []
    reg.subscribe(lambda s, a, r: seen.append((s, a, r)))
    reg.set_active("camera", True)
    agora[0] += 8
    reg.touch("camera")  # frame aceito = sinal de vida
    agora[0] += 8
    assert reg.is_active("camera")
    agora[0] += 5  # 13 s sem sinal > 10 s
    assert not reg.is_active("camera")
    assert seen[-1] == ("camera", False, "timeout")


# ── gate no analisador ────────────────────────────────────────────────────────
def _analisador(monkeypatch, chamadas):
    monkeypatch.setenv("CELIA_VISION_MIN_INTERVAL", "0")
    monkeypatch.setenv("CELIA_VISION_MIN_RELEVANCE", "0.62")
    analyzer = ScreenVisionAnalyzer()

    async def falso(generation, image, relevance, source="screen"):
        chamadas.append(source)

    monkeypatch.setattr(analyzer, "_analyze", falso)
    return analyzer


@pytest.mark.asyncio
async def test_fonte_desligada_nunca_agenda_analise(monkeypatch):
    chamadas = []
    analyzer = _analisador(monkeypatch, chamadas)
    assert analyzer.schedule(JPEG, relevance=1.0, source="screen") is False
    assert analyzer.schedule(JPEG, relevance=1.0, source="camera") is False
    assert analyzer.schedule(JPEG, relevance=1.0, source="microfone") is False
    await asyncio.sleep(0)
    assert chamadas == []
    assert analyzer.metrics["skipped_source_off"] == 3
    assert analyzer.metrics["requested"] == 0


@pytest.mark.asyncio
async def test_so_a_fonte_ligada_e_analisada(monkeypatch):
    chamadas = []
    analyzer = _analisador(monkeypatch, chamadas)
    vision_sources.set_active("camera", True)
    assert analyzer.schedule(JPEG, relevance=0.9, source="screen") is False
    assert analyzer.schedule(JPEG, relevance=0.9, source="camera") is True
    await asyncio.sleep(0)
    assert chamadas == ["camera"]


@pytest.mark.asyncio
async def test_teto_compartilhado_entre_tela_e_camera(monkeypatch):
    """Frames de 2 s alternando tela/câmera por 2 min: nunca passa de 12 por minuto."""
    monkeypatch.setenv("CELIA_VISION_MAX_CALLS_PER_MIN", "12")
    chamadas = []
    analyzer = _analisador(monkeypatch, chamadas)
    vision_sources.set_active("screen", True)
    vision_sources.set_active("camera", True)
    relogio = [1000.0]
    monkeypatch.setattr("celia.core.screen_vision.time.monotonic", lambda: relogio[0])
    aceitos = []
    for i in range(60):  # 60 frames x 2 s = 120 s
        fonte = "screen" if i % 2 == 0 else "camera"
        if analyzer.schedule(JPEG, relevance=0.95, source=fonte):
            aceitos.append(relogio[0])
        relogio[0] += 2.0
    await asyncio.sleep(0)
    assert len(aceitos) == len(chamadas)
    # nenhuma janela deslizante de 60 s tem mais de 12 chamadas
    for inicio in aceitos:
        assert sum(1 for t in aceitos if inicio <= t < inicio + 60.0) <= 12
    assert analyzer.metrics["skipped_budget"] > 0
    # as DUAS fontes foram atendidas (o teto divide, não monopoliza)
    assert {"screen", "camera"} <= set(chamadas)


@pytest.mark.asyncio
async def test_pedido_explicito_ignora_so_o_teto_mas_exige_fonte_ligada(monkeypatch):
    monkeypatch.setenv("CELIA_VISION_MAX_CALLS_PER_MIN", "1")
    chamadas = []
    analyzer = _analisador(monkeypatch, chamadas)
    vision_sources.set_active("screen", True)
    relogio = [500.0]
    monkeypatch.setattr("celia.core.screen_vision.time.monotonic", lambda: relogio[0])
    assert analyzer.schedule(JPEG, relevance=0.9, source="screen") is True
    relogio[0] += 3
    assert analyzer.schedule(JPEG, relevance=0.9, source="screen") is False  # teto
    assert analyzer.schedule(JPEG, relevance=1.0, source="screen", explicit=True) is True
    vision_sources.set_active("screen", False)
    relogio[0] += 3
    assert analyzer.schedule(JPEG, relevance=1.0, source="screen", explicit=True) is False


# ── desligar limpa só a fonte desligada ───────────────────────────────────────
def _resultado(topic):
    return BrainResult(
        {"summary": f"resumo {topic}", "topic": topic, "salience": 0.8, "should_comment": False},
        "p", "m", 10,
    )


@pytest.mark.asyncio
async def test_desligar_cancela_analise_em_voo_so_daquela_fonte(monkeypatch):
    import celia.core.cognitive_loop as cog

    analyzer = ScreenVisionAnalyzer()
    vision_sources.subscribe(analyzer._on_source_change)  # o singleton já faz isto no import
    contexto, acordes = [], []
    monkeypatch.setattr(cog.cognitive_loop, "set_screen_context", lambda t: contexto.append(t))
    monkeypatch.setattr(cog.cognitive_loop, "request_think", lambda r: acordes.append(r) or True)
    monkeypatch.setenv("CELIA_VISION_MIN_INTERVAL", "0")
    vision_sources.set_active("screen", True)
    vision_sources.set_active("camera", True)
    liberar = asyncio.Event()

    from celia.core.local_vision import LocalVisionResult
    async def rota(**kwargs):
        await liberar.wait()
        return LocalVisionResult(raw='{"summary":"resumo jogo","topic":"jogo","salience":0.8,"should_comment":false}',
                                 model="celia-qwen3.5-2b", latency_ms=10)
    class Scheduler:
        enabled = True
        async def run_job(self, kind, fn, **kwargs):
            return await fn()
    monkeypatch.setattr("celia.core.screen_vision.gpu_scheduler", Scheduler())
    monkeypatch.setattr("celia.core.screen_vision.local_vision_client.request", rota)
    monkeypatch.setenv("CELIA_VISION_BACKEND", "local")
    relogio = [2000.0]
    monkeypatch.setattr("celia.core.screen_vision.time.monotonic", lambda: relogio[0])
    assert analyzer.schedule(JPEG, relevance=0.9, source="screen")
    relogio[0] += 1.0  # o cooldown é compartilhado: a câmera espera a vez
    assert analyzer.schedule(JPEG, relevance=0.9, source="camera")
    await asyncio.sleep(0)
    vision_sources.set_active("camera", False)  # desliga SÓ a câmera, com análise em voo
    liberar.set()
    for _ in range(5):
        await asyncio.sleep(0)
    assert analyzer.metrics["stale_dropped"] == 1  # a da câmera
    assert analyzer.metrics["completed"] == 1  # a da tela segue normal
    assert len(contexto) == 1 and "source=screen" in contexto[0]
    assert acordes == ["vision_screen_change"]


def test_desligar_limpa_cache_so_se_a_nota_veio_daquela_fonte(monkeypatch):
    import celia.core.cognitive_loop as cog

    limpou = []
    monkeypatch.setattr(cog.cognitive_loop, "clear_screen_context", lambda: limpou.append(1))
    analyzer = ScreenVisionAnalyzer()
    vision_cache.clear()
    vision_cache.put("editor", "timeline", "source=screen")
    analyzer._last_result = {"summary": "timeline"}
    analyzer._last_result_ts = 1.0
    analyzer._last_result_source = "screen"

    analyzer.clear_source("camera")  # a nota é da tela: nada some
    assert vision_cache.get()[0] is not None and not limpou
    analyzer.clear_source("screen")  # agora some, e o contexto cognitivo também
    assert vision_cache.get()[0] is None
    assert analyzer._last_result == {} and limpou == [1]


def test_desligar_pelo_registro_aciona_a_limpeza_do_singleton(monkeypatch):
    from celia.core.screen_vision import screen_vision

    chamado = []
    monkeypatch.setattr(screen_vision, "clear_source", lambda s, r="off": chamado.append((s, r)))
    vision_sources.subscribe(screen_vision._on_source_change)
    vision_sources.set_active("camera", True)
    vision_sources.set_active("camera", False)
    assert chamado == [("camera", "signal")]


# ── um frame = um pensamento ──────────────────────────────────────────────────
def test_wakes_de_tela_repetidos_sao_juntados(monkeypatch):
    monkeypatch.setenv("CELIA_COGNITIVE_SCREEN_WAKE_GAP", "6")
    loop = CognitiveLoop()
    loop._running = True
    loop._enabled = True
    assert loop.request_think("screen_change") is True
    assert loop.request_think("vision_screen_change") is False  # dentro da janela
    assert loop.request_think("low_participation_after_human_turn") is True  # outras razões seguem
    assert loop._coalesced_wakes >= 0


def test_clear_screen_context_esquece_o_resumo_visual():
    loop = CognitiveLoop()
    loop.set_screen_context("topic=jogo; summary=x")
    assert loop._fresh_screen_context()
    loop.clear_screen_context()
    assert loop._fresh_screen_context() == ""


# ── observabilidade ambiente ──────────────────────────────────────────────────
def test_emit_ambient_usa_trace_fixo_e_so_metadados_seguros():
    antes = observability.latest_sequence()
    ev = observability.emit_ambient(
        "vision.source", "on",
        {"source": "camera", "width": 640, "prompt": "NUNCA DEVE APARECER", "image": "x"},
    )
    assert ev["trace_id"] == "ambient" and ev["node_id"] == "vision.source"
    assert ev["metadata"] == {"source": "camera", "width": 640}
    assert any(e["sequence"] > antes for e in observability.snapshot(antes))
