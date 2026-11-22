"""Memória visual: URL local, OCR incremental, cena, perfis, sequência e avatar (2026-10-18).

Imagens são sintéticas (texto desenhado com Pillow), então o OCR real do tesseract é exercitado sem depender da tela do Pai.
Os controles negativos existem de propósito: um detector que dispara em tudo ou em nada não prova nada.
"""
import asyncio
import base64
import io
import shutil
import time

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont

import celia.core.cognitive_loop as cognitive_module
import celia.core.screen_vision as sv
from celia.core import visual_scene as vs
from celia.core.local_vision import LocalVisionClient, LocalVisionResult, chat_completions_url
from celia.core.vision_sources import vision_sources

FONT = "/usr/share/fonts/TTF/DejaVuSans.ttf"
needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract ausente")


def img(lines=(), box=None, bg=(30, 30, 40), size=(960, 540)):
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype(FONT, 34)
    except OSError:
        font = ImageFont.load_default()
    for i, text in enumerate(lines):
        d.text((50, 50 + i * 70), text, fill=(240, 240, 240), font=font)
    if box:
        d.rectangle(box, fill=(200, 60, 60))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# ───────────── URL local ─────────────

@pytest.mark.parametrize("configured", [
    "http://h:8031/v1/chat/completions", "http://h:8031/v1", "http://h:8031/v1/", "http://h:8031/v1/chat/completions/",
])
def test_url_local_nunca_duplica_chat_completions(configured):
    assert chat_completions_url(configured) == "http://h:8031/v1/chat/completions"


def test_url_vazia_nao_inventa_endereco():
    assert chat_completions_url("") == ""


