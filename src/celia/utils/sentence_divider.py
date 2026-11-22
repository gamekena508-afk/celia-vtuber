import os
import re
from typing import List, Tuple, AsyncIterator, Optional, Union, Dict, Any
import pysbd
from loguru import logger
from langdetect import detect
from enum import Enum
from dataclasses import dataclass

# Constants for additional checks
COMMAS = [
    ",",
    "،",
    "，",
    "、",
    "፣",
    "၊",
    ";",
    "΄",
    "‛",
    "।",
    "﹐",
    "꓾",
    "⹁",
    "︐",
    "﹑",
    "､",
    "،",
]

END_PUNCTUATIONS = [".", "!", "?", "。", "！", "？", "...", "。。。"]
DELIMITER_PAIRS = {"(": ")", "[": "]", "<": ">"}
CLOSING_DELIMITERS = {closing: opening for opening, closing in DELIMITER_PAIRS.items()}
END_PUNCTUATION_CHARS = set("".join(END_PUNCTUATIONS))
ABBREVIATIONS = [
    "Mr.",
    "Mrs.",
    "Dr.",
    "Prof.",
    "Inc.",
    "Ltd.",
    "Jr.",
    "Sr.",
    "e.g.",
    "i.e.",
    "vs.",
    "St.",
    "Rd.",
    "Dr.",
]

_NO_SPACE_BEFORE = frozenset(",.;:!?)]}>%\u00bb\u201d\u2019")
_NO_SPACE_AFTER = frozenset("([{\u00ab\u201c\u2018")
_DOMAIN_SUFFIXES = frozenset({
    "com", "net", "org", "io", "dev", "ai", "br", "pt", "py", "js",
    "ts", "json", "yaml", "yml", "txt", "md",
})


def _boundary_is_token_continuation(left: str, right: str) -> bool:
    """Detect boundaries that must remain inside a number, address, or path."""
    if not left or not right:
        return False

    last_left = left[-1]
    first_right = right[0]
    if last_left in "@/\\":
        return True
    if last_left == ":" and first_right.isdigit():
        return True
    if last_left == "," and left[-2:-1].isdigit() and first_right.isdigit():
        return True
    if last_left != ".":
        return False
    if first_right.isdigit():
        return True

    stem = left[:-1].rstrip()
    if not stem:
        return False
    if "@" in stem or "/" in stem or "\\" in stem:
        return True

    previous_token = stem.rsplit(None, 1)[-1].casefold()
    next_token = right.split(None, 1)[0].split(".", 1)[0].casefold()
    return next_token in _DOMAIN_SUFFIXES and len(previous_token) > 1


def _needs_boundary_space(left: str, right: str) -> bool:
    """Decide only from the two adjacent pieces, never by rewriting their text."""
    if not left or not right or left[-1].isspace() or right[0].isspace():
        return False
    if right[0] in _NO_SPACE_BEFORE or left[-1] in _NO_SPACE_AFTER:
        return False
    if _boundary_is_token_continuation(left, right):
        return False
    return True


def juntar_pedacos(pedacos: list[str]) -> str:
    """Reassemble stripped stream pieces while preserving meaningful boundaries."""
    result = ""
    for piece in pedacos:
        if piece is None:
            continue
        piece = str(piece)
        if not piece:
            continue
        if result and _needs_boundary_space(result, piece):
            result += " "
        result += piece
    return result

# Set of languages directly supported by pysbd
SUPPORTED_LANGUAGES = {
    "am",
    "ar",
    "bg",
    "da",
    "de",
    "el",
    "en",
    "es",
    "fa",
    "fr",
    "hi",
    "hy",
    "it",
    "ja",
    "kk",
    "mr",
    "my",
    "nl",
    "pl",
    "ru",
    "sk",
    "ur",
    "zh",
}


def _iter_delimiter_states(text: str):
    """Yield each character and whether it is inside a delimited block."""
    stack = []

    for index, char in enumerate(text):
        if char == "*":
            if stack and stack[-1] == char:
                stack.pop()
            else:
                stack.append(char)
        elif char in DELIMITER_PAIRS:
            stack.append(char)
        elif char in CLOSING_DELIMITERS:
            if stack and stack[-1] == CLOSING_DELIMITERS[char]:
                stack.pop()

        yield index, char, bool(stack)


