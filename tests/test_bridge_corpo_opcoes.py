import asyncio

import httpx
from fastapi import FastAPI

from celia import bridge_routes


def _chamar(metodo, corpo=None, cliente=("testclient", 50000)):
    async def run():
        app = FastAPI()
        app.include_router(bridge_routes.init_bridge_routes(None))
        transport = httpx.ASGITransport(app=app, client=cliente)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            if metodo == "GET":
                return await c.get("/bridge/corpo-opcoes")
            return await c.post("/bridge/corpo-opcoes", json=corpo)

    return asyncio.run(run())


def test_padrao_sem_arquivo(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    assert _chamar("GET").json() == {"oculos": False, "slime": False, "hud_minecraft": False, "modo_minecraft": True, "enquete_mobile": False, "chat_tiktok": True, "boca": 0.0, "audio_source": "window"}


def test_grava_so_o_que_mudou_e_persiste(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    assert _chamar("POST", {"slime": True}).json()["slime"] is True
    r = _chamar("POST", {"oculos": True}).json()
    assert r["oculos"] is True and r["slime"] is True  # o POST anterior nao foi apagado
    assert _chamar("GET").json()["slime"] is True
    assert not list(tmp_path.glob("*.tmp"))


def test_boca_fica_em_memoria_e_expira(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    assert _chamar("POST", {"boca": 0.7}).json()["boca"] == 0.7
    assert not (tmp_path / "celia_corpo_opcoes.json").exists()  # boca nao vai para o disco
    bridge_routes._BOCA_SLIME["em"] -= 5
    assert _chamar("GET").json()["boca"] == 0.0  # velha demais: silencio


def test_valor_invalido_e_rede_externa_recusados(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    assert _chamar("POST", {"boca": 2}).status_code == 422
    assert _chamar("POST", {"oculos": "talvez"}).status_code == 422
    assert _chamar("GET", cliente=("203.0.113.9", 4000)).status_code == 403
    assert _chamar("POST", {"slime": True}, cliente=("203.0.113.9", 4000)).status_code == 403


def test_arquivo_antigo_sem_hud_migra_sem_perder_oculos_e_slime(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    (tmp_path / "celia_corpo_opcoes.json").write_text('{"oculos": true, "slime": true}', encoding="utf-8")
    assert _chamar("GET").json() == {"oculos": True, "slime": True, "hud_minecraft": False, "modo_minecraft": True, "enquete_mobile": False, "chat_tiktok": True, "boca": 0.0, "audio_source": "window"}  # arquivo antigo: Modo Minecraft herda ligado
    r = _chamar("POST", {"hud_minecraft": True}).json()
    assert r["hud_minecraft"] is True and r["oculos"] is True and r["slime"] is True  # ligar a HUD nao mexe nas outras opcoes
    r = _chamar("POST", {"oculos": False}).json()
    assert r["hud_minecraft"] is True and r["oculos"] is False and r["slime"] is True  # e mexer nelas nao mexe na HUD


def test_hud_e_um_unico_booleano_nao_existem_chaves_por_painel(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    for chave in ("inventario", "mapa", "bussola", "inventario_enabled", "mapa_enabled", "bussola_enabled"):
        _chamar("POST", {chave: True})  # campo desconhecido e ignorado, nunca persistido
    assert not (tmp_path / "celia_corpo_opcoes.json").exists()
    assert set(_chamar("GET").json()) == {"oculos", "slime", "hud_minecraft", "modo_minecraft", "enquete_mobile", "chat_tiktok", "boca", "audio_source"}
    _chamar("POST", {"hud_minecraft": True})
    assert set(__import__("json").loads((tmp_path / "celia_corpo_opcoes.json").read_text())) == {"oculos", "slime", "hud_minecraft", "modo_minecraft", "enquete_mobile", "chat_tiktok"}
    assert _chamar("POST", {"hud_minecraft": "talvez"}).status_code == 422


def test_voz_e_interruptor_escrevem_o_mesmo_estado_e_repetir_e_idempotente(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    assert bridge_routes.definir_hud_minecraft(True) == {"hud_minecraft": True, "changed": True, "confirmed": True}
    assert _chamar("GET").json()["hud_minecraft"] is True  # o interruptor do Corpo le o que a voz gravou
    assert bridge_routes.definir_hud_minecraft(True) == {"hud_minecraft": True, "changed": False, "confirmed": True}
    _chamar("POST", {"hud_minecraft": False})  # o interruptor desliga...
    assert bridge_routes.definir_hud_minecraft(False)["changed"] is False  # ...e a voz de desligar ja nao tem o que mudar
    assert bridge_routes.definir_hud_minecraft(True)["changed"] is True
    assert not list(tmp_path.glob("*.tmp"))


def test_rota_hud_so_loopback_e_sem_daemon_diz_indisponivel(monkeypatch, tmp_path):
    from celia.core import minecraft_hud

    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    monkeypatch.setattr(minecraft_hud, "VAR", tmp_path)  # sem credencial: nunca fala com o daemon real
    minecraft_hud._cache.update(at=0.0, payload=None)

    async def run(cliente):
        app = FastAPI()
        app.include_router(bridge_routes.init_bridge_routes(None))
        transport = httpx.ASGITransport(app=app, client=cliente)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            return await c.get("/bridge/minecraft/hud")

    assert asyncio.run(run(("203.0.113.9", 4000))).status_code == 403
    corpo = asyncio.run(run(("testclient", 50000))).json()
    assert corpo["available"] is False and corpo["reason"] == "jev_daemon_offline"
    assert "celia" not in corpo and "hotbar" not in corpo  # nenhum valor de jogo inventado
