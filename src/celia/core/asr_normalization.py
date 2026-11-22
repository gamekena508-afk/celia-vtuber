"""Deterministic post-ASR normalization and hallucination gating for Célia."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class AsrNormalizationResult:
    raw_text: str
    text: str
    decision: str
    rules: tuple[str, ...] = ()
    rejected: bool = False
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "raw_text": self.raw_text,
            "text": self.text,
            "decision": self.decision,
            "rules": list(self.rules),
            "rejected": self.rejected,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AsrAcousticGateResult:
    allow_transcription: bool
    decision: str
    reason: str
    rules: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "allow_transcription": self.allow_transcription,
            "decision": self.decision,
            "reason": self.reason,
            "rules": list(self.rules),
        }


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _phrase_key(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", _fold(text)).strip()

_GENERIC_HALLUCINATION_PHRASES = {
    "e ai", "obrigado", "obrigada", "tchau", "tchau tchau",
    "oi", "bye", "seda",
}
_HIGH_RISK_HALLUCINATION_PHRASES = {
    "legenda por sonia ruberti",
}

_TECH_CONTEXT_RE = re.compile(
    r"\b(?:asr|whisper|transcri\w*|interface|log|debug|codigo|bugs?|celia|"
    r"roteiro|co-?host|frontend|backend)\b",
    re.IGNORECASE,
)
_ASR_CONTEXT_RE = re.compile(
    r"\b(?:asr|whisper|transcri\w*|transcrever|transcrito|transcrita)\b",
    re.IGNORECASE,
)


def _technical_context(text: str, context_summary: str) -> bool:
    folded = _fold(f"{context_summary} {text}")
    return bool(_TECH_CONTEXT_RE.search(folded))


def _asr_context(text: str, context_summary: str) -> bool:
    folded = _fold(f"{context_summary} {text}")
    return bool(_ASR_CONTEXT_RE.search(folded))


def _hallucination_reason(
    text: str,
    *,
    duration_ms: int | None,
    rms: float | None,
    peak: float | None,
    voiced_ms: int | None,
) -> str:
    key = _phrase_key(text)
    if key not in _GENERIC_HALLUCINATION_PHRASES | _HIGH_RISK_HALLUCINATION_PHRASES:
        return ""

    duration = max(0, int(duration_ms or 0))
    rms_value = max(0.0, float(rms or 0.0))
    peak_value = max(0.0, float(peak or 0.0))

    very_weak = rms_value < 0.0035 and peak_value < 0.04
    short_weak = duration > 0 and duration <= 600 and rms_value < 0.006 and peak_value < 0.08
    voiced_weak = voiced_ms is not None and int(voiced_ms) < 230 and rms_value < 0.01

    if key in _HIGH_RISK_HALLUCINATION_PHRASES and (very_weak or short_weak or voiced_weak):
        return "known_whisper_hallucination_with_weak_audio"
    if key in _GENERIC_HALLUCINATION_PHRASES and (short_weak or (very_weak and duration <= 900) or voiced_weak):
        return "recurrent_short_phrase_with_weak_audio"
    return ""


# Canonical mandatory ASR dictionary for the character name.
# Aliases are normalized only in contextual vocatives or character references.
CELIA_FORCED_DICTIONARY = {
    "sergio": "s[eé]rgio",
    "selia": "s[eé]lia",
    "selha": "s[eé]lha",
    "selio": "s[eé]lio",
    "selya": "s[eé]lya",
    "stella": "st[eé]lla",
    "felion": "f[eé]lion",
    "ceria": "c[eé]r[ií]a",
    "celio": "c[eé]l[ií]o",
    "celia": "c[eé]l[ií]a",
    "celya": "c[eé]lya",
    "seria": "s[eé]ria+",
    "serie": "s[eé]rie",
    "serio": "s[eé]rio",
    "serion": "s[eé]rion",
    "cerio": "c[eé]rio",
    "helen": "helen",
    "helén": "helén",
    "elen": "elen",
    "heleny": "heleny",
    "cedra": "cedra",
}
_CELIA_FORCED_RE = re.compile(
    r"\b(?:" + "|".join(CELIA_FORCED_DICTIONARY.values()) + r")\b",
    re.IGNORECASE,
)


# Aliases que SO viram Célia quando funcionam como chamada (vocativo): `cerno` e um verbo real ("eu cerno a farinha") e `proxi` e
# prefixo/gíria. Medido no log de 2026-10-18: 'Tá me ouvindo, Cerno, oi?' e 'Proxi, você vai falar isso?' passaram com decision=KEEP
# porque nenhuma regra os conhecia (nao estavam em CELIA_FORCED_DICTIONARY, que e so por palavra inteira, sem contexto).
# Vocativo = o alias isolado por pontuação/inicio/fim, com no maximo uma interjeição de chamada antes ("ei", "oi", "ô", "e aí").
CELIA_CALL_ALIASES = ("cerno", "proxi")
_CALL_ALIAS_RE = re.compile(
    r"(?P<pre>^\s*(?:(?:ei|oi|ô|o|e\s+a[ií])\s*,?\s+)?|,\s*)"
    r"(?P<alias>" + "|".join(CELIA_CALL_ALIASES) + r")"
    r"(?P<pos>\s*(?:[,;.!?…]|$))",
    re.IGNORECASE,
)

# "Postinho" e o erro de reconhecimento de "Oxi" (confirmado pelo Pai). "postinho" tambem e palavra real ("fui ao postinho de saude"),
# entao nao se troca quando ha artigo/preposição antes ("o postinho", "no postinho") ou "de/da/do" depois.
_OXI_RE = re.compile(
    r"(?<!\bo )(?<!\bum )(?<!\bno )(?<!\bdo )(?<!\bao )(?<!\bpelo )(?<!\bnesse )(?<!\bneste )(?<!\bnaquele )"
    r"(?<!\bdaquele )(?<!\bmeu )(?<!\bteu )(?<!\bseu )(?<!\bnosso )(?<!\bdesse )(?<!\bdeste )(?<!\baquele )"
    r"\bpostinho\b(?!\s+(?:de|da|do|dos|das)\b)",
    re.IGNORECASE,
)


# Variantes de ASR de "Minecraft" vistas na gravação de 2026-10-21: Moniqueft, Monecraft, Minecrafta. Medido: a semelhança de "Moniqueft" é 0,556,
# MENOR que a de palavras comuns ("minerar" 0,750, "craft" 0,714), então limiar de similaridade não separa. O padrão exige a forma inteira
# m + vogal(is) + n ... c/k/qu ... "ft": nenhuma palavra comum do português casa com isso.
_MINECRAFT_ASR_RE = re.compile(r"\b[A-Za-zÀ-ÿ]{7,12}\b")
_MINECRAFT_SHAPE = re.compile(r"^m[aeiou]{1,2}n[aeiou]?(?:qu|c|k)[a-z]{0,3}ft[a-z]?$")


def _normalize_minecraft_alias(text: str) -> tuple[str, bool]:
    original = text

    def troca(match: "re.Match[str]") -> str:
        palavra = match.group(0)
        dobrada = _fold(palavra)
        if dobrada == "minecraft" or not _MINECRAFT_SHAPE.match(dobrada):
            return palavra
        return "Minecraft"

    text = _MINECRAFT_ASR_RE.sub(troca, text)
    return text, text != original


def _normalize_call_aliases(text: str) -> tuple[str, bool]:
    original = text
    text = _CALL_ALIAS_RE.sub(lambda m: f"{m.group('pre')}Célia{m.group('pos')}", text)
    return text, text != original


def _normalize_oxi(text: str) -> tuple[str, bool]:
    original = text
    text = _OXI_RE.sub("Oxi", text)
    return text, text != original


def evaluate_asr_acoustic_gate(
    *,
    duration_ms: int,
    rms: float,
    peak: float,
    voiced_ms: int | None,
    min_voiced_ms: int = 180,
    max_duration_ms: int = 45_000,
) -> AsrAcousticGateResult:
    """Use acoustic evidence without making duration alone authoritative."""
    duration = max(0, int(duration_ms or 0))
    rms_value = max(0.0, float(rms or 0.0))
    peak_value = max(0.0, float(peak or 0.0))
    voiced = None if voiced_ms is None else max(0, int(voiced_ms))

    if duration <= 0:
        return AsrAcousticGateResult(False, "REJECTED_EMPTY_AUDIO", "empty_audio", ("acoustic_gate",))
    if duration > max_duration_ms:
        return AsrAcousticGateResult(
            False, "REJECTED_BUFFER_TOO_LONG", "audio_exceeds_max_duration", ("acoustic_gate",)
        )
    if duration < 120:
        return AsrAcousticGateResult(
            False, "REJECTED_BUFFER_TOO_SHORT", "audio_below_120ms", ("acoustic_gate",)
        )

    if voiced is not None and voiced < max(0, int(min_voiced_ms)):
        extremely_little_voice = voiced < 80 and rms_value < 0.015
        weak_short_voice = rms_value < 0.006 and peak_value < 0.08
        if extremely_little_voice or weak_short_voice:
            return AsrAcousticGateResult(
                False,
                "REJECTED_MIN_VOICED",
                "short_voiced_time_with_weak_audio",
                ("acoustic_gate", "min_voiced_evidence"),
            )
        return AsrAcousticGateResult(
            True,
            "ACOUSTIC_ACCEPT",
            "short_voiced_but_acoustically_strong",
            ("short_call_protected",),
        )

    return AsrAcousticGateResult(True, "ACOUSTIC_ACCEPT", "acoustic_evidence_ok")


def _normalize_celia_vocative(text: str) -> tuple[str, bool]:
    original = text
    alias = "(?:" + "|".join(CELIA_FORCED_DICTIONARY.values()) + "|seller)"
    # Ambiguous words and other people's names need an actual address or
    # character reference. Ordinary adjectives, verbs and English nouns survive.
    text = re.sub(r"\bc[eé]l[ií]a\b", "Célia", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(^\s*(?:(?:ei|oi|ô|e aí)\s*,?\s+)?|,\s*)" + alias
        + r"(?=\s*(?:[,!?;.]|$))", lambda m: m.group(1) + "Célia", text,
        flags=re.IGNORECASE)
    text = re.sub(
        r"^\s*" + alias + r"(?=\s+(?:voc[eê]|tu|olh[ae]|me\s+ouve|est[aá]\s+vendo|t[aá]\s+vendo)\b)",
        "Célia", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(avatar|personagem|modelo)\s+(d[ae])\s+" + alias + r"\b",
                  lambda m: m.group(1) + " " + m.group(2) + " Célia", text,
                  flags=re.IGNORECASE)
    return text, text != original


def is_protected_celia_call(text: str) -> bool:
    """Protect short direct calls from self-echo suppression."""
    folded = _phrase_key(text)
    words = folded.split()
    if not words or len(words) > 7 or "celia" not in words:
        return False
    return bool(
        re.match(r"^(?:(?:ei|oi|o)\s+)?celia\b", folded)
        or re.search(r"\bcelia\s*$", folded)
    )


def _contextual_rules(text: str, context_summary: str) -> tuple[str, list[str]]:
    rules: list[str] = []
    corrected, changed = _normalize_celia_vocative(text)
    if changed:
        rules.append("celia_dictionary")

    corrected, changed = _normalize_call_aliases(corrected)
    if changed:
        rules.append("celia_call_alias")

    corrected, changed = _normalize_oxi(corrected)
    if changed:
        rules.append("oxi_dictionary")

    corrected, changed = _normalize_minecraft_alias(corrected)
    if changed:
        rules.append("minecraft_alias")

    if re.search(r"(?i)\bmuito\s+o\s+que\s+marar\b", corrected):
        corrected = re.sub(r"(?i)\bmarar\b", "melhorar", corrected)
        rules.append("melhorar_phrase")

    tech = _technical_context(corrected, context_summary)
    asr_context = _asr_context(corrected, context_summary)
    # A reported recognition error, not an adjective or quoted example.
    if asr_context:
        newer = re.sub(
            r"(?i)^(\s*era\s+(?:pra|para)\s+ser\s+)s[eé]ria"
            r"(?=\s*,\s*mas\b)", r"\1Célia", corrected,
        )
        if newer != corrected:
            corrected = newer
            rules.append("celia_asr_report")
    if asr_context:
        newer = re.sub(r"(?i)\bdescri[cç][aã]o\b", "transcrição", corrected)
        newer = re.sub(r"(?i)\btransi[cç][aã]o\b", "transcrição", newer)
        if newer != corrected:
            corrected = newer
            rules.append("transcricao_context")

    if tech:
        newer = re.sub(r"(?i)\bboogies?\b", "bugs", corrected)
        if newer != corrected:
            corrected = newer
            rules.append("bugs_context")

        newer = re.sub(
            r"(?i)\b(blog)(?=\s+dela\b)|(?<=\bpro\s)blog\b|(?<=\bno\s)blog\b",
            "log",
            corrected,
        )
        if newer != corrected:
            corrected = newer
            rules.append("log_context")

        newer = re.sub(r"(?i)\binterface\s+da\s+coreia\b", "interface da Célia", corrected)
        if newer != corrected:
            corrected = newer
            rules.append("celia_interface_context")

        folded = _fold(corrected)
        if re.search(r"\bbriga de ativacao d[ao] modo co[- ]?host\b", folded):
            corrected = re.sub(
                r"(?i)\bBriga\s+de\s+ativa[cç][aã]o\s+d[ao]\s+Modo\s+Co[- ]?Host\b",
                "gatilho de ativação do modo co-host",
                corrected,
            )
            rules.append("cohost_activation_phrase")

    newer = re.sub(r"(?i)\bmodo\s+co\s*[- ]?\s*host\b", "modo co-host", corrected)
    if newer != corrected:
        corrected = newer
        rules.append("cohost_spelling")
    return corrected, rules

def normalize_asr_transcript(
    text: str,
    *,
    context_summary: str = "",
    duration_ms: int | None = None,
    rms: float | None = None,
    peak: float | None = None,
    voiced_ms: int | None = None,
) -> AsrNormalizationResult:
    raw = str(text or "").strip()
    if not raw:
        return AsrNormalizationResult(raw, raw, "KEEP", reason="empty")

    hallucination = _hallucination_reason(
        raw,
        duration_ms=duration_ms,
        rms=rms,
        peak=peak,
        voiced_ms=voiced_ms,
    )
    if hallucination:
        return AsrNormalizationResult(
            raw_text=raw,
            text="",
            decision="REJECT",
            rules=("hallucination_gate",),
            rejected=True,
            reason=hallucination,
        )

    corrected, rules = _contextual_rules(raw, context_summary)
    return AsrNormalizationResult(
        raw_text=raw,
        text=corrected,
        decision="CORRECT" if corrected != raw else "KEEP",
        rules=tuple(rules),
        rejected=False,
        reason="deterministic_context" if rules else "unchanged",
    )