async def test_cliente_local_chama_exatamente_a_url_configurada(monkeypatch):
    """Valor de secrets/.env (URL completa): o pedido sai em /v1/chat/completions UMA vez, com as imagens na ordem dada."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 7}})

    monkeypatch.setenv("CELIA_LOCAL_VISION_URL", "http://127.0.0.1:8031/v1/chat/completions")
    client = LocalVisionClient()
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await client.request(prompt="p", image=["data:image/jpeg;base64,AA==", "data:image/jpeg;base64,BB=="], max_tokens=5)
    assert seen["url"] == "http://127.0.0.1:8031/v1/chat/completions"
    urls = [c["image_url"]["url"] for c in seen["body"]["messages"][0]["content"] if c["type"] == "image_url"]
    assert urls == ["data:image/jpeg;base64,AA==", "data:image/jpeg;base64,BB=="]
    assert result.images == 2 and result.prompt_tokens == 7 and result.url == seen["url"]


async def test_erro_http_do_modelo_nunca_vira_descricao(monkeypatch):
    client = LocalVisionClient()
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404, text="rota inexistente")))
    from celia.core.local_vision import LocalVisionHTTPError

    with pytest.raises(LocalVisionHTTPError) as err:
        await client.request(prompt="p", image="data:image/jpeg;base64,AA==", max_tokens=5)
    assert err.value.status == 404 and "rota inexistente" in str(err.value)


def test_recusa_do_modelo_nao_e_descricao_da_tela():
    assert sv.ScreenVisionAnalyzer._parse('{"summary":"Desculpe, mas não posso ajudar com isso.","topic":"x","salience":0.5,"should_comment":false}') is None


# ───────────── OCR ─────────────

@needs_tesseract
def test_ocr_le_texto_legivel_com_posicao_e_confianca():
    from celia.core.visual_ocr import run_ocr_sync

    r = run_ocr_sync(img(["Resultados da pesquisa: Mario", "Super Mario Bros wiki"]))
    assert r.status == "ok" and r.ms > 0 and r.mean_conf and r.mean_conf > 60
    assert any("Mario" in line.text for line in r.lines)
    assert all(0 <= v <= 1 for line in r.lines for v in line.box)


@needs_tesseract
def test_ocr_de_tela_sem_texto_e_vazio_e_nao_inventa():
    from celia.core.visual_ocr import run_ocr_sync

    r = run_ocr_sync(img([], box=(100, 100, 700, 400)))
    assert r.status == "empty" and r.lines == []


def test_ocr_de_imagem_invalida_e_erro_explicito():
    from celia.core.visual_ocr import run_ocr_sync

    assert run_ocr_sync("data:image/jpeg;base64,AA==").status in {"error", "unavailable"}


def L(text, y=0.1):
    return vs.OcrLine(text, (0.1, y, 0.5, y + 0.05), 90.0)


def test_ocr_incremental_nao_repete_o_que_nao_mudou():
    d = vs.diff_ocr([L("Vida 10", .1), L("Dialogo: oi", .8)], [L("Vida 10", .1), L("Dialogo: oi", .8)])
    assert d.is_empty and d.unchanged == 2


def test_ocr_incremental_separa_novo_alterado_removido():
    prev = [L("Vida 10", .1), L("Dialogo: oi tudo bem", .8), L("Menu", .2)]
    cur = [L("Vida 10", .1), L("Dialogo: oi tudo certo", .8), L("Novo item", .5)]
    d = vs.diff_ocr(prev, cur)
    assert d.changed == (("Dialogo: oi tudo bem", "Dialogo: oi tudo certo"),)
    assert d.added == ("Novo item",) and d.removed == ("Menu",) and d.unchanged == 1


# ───────────── mudança de quadro ─────────────

def test_detector_distingue_estatico_texto_e_cena_nova():
    a = vs.signature_of(img(["Pesquisa: Mario", "Super Mario wiki"]))
    same = vs.compare(a, vs.signature_of(img(["Pesquisa: Mario", "Super Mario wiki"])))
    text = vs.compare(a, vs.signature_of(img(["Pesquisa: Luigi", "Super Mario wiki"])))
    scene = vs.compare(a, vs.signature_of(img([], box=(100, 100, 800, 500))))
    assert same.kind == vs.KIND_STATIC
    assert text.kind == vs.KIND_TEXT and text.global_diff < 0.01 and text.region is not None
    assert scene.kind in (vs.KIND_MAJOR, vs.KIND_BURST)


def test_grade_16x9_nao_enxergava_a_mudanca_de_uma_palavra():
    """Controle: com a grade antiga a mesma mudança fica abaixo do limiar de 0,075 que o navegador usava."""
    a, b = vs.signature_of(img(["Pesquisa: Mario"])), vs.signature_of(img(["Pesquisa: Luigi"]))
    assert vs.compare(a, b).global_diff < 0.075 and vs.compare(a, b).kind != vs.KIND_STATIC


def test_imagem_que_nao_decodifica_nao_gera_assinatura():
    assert vs.signature_of("data:image/jpeg;base64,AA==") is None


# ───────────── buffer e sequência ─────────────

def frame_ring(n, **kw):
    ring = vs.FrameRing(**kw)
    for k in range(n):
        image = img([], box=(40 + k * 120, 100, 140 + k * 120, 200))
        ring.push("screen", image, vs.signature_of(image), now=100.0 + k * 0.5, kind=vs.KIND_LOCAL)
    return ring


def test_buffer_circular_respeita_limite_de_quadros_e_bytes():
    ring = frame_ring(10, max_frames=4)
    assert len(ring) == 4 and ring.evicted == 6
    small = frame_ring(10, max_frames=50, max_bytes=60_000)
    assert small.bytes_used() <= 60_000 or len(small) == 1


def test_buffer_expira_por_idade_e_descarta_redundantes():
    ring = frame_ring(4, max_age_s=1.0)
    ring._expire(now=110.0)
    assert len(ring) == 0
    ring = vs.FrameRing()
    image = img(["x"])
    sig = vs.signature_of(image)
    assert ring.push("screen", image, sig, 1.0, kind=vs.KIND_MAJOR) is not None   # o primeiro quadro entra
    assert ring.push("screen", image, sig, 1.5, kind=vs.KIND_STATIC) is None and ring.dropped_redundant == 1
    assert len(ring) == 1


def test_sequencia_preserva_ordem_timestamps_e_limite():
    ring = frame_ring(6)
    frames = ring.window("screen", now=103.0, limit=3, before_s=10)
    assert 2 <= len(frames) <= 3
    assert [f.at for f in frames] == sorted(f.at for f in frames)
    assert frames[-1].frame_id == 6  # o mais novo nunca fica de fora
    timeline = vs.sequence_timeline(frames)
    assert [t["t_rel_s"] for t in timeline] == sorted(t["t_rel_s"] for t in timeline) and timeline[-1]["t_rel_s"] == 0.0
    assert "change_from_previous" in timeline[-1]


def test_janela_de_um_quadro_so_nao_vira_sequencia():
    ring = frame_ring(1)
    assert len(ring.window("screen", now=100.2, limit=3)) == 1
    prompt = vs.build_prompt(vs.PROFILE_STATIC, frame_count=1)
    assert "MOVIMENTO" not in prompt


# ───────────── perfis ─────────────

def change(kind, g=0.05):
    return vs.FrameChange(kind, g, g, (1,), 0.5, (0.1, 0.1, 0.3, 0.3))


def test_perfil_conservador_na_duvida_e_automatico_pelos_sinais():
    assert vs.classify_profile(burst_recent=0, text_changes_recent=0, major_recent=0, has_scene=True, explicit=False)[0] == vs.PROFILE_STATIC
    assert vs.classify_profile(burst_recent=0, text_changes_recent=3, major_recent=0, has_scene=True, explicit=False)[0] == vs.PROFILE_GAME
    assert vs.classify_profile(burst_recent=2, text_changes_recent=0, major_recent=0, has_scene=True, explicit=False)[0] == vs.PROFILE_MOTION
    assert vs.classify_profile(burst_recent=0, text_changes_recent=0, major_recent=0, has_scene=True, explicit=True)[0] == vs.PROFILE_DETAIL


def test_decisoes_por_perfil():
    assert vs.decide(vs.PROFILE_GAME, change(vs.KIND_LOCAL), has_scene=True, scene_age_s=3, explicit=False).action == vs.ACTION_OCR_ONLY
    assert vs.decide(vs.PROFILE_STATIC, change(vs.KIND_LOCAL), has_scene=True, scene_age_s=3, explicit=False).action == vs.ACTION_VLM
    assert vs.decide(vs.PROFILE_STATIC, change(vs.KIND_TEXT), has_scene=True, scene_age_s=3, explicit=False).action == vs.ACTION_OCR_ONLY
    assert vs.decide(vs.PROFILE_STATIC, change(vs.KIND_STATIC, 0.0), has_scene=True, scene_age_s=3, explicit=False).action == vs.ACTION_SKIP
    assert vs.decide(vs.PROFILE_MOTION, change(vs.KIND_BURST, .4), has_scene=True, scene_age_s=3, explicit=False).action == vs.ACTION_SEQUENCE
    assert vs.decide(vs.PROFILE_STATIC, change(vs.KIND_BURST, .4), has_scene=True, scene_age_s=3, explicit=False).action == vs.ACTION_VLM
    assert vs.decide(vs.PROFILE_STATIC, change(vs.KIND_MAJOR, .2), has_scene=True, scene_age_s=3, explicit=False).action == vs.ACTION_VLM
    assert vs.decide(vs.PROFILE_STATIC, change(vs.KIND_TEXT), has_scene=False, scene_age_s=None, explicit=False).action == vs.ACTION_VLM
    assert vs.decide(vs.PROFILE_STATIC, change(vs.KIND_STATIC, 0.0), has_scene=True, scene_age_s=999, explicit=False).action == vs.ACTION_VLM
    assert vs.decide(vs.PROFILE_STATIC, change(vs.KIND_STATIC, 0.0), has_scene=False, scene_age_s=None, explicit=False).action == vs.ACTION_SKIP


# ───────────── estado da cena ─────────────

def analysis(name="Mario", conf=0.9, evidence=("chapeu vermelho",), summary="Jogo aberto"):
    return {"summary": summary, "topic": "jogo", "entities": [{"name": name, "kind": "personagem", "confidence": conf,
                                                               "evidence": list(evidence), "uncertain": False}]}


def apply(mem, frame, **extra):
    return mem.apply_analysis(extra.pop("data", analysis()), now=extra.pop("now", 10.0), frame_id=frame, profile=vs.PROFILE_GAME,
                              reason="t", backend="local_vlm", source="screen", change=None, state_version=0)


def test_resultado_de_quadro_antigo_nao_sobrescreve_a_cena_mais_nova():
    mem = vs.SceneMemory()
    assert apply(mem, 5, data=analysis(summary="cena nova"))
    assert apply(mem, 3, data=analysis(summary="cena velha")) is False
    assert mem.state.summary == "cena nova" and mem.state.last_frame_id == 5
    assert any(h["kind"] == "stale_result" for h in mem.history)


def test_analise_valida_de_quadro_ainda_novo_e_aplicada_mesmo_com_outra_agendada():
    mem = vs.SceneMemory()
    assert apply(mem, 4) and apply(mem, 4, data=analysis(summary="outra")) and mem.state.summary == "outra"


def test_nome_escrito_na_tela_e_pista_nao_personagem_confirmado():
    mem = vs.SceneMemory()
    mem.update_ocr([L("Pesquisa: Mario")], status="ok", now=1.0)
    apply(mem, 1, data=analysis("Mario", 0.95, evidence=("pista visual 1",)))
    e = mem.state.entities["mario"]
    assert e.confidence <= 0.4 and e.uncertain and "nome aparece escrito na tela (OCR)" in e.evidence


def test_personagem_com_pista_visual_e_sem_nome_no_ocr_mantem_confianca():
    mem = vs.SceneMemory()
    mem.update_ocr([L("Dialogo qualquer")], status="ok", now=1.0)
    apply(mem, 1, data=analysis("Mario", 0.9, evidence=("chapeu vermelho com M",)))
    e = mem.state.entities["mario"]
    assert e.confidence == 0.9 and not e.uncertain


def test_mudanca_grande_invalida_o_personagem_anterior():
    mem = vs.SceneMemory()
    apply(mem, 1)
    assert "mario" in mem.state.entities
    mem.invalidate("cena nova", 20.0, hard=True)
    assert mem.state.entities == {} and mem.state.summary == "" and mem.state.needs_reevaluation == ["scene"]


def test_entidade_antiga_expira_e_memoria_tem_teto():
    mem = vs.SceneMemory(history_max=3, ocr_lines_max=2)
    apply(mem, 1, now=0.0)
    mem.expire(now=vs.ENTITY_TTL_S + 1)
    assert mem.state.entities == {}
    mem.update_ocr([L(f"linha {i}", i / 10) for i in range(9)], status="ok", now=1.0)
    assert len(mem.state.ocr_lines) == 2
    for i in range(10):
        mem._log("x", float(i), {}, i)
    assert len(mem.history) == 3


# ───────────── avatar da Célia ─────────────

def scene_with(entity=None, ocr=()):
    st = vs.SceneState()
    if entity:
        st.entities[vs._norm_text(entity.name)] = entity
    st.ocr_lines = [L(t) for t in ocr]
    return st


def test_avatar_so_com_evidencia_visual_e_confianca():
    ok = scene_with(vs.Entity("Célia", "avatar", 0.9, ["cabelo azul e orelhas de gato"], vs.SOURCE_VISUAL, 1.0))
    assert vs.celia_presence(ok)["verdict"] == vs.AVATAR_UNCONFIRMED  # generic visual clues are not reference verification
    fraco = scene_with(vs.Entity("Célia", "avatar", 0.4, [], vs.SOURCE_VISUAL, 1.0, uncertain=True))
    assert vs.celia_presence(fraco)["verdict"] == vs.AVATAR_UNCONFIRMED


def test_avatar_nome_escrito_nao_e_avatar():
    assert vs.celia_presence(scene_with(None, ocr=["Pesquisa: Célia, a IA do criador"]))["verdict"] == vs.AVATAR_MENTION


def test_avatar_referencia_externa_nao_e_o_avatar():
    ext = scene_with(vs.Entity("Célia", "imagem", 0.9, ["arte numa pagina de pesquisa"], vs.SOURCE_VISUAL, 1.0))
    assert vs.celia_presence(ext)["verdict"] == vs.AVATAR_EXTERNAL


def test_avatar_sem_nada_e_nao_confirmado():
    assert vs.celia_presence(scene_with())["verdict"] == vs.AVATAR_UNCONFIRMED


def test_controle_negativo_do_avatar_barra_modelo_que_diz_sim_a_tudo():
    """Medido: o VLM de 1.6B respondeu 'yes' ao retrato oficial E ao impostor. Esse 'yes' não discrimina e não confirma."""
    yes = {"present": "yes", "external_reference": False, "confidence": 0.9, "evidence": ["parece"]}
    assert vs.gate_avatar_check(yes, dict(yes))["present"] == "unsure"
    no = {"present": "no", "external_reference": False, "confidence": 0.1, "evidence": []}
    assert vs.gate_avatar_check(yes, no)["present"] == "yes"
    st = vs.SceneState()
    verdict = vs.apply_avatar_check(st, vs.gate_avatar_check(yes, dict(yes)), 1.0)
    assert verdict["verdict"] == vs.AVATAR_UNCONFIRMED


def test_avatar_yes_sem_evidencia_nunca_confirma():
    st = vs.SceneState()
    verdict = vs.apply_avatar_check(st, {"present": "yes", "external_reference": False, "confidence": 0.95, "evidence": []}, 1.0)
    assert verdict["verdict"] == vs.AVATAR_UNCONFIRMED


# ───────────── pipeline completo (OCR real + VLM falso) ─────────────

class _Sched:
    enabled = True

    async def run_job(self, kind, fn, **kw):
        return await fn()

    def debug_snapshot(self):
        return {}


@pytest.fixture
def pipeline(monkeypatch):
    monkeypatch.setenv("CELIA_VISION_BACKEND", "local")
    monkeypatch.setenv("CELIA_VISION_MIN_INTERVAL", "0.25")
    monkeypatch.setenv("CELIA_VISION_OCR_MIN_INTERVAL", "0.0")
    calls, delivered = [], []

    async def fake_request(**kw):
        calls.append(kw)
        n = len(calls)
        return LocalVisionResult(
            raw='{"summary":"Jogo com personagem %d","topic":"jogo","salience":0.8,"should_comment":false,"entities":'
                '[{"name":"Heroi","kind":"personagem","confidence":0.8,"evidence":["capa vermelha"],"uncertain":false}]}' % n,
            model="fake", latency_ms=5, url="http://x/v1/chat/completions", images=len(kw["image"]) if isinstance(kw["image"], list) else 1)

    monkeypatch.setattr(sv, "gpu_scheduler", _Sched())
    monkeypatch.setattr(sv.local_vision_client, "request", fake_request)
    monkeypatch.setattr(cognitive_module.cognitive_loop, "set_screen_context", lambda t: delivered.append(t))
    monkeypatch.setattr(cognitive_module.cognitive_loop, "request_think", lambda r: True)
    vision_sources.set_active("screen", True)
    analyzer = sv.ScreenVisionAnalyzer()
    yield analyzer, calls, delivered
    vision_sources.set_active("screen", False)


async def run(analyzer, image, *, relevance=0.9, wait=True):
    analyzer.remember_frame(image, "screen")
    analyzer._last_started = 0.0
    scheduled = analyzer.schedule(image, relevance=relevance, source="screen")
    for _ in range(200):
        await asyncio.sleep(0.05)
        if not scheduled or (analyzer._in_flight == 0 and analyzer._pending == {} and not analyzer._tasks_busy()):
            break
    return scheduled


@needs_tesseract
async def test_personagem_reconhecido_uma_vez_e_fala_mudando_depois_nao_reanalisa_a_cena(pipeline):
    analyzer, calls, delivered = pipeline
    base = ["Heroi: oi, tudo bem com voce?", "Vila de Pallet"]
    await run(analyzer, img(base))                                # 1º quadro: sem referência, nada agenda
    assert calls == []
    await run(analyzer, img(["Cena nova aqui", "Outra linha"], box=(500, 300, 900, 500)))   # mudança grande: 1ª análise
    assert len(calls) == 1 and analyzer.scene.state.entities
    first_summary = analyzer.scene.state.summary
    delivered.clear()
    await run(analyzer, img(["Cena nova aqui", "Linha trocada"], box=(500, 300, 900, 500)))  # só o texto muda
    assert len(calls) == 1, "mudança de fala não pode gerar nova análise integral"
    assert analyzer.metrics["ocr_only"] == 1 and analyzer.scene.state.summary == first_summary
    assert delivered and "texto_ocr_mudou" in delivered[0] and first_summary in delivered[0]
    assert analyzer.last_run["ocr_diff"]["added"] or analyzer.last_run["ocr_diff"]["changed"]


@needs_tesseract
async def test_tela_parada_nao_chama_ocr_nem_modelo(pipeline):
    analyzer, calls, _ = pipeline
    frame = img(["Texto fixo", "mais texto"], box=(500, 300, 900, 500))
    await run(analyzer, frame)
    await run(analyzer, frame)
    await run(analyzer, frame)
    assert calls == [] and analyzer.metrics["skipped_static"] >= 1 and analyzer.metrics["ocr_only"] == 0


@needs_tesseract
async def test_mudanca_real_de_cena_invalida_o_que_foi_reconhecido(pipeline):
    analyzer, calls, _ = pipeline
    await run(analyzer, img(["Tela um"], box=(40, 40, 400, 300)))
    await run(analyzer, img(["Tela dois"], box=(500, 300, 900, 500)))
    assert "heroi" in analyzer.scene.state.entities
    analyzer.remember_frame(img([], bg=(230, 230, 230)), "screen")   # tela completamente diferente
    assert analyzer.scene.state.entities == {} and analyzer.scene.state.needs_reevaluation == ["scene"]
    assert any(h["kind"] == "scene_reset" for h in analyzer.scene.history)


@needs_tesseract
async def test_ocr_falha_nao_derruba_e_o_modelo_e_mandado_conferir_na_imagem(pipeline, monkeypatch):
    analyzer, calls, delivered = pipeline
    from celia.core.visual_ocr import OcrResult

    async def broken(image, region=None, timeout=None):
        return OcrResult([], "error", 12, "tesseract: falha simulada", region)

    monkeypatch.setattr(sv, "run_ocr", broken)
    await run(analyzer, img(["A"], box=(40, 40, 400, 300)))
    await run(analyzer, img(["B"], box=(500, 300, 900, 500)))
    assert len(calls) == 1 and "falhou" in calls[0]["prompt"] and "Confira o texto diretamente na imagem" in calls[0]["prompt"]
    assert analyzer.scene.state.ocr_status == "error" and "falha simulada" in analyzer.scene.state.ocr_error
    assert delivered, "mesmo sem OCR a descrição visual válida chega à conversa"


@needs_tesseract
async def test_resposta_visual_valida_chega_a_conversa_com_diagnostico(pipeline):
    analyzer, calls, delivered = pipeline
    await run(analyzer, img(["A"], box=(40, 40, 400, 300)))
    await run(analyzer, img(["B"], box=(500, 300, 900, 500)))
    assert delivered and "Jogo com personagem 1" in delivered[0]
    run_info = analyzer.visual_snapshot()["last_run"]
    assert run_info["backend"] == "local" and run_info["url"] == "http://x/v1/chat/completions"
    assert run_info["frames"] == 1 and run_info["ocr_status"] in {"ok", "empty"} and run_info["delivered_chars"] > 0
    assert run_info["discard"] is None and analyzer.visual_snapshot()["ring"]["frames"] >= 2


async def test_fonte_de_tela_desligada_nao_captura_nem_analisa(pipeline, monkeypatch):
    analyzer, calls, _ = pipeline
    vision_sources.set_active("screen", False)
    ocr_calls = []

    async def spy(*a, **k):
        ocr_calls.append(1)

    monkeypatch.setattr(sv, "run_ocr", spy)
    assert analyzer.schedule(img(["x"]), relevance=1.0, source="screen") is False
    await asyncio.sleep(0.05)
    assert calls == [] and ocr_calls == [] and analyzer.metrics["skipped_source_off"] == 1 and len(analyzer.ring) == 0


@needs_tesseract
async def test_mudanca_importante_barrada_por_cooldown_e_reagendada(pipeline, monkeypatch):
    analyzer, calls, _ = pipeline
    await run(analyzer, img(["A"], box=(40, 40, 400, 300)))
    await run(analyzer, img(["B"], box=(500, 300, 900, 500)))
    assert len(calls) == 1
    monkeypatch.setenv("CELIA_VISION_MIN_INTERVAL", "0.3")
    novo = img(["C"], box=(100, 350, 300, 520), bg=(60, 120, 60))
    analyzer.remember_frame(novo, "screen")
    analyzer._last_started = __import__("time").monotonic()   # análise acabou de começar: cooldown
    assert analyzer.schedule(novo, relevance=0.9, source="screen") is False
    assert "screen" in analyzer._pending and analyzer.metrics["skipped_cooldown"] == 1
    for _ in range(60):
        await asyncio.sleep(0.1)
        if len(calls) == 2:
            break
    assert len(calls) == 2 and analyzer.metrics["followup_scheduled"] == 1, "a mudança importante não foi perdida"


@needs_tesseract
async def test_sequencia_de_movimento_manda_quadros_em_ordem(pipeline, monkeypatch):
    analyzer, calls, _ = pipeline
    monkeypatch.setenv("CELIA_VISION_PROFILE", "motion_video")
    monkeypatch.setenv("CELIA_VISION_SEQ_FRAMES", "3")
    await run(analyzer, img([], box=(40, 100, 140, 200)))
    for k in range(1, 4):
        await run(analyzer, img([], box=(40 + k * 220, 100, 140 + k * 220, 200)))
    seq = [c for c in calls if isinstance(c["image"], list)]
    assert seq, "perfil movimento deveria enviar sequência"
    assert 2 <= len(seq[-1]["image"]) <= 3 and "ORDEM CRONOLÓGICA" in seq[-1]["prompt"]
    assert analyzer.metrics["sequences"] >= 1 and analyzer.last_run["frames"] == len(seq[-1]["image"])


# ───────────── análise explícita para as tools da conversa (teste 19) ─────────────

@needs_tesseract
async def test_analyze_now_devolve_cena_ocr_e_incerteza_separados(pipeline):
    analyzer, calls, _ = pipeline
    analyzer.remember_frame(img(["Pesquisa: Mario", "Super Mario Bros"]), "screen")
    result = await analyzer.analyze_now("screen", timeout=15)
    assert result["ok"] is True and result["source"] == "screen"
    assert result["summary"].startswith("Jogo com personagem") and result["entities"]
    assert any("Mario" in line for line in result["ocr_lines"]), "OCR vem separado do que o modelo descreveu"
    assert result["backend"] == "local_vlm" and len(calls) >= 1 and result["frame_age_ms"] >= 0


async def test_analyze_now_recusa_fonte_desligada_ou_sem_quadro(pipeline):
    analyzer, calls, _ = pipeline
    assert (await analyzer.analyze_now("screen"))["ok"] is False        # ligada, mas sem quadro
    vision_sources.set_active("screen", False)
    analyzer.remember_frame(img(["x"]), "screen")
    off = await analyzer.analyze_now("screen")
    assert off["ok"] is False and "nenhuma fonte" in off["error"] and calls == []


async def test_explicit_description_does_not_wait_for_streaming_ocr(pipeline, monkeypatch):
    analyzer, _, _ = pipeline
    analyzer.remember_frame(img(["current screen"]), "screen")
    analyzer._ocr_inflight = 1

    def complete(*args, **kwargs):
        analyzer.scene.state.version += 1
        analyzer.scene.state.summary = "Current visible screen"
        analyzer._last_result = {"summary": "Current visible screen"}
        analyzer._last_result_source = "screen"
        analyzer._last_result_ts = time.monotonic()
        return True

    monkeypatch.setattr(analyzer, "schedule", complete)
    started = time.monotonic()
    result = await analyzer.analyze_now("screen", timeout=2)
    assert result["ok"] and time.monotonic() - started < .6
    assert analyzer._ocr_inflight == 1


async def test_explicit_description_has_one_total_deadline(pipeline, monkeypatch):
    analyzer, _, _ = pipeline
    analyzer.remember_frame(img(["current screen"]), "screen")
    analyzer._in_flight = 1
    monkeypatch.setattr(analyzer, "schedule", lambda *a, **kw: pytest.fail("must not duplicate busy inference"))
    result = await analyzer.analyze_now("screen", timeout=.1)
    assert result == {"ok": False, "error": "analysis_busy_timeout"}


async def test_explicit_description_reuses_fresh_result_while_other_jobs_run(pipeline, monkeypatch):
    analyzer, _, _ = pipeline
    analyzer.remember_frame(img(["current screen"]), "screen")
    analyzer._in_flight = 2
    analyzer._last_result = {"summary": "Visible current scene"}
    analyzer._last_result_source = "screen"
    analyzer._last_result_ts = time.monotonic()
    analyzer.scene.state.summary = "Visible current scene"
    monkeypatch.setattr(analyzer, "schedule", lambda *a, **kw: pytest.fail("do not duplicate fresh inference"))
    assert (await analyzer.analyze_now("screen", timeout=.1))["ok"]


def test_tool_de_visao_monta_texto_com_cena_ocr_e_aviso_de_incerteza(monkeypatch):
    import importlib.util, sys as _sys, json as _json
    from pathlib import Path

    mcp_dir = Path(__file__).resolve().parents[1] / "mcp"
    _sys.path.insert(0, str(mcp_dir))
    try:
        spec = importlib.util.spec_from_file_location("vision_mcp_server_teste", mcp_dir / "vision_mcp_server.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        _sys.path.remove(str(mcp_dir))
    payload = {"ok": True, "source": "camera", "profile": "static_interface", "frame_age_ms": 420, "elapsed_ms": 1500, "summary": "Folha de sprites de pixel art.",
               "entities": [{"name": "Mario", "uncertain": True, "confidence": 0.4, "evidence": ["nome aparece escrito na tela (OCR)"]}],
               "ocr_lines": ["VectorStock"], "celia_presence": {"verdict": "unconfirmed"}}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return _json.dumps(payload).encode()

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: Resp())
    text = mod._analise_da_cena_pelo_servidor()
    assert "O QUE O MODELO VIU: Folha de sprites" in text and "Mario (INCERTO 0.4" in text
    assert "TEXTO LIDO PELO OCR" in text and "VectorStock" in text and "idade do quadro=420ms" in text
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("fora")))
    with pytest.raises(mod.VisionUnavailable):
        mod._analise_da_cena_pelo_servidor()
    payload["ok"] = False
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: Resp())
    with pytest.raises(mod.VisionUnavailable):
        mod._analise_da_cena_pelo_servidor()


@needs_tesseract
async def test_analyze_now_ocr_only_le_o_texto_sem_chamar_o_modelo(pipeline):
    analyzer, calls, _ = pipeline
    analyzer.remember_frame(img(["SERVIDOR: ONLINE", "USUARIOS: 24"], size=(320, 240)), "screen")
    result = await analyzer.analyze_now("screen", ocr_only=True)
    assert result["ok"] and calls == []
    assert any("SERVIDOR" in l for l in result["ocr_lines"]) and result["ocr_conf"] and len(result["ocr_boxes"]) == len(result["ocr_lines"])
    analyzer.remember_frame(img(["SERVIDOR: OFFLINE", "USUARIOS: 24"], size=(320, 240)), "screen")
    again = await analyzer.analyze_now("screen", ocr_only=True)
    assert again["changes"]["changed"] or again["changes"]["added"], "a releitura reporta o que mudou desde o OCR anterior"


def test_tool_de_ocr_usa_o_servidor_e_cai_no_antigo_se_ele_falhar(monkeypatch):
    import importlib.util, sys as _sys, json as _json
    from pathlib import Path

    mcp_dir = Path(__file__).resolve().parents[1] / "mcp"
    _sys.path.insert(0, str(mcp_dir))
    try:
        spec = importlib.util.spec_from_file_location("vision_mcp_server_teste2", mcp_dir / "vision_mcp_server.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        _sys.path.remove(str(mcp_dir))

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return _json.dumps({"ok": True, "source": "camera", "ocr_ms": 400, "ocr_conf": 91.0, "ocr_lines": ["SERVIDOR: ONLINE", "USUARIOS: 24"]}).encode()

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: Resp())
    text = mod._ocr_pelo_servidor()
    assert "SERVIDOR: ONLINE\nUSUARIOS: 24" in text and "confianca media=91.0" in text
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("fora")))
    with pytest.raises(mod.VisionUnavailable):
        mod._ocr_pelo_servidor()


async def test_cliente_local_tenta_de_novo_uma_unica_vez_no_500_transitorio(monkeypatch):
    monkeypatch.setenv("CELIA_LOCAL_VISION_RETRY_DELAY", "0")
    estados = {"n": 0}

    def handler(request):
        estados["n"] += 1
        if estados["n"] == 1:
            return httpx.Response(500, json={"error": {"message": "failed to process mtmd chunk"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = LocalVisionClient()
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert (await client.request(prompt="p", image="data:image/jpeg;base64,AA==", max_tokens=5)).raw == "ok" and estados["n"] == 2
    # servidor realmente quebrado: 2ª tentativa também falha => erro explícito, sem laço
    estados["n"] = 0
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: (estados.__setitem__("n", estados["n"] + 1) or httpx.Response(500, text="quebrado"))))
    from celia.core.local_vision import LocalVisionHTTPError

    with pytest.raises(LocalVisionHTTPError):
        await client.request(prompt="p", image="data:image/jpeg;base64,AA==", max_tokens=5)
    assert estados["n"] == 2


def test_timeout_local_7_5s_por_padrao_e_override(monkeypatch):
    """Regressão do limite de espera sem iniciar outro servidor VLM."""
    monkeypatch.delenv("CELIA_LOCAL_VISION_TIMEOUT", raising=False)
    assert LocalVisionClient().timeout == 7.5
    monkeypatch.setenv("CELIA_LOCAL_VISION_TIMEOUT", "9.0")
    assert LocalVisionClient().timeout == 9.0
    monkeypatch.setenv("CELIA_LOCAL_VISION_TIMEOUT", "invalido")
    assert LocalVisionClient().timeout == 7.5