def _mask_delimited_end_punctuation(text: str) -> Tuple[str, Dict[int, str]]:
    """Hide sentence punctuation inside delimiters without changing text length."""
    used_characters = set(text)
    available_masks = (
        chr(codepoint)
        for codepoint in range(0xE000, 0xF900)
        if chr(codepoint) not in used_characters
    )
    punctuation_masks = {
        punctuation: next(available_masks) for punctuation in END_PUNCTUATION_CHARS
    }
    restore_table = {
        ord(mask): punctuation for punctuation, mask in punctuation_masks.items()
    }
    masked_characters = list(text)

    for index, char, is_delimited in _iter_delimiter_states(text):
        if is_delimited and char in punctuation_masks:
            masked_characters[index] = punctuation_masks[char]

    return "".join(masked_characters), restore_table


def _find_top_level_comma(text: str) -> Optional[Tuple[int, str]]:
    """Find the first configured comma that is outside every delimiter."""
    states = list(_iter_delimiter_states(text))
    for comma in COMMAS:
        for index, char, is_delimited in states:
            if not is_delimited and char == comma:
                return index, comma
    return None


def detect_language(text: str) -> str:
    """
    Detect text language and check if it's supported by pysbd.
    Returns None for unsupported languages.
    """
    try:
        detected = detect(text)
        return detected if detected in SUPPORTED_LANGUAGES else None
    except Exception as e:
        logger.debug(f"Language detection failed, language not supported by pysdb: {e}")
        return None


def is_complete_sentence(text: str) -> bool:
    """
    Check if text ends with sentence-ending punctuation and not abbreviation.

    Args:
        text: Text to check

    Returns:
        bool: Whether the text is a complete sentence
    """
    text = text.strip()
    if not text:
        return False

    if any(text.endswith(abbrev) for abbrev in ABBREVIATIONS):
        return False

    return any(text.endswith(punct) for punct in END_PUNCTUATIONS)


def contains_comma(text: str) -> bool:
    """
    Check if text contains any comma.

    Args:
        text: Text to check

    Returns:
        bool: Whether the text contains a comma
    """
    return _find_top_level_comma(text) is not None


def comma_splitter(text: str) -> Tuple[str, str]:
    """
    Process text and split it at the first comma.
    Returns the split text (including the comma) and the remaining text.

    Args:
        text: Text to split

    Returns:
        Tuple[str, str]: (split text with comma, remaining text)
    """
    if not text:
        return [], ""

    comma_match = _find_top_level_comma(text)
    if comma_match:
        comma_index, comma = comma_match
        # Keep separator whitespace so the next stream chunk cannot join words.
        return text[:comma_index].strip() + comma, text[comma_index + 1 :]
    return text, ""


def has_punctuation(text: str) -> bool:
    """
    Check if the text is a punctuation mark.

    Args:
        text: Text to check

    Returns:
        bool: Whether the text is a punctuation mark
    """
    for punct in COMMAS + END_PUNCTUATIONS:
        if punct in text:
            return True
    return False


def contains_end_punctuation(text: str) -> bool:
    """
    Check if text contains any sentence-ending punctuation.

    Args:
        text: Text to check

    Returns:
        bool: Whether the text contains ending punctuation
    """
    return any(punct in text for punct in END_PUNCTUATIONS)


def segment_text_by_regex(text: str) -> Tuple[List[str], str]:
    """
    Segment text into complete sentences using regex pattern matching.
    More efficient but less accurate than pysbd.

    Args:
        text: Text to segment into sentences

    Returns:
        Tuple[List[str], str]: (list of complete sentences, remaining incomplete text)
    """
    if not text:
        return [], ""

    complete_sentences = []
    # Preserve trailing whitespace: it may separate this remainder from the next chunk.
    remaining_text, restore_table = _mask_delimited_end_punctuation(text.lstrip())

    # Create pattern for matching sentences ending with any end punctuation
    escaped_punctuations = [re.escape(p) for p in END_PUNCTUATIONS]
    pattern = r"(.*?(?:[" + "|".join(escaped_punctuations) + r"]))"

    while remaining_text:
        match = re.search(pattern, remaining_text)
        if not match:
            break

        end_pos = match.end(1)
        potential_sentence = remaining_text[:end_pos].strip()

        # Skip if sentence ends with abbreviation
        if any(potential_sentence.endswith(abbrev) for abbrev in ABBREVIATIONS):
            remaining_text = remaining_text[end_pos:].lstrip()
            continue

        complete_sentences.append(potential_sentence)
        remaining_text = remaining_text[end_pos:].lstrip()

    return (
        [sentence.translate(restore_table) for sentence in complete_sentences],
        remaining_text.translate(restore_table),
    )


