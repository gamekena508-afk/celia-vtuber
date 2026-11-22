"""Mapa do prompt final (src/celia/core/prompt_mapa.py): cobertura total, rótulo obrigatório, origem e polaridade. Offline, sem modelo."""
from __future__ import annotations

import json
import re
import socket
import sys
from pathlib import Path

import pytest

from celia.core import prompt_mapa as pm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def prompt_sintetico() -> str:
    return ("INTERLOCUTOR ATUAL: Criador.\n\nVocê é Célia: VTuber IA.\n# TOM\nSarcasmo.\n# PROIBIDO\n- Emojis.\n"
            "[CONVERSA NATURAL]\n- Responda direto.\n\n=== CONTEXTO DO SISTEMA ===\n[SESSÃO]\n- tema\n=== FIM DO CONTEXTO DO SISTEMA ===\n")


def test_cobertura_total_nenhum_caractere_fica_fora_do_mapa():
    s = prompt_sintetico()
    blocos = pm.mapear(s, ROOT)
    assert sum(b.chars for b in blocos) == len(s)
    assert blocos[0].inicio == 0 and blocos[-1].fim == len(s)
    assert all(a.fim == b.inicio for a, b in zip(blocos, blocos[1:]))          # sem lacuna nem sobreposição


def test_controle_positivo_texto_solto_antes_de_qualquer_cabecalho_e_denunciado():
    assert pm.resumo(pm.mapear("regra solta no começo\n" + prompt_sintetico(), ROOT))["sem_rotulo_chars"] > 0


def test_controle_positivo_bloco_novo_com_cabecalho_proprio_e_denunciado():
    s = prompt_sintetico().replace("[CONVERSA NATURAL]", "[BLOCO NOVO QUE NINGUEM REGISTROU]\n- regra escondida.\n[CONVERSA NATURAL]")
    blocos = pm.mapear(s, ROOT)
    achados = pm.cabecalhos_desconhecidos(s, blocos)
    assert [a[0] for a in achados] == ["[BLOCO NOVO QUE NINGUEM REGISTROU]"]


def test_controle_negativo_prompt_conhecido_nao_tem_sem_rotulo_nem_cabecalho_desconhecido():
    s = prompt_sintetico()
    blocos = pm.mapear(s, ROOT)
    assert pm.resumo(blocos)["sem_rotulo_chars"] == 0 and pm.cabecalhos_desconhecidos(s, blocos) == []


def test_cada_bloco_declara_camada_e_tipo_e_a_ficha_e_dado_nao_instrucao():
    por_nome = {b.nome: b for b in pm.mapear(prompt_sintetico(), ROOT)}
    assert por_nome["ficha do Jev"].tipo == pm.DADO and por_nome["ficha do Jev"].camada == pm.CONTEXTO
    assert por_nome["conversa natural (regras)"].camada == pm.CONDUTA and por_nome["conversa natural (regras)"].tipo == pm.INSTRUCAO
    assert por_nome["tom (sarcasmo, pavio curto)"].camada == pm.PERSONALIDADE


def test_registro_sem_nomes_repetidos_e_com_regex_valida():
    nomes = [r[1] for r in pm.REGISTRO]
    assert len(nomes) == len(set(nomes))
    for r in pm.REGISTRO:
        re.compile(r[0], re.M)
        assert r[2] in {pm.PERSONALIDADE, pm.CONDUTA, pm.SEGURANCA, pm.FERRAMENTAS, pm.FORMA, pm.CONTEXTO}
        assert r[3] in {pm.INSTRUCAO, pm.DADO}


@pytest.mark.parametrize("texto, fala, esperado", [
    ('- Nao use, por exemplo, "Finalmente criou algo novo hoje."; "Milagre, algo original agora."', "Milagre, algo original agora.", "EVITAR"),
    ('- Pode provocar, como: "Que frase esquisita essa."', "Que frase esquisita essa.", "USAR"),
    ('Em pergunta factual direta (ex.: "qual e a capital da Austrália?") entregue o fato.', "qual e a capital da Austrália?", "GATILHO"),
    ('# PROIBIDO\n- Reformular pergunta ("você quer saber se isso?").', "você quer saber se isso?", "EVITAR"),
])
def test_polaridade_das_falas_exemplo(texto, fala, esperado):
    achados = {e["fala"]: e for e in pm.extrair_exemplos(texto)}
    assert achados[fala]["polaridade"] == esperado


def test_fala_curta_demais_nao_vira_exemplo():
    assert pm.extrair_exemplos('- Filler: "Claro!" e "Lamento".') == []


def test_origem_aponta_para_quem_produz_o_texto_nao_para_quem_o_consome():
    blocos = {b.nome: b for b in pm.mapear(prompt_sintetico() + "\n[IDENTIDADE FINAL]\nx\n[CELIA IDENTITY - shared across every operational profile]\ny\n", ROOT)}
    assert blocos["identidade final do falante"].origem.replace("\\", "/").startswith("src/celia/agent/agents/basic_memory_agent.py")
    assert blocos["princípios de identidade"].origem.replace("\\", "/").startswith("src/celia/core/persona_identity.py")
    assert "prompt_mapa.py" not in " ".join(b.origem for b in blocos.values())


def test_integracao_prompt_real_esta_todo_rotulado_e_tem_origem(capsys):
    """Tripwire: se alguém adicionar texto ao prompt sem registrá-lo em prompt_mapa.REGISTRO, este teste acusa o bloco."""
    import celia_prompt_mapa as cli
    system, mensagens, _ = cli.montar("Bora, Célia.")
    blocos = pm.mapear(system, ROOT)
    r = pm.resumo(blocos)
    sem = [(b.nome, b.chars) for b in blocos if b.camada == pm.SEM_ROTULO]
    assert not sem, f"texto sem rótulo no prompt real: {sem} — registre o cabeçalho em prompt_mapa.REGISTRO"
    desconhecidos = pm.cabecalhos_desconhecidos(system, blocos)
    assert not desconhecidos, f"cabeçalho novo dentro de outro bloco: {desconhecidos} — registre em prompt_mapa.REGISTRO"
    assert sum(b.chars for b in blocos) == len(system)
    sem_origem = [b.nome for b in blocos if b.origem == "origem não localizada"]
    assert not sem_origem, f"bloco sem origem no código: {sem_origem}"
    assert r["exemplos"] >= 3 and set(r["por_camada"]) >= {pm.PERSONALIDADE, pm.CONDUTA, pm.CONTEXTO}


def test_cli_json_reporta_o_que_e_carregado_mas_nao_enviado_sem_chamar_modelo(monkeypatch, capsys):
    import celia_prompt_mapa as cli

    def proibido(*a, **k):
        raise AssertionError("o mapa tentou abrir conexão de rede")
    monkeypatch.setattr(socket.socket, "connect", proibido)
    assert cli.main(["--texto", "Bora, Célia.", "--json"]) == 0
    saida = capsys.readouterr().out
    d = json.loads(saida[saida.index("{"):])
    assert any("módulo de assunto" in e and "ausentes do system" in e for e in d["carregados_nao_enviados"])
    assert any("POSITIVE_EXAMPLES" in e and "turno de" in e for e in d["carregados_nao_enviados"])
    assert d["resumo"]["sem_rotulo_chars"] == 0 and d["blocos"][0]["origem"] != "origem não localizada"
    assert all(b["camada"] and b["tipo"] for b in d["blocos"])
