"""Política de avisos (core/avisos.py): amarelo só chat, vermelho voz + chat, o resto escondido. Sem rede."""

import asyncio
from types import SimpleNamespace

import pytest

from celia.core import avisos
from celia.core.quota_ledger import QuotaLedger

PROVIDERS = {
    "cloudflare": {"match": ["cf/"], "kind": "requests", "daily_limit": 10},
    "openrouter-free": {"match": ["openrouter/"], "match_suffix": ":free", "kind": "requests",
                        "daily_limit": 3, "hard_limit": True},
    "gemini": {"match": ["gemini/"], "kind": "requests"},
}
CHAIN_YAML = """
celia:
  mode: persona
  chain: [cf/a, openrouter/b:free, gemini/c]
jev:
  mode: tool
  chain: [cf/a]
"""


@pytest.fixture
def saida(tmp_path, monkeypatch):
    chain = tmp_path / "chains.yaml"
    chain.write_text(CHAIN_YAML, encoding="utf-8")
    monkeypatch.setenv("CELIA_MODEL_CHAINS", str(chain))
    avisos.resetar()
    avisos.configurar(None)
    chat, voz = [], []

    async def fake_chat(texto, persistir=True, ao_vivo=True):
        chat.append(texto)
        return True

    async def fake_voz(texto, chave):
        voz.append(texto)
        return True

    monkeypatch.setitem(avisos.ENVIO, "chat", fake_chat)
    monkeypatch.setitem(avisos.ENVIO, "voz", fake_voz)
    monkeypatch.setattr(avisos, "_gravando", lambda: False)
    ledger = QuotaLedger(PROVIDERS, tmp_path / "cota")
    ledger.add_observer(avisos.observar_ledger)
    return SimpleNamespace(chat=chat, voz=voz, ledger=ledger)


def rodar(fn):
    """Executa fn dentro de um loop e deixa os avisos agendados terminarem."""
    async def main():
        resultado = fn()
        if asyncio.iscoroutine(resultado):
            await resultado
        await asyncio.sleep(0.05)
    asyncio.run(main())


def test_live_alerts_never_enter_voice_or_chat_even_with_custom_transports(saida,monkeypatch):
    from celia.live_mode import gate
    monkeypatch.setattr(gate,'live_active',lambda:True)
    async def run():
        await avisos.amarelo('Todas as chaves API estão esgotadas.')
        await avisos._emitir_vermelho('ia','Todas as rotas de IA estão indisponíveis.')
    asyncio.run(run())
    assert saida.chat==[] and saida.voz==[]


def test_cota_numerica_avisa_80_e_95_uma_vez_so_no_chat(saida):
    def calls():
        for _ in range(12):
            saida.ledger.record("celia", "cf/a", "OK")
    rodar(calls)
    assert len(saida.chat) == 2
    assert "80%" in saida.chat[0] and "Cloudflare" in saida.chat[0] and "Zera" in saida.chat[0]
    assert "95%" in saida.chat[1]
    assert all(t.startswith("🟡") for t in saida.chat)
    assert saida.voz == []


def test_cota_esgotada_com_limite_duro_mostra_volta_e_proxima_rota(saida):
    def calls():
        for _ in range(3):
            saida.ledger.record("celia", "openrouter/b:free", "OK")
    rodar(calls)
    esgotou = [t for t in saida.chat if "esgotada" in t]
    assert len(esgotou) == 1
    assert "OpenRouter (free)" in esgotou[0] and "Volta" in esgotou[0] and "3 de 3" in esgotou[0]
    assert "Usando a próxima rota" in esgotou[0]
    assert saida.voz == []


def test_esgotou_sem_outra_rota_nao_promete_proxima(saida, tmp_path, monkeypatch):
    sozinha = tmp_path / "so_or.yaml"
    sozinha.write_text("celia:\n  chain: [openrouter/b:free]\n", encoding="utf-8")
    monkeypatch.setenv("CELIA_MODEL_CHAINS", str(sozinha))

    def calls():
        for _ in range(3):
            saida.ledger.record("celia", "openrouter/b:free", "OK")
    rodar(calls)
    esgotou = [t for t in saida.chat if "esgotada" in t]
    assert len(esgotou) == 1 and "próxima rota" not in esgotou[0]


def test_gemini_sem_numero_nao_inventa_percentual_e_so_avisa_ao_esgotar(saida):
    def calls():
        for _ in range(30):
            saida.ledger.record("celia", "gemini/c", "OK")
    rodar(calls)
    assert saida.chat == []
    rodar(lambda: saida.ledger.record("celia", "gemini/c", "CTD", message="429 per day", pause_sec=None))
    assert len(saida.chat) == 1
    assert "Gemini" in saida.chat[0] and "esgotada" in saida.chat[0] and "Volta" in saida.chat[0]
    assert "%" not in saida.chat[0] and "de 0" not in saida.chat[0]


