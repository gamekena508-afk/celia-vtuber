"""
Utilitários de debug para o sistema de emoção da Célia.
Expõe get_emotion_state() como função única que retorna JSON legível.
"""

import json
from pathlib import Path
from typing import Optional

CURRENT_EMOTION_FILE = Path(__file__).parent.parent.parent.parent.parent / "current_emotion.txt"


def get_emotion_state() -> dict:
    """
    Retorna o estado emocional atual da Célia como dict.
    Lê do CeliaSingleton se disponível, caso contrário do arquivo.
    """
    try:
        from celia.celia_singleton import CeliaSingleton
        singleton = CeliaSingleton.get_instance()
        if singleton and hasattr(singleton, "emotions") and singleton.emotions:
            state = singleton.emotions.current_state
            if state:
                return {
                    "emotion": state.emotion,
                    "intensity": state.intensity,
                    "energy": state.energy,
                    "valence": state.valence,
                    "trigger": state.trigger,
                    "confidence": state.confidence,
                    "matched_keywords": state.matched_keywords,
                    "timestamp": state.timestamp,
                    "source": "singleton",
                }
    except Exception:
        pass

    # Fallback: arquivo current_emotion.txt
    emotion = "neutral"
    if CURRENT_EMOTION_FILE.exists():
        try:
            emotion = CURRENT_EMOTION_FILE.read_text(encoding="utf-8").strip() or "neutral"
        except Exception:
            pass

    return {
        "emotion": emotion,
        "intensity": 0,
        "energy": 0.5,
        "valence": 0.5,
        "trigger": "",
        "confidence": 0.0,
        "matched_keywords": [],
        "timestamp": "",
        "source": "file",
    }


def format_emotion_debug() -> str:
    """Formata o estado emocional como string legível para chat/log."""
    state = get_emotion_state()
    linhas = [
        f"🎭 Emoção: {state['emotion']}",
        f"⚡ Energia: {state['energy']:.1f} | Valência: {state['valence']:.1f}",
        f"🎯 Trigger: {state['trigger'] or 'nenhum'}",
        f"🔑 Keywords: {', '.join(state['matched_keywords']) or 'nenhuma'}",
        f"📡 Fonte: {state['source']}",
    ]
    return "\n".join(linhas)
