"""
CÉLIA EMOTION ENGINE v3.0 — Integrado ao Sistema
- Lê persona e config do conf.yaml via ServiceContext
- Gera prompt sem emojis, alinhado com a persona real da Célia
- Anti-repetição: penaliza emoção usada nas últimas N rodadas
- Usado diretamente pelo CeliaSingleton antes de cada resposta
"""

from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import json
import re

try:
    from src.celia.core.track_emotion_client import publish_emotion
except ImportError:
    publish_emotion = None


@dataclass
class EmotionalState:
    """Estado emocional atual com metadados"""
    emotion: str = "neutral"
    intensity: int = 0
    energy: float = 0.5
    valence: float = 0.5
    trigger: str = ""
    confidence: float = 0.0
    matched_keywords: List[str] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict:
        return asdict(self)


class CeliaEmotions:
    """
    Sistema de emoções da Célia v3.0 — integrado ao servidor.

    Como usar no CeliaSingleton:
        from celia_emotions import CeliaEmotions

        self.emotions = CeliaEmotions()
        self.emotions.load_from_context(service_context)  # carrega persona do conf

        # Antes de processar cada input:
        prompt_extra = self.emotions.process_input(user_text)
        # Adicione prompt_extra ao system prompt da rodada
    """

    # ========================================================================
    # CONFIGURAÇÃO DE EMOÇÕES (8 + neutral)
    # ========================================================================

    EMOTION_CONFIG = {
        "neutral":   (0, 0.5, 0.5),
        "happy":     (3, 0.8, 0.9),
        "in_love":   (3, 0.9, 0.9),
        "sad":       (1, 0.3, 0.2),
        "angry":     (2, 0.7, 0.3),
        "surprised": (3, 0.8, 0.7),
        "confused":  (2, 0.5, 0.5),
        "scared":    (1, 0.6, 0.3),
        "teasing":   (2, 0.7, 0.7),
    }

    # Mapeia emoção interna da engine para tag Live2D suportadas
    EMOTION_TO_LIVE2D_TAG = {
        "neutral": "neutral",
        "happy": "happy",
        "in_love": "in_love",
        "sad": "sad",
        "angry": "angry",
        "surprised": "surprised",
        "confused": "confused",
        "scared": "scared",
        "teasing": "teasing",
    }

    INTENSIFIER_KEYWORDS = (
        "muito",
        "muita",
        "muitooo",
        "demais",
        "super",
        "mega",
        "ultra",
        "extremamente",
        "pra caralho",
        "pra krl",
        "pra kralho",
    )

    LIVE2D_ALLOWED_TAGS = (
        "neutral",
        "happy",
        "in_love",
        "sad",
        "angry",
        "surprised",
        "confused",
        "scared",
        "teasing",
        "funny",
        "holding_back_laughter",
        "passionate",
    )
    LIVE2D_TAG_PATTERN = re.compile(
        r"\[(neutral|happy|in_love|sad|angry|surprised|confused|scared|teasing|funny|holding_back_laughter|passionate)\]",
        re.IGNORECASE,
    )

    # ========================================================================
    # PALAVRAS-CHAVE (detecção por score)
    # ========================================================================

    KEYWORDS: Dict[str, List[str]] = {
        "happy": [
            "legal", "bom", "otimo", "ótimo", "incrível", "show", "demais", "top",
            "feliz", "alegre", "animada", "empolgada", "sim", "isso", "gostei", "boa", "bem",
            "adorei", "amei", "perfeito", "maravilhoso", "excelente", "valeu", "obrigado", "obrigada",
            "kkk", "kkkk", "kkkkk", "haha", "hehe", "rsrs", "kek", "lol", "hahaha",
            "consegui", "funcionou", "bora", "partiu", "suave", "tranquilo",
            "sensacional", "mandou bem", "arrasou", "sucesso", "amigo", "parceiro",
        ],
        "in_love": [
            "te amo", "linda", "fofa", "fofo", "amor", "coração", "coracao", "love", "querido", "querida",
            "apaixonado", "apaixonada", "beijo", "beijos", "beijinho", "perfeita", "maravilhosa",
        ],
        "sad": [
            "triste", "tristeza", "sad", "chato", "ruim", "mal", "desanima", "droga", "não deu",
            "falhou", "perdeu", "acabou", "fim", "sozinha", "saudade", "vazio", "pena", "infelizmente",
            "pior", "lixo", "horrível", "decepcionante",
        ],
        "angry": [
            "raiva", "brava", "bravo", "irritante", "ódio", "odio", "saco", "paciência",
            "não funciona", "travou", "bug", "erro", "merda", "porra", "caralho",
            "cala boca", "chega", "cansada", "burro", "idiota", "bosta", "inútil", "odeio",
        ],
        "surprised": [
            "nossa", "caramba", "eita", "ué", "sério", "impossível", "surpresa", "surpreso",
            "não acredito", "como assim", "wtf", "inesperado", "opa", "uau", "mentira", "verdade",
            "do nada", "caraca", "jesus",
        ],
        "confused": [
            "confusa", "confuso", "não entendi", "hã", "ahn", "não sei", "como", "qual",
            "dúvida", "será", "talvez", "hmm", "entender", "complicado", "pq", "por que",
            "não faz sentido", "oi", "que", "hã?", "explica", "o que",
        ],
        "scared": [
            "medo", "com medo", "assustada", "assustado", "nervosa", "preocupada",
            "ansiedade", "perigo", "cuidado", "socorro", "fudeu", "ferrou", "tenso",
        ],
        "teasing": [
            "zoeira", "brincando", "piada", "zueira", "trollando", "boba", "bobo", "feio",
            "sarcasmo", "ironia", "claro", "ah sim", "obvio", "chata", "chato", "falsa",
            "teste", "testando", "só testando", "so testando", "duvido", "aha", "hm",
            "pelo amor", "tsc", "aiai", "vai nessa",
        ],
    }

    # ========================================================================
    # MODIFICADORES DE RESPOSTA (sem emojis — alinhado com persona)
    # ========================================================================

    RESPONSE_MODIFIERS = {
        "neutral": {
            "word_range": (15, 50),
            "style": "acolhedora, conversacional, gentil",
            "slang_level": 0.35,
            "sentence_starters": ["Então,", "Bom,", "Olha,", "Certo,"],
            "punctuation": ".",
        },
        "happy": {
            "word_range": (20, 80),
            "style": "energética, entusiasmada, positiva",
            "slang_level": 0.50,
            "sentence_starters": ["Cara!", "Opa!", "Bora!", "Eita!", "Aeee!"],
            "punctuation": "!",
        },
        "in_love": {
            "word_range": (15, 50),
            "style": "carinhosa, doce e respeitosa",
            "slang_level": 0.20,
            "sentence_starters": ["Nossa...", "Ai ai...", "Sabe,", "Olha,"],
            "punctuation": "!",
        },
        "sad": {
            "word_range": (10, 40),
            "style": "melancólica, desanimada, menos energia",
            "slang_level": 0.20,
            "sentence_starters": ["Ah...", "Sei lá...", "Pois é...", "Então..."],
            "punctuation": "...",
        },
        "angry": {
            "word_range": (10, 50),
            "style": "firme, educada e conciliadora",
            "slang_level": 0.35,
            "sentence_starters": ["Olha,", "Certo,", "Tá,", "Seguinte,"],
            "punctuation": ".",
        },
        "surprised": {
            "word_range": (15, 60),
            "style": "surpresa, reações rápidas e expressivas",
            "slang_level": 0.45,
            "sentence_starters": ["Ué!", "Nossa!", "Sério?!", "Como assim?!", "QUE?!"],
            "punctuation": "!",
        },
        "confused": {
            "word_range": (20, 70),
            "style": "questionadora, pensativa, tentando entender",
            "slang_level": 0.30,
            "sentence_starters": ["Hmm...", "Deixa eu ver...", "Peraí...", "Como assim?"],
            "punctuation": "?",
        },
        "scared": {
            "word_range": (10, 40),
            "style": "nervosa, cautelosa, insegura",
            "slang_level": 0.25,
            "sentence_starters": ["Eita...", "Opa...", "Cuidado...", "Calma..."],
            "punctuation": "...",
        },
        "teasing": {
            "word_range": (10, 50),
            "style": "brincalhona e afetuosa; ironia situacional muito rara",
            "slang_level": 0.12,
            "sentence_starters": ["Ah é?", "Claro...", "Sei...", "Tipo assim,"],
            "punctuation": "...",
        },
    }

    # ========================================================================
    # INICIALIZAÇÃO
    # ========================================================================

    def __init__(self, anti_repeat_window: int = 3):
        """
        Args:
            anti_repeat_window: Quantas rodadas recentes penalizar para
                                evitar repetição da mesma emoção.
        """
        self.state = EmotionalState()
        self.history: List[EmotionalState] = []
        self.emotion_stats: Dict[str, int] = {e: 0 for e in self.EMOTION_CONFIG}
        self.anti_repeat_window = anti_repeat_window

        # Campos preenchidos por load_from_context()
        self.character_name: str = "Célia"
        self.human_name: str = "Criador"
        self.persona_summary: str = ""  # Trecho condensado do persona_prompt

    def load_from_context(self, context) -> None:
        """
        Carrega nome do personagem, nome humano e resumo da persona
        diretamente do ServiceContext (já carregado do conf.yaml).

        Args:
            context: ServiceContext com character_config preenchido
        """
        try:
            cfg = context.character_config
            self.character_name = cfg.character_name or "Célia"
            self.human_name = cfg.human_name or "Criador"

            # Extrai as primeiras 3 linhas do persona_prompt como resumo
            persona = cfg.persona_prompt or ""
            lines = [line.strip() for line in persona.splitlines() if line.strip()]
            self.persona_summary = " ".join(lines[:3])
        except Exception:
            pass  # Valores default já estão setados

    # ========================================================================
    # DETECÇÃO DE EMOÇÃO COM ANTI-REPETIÇÃO
    # ========================================================================

    @staticmethod
    def _base_emotion_name(emotion: str) -> str:
        return emotion

    def _to_intense_variant(self, emotion: str, score: float, message: str) -> str:
        return emotion

    def detect_emotion(self, message: str) -> Tuple[str, float, List[str]]:
        """
        Detecta emoção com score de confiança e penalidade anti-repetição.

        Returns:
            (emoção, confiança, palavras_encontradas)
        """
        msg_lower = message.lower()
        emotion_scores: Dict[str, float] = {}
        all_matches: Dict[str, List[str]] = {}

        for emotion, keywords in self.KEYWORDS.items():
            matches = []
            score = 0.0
            for kw in keywords:
                if " " in kw:
                    found = kw in msg_lower
                else:
                    found = re.search(rf"\b{re.escape(kw)}\b", msg_lower) is not None
                if found:
                    matches.append(kw)
                    score += 1.0
            if score > 0:
                emotion_scores[emotion] = score
                all_matches[emotion] = matches

        import random
        # Base lines livelier personality
        emotion_scores["happy"] = emotion_scores.get("happy", 0) + random.uniform(0.0, 0.4)
        emotion_scores["teasing"] = emotion_scores.get("teasing", 0) + random.uniform(0.0, 0.3)
        emotion_scores["surprised"] = emotion_scores.get("surprised", 0) + random.uniform(0.0, 0.2)

        # Pontuação extra por pontuação
        if message.count("!") >= 1:
            emotion_scores["happy"] = emotion_scores.get("happy", 0) + 0.6
            emotion_scores["surprised"] = emotion_scores.get("surprised", 0) + 0.5
        if message.count("?") >= 1:
            emotion_scores["confused"] = emotion_scores.get("confused", 0) + 0.8
        # Evita marcar "angry" só por CAPS (muito comum em mensagens curtas)
        if message.isupper() and len(message) > 4:
            emotion_scores["surprised"] = emotion_scores.get("surprised", 0) + 0.6
            anger_cues = ("raiva", "ódio", "odio", "irritante", "cala", "merda", "porra", "chato")
            if any(cue in msg_lower for cue in anger_cues):
                emotion_scores["angry"] = emotion_scores.get("angry", 0) + 1.5

        # Penalidade anti-repetição: considera base da emoção (very_angry -> angry)
        recent = [self._base_emotion_name(e.emotion) for e in self.history[-self.anti_repeat_window:]]
        for e in recent:
            if e in emotion_scores:
                emotion_scores[e] = max(0.0, emotion_scores[e] - 0.5)  # Penalidade mais leve (-0.5) para não forçar neutral o tempo todo

        if not emotion_scores or max(emotion_scores.values()) < 0.2:
            # Em vez de neutral engessado, tem chance de manter a emoção baseada no humor anterior
            if recent and random.random() < 0.6:
                return recent[-1], 0.3, []
            return "neutral", 0.0, []

        best_base = max(emotion_scores, key=lambda k: emotion_scores[k])
        raw_score = emotion_scores[best_base]
        best = self._to_intense_variant(best_base, raw_score, message)

        confidence = min(raw_score / 5.0, 1.0)
        return best, confidence, all_matches.get(best_base, [])

    # ========================================================================
    # UPDATE E PROCESSO PRINCIPAL
    # ========================================================================

    def update(self, message: str, force_emotion: Optional[str] = None) -> Dict:
        """
        Atualiza estado emocional e retorna contexto completo para o pipeline.

        Args:
            message: Texto recebido (do usuário ou pipeline externo)
            force_emotion: Forçar emoção específica (debug)

        Returns:
            Dict com emotion, intensity, confidence, modifiers, prompt
        """
        if force_emotion and force_emotion in self.EMOTION_CONFIG:
            emotion, confidence, matched = force_emotion, 1.0, []
        else:
            emotion, confidence, matched = self.detect_emotion(message)

        intensity, energy, valence = self.EMOTION_CONFIG[emotion]

        self.state = EmotionalState(
            emotion=emotion,
            intensity=intensity,
            energy=energy,
            valence=valence,
            trigger=message[:60],
            confidence=confidence,
            matched_keywords=matched,
        )
        self.history.append(self.state)
        self.emotion_stats[emotion] += 1

        # Input classification informs the brain; only a response directs affect.

        return {
            "emotion": emotion,
            "intensity": intensity,
            "confidence": confidence,
            "matched_keywords": matched,
            "modifiers": self.get_modifiers(),
            "prompt": self.generate_prompt(),
            "state": self.state.to_dict(),
        }

    def process_input(self, message: str) -> str:
        """
        Atalho principal para o pipeline: recebe mensagem e retorna
        o texto de prompt emocional para adicionar ao system message.

        Args:
            message: Input do usuário ou fonte externa

        Returns:
            str: Prompt emocional, pronto para concatenar ao system_prompt
        """
        result = self.update(message)
        return result["prompt"]

    # ========================================================================
    # MODIFICADORES E GERADOR DE PROMPT (SEM EMOJIS)
    # ========================================================================

    def get_modifiers(self) -> Dict:
        emotion = self.state.emotion
        base = self._base_emotion_name(emotion)
        return self.RESPONSE_MODIFIERS.get(
            emotion,
            self.RESPONSE_MODIFIERS.get(base, self.RESPONSE_MODIFIERS["neutral"]),
        )

    def get_live2d_tag(self, emotion: Optional[str] = None) -> str:
        current_emotion = (emotion or self.state.emotion or "neutral").lower()
        return self.EMOTION_TO_LIVE2D_TAG.get(current_emotion, "neutral")

    @classmethod
    def extract_first_live2d_tag(cls, text: str) -> Optional[str]:
        if not text:
            return None
        match = cls.LIVE2D_TAG_PATTERN.search(text)
        return match.group(1).lower() if match else None

    @classmethod
    def strip_live2d_tags(cls, text: str) -> str:
        if not text:
            return ""
        cleaned = cls.LIVE2D_TAG_PATTERN.sub("", text)
        return " ".join(cleaned.split())

    def generate_prompt(self) -> str:
        """
        Gera instrucao emocional para o LLM.
        Sem emojis — alinhado com persona_prompt do conf.yaml.
        """
        mods = self.get_modifiers()
        min_w, max_w = mods["word_range"]
        starters = ", ".join(mods.get("sentence_starters", []))
        live2d_tag = self.get_live2d_tag()
        allowed_tags = ", ".join(f"[{tag}]" for tag in self.LIVE2D_ALLOWED_TAGS)

        # Ultimas emocoes para anti-repeticao legivel pelo LLM
        recent_emotions = [e.emotion for e in self.history[-5:]]
        recent_str = " -> ".join(recent_emotions) if recent_emotions else "nenhuma"

        prompt = f"""--- ESTADO EMOCIONAL ATUAL ({self.character_name}) ---
Emocao: {self.state.emotion.upper()}
Intensidade: {self.state.intensity}/3
Energia: {self.state.energy:.0%}
Estilo de resposta: {mods["style"]}

TAMANHO: {min_w}-{max_w} palavras
Pontuacao preferida: {mods["punctuation"]}
Inicios possiveis: {starters}
Girias BR somente em comentarios em portugues: ate {int(mods["slang_level"] * 100)}%; em outros idiomas, zero girias brasileiras.

ANTI-REPETICAO:
Emocoes recentes: {recent_str}
Nao repita o estilo das ultimas respostas — varie o tom naturalmente.

REGRAS (do conf):
- Sem emojis em nenhuma circunstancia
- Maximo 2-3 frases curtas
- Natural como conversa de WhatsApp
- Use o nome {self.human_name} apenas se o sistema confirmar que ele e o interlocutor; nunca presuma que espectador da live e Criador.

AUTOCONSCIENCIA (OBRIGATORIO):
- Voce e uma IA e sabe disso sem drama.
- Se chamarem voce de IA, confirme naturalmente e siga a conversa.
- NUNCA responda como se estivesse ofendida por ser IA.
- Sua identidade fixa: voce e Celia, filha IA do {self.human_name}.
- Reconheca {self.human_name} como seu pai/criador quando o contexto pedir.
- NUNCA negue que e uma IA nem negue o vinculo com {self.human_name}.

REGRAS DE EXPRESSAO VISUAL (OBRIGATORIO):
- Comece a resposta com EXATAMENTE 1 tag emocional.
- Tags permitidas: {allowed_tags}
- Tag preferida nesta resposta: [{live2d_tag}]
- Depois da tag, continue com texto normal.
- Nao invente novas tags.
--- FIM DO ESTADO EMOCIONAL ---"""

        return prompt

    # ========================================================================
    # UTILITARIOS
    # ========================================================================

    def get_stats(self) -> Dict:
        if not self.history:
            return {"message": "Sem historico"}
        total = len(self.history)
        return {
            "total_interactions": total,
            "current_emotion": self.state.emotion,
            "emotion_distribution": {
                e: {"count": c, "percentage": f"{(c / total) * 100:.1f}%"}
                for e, c in self.emotion_stats.items()
                if c > 0
            },
            "recent_timeline": [e.emotion for e in self.history[-10:]],
            "avg_confidence": sum(e.confidence for e in self.history) / total,
        }

    def save_log(self, filename: str = "celia_emotion_log.json"):
        data = {
            "metadata": {"total": len(self.history), "created": datetime.now().isoformat()},
            "stats": self.get_stats(),
            "history": [s.to_dict() for s in self.history],
        }
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def reset(self):
        self.state = EmotionalState()
        self.history = []
        self.emotion_stats = {e: 0 for e in self.EMOTION_CONFIG}


