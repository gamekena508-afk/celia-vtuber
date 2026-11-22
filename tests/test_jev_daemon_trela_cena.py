"""_resume_after_scene deve retomar a missão com a trela que ela já tinha (policy.leash).

Sem isso, jev.start(leash=None) remove a trela e a Célia pode se afastar do dono depois de uma cena.
Fakes simples para jev/series; nenhum servidor sobe e nada é gravado fora de tmp.
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations/minecraft"))


@pytest.fixture()
def daemon_module():
    spec = importlib.util.spec_from_file_location("jev_daemon_trela_under_test", ROOT / "integrations/minecraft/jev_daemon.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeJev:
    def __init__(self, mission):
        self.mission = mission
        self.starts = []

    def current_mission(self):
        return self.mission

    async def start(self, order_text="", source="owner", leash=None):
        self.starts.append({"order": order_text, "source": source, "leash": leash})
        return {"id": "m-1", "epoch": 2}


class FakeSeries:
    def __init__(self, blocked=False):
        self.blocked = blocked

    def blocks_missions(self):
        return self.blocked


class FakeRuntime:
    """Só o que _resume_after_scene toca: possessed, series, start_lock, jev e bind_world."""

    def __init__(self, module, mission, *, possessed=False, blocked=False, bound=True):
        self.possessed = possessed
        self.series = FakeSeries(blocked)
        self.start_lock = asyncio.Lock()
        self.jev = FakeJev(mission)
        self._bound = bound
        self.module = module

    async def bind_world(self):
        return {"ok": True} if self._bound else None


def _mission(policy):
    return {"id": "m-1", "status": "paused", "epoch": 1, "data": {"policy": policy}}


def _resume(module, rt):
    asyncio.run(module.Runtime._resume_after_scene(rt))


def test_retomada_depois_da_cena_mantem_a_trela_de_24(daemon_module):
    rt = FakeRuntime(daemon_module, _mission({"visible_only": True, "leash": 24.0}))
    _resume(daemon_module, rt)
    assert len(rt.jev.starts) == 1
    assert rt.jev.starts[0]["leash"] == 24
    assert rt.jev.starts[0]["source"] == "daemon"


def test_retomada_depois_da_cena_mantem_a_trela_de_12(daemon_module):
    rt = FakeRuntime(daemon_module, _mission({"leash": 12}))
    _resume(daemon_module, rt)
    assert rt.jev.starts[0]["leash"] == 12


def test_sem_trela_anterior_a_retomada_continua_livre(daemon_module):
    rt = FakeRuntime(daemon_module, _mission({"visible_only": True}))
    _resume(daemon_module, rt)
    assert rt.jev.starts[0]["leash"] is None


@pytest.mark.parametrize("mission", [
    None,
    {"id": "m-1", "status": "paused"},
    {"id": "m-1", "status": "paused", "data": None},
    {"id": "m-1", "status": "paused", "data": {"policy": "quebrado"}},
    {"id": "m-1", "status": "paused", "data": {"policy": {"leash": "xyz"}}},
    {"id": "m-1", "status": "paused", "data": {"policy": {"leash": 0}}},
])
def test_trela_ausente_ou_malformada_vira_none_sem_excecao(daemon_module, mission):
    rt = FakeRuntime(daemon_module, mission)
    _resume(daemon_module, rt)
    assert rt.jev.starts[0]["leash"] is None


def test_possuida_ou_bloqueada_nao_retoma(daemon_module):
    rt = FakeRuntime(daemon_module, _mission({"leash": 24}), possessed=True)
    _resume(daemon_module, rt)
    assert rt.jev.starts == []

    rt = FakeRuntime(daemon_module, _mission({"leash": 24}), blocked=True)
    _resume(daemon_module, rt)
    assert rt.jev.starts == []


def test_corpo_indisponivel_nao_retoma(daemon_module):
    rt = FakeRuntime(daemon_module, _mission({"leash": 24}), bound=False)
    _resume(daemon_module, rt)
    assert rt.jev.starts == []
