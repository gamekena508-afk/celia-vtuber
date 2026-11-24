import abc
import numpy as np
import asyncio
import threading
import time

_TRANSCRIPTIONS = {}
_TRANSCRIPTION_LOCK = threading.Lock()

def transcription_snapshot():
    now = time.monotonic()
    with _TRANSCRIPTION_LOCK:
        jobs = list(_TRANSCRIPTIONS.values())
    waiting = [t for state, t in jobs if state == "waiting"]
    return {"waiting": len(waiting), "running": sum(state == "running" for state, _ in jobs),
            "oldest_wait_ms": round(1000 * (now - min(waiting))) if waiting else 0}



class ASRInterface(metaclass=abc.ABCMeta):
    SAMPLE_RATE = 16000
    NUM_CHANNELS = 1
    SAMPLE_WIDTH = 2

    async def async_transcribe_np(self, audio: np.ndarray) -> str:
        """Asynchronously transcribe speech audio in numpy array format.

        By default, this runs the synchronous transcribe_np in a coroutine.
        Subclasses can override this method to provide true async implementation.

        Args:
            audio: The numpy array of the audio data to transcribe.

        Returns:
            str: The transcription result.
        """
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        job = object()
        with _TRANSCRIPTION_LOCK:
            _TRANSCRIPTIONS[job] = ("waiting", time.monotonic())
        def run():
            with _TRANSCRIPTION_LOCK:
                _TRANSCRIPTIONS[job] = ("running", time.monotonic())
            try:
                return self.transcribe_np(audio)
            finally:
                with _TRANSCRIPTION_LOCK:
                    _TRANSCRIPTIONS.pop(job, None)
        try:
            return await asyncio.to_thread(run)
        finally:
            with _TRANSCRIPTION_LOCK:
                if _TRANSCRIPTIONS.get(job, ("", 0))[0] == "waiting":
                    _TRANSCRIPTIONS.pop(job, None)

    @abc.abstractmethod
    def transcribe_np(self, audio: np.ndarray) -> str:
        """Transcribe speech audio in numpy array format and return the transcription.

        Args:
            audio: The numpy array of the audio data to transcribe.
        """
        raise NotImplementedError

    def nparray_to_audio_file(
        self, audio: np.ndarray, sample_rate: int, file_path: str
    ) -> None:
        """Convert a numpy array of audio data to a .wav file.

        Args:
            audio: The numpy array of audio data.
            sample_rate: The sample rate of the audio data.
            file_path: The path to save the .wav file.
        """
        import wave

        # Make sure the audio is in the range [-1, 1]
        audio = np.clip(audio, -1, 1)
        # Convert the audio to 16-bit PCM
        audio_integer = (audio * 32767).astype(np.int16)

        with wave.open(file_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio_integer.tobytes())
