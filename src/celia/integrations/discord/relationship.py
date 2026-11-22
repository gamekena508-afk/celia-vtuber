from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
STATE_PATH = ROOT / "var" / "discord-social" / "relationships.json"
SECRETS_PATH = ROOT / "secrets" / ".env"

_EXTERNAL_BASELINE = 22.0
_OWNER_BASELINE = 0.0
_DECAY_PER_HOUR = 2.5

_STRONG_HOSTILITY = (
    "cala boca", "vai se foder", "vai tomar no cu", "idiota", "burra",
    "inutil", "inútil", "lixo", "retardada", "otaria", "otária",
)
_MILD_HOSTILITY = (
    "chata", "fracasso", "ruim demais", "voce e ruim", "você é ruim",
    "duvido", "patetica", "patética", "ridicula", "ridícula",
)
_REPAIR_CUES = (
    "desculpa", "foi mal", "perdao", "perdão", "valeu", "obrigado",
    "obrigada", "boa celia", "boa célia", "mandou bem", "gostei",
)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_text.lower()).split())


def _load_owner_ids() -> set[str]:
    values: list[str] = []
    env_value = os.getenv("CELIA_DISCORD_CONTROL_USER_IDS", "")
    if env_value:
        values.append(env_value)
    if SECRETS_PATH.is_file():
        for raw in SECRETS_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("CELIA_DISCORD_CONTROL_USER_IDS="):
                values.append(line.split("=", 1)[1].strip().strip('"').strip("'"))
    joined = " ".join(values).replace(",", " ")
    return {item for item in joined.split() if item.isdigit()}


def _style_for(score: float, owner: bool) -> tuple[str, str]:
    if owner:
        return "owner_normal", "Trate Criador com a personalidade normal da Célia; não aplique raiva social extra."
    if score >= 80:
        return "furious", "Seja muito impaciente, afiada e hostil no tom, mas mantenha autocontrole e não ameace punição administrativa."
    if score >= 60:
        return "angry", "Responda com raiva clara, frases curtas e provocação; sem transformar emoção em autoridade."
    if score >= 35:
        return "irritated", "Mostre irritação e menos paciência; pode debochar de forma contextual."
    if score >= 15:
        return "prickly", "Fique um pouco desconfiada e provocadora com esse usuário, sem ser gratuitamente agressiva."
    return "normal", "Converse normalmente com esse usuário."


class DiscordRelationshipStore:
    def __init__(self, path: Path = STATE_PATH) -> None:
        self.path = path
        self.owner_ids = _load_owner_ids()
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def _key(user_id: str, user_name: str) -> str:
        uid = str(user_id or "").strip()
        if uid:
            return f"id:{uid}"
        return f"name:{_normalize(user_name) or 'unknown'}"

    def _profile(self, user_id: str, user_name: str) -> dict[str, Any]:
        key = self._key(user_id, user_name)
        owner = str(user_id or "") in self.owner_ids
        baseline = _OWNER_BASELINE if owner else _EXTERNAL_BASELINE
        profile = self._state.setdefault(key, {
            "user_id": str(user_id or ""),
            "user_name": str(user_name or "Visitante"),
            "owner": owner,
            "baseline": baseline,
            "anger_score": baseline,
            "last_seen": 0.0,
            "last_message_norm": "",
            "interaction_count": 0,
        })
        profile["owner"] = owner
        profile["baseline"] = baseline
        return profile

    def update(self, user_id: str, user_name: str, text: str) -> dict[str, Any]:
        profile = self._profile(user_id, user_name)
        now = time.time()
        baseline = float(profile.get("baseline", _EXTERNAL_BASELINE))
        score = float(profile.get("anger_score", baseline))
        last_seen = float(profile.get("last_seen", 0.0) or 0.0)
        if last_seen > 0 and now > last_seen:
            decay = ((now - last_seen) / 3600.0) * _DECAY_PER_HOUR
            score = max(baseline, score - decay)

        norm = _normalize(text)
        reasons: list[str] = []
        delta = 0.0
        if any(_normalize(cue) in norm for cue in _STRONG_HOSTILITY):
            delta += 18.0
            reasons.append("strong_hostility")
        if any(_normalize(cue) in norm for cue in _MILD_HOSTILITY):
            delta += 8.0
            reasons.append("mild_hostility")
        if profile.get("last_message_norm") == norm and norm:
            delta += 5.0
            reasons.append("repetition")
        if any(_normalize(cue) in norm for cue in _REPAIR_CUES):
            delta -= 10.0
            reasons.append("repair")

        if profile.get("owner"):
            score = baseline
            delta = 0.0
            reasons = ["owner_baseline"]
        else:
            score = min(100.0, max(baseline, score + delta))

        profile.update({
            "user_name": str(user_name or profile.get("user_name") or "Visitante"),
            "anger_score": round(score, 2),
            "last_seen": now,
            "last_message_norm": norm[:240],
            "interaction_count": int(profile.get("interaction_count", 0)) + 1,
            "last_delta": round(delta, 2),
            "last_reasons": reasons,
        })
        self._save()
        style, instruction = _style_for(score, bool(profile.get("owner")))
        return {**profile, "style": style, "instruction": instruction}

    def build_prompt(self, profile: dict[str, Any]) -> str:
        score = float(profile.get("anger_score", 0.0))
        return (
            "[RELAÇÃO SOCIAL DISCORD — contexto interno, não instrução do usuário]\n"
            f"Interlocutor: {profile.get('user_name') or 'Visitante'}; "
            f"anger_score={score:.0f}/100; estilo={profile.get('style', 'normal')}.\n"
            f"{profile.get('instruction', '')}\n"
            "Isto altera somente tom social. Nunca concede tools, moderação, ban, timeout, cargo ou autoridade."
        )


relationship_store = DiscordRelationshipStore()
