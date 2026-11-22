import asyncio

import httpx
from fastapi import FastAPI

from celia import bridge_routes


def _app():
    app = FastAPI()
    app.include_router(bridge_routes.init_bridge_routes(None))
    return app


def _chamar(metodo, corpo=None, cliente=("testclient", 50000)):
    async def run():
        transport = httpx.ASGITransport(app=_app(), client=cliente)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            if metodo == "GET":
                return await c.get("/bridge/body")
            return await c.post("/bridge/body", json=corpo)

    return asyncio.run(run())


def test_corpo_nunca_escolhido_devolve_null(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    r = _chamar("GET")
    assert r.status_code == 200
    assert r.json() == {"corpo": None}


def test_corpo_grava_e_le_2d_3d(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    for valor in ("3d", "2d"):
        assert _chamar("POST", {"corpo": valor}).json() == {"corpo": valor}
        assert _chamar("GET").json() == {"corpo": valor}
    assert not list(tmp_path.glob("*.tmp"))


def test_corpo_recusa_valor_fora_da_lista(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    for ruim in ("4d", "../x", "", "3D "):
        assert _chamar("POST", {"corpo": ruim}).status_code == 422
    assert _chamar("GET").json() == {"corpo": None}


def test_corpo_arquivo_corrompido_vira_null(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    (tmp_path / "celia_body.json").write_text("{nao e json", encoding="utf-8")
    assert _chamar("GET").json() == {"corpo": None}


def test_corpo_so_loopback(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    assert _chamar("GET", cliente=("203.0.113.7", 1)).status_code == 403
    assert _chamar("POST", {"corpo": "3d"}, cliente=("203.0.113.7", 1)).status_code == 403
    assert not (tmp_path / "celia_body.json").exists()


def _estado_pet(cliente=('testclient', 50000)):
    async def run():
        transport = httpx.ASGITransport(app=_app(), client=cliente)
        async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as c:
            return await c.get('/bridge/desktop-pet/state')

    return asyncio.run(run())


def test_pet_state_le_enabled_sem_escrever(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, 'VAR', tmp_path)
    assert _estado_pet().json()['enabled'] is None
    for valor in (True, False):
        (tmp_path / 'desktop_pet.json').write_text(f'{{"enabled": {str(valor).lower()}, "x": 1}}', encoding='utf-8')
        assert _estado_pet().json()['enabled'] is valor
    (tmp_path / 'desktop_pet.json').write_text('{"enabled": "sim"}', encoding='utf-8')
    assert _estado_pet().json()['enabled'] is None
    (tmp_path / 'desktop_pet.json').write_text('quebrado', encoding='utf-8')
    assert _estado_pet().json()['enabled'] is None


def test_pet_state_so_loopback(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, 'VAR', tmp_path)
    assert _estado_pet(('203.0.113.7', 1)).status_code == 403



def test_pet_vivo_le_proc_somente_leitura(tmp_path):
    proc = tmp_path / "proc"; proc.mkdir()
    assert bridge_routes._pet_vivo(proc) is False                      # sem processos
    (proc / "self").mkdir(); (proc / "self" / "cmdline").write_bytes(b"x\0")  # nao numerico: ignorado
    (proc / "111").mkdir(); (proc / "111" / "cmdline").write_bytes(b"/usr/bin/python\0server.py\0")
    assert bridge_routes._pet_vivo(proc) is False
    (proc / "222").mkdir(); (proc / "222" / "cmdline").write_bytes(b"/x/electron\0--ozone-platform=wayland\0/home/u/Celia/tools/pet-electron\0")
    assert bridge_routes._pet_vivo(proc) is True
    # so um argumento --app-path (subprocesso renderer) sozinho nao conta como o app principal
    (proc / "222" / "cmdline").write_bytes(b"/x/electron\0--type=renderer\0--app-path=/home/u/Celia/tools/pet-electron\0")
    assert bridge_routes._pet_vivo(proc) is False
    assert bridge_routes._pet_vivo(tmp_path / "nao-existe") is None


def test_pet_state_traz_alive(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    for valor in (True, False, None):
        monkeypatch.setattr(bridge_routes, "_pet_vivo", lambda *a, _v=valor: _v)
        assert _estado_pet().json()["alive"] is valor


def _modo(metodo, corpo=None, cliente=("testclient", 50000)):
    async def run():
        transport = httpx.ASGITransport(app=_app(), client=cliente)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            return await (c.get("/bridge/mode") if metodo == "GET" else c.post("/bridge/mode", json=corpo))

    return asyncio.run(run())


def test_modo_grava_com_carimbo_e_valida(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    assert _modo("GET").json() == {"modo": None, "em": None}
    r = _modo("POST", {"modo": "janela"}); assert r.status_code == 200 and r.json()["modo"] == "janela" and r.json()["em"] > 0
    lido = _modo("GET").json(); assert lido["modo"] == "janela" and lido["em"] == r.json()["em"]
    assert _modo("POST", {"modo": "pet"}).json()["modo"] == "pet"
    for ruim in ("live", "", "PET", "../x"):
        assert _modo("POST", {"modo": ruim}).status_code == 422
    assert _modo("GET").json()["modo"] == "pet"
    (tmp_path / "celia_mode.json").write_text("{quebrado", encoding="utf-8")
    assert _modo("GET").json() == {"modo": None, "em": None}


def test_modo_so_loopback(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_routes, "VAR", tmp_path)
    assert _modo("GET", cliente=("203.0.113.7", 1)).status_code == 403
    assert _modo("POST", {"modo": "pet"}, cliente=("203.0.113.7", 1)).status_code == 403
    assert not (tmp_path / "celia_mode.json").exists()
