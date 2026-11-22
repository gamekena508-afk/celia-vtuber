"""Contas Cloudflare separadas (3 LLM / 3 ASR) e Whisper da Workers AI como reserva do Groq."""

from __future__ import annotations

import json

import pytest

from celia.asr.cloudflare_whisper import CloudflareWhisperPool, CloudflareWhisperUnavailable
from celia.core.cloudflare_contas import validar_separacao
from celia.core.quota_ledger import QuotaLedger

SEGREDO = "TOKEN-SECRETO-NAO-PODE-VAZAR"


def _providers():
    from celia.core.model_chain import load_config

    cfg = load_config()
    return cfg, QuotaLedger


def _ledger(tmp_path):
    from celia.core.model_chain import load_config

    cfg = load_config()
    return QuotaLedger(cfg["providers"], tmp_path, cfg.get("neuron_rates") or {})


class Resp:
    def __init__(self, status=200, corpo=None, text=""):
        self.status_code = status
        self._corpo = corpo
        self.text = text or json.dumps(corpo or {})

    def json(self):
        return self._corpo


class FakeClient:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = []

    def post(self, url, headers=None, json=None, timeout=None):  # noqa: A002
        self.chamadas.append((url, headers, json))
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _contas():
    return [{"nome": f"Conta{i}", "link": f"cf-asr/conta{i}", "account_id": f"acc{i}", "token": SEGREDO}
            for i in (1, 2, 3)]


def _ok(texto="olá mundo"):
    return Resp(200, {"result": {"text": texto}, "success": True})


def test_provider_of_separa_texto_e_transcricao(tmp_path):
    led = _ledger(tmp_path)
    assert led.provider_of("cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast") == "cloudflare"
    assert led.provider_of("cf-asr/conta1") == "cloudflare-asr"


def test_ok_parseia_texto_e_gasta_neurons_so_do_asr(tmp_path):
    led = _ledger(tmp_path)
    cli = FakeClient([_ok("  Boa noite  ")])
    pool = CloudflareWhisperPool(_contas(), led, language="pt", client=cli)
    assert pool.transcribe(b"RIFFwav", 60.0) == "Boa noite"
    url, headers, corpo = cli.chamadas[0]
    assert url.endswith("/acc1/ai/run/@cf/openai/whisper-large-v3-turbo")
    assert corpo["language"] == "pt" and isinstance(corpo["audio"], str)
    assert led.usage("cloudflare-asr")["used"] == pytest.approx(46.4)
    assert led.usage("cloudflare")["used"] == 0


def test_rodizio_entre_as_tres_contas(tmp_path):
    led = _ledger(tmp_path)
    cli = FakeClient([_ok(), _ok(), _ok(), _ok()])
    pool = CloudflareWhisperPool(_contas(), led, client=cli)
    for _ in range(4):
        pool.transcribe(b"x", 1.0)
    assert [c[0].split("/accounts/")[1].split("/")[0] for c in cli.chamadas] == ["acc1", "acc2", "acc3", "acc1"]


def test_429_vai_para_a_proxima_conta_e_bloqueia_so_ela(tmp_path):
    from celia.asr.cloudflare_whisper import _HttpError

    led = _ledger(tmp_path)
    cli = FakeClient([Resp(429, text="[429] daily free allocation of 10,000 neurons"), _ok("segunda")])
    pool = CloudflareWhisperPool(_contas(), led, client=cli)
    assert pool.transcribe(b"x", 1.0) == "segunda"
    assert led.can_use("cf-asr/conta1", "asr")[0] is False
    assert led.can_use("cf-asr/conta2", "asr")[0] is True
    assert led.can_use("cf-asr/conta3", "asr")[0] is True
    assert _HttpError  # importável


def test_todas_falham_levanta_indisponivel(tmp_path):
    led = _ledger(tmp_path)
    cli = FakeClient([Resp(500, text="boom"), Resp(500, text="boom"), RuntimeError("rede")])
    pool = CloudflareWhisperPool(_contas(), led, client=cli)
    with pytest.raises(CloudflareWhisperUnavailable):
        pool.transcribe(b"x", 1.0)


def test_resposta_fora_do_formato_vira_jsn(tmp_path):
    led = _ledger(tmp_path)
    cli = FakeClient([Resp(200, {"success": False}), _ok("certo")])
    pool = CloudflareWhisperPool(_contas(), led, client=cli)
    assert pool.transcribe(b"x", 1.0) == "certo"


def test_token_e_account_id_nunca_vao_para_o_ledger(tmp_path):
    led = _ledger(tmp_path)
    cli = FakeClient([Resp(429, text="[429] limit"), RuntimeError(f"falha em acc2 {SEGREDO}"), _ok()])
    pool = CloudflareWhisperPool(_contas(), led, client=cli)
    pool.transcribe(b"x", 2.0)
    todo = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in tmp_path.rglob("*") if p.is_file())
    assert SEGREDO not in todo
    assert "acc1" not in todo and "acc3" not in todo


def test_validar_separacao_recusa_conta_nos_dois_grupos():
    cfg = {"cloudflare_contas": {"llm": ["A", "B"], "asr": ["B", "C"]}}
    with pytest.raises(ValueError):
        validar_separacao(cfg)
    validar_separacao({"cloudflare_contas": {"llm": ["A", "B"], "asr": ["C"]}})


def test_config_real_tem_as_seis_contas_todas_em_llm_e_o_whisper_desligado():
    from celia.asr.cloudflare_whisper import construir
    from celia.core.model_chain import load_config

    cfg = load_config()
    validar_separacao(cfg)
    sec = cfg["cloudflare_contas"]
    assert len(sec["llm"]) == 6 and len(set(sec["llm"])) == 6
    assert sec["asr"] == []
    assert cfg["cloudflare_asr"]["enabled"] is False
    assert construir() is None


def test_groq_429_chama_cloudflare_e_devolve_texto(monkeypatch):
    import httpx

    from celia.asr import openai_compatible_asr as mod

    chamadas = []

    class PoolFalso:
        def transcribe(self, wav, dur):
            chamadas.append(dur)
            return "texto da cloudflare"

        def reportar_fim(self):
            chamadas.append("fim")

    req = httpx.Request("POST", "http://x/v1/audio/transcriptions")
    erro = httpx.HTTPStatusError("429", request=req, response=httpx.Response(429, request=req))
    assert mod._limite_de_cota(erro) is True
    assert mod._limite_de_cota(RuntimeError("x")) is False

    asr = mod.VoiceRecognition.__new__(mod.VoiceRecognition)
    asr._cf = PoolFalso()
    asr.SAMPLE_RATE = 16000
    texto = asr._tentar_cloudflare(b"RIFF", 1.5)
    assert texto == "texto da cloudflare" and chamadas == [1.5]
