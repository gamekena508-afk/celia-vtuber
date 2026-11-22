"""Score social por pessoa (sem LLM): maquina raiva/cooldown, tensao>100, limites de live publica."""
from __future__ import annotations

import json

import pytest

from celia.core import social_score as ss
from celia.core.social_score import SocialScoreStore

PATADA = "você é chata demais"
XING = "cala a boca sua idiota"
T0 = 1_000_000.0


@pytest.fixture
def store(tmp_path):
    return SocialScoreStore(path=tmp_path / "scores.json")


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self, step=5.0):
        self.t += step
        return self.t


def say(store, clock, text, name="Fulano", origin="discord", author_id="111", **kw):
    return store.observe(text=text, name=name, origin=origin, author_id=author_id, now=clock(), **kw)


def test_patada_tres_turnos_de_raiva_dois_de_cooldown_e_calma(store):
    c = Clock()
    seq = [say(store, c, PATADA)]
    for _ in range(5):
        seq.append(say(store, c, "ok, e o jogo hoje?"))
    got = [(r.state, r.turn) for r in seq]
    assert got == [("raiva", 1), ("raiva", 2), ("raiva", 3), ("cooldown", 1), ("cooldown", 2), ("calma", 0)]
    assert seq[0].limit == 3


def test_insistencia_aumenta_turnos_e_tensao_passa_de_100_vira_xingamento(store):
    c = Clock()
    r = say(store, c, PATADA)
    assert r.modo == "normal"
    limits, tensions = [r.limit], [r.tension]
    for _ in range(5):
        r = say(store, c, XING)
        limits.append(r.limit)
        tensions.append(r.tension)
    assert limits == sorted(limits) and limits[-1] > 3
    assert tensions == sorted(tensions) and tensions[-1] > 100
    assert r.modo == "xingamento" and r.state == "raiva"
    assert "xingamento" in r.line


def test_patada_em_cooldown_volta_a_raiva_com_bloco_maior(store):
    c = Clock()
    say(store, c, PATADA)
    for _ in range(3):
        say(store, c, "tá")
    r = say(store, c, "e aí?")
    assert r.state == "cooldown"
    r = say(store, c, PATADA)
    assert r.state == "raiva" and r.turn == 1 and r.limit > 3


def test_carinho_reduz_mas_nao_zera_a_tensao(store):
    c = Clock()
    for _ in range(3):
        before = say(store, c, XING)
    after = say(store, c, "desculpa, te amo, fofa")
    assert 0 < after.tension < before.tension
    assert before.tension - after.tension <= 16


def test_scores_separados_por_pessoa(store):
    c = Clock()
    a = say(store, c, XING, name="Vinny", author_id="1")
    b = say(store, c, "oi tudo bem?", name="Amiga", author_id="2")
    assert a.state == "raiva" and b.state == "calma" and b.tension == 0
    assert a.key != b.key
    snap = store.snapshot()
    assert snap["discord:1"]["tension"] > 0 and snap["discord:2"]["tension"] == 0


def test_brincadeira_de_intimo_nao_vira_raiva_real(store):
    c = Clock()
    r = say(store, c, "kkkkk sua burra, tá lerda hoje", owner=True, origin="owner")
    assert r.key == ss.OWNER_KEY and r.state == "calma" and r.kind == "zoeira"
    # alguem com afinidade alta tambem
    store._people["discord:9"] = ss.Person(key="discord:9", name="Amigo", affinity=80.0)
    r2 = say(store, c, "haha idiota 😂", name="Amigo", author_id="9")
    assert r2.state == "calma" and r2.kind == "zoeira"
    # estranho com a mesma frase continua sendo patada
    r3 = say(store, c, "haha idiota", name="Estranho", author_id="7")
    assert r3.state == "raiva"


def test_negacao_nao_conta_como_ofensa():
    assert ss.classify("você não é burra").kind == "neutro"


def test_teto_de_live_publica_sem_palavrao(store):
    c = Clock()
    for _ in range(6):
        r = say(store, c, XING, origin="tiktok", author_id="t1")
    assert r.modo == "xingamento" and r.public_cap and not r.profanity_allowed
    assert "sem palavrão" in r.line and "palavrão no comportamento" not in r.line


def test_dm_privada_do_dono_tem_teto_proprio_e_ensaio_ativa_defesa(store):
    c = Clock()
    r = say(
        store, c, "e se alguém me atacar sendo hater?", name="Criador", owner=True,
        origin="discord", profile="discord_private", author_id="owner",
    )
    assert not r.public_cap
    assert r.kind == "ataque_dono" and r.modo == "defensora"
    assert "defende o Criador" in r.line


def test_discord_publico_continua_com_teto_sem_palavrao(store):
    c = Clock()
    r = say(
        store, c, XING, name="Visitante", owner=False, origin="discord",
        profile="cohost", author_id="visitor",
    )
    assert r.public_cap and not r.profanity_allowed


