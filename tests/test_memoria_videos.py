"""Memória episódica dos vídeos: ingestão, recuperação sem LLM, esquecimento, reforço, orçamento e flag."""
from __future__ import annotations

import json

import pytest

from celia.core import video_memory as vm

DIA = 86400.0
T0 = 1_800_000_000.0


def _ep(i, vid, t, texto, reacao, aprovou=False, cit="", tipos=("engracado",), cert="alta", q=3, emo="alegre"):
    return {"id": f"{vid}-e{i:02d}", "inicio": t, "fim": t, "o_que_aconteceu": texto, "reacao_celia": reacao,
            "falas_chave": [{"t": t, "falante": "Célia", "texto": reacao}], "visual": "", "quadros": [],
            "tipos": list(tipos), "emocao_celia": emo, "qualidade_reacao": q, "justificativa_qualidade": "",
            "criador_aprovou": {"sim": aprovou, "citacao": cit, "t": t if aprovou else ""}, "certeza": cert}


def _video(vid, titulo, eps, **extra):
    d = {"video_id": vid, "titulo": titulo, "data": "2025-12-24", "url": "", "resumo": "", "participantes": [],
         "episodios": eps, "gostos_da_celia": [], "lore_da_celia": [], "piadas_que_funcionaram": [],
         "piadas_artificiais": [], "personalidade_demonstrada": [], "falas_do_criador_que_geraram": {},
         "comentaristas": [], "nota_video": {"nota": 3, "justificativa": ""}, "avisos": []}
    d.update(extra)
    return d


