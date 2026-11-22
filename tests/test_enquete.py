"""Enquete da live (overlay do modo mobile): placar por presentes, idempotente, liga/desliga pelo Corpo."""
import asyncio

import httpx
from fastapi import FastAPI

from celia import bridge_routes
from celia.core import enquete


def _chamar(metodo, rota, corpo=None, cliente=("testclient", 50000)):
    async def run():
        app = FastAPI()
        app.include_router(bridge_routes.init_bridge_routes(None))
        transport = httpx.ASGITransport(app=app, client=cliente)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            if metodo == "GET":
                return await c.get("/bridge" + rota)
            return await c.post("/bridge" + rota, json=corpo)

    return asyncio.run(run())


def _limpar_vistos():
    enquete._vistos.clear()
    enquete._vistos_set.clear()


def test_sem_arquivo_placar_zerado_e_desligada(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    r = _chamar("GET", "/enquete").json()
    assert r["ativa"] is False and r["lula"] == 0 and r["flavio"] == 0 and r["rodada"] == 1


def test_presente_soma_e_persiste(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    _limpar_vistos()
    assert _chamar("POST", "/enquete/presente", {"lado": "lula", "quantidade": 3, "evento_id": "a1"}).json()["lula"] == 3
    r = _chamar("POST", "/enquete/presente", {"lado": "flavio", "quantidade": 1, "evento_id": "a2", "presente": "Rose"}).json()
    assert r["lula"] == 3 and r["flavio"] == 1
    _limpar_vistos()  # reinicio da central: o placar vem do disco
    assert _chamar("GET", "/enquete").json()["lula"] == 3
    assert not list(tmp_path.glob("*.tmp"))


def test_mesmo_evento_nao_soma_duas_vezes(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    _limpar_vistos()
    corpo = {"lado": "lula", "quantidade": 5, "evento_id": "combo-9"}
    _chamar("POST", "/enquete/presente", corpo)
    r = _chamar("POST", "/enquete/presente", corpo).json()
    assert r["lula"] == 5 and r["duplicado"] is True


def test_entrada_invalida_e_recusada(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    assert _chamar("POST", "/enquete/presente", {"lado": "outro", "quantidade": 1, "evento_id": "x"}).status_code == 422
    assert _chamar("POST", "/enquete/presente", {"lado": "lula", "quantidade": 0, "evento_id": "x"}).status_code == 422
    assert _chamar("GET", "/enquete").json()["lula"] == 0


def test_zerar_comeca_rodada_nova(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    _limpar_vistos()
    _chamar("POST", "/enquete/presente", {"lado": "flavio", "quantidade": 7, "evento_id": "z1"})
    r = _chamar("POST", "/enquete/zerar").json()
    assert r["lula"] == 0 and r["flavio"] == 0 and r["rodada"] == 2
    # depois de zerar, um evento antigo reenviado conta na rodada nova (os vistos foram esquecidos)
    assert _chamar("POST", "/enquete/presente", {"lado": "flavio", "quantidade": 1, "evento_id": "z1"}).json()["flavio"] == 1


def test_liga_desliga_pelo_corpo(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    _chamar("POST", "/corpo-opcoes", {"enquete_mobile": True})
    assert _chamar("GET", "/enquete").json()["ativa"] is True
    _chamar("POST", "/corpo-opcoes", {"enquete_mobile": False})
    assert _chamar("GET", "/enquete").json()["ativa"] is False


def test_somente_loopback(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    fora = ("192.168.0.50", 50000)
    assert _chamar("GET", "/enquete", cliente=fora).status_code in (403, 404)
    assert _chamar("POST", "/enquete/presente", {"lado": "lula", "quantidade": 1, "evento_id": "f"}, cliente=fora).status_code in (403, 404)
