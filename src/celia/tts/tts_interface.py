import abc
import os
import uuid
import asyncio
from functools import wraps
from .output_filter import speech_blocked

from loguru import logger


TTS_ENGINE_OMNIVOICE = "omnivoice"
TTS_ENGINE_IDS = frozenset({TTS_ENGINE_OMNIVOICE, "piper_v9d"})


class TTSInterface(metaclass=abc.ABCMeta):
    engine_id: str | None = None

    def __init_subclass__(cls, **kwargs):
        """Guard sync and overridden async entrypoints, including direct callers."""
        super().__init_subclass__(**kwargs)
        for name in ("generate_audio", "async_generate_audio"):
            method = cls.__dict__.get(name)
            if method is None:
                continue
            if asyncio.iscoroutinefunction(method):
                def wrap_async(fn):
                    @wraps(fn)
                    async def guarded(self, text, *args, **kw):
                        if speech_blocked(text):
                            return None
                        return await fn(self, text, *args, **kw)
                    return guarded
                setattr(cls, name, wrap_async(method))
            else:
                def wrap_sync(fn):
                    @wraps(fn)
                    def guarded(self, text, *args, **kw):
                        if speech_blocked(text):
                            return None
                        return fn(self, text, *args, **kw)
                    return guarded
                setattr(cls, name, wrap_sync(method))

    async def async_generate_audio(self, text: str, file_name_no_ext=None) -> str:
        """
        Asynchronously generate speech audio file using TTS.

        By default, this runs the synchronous generate_audio in a coroutine.
        Subclasses can override this method to provide true async implementation.

        text: str
            the text to speak
        file_name_no_ext (optional and deprecated): str
            name of the file without file extension

        Returns:
        str: the path to the generated audio file

        """
        return await asyncio.to_thread(self.generate_audio, text, file_name_no_ext)

    @abc.abstractmethod
    def generate_audio(self, text: str, file_name_no_ext=None) -> str:
        """
        Generate speech audio file using TTS.
        text: str
            the text to speak
        file_name_no_ext (optional and deprecated): str
            name of the file without file extension

        Returns:
        str: the path to the generated audio file

        """
        raise NotImplementedError

    def remove_file(self, filepath: str, verbose: bool = True) -> None:
        """
        Remove a file from the file system.

        This is a separate method instead of a part of the `play_audio_file_local()` because `play_audio_file_local()` is not the only way to play audio files. This method will be used to remove the audio file after it has been played.

        Parameters:
            filepath (str): The path to the file to remove.
            verbose (bool): If True, print messages to the console.
        """
        if not os.path.exists(filepath):
            logger.warning(f"File {filepath} does not exist")
            return
        try:
            logger.debug(f"Removing file {filepath}") if verbose else None
            os.remove(filepath)
        except Exception as e:
            logger.error(f"Failed to remove file {filepath}: {e}")

    def generate_cache_file_name(self, file_name_no_ext=None, file_extension="wav"):
        """
        Generate a cross-platform cache file name.

        file_name_no_ext: str
            name of the file without extension
        file_extension: str
            file extension

        Returns:
        str: the path to the generated cache file
        """
        cache_dir = "cache"
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)

        if file_name_no_ext is None:
            # Nome único por chamada em vez de "temp" fixo: evita
            # PermissionError [Errno 13] em cache/temp.wav quando o arquivo
            # está locado (ex.: Terabox sync) ou há sínteses concorrentes
            # (rajada de falas Minecraft / talk do Codex).
            file_name_no_ext = f"temp_{os.getpid()}_{uuid.uuid4().hex[:12]}"

        file_name = f"{file_name_no_ext}.{file_extension}"
        return os.path.join(cache_dir, file_name)


def identify_tts_engine(engine: TTSInterface | None) -> str | None:
    """Return the canonical engine id used by memory and observability."""
    if engine is None:
        return None
    engine_id = getattr(engine, "engine_id", None)
    return engine_id if engine_id in TTS_ENGINE_IDS else None