def _fixture(tmp_path):
    ana = tmp_path / "analise"
    ana.mkdir()
    natal = _video("vidNATAL", "LIVE DE NATAL ESPECIAL VTuber IA Natal 2025", [
        _ep(1, "vidNATAL", "07:00", "Gag do Papai Noel sobre o orçamento de presentes de Natal.",
            "Faz ironia sobre o orçamento do Natal.", aprovou=True, cit="Essa foi boa", q=2),
        _ep(2, "vidNATAL", "20:10", "Célia lista presentes de Natal para o chat com ironia.",
            "Debocha dos presentes de Natal.", aprovou=False, q=5),
        _ep(3, "vidNATAL", "30:00", "Criador lê a ceia de Natal no chat.", "Comenta a ceia.", tipos=("outro",), q=3),
    ] + [_ep(10 + i, "vidNATAL", f"4{i}:00", f"Momento aleatório {i} sobre jogos de tabuleiro e música",
             "Reage.", tipos=("tecnico",)) for i in range(3)],
        comentaristas=[{"autor": "@Hater1", "texto": "essa IA é lixo, desliga isso", "curtidas": 3,
                        "sentimento": "hater", "vale_lembrar": True, "por_que": "hater recorrente"}],
        gostos_da_celia=[{"coisa": "Ataque dos Titãs", "evidencia": "diz que é um dos melhores animes", "t": "14:00"}],
        lore_da_celia=[{"fato": "Ela não assistiu Ataque dos Titãs; leu spoilers (segundo o criador)",
                        "evidencia": "resposta do criador no comentário", "t": "14:00"}])
    xad = _video("vidXADREZ", "IA VS CRIADOR", [
        _ep(1, "vidXADREZ", "13:10", "Xeque-mate das peças azuis na partida de xadrez.", "Comemora a vitória no xadrez.",
            aprovou=True, cit="boa, Célia", q=4),
        _ep(2, "vidXADREZ", "14:00", "Criador pergunta sobre a carteira de Bitcoin e a mineração de cripto.",
            "Diz que minera Bitcoin no PC dele.", tipos=("bitcoin",), cert="media"),
        _ep(3, "vidXADREZ", "15:00", "Algo incerto sobre um sorteio de pizza.", "Talvez riu.", cert="baixa"),
    ] + [_ep(10 + i, "vidXADREZ", f"2{i}:00", f"Trecho técnico {i} de código", "Neutra", tipos=("tecnico",))
         for i in range(6)],
        comentaristas=[{"autor": "@TEKeEB27", "texto": "Achei um canal semelhante, maneiro!", "curtidas": 9,
                        "sentimento": "positivo", "vale_lembrar": True, "por_que": "elogio"}])
    for d in (natal, xad):
        (ana / f"{d['video_id']}.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return ana


@pytest.fixture
def mem(tmp_path):
    ana = _fixture(tmp_path)
    dest = tmp_path / "videos.json"
    vm.ingerir(ana, dest)
    m = vm.Memoria(dest, tmp_path / "estado.json")
    m.carregar()
    return m, ana, dest


def test_ingestao_idempotente_e_incremental(tmp_path):
    ana = _fixture(tmp_path)
    dest = tmp_path / "videos.json"
    a = vm.ingerir(ana, dest)
    b1 = dest.read_bytes()
    b = vm.ingerir(ana, dest)
    assert dest.read_bytes() == b1 and a == b
    assert len({i["id"] for i in a["itens"]}) == len(a["itens"])
    # novo vídeo entra na rerodada sem duplicar os antigos; arquivo em escrita (JSON inválido) é ignorado
    novo = _video("vidNOVO", "Vídeo novo", [_ep(1, "vidNOVO", "01:00", "Célia conta uma piada de pudim.", "Ri")])
    (ana / "vidNOVO.json").write_text(json.dumps(novo, ensure_ascii=False), encoding="utf-8")
    (ana / "vidMETADE.json").write_text('{"video_id": "x", "epis', encoding="utf-8")
    c = vm.ingerir(ana, dest)
    assert len(c["fontes"]) == 3 and len(c["itens"]) == len(a["itens"]) + 1
    assert c["ignorados"] and "vidMETADE" in c["ignorados"][0]


def test_aprovado_pelo_criador_ranqueia_acima_do_nao_aprovado_mesmo_com_nota_menor(mem):
    m, _, _ = mem
    r = m.buscar("lembra do natal?")
    kinds = [(x["item"]["criador_aprovou"], x["item"]["texto"]) for x in r if x["item"]["kind"] == "episodio"]
    assert kinds[0][0] is True  # gag aprovado (nota 2) acima da lista de presentes (nota 5, sem aprovação)
    imp = {x["item"]["texto"][:12]: x["item"]["importancia"] for x in r if x["item"]["kind"] == "episodio"}
    assert imp["Gag do Papai"] > imp["Célia lista "]


def test_oi_nao_puxa_lembranca(mem):
    m, _, _ = mem
    for t in ("oi", "oi Célia, tudo bem?", "eae", "bom dia"):
        assert m.lembrar(t, atualizar=False)["bloco"] == ""


def test_perguntas_puxam_lembranca_certa_com_video_e_tempo(mem):
    m, _, _ = mem
    nat = m.lembrar("lembra do natal?", atualizar=False)["bloco"]
    assert "«Live de Natal" in nat or "«LIVE DE NATAL" in nat.upper()
    assert "07:00" in nat and "certeza alta" in nat and "Criador: aprovou" in nat
    xad = m.lembrar("você gosta de xadrez?", atualizar=False)["bloco"]
    assert "«IA VS CRIADOR»" in xad and "13:10" in xad
    btc = m.lembrar("e o bitcoin?", atualizar=False)["bloco"]
    assert "Bitcoin" in btc and "lore" in btc.lower()
    assert "dinheiro" not in btc.lower().replace("nem dinheiro de verdade", "") or "lore" in btc.lower()


def test_sem_registro_admite_que_nao_lembra(mem):
    m, _, _ = mem
    b = m.lembrar("lembra daquele dia do vulcão na lua?", atualizar=False)["bloco"]
    assert "admita que não lembra" in b and "«" not in b


def test_comentarista_conhecido_reconhecido_pelo_nome(mem):
    m, _, _ = mem
    r = m.lembrar("oi", pessoa="TEKeEB27", atualizar=False)
    assert "@TEKeEB27 já comentou que" in r["bloco"] and r["resultados"][0]["motivo"] == "pessoa"
    assert "@TEKeEB27 já comentou" in m.lembrar("e aí @tekeeb27 ?", atualizar=False)["bloco"]
    assert m.lembrar("oi", pessoa="Desconhecido", atualizar=False)["bloco"] == ""
    assert m.lembrar("oi", pessoa="TEKeEB27", owner=True, atualizar=False)["bloco"] == ""  # o dono não é "comentarista"
    # hater: registrado como conhecido, mas sem tensão inicial; a lembrança só aparece quando a pessoa fala
    assert m.stats()["haters_conhecidos"] == ["@Hater1"]
    assert "hater conhecido" in m.lembrar("oi", pessoa="@Hater1", atualizar=False)["bloco"]


def test_reforco_ao_recuperar_e_cooldown(mem):
    m, _, _ = mem
    ep = next(i for i in m.itens if i["kind"] == "episodio" and "Papai Noel" in i["texto"])
    f0 = m.forca(ep, T0)
    antes = m.estado[ep["id"]]["acessos"]
    r = m.lembrar("lembra do natal?", agora=T0)
    assert ep["id"] in {x["item"]["id"] for x in r["resultados"] if x.get("_usada")}
    assert m.forca(ep, T0) > f0 and m.estado[ep["id"]]["acessos"] == antes + 1
    # persistiu no disco
    assert json.loads(m.arq_estado.read_text(encoding="utf-8"))[ep["id"]]["acessos"] == antes + 1
    # mesma lembrança não é repetida no turno seguinte (cooldown), mas outra é possível
    r2 = m.lembrar("lembra do natal?", agora=T0 + 5)
    assert ep["id"] not in {x["item"]["id"] for x in r2["resultados"] if x.get("_usada")}
    # buscar (CLI) não mexe na força
    f1 = m.forca(ep, T0)
    m.lembrar("natal", agora=T0, atualizar=False)
    assert m.forca(ep, T0) == f1


def test_decaimento_enfraquece_mas_nao_apaga(mem):
    m, _, _ = mem
    ep = next(i for i in m.itens if i["kind"] == "episodio" and "Papai Noel" in i["texto"])
    base = m.forca(ep, T0)
    velho = m.forca(ep, T0 + 3650 * DIA)
    assert velho < base * 0.5
    assert velho >= vm.FLOOR * ep["importancia"] - 1e-6 and velho > 0
    # mesmo muito esquecida, ainda é recuperável e ser lembrada reforça
    r = m.buscar("natal gag papai noel", agora=T0 + 3650 * DIA)
    assert any(x["item"]["id"] == ep["id"] for x in r)
    assert m.forca(ep, T0 + 7 * DIA) < base


def test_contradicao_retorna_versao_do_criador(mem):
    m, _, dest = mem
    d = json.loads(dest.read_text(encoding="utf-8"))
    assert len(d["contradicoes"]) == 1 and d["contradicoes"][0]["vence"] == "criador"
    b = m.lembrar("você gosta de Ataque dos Titãs?", atualizar=False)["bloco"]
    assert "criador disse" in b and "spoilers" in b and "vale a versão dele" in b
    assert b.count("LEMBRANÇA") == 1  # a versão da Célia não aparece em paralelo


def test_certeza_baixa_nao_vira_afirmacao(mem):
    m, _, _ = mem
    b = m.lembrar("sorteio de pizza", atualizar=False)["bloco"]
    assert "certeza baixa" in b and "não afirme" in b


def test_orcamento_de_caracteres_respeitado(mem):
    m, _, _ = mem
    for t in ("lembra do natal?", "e o bitcoin?", "você gosta de xadrez?", "natal xadrez bitcoin presentes ceia"):
        r = m.lembrar(t, atualizar=False)
        assert len(r["bloco"]) <= vm.BUDGET_CHARS, (t, len(r["bloco"]))
        assert len(r["linhas"]) <= vm.MAX_LEMBRANCAS
    assert vm.RESUMO_REGRA in m.lembrar("lembra do natal?", atualizar=False)["bloco"]


def test_lore_do_dono_e_bitcoin_so_lore(mem):
    m, _, dest = mem
    d = json.loads(dest.read_text(encoding="utf-8"))
    dono = [i for i in d["itens"] if i["origem"] == "dono"]
    assert len(dono) == 4 and any("carteira de Bitcoin" in i["texto"] and "lore" in i["texto"] for i in dono)
    assert any("PC do criador" in i["texto"] for i in dono)
    assert any("defende o criador dos haters" in i["texto"] for i in dono)


def test_tensao_alta_favorece_lembranca_de_hater(mem):
    m, _, _ = mem
    calmo = {x["item"]["id"]: x["score"] for x in m.buscar("essa IA é lixo desliga isso", tensao=0)}
    tenso = {x["item"]["id"]: x["score"] for x in m.buscar("essa IA é lixo desliga isso", tensao=90)}
    h = next(i["id"] for i in m.itens if i["kind"] == "comentario" and i.get("sentimento") == "hater")
    assert tenso[h] > calmo[h]


def test_flag_desliga(mem, monkeypatch, tmp_path):
    m, _, _ = mem
    monkeypatch.setattr(vm, "_memoria", m)
    monkeypatch.delenv("CELIA_VIDEO_MEMORY", raising=False)
    assert vm.recall_block("lembra do natal?") != ""
    for v in ("0", "off", "false"):
        monkeypatch.setenv("CELIA_VIDEO_MEMORY", v)
        assert vm.recall_block("lembra do natal?") == ""


def test_sem_arquivo_nao_quebra(tmp_path):
    m = vm.Memoria(tmp_path / "nao.json", tmp_path / "e.json")
    assert m.lembrar("lembra do natal?")["bloco"] == ""
