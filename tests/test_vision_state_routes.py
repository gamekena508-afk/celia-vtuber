"""Rotas /bridge/vision/state e /bridge/vision/frame com tela e câmera separadas."""
import base64
import io

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from celia import bridge_routes
from celia.core.continuous_behavior import ContinuousBehaviorRuntime
from celia.core.vision_sources import vision_sources


def jpeg() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (30, 80, 140)).save(buf, format="JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    behavior = ContinuousBehaviorRuntime(tmp_path / "profile.json")
    behavior.select_profile("cohost")
    monkeypatch.setattr(bridge_routes, "continuous_behavior", behavior)
    app = FastAPI()
    app.include_router(bridge_routes.init_bridge_routes(None))
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def estado(c, source, active, **kw):
    r = await c.post("/bridge/vision/state", json={"source": source, "active": active, **kw})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_frame_com_fonte_desligada_e_recusado_e_nada_e_guardado(cliente):
    async with cliente as c:
        r = await c.post("/bridge/vision/frame", json={"image": jpeg(), "source": "screen"})
        assert r.status_code == 200
        corpo = r.json()
        assert corpo["accepted"] is False and corpo["reason"] == "source_inactive"
        got = await c.get("/bridge/vision/frame")
        assert got.status_code == 404 and got.json()["detail"] == "visao_desligada"


@pytest.mark.asyncio
async def test_ligar_so_a_tela_nao_liga_a_camera(cliente):
    async with cliente as c:
        out = await estado(c, "screen", True, width=1280, height=720)
        assert out["active"] is True and out["changed"] is True
        ok = await c.post("/bridge/vision/frame", json={"image": jpeg(), "source": "screen"})
        assert ok.json()["accepted"] is True
        cam = await c.post("/bridge/vision/frame", json={"image": jpeg(), "source": "camera"})
        assert cam.json()["accepted"] is False
        snap = (await c.get("/bridge/vision/state")).json()["sources"]
        assert snap["screen"]["active"] and not snap["camera"]["active"]


@pytest.mark.asyncio
async def test_desligar_uma_fonte_apaga_o_frame_dela_e_preserva_o_da_outra(cliente):
    async with cliente as c:
        await estado(c, "screen", True)
        await estado(c, "camera", True)
        await c.post("/bridge/vision/frame", json={"image": jpeg(), "source": "screen"})
        await c.post("/bridge/vision/frame", json={"image": jpeg(), "source": "camera"})
        await estado(c, "screen", False)
        so_tela = await c.get("/bridge/vision/frame", params={"source": "screen"})
        assert so_tela.status_code == 404 and so_tela.json()["detail"] == "visao_desligada"
        resto = await c.get("/bridge/vision/frame")  # sem source: o que estiver ligado
        assert resto.status_code == 200 and resto.json()["source"] == "camera"
        await estado(c, "camera", False)
        tudo_off = await c.get("/bridge/vision/frame")
        assert tudo_off.status_code == 404 and tudo_off.json()["detail"] == "visao_desligada"


@pytest.mark.asyncio
async def test_ligada_sem_frame_ainda_diz_sem_frame(cliente):
    async with cliente as c:
        await estado(c, "camera", True)
        r = await c.get("/bridge/vision/frame")
        assert r.status_code == 404 and r.json()["detail"] == "sem_frame"


@pytest.mark.asyncio
async def test_fonte_desconhecida_e_422(cliente):
    async with cliente as c:
        r = await c.post("/bridge/vision/state", json={"source": "microfone", "active": True})
        assert r.status_code == 422
        r = await c.post("/bridge/vision/frame", json={"image": jpeg(), "source": "microfone"})
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_frame_relevante_acorda_um_pensamento_so(cliente, monkeypatch):
    """Visão agendada: quem acorda é a visão (ao terminar). Não agendada: acorda o frame."""
    from celia.core.cognitive_loop import cognitive_loop
    from celia.core.screen_vision import screen_vision

    acordes = []
    monkeypatch.setattr(cognitive_loop, "request_think", lambda r: acordes.append(r) or True)
    agendou = {"valor": True}
    monkeypatch.setattr(
        screen_vision, "schedule", lambda image, **kw: agendou["valor"]
    )
    async with cliente as c:
        await estado(c, "screen", True)
        r = await c.post(
            "/bridge/vision/frame",
            json={"image": jpeg(), "source": "screen", "relevance": 0.9},
        )
        assert r.json()["screen"]["action"] == "candidate"
        assert r.json()["vision_scheduled"] is True
        assert acordes == []  # a visão acordará o loop com o resumo novo

        agendou["valor"] = False
        await c.post(
            "/bridge/vision/frame",
            json={"image": jpeg(), "source": "screen", "relevance": 0.9},
        )
        assert acordes == ["screen_change"]


@pytest.mark.asyncio
async def test_frame_inalterado_nunca_agenda_nem_acorda(cliente, monkeypatch):
    from celia.core.cognitive_loop import cognitive_loop
    from celia.core.screen_vision import screen_vision

    acordes, agendados = [], []
    monkeypatch.setattr(cognitive_loop, "request_think", lambda r: acordes.append(r) or True)
    monkeypatch.setattr(
        screen_vision, "schedule", lambda image, **kw: agendados.append(1) or True
    )
    async with cliente as c:
        await estado(c, "screen", True)
        for _ in range(5):
            r = await c.post(
                "/bridge/vision/frame",
                json={"image": jpeg(), "source": "screen", "unchanged": True},
            )
            assert r.json()["accepted"] is True
    assert acordes == [] and agendados == []