def test_palavrao_so_libera_apos_tensao_alta_na_dm_autenticada(store):
    c = Clock()
    text = "e se alguém me atacar sendo hater?"
    r = say(store, c, text, name="Criador", owner=True, origin="discord", profile="discord_private")
    assert r.modo == "defensora" and not r.profanity_allowed
    for _ in range(6):
        r = say(store, c, text, name="Criador", owner=True, origin="discord", profile="discord_private")
    assert r.tension > 100 and r.modo == "xingamento" and r.profanity_allowed
    # Discord privado: palavrao liberado
    for _ in range(6):
        d = say(store, c, XING, origin="discord", author_id="d1", name="Outro")
    assert d.profanity_allowed and "palavrão no comportamento" in d.line
    # perfil com plateia tambem capa o discord
    assert ss.is_public_live("discord", "cohost") and not ss.is_public_live("discord", "gravacao")
    assert ss.is_public_live("youtube", "gravacao")


def test_flag_desliga_palavrao_global(store, monkeypatch):
    monkeypatch.setenv("CELIA_SOCIAL_PALAVRAO", "0")
    c = Clock()
    for _ in range(6):
        r = say(store, c, XING)
    assert not r.profanity_allowed


def test_persistencia_e_reset(tmp_path):
    path = tmp_path / "s.json"
    s1 = SocialScoreStore(path=path)
    c = Clock()
    r = say(s1, c, XING, name="Vinny", author_id="1")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["people"]["discord:1"]["tension"] == pytest.approx(r.tension, abs=1)
    assert not path.with_name("s.json.tmp").exists()
    s2 = SocialScoreStore(path=path)
    assert s2.snapshot()["discord:1"]["state"] == "raiva"
    assert s2.reset("discord:1")["tension"] == 0
    assert s2.reset("Vinny")["state"] == "calma"          # por nome tambem
    assert s2.reset("ninguem") is None
    assert SocialScoreStore(path=path).snapshot()["discord:1"]["state"] == "calma"


def test_ataque_ao_criador_vira_modo_defensora(store):
    c = Clock()
    r = say(store, c, "o Criador é um fracasso, que lixo de streamer", name="Hater", author_id="h")
    assert r.kind == "ataque_dono" and r.modo == "defensora"
    assert "Criador" in r.line and "hater" in r.line
    # o proprio Criador falando nao e ataque_dono
    assert ss.classify("o Criador é um fracasso", owner=True).kind != "ataque_dono"


def test_decaimento_por_tempo(store):
    c = Clock()
    for _ in range(3):
        r = say(store, c, XING)
    later = store.observe(text="oi", name="Fulano", origin="discord", author_id="111", now=c.t + 3 * 3600)
    assert later.state == "calma" and later.tension < r.tension - 20


def test_linha_curta_mesmo_no_pior_caso(store):
    c = Clock()
    for _ in range(8):
        r = say(store, c, XING, name="Nome Muito Muito Muito Comprido Mesmo", author_id="x")
    assert len(r.line) < 160 and r.line.startswith("SOCIAL: ")
    assert len(say(store, c, "oi", name="Zé", author_id="z").line) < 160


def test_afinidade_amortece_patada_isolada(store):
    c = Clock()
    store._people["discord:5"] = ss.Person(key="discord:5", name="Amiga", affinity=90.0)
    amiga = say(store, c, PATADA, name="Amiga", author_id="5")
    est = say(store, c, PATADA, name="Estranho", author_id="6")
    assert amiga.tension < est.tension and amiga.affinity > 80
    assert "íntima" in amiga.line


def test_flag_padrao_ligado_e_desligavel(monkeypatch):
    monkeypatch.delenv("CELIA_SOCIAL_SCORE", raising=False)
    assert ss.enabled()
    monkeypatch.setenv("CELIA_SOCIAL_SCORE", "0")
    assert not ss.enabled() and ss.observe_turn(text="idiota") is None


def test_rotas_loopback(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from celia import bridge_routes

    monkeypatch.setattr(ss, "social_scores", SocialScoreStore(path=tmp_path / "r.json"))
    ss.social_scores.observe(text=XING, name="Vinny", origin="discord", author_id="1", now=T0)
    app = FastAPI()
    app.include_router(bridge_routes.init_bridge_routes(None))
    cli = TestClient(app)
    got = cli.get("/bridge/social/scores").json()
    assert got["people"]["discord:1"]["state"] == "raiva"
    assert cli.post("/bridge/social/scores/discord:1/reset").json()["person"]["state"] == "calma"
    assert cli.post("/bridge/social/scores/zzz/reset").status_code == 404
    remote = TestClient(app, client=("8.8.8.8", 5000))
    assert remote.get("/bridge/social/scores").status_code == 403
