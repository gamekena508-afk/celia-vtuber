import re
import unicodedata
from loguru import logger
from ..translate.translate_interface import TranslateInterface


def tts_filter(
    text: str,
    remove_special_char: bool,
    ignore_brackets: bool,
    ignore_parentheses: bool,
    ignore_asterisks: bool,
    ignore_angle_brackets: bool,
    translator: TranslateInterface | None = None,
) -> str:
    """
    Filter or do anything to the text before TTS generates the audio.
    Changes here do not affect subtitles or LLM's memory. The generated audio is
    the only affected thing.

    Args:
        text (str): The text to filter.
        remove_special_char (bool): Whether to remove special characters.
        ignore_brackets (bool): Whether to ignore text within brackets.
        ignore_parentheses (bool): Whether to ignore text within parentheses.
        ignore_asterisks (bool): Whether to ignore text within asterisks.
        translator (TranslateInterface, optional):
            The translator to use. If None, we'll skip the translation. Defaults to None.

    Returns:
        str: The filtered text.
    """
    if ignore_asterisks:
        try:
            text = filter_asterisks(text)
        except Exception as e:
            logger.warning(f"Error ignoring asterisks: {e}")
            logger.warning(f"Text: {text}")
            logger.warning("Skipping...")

    if ignore_brackets:
        try:
            text = filter_brackets(text)
        except Exception as e:
            logger.warning(f"Error ignoring brackets: {e}")
            logger.warning(f"Text: {text}")
            logger.warning("Skipping...")
    if ignore_parentheses:
        try:
            text = filter_parentheses(text)
        except Exception as e:
            logger.warning(f"Error ignoring parentheses: {e}")
            logger.warning(f"Text: {text}")
            logger.warning("Skipping...")
    if ignore_angle_brackets:
        try:
            text = filter_angle_brackets(text)
        except Exception as e:
            logger.warning(f"Error ignoring angle brackets: {e}")
            logger.warning(f"Text: {text}")
            logger.warning("Skipping...")
    try:
        text = remove_unbracketed_emotion_tags(text)
    except Exception as e:
        logger.warning(f"Error removing unbracketed emotion tags: {e}")
        logger.warning(f"Text: {text}")
        logger.warning("Skipping...")
    if remove_special_char:
        try:
            text = remove_special_characters(text)
        except Exception as e:
            logger.warning(f"Error removing special characters: {e}")
            logger.warning(f"Text: {text}")
            logger.warning("Skipping...")
    if translator:
        try:
            logger.info("Translating...")
            text = translator.translate(text)
            logger.info(f"Translated: {text}")
        except Exception as e:
            logger.critical(f"Error translating: {e}")
            logger.critical(f"Text: {text}")
            logger.warning("Skipping...")

    logger.debug(f"Filtered text: {text}")
    return text


def remove_special_characters(text: str) -> str:
    """
    Filter text to remove all non-letter, non-number, and non-punctuation characters.

    Args:
        text (str): The text to filter.

    Returns:
        str: The filtered text.
    """
    normalized_text = unicodedata.normalize("NFKC", text)

    def is_valid_char(char: str) -> bool:
        category = unicodedata.category(char)
        return (
            category.startswith("L")
            or category.startswith("N")
            or category.startswith("P")
            or char.isspace()
        )

    filtered_text = "".join(char for char in normalized_text if is_valid_char(char))
    return filtered_text


def _filter_nested(text: str, left: str, right: str) -> str:
    """
    Generic function to handle nested symbols.

    A delimiter that opens and is never closed must NOT swallow the rest of
    the speech: only the stray symbol itself is dropped, the words after it
    survive. Content is only removed between a LEFT/RIGHT pair that actually
    closes.

    Args:
        text (str): The text to filter.
        left (str): The left symbol (e.g. '[' or '(').
        right (str): The right symbol (e.g. ']' or ')').

    Returns:
        str: The filtered text.
    """
    if not isinstance(text, str):
        raise TypeError("Input must be a string")
    if not text:
        return text

    length = len(text)
    remove = [False] * length
    unmatched_lefts: list[int] = []
    for index, char in enumerate(text):
        if char == left:
            unmatched_lefts.append(index)
        elif char == right:
            if unmatched_lefts:
                opening = unmatched_lefts.pop()
                # Matched pair: the symbols AND everything between them are
                # stage directions, not speech.
                for i in range(opening, index + 1):
                    remove[i] = True
            else:
                # Closing without opening: drop the stray symbol only.
                remove[index] = True
    # Opened and never closed: drop the stray symbol, KEEP the words after it.
    for index in unmatched_lefts:
        remove[index] = True

    filtered_text = "".join(
        char for index, char in enumerate(text) if not remove[index]
    )
    filtered_text = re.sub(r"\s+", " ", filtered_text).strip()
    return filtered_text


