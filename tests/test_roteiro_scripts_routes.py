"""Rotas do seletor de roteiro no teleprompter.

O controle que mais importa aqui é o do perfil: trocar de roteiro NUNCA pode
passar pelo perfil `roteiro`, porque foi medido em 2026-09-30 que o handler de
`profile.changed` recarrega `roteiros/ia_vs_criador.json` do disco nesse perfil e
sobrescreve a escolha do dono.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from celia import roteiro_routes
from celia.core import roteiro_catalogo as cat
from celia.roteiro_auth import SESSION_COOKIE


def _roteiro(identificador: str, beats: list[str], versao: int = 1) -> dict:
    return {
        "id": identificador,
        "title": identificador.upper(),
        "version": versao,
        "start": beats[0],
        "beats": [
            {
                "id": b,
                "title": b,
                "lines": [{"speaker": "Criador", "text": f"fala de {b}"}],
                "next": [beats[i + 1]] if i + 1 < len(beats) else [],
            }
            for i, b in enumerate(beats)
        ],
    }


class _ComportamentoFake:
    """Só o contrato que a rota usa. O perfil é observável para o teste."""

    def __init__(self) -> None:
        self.profile = "chat"
        self._script: dict = {}
        self.perfis_pedidos: list[str] = []

    class _Script:
        def __init__(self, dono: "_ComportamentoFake") -> None:
            self._dono = dono

        def export(self) -> dict:
            return self._dono._script

    @property
    def script(self):  # noqa: D401 - espelha a API real
        return self._Script(self)

    def load_script(self, dados: dict) -> dict:
        if not isinstance(dados, dict) or not dados.get("beats"):
            raise ValueError("invalid_script")
        self._script = dados
        return dados

    def select_profile(self, perfil: str) -> dict:
        self.perfis_pedidos.append(perfil)
        anterior, self.profile = self.profile, perfil
        return {"profile": perfil, "profile_change": {"de": anterior, "para": perfil}}


class _TeleprompterFake:
    def __init__(self) -> None:
        self.script: dict = {}
        self.perfis_ativados: list[str] = []

    def snapshot(self) -> dict:
        return {"script": {k: v for k, v in self.script.items() if k != "beats"}}

    def prepare_operational_script(self, behavior, script=None) -> dict:
        # Espelha a regra real: sem script explícito, cai no canônico do disco.
        if script is None:
            return {"id": "ia-vs-criador-volta-do-canal", "beats": []}
        return script

    async def activate(self, profile="roteiro", *, new_session=True, script=None) -> dict:
        self.perfis_ativados.append(profile)
        self.script = script or {}
        return self.snapshot()


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    roteiros = tmp_path / "roteiros"
    sessoes = tmp_path / "var" / "roteiro_sessions"
    roteiros.mkdir(parents=True)
    sessoes.mkdir(parents=True)
    monkeypatch.setattr(cat, "ROTEIROS_DIR", roteiros)
    monkeypatch.setattr(cat, "SESSOES_DIR", sessoes)
    monkeypatch.setattr(cat, "MARCAS_PATH", tmp_path / "var" / "roteiros_gravados.json")

    (roteiros / "discord.json").write_text(
        json.dumps(_roteiro("discord-tutorial", ["01_abertura", "02_bot"], versao=2)),
        encoding="utf-8",
    )
    (roteiros / "familia.json").write_text(
        json.dumps(_roteiro("family-time", ["01_intro"])), encoding="utf-8"
    )

    comportamento = _ComportamentoFake()
    teleprompter = _TeleprompterFake()
    monkeypatch.setattr(roteiro_routes, "continuous_behavior", comportamento)
    monkeypatch.setattr(roteiro_routes, "roteiro_live", teleprompter)

    async def _sem_broadcast(mudanca):
        return None

    monkeypatch.setattr(roteiro_routes, "_broadcast_profile", _sem_broadcast)
    monkeypatch.setattr(roteiro_routes.roteiro_auth, "valid_session", lambda token: token == "ok")

    app = FastAPI()
    app.include_router(roteiro_routes.init_roteiro_routes())
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, "ok")
    return {"http": client, "comportamento": comportamento, "tele": teleprompter}


# ------------------------------------------------------------------ Fatia 1: ler


def test_scripts_exige_sessao(cliente):
    anonimo = TestClient(cliente["http"].app)
    assert anonimo.get("/roteiro/scripts").status_code == 401


def test_scripts_lista_os_roteiros_do_disco(cliente):
    dados = cliente["http"].get("/roteiro/scripts").json()

    assert [i["id"] for i in dados["roteiros"]] == ["discord-tutorial", "family-time"]
    assert dados["roteiros"][0]["versao"] == 2
    assert dados["roteiros"][0]["beats"] == 2


def test_scripts_nao_altera_nada(cliente):
    """Fatia 1 é somente leitura: ler a lista não pode mexer no runtime."""
    cliente["http"].get("/roteiro/scripts")

    assert cliente["comportamento"].perfis_pedidos == []
    assert cliente["tele"].perfis_ativados == []


# --------------------------------------------------------------- Fatia 2: trocar


def test_select_troca_o_roteiro_ativo(cliente):
    dados = cliente["http"].post(
        "/roteiro/scripts/select", json={"id": "family-time"}
    ).json()

    assert cliente["tele"].script["id"] == "family-time"
    assert [i["ativo"] for i in dados["roteiros"]] == [False, True]


def test_select_NUNCA_passa_pelo_perfil_roteiro(cliente):
    """CONTROLE POSITIVO da causa medida do bug.

    Se algum dia a rota voltar a pedir o perfil `roteiro`, este teste cai — e é
    exatamente esse perfil que recarrega o ia_vs_criador.json por cima.
    """
    cliente["http"].post("/roteiro/scripts/select", json={"id": "discord-tutorial"})

    assert "roteiro" not in cliente["comportamento"].perfis_pedidos
    assert "roteiro" not in cliente["tele"].perfis_ativados
    assert cliente["comportamento"].perfis_pedidos == ["gravacao"]
    assert cliente["tele"].perfis_ativados == ["gravacao"]


def test_select_nunca_ativa_sem_script_explicito(cliente):
    """O fake devolve o canônico quando o script vem None. Não pode acontecer."""
    cliente["http"].post("/roteiro/scripts/select", json={"id": "discord-tutorial"})

    assert cliente["tele"].script["id"] == "discord-tutorial"
    assert cliente["tele"].script["id"] != "ia-vs-criador-volta-do-canal"


def test_select_ja_em_gravacao_nao_repete_troca_de_perfil(cliente):
    cliente["comportamento"].profile = "gravacao"

    cliente["http"].post("/roteiro/scripts/select", json={"id": "discord-tutorial"})

    assert cliente["comportamento"].perfis_pedidos == []
    assert cliente["tele"].perfis_ativados == ["gravacao"]


def test_select_de_id_inexistente_da_404(cliente):
    assert cliente["http"].post(
        "/roteiro/scripts/select", json={"id": "nao-existe"}
    ).status_code == 404


def test_select_registra_o_ultimo_selecionado(cliente):
    cliente["http"].post("/roteiro/scripts/select", json={"id": "family-time"})

    assert cat.ler_marcas()["ultimo_selecionado"] == "family-time"


# ---------------------------------------------------------------- Fatia 3: marcar


def test_gravado_marca_e_desmarca(cliente):
    dados = cliente["http"].post(
        "/roteiro/scripts/gravado", json={"id": "discord-tutorial", "gravado": True}
    ).json()
    assert dados["roteiros"][0]["gravado"] is True

    dados = cliente["http"].post(
        "/roteiro/scripts/gravado", json={"id": "discord-tutorial", "gravado": False}
    ).json()
    assert dados["roteiros"][0]["gravado"] is False


def test_gravado_de_id_inexistente_da_404(cliente):
    assert cliente["http"].post(
        "/roteiro/scripts/gravado", json={"id": "nao-existe", "gravado": True}
    ).status_code == 404


def test_gravado_exige_sessao(cliente):
    anonimo = TestClient(cliente["http"].app)
    resposta = anonimo.post(
        "/roteiro/scripts/gravado", json={"id": "discord-tutorial", "gravado": True}
    )
    assert resposta.status_code == 401


# --------------------------------------------------------- Fatia 4: pular gravados


def test_sugestao_pula_o_gravado_pela_rota(cliente):
    """ORDEM do dono: a interface não o joga num roteiro já gravado."""
    cliente["http"].post(
        "/roteiro/scripts/gravado", json={"id": "discord-tutorial", "gravado": True}
    )

    dados = cliente["http"].get("/roteiro/scripts").json()

    assert dados["sugestao"]["id"] == "family-time"


def test_todos_gravados_fica_no_ultimo_selecionado_pela_rota(cliente):
    http = cliente["http"]
    http.post("/roteiro/scripts/select", json={"id": "discord-tutorial"})
    http.post("/roteiro/scripts/gravado", json={"id": "discord-tutorial", "gravado": True})
    http.post("/roteiro/scripts/gravado", json={"id": "family-time", "gravado": True})

    dados = http.get("/roteiro/scripts").json()

    assert dados["sugestao"]["todos_gravados"] is True
    assert dados["sugestao"]["id"] == "discord-tutorial"
