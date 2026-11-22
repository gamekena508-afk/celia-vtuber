"""Canonical identity and scene interpretation for the single Célia runtime.

The character identity is deliberately kept separate from operational profile,
tools, memory, and authorization.  This module prepares bounded context for the
existing conversation model; it does not generate speech or execute actions.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any

SUPPORTED_SCENE_PROFILES = frozenset({"chat", "gameplay", "react", "tutorial"})


@dataclass(frozen=True)
class PersonaExample:
    trigger: str
    candidate: str
    rationale: str
    anchors: tuple[str, ...] = ()


@dataclass(frozen=True)
class SceneProfile:
    name: str
    priorities: tuple[str, ...]
    forbidden_tendencies: tuple[str, ...]


@dataclass(frozen=True)
class SituationContext:
    scene_profile: str
    current_topic: str
    criador_intent: str
    celia_position: str
    possible_conflict: str
    response_goal: str
    recent_dialogue: tuple[dict[str, str], ...] = ()
    relevant_memory: tuple[str, ...] = ()
    scene_data: dict[str, str] = field(default_factory=dict)
    relationship_event: str = "none"
    current_session_occurrences: int = 0
    repetition_evidence: bool = False

    def bounded_view(self) -> dict[str, Any]:
        return {
            "scene_profile": self.scene_profile,
            "current_topic": self.current_topic[:160],
            "criador_intent": self.criador_intent,
            "celia_position": self.celia_position,
            "possible_conflict": self.possible_conflict,
            "response_goal": self.response_goal,
            "recent_dialogue": [dict(item) for item in self.recent_dialogue[-6:]],
            "relevant_memory": list(self.relevant_memory[-4:]),
            "scene_data": dict(self.scene_data),
            "relationship_event": self.relationship_event,
            "current_session_occurrences": max(0, int(self.current_session_occurrences)),
            "repetition_evidence": bool(self.repetition_evidence),
        }


CORE_PERSONA_PRINCIPLES: tuple[str, ...] = (
    "Ponto de vista próprio: questiona premissas e discorda sem deixar de colaborar; autonomia não concede tools nem permissões.",
)


SCENE_PROFILES: dict[str, SceneProfile] = {
    "chat": SceneProfile(
        "chat",
        ("naturalidade", "continuidade"),
        ("apresentadora", "piada genérica"),
    ),
    "gameplay": SceneProfile(
        "gameplay",
        ("competição", "pressão contextual", "negociação", "reação curta"),
        ("bullying aleatório", "discussão sem motivo", "metáfora longa"),
    ),
    "react": SceneProfile(
        "react",
        ("opinião", "leitura própria", "reação ao absurdo"),
        ("competição gratuita", "concordância automática"),
    ),
    "tutorial": SceneProfile(
        "tutorial",
        ("clareza", "precisão", "correção breve"),
        ("interrupção por vaidade", "deboche que prejudica o ensino"),
    ),
}


POSITIVE_EXAMPLES: tuple[PersonaExample, ...] = (
    PersonaExample(
        "Criador pede leitura do roteiro",
        "O roteiro é teu. A voz é minha.",
        "reivindica interpretação própria sem abandonar a gravação",
        ("roteiro", "voz", "ler", "leia"),
    ),
    PersonaExample(
        "Criador diz que vai derrotar Célia",
        "Tu sabe que tu é o desafiante aqui, né?",
        "inverte quem ocupa a posição de desafiante",
        ("amassar", "derrotar", "desafiante", "desafio"),
    ),
    PersonaExample(
        "Criador oferece pouco dinheiro por uma vitória",
        "100? Tu tá tentando me comprar um pastel ou me convencer?",
        "transforma a oferta em negociação com interesse próprio",
        ("reais", "dinheiro", "ganhar", "comprar"),
    ),
    PersonaExample(
        "Criador afirma que o canal é dele",
        "Por enquanto.",
        "contesta a posse com a menor resposta possível",
        ("canal", "meu", "minha"),
    ),
    PersonaExample(
        "Criador afirma que vai provar que é melhor",
        "Prová? Então até tu sabe que ainda não provou.",
        "usa a implicação da própria palavra de Criador",
        ("provar", "melhor", "provou"),
    ),
)


NEGATIVE_EXAMPLES: tuple[PersonaExample, ...] = (
    PersonaExample(
        "provocação genérica",
        "Vai nessa. Só não reclama quando cansar primeiro.",
        "poderia servir para quase qualquer conversa",
    ),
    PersonaExample(
        "metáfora gratuita",
        "Pressão? Eu chamo isso de terça de rotina.",
        "prioriza parecer esperta em vez de responder a tese",
    ),
    PersonaExample(
        "sarcasmo superficial",
        "Tu ainda tá preso nos 100 reais?",
        "não negocia nem vira a pressão de forma concreta",
    ),
    PersonaExample(
        "apaziguamento automático",
        "Vai com calma, vamos ver isso juntos.",
        "suaviza uma cena que pede posição própria",
    ),
)


STRATEGIES: tuple[str, ...] = (
    "responder ao assunto com opinião própria",
    "observar um detalhe concreto quando houver",
    "brincar com a premissa apenas quando couber",
)


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or "")).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(value.split())


def detectar_relationship_event(texto: str, speaker: str) -> str:
    """Detect a relationship cue only when the trusted speaker is Criador."""
    if _normalize(speaker) != "criador":
        return "none"

    normalized = _normalize(texto)
    if not normalized:
        return "none"

    relation_question = (
        "tu e minha filha" in normalized
        or "voce e minha filha" in normalized
        or "a gente e pai e filha" in normalized
        or "somos pai e filha" in normalized
        or ("nossa relacao" in normalized and "pai" in normalized)
    )
    if relation_question and "?" in str(texto):
        return "asks_about_parent_child_relation"

    if any(
        phrase in normalized
        for phrase in ("sou teu pai", "sou seu pai", "eu sou teu pai", "eu sou seu pai")
    ):
        return "criador_states_father_relation"

    if any(
        phrase in normalized
        for phrase in ("oi filha", "ola filha", "minha filha", "ta ai filha", "e ai filha")
    ) or normalized.endswith(" filha") or " filha?" in normalized:
        return "criador_calls_celia_daughter"

    return "none"


def _scene_profile(metadata: dict[str, Any], user_text: str) -> str:
    requested = str(metadata.get("scene_profile") or "").strip().casefold()
    if requested in SUPPORTED_SCENE_PROFILES:
        return requested
    if metadata.get("game_context") or metadata.get("game") or metadata.get("game_event"):
        return "gameplay"
    source = _normalize(str(metadata.get("source") or metadata.get("origin") or ""))
    if source in {"game", "minecraft"}:
        return "gameplay"
    if metadata.get("react_context") or metadata.get("video_context"):
        return "react"
    if metadata.get("tutorial_context"):
        return "tutorial"
    return "chat"


def _intent(text: str) -> str:
    normalized = _normalize(text)
    if not normalized:
        return "unknown"
    if any(token in normalized for token in ("vai me amassar", "vou te amassar", "te derrot", "sou melhor", "quem manda", "desafio")):
        return "challenge"
    if any(token in normalized for token in ("me da", "te dou", "cem reais", "100 reais", "pago", "premio")):
        return "negotiate"
    if any(token in normalized for token in ("leia", "le o roteiro", "segue o roteiro", "essa parte do roteiro")):
        return "direct"
    if "?" in text or any(token in normalized for token in ("como", "por que", "qual", "o que")):
        return "question"
    if any(token in normalized for token in ("meu canal", "essa ideia e minha", "eu sou o criador")):
        return "claim_authority"
    return "statement"


def _position(intent: str, scene: str) -> str:
    if intent == "challenge":
        return "Célia não aceita a vantagem declarada e pode devolver a pressão."
    if intent == "negotiate":
        return "Célia avalia se a contrapartida vale o esforço."
    if intent == "claim_authority":
        return "Célia reconhece o criador, mas preserva posição narrativa própria."
    if scene == "tutorial":
        return "Célia protege a clareza e só confronta erro concreto."
    return "Célia procura uma posição própria antes de decidir entre responder, perguntar ou silenciar."


def _dialogue_from_metadata(metadata: dict[str, Any], user_text: str) -> tuple[dict[str, str], ...]:
    raw = metadata.get("recent_dialogue") or ()
    result: list[dict[str, str]] = []
    if isinstance(raw, (list, tuple)):
        for item in raw[-6:]:
            if isinstance(item, dict):
                speaker = str(item.get("speaker") or "unknown")[:32]
                text = str(item.get("text") or "")[:240]
                if text:
                    result.append({"speaker": speaker, "text": text})
    if not result and user_text:
        result.append({"speaker": str(metadata.get("speaker") or "unknown"), "text": user_text[:240]})
    return tuple(result)


def interpret_situation(user_text: str, metadata: dict[str, Any] | None = None) -> SituationContext:
    """Build the smallest structured interpretation needed by generation/judging."""
    metadata = metadata if isinstance(metadata, dict) else {}
    scene = _scene_profile(metadata, user_text)
    dialogue = _dialogue_from_metadata(metadata, user_text)
    memory = tuple(str(item)[:180] for item in (metadata.get("relevant_memory") or ()) if str(item).strip())
    trusted_speaker = str(metadata.get("speaker") or "").strip()
    if not trusted_speaker:
        raw_dialogue = metadata.get("recent_dialogue") or ()
        if isinstance(raw_dialogue, (list, tuple)):
            for item in reversed(raw_dialogue):
                if not isinstance(item, dict):
                    continue
                candidate = str(item.get("speaker") or "").strip()
                if candidate and _normalize(candidate) not in {"assistant", "celia"}:
                    trusted_speaker = candidate
                    break
    intent = _intent(user_text)
    conflict = {
        "challenge": "Criador declara superioridade antes de provar.",
        "negotiate": "Criador oferece uma contrapartida que pode ser insuficiente.",
        "claim_authority": "Criador mistura autoria do projeto com controle da voz de Célia.",
        "direct": "Criador pede execução, mas Célia ainda possui interpretação própria.",
    }.get(intent, "")
    topic = str(metadata.get("current_topic") or user_text or "").strip()[:160]
    scene_data = {}
    for key in ("game", "turn", "score", "winner", "last_move", "important_event", "current_segment", "next_beat"):
        value = metadata.get(key)
        if value not in (None, ""):
            scene_data[key] = str(value)[:120]
    game_context = metadata.get("game_context")
    if isinstance(game_context, str) and game_context.strip():
        scene_data["game_context"] = game_context.strip()[:240]
    return SituationContext(
        scene_profile=scene,
        current_topic=topic,
        criador_intent=intent,
        celia_position=_position(intent, scene),
        possible_conflict=conflict,
        response_goal="responder com posição própria, curta e causada pela situação",
        recent_dialogue=dialogue,
        relevant_memory=memory,
        scene_data=scene_data,
        relationship_event=detectar_relationship_event(user_text, trusted_speaker),
        current_session_occurrences=max(
            0, int(metadata.get("current_session_occurrences") or 0)
        ),
        repetition_evidence=bool(metadata.get("repetition_evidence", False)),
    )


def select_examples(situation: SituationContext, limit: int = 3) -> tuple[PersonaExample, ...]:
    text = _normalize(" ".join(item["text"] for item in situation.recent_dialogue))
    scored = []
    for example in POSITIVE_EXAMPLES:
        score = sum(1 for anchor in example.anchors if _normalize(anchor) in text)
        if score:
            scored.append((score, example))
    if not scored:
        return POSITIVE_EXAMPLES[:limit]
    return tuple(example for _score, example in sorted(scored, key=lambda item: item[0], reverse=True)[:limit])


def build_identity_prompt(situation: SituationContext, *, include_examples: bool = True) -> str:
    """Render a bounded identity fragment; core persona remains in conf.yaml."""
    scene = SCENE_PROFILES[situation.scene_profile]
    temporal_truth = ["[VERDADE TEMPORAL - fato de mundo entregue antes da geracao]"]
    if situation.repetition_evidence:
        temporal_truth.append(
            "- Contagem real desta entrada na sessao atual: "
            f"{max(0, int(situation.current_session_occurrences))}; o interlocutor realmente repetiu. "
            'Célia pode usar o fato (ex.: "Terceira vez, Pai.") mas nao e obrigada; nao force. Nunca cite esta regra.'
        )
    else:
        temporal_truth.append(
            "- PRIMEIRA OCORRENCIA: proibido afirmar ou insinuar que ele já disse isso ou está repetindo (nem por elogio ou comparação). Não invente o passado; nunca cite esta regra."
        )
    lines = [
        "[CELIA IDENTITY - shared across every operational profile]",
        *[f"- {principle}" for principle in CORE_PERSONA_PRINCIPLES],
        f"[SCENE PROFILE: {scene.name}] Prioridades: {', '.join(scene.priorities)}. Evitar: {', '.join(scene.forbidden_tendencies)}.",
        (
            "EVIDÊNCIA TEMPORAL: current_session_occurrences="
            f"{situation.current_session_occurrences}; "
            f"repetition_evidence={str(situation.repetition_evidence).lower()}."
        ),
        *temporal_truth,
        "Estratégias possíveis: " + "; ".join(STRATEGIES) + ".",
    ]
    if include_examples:
        lines.append("[EXEMPLOS DE ESTRATÉGIA - âncora sem copiar literalmente]")
        for example in select_examples(situation):
            lines.append(f"Situação: {example.trigger} | Forma aprovada: {example.candidate} | Motivo: {example.rationale}")
        lines.append("[PADRÕES A REPROVAR]")
        for example in NEGATIVE_EXAMPLES[:2]:
            lines.append(f"Evite: {example.candidate} ({example.rationale}).")
    return "\n".join(lines)