def segment_text_by_pysbd(text: str) -> Tuple[List[str], str]:
    """
    Segment text into complete sentences and remaining text.
    Uses pysbd for supported languages, falls back to regex for others.

    Args:
        text: Text to segment into sentences

    Returns:
        Tuple[List[str], str]: (list of complete sentences, remaining incomplete text)
    """
    if not text:
        return [], ""

    try:
        # Detect language
        lang = detect_language(text)

        if lang is not None:
            # Use pysbd for supported languages
            segmenter = pysbd.Segmenter(language=lang, clean=False)
            masked_text, restore_table = _mask_delimited_end_punctuation(text)
            sentences = segmenter.segment(masked_text)

            if not sentences:
                return [], text

            # Process all but the last sentence
            complete_sentences = []
            for sent in sentences[:-1]:
                sent = sent.strip()
                if sent:
                    complete_sentences.append(sent.translate(restore_table))

            # Handle the last sentence
            # Keep trailing whitespace on an incomplete sentence for the next chunk.
            masked_last_sent = sentences[-1].lstrip()
            last_sent = masked_last_sent.translate(restore_table)
            if is_complete_sentence(masked_last_sent):
                complete_sentences.append(last_sent.strip())
                remaining = ""
            else:
                remaining = last_sent

        else:
            # Use regex for unsupported languages
            return segment_text_by_regex(text)

        logger.debug(
            f"Processed sentences: {complete_sentences}, Remaining: {remaining}"
        )
        return complete_sentences, remaining

    except Exception as e:
        logger.error(f"Error in sentence segmentation: {e}")
        # Fallback to regex on any error
        return segment_text_by_regex(text)


class TagState(Enum):
    """State of a tag in text"""

    START = "start"  # <tag>
    INSIDE = "inside"  # text between tags
    END = "end"  # </tag>
    SELF_CLOSING = "self"  # <tag/>
    NONE = "none"  # no tag


@dataclass
class TagInfo:
    """Information about a tag"""

    name: str
    state: TagState

    def __str__(self) -> str:
        """String representation of tag info"""
        if self.state == TagState.NONE:
            return "none"
        return f"{self.name}:{self.state.value}"


@dataclass
class SentenceWithTags:
    """A sentence with its tag information, supporting nested tags"""

    text: str
    tags: List[TagInfo]  # List of tags from outermost to innermost


# Tamanho mínimo, em caracteres, de um pedaço enviado ao TTS.
#
# 60 é conservador: fica abaixo de uma frase média falada e acima do caso
# medido em 2026-10-01, em que uma resposta de 61 caracteres virou duas frases
# e duas sínteses do OmniVoice (3 s + 2 s). O engine tem custo fixo alto por
# chamada, então dois pedaços curtos custam quase o dobro de um pedaço só.
#
# Respostas longas não são afetadas: a primeira frase já passa de 60 e sai na
# hora, preservando o streaming.
#
# Rollback sem editar código:  CELIA_MIN_TTS_CHARS=0
DEFAULT_MIN_TTS_CHARS = 60


def _default_min_tts_chars() -> int:
    bruto = os.getenv("CELIA_MIN_TTS_CHARS")
    if bruto is None or not bruto.strip():
        return DEFAULT_MIN_TTS_CHARS
    try:
        return max(0, int(bruto))
    except ValueError:
        logger.warning(
            f"CELIA_MIN_TTS_CHARS inválido ({bruto!r}); usando {DEFAULT_MIN_TTS_CHARS}"
        )
        return DEFAULT_MIN_TTS_CHARS