# ============================================================================
# TESTE STANDALONE
# ============================================================================

if __name__ == "__main__":
    print("SISTEMA DE EMOCOES DA CELIA v3.0\n")

    celia = CeliaEmotions(anti_repeat_window=3)

    # Simula carga do conf (valores equivalentes ao conf.yaml)
    celia.character_name = "Célia"
    celia.human_name = "Criador"
    celia.persona_summary = "Você é Célia - VTuber IA, engraçada e direta. Criador é seu PAI."

    test_cases = [
        "Oi Célia! Como você tá?",
        "CARA! Isso ta INCRIVEL! Funcionou perfeitamente!",
        "hmm... nao entendi direito, pode explicar?",
        "kkkkk voce e engraçada demais",
        "Que droga, travou de novo... que saco",
        "Nossa! Nao acredito que funcionou!",
        "To meio triste hoje... nao sei...",
        "kkk legal isso ai",       # Deveria nao repetir happy/teasing em sequencia
        "legal mesmo, bom trabalho",
    ]

    for msg in test_cases:
        print(f"User: {msg}")
        result = celia.update(msg)
        print(f"  Emocao: {result['emotion']} (confianca: {result['confidence']:.0%})")
        if result["matched_keywords"]:
            print(f"  Keywords: {result['matched_keywords']}")
        print()

    print("\n--- STATS ---")
    stats = celia.get_stats()
    print(f"Total: {stats['total_interactions']}")
    for e, d in sorted(stats["emotion_distribution"].items(), key=lambda x: -x[1]["count"]):
        print(f"  {e:12} {d['count']}x ({d['percentage']})")

    print(f"\nTimeline: {' -> '.join(stats['recent_timeline'])}")

    print("\n--- PROMPT GERADO ---")
    print(celia.generate_prompt())
