"""Bounded sensor evidence; human speech alone controls conversation routing."""
from __future__ import annotations

import json
import re
import unicodedata

MAX_AGE_SEC = 15.0
EVIDENCE_RULES = (
    "[CURRENT VISUAL EVIDENCE CONTRACT]\n"
    "Use the latest frame evidence for present-tense visual claims. Conversation history is past speech, "
    "not a current observation or a reply to copy. A tool result obtained this turn supersedes pre-tool "
    "availability notes. OCR is text displayed on screen, not a system event: NVIDIA text does not "
    "prove a provider error, RTX 3060 is hardware, and Minecraft text does not prove a game is visible. "
    "Only an actual failed request establishes a provider failure; name its actual local provider/code. "
    "Missing or expired frame evidence means no recent observation; service health is unknown. "
    "Explain that limitation as missing current evidence, and report an outage only from a failed request. "
    "A figure mentioned in the question is a hypothesis, not proof that it is visible; "
    "check the current observation before describing it. "
    "Confirmed self_avatar represents you; creator_avatar represents Criador, your father/creator. "
    "Unconfirmed candidates retain their original visual description. Answer the current question "
    "naturally using current evidence; preserve legitimate conversational memories as memories."
)
_VISUAL = re.compile(r"\b(tela|imagem|avatar|slime|cabelo|roupa|personagem|vendo|enxerga|olha|olhe|apareceu|mostrando|modelo 3d|se reconhece)\b")
_LEGACY = re.compile(r"\[VISAO -[^\n]*\]\s*\n?", re.IGNORECASE)


def human_text(text: str) -> str:
    return _LEGACY.sub("", text or "").strip()


def visual_relevant(text: str) -> bool:
    plain = unicodedata.normalize("NFKD", human_text(text)).encode("ascii", "ignore").decode().lower()
    return bool(_VISUAL.search(plain) or re.search(
        r"\b(minecraft|jogo)\b.*\b(rodando|aberto|visivel|agora)\b", plain))


def visual_user_evidence(text: str, result: dict, age: float | None, *, source: str = "screen") -> str:
    note = sensor_context(text, result, age, source=source)
    return "\n\n" + note + "\n" + EVIDENCE_RULES if note else ""


def sensor_context(text: str, result: dict, age: float | None, *, source: str = "screen") -> str:
    if not visual_relevant(text):
        return ""
    if age is None or not 0 <= age <= MAX_AGE_SEC or not result.get("summary"):
        return '[VISUAL SENSOR] ' + json.dumps({
            "status": "unavailable", "reason": "no_recent_analysis",
            "service_status": "unknown",
            "instruction": "Falta uma análise recente da tela. Explique essa limitação como falta de "
                           "observação atual; isso não diagnostica serviço desligado, falha ou queda."
        }, ensure_ascii=False)
    note = {"status": "observed", "source": source, "analysis_age_s": round(age, 1),
            "summary": str(result["summary"])[:180], "confidence": "model_observation_not_verified_identity"}
    for key in ("frame_id", "generation", "analyzed_at"):
        if key in result:
            note[key] = result[key]
    identities = [i for i in result.get("identities", []) if i.get("confirmed")]
    if identities:
        note["identities"] = [{k: i[k] for k in ("identity", "relation", "confidence") if k in i} for i in identities[:3]]
    return ("[VISUAL SENSOR — evidence, not a user request; prioritize the spoken question]\n"
            + json.dumps(note, ensure_ascii=False))


def asks_current_visual(text: str) -> bool:
    plain = unicodedata.normalize("NFKD", human_text(text)).encode("ascii", "ignore").decode().lower()
    return bool(re.search(r"\b(o que (que )?(tu |voce |ce )?(ta |esta )?(vendo|ve|enxerga)|ta vendo|esta vendo|olha (isso|a tela)|descrev[ae] (a |essa )?(tela|imagem))\b", plain))


def requested_identity(text: str) -> str | None:
    plain = unicodedata.normalize("NFKD", human_text(text)).encode("ascii", "ignore").decode().lower()
    if re.search(r"\b(se reconhece|reconhece (esse |este |o |essa |esta |a )?(avatar|slime|criador|celia)|quem e (esse |este |o )?(slime|avatar))\b", plain):
        return "criador_slime" if "slime" in plain else "criador_human" if "humano" in plain else "celia"
    return None


def identity_context(result: dict) -> str:
    """Only a successful reference match gives the persona a relationship/name."""
    if result.get("confirmed") is True:
        evidence = {key: result.get(key) for key in
                    ("identity", "relation", "confidence", "frame_sha256", "analyzed_at")}
        evidence["status"] = "confirmed_reference_match"
        evidence["represents"] = {
            "self_avatar": "Célia: a própria assistente que está respondendo, não Criador",
            "creator_avatar": "Criador: o pai/criador da assistente Célia",
        }.get(result.get("relation"), "Relação não estabelecida")
    else:
        evidence = {"status": "unconfirmed_reference_match",
                    "instruction": "A comparação visual não confirmou quem é. Responda com a incerteza real; "
                                   "descreva a figura sem atribuir nome, parentesco ou identidade."}
    return "[VISUAL IDENTITY SENSOR]\n" + json.dumps(evidence, ensure_ascii=False)


def identity_user_evidence(content: str, text: str, result: dict) -> str:
    """Replace pre-tool availability appended after this exact human question."""
    anchor = text + "\n\n[VISUAL SENSOR"
    start = content.rfind(anchor) if text else -1
    if start >= 0:
        content = content[:start + len(text)]
    return content + "\n\n" + identity_context(result)


def current_visual_messages(messages: list[dict], text: str, *, preserve_current: bool = False) -> list[dict]:
    """Keep stored dialogue intact; avoid priming a new observation with old answers."""
    if preserve_current and messages:
        return current_visual_messages(messages[:-1], text) + [dict(messages[-1])]
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    if not visual_relevant(text) or re.search(r"\b(lembra|lembre|ontem|antes|passado)\b", plain):
        return [dict(item) for item in messages]
    return [dict(item) for item in messages if item.get("role") != "assistant"
            and not (item.get("role") == "user" and visual_relevant(str(item.get("content", ""))))]
