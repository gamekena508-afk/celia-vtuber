"""ASR único = TAGARELA (CPU, :8011), sem reserva (decisão de 2026-10-04).

O Whisper local (:8010) foi desinstalado porque errava e alucinava. Se o TAGARELA cair, a Célia fica
sem transcrição e o monitor de avisos diz que ela está surda; nada responde no lugar dele.
"""
import sys
import types
from pathlib import Path

import httpx
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from celia.asr import openai_compatible_asr as mod  # noqa: E402
from celia.asr.openai_compatible_asr import VoiceRecognition, _audio_longo  # noqa: E402


def _achar_cfg(d):
    if isinstance(d, dict):
        c = d.get("openai_compatible_asr")
        if isinstance(c, dict) and "base_url" in c:
            return c
        for v in d.values():
            r = _achar_cfg(v)
            if r:
                return r
    return None


def test_conf_real_usa_so_tagarela_sem_reserva():
    cfg = _achar_cfg(yaml.safe_load((ROOT / "conf.yaml").read_text(encoding="utf-8")))
    assert cfg["base_url"] == "http://127.0.0.1:8011/v1"
    assert cfg["model"] == "tagarela-q2-int8"
    assert not cfg.get("fallback_base_url"), "voltou uma reserva de ASR"
    assert not cfg.get("fallback_model")
    assert cfg["cloudflare_fallback"] is False


def test_nada_no_launcher_ou_no_conf_aponta_para_a_porta_8010():
    for rel in ("conf.yaml", "bin/ligar.sh", "bin/parar.sh", "bin/atualizar-painel.py", "bin/preparar_gravacao_celia.py"):
        texto = (ROOT / rel).read_text(encoding="utf-8")
        linhas = [l for l in texto.splitlines() if "8010" in l and not l.lstrip().startswith("#")]
        assert not linhas, (rel, linhas)


def _resp(status):
    return httpx.Response(status, request=httpx.Request("POST", "http://x/v1/audio/transcriptions"))


def test_audio_longo_reconhece_413():
    assert _audio_longo(httpx.HTTPStatusError("x", request=httpx.Request("POST", "http://x"), response=_resp(413)))
    assert not _audio_longo(httpx.HTTPStatusError("x", request=httpx.Request("POST", "http://x"), response=_resp(500)))
    assert not _audio_longo(httpx.ConnectError("x"))


def _vr():
    return VoiceRecognition(base_url="http://127.0.0.1:8011/v1", model="tagarela-q2-int8", lang="pt")


def test_sem_fallback_primario_fora_devolve_vazio_sem_segurar_hold(monkeypatch):
    chamadas = []

    def post(url, **kw):
        chamadas.append(url)
        raise httpx.ConnectError("sem TAGARELA")

    monkeypatch.setattr(mod.httpx, "post", post)
    vr = _vr()
    audio = np.zeros(16000, dtype=np.float32)
    assert vr.transcribe_np(audio) == ""
    assert vr.transcribe_np(audio) == ""
    # Cada fala tenta o TAGARELA de novo (nao existe outro caminho para "segurar"), e so ele e chamado.
    assert len(chamadas) == 2
    assert all(":8011" in c for c in chamadas)
    assert all(":8010" not in c for c in chamadas)


def test_sem_fallback_413_nao_chama_nenhum_outro_endpoint(monkeypatch):
    chamadas = []

    def post(url, **kw):
        chamadas.append(url)
        r = _resp(413)
        r.raise_for_status()

    monkeypatch.setattr(mod.httpx, "post", post)
    vr = _vr()
    assert vr.transcribe_np(np.zeros(16000, dtype=np.float32)) == ""
    assert len(chamadas) == 1


def test_tagarela_respondendo_devolve_o_texto(monkeypatch):
    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"text": " Oi, Célia. "}

    monkeypatch.setattr(mod.httpx, "post", lambda url, **kw: R())
    vr = _vr()
    assert vr.transcribe_np(np.zeros(16000, dtype=np.float32)) == "Oi, Célia."
    assert vr.state == "gateway_ready"


def _singleton_falso(monkeypatch, snapshot):
    class Asr:
        def health_snapshot(self):
            return snapshot

    ctx = types.SimpleNamespace(asr_engine=Asr())
    inst = types.SimpleNamespace(context=ctx)
    fake = types.ModuleType("celia.celia_singleton")
    fake.CeliaSingleton = types.SimpleNamespace(get_instance=lambda: inst)
    monkeypatch.setitem(sys.modules, "celia.celia_singleton", fake)


def test_monitor_diz_asr_fora_quando_o_tagarela_nao_responde(monkeypatch):
    from celia.core import avisos

    _singleton_falso(monkeypatch, {"primary": False, "fallback": False})
    assert avisos._asr_fora() is True
    _singleton_falso(monkeypatch, {"primary": True, "fallback": False})
    assert avisos._asr_fora() is False


def test_aviso_de_asr_fora_diz_que_nao_ouve():
    from celia.core import avisos

    assert "Não consigo te ouvir" in avisos.MENSAGENS["asr"]


def test_validacao_de_tts_usa_groq_e_tagarela_e_nao_o_whisper_local(monkeypatch):
    from celia.tts import audio_content_validation as v

    urls = []

    def fake(path, endpoint, model):
        urls.append((endpoint, model))
        return "oi", {}

    monkeypatch.setattr(v, "_transcribe_content", fake)
    monkeypatch.setattr(v, "sha256_file", lambda p: "x")
    out = v.validate_audio_content(Path("a.wav"), "oi")
    assert (("http://127.0.0.1:8011/v1/audio/transcriptions", "tagarela-q2-int8")) in urls
    assert not any("8010" in e for e, _ in urls)
    assert out["asr_policy"] == "ASR-2026-10-04"
    assert "beam_size" not in out["asr_local_judge"]
