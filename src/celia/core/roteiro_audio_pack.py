"""Validated, script-bound recordings. Published sources outlive TTS cleanup."""

from __future__ import annotations

import hashlib
import json
import math
import wave
from pathlib import Path

from .raizes import RAIZ
from ..tts.production_voice import production_descriptor
from ..tts.audio_content_validation import normalize_spoken_text, content_equivalence

PACK_ROOT = RAIZ / "roteiros" / "audio"


class RoteiroAudioPack:
    def __init__(self, root: Path = PACK_ROOT):
        self.root = root
        self.identity = None
        self.entries = {}
        self.missing = []

    def load(self, script: dict) -> dict:
        self.identity = None
        self.entries = {}
        script_id, version = script.get("id"), script.get("version", 1)
        expected = {}
        for beat in script.get("beats", []):
            for index, line in enumerate(beat.get("lines", [])):
                if str(line.get("speaker", "")).casefold() in {"celia", "célia"}:
                    expected[f"{beat['id']}:{index}"] = (beat["id"], line["text"])
        self.missing = sorted(expected)
        # Script ids are data, never paths supplied by a recording client.
        if not isinstance(script_id, str) or Path(script_id).name != script_id or script_id in {".", ".."}:
            raise ValueError("invalid_audio_pack_script_id")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("invalid_audio_pack_version")
        directory = self.root / script_id / f"v{version}"
        manifest = directory / "manifest.json"
        if not manifest.exists():
            return self.status()
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if (data["schema_version"] != 2 or data["script_id"] != script_id
                    or data["script_version"] != version):
                raise ValueError("identity_mismatch")
            if data.get("voice_profile") != production_descriptor():
                raise ValueError("voice_profile_mismatch")
            entries = {}
            for entry in data["lines"]:
                line_id = entry["line_id"]
                if line_id in entries or expected.get(line_id) != (entry["beat_id"], entry["text"]):
                    raise ValueError("line_mismatch")
                path = (directory / entry["audio_path"]).resolve()
                if not path.is_relative_to(directory.resolve()):
                    raise ValueError("audio_outside_pack")
                asset = {**entry, "audio_path": str(path), "texto": entry["text"]}
                # An authentic original-reference entry may become stale when
                # the approved router selects the anchor. Regenerate that line;
                # corrupted or unknown descriptors still fail closed below.
                original = production_descriptor()
                if (production_descriptor(entry["text"]) != original
                        and all(entry.get(k) == v for k, v in original.items())):
                    continue
                self._validate_audio(asset)
                entries[line_id] = asset
        except (OSError, KeyError, TypeError, ValueError, EOFError, wave.Error) as exc:
            raise ValueError(f"invalid_recording_audio_pack:{exc}") from exc
        self.identity = (script_id, version)
        self.entries = entries
        self.missing = sorted(set(expected) - entries.keys())
        return self.status()

    @staticmethod
    def _validate_audio(entry: dict) -> None:
        profile = production_descriptor(entry.get("text", ""))
        if any(entry.get(key) != value for key, value in profile.items()):
            raise ValueError("voice_profile_mismatch")
        if (entry.get("expected_text") != entry.get("text")
                or entry.get("asr_status") != "match_dual_consensus"
                or entry.get("asr_audio_sha256") != entry.get("sha256")
                or not normalize_spoken_text(entry.get("expected_text", ""))
                or content_equivalence(entry["expected_text"], entry.get("asr_text", "")) is None):
            raise ValueError("audio_content_not_approved")
        path = Path(entry["audio_path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("audio_hash_mismatch")
        with wave.open(str(path), "rb") as wav:
            frames, rate = wav.getnframes(), wav.getframerate()
            pcm = wav.readframes(frames)
            if (rate != 24000 or wav.getnchannels() != 1 or wav.getsampwidth() != 2
                    or frames / rate < min(1.2, max(0.20, len(entry["text"].split()) * 0.11))
                    or len(pcm) != frames * wav.getnchannels() * wav.getsampwidth()):
                raise ValueError("empty_or_truncated_audio")
            silence = 128 if wav.getsampwidth() == 1 else 0
            if not any(sample != silence for sample in pcm):
                raise ValueError("silent_audio")
            import numpy as np
            samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
            if float(np.max(np.abs(samples))) < 0.025 or float(np.sqrt(np.mean(samples ** 2))) < 0.003:
                raise ValueError("near_silent_audio")
            if not math.isclose(frames / rate, float(entry["duration_seconds"]), abs_tol=1 / rate):
                raise ValueError("audio_duration_mismatch")

    def lookup(self, *, script_id, script_version, beat_id, line_id, text):
        if self.identity != (script_id, script_version):
            return None
        entry = self.entries.get(line_id)
        if not entry or (entry["beat_id"], entry["text"]) != (beat_id, text):
            return None
        try:
            self._validate_audio(entry)
        except (OSError, ValueError, EOFError, wave.Error):
            return None
        return dict(entry)

    def status(self):
        return {"prepared_lines": len(self.entries), "missing_line_ids": list(self.missing)}


roteiro_audio_pack = RoteiroAudioPack()