def test_troca_fallback_retry_e_timeout_nao_viram_mensagem(saida):
    def calls():
        for code in ("CTM", "LNT", "ERR", "JSN", "VAZ", "MRT", "CHV"):
            saida.ledger.record("celia", "gemini/c", code)
        saida.ledger.record("celia", "cf/a", "OK")
    rodar(calls)
    assert saida.chat == [] and saida.voz == []


def test_marca_de_aviso_persiste_e_nao_repete(saida, tmp_path):
    assert saida.ledger.mark_aviso("cloudflare", "p80") is True
    assert saida.ledger.mark_aviso("cloudflare", "p80") is False
    outro = QuotaLedger(PROVIDERS, tmp_path / "cota")
    assert outro.mark_aviso("cloudflare", "p80") is False


def test_fila_da_celia_esgotada_fala_e_escreve_uma_vez(saida):
    rodar(lambda: saida.ledger.record("celia", "", "FIM"))
    assert len(saida.voz) == 1 and "rotas de IA" in saida.voz[0]
    assert saida.chat == ["🔴 " + saida.voz[0]]
    rodar(lambda: saida.ledger.record("celia", "", "FIM"))  # segue fora: não repete
    assert len(saida.voz) == 1


def test_acao_sem_nenhum_recurso_vem_do_fim_do_jev(saida):
    rodar(lambda: saida.ledger.record("jev", "", "FIM"))
    assert len(saida.voz) == 1 and "nenhuma ferramenta" in saida.voz[0]


def test_vermelho_na_gravacao_diz_que_compromete(saida, monkeypatch):
    monkeypatch.setattr(avisos, "_gravando", lambda: True)
    rodar(lambda: saida.ledger.record("celia", "", "FIM"))
    assert saida.voz[0].endswith("Isso compromete a gravação.")


def test_tts_so_avisa_apos_falhas_seguidas_e_so_no_chat(saida):
    def calls():
        avisos.tts_falhou()
        avisos.tts_falhou()
        avisos.tts_ok()          # voltou: zera
        avisos.tts_falhou()
        avisos.tts_falhou()
    rodar(calls)
    assert saida.chat == [] and saida.voz == []
    rodar(avisos.tts_falhou)
    assert saida.voz == []                                   # sem TTS não há como falar
    assert len(saida.chat) == 1 and "voz está indisponível" in saida.chat[0]


def _monitor_quieto(monkeypatch, ram=False, servico_ok=True):
    monkeypatch.setattr(avisos, "_checar_ram", lambda: ram)
    monkeypatch.setattr(avisos, "_checar_gpu", lambda: False)
    monkeypatch.setattr(avisos, "_asr_fora", lambda: False)

    async def servico(url):
        return servico_ok
    monkeypatch.setattr(avisos, "_checar_servico", servico)


def test_ram_critica_exige_leituras_seguidas(saida, monkeypatch):
    leituras = iter([True, False, True, True, True])
    _monitor_quieto(monkeypatch)
    monkeypatch.setattr(avisos, "_checar_ram", lambda: next(leituras))
    avisos.configurar({"servicos": []})

    async def ciclos(n):
        for _ in range(n):
            await avisos.ciclo_monitor()
    rodar(lambda: ciclos(2))                    # oscilação (True, False): nada
    assert saida.chat == []
    rodar(lambda: ciclos(3))                    # True, True, True: avisa
    assert len(saida.voz) == 1 and "memória" in saida.voz[0]


def test_servico_essencial_morto_diz_o_impacto(saida, monkeypatch):
    _monitor_quieto(monkeypatch, servico_ok=False)
    avisos.configurar({"servicos": [{"nome": "gateway de IA", "url": "http://x", "impacto": "fico sem modelos"}]})

    async def ciclos():
        for _ in range(3):
            await avisos.ciclo_monitor()
    rodar(ciclos)
    assert len(saida.voz) == 1
    assert saida.voz[0] == "O gateway de IA parou, e fico sem modelos."


def test_alertas_desligados_nao_dizem_nada(saida):
    avisos.configurar({"enabled": False})
    rodar(lambda: saida.ledger.record("celia", "", "FIM"))
    assert saida.chat == [] and saida.voz == []


def test_focus_fallback_nao_esconde_falha_com_frase_generica():
    from celia.agent.agents.basic_memory_agent import BasicMemoryAgent

    agente = SimpleNamespace(_focus_norm=lambda t: t.lower())
    assert BasicMemoryAgent._focus_fallback(agente, "me conta uma coisa qualquer") == ""
    assert "Fechado" not in BasicMemoryAgent._focus_fallback(agente, "qualquer pedido")
    assert BasicMemoryAgent._falha_de_provedor("Error calling the chat endpoint: todos falharam.")
    assert not BasicMemoryAgent._falha_de_provedor("Oi, tudo bem?")
