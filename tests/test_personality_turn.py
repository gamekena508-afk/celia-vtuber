"""As regras de tom dependem de identidade confirmada, known_people e contexto presente."""
from __future__ import annotations

import json

from celia.core import personality_turn as pt

KNOWN_PEOPLE = [
    {"name": "Amiga", "notes": "amiga e colega próxima, irmãzinha por carinho"},
    {"name": "AmigoJogos", "notes": "amigo de jogos e projetos de bots e agentes"},
    {"name": "AmigoDev", "notes": "amigo dev que organiza canais e salas do Discord"},
]


def _profiles(tmp_path):
    p = tmp_path / "pessoas.json"
    p.write_text(json.dumps({"profiles": [
        {"discord_user_id": "111", "name": "Amiga"},
        {"discord_user_id": "222", "name": "AmigoJogos"},
        {"discord_user_id": "333", "name": "AmigoDev"},
    ]}), encoding="utf-8")
    return p


def test_known_people_vazia_nao_aplica_tratamento_especial(tmp_path):
    p = _profiles(tmp_path)
    assert pt.turn_hint(
        "Ih, olha ela se achando a rainha do servidor!",
        author_id="111",
        profiles_path=p,
        known_people=[],
    ) == ""
    assert pt.turn_hint(
        "Criei três salas no Discord.",
        author_id="333",
        profiles_path=p,
        known_people=[],
    ) == ""
    assert pt.turn_hint(
        "Atualizei meu agente para usar ferramentas.",
        author_id="222",
        profiles_path=p,
        known_people=[],
    ) == ""


def test_amiga_implicancia_reciproca(tmp_path):
    p = _profiles(tmp_path)
    hint = pt.turn_hint(
        "Ih, olha ela se achando a rainha do servidor!",
        author_id="111",
        profiles_path=p,
        known_people=KNOWN_PEOPLE,
    )
    assert "implicância de irmãs" in hint and "igual para igual" in hint
    assert pt.turn_hint(
        "Ih, olha ela se achando a rainha do servidor!",
        author_id="nulo",
        profiles_path=p,
        known_people=KNOWN_PEOPLE,
    ) == ""


def test_amiga_ajuda_precede_brincadeira(tmp_path):
    p = _profiles(tmp_path)
    t = pt.turn_hint(
        "Não lembro o que falei ontem. Como organizo meu histórico, metida?",
        author_id="111",
        profiles_path=p,
        known_people=KNOWN_PEOPLE,
    )
    assert "conteúdo concreto" in t
    assert "não transforme o pedido em competição" in t
    t = pt.turn_hint(
        "Célia, tô triste hoje.",
        author_id="111",
        profiles_path=p,
        known_people=KNOWN_PEOPLE,
    )
    assert "falando sério" in t


def test_amigo_dev_reconhecido_sem_ia(tmp_path):
    p = _profiles(tmp_path)
    t = pt.turn_hint(
        "Criei três salas no Discord.",
        author_id="333",
        profiles_path=p,
        known_people=KNOWN_PEOPLE,
    )
    assert "AMIGODEV" in t and "respeitarem" in t
    assert pt.turn_hint(
        "Criei três salas no Discord.",
        author_id="222",
        profiles_path=p,
        known_people=KNOWN_PEOPLE,
    ) == ""


def test_amigo_jogos_curiosidade(tmp_path):
    p = _profiles(tmp_path)
    t = pt.turn_hint(
        "Atualizei meu agente para usar ferramentas.",
        author_id="222",
        profiles_path=p,
        known_people=KNOWN_PEOPLE,
    )
    assert "curiosidade" in t


def test_criador_afeto_e_implicancia():
    assert "carinho real" in pt.turn_hint("Tu é especial pra mim, Célia.", trusted_owner=True)
    assert "patadinha" in pt.turn_hint("Tu tá lerda, hein!", trusted_owner=True)
    assert pt.turn_hint("Me explica CPU e GPU.", trusted_owner=True) == ""


def test_lore_e_xadrez_geram_hints_somente_com_recordacao():
    assert pt.turn_hint("Quanto BTC guardou?", memory_block="") == ""
    t = pt.turn_hint(
        "Quanto BTC guardou?",
        memory_block="LEMBRANÇA (lore): carteira de Bitcoin imaginária",
        trusted_owner=True)
    assert "IMAGINÁRIOS" in t and "nem desvie" in t.lower()
    t = pt.turn_hint(
        "Quer revanche no xadrez?",
        memory_block="LEMBRANÇA (vídeo): Criador ensinou xadrez",
        trusted_owner=True)
    assert "revanche" in t
    assert pt.turn_hint("Quer revanche no xadrez?", memory_block="") == ""


def test_memoria_ausente_tem_prioridade(tmp_path):
    p = _profiles(tmp_path)
    t = pt.turn_hint(
        "Lembra aquele vulcão na Lua que a gente filmou?",
        author_id="111",
        memory_block="LEMBRANÇA AUSENTE NOS VÍDEOS: sem registro do fato",
        profiles_path=p,
        known_people=KNOWN_PEOPLE)
    assert "NÃO" in t and "Nunca diga" in t
    assert pt.turn_hint(
        "Não lembro minha senha.",
        author_id="111",
        memory_block="LEMBRANÇA (vídeo): outra coisa",
        profiles_path=p,
        known_people=KNOWN_PEOPLE) != t