def filter_brackets(text: str) -> str:
    """
    Filter text to remove all text within brackets, handling nested cases.

    Args:
        text (str): The text to filter.

    Returns:
        str: The filtered text.
    """
    return _filter_nested(text, "[", "]")


def filter_parentheses(text: str) -> str:
    """
    Filter text to remove all text within parentheses, handling nested cases.

    Args:
        text (str): The text to filter.

    Returns:
        str: The filtered text.
    """
    return _filter_nested(text, "(", ")")


def filter_angle_brackets(text: str) -> str:
    """
    Filter text to remove all text within angle brackets, handling nested cases.

    Args:
        text (str): The text to filter.

    Returns:
        str: The filtered text.
    """
    return _filter_nested(text, "<", ">")


# Emotion tags the Live2D bridge understands (union of the emotionMap of the
# models in model_dict.json plus the emotion emitted by the games bridge).
# Written without brackets by the model, they would otherwise be SPOKEN.
EMOTION_TAGS: tuple[str, ...] = (
    "angry",
    "confused",
    "funny",
    "happy",
    "holding_back_laughter",
    "in_love",
    "neutral",
    "passionate",
    "sad",
    "scared",
    "surprised",
    "teasing",
    "trol_idle",
    "trol_speaking",
    "confident",
    "excited", "calm", "whisper", "singing", "nervous",
)

# Markup tokens are transport/control metadata, not user-facing speech. Keep the
# list narrow so ordinary bracketed prose is not erased. Emotion extraction for
# Live2D happens before this cleaner is applied to display/TTS.
_CONTROL_TAG_NAMES: tuple[str, ...] = (*EMOTION_TAGS, "think", "emotion")
_CONTROL_TAG_ALT = "|".join(
    re.escape(tag) for tag in sorted(_CONTROL_TAG_NAMES, key=len, reverse=True)
)
_CONTROL_MARKUP_RE = re.compile(
    rf"(?:\[\s*/?\s*(?:{_CONTROL_TAG_ALT})\s*\]|<\s*/?\s*(?:{_CONTROL_TAG_ALT})\s*>)",
    re.IGNORECASE,
)


def remove_control_markup_tags(text: str) -> str:
    """Remove known Célia control markers without deleting surrounding speech."""
    if not isinstance(text, str):
        raise TypeError("Input must be a string")
    cleaned = _CONTROL_MARKUP_RE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip() if cleaned != text else text

# Longest first so an alternation branch never shadows a longer sibling.
_TAG_ALTERNATION = "|".join(
    re.escape(tag) for tag in sorted(EMOTION_TAGS, key=len, reverse=True)
)

# Bare tag at the very START: optional punctuation/whitespace junk, the tag as
# a whole word, then junk. "\\W" cannot eat word characters, so a tag glued to
# a longer word ("happily", "unsad") never matches.
_LEADING_TAG_RE = re.compile(rf"^[\W_]*(?:{_TAG_ALTERNATION})\b[\W_]*", re.IGNORECASE)

# Bare tag at the very END: whitespace before it, the tag as a whole word,
# trailing punctuation. The punctuation BEFORE the tag is preserved so
# "Vou jogar agora. confident" keeps its final period.
_TRAILING_TAG_RE = re.compile(rf"\s*\b(?:{_TAG_ALTERNATION})[\W_]*$", re.IGNORECASE)


def remove_unbracketed_emotion_tags(text: str) -> str:
    """
    Remove a known emotion tag (with or without brackets — bracketed forms are
    normally already gone by now) when it sits ALONE at the very beginning or
    the very end of the text, case-insensitive, whole token.

    A tag in the MIDDLE of real text is kept: removing it there would be a
    guess, and words like "saudade" contain no tag but words like a quoted
    "happy" mid-sentence are real speech.

    Args:
        text (str): The text to clean.

    Returns:
        str: The cleaned text. Untouched byte-for-byte when no tag matches.
    """
    if not isinstance(text, str):
        raise TypeError("Input must be a string")

    result = text
    # Bounded: handles stacks like "happy happy Oi Pai. sad sad" without risk
    # of an unbounded loop.
    for _ in range(8):
        stripped = _LEADING_TAG_RE.sub("", result, count=1)
        stripped = _TRAILING_TAG_RE.sub("", stripped, count=1)
        if stripped == result:
            break
        result = stripped
    if result != text:
        result = result.strip()
    return result


