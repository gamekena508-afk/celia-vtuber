"""Regressão da integração Célia V02 com o gate privado do Central IA."""
from __future__ import annotations
import json
import numpy as np
import pytest
import soundfile as sf

from celia.tts import production_voice
from celia.tts.omnivoice_tts import TTSEngine, OmniVoiceSynthesisError


def _engine(monkeypatch, tmp_path, *, approved=True):
    reference=production_voice.REFERENCE_PATH
    engine=TTSEngine(model_path=production_voice.MODEL_PATH,
                     ref_audio=reference,ref_text=production_voice.REF_TEXT)
    monkeypatch.setattr(engine,"_validate_production_assets",lambda: None)
    audio=tmp_path/"central-approved.wav"
    sf.write(audio,0.2*np.sin(np.linspace(0,100,24000,dtype=np.float32)),24000)
    calls=[]
    class Client:
        def generate(self,channel,language,text,**kwargs):
            calls.append((channel,language,text,kwargs))
            return ({"status":"APPROVED","path":str(audio),"seed":8347423,
                     "similarity_ecapa":0.72,"steps":10,"threshold":0.60,
                     "attempts":2,"evaluations":[{"attempt":1,"approved":False},
                                                 {"attempt":2,"approved":True}]}
                    if approved else
                    {"status":"NO_APPROVED_AUDIO","path":None,"steps":10,"attempts":4})
    monkeypatch.setattr(engine,"_central_client",lambda: Client())
    return engine,calls


def test_production_uses_adaptive_seed_and_records_similarity(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path)
    engine,calls=_engine(monkeypatch,tmp_path)
    output=engine.generate_audio("Oi, turma!",emotion="happy")
    assert len(calls)==1
    assert calls[0][0:3]==("celia","pt","Oi, turma!")
    assert calls[0][3]["num_step"]==10
    assert calls[0][3]["emotion"]=="happy"
    assert sf.info(output).samplerate==24000
    record=json.loads(__import__("pathlib").Path(output).with_suffix(".run.json").read_text(encoding="utf-8"))
    assert record["parameters"]["seed"]==8347423
    assert record["parameters"]["num_step"]==10
    assert record["biometric_verification"]["score"]==0.72
    assert record["biometric_verification"]["attempts"]==2


def test_no_approved_audio_is_not_written_or_played(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path)
    engine,calls=_engine(monkeypatch,tmp_path,approved=False)
    with pytest.raises(OmniVoiceSynthesisError,match="NO_APPROVED_AUDIO"):
        engine.generate_audio("Oi, turma!")
    assert len(calls)==1
    assert not list(tmp_path.rglob("*.wav")) or all(p.name=="central-approved.wav" for p in tmp_path.rglob("*.wav"))
    records=list(tmp_path.rglob("*.run.json"))
    assert len(records)==1
    assert json.loads(records[0].read_text(encoding="utf-8"))["status"]=="error"


def test_production_policy_ten_steps_no_fixed_seed():
    from celia.config_manager.tts import OmniVoiceConfig
    fields=OmniVoiceConfig.model_fields
    assert fields["seed"].default is None
    assert fields["num_step"].default==10
    assert production_voice.GENERATION_OPTIONS["num_step"]==10
    assert "seed" not in production_voice.PRODUCTION_CONFIG
