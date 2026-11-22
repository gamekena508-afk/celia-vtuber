"""Conduta conversacional antes da persona (src/celia/core/conduta.py). Offline, sem modelo, sem cota.

Contrato de 2026-10-08: no prompt de CONVERSA HUMANA, `[CONDUTA CONVERSACIONAL]` (como ler a situação) vem ANTES de `[PERSONA]` (a voz) e antes
da ficha do Jev; o turno de MÁQUINA continua byte a byte como era. Os exemplos mostram o jeito e não repetem nenhuma frase dos casos de teste.
O que NÃO se prova aqui (e só o A/B com o 70B real prova): que a fala fica melhor.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

import pytest

from celia.core import conduta
from celia.core import prompt_mapa as pm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

# SHA-256 do bloco `[CONVERSA NATURAL]` exatamente como estava em basic_memory_agent.py antes de ser movido (1.045 chars).
SHA_CONVERSA_NATURAL_ANTIGA = "3e06692ef5daaee7540a22c93bfe65bd2e31b426a8818192fde3987e0a6f9cbc"
# Frases dos casos do A/B e das reclamações do Pai: nenhuma pode aparecer nos exemplos (senão o A/B mediria cópia, não generalização).
FRASES_DE_TESTE = ("bora celia", "ta celia", "ta me ouvindo", "foi demais", "cara triste", "ta fraca", "blender", "celia pato")


def sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", t.lower()) if not unicodedata.combining(c))


def devolve_pergunta(resposta: str) -> bool:
    """Detector de 'devolve a pergunta ao Pai'. Controle positivo e negativo logo abaixo (lei da validade instrumental)."""
    r = sem_acento(resposta)
    return "?" in r or bool(re.search(r"\bo que (tu |voce )?(quer|ta rolando|aconteceu)|\be agora\b|conta pra mim|explica melhor", r))


def test_detector_dispara_no_positivo_e_fica_quieto_no_negativo():
    for ruim in ("[teasing] Bora. O que tá rolando?", "[neutral] Tô aqui. E agora?", "[teasing] Beleza, conta pra mim."):
        assert devolve_pergunta(ruim), ruim
    for boa in ("[teasing] Vamo. Abertura fechada, agora o meio.", "[teasing] Na linha. Pode falar."):
        assert not devolve_pergunta(boa), boa


# ───────────────────────────── bloco e fonte única ─────────────────────────────
def test_turno_de_maquina_recebe_o_bloco_antigo_byte_a_byte():
    texto = conduta.bloco_conversa_natural()
    assert len(texto) == 1045 and hashlib.sha256(texto.encode("utf-8")).hexdigest() == SHA_CONVERSA_NATURAL_ANTIGA
    assert texto.startswith("\n\n[CONVERSA NATURAL]\n")


def test_conducta_traz_a_leitura_primeiro_e_as_regras_antigas_sem_reescrever():
    b = conduta.bloco_conduta()
    assert b.startswith(conduta.CABECALHO_CONDUTA)
    assert b.index("Leia a situação") < b.index(conduta.REGRAS_NA_CONDUTA[0]) < b.index("Jeito:")
    for regra in conduta.REGRAS_NA_CONDUTA:
        assert b.count(regra) == 1, regra
    # 2026-11-12: a conversa humana usa a versão COMPACTA das regras (mesmas proibições, ditas uma vez); o turno de máquina segue com as oito, byte a byte (SHA acima).
    assert len(conduta.REGRAS_NA_CONDUTA) < len(conduta.REGRAS_CONVERSA_NATURAL)
    compacto = " ".join(conduta.REGRAS_NA_CONDUTA)
    for essencial in ("UMA pergunta", "como assim", "PRIMEIRA frase", "quem é você", "não invente"):
        assert essencial in compacto, essencial
    assert "[CONVERSA NATURAL]" not in b


def test_poucos_exemplos_fortes_e_nenhuma_frase_dos_casos_de_teste():
    assert len(conduta.EXEMPLOS) == 3                                              # "poucos exemplos fortes", não 20 regras
    tudo = sem_acento(conduta.bloco_conduta())
    for frase in FRASES_DE_TESTE:
        assert frase not in tudo, frase


def test_nenhum_exemplo_devolve_a_pergunta_ao_pai():
    for situacao, fala, resposta in conduta.EXEMPLOS:
        assert not resposta.startswith(("[teasing]", "[neutral]")), resposta
        assert not devolve_pergunta(resposta), resposta
        assert "Criador" not in resposta and "Pai" not in resposta


def test_exemplos_cobrem_avanco_confirmacao_e_pergunta_direta():
    falas = [f for _, f, _ in conduta.EXEMPLOS]
    assert falas[0].rstrip(".") in {"Vamo", "Partiu", "Segue"} and falas[1].rstrip(".") == "Beleza" and falas[2].endswith("?")


def test_orcamento_de_tokens_a_conduta_custa_no_maximo_700_chars_a_mais_que_o_bloco_antigo():
    humano = len(conduta.bloco_conduta()) + len("\n\n") + len(conduta.cabecalho_persona()) + len("\n")
    antigo = len(conduta.bloco_conversa_natural())
    assert humano - antigo <= 700, humano - antigo                                  # teto do orçamento da dieta do Jev (test_jev::old_diet)


# ───────────────────────────── ordem no prompt real ─────────────────────────────
@pytest.fixture(scope="module")
def sistema_humano() -> str:
    import celia_prompt_mapa as cli
    system, _mensagens, _ = cli.montar("Bora, Célia.")
    return system


def test_ordem_no_prompt_de_conversa_conduta_antes_da_persona_antes_da_ficha(sistema_humano):
    s = sistema_humano
    idx = [s.index(x) for x in (conduta.CABECALHO_CONDUTA, conduta.CABECALHO_PERSONA, "Você é Célia,", "JEITO:", "[FOCO DA CONVERSA]", "=== CONTEXTO DO SISTEMA ===")]
    assert idx == sorted(idx) and len(set(idx)) == len(idx)
    assert s.count(conduta.CABECALHO_CONDUTA) == 1 and s.count(conduta.CABECALHO_PERSONA) == 1


def test_regras_aparecem_uma_vez_e_o_bloco_antigo_nao_sobra_no_fim(sistema_humano):
    assert "[CONVERSA NATURAL]" not in sistema_humano
    for regra in conduta.REGRAS_NA_CONDUTA:
        assert sistema_humano.count(regra) == 1, regra
    assert sistema_humano.count(conduta.REGRAS_CONVERSA_NATURAL[-1]) == 0


def test_mapa_do_prompt_real_cobre_tudo_e_rotula_os_blocos_novos(sistema_humano):
    blocos = pm.mapear(sistema_humano, ROOT)
    assert sum(b.chars for b in blocos) == len(sistema_humano)
    assert pm.cabecalhos_desconhecidos(sistema_humano, blocos) == []
    assert pm.resumo(blocos)["sem_rotulo_chars"] == 0
    nomes = [b.nome for b in blocos]
    i_conduta = next(i for i, n in enumerate(nomes) if n.startswith("conduta conversacional"))
    assert i_conduta < nomes.index("cabeçalho da persona") < nomes.index("persona: abertura") < nomes.index("persona: estilo contextual") < nomes.index("persona: uso proporcional de memória")
    assert blocos[i_conduta].camada == pm.CONDUTA and blocos[nomes.index("cabeçalho da persona")].camada == pm.PERSONALIDADE


def test_exemplos_de_continuidade_entram_como_USAR_no_mapa(sistema_humano):
    bloco = next(b for b in pm.mapear(sistema_humano, ROOT) if b.nome.startswith("conduta conversacional"))
    usar = [e["fala"] for e in bloco.exemplos if e["polaridade"] == "USAR"]
    for _, _, resposta in conduta.EXEMPLOS:
        assert resposta in usar, resposta


def _sistema_de_maquina() -> str:
    import jev_corpus_replay as rp
    src = inspect.getsource(rp.build_b_prompt)
    alvo = '"turn_id": case["turn_id"]}'
    assert src.count(alvo) == 1
    ns = dict(vars(rp))
    exec(src.replace(alvo, '"turn_id": case["turn_id"], "machine_response": True}'), ns)
    caso = {"turn_id": "t-maquina", "ts": time.time(), "text": "Bora, Célia.", "raw_input": "Bora, Célia.", "profile": "gravacao", "history": [], "celia_said": []}
    return ns["build_b_prompt"](caso, rp.persona_system())[0]


def test_turno_de_maquina_nao_ganha_conduta_nem_cabecalho_de_persona():
    s = _sistema_de_maquina()
    assert conduta.CABECALHO_CONDUTA not in s and conduta.CABECALHO_PERSONA not in s
    assert s.startswith("Você é Célia")                                            # persona direto, como sempre foi


# ───────────────────────────── comparação BASE × NOVO ─────────────────────────────
def _sintetico(com_conduta: bool, trocar_ordem: bool = False) -> str:
    base = ["INTERLOCUTOR ATUAL: Criador.", "Você é Célia: VTuber IA.", "# TOM\nSarcasmo.", "# PROIBIDO\n- Emojis."]
    if com_conduta:
        base.insert(1, conduta.bloco_conduta() + "\n\n" + conduta.cabecalho_persona())
    if trocar_ordem:
        base[-1], base[-2] = base[-2], base[-1]
    return "\n\n".join(base) + "\n\n=== CONTEXTO DO SISTEMA ===\n[SESSÃO]\n- tema\n=== FIM DO CONTEXTO DO SISTEMA ===\n"


def test_comparar_mostra_o_que_entrou_o_que_saiu_e_os_exemplos():
    a, b = pm.mapa_canonico(_sintetico(False), ROOT), pm.mapa_canonico(_sintetico(True), ROOT)
    d = pm.comparar(a, b)
    assert not d["identico"] and d["sairam"] == [] and len(d["entraram"]) == 2
    assert d["chars"][1] - d["chars"][0] > 700 and not d["ordem_dos_blocos_comuns_mudou"]
    assert any("A abertura fechou; agora vem o meio" in fala for fala, _ in d["falas_entraram"]) and d["falas_sairam"] == []
    texto = pm.formatar_comparacao(d)
    for trecho in ("ENTRARAM", "SAÍRAM", "Ordem no NOVO", "Falas-exemplo", "Por camada"):
        assert trecho in texto, trecho


def test_comparar_controle_negativo_prompts_iguais_sao_identicos():
    m = pm.mapa_canonico(_sintetico(True), ROOT)
    assert pm.comparar(m, pm.mapa_canonico(_sintetico(True), ROOT))["identico"] is True
    assert "IDÊNTICOS" in pm.formatar_comparacao(pm.comparar(m, m))


def test_comparar_detecta_troca_de_ordem_mesmo_com_o_mesmo_conteudo():
    a, b = pm.mapa_canonico(_sintetico(True), ROOT), pm.mapa_canonico(_sintetico(True, trocar_ordem=True), ROOT)
    d = pm.comparar(a, b)
    assert d["identico"] is False and d["entraram"] == [] and d["sairam"] == [] and d["ordem_dos_blocos_comuns_mudou"] is True


def test_mapa_canonico_e_deterministico():
    s = _sintetico(True)
    assert json.dumps(pm.mapa_canonico(s, ROOT), sort_keys=True) == json.dumps(pm.mapa_canonico(s, ROOT), sort_keys=True)