class SentenceDivider:
    def __init__(
        self,
        faster_first_response: bool = True,
        segment_method: str = "pysbd",
        valid_tags: List[str] = None,
        min_tts_chars: Optional[int] = None,
    ):
        """
        Initialize the SentenceDivider.

        Args:
            faster_first_response: Whether to split first sentence at commas
            segment_method: Method for segmenting sentences
            valid_tags: List of valid tag names to detect
            min_tts_chars: tamanho mínimo, em caracteres, de um pedaço enviado
                ao TTS. Frases menores são agrupadas com a seguinte.

                MEDIDO em 2026-10-01: uma resposta de 61 caracteres virou DUAS
                frases e portanto DUAS sínteses do OmniVoice — 3 s a primeira e
                mais 2 s a segunda. O engine tem custo fixo alto por chamada
                (lock global em src/celia/tts/omnivoice_tts.py:29), então dois
                pedaços curtos custam quase o dobro de um pedaço só.

                Agrupar não atrasa a primeira fala no caso que importa: no
                incidente o stream do LLM já tinha terminado ("Agent output
                stream completed") ANTES de a síntese começar, ou seja as duas
                frases estavam disponíveis ao mesmo tempo.

                None usa DEFAULT_MIN_TTS_CHARS, que pode ser sobrescrito pela
                variável de ambiente CELIA_MIN_TTS_CHARS. `0` desliga o
                agrupamento e restaura exatamente o comportamento anterior —
                é o rollback sem editar código.
        """
        self.faster_first_response = faster_first_response
        self.segment_method = segment_method
        self.valid_tags = valid_tags or ["think"]
        self.min_tts_chars = (
            _default_min_tts_chars() if min_tts_chars is None else max(0, int(min_tts_chars))
        )
        # Pedaço curto segurado à espera do próximo, com as tags dele.
        self._pending_short: Optional[SentenceWithTags] = None
        self._is_first_sentence = True
        self._buffer = ""
        # Replace active_tags dict with a stack to handle nesting
        self._tag_stack = []

    def _get_current_tags(self) -> List[TagInfo]:
        """
        Get all current active tags from outermost to innermost.

        Returns:
            List[TagInfo]: List of active tags
        """
        return [TagInfo(tag.name, TagState.INSIDE) for tag in self._tag_stack]

    def _get_current_tag(self) -> Optional[TagInfo]:
        """
        Get the current innermost active tag.

        Returns:
            TagInfo if there's an active tag, None otherwise
        """
        return self._tag_stack[-1] if self._tag_stack else None

    def _extract_tag(self, text: str) -> Tuple[Optional[TagInfo], str]:
        """
        Extract the first tag from text if present.
        Handles nested tags by maintaining a tag stack.

        Args:
            text: Text to check for tags

        Returns:
            Tuple of (TagInfo if tag found else None, remaining text)
        """
        # Find the first occurrence of any tag
        first_tag = None
        first_pos = len(text)
        tag_type = None
        matched_tag = None

        # Check for self-closing tags
        for tag in self.valid_tags:
            pattern = f"<{tag}/>"
            match = re.search(pattern, text)
            if match and match.start() < first_pos:
                first_pos = match.start()
                first_tag = match
                tag_type = TagState.SELF_CLOSING
                matched_tag = tag

        # Check for opening tags
        for tag in self.valid_tags:
            pattern = f"<{tag}>"
            match = re.search(pattern, text)
            if match and match.start() < first_pos:
                first_pos = match.start()
                first_tag = match
                tag_type = TagState.START
                matched_tag = tag

        # Check for closing tags
        for tag in self.valid_tags:
            pattern = f"</{tag}>"
            match = re.search(pattern, text)
            if match and match.start() < first_pos:
                first_pos = match.start()
                first_tag = match
                tag_type = TagState.END
                matched_tag = tag

        if not first_tag:
            return None, text

        # Handle the found tag
        if tag_type == TagState.START:
            # Push new tag onto stack
            self._tag_stack.append(TagInfo(matched_tag, TagState.START))
        elif tag_type == TagState.END:
            # Verify matching tags
            if not self._tag_stack or self._tag_stack[-1].name != matched_tag:
                logger.warning(f"Mismatched closing tag: {matched_tag}")
            else:
                self._tag_stack.pop()

        return (TagInfo(matched_tag, tag_type), text[first_tag.end() :].lstrip())

    async def _process_buffer(self) -> AsyncIterator[SentenceWithTags]:
        """
        Process the current buffer, yielding complete sentences with tags.
        This is now an async generator.
        It consumes processed parts from self._buffer.
        """
        processed_something = True  # Flag to loop until no more processing can be done
        while processed_something:
            processed_something = False
            original_buffer_len = len(self._buffer)

            if not self._buffer.strip():
                break

            # Find the next tag position
            next_tag_pos = len(self._buffer)
            tag_pattern_found = None
            for tag in self.valid_tags:
                patterns = [f"<{tag}>", f"</{tag}>", f"<{tag}/>"]
                for pattern in patterns:
                    pos = self._buffer.find(pattern)
                    if pos != -1 and pos < next_tag_pos:
                        next_tag_pos = pos
                        tag_pattern_found = pattern  # Store the found pattern

            if next_tag_pos == 0:
                # Tag is at the start of buffer
                tag_info, remaining = self._extract_tag(self._buffer)
                if tag_info:
                    processed_text = self._buffer[
                        : len(self._buffer) - len(remaining)
                    ].strip()
                    # Yield the tag itself, represented as a SentenceWithTags
                    yield SentenceWithTags(text=processed_text, tags=[tag_info])
                    self._buffer = remaining
                    processed_something = True
                    continue  # Restart processing loop for the remaining buffer

            elif next_tag_pos < len(self._buffer):
                # Tag is in the middle - process text before tag first
                text_before_tag = self._buffer[:next_tag_pos]
                current_tags = self._get_current_tags()
                processed_segment = ""

                # Process complete sentences in text before tag
                if contains_end_punctuation(text_before_tag):
                    sentences, remaining_before = self._segment_text(text_before_tag)
                    for sentence in sentences:
                        if sentence.strip():
                            yield SentenceWithTags(
                                text=sentence.strip(),
                                tags=current_tags or [TagInfo("", TagState.NONE)],
                            )
                    # The part consumed includes sentences + what's left before the tag
                    processed_segment = text_before_tag
                    self._buffer = self._buffer[len(processed_segment) :]
                    processed_something = True
                    continue  # Restart processing loop

                elif text_before_tag.strip() and tag_pattern_found:
                    # No sentence end, but content exists AND we found a tag pattern after it.
                    # We can yield this segment because the tag provides a boundary.
                    yield SentenceWithTags(
                        text=text_before_tag.strip(),
                        tags=current_tags or [TagInfo("", TagState.NONE)],
                    )
                    self._buffer = self._buffer[len(text_before_tag) :]
                    processed_something = True
                    continue  # Restart processing loop
                # --- If no tag found after text_before_tag, we wait for more input or end punctuation ---

                # Process the tag itself if we haven't continued
                tag_info, remaining_after_tag = self._extract_tag(self._buffer)
                if tag_info:
                    processed_tag_text = self._buffer[
                        : len(self._buffer) - len(remaining_after_tag)
                    ].strip()
                    yield SentenceWithTags(text=processed_tag_text, tags=[tag_info])
                    self._buffer = remaining_after_tag
                    processed_something = True
                    continue  # Restart processing loop

            # No tags found or tag is not at the beginning/middle of processable segment
            # Process normal text if buffer has changed or punctuation exists
            if original_buffer_len > 0:
                current_tags = self._get_current_tags()

                # Handle first sentence with comma if enabled
                if (
                    self._is_first_sentence
                    and self.faster_first_response
                    and contains_comma(self._buffer)
                ):
                    sentence, remaining = comma_splitter(self._buffer)
                    if sentence.strip():
                        yield SentenceWithTags(
                            text=sentence.strip(),
                            tags=current_tags or [TagInfo("", TagState.NONE)],
                        )
                        self._buffer = remaining
                        self._is_first_sentence = False
                        processed_something = True
                        continue  # Restart processing loop

                # Process normal sentences based on end punctuation
                if contains_end_punctuation(self._buffer):
                    sentences, remaining = self._segment_text(self._buffer)
                    if sentences:  # Only process if segmentation yielded sentences
                        self._buffer = remaining
                        self._is_first_sentence = False
                        processed_something = True
                        for sentence in sentences:
                            if sentence.strip():
                                for pedaco in self._emit_or_hold(
                                    SentenceWithTags(
                                        text=sentence.strip(),
                                        tags=current_tags
                                        or [TagInfo("", TagState.NONE)],
                                    )
                                ):
                                    yield pedaco
                        continue  # Restart processing loop

            # If we reached here without processing anything, break the loop
            if not processed_something:
                break

    def _emit_or_hold(self, sentence: SentenceWithTags) -> List[SentenceWithTags]:
        """Agrupa pedaços curtos para não pagar o custo fixo do TTS duas vezes.

        Devolve o que já pode ir para o TTS. Um pedaço abaixo de
        `min_tts_chars` fica retido e sai junto com o próximo — ou sozinho, no
        flush, quando não existe próximo.
        """
        if self.min_tts_chars <= 0:
            return [sentence]

        if self._pending_short is not None:
            juntado = SentenceWithTags(
                text=f"{self._pending_short.text} {sentence.text}".strip(),
                # As tags do pedaço retido mandam: ele vem primeiro na fala.
                tags=self._pending_short.tags or sentence.tags,
            )
            self._pending_short = None
            sentence = juntado

        if len(sentence.text.strip()) < self.min_tts_chars:
            self._pending_short = sentence
            return []
        return [sentence]

    def _drain_pending(self) -> List[SentenceWithTags]:
        """Libera o pedaço retido. Nada pode ficar preso no fim do stream."""
        if self._pending_short is None:
            return []
        pendente = self._pending_short
        self._pending_short = None
        return [pendente] if pendente.text.strip() else []

    async def _flush_buffer(self) -> AsyncIterator[SentenceWithTags]:
        """
        Process and yield all remaining content in the buffer at the end of the stream.
        """
        logger.debug(f"Flushing remaining buffer: '{self._buffer}'")
        # First, run _process_buffer to yield any standard sentences/tags
        async for sentence in self._process_buffer():
            yield sentence

        # After processing standard structures, if anything is left, yield it as a final fragment
        if self._buffer.strip():
            logger.debug(
                f"Yielding final fragment from buffer: '{self._buffer.strip()}'"
            )
            current_tags = self._get_current_tags()
            # O fragmento final junta-se ao pedaço retido, se houver: no fim do
            # stream não existe "próximo", então reter de novo seria silêncio.
            for pedaco in self._emit_or_hold(
                SentenceWithTags(
                    text=self._buffer.strip(),
                    tags=current_tags or [TagInfo("", TagState.NONE)],
                )
            ):
                yield pedaco
            self._buffer = ""  # Clear buffer after flushing

        # Nada pode ficar preso: o que sobrou retido vai agora, mesmo curto.
        for pedaco in self._drain_pending():
            yield pedaco

    async def process_stream(
        self, segment_stream: AsyncIterator[Union[str, Dict[str, Any]]]
    ) -> AsyncIterator[Union[SentenceWithTags, Dict[str, Any]]]:
        """
        Process a stream of tokens (strings) and dictionaries.
        Yields complete sentences with tags (SentenceWithTags) or dictionaries directly.

        Args:
            segment_stream: An async iterator yielding strings or dictionaries.

        Yields:
            Union[SentenceWithTags, Dict[str, Any]]: Complete sentences/tags or original dictionaries.
        """
        self._full_response = []
        self.reset()  # Ensure state is clean

        async for item in segment_stream:
            if isinstance(item, dict):
                if item.get("type") == "tool_call_status" and item.get("status") == "running":
                    # A spoken cue must leave the buffer before a long tool starts.
                    async for sentence in self._flush_buffer():
                        self._full_response.append(sentence.text)
                        yield sentence
                # Before yielding the dict, process and yield any complete sentences formed so far
                async for sentence in self._process_buffer():
                    self._full_response.append(
                        sentence.text
                    )  # Track for complete response
                    yield sentence
                # Now yield the dictionary
                yield item
            elif isinstance(item, str):
                self._buffer += item
                # Process the buffer incrementally as string chunks arrive
                async for sentence in self._process_buffer():
                    self._full_response.append(
                        sentence.text
                    )  # Track for complete response
                    yield sentence
            else:
                logger.warning(
                    f"SentenceDivider received unexpected type: {type(item)}"
                )

        # After the stream finishes, flush any remaining text in the buffer
        async for sentence in self._flush_buffer():
            self._full_response.append(sentence.text)
            yield sentence

    @property
    def complete_response(self) -> str:
        """Get the complete response accumulated so far"""
        return "".join(self._full_response)

    def _segment_text(self, text: str) -> Tuple[List[str], str]:
        """Segment text using the configured method"""
        if self.segment_method == "regex":
            return segment_text_by_regex(text)
        return segment_text_by_pysbd(text)

    def reset(self):
        """Reset the divider state for a new conversation"""
        self._is_first_sentence = True
        self._buffer = ""
        self._tag_stack = []
