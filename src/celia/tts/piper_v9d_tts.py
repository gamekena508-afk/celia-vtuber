"""GPU-only Piper engine for the approved V9-D Célia voice."""
from __future__ import annotations

import importlib
import json
import os
import sys
import threading
import wave
from pathlib import Path

from loguru import logger

from .tts_interface import TTSInterface


class TTSEngine(TTSInterface):
    engine_id = "piper_v9d"

    def __init__(
        self,
        model_path: str,
        config_path: str,
        piper_source_path: str,
        use_cuda: bool = True,
    ) -> None:
        if not use_cuda:
            raise ValueError("V9-D is configured GPU-only; CPU fallback is disabled.")
        # Load CUDA/cuDNN dependencies from this venv's PyTorch wheels before ORT
        # initializes its CUDA provider. This machine has CUDA 12 libraries in the
        # venv while the system-wide CUDA toolkit is version 13.
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("V9-D GPU blocked: PyTorch cannot access CUDA.")
        torch.cuda.init()
        import onnxruntime as ort

        if "CUDAExecutionProvider" not in ort.get_available_providers():
            raise RuntimeError(
                "V9-D GPU blocked: ONNX Runtime has no CUDAExecutionProvider."
            )

        self.model_path = Path(model_path).expanduser().resolve()
        self.config_path = Path(config_path).expanduser().resolve()
        self.piper_source_path = Path(piper_source_path).expanduser().resolve()
        for path in (self.model_path, self.config_path):
            if not path.is_file():
                raise FileNotFoundError(f"V9-D asset missing: {path}")
        if not (self.piper_source_path / "piper" / "voice.py").is_file():
            raise FileNotFoundError(
                f"Piper implementation missing: {self.piper_source_path}"
            )

        source = str(self.piper_source_path)
        if source not in sys.path:
            sys.path.insert(0, source)

        self._lock = threading.Lock()
        self._profile_enabled = os.environ.get("CELIA_PIPER_PROFILE") == "1"
        original_session = ort.InferenceSession
        if self._profile_enabled:
            def profiled_session(*args, **kwargs):
                options = kwargs.get("sess_options") or ort.SessionOptions()
                options.enable_profiling = True
                kwargs["sess_options"] = options
                return original_session(*args, **kwargs)

            ort.InferenceSession = profiled_session

        try:
            piper = importlib.import_module("piper")
            self._voice = piper.PiperVoice.load(
                str(self.model_path),
                config_path=str(self.config_path),
                use_cuda=True,
            )
        finally:
            if self._profile_enabled:
                ort.InferenceSession = original_session

        providers = self._voice.session.get_providers()
        logger.info(
            "V9-D Piper loaded: model={} runtime=ONNX Runtime {} providers={}",
            self.model_path,
            ort.__version__,
            providers,
        )
        if not providers or providers[0] != "CUDAExecutionProvider":
            raise RuntimeError(
                "V9-D GPU blocked: CUDAExecutionProvider is not the active first provider: "
                + repr(providers)
            )

    def generate_audio(self, text: str, file_name_no_ext=None) -> str:
        if not text or not text.strip():
            raise ValueError("V9-D received empty text.")

        output_path = Path(
            self.generate_cache_file_name(file_name_no_ext, file_extension="wav")
        ).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            with wave.open(str(output_path), "wb") as wav_file:
                self._voice.synthesize_wav(text, wav_file)

            if self._profile_enabled:
                profile_path = Path(self._voice.session.end_profiling())
                events = json.loads(profile_path.read_text(encoding="utf-8"))
                gpu_nodes = [
                    event for event in events
                    if event.get("cat") == "Node"
                    and (
                        event.get("args", {}).get("provider") == "CUDAExecutionProvider"
                        or event.get("args", {}).get("execution_provider") == "CUDAExecutionProvider"
                    )
                ]
                cpu_nodes = [
                    event for event in events
                    if event.get("cat") == "Node"
                    and (
                        event.get("args", {}).get("provider") == "CPUExecutionProvider"
                        or event.get("args", {}).get("execution_provider") == "CPUExecutionProvider"
                    )
                ]
                logger.info(
                    "V9-D ONNX profile: trace={} CUDA node events={} CPU node events={}",
                    profile_path,
                    len(gpu_nodes),
                    len(cpu_nodes),
                )
                if not gpu_nodes:
                    raise RuntimeError(
                        "V9-D audio was generated, but the ONNX profile did not confirm CUDA node execution."
                    )

        if not output_path.is_file() or output_path.stat().st_size <= 44:
            raise RuntimeError(f"V9-D produced an invalid WAV: {output_path}")
        logger.info("V9-D audio generated: {} ({} bytes)", output_path, output_path.stat().st_size)
        return str(output_path)