def filter_asterisks(text: str) -> str:
    """
    Removes text enclosed within asterisks of any length (*, **, ***, etc.) from a string.

    Args:
        text: The input string.

    Returns:
        The string with asterisk-enclosed text removed.
    """
    # Handle asterisks of any length (*, **, ***, etc.)
    filtered_text = re.sub(r"\*{1,}((?!\*).)*?\*{1,}", "", text)

    # Clean up any extra spaces
    filtered_text = re.sub(r"\s+", " ", filtered_text).strip()

    return filtered_text


def _spoken_number_words(token: str) -> str:
    """Render a numeric token as deterministic Brazilian Portuguese speech."""
    from num2words import num2words

    raw = str(token).strip()
    negative = raw.startswith("-")
    if negative:
        raw = raw[1:]
    separator = "," if "," in raw else ("." if "." in raw else None)
    if separator:
        integer, fraction = raw.split(separator, 1)
        whole = num2words(int(integer or "0"), lang="pt_BR")
        decimal = " ".join(num2words(int(digit), lang="pt_BR") for digit in fraction)
        words = f"{whole} vírgula {decimal}"
    else:
        words = num2words(int(raw), lang="pt_BR")
    return f"menos {words}" if negative else words


def _capitalize_number_if_sentence_start(source: str, start: int, words: str) -> str:
    before = source[:start].rstrip()
    if not before or before.endswith((".", "!", "?")):
        return words[:1].upper() + words[1:]
    return words


def normalize_production_spoken_numbers(text: str) -> str:
    """Keep digits out of the fixed-voice TTS channel while preserving display text."""
    # Common spoken units get semantic wording instead of leaving symbols for TTS.
    # Brazil: R$ 1.234,56 deve ser lido como mil duzentos e trinta
    # e quatro reais e cinquenta e seis centavos, não como decimal "1.234".
    # Suporta também R$ 50, R$ 50,5 e R$ 50.25, sem confundir ponto final.
    currency = re.compile(
        r"R\$\s*(-?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:[.,]\d{1,2})?)(?!\d)(?![.,]\d)",
        re.IGNORECASE,
    )
    def replace_currency(match):
        raw = match.group(1)
        negative = raw.startswith("-")
        positive = raw.lstrip("-")
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", positive):
            positive = positive.replace(".", "")  # agrupamento de milhares
        integer, _, fraction = positive.partition("," if "," in positive else ".")
        reais = int(integer)
        centavos = int(fraction.ljust(2, "0")) if fraction else 0
        words = f"{_spoken_number_words(str(reais))} {'real' if reais == 1 else 'reais'}"
        if centavos:
            words += f" e {_spoken_number_words(str(centavos))} {'centavo' if centavos == 1 else 'centavos'}"
        if negative:
            words = "menos " + words
        return _capitalize_number_if_sentence_start(text, match.start(), words)
    normalized = currency.sub(replace_currency, text)

    percent = re.compile(r"(?<![\w])(-?\d+(?:[.,]\d+)?)\s*%")
    def replace_percent(match):
        words = _spoken_number_words(match.group(1))
        return _capitalize_number_if_sentence_start(normalized, match.start(), f"{words} por cento")
    normalized = percent.sub(replace_percent, normalized)

    number = re.compile(r"(?<![\w])(-?\d+(?:[.,]\d+)?)(?![\w])")
    def replace_number(match):
        words = _spoken_number_words(match.group(1))
        return _capitalize_number_if_sentence_start(normalized, match.start(), words)
    return number.sub(replace_number, normalized)


def sanitize_production_tts_text(text: str) -> str:
    """Strip control markers and normalize spoken numbers for the production TTS only."""
    if not isinstance(text, str):
        raise TypeError("Input must be a string")
    cleaned = remove_control_markup_tags(text)
    cleaned = filter_brackets(cleaned)
    cleaned = remove_unbracketed_emotion_tags(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return normalize_production_spoken_numbers(cleaned)
