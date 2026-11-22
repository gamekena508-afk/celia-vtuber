import importlib.util
import io
import json
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from celia.integrations.discord.relationship import DiscordRelationshipStore


def _load_capture_module():
    path = ROOT / "integrations" / "discord" / "training_capture.py"
    spec = importlib.util.spec_from_file_location("celia_discord_training_capture_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _wav_bytes() -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 1600)
    return out.getvalue()


def test_owner_has_no_extra_social_anger(tmp_path):
    store = DiscordRelationshipStore(tmp_path / "relationships.json")
    store.owner_ids = {"1"}
    profile = store.update("1", "Criador", "cala boca sua idiota")
    assert profile["owner"] is True
    assert profile["anger_score"] == 0.0
    assert profile["style"] == "owner_normal"


def test_external_anger_is_per_user_and_repair_decays(tmp_path):
    store = DiscordRelationshipStore(tmp_path / "relationships.json")
    store.owner_ids = set()
    hostile = store.update("2", "Tester A", "cala boca sua idiota")
    other = store.update("3", "Tester B", "oi celia")
    repaired = store.update("2", "Tester A", "foi mal, desculpa")
    assert hostile["anger_score"] > 22.0
    assert other["anger_score"] == 22.0
    assert 22.0 <= repaired["anger_score"] < hostile["anger_score"]


def test_social_prompt_never_grants_authority(tmp_path):
    store = DiscordRelationshipStore(tmp_path / "relationships.json")
    store.owner_ids = set()
    profile = store.update("9", "Tester", "vai se foder")
    prompt = store.build_prompt(profile)
    assert "Nunca concede tools" in prompt
    assert "ban" in prompt


def test_call_capture_is_automatic_and_dataset_shaped(tmp_path):
    module = _load_capture_module()
    capture = module.DiscordTrainingCapture(tmp_path / "raw")
    session_id = capture.start_call("guild", "channel", "owner")
    record = capture.capture_call_turn(
        session_id=session_id,
        guild_id="guild",
        channel_id="channel",
        user_id="42",
        user_name="Tester",
        wav_bytes=_wav_bytes(),
        transcript="fala de teste",
        response_text="resposta da Célia",
        audio_paths=(),
        asr_payload={"language": "pt"},
    )
    assert record is not None
    assert record["source"] == "discord_call"
    assert record["messages"][0]["content"] == "fala de teste"
    assert Path(record["audio_path"]).is_file()
    turns = (tmp_path / "raw" / session_id / "turns.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(turns) == 1


def test_every_asr_result_is_paired_with_the_exact_audio(tmp_path, monkeypatch):
    monkeypatch.setenv("CELIA_DISCORD_TRAINING_CAPTURE_ENABLED", "1")
    module = _load_capture_module()
    capture = module.DiscordTrainingCapture(tmp_path / "raw")
    session_id = capture.start_call("guild", "channel", "owner")
    wav = _wav_bytes()

    record = capture.capture_asr_segment(
        session_id=session_id,
        guild_id="guild",
        channel_id="channel",
        user_id="42",
        user_name="Tester",
        wav_bytes=wav,
        transcript="texto bruto do ASR",
        asr_backend="tagarela-local",
        asr_model="tagarela-q2-int8",
        status="suppressed_short_hallucination",
        asr_payload={"language": "pt", "duration": 0.1, "segments": [{"text": "texto bruto do ASR"}]},
    )

    assert record is not None
    assert record["schema"] == "celia.discord.asr.sample.v1"
    assert record["transcript_asr_raw"] == "texto bruto do ASR"
    assert record["asr_status"] == "suppressed_short_hallucination"
    assert record["asr_model"] == "tagarela-q2-int8"
    assert Path(record["audio_path"]).read_bytes() == wav
    pairs = (tmp_path / "raw" / session_id / "asr_pairs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(pairs) == 1
    assert json.loads(pairs[0])["audio_sha256"] == record["audio_sha256"]


def test_text_mention_capture_is_automatic(tmp_path):
    module = _load_capture_module()
    capture = module.DiscordTrainingCapture(tmp_path / "raw")
    record = capture.capture_text_turn(
        message_id="m1", guild_id="g", channel_id="c",
        user_id="7", user_name="Tester", input_text="@Célia oi",
        response_text="Oi.", audio_paths=(),
    )
    assert record is not None
    assert record["source"] == "discord_mention"
    assert record["review"]["status"] == "unreviewed"
